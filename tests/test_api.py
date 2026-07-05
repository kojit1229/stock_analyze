"""決算ナビ バックエンドの統合テスト（標準ライブラリ unittest）。

一時ディレクトリを DB / PDF 保存先に割り当てたうえで、HTTP サーバを実際に
起動して API をエンドツーエンドで検証する。
"""
import datetime
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

# --- テスト用の隔離した保存先を import 前に設定 ---
_TMP = tempfile.mkdtemp(prefix="kessan_test_")
os.environ["KESSAN_DATA_DIR"] = _TMP
os.environ["KESSAN_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["KESSAN_PDF_DIR"] = os.path.join(_TMP, "pdfs")

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kessan import config, db, fetcher, models, seed  # noqa: E402
from kessan import market_cap as mc  # noqa: E402
from kessan.server import Handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

REF_DATE = datetime.date(2026, 1, 15)  # 決定論的な基準日（木曜）


def reset_db():
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    if os.path.isdir(config.PDF_DIR):
        shutil.rmtree(config.PDF_DIR)
    db.init_db()
    seed.seed(reference_date=REF_DATE)


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(_TMP, ignore_errors=True)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url(path), data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), r
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else None), e

    def get_bytes(self, path):
        with urllib.request.urlopen(self.url(path)) as r:
            return r.status, r.read(), r.headers


class TestMarketCap(unittest.TestCase):
    def test_classify_ranges(self):
        self.assertEqual(mc.classify(50 * mc.OKU), "lt100oku")
        self.assertEqual(mc.classify(200 * mc.OKU), "100to300oku")
        self.assertEqual(mc.classify(mc.CHO), "gte1cho")
        self.assertEqual(mc.classify(2 * mc.CHO), "gte1cho")

    def test_bounds(self):
        lo, hi = mc.range_bounds("100to300oku")
        self.assertEqual(lo, 100 * mc.OKU)
        self.assertEqual(hi, 300 * mc.OKU)
        lo, hi = mc.range_bounds("gte1cho")
        self.assertIsNone(hi)

    def test_format(self):
        self.assertEqual(mc.format_oku(150 * mc.OKU), "150億円")
        self.assertIn("兆円", mc.format_oku(3 * mc.CHO))


class TestStaticAndMeta(ServerTestCase):
    def test_index_served(self):
        status, body, headers = self.get_bytes("/")
        self.assertEqual(status, 200)
        self.assertIn(b"\xe6\xb1\xba\xe7\xae\x97", body)  # "決算" in UTF-8

    def test_meta(self):
        status, body, _ = self.call("GET", "/api/meta")
        self.assertEqual(status, 200)
        self.assertIn("last_updated", body)

    def test_cap_ranges(self):
        status, body, _ = self.call("GET", "/api/cap-ranges")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["ranges"]), 6)


class TestSchedule(ServerTestCase):
    def test_list_all(self):
        status, body, _ = self.call("GET", "/api/schedule")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], len(seed.SAMPLE_STOCKS))

    def test_filter_by_code(self):
        status, body, _ = self.call("GET", "/api/schedule?code=7203")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["code"], "7203")

    def test_filter_by_name(self):
        status, body, _ = self.call("GET", "/api/schedule?name=" + urllib.parse.quote("トヨタ"))
        self.assertEqual(body["count"], 1)

    def test_cap_range_filter(self):
        status, body, _ = self.call("GET", "/api/schedule?cap_range=gte1cho")
        for item in body["items"]:
            self.assertGreaterEqual(item["market_cap"], mc.CHO)

    def test_custom_cap_range(self):
        # 100億〜300億円
        lo, hi = 100 * mc.OKU, 300 * mc.OKU
        status, body, _ = self.call("GET", f"/api/schedule?cap_min={lo}&cap_max={hi}")
        for item in body["items"]:
            self.assertGreaterEqual(item["market_cap"], lo)
            self.assertLess(item["market_cap"], hi)

    def test_sort_by_cap_desc(self):
        status, body, _ = self.call("GET", "/api/schedule?sort=cap&order=desc")
        caps = [i["market_cap"] for i in body["items"]]
        self.assertEqual(caps, sorted(caps, reverse=True))

    def test_market_cap_label_present(self):
        status, body, _ = self.call("GET", "/api/schedule?code=7203")
        self.assertIn("兆円", body["items"][0]["market_cap_label"])


class TestScheduleDateRange(unittest.TestCase):
    def setUp(self):
        reset_db()

    def test_today_range(self):
        items = models.list_schedule({"date_range": "today"}, reference_date=REF_DATE)
        for i in items:
            self.assertEqual(i["announce_date"], REF_DATE.isoformat())
        self.assertTrue(len(items) >= 1)

    def test_month_range(self):
        items = models.list_schedule({"date_range": "month"}, reference_date=REF_DATE)
        end = (REF_DATE + datetime.timedelta(days=30)).isoformat()
        for i in items:
            self.assertLessEqual(i["announce_date"], end)


class TestMyStocks(ServerTestCase):
    def setUp(self):
        # 各テスト前に登録銘柄をクリア
        conn = db.connect()
        conn.execute("DELETE FROM my_stocks")
        conn.execute("DELETE FROM disclosures")
        conn.commit()
        conn.close()

    def test_register_and_list(self):
        status, body, _ = self.call("POST", "/api/mystocks",
                                    {"code": "7203", "holding_type": "保有中", "importance": 5, "memo": "主力"})
        self.assertEqual(status, 201)
        status, body, _ = self.call("GET", "/api/mystocks")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["code"], "7203")
        self.assertEqual(body["items"][0]["holding_type"], "保有中")

    def test_register_unknown_code(self):
        status, body, _ = self.call("POST", "/api/mystocks", {"code": "0000"})
        self.assertEqual(status, 404)

    def test_register_requires_code(self):
        status, body, _ = self.call("POST", "/api/mystocks", {})
        self.assertEqual(status, 400)

    def test_update(self):
        self.call("POST", "/api/mystocks", {"code": "6758"})
        status, body, _ = self.call("PATCH", "/api/mystocks/6758", {"importance": 1, "memo": "更新"})
        self.assertEqual(status, 200)
        self.assertEqual(body["registration"]["importance"], 1)
        self.assertEqual(body["registration"]["memo"], "更新")

    def test_delete(self):
        self.call("POST", "/api/mystocks", {"code": "9984"})
        status, body, _ = self.call("DELETE", "/api/mystocks/9984")
        self.assertEqual(status, 200)
        status, body, _ = self.call("GET", "/api/mystocks")
        self.assertEqual(body["count"], 0)

    def test_delete_missing(self):
        status, body, _ = self.call("DELETE", "/api/mystocks/7203")
        self.assertEqual(status, 404)


class TestFetchAndDisclosures(ServerTestCase):
    def setUp(self):
        conn = db.connect()
        conn.execute("DELETE FROM my_stocks")
        conn.execute("DELETE FROM disclosures")
        conn.commit()
        conn.close()

    def test_fetch_creates_disclosure_for_registered(self):
        # 7203 は基準日以前(オフセット0=1/15)に決算 → 登録して取得すると1件
        models.add_my_stock("7203")
        count = fetcher.run_fetch(reference_date=REF_DATE)
        self.assertEqual(count, 1)
        items = models.list_disclosures({"code": "7203"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["doc_type"], "決算短信")
        # PDF ファイルが実在する
        path = os.path.join(config.PDF_DIR, items[0]["pdf_path"])
        self.assertTrue(os.path.exists(path))

    def test_fetch_dedup(self):
        models.add_my_stock("7203")
        first = fetcher.run_fetch(reference_date=REF_DATE)
        second = fetcher.run_fetch(reference_date=REF_DATE)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # 重複取得しない

    def test_fetch_skips_future(self):
        # 2158 (index15, offset15 → 1/30) は基準日1/15より未来 → 取得されない
        models.add_my_stock("2158")
        count = fetcher.run_fetch(reference_date=REF_DATE)
        self.assertEqual(count, 0)

    def test_only_registered_fetched(self):
        # 登録なしで取得 → 0件
        count = fetcher.run_fetch(reference_date=REF_DATE)
        self.assertEqual(count, 0)

    def test_pdf_endpoint(self):
        models.add_my_stock("7203")
        fetcher.run_fetch(reference_date=REF_DATE)
        did = models.list_disclosures({"code": "7203"})[0]["id"]
        status, data, headers = self.get_bytes(f"/api/disclosures/{did}/pdf")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/pdf")
        self.assertTrue(data.startswith(b"%PDF"))

    def test_mark_read(self):
        models.add_my_stock("7203")
        fetcher.run_fetch(reference_date=REF_DATE)
        did = models.list_disclosures({"code": "7203"})[0]["id"]
        status, body, _ = self.call("POST", f"/api/disclosures/{did}/read", {"is_read": True})
        self.assertEqual(status, 200)
        self.assertEqual(body["is_read"], 1)
        # 未閲覧フィルタで消える
        status, body, _ = self.call("GET", "/api/disclosures?unread=1")
        self.assertEqual(len([x for x in body["items"] if x["id"] == did]), 0)

    def test_comment_update(self):
        models.add_my_stock("7203")
        fetcher.run_fetch(reference_date=REF_DATE)
        did = models.list_disclosures({"code": "7203"})[0]["id"]
        status, body, _ = self.call("PATCH", f"/api/disclosures/{did}", {"comment": "好決算"})
        self.assertEqual(body["comment"], "好決算")


class TestStockDetailAndHome(ServerTestCase):
    def test_stock_detail(self):
        status, body, _ = self.call("GET", "/api/stocks/7203")
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "トヨタ自動車")
        self.assertIn("schedules", body)

    def test_stock_not_found(self):
        status, body, _ = self.call("GET", "/api/stocks/9999")
        self.assertEqual(status, 404)

    def test_home_summary(self):
        status, body, _ = self.call("GET", "/api/home")
        self.assertEqual(status, 200)
        for key in ("todays_earnings", "unread_disclosures", "fetched_total", "watchlist", "last_updated"):
            self.assertIn(key, body)


if __name__ == "__main__":
    import urllib.parse  # noqa
    unittest.main(verbosity=2)
