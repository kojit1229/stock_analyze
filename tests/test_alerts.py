"""scripts/generate_alerts.py のアラート判定ロジックのテスト。"""
import importlib.util
import os
import sys
import unittest

_spec = importlib.util.spec_from_file_location(
    "generate_alerts",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_alerts.py"))
ga = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ga)


PRICES = {
    "date": "2026-07-07",
    "stocks": {
        # [終値, 前日比%, 52週高値, 52週安値, 出来高, 平均出来高]
        "7203": [3500.0, +4.2, 3600.0, 2200.0, 30_000_000, 25_000_000],
        "6506": [8000.0, -1.0, 8000.0, 4000.0, 9_000_000, 3_000_000],
        "9972": [900.0, -6.5, 2000.0, 900.0, 100_000, 80_000],
        "1301": [4000.0, +0.5, 5000.0, 3000.0, 50_000, 60_000],
    },
}
SCHEDULE = [
    {"code": "1301", "announce_date": "2026-07-07", "fiscal_type": "第1四半期"},
    {"code": "7203", "announce_date": "2026-07-08", "fiscal_type": "第1四半期"},
    {"code": "6506", "announce_date": "2026-08-01", "fiscal_type": "第1四半期"},
]


def all_on(pct=3, vol_x=2):
    return {"price_move": 1, "pct": pct, "wk52": 1, "volume": 1, "vol_x": vol_x, "earnings": 1}


class TestGenerate(unittest.TestCase):
    def test_price_move_threshold(self):
        settings = {"levels": {"5": all_on(pct=3)}}
        out = ga.generate(PRICES, [], [{"code": "7203", "importance": 5}], settings, "2026-07-07")
        self.assertTrue(any(a["type"] == "price_move" for a in out))
        # 閾値を5%に上げると出ない
        settings = {"levels": {"5": all_on(pct=5)}}
        out = ga.generate(PRICES, [], [{"code": "7203", "importance": 5}], settings, "2026-07-07")
        self.assertFalse(any(a["type"] == "price_move" for a in out))

    def test_wk52_high_and_low(self):
        settings = {"levels": {"5": all_on()}}
        out = ga.generate(PRICES, [], [{"code": "6506", "importance": 5},
                                       {"code": "9972", "importance": 5}], settings, "2026-07-07")
        types = {(a["code"], a["type"]) for a in out}
        self.assertIn(("6506", "wk52_high"), types)
        self.assertIn(("9972", "wk52_low"), types)

    def test_volume_spike(self):
        settings = {"levels": {"5": all_on(vol_x=2)}}
        out = ga.generate(PRICES, [], [{"code": "6506", "importance": 5}], settings, "2026-07-07")
        self.assertTrue(any(a["type"] == "volume" for a in out))
        # 1301 は平均以下なので出ない
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 5}], settings, "2026-07-07")
        self.assertFalse(any(a["type"] == "volume" for a in out))

    def test_earnings_today_and_tomorrow(self):
        settings = {"levels": {"5": all_on()}}
        my = [{"code": "1301", "importance": 5}, {"code": "7203", "importance": 5},
              {"code": "6506", "importance": 5}]
        out = ga.generate(PRICES, SCHEDULE, my, settings, "2026-07-07")
        earn = {a["code"]: a["title"] for a in out if a["type"] == "earnings"}
        self.assertIn("1301", earn)
        self.assertIn("本日", earn["1301"])
        self.assertIn("7203", earn)
        self.assertIn("明日", earn["7203"])
        self.assertNotIn("6506", earn)  # 8/1 は対象外

    def test_importance_levels_respected(self):
        # 重要度2は既定で price_move 無効 / earnings のみ
        out = ga.generate(PRICES, SCHEDULE, [{"code": "9972", "importance": 2}],
                          None, "2026-07-07")
        self.assertFalse(any(a["type"] == "price_move" for a in out))
        # 重要度5は既定で price_move 有効 (±3%)
        out = ga.generate(PRICES, SCHEDULE, [{"code": "9972", "importance": 5}],
                          None, "2026-07-07")
        self.assertTrue(any(a["type"] == "price_move" for a in out))

    def test_disabled_all(self):
        settings = {"levels": {str(i): {"price_move": 0, "wk52": 0, "volume": 0, "earnings": 0}
                               for i in range(1, 6)}}
        out = ga.generate(PRICES, SCHEDULE,
                          [{"code": c, "importance": 5} for c in PRICES["stocks"]],
                          settings, "2026-07-07")
        self.assertEqual(out, [])

    def test_missing_price_row_no_crash(self):
        out = ga.generate(PRICES, [], [{"code": "0000", "importance": 5}], None, "2026-07-07")
        self.assertEqual(out, [])

    def test_parse_user_data(self):
        import json
        payload = {"app": "kessan-navi", "data": {
            "kessan_local_v1": json.dumps({"mystocks": [
                {"code": "7203", "importance": 5, "memo": "x"},
                {"code": "1301", "importance": 2},
            ]}),
            "kessan_settings_v1": json.dumps({"alerts": {"email": False, "levels": {}}}),
        }}
        my, settings = ga.parse_user_data(payload)
        self.assertEqual([m["code"] for m in my], ["7203", "1301"])
        self.assertEqual(my[0]["importance"], 5)
        self.assertEqual(settings["email"], False)

    def test_alert_key_dedupe(self):
        a = {"date": "2026-07-07", "code": "7203", "type": "price_move"}
        b = dict(a, title="違うタイトル")
        self.assertEqual(ga.alert_key(a), ga.alert_key(b))

    def test_disclosure_alert(self):
        discs = [
            {"code": "1301", "doc_type": "業績予想修正", "title": "業績予想の修正に関するお知らせ",
             "published_at": "2026-07-07T15:00:00"},
            {"code": "1301", "doc_type": "自己株式取得", "title": "自己株式の取得に係る事項の決定",
             "published_at": "2026-07-07T15:00:00"},
            {"code": "1301", "doc_type": "決算説明資料", "title": "説明資料",  # 対象外
             "published_at": "2026-07-07T15:00:00"},
            {"code": "1301", "doc_type": "業績予想修正", "title": "昨日の修正",  # 日付違い
             "published_at": "2026-07-06T15:00:00"},
        ]
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", disclosures=discs)
        types = sorted(a["type"] for a in out if a["type"].startswith("disclosure"))
        self.assertEqual(types, ["disclosure_業績予想修正", "disclosure_自己株式取得"])
        # 重要度1は既定で開示アラート無効
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 1}], None,
                          "2026-07-07", disclosures=discs)
        self.assertEqual([a for a in out if a["type"].startswith("disclosure")], [])

    def test_streak_alert(self):
        reactions = {"tail": {"dates": ["d1", "d2", "d3", "d4"],
                              "closes": {"1301": [500.0, 480.0, 460.0, 440.0],
                                         "7203": [100.0, 110.0, 105.0, 120.0]}},
                     "events": []}
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 5},
                                       {"code": "7203", "importance": 5}], None,
                          "2026-07-07", reactions=reactions)
        st = {a["code"]: a for a in out if a["type"].startswith("streak")}
        self.assertIn("1301", st)
        self.assertEqual(st["1301"]["type"], "streak_down")
        self.assertIn("3日続落", st["1301"]["title"])
        self.assertNotIn("7203", st)  # 連続していない

    def test_reaction_alert(self):
        reactions = {"tail": {"dates": [], "closes": {}}, "events": [
            {"d": "2026-07-07", "code": "1301", "t": "決算短信", "l": "", "c1": -8.2, "c2": None, "nd": None},
            {"d": "2026-07-04", "code": "7203", "t": "決算短信", "l": "", "c1": 1.0, "c2": 6.3, "nd": "2026-07-07"},
            {"d": "2026-07-04", "code": "6506", "t": "急変動", "l": "", "c1": 9.0, "c2": 9.9, "nd": "2026-07-07"},
        ]}
        my = [{"code": c, "importance": 5} for c in ("1301", "7203", "6506")]
        out = ga.generate({"date": "2026-07-07", "stocks": {}}, [], my, None,
                          "2026-07-07", reactions=reactions)
        r = {a["code"]: a["title"] for a in out if a["type"] == "reaction"}
        self.assertIn("決算反応 -8.2%", r.get("1301", ""))
        self.assertIn("決算翌日 +6.3%", r.get("7203", ""))
        self.assertNotIn("6506", r)  # 急変動イベントは決算反応の対象外


if __name__ == "__main__":
    unittest.main()
