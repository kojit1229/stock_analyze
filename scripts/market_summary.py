#!/usr/bin/env python3
"""市況概況の日次スナップショット生成 (GitHub Actions で毎営業日の引け後に実行)。

market タブと同じ統計を計算し、以下を保存する:
- frontend/data/market/YYYY-MM-DD.json : アプリの市況タブが過去日表示に使う
- frontend/data/market/YYYY-MM-DD.md   : 生成AIに読ませやすい Markdown レポート
- frontend/data/market/index.json      : 保存済み日付の一覧 + 主要指標の時系列
                                         (騰落レシオ・値上がり数などの推移チャート用)

複数日にまたがる地合いの傾向分析や、MDファイルをそのまま生成AIへ渡しての
問い合わせを想定している。

依存: Python 標準ライブラリのみ。
"""
import argparse
import datetime
import json
import os

JST = datetime.timezone(datetime.timedelta(hours=9))
KEEP_DAYS = 730          # スナップショットの保持日数 (約2年)
RATIO_DAYS = 25          # 騰落レシオの対象日数
SEGMENTS = ("プライム", "スタンダード", "グロース")
DISC_TYPES = ("決算短信", "訂正決算短信", "業績予想修正", "配当予想修正", "自己株式取得", "決算説明資料")


def log(msg):
    print(msg, flush=True)


def load_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def fnum(v):
    return v if isinstance(v, (int, float)) else None


def updown_ratio(tail, max_days=RATIO_DAYS):
    """終値履歴から騰落レシオを計算する。(レシオ%, 使った日数)"""
    dates = (tail or {}).get("dates") or []
    closes = (tail or {}).get("closes") or {}
    if len(dates) < 2:
        return None, 0
    adv = dec = days = 0
    start = max(1, len(dates) - max_days)
    for i in range(start, len(dates)):
        for row in closes.values():
            if i < len(row):
                a, b = fnum(row[i - 1]), fnum(row[i])
                if a is not None and b is not None:
                    if b > a:
                        adv += 1
                    elif b < a:
                        dec += 1
        days += 1
    return ((adv / dec) * 100 if dec else None), days


def ratio_judge(ratio):
    if ratio is None:
        return None
    if ratio >= 120:
        return "過熱気味"
    if ratio <= 70:
        return "売られすぎ圏"
    return "中立圏"


def brief(r):
    return {"code": r["code"], "name": r["name"], "close": r["close"], "chg": r["chg"]}


def compute_stats(stocks, prices, reactions, schedule, disclosures, today):
    """市況タブと同じ形の統計オブジェクトを組み立てる (JS renderMarketHtml と同形)。"""
    pstocks = (prices or {}).get("stocks") or {}
    rows = []
    for s in stocks:
        p = pstocks.get(s.get("code"))
        if not p:
            continue
        close, chg, hi, lo, vol, avg = [fnum(x) for x in (list(p) + [None] * 6)[:6]]
        cap = fnum(s.get("market_cap"))
        rows.append({
            "code": s["code"], "name": s.get("name", ""), "market": s.get("market", ""),
            "sector": s.get("sector", ""), "cap": cap,
            "close": close, "chg": chg, "hi": hi, "lo": lo, "vol": vol, "avg": avg,
            "value": close * vol if close is not None and vol is not None else None,
            "impact": (cap - cap / (1 + chg / 100)
                       if cap and chg is not None and chg > -100 else None),
        })
    with_chg = [r for r in rows if r["chg"] is not None]
    up = sum(1 for r in with_chg if r["chg"] > 0.0001)
    down = sum(1 for r in with_chg if r["chg"] < -0.0001)
    flat = len(with_chg) - up - down
    avg_chg = sum(r["chg"] for r in with_chg) / len(with_chg) if with_chg else None
    cap_sum = sum(r["cap"] or 0 for r in with_chg)
    wavg = (sum((r["cap"] or 0) * r["chg"] for r in with_chg) / cap_sum) if cap_sum else None
    ratio, ratio_days = updown_ratio((reactions or {}).get("tail"))

    hi52 = sorted([r for r in rows if r["close"] is not None and r["hi"] is not None and r["close"] >= r["hi"]],
                  key=lambda r: -(r["cap"] or 0))
    lo52 = sorted([r for r in rows if r["close"] is not None and r["lo"] is not None and r["close"] <= r["lo"]],
                  key=lambda r: -(r["cap"] or 0))
    value_total = sum(r["value"] or 0 for r in rows)
    cap_change = sum(r["impact"] or 0 for r in rows)

    # セクター別 (時価総額加重)
    sec = {}
    for r in with_chg:
        if not r["sector"]:
            continue
        m = sec.setdefault(r["sector"], {"cap": 0.0, "wsum": 0.0, "n": 0, "sum": 0.0})
        w = r["cap"] or 0
        m["cap"] += w
        m["wsum"] += w * r["chg"]
        m["n"] += 1
        m["sum"] += r["chg"]
    sectors = sorted(
        [{"name": k, "chg": (m["wsum"] / m["cap"] if m["cap"] else m["sum"] / m["n"]), "n": m["n"]}
         for k, m in sec.items()],
        key=lambda x: -x["chg"])

    segments = []
    for seg in SEGMENTS:
        rs = [r for r in with_chg if r["market"] == seg]
        cs = sum(r["cap"] or 0 for r in rs)
        segments.append({"seg": seg, "n": len(rs),
                         "chg": (sum((r["cap"] or 0) * r["chg"] for r in rs) / cs) if cs else None})

    impacts = sorted([r for r in rows if r["impact"] is not None], key=lambda r: -r["impact"])
    vol_spike = sorted([r for r in rows if r["vol"] and r["avg"] and r["vol"] >= r["avg"] * 5],
                       key=lambda r: -(r["vol"] / r["avg"]))[:10]

    # 今週の決算発表 (日別) と本日の開示件数
    monday = datetime.date.fromisoformat(today)
    monday -= datetime.timedelta(days=monday.weekday())
    week_end = (monday + datetime.timedelta(days=6)).isoformat()
    by_day = {}
    for r in schedule or []:
        d = r.get("announce_date") or ""
        if monday.isoformat() <= d <= week_end:
            by_day[d] = by_day.get(d, 0) + 1
    disc_today = {}
    for d in disclosures or []:
        if (d.get("published_at") or "")[:10] == today and d.get("doc_type") in DISC_TYPES:
            disc_today[d["doc_type"]] = disc_today.get(d["doc_type"], 0) + 1

    stats = {
        "date": (prices or {}).get("date") or today,
        "summary": {
            "total": len(with_chg), "up": up, "down": down, "flat": flat,
            "avg": avg_chg, "wavg": wavg,
            "ratio": ratio, "ratio_days": ratio_days, "ratio_judge": ratio_judge(ratio),
            "surge": sum(1 for r in with_chg if r["chg"] >= 8),
            "plunge": sum(1 for r in with_chg if r["chg"] <= -8),
            "hi52_count": len(hi52), "lo52_count": len(lo52),
            "value_total": value_total, "cap_total": cap_sum, "cap_change": cap_change,
            "sectors_up": sum(1 for s in sectors if s["chg"] > 0),
            "sectors_down": sum(1 for s in sectors if s["chg"] < 0),
        },
        "sectors": sectors,
        "segments": segments,
        "rank_value": [dict(brief(r), value=r["value"]) for r in
                       sorted([r for r in rows if r["value"] is not None], key=lambda r: -r["value"])[:15]],
        "gainers": [brief(r) for r in sorted(with_chg, key=lambda r: -r["chg"])[:10]],
        "losers": [brief(r) for r in sorted(with_chg, key=lambda r: r["chg"])[:10]],
        "hi52": [brief(r) for r in hi52[:10]],
        "lo52": [brief(r) for r in lo52[:10]],
        "impact_pos": [dict(brief(r), impact=r["impact"]) for r in impacts[:8] if r["impact"] > 0],
        "impact_neg": [dict(brief(r), impact=r["impact"]) for r in impacts[::-1][:8] if r["impact"] < 0],
        "vol_spike": [dict(brief(r), x=r["vol"] / r["avg"]) for r in vol_spike],
        "earnings_week": sorted(by_day.items()),
        "disclosures_today": disc_today,
    }
    stats["comment"] = build_comment(stats)
    return stats


def oku(v):
    """円 → 「1.2兆円」「345億円」表記。"""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:,.2f}兆円"
    return f"{v / 1e8:,.0f}億円"


def pctf(v, digits=2):
    return "-" if v is None else f"{v:+.{digits}f}%"


def build_comment(st):
    """ルールベースの概況コメント (MDとアプリの両方に載せる)。"""
    s = st["summary"]
    parts = []
    if s["up"] or s["down"]:
        lead = "値上がり優勢" if s["up"] > s["down"] else ("値下がり優勢" if s["down"] > s["up"] else "拮抗")
        parts.append(f"値上がり{s['up']:,}銘柄・値下がり{s['down']:,}銘柄で{lead}。"
                     f"時価総額加重の平均騰落率は{pctf(s['wavg'])} (単純平均{pctf(s['avg'])})。")
    if st["sectors"]:
        top, bottom = st["sectors"][0], st["sectors"][-1]
        parts.append(f"セクターは{s['sectors_up']}業種が上昇・{s['sectors_down']}業種が下落。"
                     f"最強は{top['name']}({pctf(top['chg'])})、最弱は{bottom['name']}({pctf(bottom['chg'])})。")
    if s["ratio"] is not None:
        parts.append(f"騰落レシオ({s['ratio_days']}日)は{s['ratio']:.0f}%で{s['ratio_judge']}。")
    parts.append(f"52週高値更新{s['hi52_count']}件・安値更新{s['lo52_count']}件"
                 f" (ネット新高値{s['hi52_count'] - s['lo52_count']:+d}件)。")
    if st["rank_value"]:
        names = "、".join(f"{r['name']}({r['code']})" for r in st["rank_value"][:3])
        parts.append(f"売買代金上位は{names}。概算売買代金合計は{oku(s['value_total'])}。")
    if st["disclosures_today"]:
        d = st["disclosures_today"]
        parts.append("本日の開示: " + "、".join(f"{k}{v}件" for k, v in sorted(d.items(), key=lambda x: -x[1])) + "。")
    return "".join(parts)


def to_markdown(st):
    """生成AIに読ませやすい Markdown レポート。"""
    s = st["summary"]
    L = []
    L.append(f"# 市況概況 {st['date']}")
    L.append("")
    L.append("東証全銘柄(プライム/スタンダード/グロース)の終値ベースの市況サマリです。")
    L.append("")
    L.append("## 概況コメント")
    L.append("")
    L.append(st["comment"])
    L.append("")
    L.append("## サマリ")
    L.append("")
    L.append(f"- 対象銘柄数: {s['total']:,}")
    L.append(f"- 値上がり: {s['up']:,} / 値下がり: {s['down']:,} / 変わらず: {s['flat']:,}")
    L.append(f"- 平均騰落率: 単純 {pctf(s['avg'])} / 時価総額加重 {pctf(s['wavg'])}")
    L.append(f"- 騰落レシオ({s['ratio_days']}日): "
             + ("-" if s["ratio"] is None else f"{s['ratio']:.0f}% ({s['ratio_judge']})"))
    L.append(f"- 急騰(+8%以上): {s['surge']}銘柄 / 急落(-8%以下): {s['plunge']}銘柄")
    L.append(f"- 52週高値更新: {s['hi52_count']}銘柄 / 52週安値更新: {s['lo52_count']}銘柄"
             f" (ネット新高値 {s['hi52_count'] - s['lo52_count']:+d})")
    L.append(f"- 上昇業種: {s['sectors_up']} / 下落業種: {s['sectors_down']} (東証33業種)")
    L.append(f"- 概算売買代金合計: {oku(s['value_total'])}")
    L.append(f"- 時価総額合計: {oku(s['cap_total'])} (前日比 {oku(s['cap_change'])})")
    L.append("")
    L.append("## セクター別騰落 (33業種・時価総額加重)")
    L.append("")
    L.append("| 業種 | 騰落率 | 銘柄数 |")
    L.append("|---|---|---|")
    for x in st["sectors"]:
        L.append(f"| {x['name']} | {pctf(x['chg'])} | {x['n']} |")
    L.append("")
    L.append("## 市場区分別騰落 (時価総額加重)")
    L.append("")
    L.append("| 市場 | 騰落率 | 銘柄数 |")
    L.append("|---|---|---|")
    for x in st["segments"]:
        L.append(f"| {x['seg']} | {pctf(x['chg'])} | {x['n']:,} |")
    L.append("")

    def rank_md(title, items, cols, fmt):
        L.append(f"## {title}")
        L.append("")
        if not items:
            L.append("該当なし")
            L.append("")
            return
        L.append("| コード | 銘柄名 | " + " | ".join(cols) + " |")
        L.append("|---|---|" + "---|" * len(cols))
        for r in items:
            L.append(f"| {r['code']} | {r['name']} | " + " | ".join(fmt(r)) + " |")
        L.append("")

    rank_md("売買代金ランキング (概算: 終値×出来高)", st["rank_value"], ["売買代金", "前日比"],
            lambda r: [oku(r.get("value")), pctf(r["chg"])])
    rank_md("値上がり率ランキング", st["gainers"], ["前日比", "終値"],
            lambda r: [pctf(r["chg"]), "-" if r["close"] is None else f"{r['close']:,.0f}円"])
    rank_md("値下がり率ランキング", st["losers"], ["前日比", "終値"],
            lambda r: [pctf(r["chg"]), "-" if r["close"] is None else f"{r['close']:,.0f}円"])
    rank_md("52週高値更新 (時価総額上位)", st["hi52"], ["終値", "前日比"],
            lambda r: ["-" if r["close"] is None else f"{r['close']:,.0f}円", pctf(r["chg"])])
    rank_md("52週安値更新 (時価総額上位)", st["lo52"], ["終値", "前日比"],
            lambda r: ["-" if r["close"] is None else f"{r['close']:,.0f}円", pctf(r["chg"])])
    rank_md("指数インパクト: 時価総額の増加上位", st["impact_pos"], ["増加額"],
            lambda r: [oku(r.get("impact"))])
    rank_md("指数インパクト: 時価総額の減少上位", st["impact_neg"], ["減少額"],
            lambda r: [oku(r.get("impact"))])
    rank_md("出来高急増 (3ヶ月平均の5倍以上)", st["vol_spike"], ["出来高倍率", "前日比"],
            lambda r: [f"{r.get('x', 0):.1f}倍", pctf(r["chg"])])

    L.append("## 本日の開示件数")
    L.append("")
    if st["disclosures_today"]:
        for k, v in sorted(st["disclosures_today"].items(), key=lambda x: -x[1]):
            L.append(f"- {k}: {v}件")
    else:
        L.append("- 決算関連の開示はありません")
    L.append("")
    L.append("## 今週の決算発表予定 (日別)")
    L.append("")
    for d, n in st["earnings_week"]:
        L.append(f"- {d}: {n}件")
    L.append("")
    L.append("---")
    L.append("注: 52週高値/安値は年初来・上場来の代替。指数インパクトは時価総額の増減額であり、"
             "株価加重の日経平均の厳密な寄与度とは異なる。出典: JPX/TDnet/Yahoo Finance (決算ナビ自動生成)。")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="frontend/data")
    ap.add_argument("--force", action="store_true", help="場中(JST15時前)でも実行する")
    args = ap.parse_args()

    now = datetime.datetime.now(JST)
    if now.hour < 15 and not args.force:
        log(f"JST {now.hour}時 (15時前) のため市況スナップショットをスキップ (終値確定後に実行)")
        return

    prices = load_json(os.path.join(args.data, "prices.json"), None)
    stocks = load_json(os.path.join(args.data, "stocks.json"), [])
    if not prices or not (prices.get("stocks") or {}) or not stocks:
        log("prices.json / stocks.json が無いためスキップ")
        return
    reactions = load_json(os.path.join(args.data, "reactions.json"), {})
    schedule = load_json(os.path.join(args.data, "schedule.json"), [])
    disclosures = load_json(os.path.join(args.data, "disclosures.json"), [])

    today = now.strftime("%Y-%m-%d")
    stats = compute_stats(stocks, prices, reactions, schedule, disclosures, today)
    stats["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    date = stats["date"]

    out_dir = os.path.join(args.data, "market")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{date}.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out_dir, f"{date}.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(stats))

    # index.json: 日付一覧 + 主要指標の時系列 (推移チャート用)
    idx_path = os.path.join(out_dir, "index.json")
    idx = load_json(idx_path, None)
    if idx is None and os.path.exists(idx_path) and os.path.getsize(idx_path) > 2:
        # 壊れたindexを空で上書きすると時系列の蓄積が消えるため、indexの更新のみ中止
        log("index.json の読み込みに失敗 (破損の可能性)。時系列の上書きを避けるため index 更新をスキップします")
        return
    idx = idx or {}
    series = {r[0]: r for r in (idx.get("series") or []) if isinstance(r, list) and r}
    s = stats["summary"]
    series[date] = [date, s["up"], s["down"],
                    round(s["ratio"], 1) if s["ratio"] is not None else None,
                    round(s["wavg"], 3) if s["wavg"] is not None else None,
                    s["hi52_count"], s["lo52_count"],
                    round(s["value_total"] / 1e12, 3) if s["value_total"] else None]
    cutoff = (now - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    rows = sorted([r for r in series.values() if r[0] >= cutoff])
    # 保持期間を過ぎたスナップショットは削除
    for fn in os.listdir(out_dir):
        if fn[:10] < cutoff and (fn.endswith(".json") or fn.endswith(".md")) and fn != "index.json":
            try:
                os.remove(os.path.join(out_dir, fn))
            except OSError:
                pass
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump({"updated_at": stats["generated_at"],
                   "columns": ["date", "up", "down", "ratio", "wavg", "hi52", "lo52", "value_total_cho"],
                   "dates": [r[0] for r in rows],
                   "series": rows}, f, ensure_ascii=False, separators=(",", ":"))
    log(f"市況スナップショット: {date}.json / {date}.md / index.json ({len(rows)}日分) を書き出しました")


if __name__ == "__main__":
    main()
