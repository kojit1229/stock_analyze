"""scripts/market_summary.py の市況統計・MD生成のテスト。"""
import importlib.util
import json
import os
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location(
    "market_summary",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "market_summary.py"))
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

STOCKS = [
    {"code": "7203", "name": "トヨタ", "market": "プライム", "sector": "輸送用機器", "market_cap": 40e12},
    {"code": "1301", "name": "極洋", "market": "プライム", "sector": "水産・農林業", "market_cap": 50e9},
    {"code": "1332", "name": "ニッスイ", "market": "プライム", "sector": "水産・農林業", "market_cap": 400e9},
    {"code": "4385", "name": "メルカリ", "market": "グロース", "sector": "情報・通信業", "market_cap": 300e9},
    {"code": "9999", "name": "データ無し", "market": "スタンダード", "sector": "小売業", "market_cap": 10e9},
]
PRICES = {"date": "2026-07-07", "stocks": {
    # [close, chg%, hi52, lo52, vol, avgVol]
    "7203": [3000.0, 2.0, 3000.0, 2000.0, 20_000_000, 25_000_000],
    "1301": [4000.0, 10.0, 5000.0, 3000.0, 100_000, 10_000],   # 出来高10倍
    "1332": [900.0, -6.0, 2000.0, 900.0, 300_000, 100_000],    # 52週安値
    "4385": [2000.0, -1.0, 4000.0, 1500.0, 1_000_000, 1_000_000],
}}
REACTIONS = {"tail": {"dates": ["2026-07-03", "2026-07-04", "2026-07-07"],
                      "closes": {"7203": [2900.0, 2950.0, 3000.0],
                                 "1301": [3600.0, 3640.0, 4000.0],
                                 "1332": [1000.0, 960.0, 900.0],
                                 "4385": [2050.0, 2020.0, 2000.0]}}}
SCHEDULE = [{"code": "1301", "announce_date": "2026-07-08", "fiscal_type": "Q1"},
            {"code": "1332", "announce_date": "2026-07-10", "fiscal_type": "Q1"}]
DISCS = [{"code": "7203", "doc_type": "業績予想修正", "published_at": "2026-07-07T15:00:00"},
         {"code": "1301", "doc_type": "決算短信", "published_at": "2026-07-06T15:00:00"}]  # 前日分は対象外


def stats():
    return ms.compute_stats(STOCKS, PRICES, REACTIONS, SCHEDULE, DISCS, "2026-07-07")


class TestCompute(unittest.TestCase):
    def test_summary_counts(self):
        s = stats()["summary"]
        self.assertEqual(s["total"], 4)
        self.assertEqual((s["up"], s["down"], s["flat"]), (2, 2, 0))
        self.assertEqual(s["hi52_count"], 1)   # 7203
        self.assertEqual(s["lo52_count"], 1)   # 1332
        self.assertEqual(s["surge"], 1)        # 1301 +10%
        self.assertEqual(s["sectors_up"] + s["sectors_down"], 3)

    def test_weighted_avg_dominated_by_large_cap(self):
        s = stats()["summary"]
        # トヨタ(40兆・+2%)が支配的 → 加重平均は+2%付近
        self.assertGreater(s["wavg"], 1.5)
        self.assertLess(s["wavg"], 2.1)

    def test_updown_ratio(self):
        # 2日分: 上昇 7203×2 + 1301×2 = 4 / 下落 1332×2 + 4385×2 = 4 → 100%
        ratio, days = ms.updown_ratio(REACTIONS["tail"])
        self.assertEqual(days, 2)
        self.assertAlmostEqual(ratio, 100.0)

    def test_sector_weighted(self):
        st = stats()
        fish = next(x for x in st["sectors"] if x["name"] == "水産・農林業")
        # 加重: (50e9*10 + 400e9*(-6)) / 450e9 = -4.22%
        self.assertAlmostEqual(fish["chg"], (50e9 * 10 - 400e9 * 6) / 450e9, places=2)
        self.assertEqual(fish["n"], 2)

    def test_rankings_and_events(self):
        st = stats()
        self.assertEqual(st["rank_value"][0]["code"], "7203")  # 売買代金最大
        self.assertEqual(st["gainers"][0]["code"], "1301")
        self.assertEqual(st["losers"][0]["code"], "1332")
        self.assertEqual([r["code"] for r in st["vol_spike"]], ["1301"])
        self.assertEqual(st["impact_pos"][0]["code"], "7203")
        self.assertEqual(st["disclosures_today"], {"業績予想修正": 1})
        self.assertEqual(dict(st["earnings_week"]), {"2026-07-08": 1, "2026-07-10": 1})

    def test_comment_mentions_key_facts(self):
        st = stats()
        self.assertIn("騰落レシオ", st["comment"])
        self.assertIn("52週高値更新1件", st["comment"])
        self.assertIn("業績予想修正1件", st["comment"])

    def test_markdown_structure(self):
        md = ms.to_markdown(stats())
        for sec in ["# 市況概況 2026-07-07", "## 概況コメント", "## サマリ",
                    "## セクター別騰落", "## 売買代金ランキング", "## 値上がり率ランキング",
                    "## 52週高値更新", "## 指数インパクト", "## 本日の開示件数",
                    "## 今週の決算発表予定"]:
            self.assertIn(sec, md)
        self.assertIn("| 7203 | トヨタ |", md)
        self.assertIn("騰落レシオ(2日): 100%", md)

    def test_oku_format(self):
        self.assertEqual(ms.oku(1.99e12), "1.99兆円")
        self.assertEqual(ms.oku(345e8), "345億円")
        self.assertEqual(ms.oku(None), "-")

    def test_minus_100pct_does_not_crash(self):
        # 前日比-100% (ゼロ除算になる値) でもスナップショット計算が落ちない
        prices = {"date": "2026-07-07", "stocks": {
            "7203": [0.0, -100.0, 3000.0, 0.0, 1000, 500],
            "1301": [4000.0, 1.0, 5000.0, 3000.0, 100, 90],
        }}
        st = ms.compute_stats(STOCKS, prices, {}, [], [], "2026-07-07")
        self.assertEqual(st["summary"]["total"], 2)
        codes = [r["code"] for r in st["impact_neg"]]
        self.assertNotIn("7203", codes)  # impact は None として除外される


class TestMainOutputs(unittest.TestCase):
    def test_files_written_and_index_accumulates(self):
        with tempfile.TemporaryDirectory() as d:
            for name, obj in [("prices.json", PRICES), ("stocks.json", STOCKS),
                              ("reactions.json", REACTIONS), ("schedule.json", SCHEDULE),
                              ("disclosures.json", DISCS)]:
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            import sys
            argv = sys.argv
            sys.argv = ["market_summary.py", "--data", d, "--force"]
            try:
                ms.main()
                ms.main()  # 同日再実行 → 上書き・indexは1件のまま
            finally:
                sys.argv = argv
            mdir = os.path.join(d, "market")
            self.assertTrue(os.path.exists(os.path.join(mdir, "2026-07-07.json")))
            self.assertTrue(os.path.exists(os.path.join(mdir, "2026-07-07.md")))
            with open(os.path.join(mdir, "index.json"), encoding="utf-8") as f:
                idx = json.load(f)
            self.assertEqual(idx["dates"], ["2026-07-07"])
            self.assertEqual(len(idx["series"]), 1)
            row = idx["series"][0]
            self.assertEqual(row[0], "2026-07-07")
            self.assertEqual(row[1], 2)  # up
            self.assertEqual(row[2], 2)  # down
            with open(os.path.join(mdir, "2026-07-07.json"), encoding="utf-8") as f:
                snap = json.load(f)
            self.assertIn("comment", snap)
            self.assertEqual(snap["summary"]["total"], 4)


if __name__ == "__main__":
    unittest.main()
