"""HTTP サーバとルーティング（標準ライブラリのみ）。

`http.server.ThreadingHTTPServer` を用い、/api/* を JSON API に、それ以外を
frontend/ の静的ファイル配信にマッピングする。
"""
import json
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api, config, db, seed

# ルート定義: (method, 正規表現, handler)
ROUTES = [
    ("GET", r"^/api/home$", api.get_home),
    ("GET", r"^/api/meta$", api.get_meta),
    ("GET", r"^/api/sectors$", api.get_sectors),
    ("GET", r"^/api/markets$", api.get_markets),
    ("GET", r"^/api/cap-ranges$", api.get_cap_ranges),
    ("GET", r"^/api/schedule$", api.get_schedule),
    ("GET", r"^/api/stocks/(?P<code>[^/]+)$", api.get_stock),
    ("GET", r"^/api/mystocks$", api.get_my_stocks),
    ("POST", r"^/api/mystocks$", api.post_my_stock),
    ("PATCH", r"^/api/mystocks/(?P<code>[^/]+)$", api.patch_my_stock),
    ("DELETE", r"^/api/mystocks/(?P<code>[^/]+)$", api.delete_my_stock),
    ("GET", r"^/api/disclosures$", api.get_disclosures),
    ("POST", r"^/api/disclosures/(?P<id>\d+)/read$", api.post_disclosure_read),
    ("GET", r"^/api/disclosures/(?P<id>\d+)/pdf$", api.get_disclosure_pdf),
    ("GET", r"^/api/disclosures/(?P<id>\d+)$", api.get_disclosure),
    ("PATCH", r"^/api/disclosures/(?P<id>\d+)$", api.patch_disclosure),
    ("POST", r"^/api/fetch$", api.post_fetch),
]

_COMPILED = [(m, re.compile(p), h) for (m, p, h) in ROUTES]

# 静的ファイルの Content-Type
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def dispatch(method, path, query, body):
    """ルートを探索し handler を呼び出す。戻り値は (status, headers, body_bytes)。"""
    for m, regex, handler in _COMPILED:
        if m != method:
            continue
        match = regex.match(path)
        if not match:
            continue
        try:
            result = handler(match.groupdict(), query, body)
        except api.ApiError as e:
            return _json_response(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001 - API 全体のフォールバック
            return _json_response(500, {"error": str(e)})
        return _to_response(result)
    return _json_response(404, {"error": "not found"})


def _to_response(result):
    if isinstance(result, api.Raw):
        headers = {"Content-Type": result.content_type}
        headers.update(result.headers)
        return result.status, headers, result.data
    if isinstance(result, tuple):
        status, payload = result
        return _json_response(status, payload)
    return _json_response(200, result)


def _json_response(status, payload):
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8"}, data


class Handler(BaseHTTPRequestHandler):
    server_version = "KessanHTTP/0.1"

    def log_message(self, fmt, *args):  # 静かにする
        pass

    # --- ディスパッチ ---
    def _handle(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        if path.startswith("/api/"):
            body = self._read_json_body() if method in ("POST", "PATCH", "PUT") else None
            status, headers, data = dispatch(method, path, query, body)
            self._send(status, headers, data)
            return

        if method == "GET":
            self._serve_static(path)
            return
        self._send(*_json_response(405, {"error": "method not allowed"}))

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    # --- 補助 ---
    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        # ディレクトリトラバーサル対策
        rel = path.lstrip("/")
        full = _safe_join(config.FRONTEND_DIR, rel)
        if full is None or not _isfile(full):
            # SPA フォールバック: 未知パスは index.html
            full = _safe_join(config.FRONTEND_DIR, "index.html")
            if full is None or not _isfile(full):
                self._send(*_json_response(404, {"error": "not found"}))
                return
        ext = full[full.rfind("."):] if "." in full else ""
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, {"Content-Type": ctype}, data)

    def _send(self, status, headers, data):
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _safe_join(base, rel):
    import os

    full = os.path.normpath(os.path.join(base, rel))
    if not full.startswith(os.path.abspath(base)):
        return None
    return full


def _isfile(path):
    import os

    return os.path.isfile(path)


def create_server(host="127.0.0.1", port=8000):
    db.init_db()
    seed.seed()
    return ThreadingHTTPServer((host, port), Handler)


def run(host="127.0.0.1", port=8000):
    httpd = create_server(host, port)
    print(f"決算短信アプリ 起動: http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
        httpd.server_close()
