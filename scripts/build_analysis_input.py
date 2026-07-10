#!/usr/bin/env python3
"""決算分析AI向け入力素材の組み立て (Phase2 P2-1)。

入力ソースは以下の3つに限定する(決算短信PDF本文は一切取得・パースしない):
- frontend/data/financials.json  : XBRL由来の構造化実績数値 (年次/四半期)
- frontend/data/disclosures.json : TDnet開示タイトル(業績予想修正の方向判定用。本文は使わない。
                                    generate_scores.py の revision シグナルと同じ経路)
- frontend/data/market/<最新日>.md : 市況概況レポート(市場全体の定性文脈)

会社予想の構造化データは本リポジトリに存在しないため(app.js progressInfo() 参照)、
「前期通期に対する四半期累計の消化率を前年同時点と比較」する代替指標
(generate_scores.compute_progress_signal と同じロジック)を数値付きで採用する。

出力: 分析プロンプトの元になるJSON素材 (dict)。欠損時は黙って None を返さず
InputAssemblyError を送出する(フェイルラウド)。

依存: Python 標準ライブラリのみ。
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_scores as gs  # noqa: E402 (増収増益/利益率/修正シグナルのロジックを再利用)

# このモジュールが値を読む対象ファイル(テストで「PDF本文を混入させない」ことの根拠にする)。
SOURCE_FILES = ("financials.json", "disclosures.json")


class InputAssemblyError(Exception):
    """分析入力の組み立てに必要なデータが欠損している場合に送出する(フェイルラウド)。"""


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def latest_market_md(data_dir):
    """market/ 配下の最新日付の .md ファイルパスを返す。無ければ None。"""
    idx_path = os.path.join(data_dir, "market", "index.json")
    dates = []
    if os.path.exists(idx_path):
        try:
            dates = load_json(idx_path).get("dates") or []
        except (OSError, ValueError):
            dates = []
    if dates:
        path = os.path.join(data_dir, "market", dates[-1] + ".md")
        if os.path.exists(path):
            return path
    # index.json が無い/壊れている場合はファイル名(日付)昇順でフォールバック走査
    mds = sorted(glob.glob(os.path.join(data_dir, "market", "*.md")))
    return mds[-1] if mds else None


def progress_detail(annual, quarterly):
    """進捗率(前期通期に対する四半期累計)の当期・前年同時点の実数値を返す。
    generate_scores.compute_progress_signal と同一ロジックの数値版
    (会社予想データが無いため、Phase1スコアリングと同じ代替指標を分析入力にも使う)。
    算出不能なら None。"""
    if len(annual) < 2 or not quarterly:
        return None
    last_a, prev_a = annual[-1], annual[-2]
    cur_q = [r for r in quarterly if r and r[0] > last_a[0]]
    n = len(cur_q)
    if not n:
        return None
    prev_q = [r for r in quarterly if r and prev_a[0] < r[0] <= last_a[0]][:n]
    if len(prev_q) != n:
        return None

    def sumcol(rows):
        vals = [r[2] for r in rows if len(r) > 2 and isinstance(r[2], (int, float))]
        return sum(vals) if len(vals) == len(rows) else None

    cur_sum, base = sumcol(cur_q), (last_a[2] if len(last_a) > 2 and isinstance(last_a[2], (int, float)) else None)
    prev_sum, prev_base = sumcol(prev_q), (prev_a[2] if len(prev_a) > 2 and isinstance(prev_a[2], (int, float)) else None)
    if None in (cur_sum, base, prev_sum, prev_base) or base <= 0 or prev_base <= 0:
        return None
    cur_pct, prev_pct = cur_sum / base * 100, prev_sum / prev_base * 100
    return {"quarters": n, "current_pct": round(cur_pct, 1),
            "prior_year_same_point_pct": round(prev_pct, 1), "diff_pt": round(cur_pct - prev_pct, 1)}


def build_analysis_input(code, disclosure, data_dir="frontend/data"):
    """1件の決算短信/訂正決算短信開示について分析用プロンプト素材を組み立てて返す。

    code:        銘柄コード
    disclosure:  disclosures.json の1件({"key","code","title","doc_type","published_at","pdf_url"}）。
                 pdf_url は参照リンクとしてのみ出力に含め、PDF本文は取得・パースしない。
    欠損時は InputAssemblyError を送出する(フェイルラウド)。
    """
    fin_path = os.path.join(data_dir, "financials.json")
    if not os.path.exists(fin_path):
        raise InputAssemblyError(f"{fin_path} が無いため分析素材を組み立てられません")
    financials = load_json(fin_path)
    entry = (financials.get("stocks") or {}).get(code)
    if not entry or not (entry.get("a") or entry.get("q")):
        raise InputAssemblyError(f"銘柄 {code} の実績数値(financials.json)が無いため分析素材を組み立てられません")

    disc_path = os.path.join(data_dir, "disclosures.json")
    all_discs = load_json(disc_path) if os.path.exists(disc_path) else []
    revision_discs = sorted(
        (d for d in all_discs if str(d.get("code")) == code and d.get("doc_type") == "業績予想修正"),
        key=lambda d: d.get("published_at") or "", reverse=True)

    md_path = latest_market_md(data_dir)
    if not md_path:
        raise InputAssemblyError("市況MD(frontend/data/market/*.md)が無いため分析素材を組み立てられません")
    with open(md_path, encoding="utf-8") as f:
        market_md = f.read()

    annual, quarterly = entry.get("a") or [], entry.get("q") or []
    growth, margin = gs.compute_growth_and_margin(annual)

    return {
        "code": code,
        "disclosure": {
            "id": str(disclosure.get("key")),
            "title": disclosure.get("title"),
            "doc_type": disclosure.get("doc_type"),
            "published_at": disclosure.get("published_at"),
            "pdf_url": disclosure.get("pdf_url"),  # 参照リンクのみ。本文は取得しない
        },
        "actuals": {  # [期末日,売上高,営業利益,純利益,EPS] (financials.jsonの生の値)
            "annual": annual[-3:],
            "quarterly": quarterly[-5:],
        },
        "growth_signal": growth,   # 1=増収増益 / -1=減収減益 / 0=どちらでもない / None=算出不可
        "margin_signal": margin,   # 営業利益率の前期比: 改善(1)/悪化(-1)/横ばい(0)/None
        "progress": progress_detail(annual, quarterly),  # 会社予想データが無いための代替指標
        "revision_signal": gs.compute_revision_signal(revision_discs),  # 直近開示からの方向(1/-1/0)
        "revision_disclosures": [
            {"title": d.get("title"), "published_at": d.get("published_at")} for d in revision_discs[:3]
        ],
        "market_context_md": market_md,
        "market_context_date": os.path.splitext(os.path.basename(md_path))[0],
        "sources": ["frontend/data/financials.json", "frontend/data/disclosures.json",
                    "frontend/data/market/" + os.path.basename(md_path)],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="frontend/data")
    ap.add_argument("--code", required=True)
    ap.add_argument("--key", required=True, help="disclosures.json の該当開示の key (disclosure_id)")
    args = ap.parse_args()
    disclosures = load_json(os.path.join(args.data, "disclosures.json"))
    disclosure = next((d for d in disclosures if str(d.get("key")) == args.key), None)
    if not disclosure:
        print(f"key={args.key} の開示が disclosures.json に見つかりません", file=sys.stderr)
        sys.exit(1)
    material = build_analysis_input(args.code, disclosure, args.data)
    print(json.dumps(material, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
