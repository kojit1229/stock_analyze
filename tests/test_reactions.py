"""scripts/track_reactions.py のイベント反応記録ロジックのテスト。"""
import importlib.util
import os
import unittest

_spec = importlib.util.spec_from_file_location(
    "track_reactions",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "track_reactions.py"))
tr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tr)


def make_tail():
    # 3営業日: 7/3(木), 7/4(金), 7/7(月)
    return {"dates": ["2026-07-03", "2026-07-04", "2026-07-07"],
            "closes": {"7203": [1000.0, 1100.0, 1210.0],
                       "1301": [500.0, 450.0, 400.0]}}


class TestTail(unittest.TestCase):
    def test_append_and_trim(self):
        import datetime
        tail = {"dates": [], "closes": {}}
        n = tr.TAIL_DAYS + 4
        base = datetime.date(2026, 5, 1)
        for i in range(n):
            d = (base + datetime.timedelta(days=i)).isoformat()
            tr.append_tail(tail, {"date": d, "stocks": {"7203": [100.0 + i, 0, None, None, None, None]}})
        self.assertEqual(len(tail["dates"]), tr.TAIL_DAYS)
        self.assertEqual(tail["dates"][-1], (base + datetime.timedelta(days=n - 1)).isoformat())
        self.assertEqual(len(tail["closes"]["7203"]), tr.TAIL_DAYS)
        self.assertEqual(tail["closes"]["7203"][-1], 100.0 + n - 1)

    def test_same_day_overwrite(self):
        tail = {"dates": [], "closes": {}}
        tr.append_tail(tail, {"date": "2026-07-07", "stocks": {"7203": [100.0, 0]}})
        tr.append_tail(tail, {"date": "2026-07-07", "stocks": {"7203": [105.0, 0]}})
        self.assertEqual(tail["dates"], ["2026-07-07"])
        self.assertEqual(tail["closes"]["7203"], [105.0])

    def test_new_code_backfills_none(self):
        tail = {"dates": [], "closes": {}}
        tr.append_tail(tail, {"date": "2026-07-04", "stocks": {"7203": [100.0, 0]}})
        tr.append_tail(tail, {"date": "2026-07-07", "stocks": {"7203": [101.0, 0], "9999": [50.0, 0]}})
        self.assertEqual(tail["closes"]["9999"], [None, 50.0])


class TestReactionDate(unittest.TestCase):
    def test_before_15_same_day(self):
        tail = make_tail()
        self.assertEqual(tr.reaction_date(tail, "2026-07-04T11:30:00"), "2026-07-04")

    def test_after_15_next_trading_day(self):
        tail = make_tail()
        # 金曜15:30公表 → 反応日は月曜
        self.assertEqual(tr.reaction_date(tail, "2026-07-04T15:30:00"), "2026-07-07")

    def test_after_15_no_next_day_yet(self):
        tail = make_tail()
        # 月曜15:30公表 → 翌営業日データがまだ無い → None (持ち越し)
        self.assertIsNone(tr.reaction_date(tail, "2026-07-07T15:30:00"))


class TestEventsAndFill(unittest.TestCase):
    def test_disclosure_event_and_reactions(self):
        tail = make_tail()
        prices = {"date": "2026-07-07", "stocks": {}}
        discs = [{"code": "7203", "doc_type": "決算短信", "title": "2026年3月期 決算短信",
                  "published_at": "2026-07-03T15:30:00"}]  # 木曜引け後 → 反応日は金曜
        seen = set()
        events = tr.make_events(tail, prices, discs, seen)
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev["d"], "2026-07-04")
        tr.fill_reactions(events, tail)
        self.assertAlmostEqual(ev["c1"], 10.0)   # 1000→1100
        self.assertAlmostEqual(ev["c2"], 10.0)   # 1100→1210
        self.assertEqual(ev["nd"], "2026-07-07")
        # 再実行しても重複しない
        events2 = tr.make_events(tail, prices, discs, seen)
        self.assertEqual(events2, [])

    def test_price_events(self):
        tail = make_tail()
        prices = {"date": "2026-07-07", "stocks": {
            "7203": [1210.0, 10.0, 1300.0, 900.0, 1000, 500],   # ±8%以上 → 急変動
            "1301": [400.0, -11.1, 800.0, 400.0, 100, 90],      # 52週安値だが急変動が優先
            "6506": [500.0, 1.0, 500.0, 300.0, 100, 90],        # 52週高値
            "9972": [300.0, 0.5, 900.0, 100.0, 1000, 100],      # 出来高10倍
        }}
        events = tr.make_events(tail, prices, [], set())
        types = {(e["code"], e["t"]) for e in events}
        self.assertIn(("7203", "急変動"), types)
        self.assertIn(("1301", "急変動"), types)
        self.assertIn(("6506", "52週高値"), types)
        self.assertIn(("9972", "出来高急増"), types)

    def test_fill_waits_for_next_day(self):
        tail = make_tail()
        events = [{"d": "2026-07-07", "code": "7203", "t": "急変動", "l": "",
                   "c1": None, "c2": None, "nd": None}]
        tr.fill_reactions(events, tail)
        self.assertAlmostEqual(events[0]["c1"], 10.0)  # 1100→1210
        self.assertIsNone(events[0]["c2"])             # 翌営業日はまだ無い
        # 翌営業日のデータが来たら c2 が埋まる
        tr.append_tail(tail, {"date": "2026-07-08", "stocks": {"7203": [1149.5, 0]}})
        tr.fill_reactions(events, tail)
        self.assertAlmostEqual(events[0]["c2"], -5.0)
        self.assertEqual(events[0]["nd"], "2026-07-08")


if __name__ == "__main__":
    unittest.main()
