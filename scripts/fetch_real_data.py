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
    (lambda t: "決算説明" in t, "決算説明資料"),
]


def classify_doc(title):
    for cond, label in DOC_TYPE_RULES:
        if cond(title):
            return label
    return None


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
            fail_days += 1
            continue
        ok_days += 1
        day_count = 0
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
            tid = str(t.get("id", "") or "")
            if not tid:
                tid = hashlib.md5(f"{code}|{published_at}|{title}".encode()).hexdigest()[:12]
            if tid in seen:
                continue
            seen.add(tid)
            day_count += 1
            out.append({
                "key": tid,
                "code": code,
                "title": title,
                "pdf_url": pdf_url,
                "doc_type": doc_type,
                "published_at": published_at,
            })
        if day_count:
            log(f"  {ds}: 決算関連 {day_count}件")
        time.sleep(0.5)
    out.sort(key=lambda x: x["published_at"] or "", reverse=True)
    log(f"決算関連開示: {len(out)}件 (成功{ok_days}日 / 失敗{fail_days}日)")
    return out


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
    """Yahoo Finance のバッチ quote API から銘柄別時価総額(円)を取得する。

    /v7/finance/quote を100銘柄ずつ呼ぶ。失敗した銘柄・バッチはスキップし、
    取得できた分だけ返す。
    """
    caps = {}
    symbols = [c + ".T" for c in codes]
    batches = fails = 0
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        batches += 1
        try:
            r = s.get(YAHOO_QUOTE_URL, timeout=30, params={
                "symbols": ",".join(chunk),
                "fields": "marketCap",
                "crumb": crumb,
            })
            r.raise_for_status()
            for q in (r.json().get("quoteResponse", {}).get("result") or []):
                sym = str(q.get("symbol", ""))
                cap = q.get("marketCap")
                if cap and sym.endswith(".T"):
                    caps[sym[:-2]] = int(cap)
        except Exception as e:  # noqa: BLE001
            fails += 1
            if fails <= 3:
                log(f"  Yahoo quote バッチ{batches}失敗: {type(e).__name__}: {e}")
        time.sleep(0.4)
    log(f"時価総額: Yahoo Finance から {len(caps)}件 ({batches}バッチ中 失敗{fails})")
    return caps


# ---------------------------------------------------------------------------
# 財務数値の推移 (Yahoo fundamentals-timeseries、巡回取得)
# ---------------------------------------------------------------------------
TS_TYPES = [
    "annualTotalRevenue", "annualOperatingIncome", "annualNetIncome", "annualDilutedEPS",
    "quarterlyTotalRevenue", "quarterlyOperatingIncome", "quarterlyNetIncome", "quarterlyDilutedEPS",
]


def parse_timeseries(payload):
    """Yahoo fundamentals-timeseries 応答を {a: [...], q: [...]} に変換する。

    各要素は [期末日, 売上高, 営業利益, 純利益, EPS] (取得できない値は None)。
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

    def pack(bucket):
        out = []
        for d in sorted(bucket):
            m = bucket[d]
            out.append([
                d,
                m.get("TotalRevenue"),
                m.get("OperatingIncome"),
                m.get("NetIncome"),
                m.get("DilutedEPS"),
            ])
        return out[-12:]  # 直近12期分まで保持

    return {"a": pack(annual), "q": pack(quarterly)}


def fetch_financials(codes, existing, s, crumb, per_run=400):
    """財務数値の推移を巡回取得する。

    Yahoo の fundamentals-timeseries は銘柄ごとに1リクエスト必要なため、
    1回の実行では per_run 銘柄だけ更新し、実行ごとにローテーションする
    (データ無し → 最終取得が古い順)。結果は existing にマージされる。
    """
    stocks_data = existing.setdefault("stocks", {})
    today = jst_now().strftime("%Y-%m-%d")
    order = sorted(codes, key=lambda c: (stocks_data.get(c, {}).get("t", ""), c))
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
            if entry["a"] or entry["q"]:
                stocks_data[code] = entry
                ok += 1
            else:
                # データが無い銘柄も t を記録し、毎回問い合わせないようにする
                old = stocks_data.get(code, {})
                stocks_data[code] = {"a": old.get("a", []), "q": old.get("q", []), "t": today}
                empty += 1
        except Exception as e:  # noqa: BLE001
            fails += 1
            if fails <= 3:
                log(f"  財務 {sym} 失敗: {type(e).__name__}: {e}")
        time.sleep(0.35)
    log(f"財務数値: 更新{ok}件 / データ無し{empty}件 / 失敗{fails}件 (対象{len(targets)}銘柄)")
    return existing


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
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    generated_at = jst_now().replace(microsecond=0).isoformat()

    # 1) 銘柄マスタ (失敗したら致命的)
    stocks = fetch_stock_master()

    # 2) 時価総額 + 財務数値 (Yahoo Finance、ベストエフォート)
    ysession, ycrumb = yahoo_session()
    caps = {}
    if ysession:
        try:
            caps = fetch_market_caps(sorted(stocks.keys()), ysession, ycrumb)
        except Exception as e:  # noqa: BLE001
            log(f"時価総額の取得でエラー: {e}")
    for code, cap in caps.items():
        if code in stocks:
            stocks[code]["market_cap"] = cap

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
        },
        "counts": {
            "stocks": len(stocks),
            "market_caps": len(caps),
            "schedule": len(schedule),
            "disclosures": len(disclosures),
            "financials": fin_count,
        },
    }
    write_json(os.path.join(args.out, "meta.json"), meta)
    log(f"完了: {json.dumps(meta['counts'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
