"""scripts/generate_scores.py の決算スコアリングv1ロジックのテスト。"""
import importlib.util
import json
import os
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location(
    "generate_scores",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_scores.py"))
gs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gs)


class TestGrowthAndMargin(unittest.TestCase):
    def test_increase_both_is_growth_positive(self):
        annual = [["2025-03-31", 100.0, 10.0, 8.0, 5.0], ["2026-03-31", 120.0, 15.0, 11.0, 7.0]]
        growth, margin = gs.compute_growth_and_margin(annual)
        self.assertEqual(growth, 1)  # 増収増益

    def test_decrease_both_is_growth_negative(self):
        annual = [["2025-03-31", 120.0, 15.0, 11.0, 7.0], ["2026-03-31", 100.0, 10.0, 8.0, 5.0]]
        growth, margin = gs.compute_growth_and_margin(annual)
        self.assertEqual(growth, -1)  # 減収減益

    def test_mixed_growth_is_neutral(self):
        annual = [["2025-03-31", 100.0, 10.0, 8.0, 5.0], ["2026-03-31", 120.0, 8.0, 6.0, 4.0]]
        growth, margin = gs.compute_growth_and_margin(annual)
        self.assertEqual(growth, 0)  # 増収減益

    def test_margin_improved(self):
        annual = [["2025-03-31", 100.0, 10.0, 8.0, 5.0], ["2026-03-31", 100.0, 20.0, 15.0, 9.0]]
        growth, margin = gs.compute_growth_and_margin(annual)
        self.assertEqual(margin, 1)

    def test_margin_worsened(self):
        annual = [["2025-03-31", 100.0, 20.0, 15.0, 9.0], ["2026-03-31", 100.0, 10.0, 8.0, 5.0]]
        growth, margin = gs.compute_growth_and_margin(annual)
        self.assertEqual(margin, -1)

    def test_insufficient_annual_history_returns_none(self):
        self.assertEqual(gs.compute_growth_and_margin([["2026-03-31", 100.0, 10.0, 8.0, 5.0]]),
                         (None, None))
        self.assertEqual(gs.compute_growth_and_margin([]), (None, None))

    def test_missing_values_return_none(self):
        annual = [["2025-03-31", None, None, None, 5.0], ["2026-03-31", 100.0, 10.0, 8.0, 7.0]]
        growth, margin = gs.compute_growth_and_margin(annual)
        self.assertIsNone(growth)
        self.assertIsNone(margin)


class TestProgressSignal(unittest.TestCase):
    ANNUAL = [["2025-03-31", 1000.0, 400.0, 300.0, 10.0], ["2026-03-31", 1200.0, 500.0, 380.0, 12.0]]

    def test_ahead_of_last_year(self):
        # 今期Q1営業利益150 / 前期通期500 = 30%、前年同時点は 100/400=25% → +5pt 先行
        quarterly = [["2025-06-30", 260.0, 100.0, 80.0, 2.0], ["2026-06-30", 300.0, 150.0, 110.0, 3.0]]
        self.assertEqual(gs.compute_progress_signal(self.ANNUAL, quarterly), 1)

    def test_behind_last_year(self):
        # 今期Q1営業利益50/500=10%、前年同時点150/400=37.5% → 遅れ
        quarterly = [["2025-06-30", 260.0, 150.0, 80.0, 2.0], ["2026-06-30", 300.0, 50.0, 110.0, 3.0]]
        self.assertEqual(gs.compute_progress_signal(self.ANNUAL, quarterly), -1)

    def test_no_quarterly_data_returns_none(self):
        self.assertIsNone(gs.compute_progress_signal(self.ANNUAL, []))

    def test_insufficient_annual_returns_none(self):
        self.assertIsNone(gs.compute_progress_signal(self.ANNUAL[-1:], [["2026-06-30", 1, 1, 1, 1]]))


class TestRevisionSignal(unittest.TestCase):
    def test_upward_revision(self):
        discs = [{"code": "1301", "doc_type": "業績予想修正", "title": "業績予想の上方修正について",
                  "published_at": "2026-07-01T15:00:00"}]
        self.assertEqual(gs.compute_revision_signal(discs), 1)

    def test_downward_revision(self):
        discs = [{"code": "1301", "doc_type": "業績予想修正", "title": "業績予想の下方修正について",
                  "published_at": "2026-07-01T15:00:00"}]
        self.assertEqual(gs.compute_revision_signal(discs), -1)

    def test_no_revision_is_neutral(self):
        self.assertEqual(gs.compute_revision_signal([]), 0)

    def test_unclassifiable_direction_is_neutral(self):
        discs = [{"code": "1301", "doc_type": "業績予想修正", "title": "業績予想の修正について",
                  "published_at": "2026-07-01T15:00:00"}]
        self.assertEqual(gs.compute_revision_signal(discs), 0)


class TestScoreStock(unittest.TestCase):
    def test_all_positive_signals_score_100(self):
        entry = {
            "a": [["2025-03-31", 1000.0, 400.0, 300.0, 10.0], ["2026-03-31", 1200.0, 600.0, 400.0, 14.0]],
            "q": [],
        }
        discs = [{"code": "1301", "doc_type": "業績予想修正", "title": "上方修正のお知らせ",
                  "published_at": "2026-07-01T15:00:00"}]
        result = gs.score_stock(entry, discs, disclosures_available=True)
        self.assertFalse(result["insufficient_data"])
        self.assertEqual(result["score"], 100)
        self.assertEqual(len(result["breakdown"]), 4)

    def test_missing_financials_is_insufficient_data_not_zero(self):
        """フェイルラウド: 年次データが全く無い銘柄は黙って0点にせず insufficient_data=True, score=None。"""
        entry = {"a": [], "q": []}
        result = gs.score_stock(entry, [], disclosures_available=False)
        self.assertIsNone(result["score"])
        self.assertTrue(result["insufficient_data"])
        for b in result["breakdown"]:
            self.assertIsNone(b["signal"])

    def test_partial_signals_average_only_available_ones(self):
        # growth/marginは算出可能(+1/+1)、進捗率は四半期無しでNone、修正は開示取得不可でNone
        entry = {
            "a": [["2025-03-31", 100.0, 10.0, 8.0, 5.0], ["2026-03-31", 120.0, 20.0, 15.0, 9.0]],
            "q": [],
        }
        result = gs.score_stock(entry, [], disclosures_available=False)
        self.assertFalse(result["insufficient_data"])
        self.assertEqual(result["score"], 100)  # 算出できた2項目が両方+1
        signals = {b["key"]: b["signal"] for b in result["breakdown"]}
        self.assertIsNone(signals["progress"])
        self.assertIsNone(signals["revision"])


class TestGenerateScores(unittest.TestCase):
    def test_generates_entry_for_every_code_in_financials(self):
        financials = {"stocks": {
            "1301": {"a": [["2025-03-31", 100.0, 10.0, 8.0, 5.0], ["2026-03-31", 120.0, 20.0, 15.0, 9.0]], "q": []},
            "9999": {"a": [], "q": []},  # データ皆無の新興銘柄相当
        }}
        out = gs.generate_scores(financials, [], disclosures_available=True)
        self.assertEqual(set(out.keys()), {"1301", "9999"})
        self.assertFalse(out["1301"]["insufficient_data"])
        self.assertTrue(out["9999"]["insufficient_data"])
        self.assertIsNone(out["9999"]["score"])

    def test_disclosures_unavailable_makes_revision_signal_none_for_all(self):
        financials = {"stocks": {
            "1301": {"a": [["2025-03-31", 100.0, 10.0, 8.0, 5.0], ["2026-03-31", 120.0, 20.0, 15.0, 9.0]], "q": []},
        }}
        out = gs.generate_scores(financials, [{"code": "1301", "doc_type": "業績予想修正",
                                               "title": "上方修正", "published_at": "2026-07-01"}],
                                 disclosures_available=False)
        signals = {b["key"]: b["signal"] for b in out["1301"]["breakdown"]}
        self.assertIsNone(signals["revision"])


class TestMainWritesFileForAllCodes(unittest.TestCase):
    def test_main_generates_scores_json_covering_all_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            financials = {"stocks": {
                "1301": {"a": [["2025-03-31", 100.0, 10.0, 8.0, 5.0],
                               ["2026-03-31", 120.0, 20.0, 15.0, 9.0]], "q": [], "t": "2026-07-07"},
                "6506": {"a": [["2025-03-31", 100.0, 10.0, 8.0, 5.0],
                               ["2026-03-31", 90.0, 5.0, 3.0, 1.0]], "q": [], "t": "2026-07-07"},
            }}
            with open(os.path.join(tmp, "financials.json"), "w", encoding="utf-8") as f:
                json.dump(financials, f)
            with open(os.path.join(tmp, "disclosures.json"), "w", encoding="utf-8") as f:
                json.dump([], f)

            import sys
            old_argv = sys.argv
            try:
                sys.argv = ["generate_scores.py", "--data", tmp]
                gs.main()
            finally:
                sys.argv = old_argv

            out_path = os.path.join(tmp, "scores.json")
            self.assertTrue(os.path.exists(out_path))
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(set(data["scores"].keys()), {"1301", "6506"})
            self.assertIn("generated_at", data)


if __name__ == "__main__":
    unittest.main()
