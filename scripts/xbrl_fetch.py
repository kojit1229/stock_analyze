#!/usr/bin/env python3
"""決算短信サマリーXBRL(zip)の取得・キャッシュ・展開 (W2-1)。

disclosures.json の各エントリ (fetch_real_data.py が付与する xbrl_url) から
TDnetの決算短信サマリーXBRL zipを取得し、中のサマリーファイル
(XBRLData/Summary/*-ixbrl.htm。実データで確認した実際のパス。詳細は
xbrl_parser.py のモジュールdocstring) だけを取り出して xbrl_parser.parse_summary()
に渡せる bytes として返す。

設計:
- 対象は当日の決算短信/訂正決算短信のみ (全銘柄・全期間の一括取得はしない)。
- 取得結果は frontend/data/xbrl_cache/<key>.htm にキャッシュし、同じキーは
  再取得しない (冪等)。
- 取得・展開・解析いずれの失敗も例外を送出せず None を返し、ログに残す
  (呼び出し側 generate_alerts.xbrl_confirmed_operating_income が Yahoo由来値へ
  フォールバックする)。

依存: Python標準ライブラリのみ。実ネットワークアクセスはテスト対象外
(fetcher/opener を差し替えてモックする)。
"""
import io
import os
import urllib.request
import zipfile

CACHE_DIR = os.path.join("frontend", "data", "xbrl_cache")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; kessan-navi-updater/1.0)"}
TIMEOUT = 60
TARGET_DOC_TYPES = ("決算短信", "訂正決算短信")


def log(msg):
    print(f"[xbrl_fetch] {msg}", flush=True)


def _cache_path(cache_dir, cache_key):
    return os.path.join(cache_dir, f"{cache_key}.htm")


def _extract_summary(zip_bytes):
    """zip中のサマリーXBRL本体 (XBRLData/Summary/ 配下) を取り出す。
    見つからない・zipとして開けない場合は None。ixbrl/htmを優先し、
    無ければ他の拡張子 (.xbrl/.xml) を候補にする (将来のタクソノミ変化に備える)。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return None
    candidates = [n for n in zf.namelist()
                  if "/Summary/" in n and n.lower().endswith((".htm", ".html", ".xbrl", ".xml"))]
    if not candidates:
        return None
    candidates.sort(key=lambda n: (0 if n.lower().endswith((".htm", ".html")) else 1, n))
    try:
        return zf.read(candidates[0])
    except (KeyError, zipfile.BadZipFile):
        return None


def fetch_summary_bytes(xbrl_zip_url, cache_key, cache_dir=CACHE_DIR, opener=None):
    """XBRL zip URLからサマリーXBRL本体(bytes)を取得する。キャッシュがあれば
    再取得しない。取得・展開に失敗した場合は None を返す(例外を送出しない)。

    opener: urllib.request.urlopen 互換の callable(Request, timeout=) を注入できる
    (テスト用。実ネットワークアクセスを避けるため)。
    """
    if not cache_key:
        return None
    os.makedirs(cache_dir, exist_ok=True)
    path = _cache_path(cache_dir, cache_key)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    if not xbrl_zip_url:
        return None
    opener = opener or urllib.request.urlopen
    try:
        req = urllib.request.Request(xbrl_zip_url, headers=HEADERS)
        with opener(req, timeout=TIMEOUT) as r:
            zip_bytes = r.read()
    except Exception as e:  # noqa: BLE001 — 取得経路の例外は握りつぶさずログにする
        log(f"XBRL zip取得失敗 ({xbrl_zip_url}): {type(e).__name__}: {e}")
        return None
    summary = _extract_summary(zip_bytes)
    if summary is None:
        log(f"XBRL zipにサマリーファイルが見つかりません ({xbrl_zip_url})")
        return None
    with open(path, "wb") as f:
        f.write(summary)
    return summary


def make_lookup(disclosures, today, cache_dir=CACHE_DIR, fetcher=None):
    """generate_alerts.generate() に渡す xbrl_lookup(code) -> bytes|None を作る。

    disclosures: frontend/data/disclosures.json の一覧 (dictのリスト)。
    today: 'YYYY-MM-DD'。当日公表分の決算短信/訂正決算短信のみ対象にする
    (対象を絞ることで全銘柄・全期間の一括取得を避ける。設計原則)。
    同一銘柄・同日に複数開示があれば published_at が最も新しいものを使う。
    fetcher: fetch_summary_bytes の差し替え用 (テスト注入)。
    """
    fetcher = fetcher or fetch_summary_bytes
    by_code = {}
    for d in disclosures or []:
        if d.get("doc_type") not in TARGET_DOC_TYPES:
            continue
        if (d.get("published_at") or "")[:10] != today:
            continue
        if not d.get("xbrl_url"):
            continue
        code = d.get("code")
        prev = by_code.get(code)
        if prev is None or (d.get("published_at") or "") > (prev.get("published_at") or ""):
            by_code[code] = d

    def lookup(code):
        d = by_code.get(code)
        if not d:
            return None
        return fetcher(d["xbrl_url"], d.get("key") or "", cache_dir=cache_dir)

    return lookup
