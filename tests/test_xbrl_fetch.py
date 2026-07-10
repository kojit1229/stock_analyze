"""scripts/xbrl_fetch.py (XBRL zip取得・キャッシュ・展開) のテスト。

実ネットワークアクセスはしない。opener/fetcher を差し替えてモックし、
合成zipバイト列(io.BytesIO + zipfile)で検証する。
"""
import importlib.util
import io
import os
import tempfile
import unittest
import zipfile
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "xbrl_fetch", os.path.join(os.path.dirname(__file__), "..", "scripts", "xbrl_fetch.py"))
xf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xf)


def _build_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


SUMMARY_HTM = b"<html><body>summary-fact</body></html>"


class _FakeResponse:
    """urllib.request.urlopen 互換の with文レスポンス。"""

    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


class TestExtractSummary(unittest.TestCase):
    def test_picks_summary_htm_over_other_files(self):
        z = _build_zip({
            "XBRLData/Summary/tse-qcedjpsm-0000-ixbrl.htm": SUMMARY_HTM,
            "XBRLData/Attachment/qualitative.htm": b"not the summary",
        })
        self.assertEqual(xf._extract_summary(z), SUMMARY_HTM)

    def test_returns_none_for_bad_zip(self):
        self.assertIsNone(xf._extract_summary(b"not a zip"))

    def test_returns_none_when_no_summary_dir(self):
        z = _build_zip({"XBRLData/Attachment/qualitative.htm": b"x"})
        self.assertIsNone(xf._extract_summary(z))


class TestFetchSummaryBytes(unittest.TestCase):
    def test_fetches_extracts_and_caches(self):
        z = _build_zip({"XBRLData/Summary/x-ixbrl.htm": SUMMARY_HTM})
        opener = mock.Mock(return_value=_FakeResponse(z))
        with tempfile.TemporaryDirectory() as tmp:
            result = xf.fetch_summary_bytes("https://example/x.zip", "K1", cache_dir=tmp, opener=opener)
            self.assertEqual(result, SUMMARY_HTM)
            self.assertTrue(os.path.exists(os.path.join(tmp, "K1.htm")))
            opener.assert_called_once()

            # 2回目はキャッシュから返し、opener は呼ばれない (冪等)
            result2 = xf.fetch_summary_bytes("https://example/x.zip", "K1", cache_dir=tmp, opener=opener)
            self.assertEqual(result2, SUMMARY_HTM)
            opener.assert_called_once()

    def test_network_failure_returns_none_without_raising(self):
        opener = mock.Mock(side_effect=OSError("timeout"))
        with tempfile.TemporaryDirectory() as tmp:
            result = xf.fetch_summary_bytes("https://example/x.zip", "K2", cache_dir=tmp, opener=opener)
        self.assertIsNone(result)

    def test_bad_zip_returns_none_without_raising(self):
        opener = mock.Mock(return_value=_FakeResponse(b"not a zip"))
        with tempfile.TemporaryDirectory() as tmp:
            result = xf.fetch_summary_bytes("https://example/x.zip", "K3", cache_dir=tmp, opener=opener)
        self.assertIsNone(result)

    def test_missing_url_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = xf.fetch_summary_bytes("", "K4", cache_dir=tmp)
        self.assertIsNone(result)


class TestMakeLookup(unittest.TestCase):
    DISCLOSURES = [
        {"key": "K1", "code": "1301", "doc_type": "決算短信", "xbrl_url": "https://x/1.zip",
         "published_at": "2026-07-07T15:00:00"},
        {"key": "K2", "code": "7203", "doc_type": "業績予想修正", "xbrl_url": "https://x/2.zip",
         "published_at": "2026-07-07T15:00:00"},  # 決算短信ではないので対象外
        {"key": "K3", "code": "6506", "doc_type": "決算短信", "xbrl_url": "",
         "published_at": "2026-07-07T15:00:00"},  # xbrl_urlが空なので対象外
        {"key": "K4", "code": "9972", "doc_type": "決算短信", "xbrl_url": "https://x/4.zip",
         "published_at": "2026-07-06T15:00:00"},  # 前日分なので対象外
    ]

    def test_lookup_calls_fetcher_only_for_todays_tanshin_with_xbrl_url(self):
        fetcher = mock.Mock(return_value=b"summary-bytes")
        lookup = xf.make_lookup(self.DISCLOSURES, "2026-07-07", fetcher=fetcher)

        self.assertEqual(lookup("1301"), b"summary-bytes")
        fetcher.assert_called_once_with("https://x/1.zip", "K1", cache_dir=xf.CACHE_DIR)

        self.assertIsNone(lookup("7203"))  # 対象外の開示種別
        self.assertIsNone(lookup("6506"))  # xbrl_urlが空
        self.assertIsNone(lookup("9972"))  # 前日分
        self.assertIsNone(lookup("0000"))  # 該当開示なし

    def test_multiple_same_day_disclosures_use_latest_published_at(self):
        discs = [
            {"key": "OLD", "code": "1301", "doc_type": "決算短信", "xbrl_url": "https://x/old.zip",
             "published_at": "2026-07-07T13:00:00"},
            {"key": "NEW", "code": "1301", "doc_type": "訂正決算短信", "xbrl_url": "https://x/new.zip",
             "published_at": "2026-07-07T16:00:00"},
        ]
        fetcher = mock.Mock(return_value=b"x")
        lookup = xf.make_lookup(discs, "2026-07-07", fetcher=fetcher)
        lookup("1301")
        fetcher.assert_called_once_with("https://x/new.zip", "NEW", cache_dir=xf.CACHE_DIR)


if __name__ == "__main__":
    unittest.main()
