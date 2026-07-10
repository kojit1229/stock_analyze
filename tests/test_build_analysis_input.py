"""scripts/build_analysis_input.py のテスト。

PDF本文由来の値を混入させないこと(入力ソースの限定)と、欠損時のフェイルラウドを担保する。
"""
import importlib.util
import json
import os
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location(
    "build_analysis_input",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "build_analysis_input.py"))
bai = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bai)

DISCLOSURE = {"key": "1264399", "code": "8125", "title": "2027年２月期第１四半期決算短信〔日本基準〕(連結)",
              "doc_type": "決算短信", "published_at": "2026-07-09T16:00:00",
              "pdf_url": "https://webapi.yanoshin.jp/rd.php?https://example/dummy.pdf"}
ANNUAL = [["2025-03-31", 1000.0, 400.0, 300.0, 10.0], ["2026-03-31", 1200.0, 500.0, 380.0, 12.0]]
QUARTERLY = [["2025-06-30", 260.0, 100.0, 80.0, 2.0], ["2026-06-30", 300.0, 150.0, 110.0, 3.0]]


def _write_fixture(tmp, annual=ANNUAL, quarterly=QUARTERLY, discs=None, market_date="2026-07-09"):
    with open(os.path.join(tmp, "financials.json"), "w", encoding="utf-8") as f:
        json.dump({"stocks": {"8125": {"a": annual, "q": quarterly}}}, f)
    with open(os.path.join(tmp, "disclosures.json"), "w", encoding="utf-8") as f:
        json.dump(discs or [], f)
    market_dir = os.path.join(tmp, "market")
    os.makedirs(market_dir, exist_ok=True)
    with open(os.path.join(market_dir, market_date + ".md"), "w", encoding="utf-8") as f:
        f.write("# 市況概況 " + market_date + "\n\n値上がり優勢。\n")
    with open(os.path.join(market_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"dates": [market_date]}, f)


class TestProgressDetail(unittest.TestCase):
    def test_ahead_of_last_year_returns_numbers(self):
        detail = bai.progress_detail(ANNUAL, QUARTERLY)
        self.assertEqual(detail["current_pct"], 30.0)
        self.assertEqual(detail["prior_year_same_point_pct"], 25.0)
        self.assertEqual(detail["diff_pt"], 5.0)

    def test_no_quarterly_returns_none(self):
        self.assertIsNone(bai.progress_detail(ANNUAL, []))


class TestBuildAnalysisInput(unittest.TestCase):
    def test_happy_path_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_fixture(tmp, discs=[{"code": "8125", "doc_type": "業績予想修正",
                                        "title": "業績予想の上方修正について",
                                        "published_at": "2026-07-01T15:00:00"}])
            material = bai.build_analysis_input("8125", DISCLOSURE, tmp)
        self.assertEqual(material["code"], "8125")
        self.assertEqual(material["disclosure"]["id"], "1264399")
        self.assertEqual(material["growth_signal"], 1)
        self.assertEqual(material["revision_signal"], 1)
        self.assertIn("値上がり優勢", material["market_context_md"])
        self.assertEqual(material["market_context_date"], "2026-07-09")

    def test_missing_financials_entry_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_fixture(tmp)
            with self.assertRaises(bai.InputAssemblyError):
                bai.build_analysis_input("9999", DISCLOSURE, tmp)

    def test_missing_market_md_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "financials.json"), "w", encoding="utf-8") as f:
                json.dump({"stocks": {"8125": {"a": ANNUAL, "q": QUARTERLY}}}, f)
            with open(os.path.join(tmp, "disclosures.json"), "w", encoding="utf-8") as f:
                json.dump([], f)
            with self.assertRaises(bai.InputAssemblyError):
                bai.build_analysis_input("8125", DISCLOSURE, tmp)

    def test_missing_financials_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(bai.InputAssemblyError):
                bai.build_analysis_input("8125", DISCLOSURE, tmp)


class TestSourceDeclaration(unittest.TestCase):
    """PDF本文由来の値を混入させないことの担保。"""

    def test_sources_are_limited_to_financials_disclosures_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_fixture(tmp)
            material = bai.build_analysis_input("8125", DISCLOSURE, tmp)
        self.assertEqual(material["sources"], [
            "frontend/data/financials.json", "frontend/data/disclosures.json",
            "frontend/data/market/2026-07-09.md"])

    def test_pdf_url_is_reference_only_not_fetched(self):
        """モジュールがPDFを取得・パースする手段(urllib等)を一切使っていないことをソースから確認する。"""
        src_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "build_analysis_input.py")
        with open(src_path, encoding="utf-8") as f:
            src = f.read()
        for forbidden in ("urlopen", "urllib", "requests.", "pdfplumber"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
