"""API ハンドラ層。

各ハンドラは (params, query, body) を受け取り、以下のいずれかを返す:
- dict / list            → 200 で JSON 応答
- (status_int, payload)  → 指定ステータスで JSON 応答
- Raw(content_type, bytes, status) → 生バイナリ応答（PDF 等）
すべての例外は server 側で 500/400 に変換される。
"""
import os

from . import config, fetcher, models
from . import market_cap as mc


class Raw:
    """生バイナリ応答（PDF など）。"""

    def __init__(self, content_type, data, status=200, headers=None):
        self.content_type = content_type
        self.data = data
        self.status = status
        self.headers = headers or {}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# メタ / マスタ
# ---------------------------------------------------------------------------
def get_home(params, query, body):
    return models.home_summary()


def get_meta(params, query, body):
    return {
        "last_updated": models.meta_last_updated(),
        "version": __import__("kessan").__version__,
    }


def get_sectors(params, query, body):
    return {"sectors": models.list_sectors()}


def get_markets(params, query, body):
    return {"markets": models.list_markets()}


def get_cap_ranges(params, query, body):
    return {
        "ranges": [
            {"key": r["key"], "label": r["label"], "min": r["min"], "max": r["max"]}
            for r in mc.MARKET_CAP_RANGES
        ]
    }


# ---------------------------------------------------------------------------
# 決算予定 / 検索・絞り込み
# ---------------------------------------------------------------------------
def get_schedule(params, query, body):
    filters = {
        "date_range": query.get("date_range", "all"),
        "date": query.get("date"),
        "code": query.get("code"),
        "name": query.get("name"),
        "sector": query.get("sector"),
        "market": query.get("market"),
        "cap_range": query.get("cap_range"),
        "cap_min": query.get("cap_min"),
        "cap_max": query.get("cap_max"),
        "sort": query.get("sort", "date"),
        "order": query.get("order", "asc"),
    }
    items = models.list_schedule(filters)
    return {"count": len(items), "items": items}


def get_stock(params, query, body):
    stock = models.get_stock(params["code"])
    if not stock:
        raise ApiError(404, f"銘柄 {params['code']} が見つかりません")
    return stock


# ---------------------------------------------------------------------------
# マイ銘柄
# ---------------------------------------------------------------------------
def get_my_stocks(params, query, body):
    items = models.list_my_stocks()
    return {"count": len(items), "items": items}


def post_my_stock(params, query, body):
    body = body or {}
    code = str(body.get("code", "")).strip()
    if not code:
        raise ApiError(400, "code は必須です")
    try:
        stock = models.add_my_stock(
            code,
            holding_type=body.get("holding_type"),
            importance=body.get("importance", 3),
            memo=body.get("memo", ""),
            notify=body.get("notify", 1),
        )
    except ValueError as e:
        raise ApiError(404, str(e))
    return (201, stock)


def patch_my_stock(params, query, body):
    stock = models.update_my_stock(params["code"], body or {})
    if not stock:
        raise ApiError(404, "登録銘柄が見つかりません")
    return stock


def delete_my_stock(params, query, body):
    ok = models.delete_my_stock(params["code"])
    if not ok:
        raise ApiError(404, "登録銘柄が見つかりません")
    return {"deleted": True, "code": params["code"]}


# ---------------------------------------------------------------------------
# 決算短信
# ---------------------------------------------------------------------------
def get_disclosures(params, query, body):
    filters = {
        "code": query.get("code"),
        "unread": query.get("unread"),
        "doc_type": query.get("doc_type"),
        "cap_range": query.get("cap_range"),
        "cap_min": query.get("cap_min"),
        "cap_max": query.get("cap_max"),
    }
    items = models.list_disclosures(filters)
    return {"count": len(items), "items": items}


def get_disclosure(params, query, body):
    d = models.get_disclosure(int(params["id"]))
    if not d:
        raise ApiError(404, "決算短信が見つかりません")
    return d


def patch_disclosure(params, query, body):
    d = models.update_disclosure(int(params["id"]), body or {})
    if not d:
        raise ApiError(404, "決算短信が見つかりません")
    return d


def post_disclosure_read(params, query, body):
    body = body or {}
    is_read = body.get("is_read", True)
    d = models.update_disclosure(int(params["id"]), {"is_read": is_read})
    if not d:
        raise ApiError(404, "決算短信が見つかりません")
    return d


def get_disclosure_pdf(params, query, body):
    d = models.get_disclosure(int(params["id"]))
    if not d:
        raise ApiError(404, "決算短信が見つかりません")
    path = os.path.join(config.PDF_DIR, d["pdf_path"] or "")
    if not d["pdf_path"] or not os.path.exists(path):
        raise ApiError(404, "PDF ファイルが見つかりません")
    with open(path, "rb") as f:
        data = f.read()
    disp = "attachment" if query.get("download") else "inline"
    filename = d["pdf_path"]
    return Raw(
        "application/pdf",
        data,
        headers={"Content-Disposition": f'{disp}; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# 自動取得
# ---------------------------------------------------------------------------
def post_fetch(params, query, body):
    count = fetcher.run_fetch()
    return {"fetched": count, "message": f"{count}件の決算短信を取得しました"}
