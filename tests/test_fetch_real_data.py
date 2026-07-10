"""scripts/fetch_real_data.py の純粋関数(ネットワークアクセスを伴わない部分)のテスト。

実ネットワークアクセスはしない(禁止)。fetch_tdnet_day() の http_get 呼び出しは
モックし、やのしんWebAPIの実レスポンス形式(2026-07-10 に実際のAPIへ単発の確認
リクエストを行って検証した形式)を模したフィクスチャで検証する。
"""
import importlib.util
import os
import unittest
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "fetch_real_data",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "fetch_real_data.py"))
frd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frd)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


# やのしんWebAPI (https://webapi.yanoshin.jp/webapi/tdnet/list/...) の実レスポンス形式。
# 決算短信には url_xbrl (サマリーXBRL zipへのリダイレクタURL) が付与されるが、
# 業績予想修正等の開示では null になる (2026-07-10 実データで確認)。
YANOSHIN_ITEMS = {
    "items": [
        {"Tdnet": {
            "id": "1264392", "pubdate": "2026-07-09 16:00:00", "company_code": "50180",
            "title": "2027年２月期第１四半期決算短信〔日本基準〕(連結)",
            "document_url": "https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/140120260707589166.pdf",
            "url_xbrl": "https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/081220260707589166.zip",
        }},
        {"Tdnet": {
            "id": "1264506", "pubdate": "2026-07-09 17:45:00", "company_code": "29270",
            "title": "自己株式の取得状況および消却に関するお知らせ",
            "document_url": "https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/140120260709590704.pdf",
            "url_xbrl": None,
        }},
    ]
}


class TestFetchTdnetDay(unittest.TestCase):
    def test_xbrl_url_is_captured_and_unwrapped_for_kessan_tanshin(self):
        with mock.patch.object(frd, "http_get", return_value=_FakeResponse(YANOSHIN_ITEMS)):
            out = frd.fetch_tdnet_day("20260709")
        tanshin = next(i for i in out if i["code"] == "5018")
        self.assertEqual(tanshin["doc_type"], "決算短信")
        self.assertEqual(tanshin["xbrl_url"],
                          "https://www.release.tdnet.info/inbs/081220260707589166.zip")

    def test_xbrl_url_is_empty_string_when_not_provided(self):
        """自己株式取得のような開示には url_xbrl が無い (null)。空文字にすること
        (None のままだと disclosures.json のJSON化・後続の bool 判定で扱いが揺れるため)。"""
        with mock.patch.object(frd, "http_get", return_value=_FakeResponse(YANOSHIN_ITEMS)):
            out = frd.fetch_tdnet_day("20260709")
        buyback = next(i for i in out if i["code"] == "2927")
        self.assertEqual(buyback["doc_type"], "自己株式取得")
        self.assertEqual(buyback["xbrl_url"], "")

    def test_fetch_failure_returns_none(self):
        with mock.patch.object(frd, "http_get", side_effect=OSError("boom")):
            with mock.patch("time.sleep"):
                out = frd.fetch_tdnet_day("20260709")
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
