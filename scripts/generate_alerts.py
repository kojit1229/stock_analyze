#!/usr/bin/env python3
"""マイ銘柄の終値アラート生成 (GitHub Actions で実行)。

入力:
- frontend/data/prices.json   : 終値・前日比・52週高安・出来高 (fetch_real_data.py が生成)
- frontend/data/schedule.json : 決算発表予定
- frontend/data/stocks.json   : 銘柄名の解決用
- config/user_data.json       : アプリが自動バックアップしたユーザーデータ
                                (マイ銘柄+重要度、アラート設定 kessan_settings_v1)

出力:
- frontend/data/alerts.json   : アプリのホーム画面に表示するアラート
- (新規アラートがあれば) GitHub Issue を作成し、@オーナーへメンション
  → GitHub の通知メールが届く (SMTP等の秘密情報は不要)

アラート種別 (重要度1〜5ごとに有効/無効と閾値を設定可能):
- price_move : 前日比 ±X% 以上の変動
- wk52       : 52週高値/安値の更新
- volume     : 出来高急増 (3ヶ月平均のY倍以上)
- earnings   : 決算発表の前日・当日

依存: Python 標準ライブラリのみ。
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

JST = datetime.timezone(datetime.timedelta(hours=9))
KEEP_DAYS = 30

# 重要度ごとの既定値 (設定画面で未保存の場合に使う)
DEFAULT_SETTINGS = {
    "email": True,
    "levels": {
        "5": {"price_move": 1, "pct": 3, "wk52": 1, "volume": 1, "vol_x": 2, "earnings": 1},
        "4": {"price_move": 1, "pct": 3, "wk52": 1, "volume": 1, "vol_x": 2, "earnings": 1},
        "3": {"price_move": 1, "pct": 5, "wk52": 0, "volume": 0, "vol_x": 2, "earnings": 1},
        "2": {"price_move": 0, "pct": 5, "wk52": 0, "volume": 0, "vol_x": 2, "earnings": 1},
        "1": {"price_move": 0, "pct": 5, "wk52": 0, "volume": 0, "vol_x": 2, "earnings": 0},
    },
}


def log(msg):
    print(msg, flush=True)


def load_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def parse_user_data(user_payload):
    """バックアップJSON (config/user_data.json) からマイ銘柄と設定を取り出す。"""
    mystocks = []
    settings = None
    data = (user_payload or {}).get("data") or {}
    try:
        local = json.loads(data.get("kessan_local_v1") or "{}")
        for m in local.get("mystocks") or []:
            code = str(m.get("code") or "")
            if code:
                mystocks.append({
                    "code": code,
                    "importance": int(m.get("importance") or 3),
                    "memo": m.get("memo") or "",
                })
    except (ValueError, TypeError):
        pass
    try:
        s = json.loads(data.get("kessan_settings_v1") or "null")
        if isinstance(s, dict) and isinstance(s.get("alerts"), dict):
            settings = s["alerts"]
    except (ValueError, TypeError):
        pass
    return mystocks, settings


def level_conf(settings, importance):
    levels = (settings or {}).get("levels") or {}
    conf = levels.get(str(importance))
    if not isinstance(conf, dict):
        conf = DEFAULT_SETTINGS["levels"][str(max(1, min(5, importance)))]
    return conf


def fnum(v):
    return v if isinstance(v, (int, float)) else None


def generate(prices, schedule, mystocks, settings, today):
    """アラート判定の純粋関数。alerts のリストを返す (dedupe は呼び出し側)。

    prices:   {"date": "YYYY-MM-DD", "stocks": {code: [close, chg%, hi52, lo52, vol, avgVol]}}
    schedule: [{"code","announce_date","fiscal_type"}...]
    mystocks: [{"code","importance"}...]
    """
    alerts = []
    pstocks = (prices or {}).get("stocks") or {}
    pdate = (prices or {}).get("date") or today
    tomorrow = (datetime.date.fromisoformat(today) + datetime.timedelta(days=1)).isoformat()
    sched_by_code = {}
    for r in schedule or []:
        d = r.get("announce_date") or ""
        if d in (today, tomorrow):
            sched_by_code.setdefault(str(r.get("code")), []).append(r)

    for m in mystocks:
        code = m["code"]
        imp = m.get("importance") or 3
        conf = level_conf(settings, imp)
        row = pstocks.get(code)
        if row:
            close, chg, hi, lo, vol, avg = (list(row) + [None] * 6)[:6]
            close, chg, hi, lo, vol, avg = map(fnum, (close, chg, hi, lo, vol, avg))
            # 前日比 ±X% 以上
            if conf.get("price_move") and chg is not None:
                pct = float(conf.get("pct") or 5)
                if abs(chg) >= pct:
                    alerts.append({
                        "date": pdate, "code": code, "importance": imp,
                        "type": "price_move",
                        "title": f"前日比 {chg:+.1f}%",
                        "detail": f"終値 {close:,.1f}円 (設定閾値 ±{pct:g}%)",
                    })
            # 52週高値/安値
            if conf.get("wk52") and close is not None:
                if hi is not None and close >= hi:
                    alerts.append({
                        "date": pdate, "code": code, "importance": imp,
                        "type": "wk52_high",
                        "title": "52週高値を更新",
                        "detail": f"終値 {close:,.1f}円",
                    })
                elif lo is not None and close <= lo:
                    alerts.append({
                        "date": pdate, "code": code, "importance": imp,
                        "type": "wk52_low",
                        "title": "52週安値を更新",
                        "detail": f"終値 {close:,.1f}円",
                    })
            # 出来高急増
            if conf.get("volume") and vol and avg:
                x = float(conf.get("vol_x") or 2)
                if vol >= avg * x:
                    alerts.append({
                        "date": pdate, "code": code, "importance": imp,
                        "type": "volume",
                        "title": f"出来高急増 ({vol / avg:.1f}倍)",
                        "detail": f"出来高 {int(vol):,} / 3ヶ月平均 {int(avg):,}",
                    })
        # 決算発表の前日・当日
        if conf.get("earnings"):
            for r in sched_by_code.get(code, []):
                when = "本日" if r["announce_date"] == today else "明日"
                alerts.append({
                    "date": today, "code": code, "importance": imp,
                    "type": "earnings",
                    "title": f"{when}決算発表 ({r.get('fiscal_type') or ''})",
                    "detail": f"発表予定日 {r['announce_date']}",
                })
    return alerts


def alert_key(a):
    return f"{a['date']}|{a['code']}|{a['type']}"


def create_issue(new_alerts, names, today):
    """GitHub Issue を作成してオーナーにメンションする (通知メールが届く)。"""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")  # 例: kojit1229/stock_analyze
    if not token or not repo:
        log("GITHUB_TOKEN/GITHUB_REPOSITORY が無いため Issue 通知はスキップ")
        return False
    owner = repo.split("/")[0]
    icon = {"price_move": "📈", "wk52_high": "🚀", "wk52_low": "🔻",
            "volume": "📊", "earnings": "📅"}
    lines = [f"@{owner} マイ銘柄に {len(new_alerts)}件のアラートがあります。", "",
             "| 銘柄 | 重要度 | アラート | 詳細 |", "|---|---|---|---|"]
    for a in sorted(new_alerts, key=lambda x: (-x["importance"], x["code"])):
        name = names.get(a["code"], "")
        star = "★" * max(0, min(5, a["importance"]))
        lines.append(f"| {a['code']} {name} | {star} | {icon.get(a['type'], '🔔')} "
                     f"{a['title']} | {a['detail']} |")
    lines += ["", f"詳細: https://{owner}.github.io/{repo.split('/')[1]}/ のホーム画面",
              "", "> このIssueは決算ナビのアラート機能が自動作成しました。確認後はクローズしてください。"]
    body = {
        "title": f"🔔 株価アラート {today} ({len(new_alerts)}件)",
        "body": "\n".join(lines),
        "labels": ["alert"],
    }
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            log(f"Issue 通知を作成: HTTP {r.status}")
            return True
    except Exception as e:  # noqa: BLE001
        log(f"Issue 作成に失敗: {type(e).__name__}: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="frontend/data")
    ap.add_argument("--user", default="config/user_data.json")
    ap.add_argument("--force", action="store_true",
                    help="場中(JST15時前)でもアラートを生成する")
    args = ap.parse_args()

    now = datetime.datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    if now.hour < 15 and not args.force:
        log(f"JST {now.hour}時 (15時前) のためアラート生成をスキップ (場中の暫定値を避ける)")
        return

    prices = load_json(os.path.join(args.data, "prices.json"), None)
    if not prices or not (prices.get("stocks") or {}):
        log("prices.json が無いためスキップ")
        return
    schedule = load_json(os.path.join(args.data, "schedule.json"), [])
    stocks = load_json(os.path.join(args.data, "stocks.json"), [])
    names = {s.get("code"): s.get("name", "") for s in stocks if isinstance(s, dict)}

    user = load_json(args.user, None)
    if not user:
        log(f"{args.user} が無いためスキップ (アプリの設定画面から自動バックアップを有効にしてください)")
        return
    mystocks, settings = parse_user_data(user)
    if not mystocks:
        log("バックアップにマイ銘柄が無いためスキップ")
        return
    settings = settings or DEFAULT_SETTINGS

    out_path = os.path.join(args.data, "alerts.json")
    existing = load_json(out_path, {})
    old_alerts = existing.get("alerts") or []
    known = {alert_key(a) for a in old_alerts}

    generated = generate(prices, schedule, mystocks, settings, today)
    new_alerts = [a for a in generated if alert_key(a) not in known]
    for a in new_alerts:
        a["name"] = names.get(a["code"], "")

    cutoff = (now - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    merged = [a for a in old_alerts if (a.get("date") or "") >= cutoff] + new_alerts
    merged.sort(key=lambda a: (a.get("date") or "", -a.get("importance", 0), a.get("code") or ""),
                reverse=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                   "alerts": merged}, f, ensure_ascii=False, separators=(",", ":"))
    log(f"アラート: 新規{len(new_alerts)}件 / 保持{len(merged)}件 → {out_path}")

    if new_alerts and settings.get("email", True):
        create_issue(new_alerts, names, today)


if __name__ == "__main__":
    main()
