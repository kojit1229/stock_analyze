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

実データ検証の追記 (2026-07-10, W2-1): 実際にTDnetから取得した決算短信
サマリーXBRLは常にインラインXBRL (iXBRL、拡張子 .htm) であり、値は
`<tse-ed-t:NetSales>...</tse-ed-t:NetSales>` のような専用タグではなく、
`<ix:nonFraction name="tse-ed-t:NetSales" contextRef="..." scale="6"
sign="-">...</ix:nonFraction>` の形で埋め込まれる(タグ名は常に
`ix:nonFraction`、実際の要素名は `name` 属性、符号は `sign="-"` 属性、
実数値は `scale` 属性 (10のべき乗) を掛けて求める)。本パーサは通常の
XBRLタグとインラインXBRLの両方を認識する。
"""
import xml.etree.ElementTree as ET


class XbrlParseError(Exception):
    """不正な XBRL (パース不能) を表す例外。呼び出し側はこれを捕捉して
    Yahoo由来値へのフォールバックとログ出力を行うこと。"""


# 売上高・営業利益の代表的な要素ローカル名 (名前空間prefixには依存しない)
NET_SALES_TAGS = ("NetSales", "NetSalesSummaryOfBusinessResults", "OperatingRevenue1")
OPERATING_INCOME_TAGS = ("OperatingIncome", "OperatingIncomeSummaryOfBusinessResults")

# 「業績予想の修正有無」を示す要素名の代表的な語幹 (部分一致・大文字小文字無視)。
# "CorrectionOf...FinancialForecast"/"CorrectionOfDividendForecast" は実際にTDnetから
# 取得した決算短信サマリーXBRL (2026-07-10 検証) で確認した正式タグ名
# ("CorrectionOfConsolidatedFinancialForecastInThisQuarter" /
# "CorrectionOfDividendForecastInThisQuarter")。連結・単体の両方に備えて
# "Consolidated" を含まない語幹 "FinancialForecastInThisQuarter" で照合する。
# 他はタクソノミの別バージョンに備えた従来からの語幹 (実データでの確認はできていない)。
REVISION_FLAG_HINTS = (
    "RevisionOfForecast", "RevisionsOfForecast", "NumOfRevision",
    "ForecastRevision", "RevisionOfDividendForecast",
    "FinancialForecastInThisQuarter", "CorrectionOfDividendForecast",
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


def _ix_concept_local_name(name_attr):
    """ix:nonFraction の name 属性 ('tse-ed-t:NetSales') → 'NetSales'
    (タクソノミprefixを剥がす)。属性が無ければ空文字を返す。"""
    if not name_attr:
        return ""
    return name_attr.split(":", 1)[1] if ":" in name_attr else name_attr


_XSI_NIL_ATTR = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def _is_ix_nil(el):
    v = el.get(_XSI_NIL_ATTR)
    return bool(v) and v.strip().lower() == "true"


def _to_number_inline(el):
    """ix:nonFraction 要素から値を取り出す。通常のXBRLタグと異なり、符号は
    テキストの △/▲ ではなく sign="-" 属性で表され、表示値は scale 属性
    (10のべき乗) を掛けて実際の値にする必要がある。xsi:nil="true" は
    欠損として None を返す。"""
    if _is_ix_nil(el):
        return None
    v = _to_number(el.text)
    if v is None:
        return None
    scale = el.get("scale")
    if scale is not None:
        try:
            v *= 10 ** int(scale)
        except ValueError:
            pass
    if (el.get("sign") or "").strip() == "-":
        v = -v
    return v


def _matched_value(el, tag_candidates):
    """要素が対象タグ群にマッチするかを判定し、マッチすれば値を返す。
    通常のXBRLタグは要素タグ名そのもので判定し、インラインXBRL
    (ix:nonFraction) は name 属性 (prefix除去後) で判定する。
    戻り値: (matched: bool, value: float|None)。"""
    local = _local(el.tag)
    if local in tag_candidates:
        return True, _to_number(el.text)
    if local == "nonFraction" and _ix_concept_local_name(el.get("name")) in tag_candidates:
        return True, _to_number_inline(el)
    return False, None


def _is_non_consolidated_context(context_id):
    """contextRef が明確に単体(非連結)を示す場合のみ True。
    "Member" を含むことを理由に一律で単体扱いにしない(設計原則2)。"""
    return "NonConsolidated" in (context_id or "")


def _context_periods(root):
    """<xbrli:context> 要素を読み、contextRef -> 期間終了日 (duration の
    endDate、instant の instant) のマップを作る。同一タグに複数期間
    (当期・前期など) の fact が併記されている場合に、どれが当期かを
    contextRef の命名規則に頼らず実際の期間定義から判定するために使う。
    期間情報が取れないコンテキストはマップに含めない。"""
    periods = {}
    for ctx in root.iter():
        if _local(ctx.tag) != "context":
            continue
        cid = ctx.get("id")
        if not cid:
            continue
        end = None
        for child in ctx.iter():
            local = _local(child.tag)
            if local in ("endDate", "instant"):
                text = (child.text or "").strip()
                if text:
                    end = text
        if end:
            periods[cid] = end
    return periods


def _pick_value(root, tag_candidates):
    """指定タグ群から連結優先・単体劣後、かつ同じ連結区分内では期間終了日が
    最も新しい (=当期) factを選ぶ。
    戻り値: (value: float|None, consolidated: bool)
    連結値が1つも見つからなければ単体値にフォールバックする。
    期間定義が読めないコンテキストは最劣後として扱い、他に手がかりが
    無い場合のみ文書順で最初のfactを採用する(従来の挙動を維持)。
    """
    periods = _context_periods(root)
    cons_best = None  # (period_end, value)
    noncons_best = None
    for el in root.iter():
        matched, val = _matched_value(el, tag_candidates)
        if not matched or val is None:
            continue
        ctx = el.get("contextRef") or ""
        period_end = periods.get(ctx) or ""
        entry = (period_end, val)
        if _is_non_consolidated_context(ctx):
            if noncons_best is None or period_end > noncons_best[0]:
                noncons_best = entry
        else:
            if cons_best is None or period_end > cons_best[0]:
                cons_best = entry
    if cons_best is not None:
        return cons_best[1], True
    if noncons_best is not None:
        return noncons_best[1], False
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
        concept = _ix_concept_local_name(el.get("name")) if local == "nonNumeric" else local
        if any(hint.lower() in concept.lower() for hint in REVISION_FLAG_HINTS):
            if _is_ix_nil(el):
                continue
            text = "".join(el.itertext()).strip()
            if text:
                forecast_revised = text.lower() in _TRUE_TEXTS
                break

    return {
        "net_sales": net_sales,
        "operating_income": op_income,
        "forecast_revised": forecast_revised,
        "consolidated": consolidated,
    }
