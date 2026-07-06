#!/usr/bin/env python3
"""実データ取得スクリプト (GitHub Actions で定期実行)。

公式・公開データソースから以下を取得し、frontend/data/*.json に書き出す。
GitHub Pages 上の静的アプリ (frontend/local-api.js) がこの JSON を読み込む。

- 銘柄マスタ:  JPX「東証上場銘柄一覧」data_j.xls
               (コード / 銘柄名 / 市場区分 / 33業種)
- 決算発表予定: JPX「決算発表予定日」ページからリンクされる Excel
- 決算短信:    TDnet (やのしんWebAPI 経由) の適時開示から
               決算短信・訂正短信・業績予想修正・決算説明資料を抽出
- 時価総額:    JPX 統計ページの銘柄別時価総額 Excel (ベストエフォート。
               取得できない場合は null のままにする)

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
import urllib.parse

import requests

JPX_BASE = "https://www.jpx.co.jp"
DATA_J_URL = JPX_BASE + "/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
SCHEDULE_INDEX_URL = JPX_BASE + "/listing/event-schedules/financial-announcement/index.html"
# 銘柄別の時価総額 Excel を探すページ候補 (構成変更に備え複数)
MARKET_CAP_PAGES = [
    JPX_BASE + "/markets/statistics-equities/misc/02.html",
    JPX_BASE + "/markets/statistics-equities/misc/index.html",
]
YANOSHIN_URL = "https://webapi.yanoshin.jp/webapi/tdnet/list/{start}-{end}.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; kessan-navi-updater/1.0)"}
TIMEOUT = 90


def log(msg):
    print(f"[fetch_real_data] {msg}", flush=True)


def jst_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)


def http_get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
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
    html = http_get(page_url).text
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


def fetch_disclosures(days=35, limit=10000):
    end = jst_now().date()
    start = end - datetime.timedelta(days=days)
    url = YANOSHIN_URL.format(start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    log(f"TDnet 開示取得: {url}")
    data = http_get(url, params={"limit": limit}).json()
    items = data.get("items", [])
    log(f"開示 {len(items)} 件取得")

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
        tid = str(t.get("id", "") or "")
        if not tid:
            tid = hashlib.md5(f"{code}|{published_at}|{title}".encode()).hexdigest()[:12]
        out.append({
            "key": tid,
            "code": code,
            "title": title,
            "pdf_url": pdf_url,
            "doc_type": doc_type,
            "published_at": published_at,
        })
    out.sort(key=lambda x: x["published_at"] or "", reverse=True)
    log(f"決算関連開示: {len(out)}件")
    return out


# ---------------------------------------------------------------------------
# 時価総額 (JPX 統計、ベストエフォート)
# ---------------------------------------------------------------------------
def fetch_market_caps():
    import pandas as pd

    for page in MARKET_CAP_PAGES:
        try:
            html = http_get(page).text
        except Exception as e:  # noqa: BLE001
            log(f"時価総額ページ取得失敗 {page}: {e}")
            continue
        # ページ内の Excel リンクのうち、ファイル名/周辺に「時価総額」を含むものを試す
        candidates = []
        for m in re.finditer(r'<a[^>]+href="([^"]+?\.xlsx?)"[^>]*>(.*?)</a>', html,
                             flags=re.IGNORECASE | re.DOTALL):
            href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            if "時価総額" in text or "jikasougaku" in href or "value" in href.lower():
                candidates.append(urllib.parse.urljoin(page, href))
        log(f"{page}: 時価総額候補 {len(candidates)}件")
        for url in candidates[:4]:
            try:
                content = http_get(url).content
                xls = pd.ExcelFile(io.BytesIO(content))
                for sheet in xls.sheet_names:
                    raw = xls.parse(sheet, header=None, dtype=object)
                    header_idx, c_code, c_cap = None, None, None
                    for i in range(min(len(raw), 15)):
                        vals = [str(v) for v in raw.iloc[i].tolist()]
                        if any("コード" in v for v in vals) and any("時価総額" in v for v in vals):
                            header_idx = i
                            for j, v in enumerate(vals):
                                if "コード" in v and c_code is None:
                                    c_code = j
                                if "時価総額" in v and c_cap is None:
                                    c_cap = j
                            break
                    if header_idx is None:
                        continue
                    caps = {}
                    for i in range(header_idx + 1, len(raw)):
                        r = raw.iloc[i].tolist()
                        code = normalize_code(r[c_code] if c_code < len(r) else "")
                        if not code:
                            continue
                        try:
                            v = float(str(r[c_cap]).replace(",", ""))
                        except (TypeError, ValueError):
                            continue
                        # JPX の統計は百万円単位が通例。桁から単位を推定する。
                        yen = v * 1_000_000 if v < 1e12 else v
                        caps[code] = int(yen)
                    if len(caps) > 1000:
                        log(f"時価総額: {url.rsplit('/', 1)[-1]} から {len(caps)}件")
                        return caps
            except Exception as e:  # noqa: BLE001
                log(f"  {url}: 解析失敗 ({e})")
    log("時価総額: 取得できず (null のままにします)")
    return {}


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
    parser.add_argument("--days", type=int, default=35, help="開示の取得日数")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    generated_at = jst_now().replace(microsecond=0).isoformat()

    # 1) 銘柄マスタ (失敗したら致命的)
    stocks = fetch_stock_master()

    # 2) 時価総額 (ベストエフォート)
    caps = {}
    try:
        caps = fetch_market_caps()
    except Exception as e:  # noqa: BLE001
        log(f"時価総額の取得でエラー: {e}")
    for code, cap in caps.items():
        if code in stocks:
            stocks[code]["market_cap"] = cap

    # 3) 決算発表予定
    schedule = []
    try:
        schedule = [r for r in fetch_schedule() if r["code"] in stocks]
    except Exception as e:  # noqa: BLE001
        log(f"決算発表予定の取得でエラー: {e}")

    # 4) 決算短信ほか開示
    disclosures = []
    try:
        disclosures = [d for d in fetch_disclosures(days=args.days) if d["code"] in stocks]
    except Exception as e:  # noqa: BLE001
        log(f"開示の取得でエラー: {e}")

    # 書き出し (空データでの上書きはしない)
    write_json(os.path.join(args.out, "stocks.json"), sorted(stocks.values(), key=lambda s: s["code"]))
    if schedule:
        write_json(os.path.join(args.out, "schedule.json"), schedule)
    else:
        log("決算発表予定が空のため schedule.json は更新しません")
    if disclosures:
        write_json(os.path.join(args.out, "disclosures.json"), disclosures)
    else:
        log("開示が空のため disclosures.json は更新しません")

    meta = {
        "generated_at": generated_at,
        "sources": {
            "stocks": "JPX 東証上場銘柄一覧",
            "schedule": "JPX 決算発表予定日",
            "disclosures": "TDnet (やのしんWebAPI)",
            "market_cap": "JPX 統計 (取得できた場合)",
        },
        "counts": {
            "stocks": len(stocks),
            "market_caps": len(caps),
            "schedule": len(schedule),
            "disclosures": len(disclosures),
        },
    }
    write_json(os.path.join(args.out, "meta.json"), meta)
    log(f"完了: {json.dumps(meta['counts'], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
