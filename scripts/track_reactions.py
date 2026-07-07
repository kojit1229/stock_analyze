#!/usr/bin/env python3
"""イベント→株価反応の記録 (GitHub Actions で毎営業日の引け後に実行)。

「決算発表の翌日に株価がどう動いたか」を銘柄ごとに蓄積し、
決算プレイの癖 (好決算でも売られる等) を分析できるようにする。
決算発表以外にも、以下のイベントを同じ枠組みで記録する:

- 開示イベント: 決算短信 / 訂正決算短信 / 業績予想修正 / 配当予想修正 /
  自己株式取得 (disclosures.json から)
- 株価イベント: 急変動 (前日比±8%以上) / 52週高値・安値の更新 /
  出来高急増 (3ヶ月平均の5倍以上) (prices.json から、全銘柄)

各イベントについて「当日の反応 (c1)」と「翌営業日の反応 (c2)」を%で記録する。
15時以降に公表された開示は、翌営業日を「当日」として扱う。

出力 frontend/data/reactions.json:
{
  "updated_at": ...,
  "tail": {"dates": ["YYYY-MM-DD", ...最大8日], "closes": {code: [終値, ...]}},
  "events": [{"d": 反応日, "code", "t": 種別, "l": ラベル,
              "c1": 当日%, "c2": 翌日%|null, "nd": 翌営業日|null}, ...]
}

依存: Python 標準ライブラリのみ。
"""
import argparse
import datetime
import json
import os

JST = datetime.timezone(datetime.timedelta(hours=9))
TAIL_DAYS = 30           # 終値の履歴保持日数 (反応計算・連続下落判定・騰落レシオ25日用)
EVENT_KEEP_DAYS = 400    # イベントの保持日数
EVENT_CAP = 20000        # イベント総数の上限
DISC_EVENT_TYPES = ("決算短信", "訂正決算短信", "業績予想修正", "配当予想修正", "自己株式取得")
MOVE_TH = 8.0            # 急変動イベントの閾値 (%)
VOL_TH = 5.0             # 出来高急増イベントの閾値 (倍)
PRICE_EVENTS_PER_DAY = 150  # 株価イベントの1日あたり上限 (|変動率|順)


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


def pct(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return round((cur - prev) / prev * 100, 2)


def close_on(tail, code, date):
    """tail から指定日の終値を返す (無ければ None)。"""
    try:
        i = tail["dates"].index(date)
    except ValueError:
        return None
    row = tail["closes"].get(code)
    return fnum(row[i]) if row and i < len(row) else None


def next_trading_date(tail, date):
    """tail 内で date より後の最初の営業日 (無ければ None)。"""
    for d in tail["dates"]:
        if d > date:
            return d
    return None


def prev_trading_date(tail, date):
    prev = None
    for d in tail["dates"]:
        if d < date:
            prev = d
        else:
            break
    return prev


def reaction_date(tail, pub_at):
    """公表日時から「反応日」を求める。15時以降の公表は翌営業日。

    pub_at: "YYYY-MM-DDTHH:MM:SS"。反応日がまだ tail に無い場合は None
    (翌営業日のデータ到着を待つ)。
    """
    if not pub_at or len(pub_at) < 10:
        return None
    d = pub_at[:10]
    hour = 0
    if len(pub_at) >= 13:
        try:
            hour = int(pub_at[11:13])
        except ValueError:
            hour = 0
    if hour >= 15:
        return next_trading_date(tail, d)
    return d if d in tail["dates"] else next_trading_date(tail, d)


def append_tail(tail, prices):
    """今日の終値スナップショットを tail に追記する (同日再実行は上書き)。"""
    date = prices.get("date")
    stocks = prices.get("stocks") or {}
    if not date or not stocks:
        return tail
    if date in tail["dates"]:
        i = tail["dates"].index(date)
    else:
        tail["dates"].append(date)
        tail["dates"].sort()
        i = tail["dates"].index(date)
        for row in tail["closes"].values():
            row.insert(i, None)
    codes = set(tail["closes"].keys()) | set(stocks.keys())
    n = len(tail["dates"])
    for code in codes:
        row = tail["closes"].setdefault(code, [None] * n)
        while len(row) < n:
            row.append(None)
        p = stocks.get(code)
        if p and fnum(p[0]) is not None:
            row[i] = p[0]
    # 保持日数を超えた分を落とす
    while len(tail["dates"]) > TAIL_DAYS:
        tail["dates"].pop(0)
        for row in tail["closes"].values():
            row.pop(0)
    return tail


def make_events(tail, prices, disclosures, existing_keys):
    """新規イベントを生成する (dedupe キー: 反応日|code|種別)。"""
    events = []
    today = prices.get("date")

    # --- 開示イベント ---
    for d in disclosures or []:
        if d.get("doc_type") not in DISC_EVENT_TYPES:
            continue
        code = d.get("code")
        rd = reaction_date(tail, d.get("published_at") or "")
        if not code or not rd:
            continue  # 反応日のデータがまだ無い (翌営業日に処理)
        key = f"{rd}|{code}|{d['doc_type']}"
        if key in existing_keys:
            continue
        events.append({"d": rd, "code": code, "t": d["doc_type"],
                       "l": (d.get("title") or "")[:60], "c1": None, "c2": None, "nd": None})
        existing_keys.add(key)

    # --- 株価イベント (本日分・全銘柄) ---
    if today in (tail.get("dates") or []):
        cands = []
        for code, p in (prices.get("stocks") or {}).items():
            close, chg, hi, lo, vol, avg = (list(p) + [None] * 6)[:6]
            close, chg, hi, lo, vol, avg = map(fnum, (close, chg, hi, lo, vol, avg))
            if chg is not None and abs(chg) >= MOVE_TH:
                cands.append((abs(chg), {"d": today, "code": code, "t": "急変動",
                                         "l": f"前日比 {chg:+.1f}%", "c1": None, "c2": None, "nd": None}))
            elif close is not None and hi is not None and close >= hi:
                cands.append((abs(chg or 0), {"d": today, "code": code, "t": "52週高値",
                                              "l": f"終値 {close:,.1f}円", "c1": None, "c2": None, "nd": None}))
            elif close is not None and lo is not None and close <= lo:
                cands.append((abs(chg or 0), {"d": today, "code": code, "t": "52週安値",
                                              "l": f"終値 {close:,.1f}円", "c1": None, "c2": None, "nd": None}))
            elif vol and avg and vol >= avg * VOL_TH:
                cands.append((abs(chg or 0), {"d": today, "code": code, "t": "出来高急増",
                                              "l": f"平均の{vol / avg:.1f}倍", "c1": None, "c2": None, "nd": None}))
        cands.sort(key=lambda x: -x[0])
        for _, ev in cands[:PRICE_EVENTS_PER_DAY]:
            key = f"{ev['d']}|{ev['code']}|{ev['t']}"
            if key not in existing_keys:
                events.append(ev)
                existing_keys.add(key)
    return events


def fill_reactions(events, tail):
    """c1 (当日反応) / c2 (翌日反応) の未計算分を tail から埋める。"""
    filled = 0
    for ev in events:
        if ev.get("c1") is None:
            prev = prev_trading_date(tail, ev["d"])
            if prev:
                c1 = pct(close_on(tail, ev["code"], ev["d"]), close_on(tail, ev["code"], prev))
                if c1 is not None:
                    ev["c1"] = c1
                    filled += 1
        if ev.get("c2") is None:
            nd = next_trading_date(tail, ev["d"])
            if nd:
                c2 = pct(close_on(tail, ev["code"], nd), close_on(tail, ev["code"], ev["d"]))
                if c2 is not None:
                    ev["c2"] = c2
                    ev["nd"] = nd
                    filled += 1
    return filled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="frontend/data")
    ap.add_argument("--force", action="store_true", help="場中(JST15時前)でも実行する")
    args = ap.parse_args()

    now = datetime.datetime.now(JST)
    if now.hour < 15 and not args.force:
        log(f"JST {now.hour}時 (15時前) のため反応記録をスキップ (終値確定後に実行)")
        return

    prices = load_json(os.path.join(args.data, "prices.json"), None)
    if not prices or not (prices.get("stocks") or {}):
        log("prices.json が無いためスキップ")
        return
    disclosures = load_json(os.path.join(args.data, "disclosures.json"), [])

    out_path = os.path.join(args.data, "reactions.json")
    data = load_json(out_path, None)
    if data is None and os.path.exists(out_path) and os.path.getsize(out_path) > 2:
        # 壊れたファイルを空データで上書きすると蓄積した終値履歴が消えるため中止
        log("reactions.json の読み込みに失敗 (破損の可能性)。上書きを避けるため処理を中止します")
        return
    data = data or {}
    tail = data.get("tail") or {"dates": [], "closes": {}}
    events = data.get("events") or []

    append_tail(tail, prices)

    # 直近5日の開示のみイベント化の対象にする (それより古い分は確定済み)
    recent_cut = (now - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
    recent_discs = [d for d in disclosures if (d.get("published_at") or "") >= recent_cut]
    existing_keys = {f"{e['d']}|{e['code']}|{e['t']}" for e in events}
    new_events = make_events(tail, prices, recent_discs, existing_keys)
    events.extend(new_events)

    filled = fill_reactions(events, tail)

    keep_cut = (now - datetime.timedelta(days=EVENT_KEEP_DAYS)).strftime("%Y-%m-%d")
    events = [e for e in events if e["d"] >= keep_cut]
    events.sort(key=lambda e: (e["d"], e["code"]), reverse=True)
    events = events[:EVENT_CAP]

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"updated_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                   "tail": tail, "events": events}, f, ensure_ascii=False, separators=(",", ":"))
    log(f"反応記録: 新規イベント{len(new_events)}件 / 反応値の更新{filled}件 / "
        f"保持{len(events)}件 / tail {len(tail['dates'])}日")


if __name__ == "__main__":
    main()
