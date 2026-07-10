#!/usr/bin/env python3
"""決算スコアリング v1 (ルールベース・コンセンサス不使用)。

入力: frontend/data/financials.json (年次/四半期PL、構造化数値のみ・PDF本文はパースしない) +
      frontend/data/disclosures.json (直近45日の適時開示、業績予想修正の方向判定に使用)
出力: frontend/data/scores.json (financials.json 収録の全銘柄のスコアと内訳)

4項目のシグナル(-1/0/+1、算出不能ならNone)の平均を50点中心・±50点で
0〜100に正規化する。財務データ由来のシグナル(増収増益・利益率改善・進捗率)が
1つも算出できない銘柄は score:null, insufficient_data:true とし、黙って0点や
50点を出さない(フェイルラウド)。

- 増収増益: 直近年度が前年度に対し増収かつ増益(営業利益)か
- 利益率改善: 営業利益率(営業利益/売上高)が前年度から改善したか
- 進捗率: 四半期累計÷前期通期の消化率を前年同時点と比較(frontend/app.js
  progressInfo() と同一ロジックの移植。会社予想データが無いための代替指標)
- 業績予想修正: 直近45日以内の業績予想修正開示タイトルから上方/下方を判定
  (方向不明な修正、または開示自体が無ければ0)

依存: Python 標準ライブラリのみ。
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_alerts as ga  # noqa: E402 (開示タイトル分類ロジックを再利用)

JST = datetime.timezone(datetime.timedelta(hours=9))

# key -> {シグナル値: 説明文} (Noneキーは算出不能時の説明)
NOTES = {
    "growth": {1: "増収かつ増益", 0: "増収増益・減収減益のいずれでもない", -1: "減収かつ減益",
               None: "年次データ不足(2期未満、または数値欠損)のため算出不可"},
    "margin": {1: "営業利益率が改善", 0: "営業利益率はほぼ横ばい", -1: "営業利益率が悪化",
               None: "年次データ不足(2期未満、または数値欠損)のため算出不可"},
    "progress": {1: "前年同時点より進捗が先行", 0: "前年同時点並みの進捗", -1: "前年同時点より進捗が遅れ",
                 None: "四半期データが無いため算出不可"},
    "revision": {1: "直近45日以内に上方修正の開示", 0: "直近45日以内に修正の開示なし(または方向不明)",
                 -1: "直近45日以内に下方修正の開示", None: "開示データを取得できなかったため算出不可"},
}
LABELS = {"growth": "増収増益", "margin": "利益率改善", "progress": "進捗率(前年同時点比)",
          "revision": "業績予想修正"}


def log(msg):
    print(msg, flush=True)


def _val(row, idx):
    if row is None or len(row) <= idx or not isinstance(row[idx], (int, float)):
        return None
    return row[idx]


def compute_growth_and_margin(annual):
    """直近2期の年次PL([期末日,売上高,営業利益,純利益,EPS])から
    (増収増益シグナル, 利益率改善シグナル) を返す。算出不能なら None。"""
    if len(annual) < 2:
        return None, None
    latest, prev = annual[-1], annual[-2]
    rev_l, rev_p = _val(latest, 1), _val(prev, 1)
    op_l, op_p = _val(latest, 2), _val(prev, 2)
    if None in (rev_l, rev_p, op_l, op_p):
        return None, None
    rev_up, op_up = rev_l > rev_p, op_l > op_p
    growth = 1 if (rev_up and op_up) else (-1 if (not rev_up and not op_up) else 0)
    margin = None
    if rev_l > 0 and rev_p > 0:
        m_l, m_p = op_l / rev_l, op_p / rev_p
        margin = 1 if m_l > m_p else (-1 if m_l < m_p else 0)
    return growth, margin


def compute_progress_signal(annual, quarterly):
    """frontend/app.js progressInfo() と同一ロジック(営業利益列)の移植。
    今期累計÷前期通期の消化率を前年同時点と比較し、+3pt以上=先行(1)、
    -3pt以下=遅れ(-1)、それ以外0。算出不能ならNone。"""
    if len(annual) < 2 or not quarterly:
        return None
    last_a, prev_a = annual[-1], annual[-2]
    cur_q = [r for r in quarterly if r and r[0] > last_a[0]]
    n = len(cur_q)
    if not n:
        return None
    prev_q = [r for r in quarterly if r and prev_a[0] < r[0] <= last_a[0]][:n]

    def sumcol(rows):
        s = 0
        for r in rows:
            v = _val(r, 2)
            if v is None:
                return None
            s += v
        return s

    cur_sum, base = sumcol(cur_q), _val(last_a, 2)
    if cur_sum is None or base is None or base <= 0 or len(prev_q) != n:
        return None
    prev_sum, prev_base = sumcol(prev_q), _val(prev_a, 2)
    if prev_sum is None or prev_base is None or prev_base <= 0:
        return None
    diff = (cur_sum / base * 100) - (prev_sum / prev_base * 100)
    return 1 if diff >= 3 else (-1 if diff <= -3 else 0)


def compute_revision_signal(disclosures_for_code):
    """直近開示(45日保持)の業績予想修正から方向シグナルを返す(常に0/±1、Noneにはしない)。
    新しい順に走査し、方向が分類できた最初の開示を採用。修正が無ければ0。"""
    for d in disclosures_for_code:
        if d.get("doc_type") != "業績予想修正":
            continue
        label = ga.classify_surprise_type(d.get("doc_type"), d.get("title"))
        if label == "業績予想上方修正":
            return 1
        if label == "業績予想下方修正":
            return -1
        return 0
    return 0


def score_stock(entry, disclosures_for_code, disclosures_available):
    """1銘柄分のスコアと内訳を返す。財務データ由来シグナルが1つも無ければ
    score=None, insufficient_data=True とし、黙って0点にはしない。"""
    annual, quarterly = entry.get("a") or [], entry.get("q") or []
    growth, margin = compute_growth_and_margin(annual)
    progress = compute_progress_signal(annual, quarterly)
    revision = compute_revision_signal(disclosures_for_code) if disclosures_available else None
    signals = {"growth": growth, "margin": margin, "progress": progress, "revision": revision}
    breakdown = [{"key": k, "label": LABELS[k], "signal": v, "note": NOTES[k][v]}
                 for k, v in signals.items()]
    # 業績予想修正は「開示が無い=0」も有効値になるため、財務データ由来の3項目が
    # 1つも算出できない銘柄は修正シグナルだけを根拠に点数を出さない(フェイルラウド)。
    if growth is None and margin is None and progress is None:
        return {"score": None, "insufficient_data": True, "breakdown": breakdown}
    available = [v for v in signals.values() if v is not None]
    score = max(0, min(100, round(50 + (sum(available) / len(available)) * 50)))
    return {"score": score, "insufficient_data": False, "breakdown": breakdown}


def generate_scores(financials, disclosures, disclosures_available=True):
    """financials(stocks辞書)とdisclosures(リスト)からスコア辞書を返す純粋関数。"""
    disc_by_code = {}
    for d in sorted(disclosures or [], key=lambda x: x.get("published_at") or "", reverse=True):
        code = str(d.get("code") or "")
        if code:
            disc_by_code.setdefault(code, []).append(d)
    stocks = (financials or {}).get("stocks") or {}
    return {code: score_stock(entry, disc_by_code.get(code, []), disclosures_available)
            for code, entry in stocks.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="frontend/data")
    args = ap.parse_args()

    fin_path = os.path.join(args.data, "financials.json")
    if not os.path.exists(fin_path):
        log(f"{fin_path} が無いため終了(フェイルラウド: スコア生成をスキップ)")
        sys.exit(1)
    try:
        with open(fin_path, encoding="utf-8") as f:
            financials = json.load(f)
    except (OSError, ValueError) as e:
        log(f"{fin_path} の読み込みに失敗したため終了: {e}")
        sys.exit(1)

    disc_path = os.path.join(args.data, "disclosures.json")
    disclosures, disclosures_available = [], os.path.exists(disc_path)
    if disclosures_available:
        try:
            with open(disc_path, encoding="utf-8") as f:
                disclosures = json.load(f)
        except (OSError, ValueError) as e:
            log(f"{disc_path} の読み込みに失敗したため業績予想修正シグナルは算出不可扱いにする: {e}")
            disclosures_available = False
    else:
        log(f"{disc_path} が無いため業績予想修正シグナルは算出不可扱いにする")

    scores = generate_scores(financials, disclosures, disclosures_available)
    insufficient = sum(1 for v in scores.values() if v["insufficient_data"])
    out_path = os.path.join(args.data, "scores.json")
    now = datetime.datetime.now(JST)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"), "scores": scores},
                   f, ensure_ascii=False, separators=(",", ":"))
    log(f"スコア: {len(scores)}銘柄 (データ不足 {insufficient}銘柄) → {out_path}")


if __name__ == "__main__":
    main()
