#!/usr/bin/env python3
"""実データ取得スクリプト (GitHub Actions で定期実行)。

公式・公開データソースから以下を取得し、frontend/data/*.json に書き出す。
GitHub Pages 上の静的アプリ (frontend/local-api.js) がこの JSON を読み込む。

- 銘柄マスタ:  JPX「東証上場銘柄一覧」data_j.xls
               (コード / 銘柄名 / 市場区分 / 33業種)
- 決算発表予定: JPX「決算発表予定日」ページからリンクされる Excel
- 決算短信:    TDnet (やのしんWebAPI 経由) の適時開示から
               決算短信・訂正短信・業績予想修正・決算説明資料を抽出
- 時価総額:    Yahoo Finance のバッチ quote API (ベストエフォート。
               取得できない銘柄は null のままにする)
               ※JPXの公開Excelは市場合計のみで銘柄別時価総額が無いため

方針:
- 各ソースは独立に失敗しうる。新データが空の場合は既存ファイルを
  上書きしない(前回の正常データを温存する)。
- 銘柄マスタの取得失敗のみ致命的エラーとして非ゼロ終了する。
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse

import requests

JPX_BASE = "https://www.jpx.co.jp"
DATA_J_URL = JPX_BASE + "/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
SCHEDULE_INDEX_URL = JPX_BASE + "/listing/event-schedules/financial-announcement/index.html"
YANOSHIN_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/{start}-{end}.json"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_TS_URL = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; kessan-navi-updater/1.0)"}
TIMEOUT = 90


def log(msg):
    print(f"[fetch_real_data] {msg}", flush=True)


def jst_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)


def http_get(url, timeout=TIMEOUT, **kw):
    r = requests.get(url, headers=HEADERS, timeout=timeout, **kw)
    r.raise_for_status()
    return r


def normalize_code(raw):
    """銘柄コードを4桁表記に正規化する (TDnet は5桁 '72030' 形式)。"""
    s = str(raw).strip()
    if re.fullmatch(r"[0-9A-Z]{5}", s) and s.endswith("0"):
        return s[:4]
    if re.fullmatch(r"[0-9A-Z]{4}", s):
        return s
    return None


def zen_to_han(s):
    """全角数字を半角へ (第１四半期 → 第1四半期)。"""
    return s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def direct_pdf_url(url):
    """TDnetミラーのリダイレクタ (rd.php?<URL>) を剥がして直接URLにする。"""
    m = re.search(r"rd\.php\?(https?://.+)$", url or "")
    return m.group(1) if m else (url or "")


# ---------------------------------------------------------------------------
# 銘柄マスタ (JPX data_j.xls)
# ---------------------------------------------------------------------------
def fetch_stock_master():
    import pandas as pd

    log(f"銘柄マスタ取得: {DATA_J_URL}")
    df = pd.read_excel(io.BytesIO(http_get(DATA_J_URL).content), dtype=str)
    cols = {c: str(c) for c in df.columns}

    def find_col(*keywords, exclude=()):
        for c, name in cols.items():
            if any(k in name for k in keywords) and not any(e in name for e in exclude):
                return c
        return None

    code_col = find_col("コード", exclude=("業種", "規模"))
    name_col = find_col("銘柄名", "会社名")
    market_col = find_col("市場")
    sector_col = find_col("33業種区分")
    if code_col is None or name_col is None or market_col is None:
        raise RuntimeError(f"data_j.xls の列を特定できません: {list(cols.values())}")

    stocks = {}
    for _, row in df.iterrows():
        code = normalize_code(row.get(code_col, ""))
        if not code:
            continue
        market_raw = str(row.get(market_col, "") or "")
        market = next((m for m in ("プライム", "スタンダード", "グロース") if m in market_raw), None)
        if not market:
            continue  # ETF・REIT 等の内国株式以外は対象外
        sector = str(row.get(sector_col, "") or "").strip() if sector_col else ""
        stocks[code] = {
            "code": code,
            "name": str(row.get(name_col, "") or "").strip(),
            "market": market,
            "sector": sector if sector and sector != "-" else None,
            "market_cap": None,
        }
    log(f"銘柄マスタ: {len(stocks)}件 (プライム/スタンダード/グロース)")
    if len(stocks) < 1000:
        raise RuntimeError(f"銘柄マスタが少なすぎます: {len(stocks)}件")
    return stocks


# ---------------------------------------------------------------------------
# 決算発表予定 (JPX)
# ---------------------------------------------------------------------------
def _excel_links(page_url):
    html = http_get(page_url).content.decode("utf-8", "replace")
    links = re.findall(r'href="([^"]+?\.xlsx?)"', html, flags=re.IGNORECASE)
    seen, out = set(), []
    for l in links:
        full = urllib.parse.urljoin(page_url, l)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def _parse_schedule_excel(content):
    """JPX 決算発表予定 Excel から (code, date, fiscal_type) を抽出する。

    列見出しの位置が固定でない可能性があるため、見出し行を探索してから
    キーワードで列を対応付ける。
    """
    import pandas as pd

    rows = []
    xls = pd.ExcelFile(io.BytesIO(content))
    for sheet in xls.sheet_names:
        raw = xls.parse(sheet, header=None, dtype=object)
        header_idx = None
        for i in range(min(len(raw), 15)):
            vals = [str(v) for v in raw.iloc[i].tolist()]
            if any("コード" in v for v in vals) and any("発表" in v for v in vals):
                header_idx = i
                break
        if header_idx is None:
            continue
        headers = [str(v) for v in raw.iloc[header_idx].tolist()]

        def col(*kws, exclude=()):
            for j, h in enumerate(headers):
                if any(k in h for k in kws) and not any(e in h for e in exclude):
                    return j
            return None

        c_code = col("コード", exclude=("業種",))
        c_date = col("発表")
        c_fiscal = col("四半期", "種別", "期別")
        c_period = col("決算期", "期末")
        if c_code is None or c_date is None:
            continue

        for i in range(header_idx + 1, len(raw)):
            r = raw.iloc[i].tolist()
            code = normalize_code(r[c_code] if c_code < len(r) else "")
            if not code:
                continue
            rawdate = r[c_date] if c_date < len(r) else None
            date = None
            if hasattr(rawdate, "strftime"):
                date = rawdate.strftime("%Y-%m-%d")
            else:
                s = str(rawdate or "").strip()
                m = re.search(r"(\d{4})[/\-年](\d{1,2})[/\-月](\d{1,2})", s)
                if m:
                    date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if not date:
                continue
            fiscal = ""
            if c_fiscal is not None and c_fiscal < len(r) and r[c_fiscal] is not None:
                fiscal = zen_to_han(str(r[c_fiscal]).strip())
            if not fiscal and c_period is not None and c_period < len(r) and r[c_period] is not None:
                fiscal = zen_to_han(str(r[c_period]).strip())
            if fiscal in ("nan", "None", "-"):
                fiscal = ""
            # 通期・本決算表記のゆれを吸収
            if fiscal and ("通期" in fiscal or "本決算" in fiscal):
                fiscal = "本決算"
            rows.append({"code": code, "date": date, "fiscal_type": fiscal or None})
    return rows


def fetch_schedule():
    log(f"決算発表予定ページ取得: {SCHEDULE_INDEX_URL}")
    links = _excel_links(SCHEDULE_INDEX_URL)
    log(f"Excel リンク {len(links)} 件検出")
    merged = {}
    for url in links[:8]:
        try:
            content = http_get(url).content
            rows = _parse_schedule_excel(content)
            log(f"  {url.rsplit('/', 1)[-1]}: {len(rows)}行")
            for row in rows:
                merged[(row["code"], row["date"])] = row
        except Exception as e:  # noqa: BLE001
            log(f"  {url}: 解析失敗 ({e})")
    result = sorted(merged.values(), key=lambda x: (x["date"], x["code"]))
    log(f"決算発表予定: {len(result)}件")
    return result


# ---------------------------------------------------------------------------
# 決算短信 (TDnet / やのしんWebAPI)
# ---------------------------------------------------------------------------
DOC_TYPE_RULES = [
    (lambda t: "訂正" in t and "短信" in t, "訂正決算短信"),
    (lambda t: "決算短信" in t, "決算短信"),
    (lambda t: "業績予想" in t, "業績予想修正"),
    (lambda t: "配当予想" in t or ("配当" in t and "修正" in t), "配当予想修正"),
    (lambda t: ("自己株式" in t or "自社株" in t) and ("取得" in t or "買付" in t), "自己株式取得"),
    (lambda t: "決算説明" in t, "決算説明資料"),
]


def classify_doc(title):
    for cond, label in DOC_TYPE_RULES:
        if cond(title):
            return label
    return None


def fetch_tdnet_day(ds, per_day_limit=3000):
    """1日分の TDnet 開示を取得し、決算関連のみを dict のリストで返す。

    取得に失敗した場合は None を返す (呼び出し側で再試行を判断)。
    """
    url = YANOSHIN_URL.format(start=ds, end=ds)
    items = None
    for attempt in (1, 2):
        try:
            items = http_get(url, params={"limit": per_day_limit}, timeout=45).json().get("items", [])
            break
        except Exception as e:  # noqa: BLE001
            log(f"  {ds}: 取得失敗 (試行{attempt}) {type(e).__name__}: {e}")
            time.sleep(3)
    if items is None:
        return None
    out = []
    for it in items:
        t = it.get("Tdnet") or it  # 念のため両対応
        title = str(t.get("title", "") or "")
        doc_type = classify_doc(title)
        if not doc_type:
            continue
        code = normalize_code(t.get("company_code", ""))
        if not code:
            continue
        pub = str(t.get("pubdate", "") or "").strip()
        published_at = pub.replace(" ", "T") if pub else None
        pdf_url = t.get("document_url") or ""
        # url_xbrl: 決算短信サマリーXBRL(zip)の直接URL。やのしんWebAPIは決算短信
        # 系の開示のみこのフィールドを持つ(業績予想修正等では null)。document_url
        # と同じ rd.php リダイレクタ形式のため direct_pdf_url() で剥がす
        # (generate_alerts.py の xbrl_lookup がこのURLからzipを取得する)。
        xbrl_url = direct_pdf_url(t.get("url_xbrl") or "")
        tid = str(t.get("id", "") or "")
        if not tid:
            tid = hashlib.md5(f"{code}|{published_at}|{title}".encode()).hexdigest()[:12]
        out.append({
            "key": tid,
            "code": code,
            "title": title,
            "pdf_url": pdf_url,
            "xbrl_url": xbrl_url,
            "doc_type": doc_type,
            "published_at": published_at,
        })
    return out


def fetch_disclosures(days=35, per_day_limit=3000):
    """TDnet 開示を日別に取得する。

    期間一括だとレスポンスが大きくなり途中で切断されることがあるため、
    1日ずつ取得して失敗した日はスキップする。
    """
    end = jst_now().date()
    out, seen = [], set()
    ok_days = fail_days = 0
    for i in range(days):
        d = end - datetime.timedelta(days=i)
        if d.weekday() >= 5:  # 土日は開示なし
            continue
        ds = d.strftime("%Y%m%d")
        items = fetch_tdnet_day(ds, per_day_limit)
        if items is None:
            fail_days += 1
            continue
        ok_days += 1
        day_count = 0
        for item in items:
            if item["key"] in seen:
                continue
            seen.add(item["key"])
            day_count += 1
            out.append(item)
        if day_count:
            log(f"  {ds}: 決算関連 {day_count}件")
        time.sleep(0.5)
    out.sort(key=lambda x: x["published_at"] or "", reverse=True)
    log(f"決算関連開示: {len(out)}件 (成功{ok_days}日 / 失敗{fail_days}日)")
    return out


# ---------------------------------------------------------------------------
# 開示履歴アーカイブ (過去2年分。銘柄コード先頭1文字ごとのシャードJSON)
#
# やのしんAPIはブラウザから直接利用できない (CORS非対応・応答が遅く公開
# プロキシのタイムアウトも超過する) ことが判明したため、過去分の決算短信
# 履歴はここで日次バックフィルにより蓄積し、静的JSONとしてアプリに配る。
# ---------------------------------------------------------------------------
HISTORY_KEEP_DAYS = 750  # 約2年 + 余裕
HISTORY_URL_KEEP_DAYS = 45  # これより古いPDFはTDnetから削除済みのためURLを持たない


def _hist_dir(out_dir):
    d = os.path.join(out_dir, "history")
    os.makedirs(d, exist_ok=True)
    return d


def load_history_state(out_dir):
    try:
        with open(os.path.join(_hist_dir(out_dir), "state.json"), encoding="utf-8") as f:
            s = json.load(f)
            if isinstance(s, dict):
                return s
    except (OSError, ValueError):
        pass
    return {"oldest": None, "complete": False}


def save_history_state(out_dir, state):
    write_json(os.path.join(_hist_dir(out_dir), "state.json"), state)


def merge_into_history(out_dir, items, valid_codes=None):
    """開示アイテム群をシャードにマージする。

    シャード構造: {"codes": {code: [[published_at, doc_type, title, pdf_url], ...]}}
    (published_at 降順)。古いアイテムの pdf_url は削除して容量を抑える。
    """
    hist_dir = _hist_dir(out_dir)
    now = jst_now()
    url_cutoff = (now - datetime.timedelta(days=HISTORY_URL_KEEP_DAYS)).strftime("%Y-%m-%dT00:00:00")
    keep_cutoff = (now - datetime.timedelta(days=HISTORY_KEEP_DAYS)).strftime("%Y-%m-%dT00:00:00")

    by_prefix = {}
    for it in items:
        code = it["code"]
        if valid_codes is not None and code not in valid_codes:
            continue
        if not it.get("published_at") or it["published_at"] < keep_cutoff:
            continue
        by_prefix.setdefault(code[0], []).append(it)

    total_added = 0
    for prefix, plist in sorted(by_prefix.items()):
        path = os.path.join(hist_dir, f"{prefix}.json")
        shard = {"codes": {}}
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("codes"), dict):
                    shard = loaded
        except (OSError, ValueError):
            pass
        changed = False
        for it in plist:
            rows = shard["codes"].setdefault(it["code"], [])
            ident = (it["published_at"][:16], it["title"][:40])
            if any((r[0][:16], r[2][:40]) == ident for r in rows):
                continue
            url = it.get("pdf_url") or ""
            if it["published_at"] < url_cutoff:
                url = ""
            rows.append([it["published_at"], it["doc_type"], it["title"], url])
            changed = True
            total_added += 1
        if changed:
            # 整理: 期限切れの削除・URLの剥落・降順ソート
            # (短い行が混入していても落ちないよう4要素に正規化する)
            for code, rows in shard["codes"].items():
                rows[:] = [
                    [q[0], q[1], q[2], (q[3] if q[0] >= url_cutoff else "")]
                    for q in ((list(r) + ["", "", "", ""])[:4] for r in rows)
                    if q[0] >= keep_cutoff
                ]
                rows.sort(key=lambda r: r[0], reverse=True)
            shard["codes"] = {c: r for c, r in shard["codes"].items() if r}
            write_json(path, shard)
    if total_added:
        log(f"開示アーカイブ: {total_added}件追加")
    return total_added


def backfill_history(out_dir, valid_codes, max_days=15):
    """アーカイブの過去方向バックフィル。

    state.json の oldest から過去へ max_days 営業日分を取得してマージする。
    取得に失敗した日は state["missed"] に記録して先へ進み (中断しない)、
    次回以降の実行の冒頭で最大3回まで再試行する。
    2年分に到達し、失敗日の再試行も尽きたら complete を立てて以後は何もしない。
    """
    state = load_history_state(out_dir)
    missed = state.get("missed") or {}
    today = jst_now().date()
    cutoff = today - datetime.timedelta(days=HISTORY_KEEP_DAYS)
    reached = bool(state.get("oldest")) and datetime.date.fromisoformat(state["oldest"]) <= cutoff
    retry_targets = sorted(d for d, n in missed.items() if n < 3)[:6]
    if state.get("complete") or (reached and not retry_targets):
        state["complete"] = True
        save_history_state(out_dir, state)
        log("バックフィル: 完了済み (2年分取得済み)")
        return state

    items_all = []
    processed = 0

    # 1) 過去の失敗日をまず再試行する
    for ds in retry_targets:
        items = fetch_tdnet_day(ds.replace("-", ""))
        if items is None:
            missed[ds] = missed.get(ds, 0) + 1
            log(f"  バックフィル再試行 {ds}: 失敗 ({missed[ds]}回目)")
        else:
            kept = [i for i in items if valid_codes is None or i["code"] in valid_codes]
            items_all.extend(kept)
            missed.pop(ds, None)
            log(f"  バックフィル再試行 {ds}: 成功 (決算関連 {len(kept)}件)")
        time.sleep(0.5)

    # 2) 過去方向へ進む (失敗しても記録して続行する)
    cur = datetime.date.fromisoformat(state["oldest"]) if state.get("oldest") else today
    day = cur - datetime.timedelta(days=1)
    while processed < max_days and day >= cutoff:
        if day.weekday() < 5:
            items = fetch_tdnet_day(day.strftime("%Y%m%d"))
            if items is None:
                missed[day.isoformat()] = missed.get(day.isoformat(), 0) + 1
                log(f"  バックフィル {day}: 失敗 (記録して続行)")
            else:
                kept = [i for i in items if valid_codes is None or i["code"] in valid_codes]
                if kept:
                    log(f"  バックフィル {day}: 決算関連 {len(kept)}件")
                items_all.extend(kept)
            processed += 1
            time.sleep(0.5)
        cur = day
        day = cur - datetime.timedelta(days=1)

    if items_all:
        merge_into_history(out_dir, items_all, valid_codes)
    state["oldest"] = cur.isoformat()
    # 保持期間より古い失敗記録は捨てる
    keep_cut = cutoff.isoformat()
    state["missed"] = {d: n for d, n in missed.items() if d >= keep_cut}
    still_retryable = any(n < 3 for n in state["missed"].values())
    state["complete"] = (cur <= cutoff) and not still_retryable
    save_history_state(out_dir, state)
    log(f"バックフィル: {processed}営業日分処理 / 範囲 {state['oldest']}〜 / 失敗記録{len(state['missed'])}日 (complete={state['complete']})")
    return state


# ---------------------------------------------------------------------------
# Yahoo Finance (時価総額・財務数値。ベストエフォート)
# ---------------------------------------------------------------------------
def yahoo_session():
    """Yahoo Finance 用のセッションと crumb を用意する (yfinance と同方式)。

    失敗した場合は (None, None) を返す。
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    })
    try:
        s.get("https://fc.yahoo.com", timeout=30)
        crumb = s.get(YAHOO_CRUMB_URL, timeout=30).text.strip()
    except Exception as e:  # noqa: BLE001
        log(f"Yahoo crumb 取得失敗 ({type(e).__name__}: {e})")
        return None, None
    if not crumb or "<" in crumb or len(crumb) > 32:
        log(f"Yahoo crumb が無効 ({crumb[:40]!r})")
        return None, None
    return s, crumb


def fetch_market_caps(codes, s, crumb):
    """Yahoo Finance のバッチ quote API から時価総額(円)と株価情報を取得する。

    /v7/finance/quote を100銘柄ずつ呼ぶ。失敗した銘柄・バッチはスキップし、
    取得できた分だけ返す。

    戻り値: (caps, prices)
      caps:   {code: 時価総額(円)}
      prices: {code: [終値, 前日比%, 52週高値, 52週安値, 出来高, 平均出来高(3ヶ月),
                      配当利回り%, 年間配当(円)]}
    """
    caps = {}
    prices = {}
    symbols = [c + ".T" for c in codes]
    batches = fails = 0
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        batches += 1
        try:
            r = s.get(YAHOO_QUOTE_URL, timeout=30, params={
                "symbols": ",".join(chunk),
                "fields": "marketCap,regularMarketPrice,regularMarketChangePercent,"
                          "fiftyTwoWeekHigh,fiftyTwoWeekLow,"
                          "regularMarketVolume,averageDailyVolume3Month,"
                          "trailingAnnualDividendYield,trailingAnnualDividendRate",
                "crumb": crumb,
            })
            r.raise_for_status()
            for q in (r.json().get("quoteResponse", {}).get("result") or []):
                sym = str(q.get("symbol", ""))
                if not sym.endswith(".T"):
                    continue
                code = sym[:-2]
                cap = q.get("marketCap")
                if cap:
                    caps[code] = int(cap)
                price = q.get("regularMarketPrice")
                if price is not None:
                    dy = q.get("trailingAnnualDividendYield")
                    prices[code] = [
                        price,
                        q.get("regularMarketChangePercent"),
                        q.get("fiftyTwoWeekHigh"),
                        q.get("fiftyTwoWeekLow"),
                        q.get("regularMarketVolume"),
                        q.get("averageDailyVolume3Month"),
                        round(dy * 100, 3) if isinstance(dy, (int, float)) else None,
                        q.get("trailingAnnualDividendRate"),
                    ]
        except Exception as e:  # noqa: BLE001
            fails += 1
            if fails <= 3:
                log(f"  Yahoo quote バッチ{batches}失敗: {type(e).__name__}: {e}")
        time.sleep(0.4)
    log(f"時価総額: Yahoo Finance から {len(caps)}件 / 株価 {len(prices)}件 ({batches}バッチ中 失敗{fails})")
    return caps, prices


# ---------------------------------------------------------------------------
# 財務数値の推移 (Yahoo fundamentals-timeseries、巡回取得)
# ---------------------------------------------------------------------------
TS_TYPES = [
    # PL (年次・四半期)
    "annualTotalRevenue", "annualOperatingIncome", "annualNetIncome", "annualDilutedEPS",
    "quarterlyTotalRevenue", "quarterlyOperatingIncome", "quarterlyNetIncome", "quarterlyDilutedEPS",
    # BS (年次のみ) — ネットキャッシュ比率(清原式)とBS構成分析に使用
    "annualCurrentAssets", "annualTotalAssets",
    "annualCurrentLiabilities", "annualTotalLiabilitiesNetMinorityInterest",
    "annualStockholdersEquity", "annualInvestmentsAndAdvances",
    # CF (年次のみ)
    "annualOperatingCashFlow", "annualInvestingCashFlow", "annualFinancingCashFlow",
]

# financials.json の系列定義: キー → [期末日, 各指標...] の指標名リスト
FIN_SERIES = {
    "a": ["TotalRevenue", "OperatingIncome", "NetIncome", "DilutedEPS"],
    "b": ["CurrentAssets", "TotalAssets", "CurrentLiabilities",
          "TotalLiabilitiesNetMinorityInterest", "StockholdersEquity", "InvestmentsAndAdvances"],
    "c": ["OperatingCashFlow", "InvestingCashFlow", "FinancingCashFlow"],
}


def parse_timeseries(payload):
    """Yahoo fundamentals-timeseries 応答を系列dictに変換する。

    返り値: {"a": PL年次, "q": PL四半期, "b": BS年次, "c": CF年次}
    各要素は [期末日, 指標...] (FIN_SERIES 定義順。取得できない値は None)。
    """
    annual, quarterly = {}, {}
    for block in (payload.get("timeseries", {}).get("result") or []):
        types = (block.get("meta") or {}).get("type") or []
        if not types:
            continue
        tname = types[0]
        for row in (block.get(tname) or []):
            if not row:
                continue
            d = row.get("asOfDate")
            v = (row.get("reportedValue") or {}).get("raw")
            if d is None or v is None:
                continue
            bucket = annual if tname.startswith("annual") else quarterly
            metric = tname.replace("annual", "").replace("quarterly", "")
            bucket.setdefault(d, {})[metric] = v

    def pack(bucket, metrics):
        out = []
        for d in sorted(bucket):
            m = bucket[d]
            vals = [m.get(k) for k in metrics]
            if all(v is None for v in vals):
                continue  # この系列の値が1つもない期は持たない
            out.append([d] + vals)
        return out[-12:]  # 直近12期分まで保持

    return {
        "a": pack(annual, FIN_SERIES["a"]),
        "q": pack(quarterly, FIN_SERIES["a"]),
        "b": pack(annual, FIN_SERIES["b"]),
        "c": pack(annual, FIN_SERIES["c"]),
    }


def fetch_financials(codes, existing, s, crumb, per_run=400):
    """財務数値の推移を巡回取得する。

    Yahoo の fundamentals-timeseries は銘柄ごとに1リクエスト必要なため、
    1回の実行では per_run 銘柄だけ更新し、実行ごとにローテーションする
    (データ無し → 最終取得が古い順)。結果は existing にマージされる。
    """
    stocks_data = existing.setdefault("stocks", {})
    today = jst_now().strftime("%Y-%m-%d")
    # 優先順: BS/CF系列(b)が無い旧スキーマの銘柄 → 最終取得が古い順 → コード順
    order = sorted(codes, key=lambda c: (
        0 if "b" not in stocks_data.get(c, {}) else 1,
        stocks_data.get(c, {}).get("t", ""),
        c,
    ))
    targets = order[:per_run]
    now_ts = int(time.time())
    ok = fails = empty = 0
    for code in targets:
        sym = code + ".T"
        try:
            r = s.get(YAHOO_TS_URL.format(sym=sym), timeout=30, params={
                "symbol": sym,
                "type": ",".join(TS_TYPES),
                "period1": "1420070400",  # 2015-01-01
                "period2": str(now_ts),
                "merge": "false",
                "crumb": crumb,
            })
            r.raise_for_status()
            entry = parse_timeseries(r.json())
            entry["t"] = today
            if entry["a"] or entry["q"] or entry["b"] or entry["c"]:
                stocks_data[code] = entry
                ok += 1
            else:
                # データが無い銘柄も t と空系列を記録し、毎回問い合わせないようにする
                old = stocks_data.get(code, {})
                stocks_data[code] = {
                    "a": old.get("a", []), "q": old.get("q", []),
                    "b": old.get("b", []), "c": old.get("c", []),
                    "t": today,
                }
                empty += 1
        except Exception as e:  # noqa: BLE001
            fails += 1
            if fails <= 3:
                log(f"  財務 {sym} 失敗: {type(e).__name__}: {e}")
        time.sleep(0.35)
    log(f"財務数値: 更新{ok}件 / データ無し{empty}件 / 失敗{fails}件 (対象{len(targets)}銘柄)")
    return existing


# ---------------------------------------------------------------------------
# 決算短信PDFの恒久保存 (config/pdf_watchlist.json の銘柄のみ)
#
# TDnetの掲載期間は約1ヶ月のため、保存リストの銘柄については掲載期間内に
# PDF本体をダウンロードしてリポジトリ (frontend/pdfs/) にコミットする。
# 5年後でも参照できる恒久アーカイブとなる。全銘柄の保存は容量制限
# (GitHub/Pages ~1GB) を超えるため、リスト方式とする。
# ---------------------------------------------------------------------------
PDF_DOC_TYPES = ("決算短信", "訂正決算短信")
PDF_MAX_BYTES = 15 * 1024 * 1024  # 異常に大きいファイルは保存しない


def load_pdf_watchlist(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        codes = data.get("codes") or []
        return {str(c).strip() for c in codes if str(c).strip()}
    except (OSError, ValueError) as e:
        log(f"PDF保存リストが読めません ({path}): {e}")
        return set()


def archive_watchlist_pdfs(out_dir, pdf_dir, watchlist_path):
    """保存リスト銘柄の決算短信PDFをダウンロードして蓄積する。"""
    watchlist = load_pdf_watchlist(watchlist_path)
    if not watchlist:
        log("PDF保存リストが空のためスキップ")
        return 0
    try:
        with open(os.path.join(out_dir, "disclosures.json"), encoding="utf-8") as f:
            disclosures = json.load(f)
    except (OSError, ValueError):
        log("disclosures.json が読めないためPDF保存をスキップ")
        return 0

    os.makedirs(pdf_dir, exist_ok=True)
    index_path = os.path.join(pdf_dir, "index.json")
    index = {"codes": {}}
    try:
        with open(index_path, encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("codes"), dict):
                index = loaded
    except (OSError, ValueError):
        pass

    saved = skipped = failed = 0
    for d in disclosures:
        code = d.get("code")
        if code not in watchlist:
            continue
        if d.get("doc_type") not in PDF_DOC_TYPES:
            continue
        url = direct_pdf_url(d.get("pdf_url") or "")
        pub = d.get("published_at") or ""
        if not url or not pub:
            continue
        fname = f"{pub[:10]}_{d.get('key', 'x')}.pdf"
        rel = f"{code}/{fname}"
        rows = index["codes"].setdefault(code, [])
        if any(r[3] == rel for r in rows):
            continue  # 保存済み
        target_dir = os.path.join(pdf_dir, code)
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, fname)
        try:
            r = http_get(url, timeout=60)
            content = r.content
            if not content.startswith(b"%PDF"):
                log(f"  PDF保存 {code} {pub[:10]}: PDFではない応答のためスキップ")
                skipped += 1
                continue
            if len(content) > PDF_MAX_BYTES:
                log(f"  PDF保存 {code} {pub[:10]}: サイズ超過 ({len(content):,}B) スキップ")
                skipped += 1
                continue
            with open(target, "wb") as f:
                f.write(content)
            rows.append([pub, d.get("doc_type"), d.get("title", ""), rel])
            rows.sort(key=lambda x: x[0], reverse=True)
            saved += 1
            log(f"  PDF保存 {code}: {fname} ({len(content):,}B)")
        except Exception as e:  # noqa: BLE001
            failed += 1
            log(f"  PDF保存 {code} {pub[:10]}: 失敗 ({type(e).__name__}: {e})")
        time.sleep(0.5)

    if saved:
        write_json(index_path, index)
    log(f"PDF恒久保存: 新規{saved}件 / スキップ{skipped} / 失敗{failed} (対象{len(watchlist)}銘柄)")
    return saved


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"書き出し: {path} ({os.path.getsize(path):,} bytes)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="frontend/data")
    parser.add_argument("--days", type=int, default=7,
                        help="開示の取得日数 (既存データとマージするため通常は直近数日で足りる)")
    parser.add_argument("--keep-days", type=int, default=45, help="開示の保持日数")
    parser.add_argument("--fin-per-run", type=int, default=400,
                        help="1回の実行で財務数値を更新する銘柄数 (巡回)")
    parser.add_argument("--backfill-days", type=int, default=15,
                        help="開示アーカイブを過去方向へ何営業日分バックフィルするか")
    parser.add_argument("--backfill-only", action="store_true",
                        help="開示アーカイブのバックフィルのみ実行する (軽量モード)")
    parser.add_argument("--pdf-dir", default="frontend/pdfs",
                        help="決算短信PDFの恒久保存先")
    parser.add_argument("--watchlist", default="config/pdf_watchlist.json",
                        help="PDF恒久保存の対象銘柄リスト")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    generated_at = jst_now().replace(microsecond=0).isoformat()

    # バックフィル専用モード: 既存の銘柄マスタを使い、アーカイブと財務数値だけ進める
    if args.backfill_only:
        valid_codes = None
        try:
            with open(os.path.join(args.out, "stocks.json"), encoding="utf-8") as f:
                valid_codes = {s["code"] for s in json.load(f)}
        except (OSError, ValueError):
            log("stocks.json が読めないためコードフィルタなしでバックフィルします")
        backfill_history(args.out, valid_codes, max_days=args.backfill_days)

        # 財務数値の巡回も進める (毎時実行に載せて全銘柄カバーを早める)
        if args.fin_per_run > 0 and valid_codes:
            fin_path = os.path.join(args.out, "financials.json")
            financials = {"stocks": {}}
            try:
                with open(fin_path, encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict) and isinstance(loaded.get("stocks"), dict):
                        financials = loaded
            except (OSError, ValueError):
                pass
            ysession, ycrumb = yahoo_session()
            if ysession:
                try:
                    fetch_financials(sorted(valid_codes), financials, ysession, ycrumb,
                                     per_run=args.fin_per_run)
                    financials["stocks"] = {c: v for c, v in financials["stocks"].items()
                                            if c in valid_codes}
                    financials["updated_at"] = generated_at
                    write_json(fin_path, financials)
                except Exception as e:  # noqa: BLE001
                    log(f"財務数値の取得でエラー: {e}")
        return

    # 1) 銘柄マスタ (失敗したら致命的)
    stocks = fetch_stock_master()

    # 2) 時価総額 + 財務数値 (Yahoo Finance、ベストエフォート)
    ysession, ycrumb = yahoo_session()
    caps = {}
    prices = {}
    if ysession:
        try:
            caps, prices = fetch_market_caps(sorted(stocks.keys()), ysession, ycrumb)
        except Exception as e:  # noqa: BLE001
            log(f"時価総額の取得でエラー: {e}")
    for code, cap in caps.items():
        if code in stocks:
            stocks[code]["market_cap"] = cap
    if prices:
        write_json(os.path.join(args.out, "prices.json"), {
            "date": jst_now().strftime("%Y-%m-%d"),
            "updated_at": generated_at,
            "stocks": {c: v for c, v in prices.items() if c in stocks},
        })

    fin_path = os.path.join(args.out, "financials.json")
    financials = {"stocks": {}}
    try:
        with open(fin_path, encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("stocks"), dict):
                financials = loaded
    except (OSError, ValueError):
        pass
    if ysession:
        try:
            fetch_financials(sorted(stocks.keys()), financials, ysession, ycrumb,
                             per_run=args.fin_per_run)
        except Exception as e:  # noqa: BLE001
            log(f"財務数値の取得でエラー: {e}")
    # 上場廃止銘柄の掃除
    financials["stocks"] = {c: v for c, v in financials["stocks"].items() if c in stocks}
    financials["updated_at"] = generated_at

    # 3) 決算発表予定
    schedule = []
    try:
        schedule = [r for r in fetch_schedule() if r["code"] in stocks]
    except Exception as e:  # noqa: BLE001
        log(f"決算発表予定の取得でエラー: {e}")

    # 4) 決算短信ほか開示 (直近 days 日分を取得し、既存データとマージする。
    #    取得に失敗した日があっても、過去の実行で取得済みのデータは残る)
    fetched_discs = []
    try:
        fetched_discs = [d for d in fetch_disclosures(days=args.days) if d["code"] in stocks]
    except Exception as e:  # noqa: BLE001
        log(f"開示の取得でエラー: {e}")

    existing_discs = []
    disc_path = os.path.join(args.out, "disclosures.json")
    try:
        with open(disc_path, encoding="utf-8") as f:
            existing_discs = json.load(f)
    except (OSError, ValueError):
        pass
    merged = {d["key"]: d for d in existing_discs if d.get("key")}
    for d in fetched_discs:
        merged[d["key"]] = d
    cutoff = (jst_now() - datetime.timedelta(days=args.keep_days)).strftime("%Y-%m-%dT00:00:00")
    disclosures = [d for d in merged.values() if (d.get("published_at") or "") >= cutoff]
    disclosures.sort(key=lambda x: x["published_at"] or "", reverse=True)
    log(f"開示マージ: 新規{len(fetched_discs)}件 + 既存{len(existing_discs)}件 → {len(disclosures)}件")

    # 開示アーカイブ: 直近取得分をマージし、過去方向へバックフィル
    try:
        merge_into_history(args.out, fetched_discs, set(stocks.keys()))
        if args.backfill_days > 0:
            backfill_history(args.out, set(stocks.keys()), max_days=args.backfill_days)
    except Exception as e:  # noqa: BLE001
        log(f"開示アーカイブの更新でエラー: {e}")


    # 書き出し (空データでの上書きはしない)
    write_json(os.path.join(args.out, "stocks.json"), sorted(stocks.values(), key=lambda s: s["code"]))
    if schedule:
        write_json(os.path.join(args.out, "schedule.json"), schedule)
    else:
        log("決算発表予定が空のため schedule.json は更新しません")
    if disclosures:
        write_json(disc_path, disclosures)
    else:
        log("開示が空のため disclosures.json は更新しません")
    write_json(fin_path, financials)

    fin_count = sum(1 for v in financials["stocks"].values() if v.get("a") or v.get("q"))
    meta = {
        "generated_at": generated_at,
        "sources": {
            "stocks": "JPX 東証上場銘柄一覧",
            "schedule": "JPX 決算発表予定日",
            "disclosures": "TDnet (やのしんWebAPI)",
            "market_cap": "Yahoo Finance (取得できた銘柄のみ)",
            "financials": "Yahoo Finance (巡回取得)",
            "prices": "Yahoo Finance (終値・前日比・52週高安・出来高)",
        },
        "counts": {
            "stocks": len(stocks),
            "market_caps": len(caps),
            "prices": len(prices),
            "schedule": len(schedule),
            "disclosures": len(disclosures),
            "financials": fin_count,
        },
    }

    # 保存リスト銘柄の決算短信PDFを恒久保存
    try:
        archive_watchlist_pdfs(args.out, args.pdf_dir, args.watchlist)
    except Exception as e:  # noqa: BLE001
        log(f"PDF恒久保存でエラー: {e}")

    write_json(os.path.join(args.out, "meta.json"), meta)
    log(f"完了: {json.dumps(meta['counts'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
