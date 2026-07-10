#!/usr/bin/env python3
"""決算短信サマリー XBRL の構造化数値パーサ (標準ライブラリのみ)。

TDnet の決算短信に添付される短信サマリーXBRLから、売上高・営業利益・
業績予想の修正有無を直接取得するための最小パーサ。PDF本文は一切パース
しない (T-3 の設計原則(1))。

設計原則 (運用契約 T-3 の指示。逸脱禁止):
1. XBRLタグのみパース。PDF本文の数値抽出はしない。
2. コンテキスト選択は連結優先・単体劣後。contextRef に "Member" が含まれる
   ことを理由に一律除外しない。実際の短信サマリーXBRLでは連結値にも
   "...ConsolidatedMember" のように Member 付きコンテキストが使われることが
   あり、これを一律除外すると単体(非連結)値まで落ちてしまう既知バグがある。
   本パーサが除外するのは contextRef に "NonConsolidated" を含むものだけで
   あり、それも「連結コンテキストが1つも見つからない場合のフォールバック」
   としてのみ採用する(単体劣後)。
3. 値のテキストに全角の負符号「△」「▲」が含まれる場合は負数に変換する。
4. 不正な XBRL (パース不能) は XbrlParseError を送出し、呼び出し側で
   明示的にフォールバック処理させる(黙って空値やゼロを返さない)。

注記(既知の限界): 「業績予想の修正有無」を示す要素の正式タグ名は
EDINET/TDnetの決算短信サマリー・タクソノミのバージョンによって表記が
揺れる。本パーサはこのリポジトリに実XBRLサンプルが無く実データでの
検証ができていないため、代表的な語幹 (REVISION_FLAG_HINTS) への部分一致
で検出する簡易実装とし、完全一致の正式タグ名を1つに固定していない。
実データでの検証は別途フォローアップが必要。
"""
import xml.etree.ElementTree as ET


class XbrlParseError(Exception):
    """不正な XBRL (パース不能) を表す例外。呼び出し側はこれを捕捉して
    Yahoo由来値へのフォールバックとログ出力を行うこと。"""


# 売上高・営業利益の代表的な要素ローカル名 (名前空間prefixには依存しない)
NET_SALES_TAGS = ("NetSales", "NetSalesSummaryOfBusinessResults", "OperatingRevenue1")
OPERATING_INCOME_TAGS = ("OperatingIncome", "OperatingIncomeSummaryOfBusinessResults")

# 「業績予想の修正有無」を示す要素名の代表的な語幹 (部分一致・大文字小文字無視)
REVISION_FLAG_HINTS = (
    "RevisionOfForecast", "RevisionsOfForecast", "NumOfRevision",
    "ForecastRevision", "RevisionOfDividendForecast",
)

# 決算短信の負数表記に使われる全角記号
_NEG_PREFIX = ("△", "▲")  # △(白三角) / ▲(黒三角)

_TRUE_TEXTS = ("true", "1", "有", "あり", "yes")


def _to_number(text):
    """XBRL の数値テキストを float に変換する。△/▲ 接頭辞・カンマを処理する。
    数値として解釈できなければ None を返す(例外にしない。単一factの欠損は
    致命的ではないため)。"""
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    neg = False
    if s[0] in _NEG_PREFIX:
        neg = True
        s = s[1:].strip()
    s = s.replace(",", "").replace("　", "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _local(tag):
    """'{namespace}LocalName' → 'LocalName' (名前空間prefixを剥がす)。"""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _is_non_consolidated_context(context_id):
    """contextRef が明確に単体(非連結)を示す場合のみ True。
    "Member" を含むことを理由に一律で単体扱いにしない(設計原則2)。"""
    return "NonConsolidated" in (context_id or "")


def _pick_value(root, tag_candidates):
    """指定タグ群から連結優先・単体劣後で値を選ぶ。
    戻り値: (value: float|None, consolidated: bool)
    連結値が1つも見つからなければ単体値にフォールバックする。
    """
    cons_val = None
    noncons_val = None
    for el in root.iter():
        if _local(el.tag) not in tag_candidates:
            continue
        val = _to_number(el.text)
        if val is None:
            continue
        ctx = el.get("contextRef") or ""
        if _is_non_consolidated_context(ctx):
            if noncons_val is None:
                noncons_val = val
        else:
            if cons_val is None:
                cons_val = val
    if cons_val is not None:
        return cons_val, True
    if noncons_val is not None:
        return noncons_val, False
    return None, True


def parse_summary(xbrl_bytes):
    """短信サマリー XBRL (bytes または str) から構造化数値を抽出する。

    返り値:
      {
        "net_sales": float | None,          # 売上高 (連結優先)
        "operating_income": float | None,   # 営業利益 (連結優先)
        "forecast_revised": bool | None,    # 業績予想の修正有無 (判定不能なら None)
        "consolidated": bool,               # 連結値を採用できたか (False=単体へフォールバック)
      }

    不正な XBRL (XML として解析できない・入力が空) の場合は XbrlParseError
    を送出する。呼び出し側でこれを捕捉し、Yahoo由来値へフォールバックする
    こと(設計原則4)。
    """
    if not xbrl_bytes:
        raise XbrlParseError("XBRL データが空です")
    try:
        root = ET.fromstring(xbrl_bytes)
    except ET.ParseError as e:
        raise XbrlParseError(f"XBRL のXML解析に失敗しました: {e}") from e

    net_sales, ns_cons = _pick_value(root, NET_SALES_TAGS)
    op_income, op_cons = _pick_value(root, OPERATING_INCOME_TAGS)
    have_any = net_sales is not None or op_income is not None
    consolidated = (ns_cons if net_sales is not None else True) and \
                   (op_cons if op_income is not None else True) if have_any else True

    forecast_revised = None
    for el in root.iter():
        local = _local(el.tag)
        if any(hint.lower() in local.lower() for hint in REVISION_FLAG_HINTS):
            text = (el.text or "").strip()
            if text:
                forecast_revised = text.lower() in _TRUE_TEXTS
                break

    return {
        "net_sales": net_sales,
        "operating_income": op_income,
        "forecast_revised": forecast_revised,
        "consolidated": consolidated,
    }
