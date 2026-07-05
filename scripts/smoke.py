#!/usr/bin/env python3
"""起動スモークテスト。

サーバを一時ディレクトリ上で起動し、主要フロー(予定一覧→登録→取得→PDF)を
1 回通して疎通確認する。CI では追加インストールなしで実行できる。
失敗時は非ゼロ終了する。
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request

_TMP = tempfile.mkdtemp(prefix="kessan_smoke_")
os.environ["KESSAN_DATA_DIR"] = _TMP
os.environ["KESSAN_DB_PATH"] = os.path.join(_TMP, "smoke.db")
os.environ["KESSAN_PDF_DIR"] = os.path.join(_TMP, "pdfs")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import ThreadingHTTPServer  # noqa: E402
from kessan import db, seed  # noqa: E402
from kessan.server import Handler  # noqa: E402


def call(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw and r.headers.get("Content-Type", "").startswith("application/json") else raw)


def main():
    db.init_db()
    seed.seed()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        checks = []

        status, body = call(base, "GET", "/api/schedule")
        checks.append(("schedule", status == 200 and body["count"] > 0))

        status, body = call(base, "GET", "/api/schedule?cap_range=gte1cho")
        checks.append(("cap filter", status == 200))

        status, body = call(base, "POST", "/api/mystocks", {"code": "7203"})
        checks.append(("register", status == 201))

        status, body = call(base, "POST", "/api/fetch")
        checks.append(("fetch", status == 200))

        status, body = call(base, "GET", "/api/disclosures")
        checks.append(("disclosures", status == 200))
        did = body["items"][0]["id"] if body["items"] else None

        if did:
            status, pdf = call(base, "GET", f"/api/disclosures/{did}/pdf")
            checks.append(("pdf", status == 200 and pdf[:4] == b"%PDF"))

        status, body = call(base, "GET", "/api/home")
        checks.append(("home", status == 200))

        ok = True
        for name, passed in checks:
            print(f"  [{'OK' if passed else 'NG'}] {name}")
            ok = ok and passed
        if not ok:
            print("SMOKE TEST FAILED")
            sys.exit(1)
        print("SMOKE TEST PASSED")
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
