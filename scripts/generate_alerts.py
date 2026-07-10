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
- disclosure : 重要開示 (業績予想修正・配当予想修正・自己株式取得・訂正決算短信)
  - 業績予想修正/配当予想修正は開示タイトルから上方・下方/増配・減配を判定できれば
    より具体的な種別 (例: disclosure_業績予想上方修正) にする (決算サプライズ検出)。
- surprise   : 決算サプライズ (frontend/data/financials.json の構造化数値が基本。
    当日更新分は取得できれば決算短信サマリーXBRL (xbrl_parser.py) の連結営業利益で
    上書きし、より確実な確定値にする。XBRL取得・解析に失敗した場合は Yahoo 由来値
    へフォールバックする (フォールバックした旨は必ずログに出す)。PDF本文はパース
    しない — 対象はXBRLタグのみ)。黒字転換・営業利益の最高益更新を検出する。
- streak     : 3日以上の連続下落/連続上昇 (reactions.json の終値履歴から判定)
- reaction   : 決算発表への株価反応 (反応日/翌営業日に±X%以上動いた)

依存: Python 標準ライブラリのみ。
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys
import urllib.request

# xbrl_parser.py は scripts/ 内の同居モジュール。generate_alerts.py がテストから
# importlib 経由で読み込まれる場合でも解決できるよう、パスを直接指定して読み込む
# (通常の `import xbrl_parser` は sys.path に scripts/ が無いと失敗するため)。
_xbrl_spec = importlib.util.spec_from_file_location(
    "xbrl_parser", os.path.join(os.path.dirname(__file__), "xbrl_parser.py"))
xbrl_parser = importlib.util.module_from_spec(_xbrl_spec)
_xbrl_spec.loader.exec_module(xbrl_parser)

_xbrl_fetch_spec = importlib.util.spec_from_file_location(
    "xbrl_fetch", os.path.join(os.path.dirname(__file__), "xbrl_fetch.py"))
xbrl_fetch = importlib.util.module_from_spec(_xbrl_fetch_spec)
_xbrl_fetch_spec.loader.exec_module(xbrl_fetch)

JST = datetime.timezone(datetime.timedelta(hours=9))
KEEP_DAYS = 30
DISC_ALERT_TYPES = ("業績予想修正", "配当予想修正", "自己株式取得", "訂正決算短信")
# 開示タイトルから方向性まで分類できるルール (doc_type -> [(キーワード群, 分類後ラベル)])
# 「PDF regex禁止」の対象は決算短信PDF本文の数値抽出であり、TDnet開示タイトルの
# 分類は fetch_real_data.py の DOC_TYPE_RULES と同じ既存パターン(タイトル文字列判定)。
SURPRISE_TITLE_RULES = {
    "業績予想修正": (
        (("上方", "増額"), "業績予想上方修正"),
        (("下方", "減額"), "業績予想下方修正"),
    ),
    "配当予想修正": (
        (("増配",), "配当増配"),
        (("減配",), "配当減配"),
    ),
}

# 重要度ごとの既定値 (設定画面で未保存の場合に使う)
DEFAULT_SETTINGS = {
    "email": True,
    "levels": {
        "5": {"price_move": 1, "pct": 3, "wk52": 1, "volume": 1, "vol_x": 2, "earnings": 1,
              "disclosure": 1, "streak": 1, "reaction": 1, "rpct": 5},
        "4": {"price_move": 1, "pct": 3, "wk52": 1, "volume": 1, "vol_x": 2, "earnings": 1,
              "disclosure": 1, "streak": 1, "reaction": 1, "rpct": 5},
        "3": {"price_move": 1, "pct": 5, "wk52": 0, "volume": 0, "vol_x": 2, "earnings": 1,
              "disclosure": 1, "streak": 0, "reaction": 1, "rpct": 5},
        "2": {"price_move": 0, "pct": 5, "wk52": 0, "volume": 0, "vol_x": 2, "earnings": 1,
              "disclosure": 1, "streak": 0, "reaction": 0, "rpct": 5},
        "1": {"price_move": 0, "pct": 5, "wk52": 0, "volume": 0, "vol_x": 2, "earnings": 0,
              "disclosure": 0, "streak": 0, "reaction": 0, "rpct": 5},
    },
}


def log(msg):
    print(msg, flush=True)


def load_json(path, fallback):
    """ファイル未存在は fallback を返すが、存在するファイルの読込・パース失敗は
    黙殺せず例外を送出する(alerts.json 等の冪等性台帳の破損を空扱いにして
    再通知・再処理を発生させないため。FI原則)。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return fallback
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{path} の読み込みに失敗しました(壊れている可能性): {e}") from e


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


def streak_of(tail, code, min_days=3):
    """終値履歴から連続下落/上昇の日数を返す。(日数, 方向) 方向: -1/+1/0。

    欠損日 (None) を挟んだら連続とはみなさない。直近日が欠損している場合も
    「現在の連続」ではないため 0 を返す。
    """
    closes = [fnum(v) for v in (tail or {}).get("closes", {}).get(code, [])]
    if len(closes) < min_days + 1 or closes[-1] is None:
        return 0, 0
    n = 0
    direction = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] is None or closes[i - 1] is None:
            break
        d = 1 if closes[i] > closes[i - 1] else (-1 if closes[i] < closes[i - 1] else 0)
        if d == 0:
            break
        if direction == 0:
            direction = d
        elif d != direction:
            break
        n += 1
    return (n, direction) if n >= min_days else (0, 0)


def classify_surprise_type(doc_type, title):
    """開示タイトルから方向性 (上方/下方修正・増配/減配) を分類する。分類不可なら None。"""
    title = title or ""
    for keywords, label in SURPRISE_TITLE_RULES.get(doc_type, ()):
        if any(k in title for k in keywords):
            return label
    return None


def xbrl_confirmed_operating_income(code, xbrl_lookup):
    """xbrl_lookup(code) で決算短信サマリーXBRLを取得できれば、そこから
    連結優先(単体劣後)の営業利益を返す。取得できない・解析に失敗した場合は
    None を返し、その旨をログに出す(黙殺しない。設計原則4)。

    xbrl_lookup: callable(code) -> bytes|None。実際のTDnetからの取得経路は
    本チケットの範囲外(実ネットワークアクセスはテスト対象外のため)。呼び出し
    元がフィクスチャや将来のキャッシュ実装を注入できるよう依存性注入にしている。
    """
    if xbrl_lookup is None:
        return None
    try:
        xbrl_bytes = xbrl_lookup(code)
    except Exception as e:  # noqa: BLE001 — 取得経路の例外は握りつぶさずログにする
        log(f"XBRL取得失敗 ({code}): {type(e).__name__}: {e} — Yahoo由来値にフォールバックします")
        return None
    if not xbrl_bytes:
        return None
    try:
        parsed = xbrl_parser.parse_summary(xbrl_bytes)
    except xbrl_parser.XbrlParseError as e:
        log(f"XBRL解析失敗 ({code}): {e} — Yahoo由来値にフォールバックします")
        return None
    op = parsed.get("operating_income")
    if op is None:
        log(f"XBRLに営業利益が無い ({code}) — Yahoo由来値にフォールバックします")
        return None
    return op


def financial_surprises(financials, mystocks, today, xbrl_lookup=None):
    """financials.json (Yahoo由来の構造化決算数値。PDF本文は使わない) から
    営業利益の黒字転換・最高益更新を検出する。

    当日 (今回のパイプライン実行) にデータ更新があった銘柄 (t == today) の
    最新年度のみを判定対象にする (古いデータで繰り返しアラートが出ないため)。

    xbrl_lookup が渡された場合、当日分の営業利益は決算短信サマリーXBRL
    (xbrl_parser.py) から取得できればそちらを優先する(決算当日の確定値を
    より確実にするため)。取得・解析に失敗した場合は Yahoo 由来の値へ
    フォールバックする。
    """
    alerts = []
    stocks_fin = (financials or {}).get("stocks") or {}
    for m in mystocks:
        code = m["code"]
        entry = stocks_fin.get(code)
        if not entry or entry.get("t") != today:
            continue
        annual = entry.get("a") or []
        if len(annual) < 2:
            continue
        # [期末日, 売上高, 営業利益, 純利益, EPS]
        latest, prev = annual[-1], annual[-2]
        latest_op = fnum(latest[2]) if len(latest) > 2 else None
        prev_op = fnum(prev[2]) if len(prev) > 2 else None
        xbrl_op = xbrl_confirmed_operating_income(code, xbrl_lookup)
        # basis: サプライズ一覧タブ(#/surprises)の根拠バッジ用。営業利益がXBRL確定値で
        # 上書きされたか、Yahoo由来financials.jsonのままかを記録する。
        basis = "財務"
        if xbrl_op is not None:
            latest_op = xbrl_op
            basis = "XBRL"
        if latest_op is None:
            continue
        imp = m.get("importance") or 3
        if prev_op is not None and latest_op > 0 and prev_op <= 0:
            alerts.append({
                "date": today, "code": code, "importance": imp,
                "type": "surprise_turnaround",
                "title": "営業利益が黒字転換",
                "detail": f"{latest[0]} 営業利益 {latest_op:,.0f}円 (前期 {prev_op:,.0f}円)",
                "basis": basis,
            })
        history = [fnum(row[2]) for row in annual[:-1] if len(row) > 2]
        history = [v for v in history if v is not None]
        if history and latest_op > max(history):
            alerts.append({
                "date": today, "code": code, "importance": imp,
                "type": "surprise_record_profit",
                "title": "営業利益が過去最高を更新",
                "detail": f"{latest[0]} 営業利益 {latest_op:,.0f}円",
                "basis": basis,
            })
    return alerts


def generate(prices, schedule, mystocks, settings, today, disclosures=None, reactions=None,
             financials=None, xbrl_lookup=None):
    """アラート判定の純粋関数。alerts のリストを返す (dedupe は呼び出し側)。

    prices:      {"date": "YYYY-MM-DD", "stocks": {code: [close, chg%, hi52, lo52, vol, avgVol, ...]}}
    schedule:    [{"code","announce_date","fiscal_type"}...]
    mystocks:    [{"code","importance"}...]
    disclosures: [{"code","doc_type","title","published_at"}...] (本日公表分のみ判定)
    reactions:   {"tail": {...}, "events": [...]} (track_reactions.py の出力)
    financials:  {"stocks": {code: {"a": [[期末日,売上高,営業利益,純利益,EPS]...], "t": "YYYY-MM-DD"}}}
                 (frontend/data/financials.json。構造化数値のみ使用しPDFはパースしない)
    xbrl_lookup: callable(code) -> bytes|None (省略時は None)。決算サプライズ判定の
                 営業利益を決算短信サマリーXBRLから取得できる場合に渡す。取得・解析に
                 失敗した場合は financials (Yahoo由来) の値へ自動フォールバックする。
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
    disc_by_code = {}
    for d in disclosures or []:
        if d.get("doc_type") in DISC_ALERT_TYPES and (d.get("published_at") or "")[:10] == today:
            disc_by_code.setdefault(str(d.get("code")), []).append(d)
    tail = (reactions or {}).get("tail") or {}
    ev_by_code = {}
    for e in (reactions or {}).get("events") or []:
        if e.get("t") in ("決算短信", "訂正決算短信"):
            ev_by_code.setdefault(str(e.get("code")), []).append(e)

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
        # 重要開示 (業績予想修正・配当予想修正・自己株式取得・訂正決算短信)
        if conf.get("disclosure"):
            for d in disc_by_code.get(code, []):
                specific = classify_surprise_type(d["doc_type"], d.get("title"))
                label = specific or d["doc_type"]
                alert = {
                    "date": today, "code": code, "importance": imp,
                    "type": "disclosure_" + label,
                    "title": f"サプライズ: {label}" if specific else f"開示: {label}",
                    "detail": (d.get("title") or "")[:70],
                }
                # basis: 上方/下方修正・増配/減配など方向性まで分類できた場合のみ、
                # サプライズ一覧タブ(#/surprises)向けに開示タイトル判定である旨を記録する。
                if specific:
                    alert["basis"] = "タイトル"
                alerts.append(alert)
            # 決算サプライズ (黒字転換・最高益) — XBRL/Yahoo由来の構造化数値のみで判定
            alerts.extend(financial_surprises(financials, [m], today, xbrl_lookup=xbrl_lookup))
        # 連続下落 / 連続上昇 (3日以上)
        if conf.get("streak"):
            n, direction = streak_of(tail, code)
            if direction:
                alerts.append({
                    "date": today, "code": code, "importance": imp,
                    "type": "streak_down" if direction < 0 else "streak_up",
                    "title": f"{n}日{'続落' if direction < 0 else '続伸'}",
                    "detail": "終値ベースの連続" + ("下落" if direction < 0 else "上昇"),
                })
        # 決算発表への株価反応 (反応日 / 翌営業日に大きく動いた)
        if conf.get("reaction"):
            rpct = float(conf.get("rpct") or 5)
            for e in ev_by_code.get(code, []):
                c1, c2 = fnum(e.get("c1")), fnum(e.get("c2"))
                if e.get("d") == today and c1 is not None and abs(c1) >= rpct:
                    alerts.append({
                        "date": today, "code": code, "importance": imp,
                        "type": "reaction",
                        "title": f"決算反応 {c1:+.1f}%",
                        "detail": f"{e.get('t')}への当日反応 (閾値±{rpct:g}%)",
                    })
                elif e.get("nd") == today and c2 is not None and abs(c2) >= rpct:
                    alerts.append({
                        "date": today, "code": code, "importance": imp,
                        "type": "reaction",
                        "title": f"決算翌日 {c2:+.1f}%",
                        "detail": f"{e.get('t')}の翌営業日反応 (閾値±{rpct:g}%)",
                    })
    return alerts


def alert_key(a):
    return f"{a['date']}|{a['code']}|{a['type']}"


def should_include_price_alerts(now, force):
    """株価系アラート(price_move/wk52/volume/disclosure/surprise/streak/reaction)を
    含めてよいかを判定する。場中(JST15時より前)の暫定値を避けるため、15時以降
    または --force のときのみ True。

    決算発表予定(前日・当日朝)の earnings 通知はこのゲートの対象外
    (呼び出し側が prices=None で generate() を呼ぶことで分離する)。
    """
    return now.hour >= 15 or bool(force)


def create_issue(new_alerts, names, today):
    """GitHub Issue を作成してオーナーにメンションする (通知メールが届く)。"""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")  # 例: kojit1229/stock_analyze
    if not token or not repo:
        log("GITHUB_TOKEN/GITHUB_REPOSITORY が無いため Issue 通知はスキップ")
        return False
    owner = repo.split("/")[0]
    icon = {"price_move": "📈", "wk52_high": "🚀", "wk52_low": "🔻",
            "volume": "📊", "earnings": "📅", "streak_down": "📉",
            "streak_up": "📈", "reaction": "🎯",
            "surprise_turnaround": "🌱", "surprise_record_profit": "🏆"}

    def icon_of(t):
        return "📢" if t.startswith("disclosure") else icon.get(t, "🔔")
    lines = [f"@{owner} マイ銘柄に {len(new_alerts)}件のアラートがあります。", "",
             "| 銘柄 | 重要度 | アラート | 詳細 |", "|---|---|---|---|"]
    for a in sorted(new_alerts, key=lambda x: (-x["importance"], x["code"])):
        name = names.get(a["code"], "")
        star = "★" * max(0, min(5, a["importance"]))
        lines.append(f"| {a['code']} {name} | {star} | {icon_of(a['type'])} "
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
    after_hours = should_include_price_alerts(now, args.force)

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

    if after_hours:
        prices = load_json(os.path.join(args.data, "prices.json"), None)
        if not prices or not (prices.get("stocks") or {}):
            log("prices.json が無いためスキップ")
            return
        disclosures = load_json(os.path.join(args.data, "disclosures.json"), [])
        reactions = load_json(os.path.join(args.data, "reactions.json"), None)
        financials = load_json(os.path.join(args.data, "financials.json"), None)
        # xbrl_lookup: 当日の決算短信/訂正決算短信(disclosures.jsonのxbrl_url、
        # W2-1b)からサマリーXBRL zipを取得・キャッシュするlookupを配線する
        # (W2-1c: xbrl_fetch.py)。lookup構築自体が失敗しても株価系・開示系の
        # アラート生成は継続する(Yahoo由来値のみでの判定に自動的にフォールバック
        # するため。xbrl_confirmed_operating_income が取得・解析失敗を個別にログする)。
        try:
            xbrl_lookup = xbrl_fetch.make_lookup(
                disclosures, today, cache_dir=os.path.join(args.data, "xbrl_cache"))
        except Exception as e:  # noqa: BLE001 — lookup構築自体の失敗は握りつぶさずログにする
            log(f"XBRL lookupの構築に失敗 ({type(e).__name__}: {e}) — Yahoo由来値のみで判定します")
            xbrl_lookup = None
        generated = generate(prices, schedule, mystocks, settings, today,
                             disclosures=disclosures, reactions=reactions, financials=financials,
                             xbrl_lookup=xbrl_lookup)
    else:
        log(f"JST {now.hour}時 (15時前) のため株価系アラートはスキップし、"
            "決算発表予定(前日・当日朝)の通知のみ生成します (場中の暫定値を避ける)")
        # prices/disclosures/reactions/financials を渡さない (None) ことで、
        # generate() 内の株価系・開示系判定は自然にスキップされ、schedule 由来の
        # earnings 判定のみが実行される。
        generated = generate(None, schedule, mystocks, settings, today)

    out_path = os.path.join(args.data, "alerts.json")
    existing = load_json(out_path, {})
    old_alerts = existing.get("alerts") or []
    known = {alert_key(a) for a in old_alerts}

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
