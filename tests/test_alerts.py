"""scripts/generate_alerts.py のアラート判定ロジックのテスト。"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_streak_ignores_gaps(self):
        # 欠損日(None)を挟んだら連続とみなさない / 直近日が欠損なら現在の連続ではない
        tail = {"dates": ["d1", "d2", "d3", "d4", "d5"],
                "closes": {"1301": [500.0, 480.0, None, 460.0, 440.0],   # 途中に欠損
                           "1332": [500.0, 480.0, 460.0, 440.0, None]}}  # 直近が欠損
        self.assertEqual(ga.streak_of(tail, "1301"), (0, 0))
        self.assertEqual(ga.streak_of(tail, "1332"), (0, 0))
        # 欠損なしの3日続伸は検出される
        tail2 = {"dates": ["d1", "d2", "d3", "d4"],
                 "closes": {"7203": [100.0, 110.0, 120.0, 130.0]}}
        self.assertEqual(ga.streak_of(tail2, "7203"), (3, 1))

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

    def test_surprise_title_classification_upward_downward(self):
        discs = [
            {"code": "1301", "doc_type": "業績予想修正", "title": "通期業績予想の上方修正に関するお知らせ",
             "published_at": "2026-07-07T15:00:00"},
            {"code": "7203", "doc_type": "業績予想修正", "title": "業績予想の下方修正について",
             "published_at": "2026-07-07T15:00:00"},
        ]
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3},
                                       {"code": "7203", "importance": 3}], None,
                          "2026-07-07", disclosures=discs)
        types = {a["code"]: a["type"] for a in out if a["type"].startswith("disclosure_業績予想")}
        self.assertEqual(types["1301"], "disclosure_業績予想上方修正")
        self.assertEqual(types["7203"], "disclosure_業績予想下方修正")

    def test_surprise_title_classification_dividend(self):
        discs = [
            {"code": "1301", "doc_type": "配当予想修正", "title": "配当予想の修正(増配)に関するお知らせ",
             "published_at": "2026-07-07T15:00:00"},
        ]
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", disclosures=discs)
        types = [a["type"] for a in out if a["type"].startswith("disclosure_配当")]
        self.assertEqual(types, ["disclosure_配当増配"])

    def test_surprise_title_classification_fallback_generic(self):
        # 上方/下方/増配/減配のいずれのキーワードも無ければ従来どおり汎用種別のまま
        discs = [
            {"code": "1301", "doc_type": "業績予想修正", "title": "業績予想の修正に関するお知らせ",
             "published_at": "2026-07-07T15:00:00"},
        ]
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", disclosures=discs)
        types = [a["type"] for a in out if a["type"].startswith("disclosure_")]
        self.assertEqual(types, ["disclosure_業績予想修正"])

    def test_financial_surprise_turnaround_and_record_profit(self):
        financials = {"stocks": {"1301": {
            "a": [["2024-03-31", 100.0, -50.0, -60.0, -1.0],
                  ["2025-03-31", 110.0, 10.0, 5.0, 0.5],
                  ["2026-03-31", 130.0, 200.0, 150.0, 15.0]],
            "t": "2026-07-07",
        }}}
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", financials=financials)
        types = {a["type"] for a in out if a["type"].startswith("surprise_")}
        # 直近期(2026-03-31)は前期比黒字転換ではない(前期も黒字)が、過去最高益ではある
        self.assertIn("surprise_record_profit", types)
        self.assertNotIn("surprise_turnaround", types)

    def test_financial_surprise_only_when_updated_today(self):
        financials = {"stocks": {"1301": {
            "a": [["2025-03-31", 100.0, -10.0, -10.0, -1.0],
                  ["2026-03-31", 130.0, 20.0, 15.0, 1.5]],
            "t": "2026-07-06",  # 今日更新されていない
        }}}
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", financials=financials)
        self.assertEqual([a for a in out if a["type"].startswith("surprise_")], [])

    def test_financial_surprise_turnaround_only(self):
        financials = {"stocks": {"1301": {
            "a": [["2024-03-31", 100.0, 50.0, 40.0, 3.0],
                  ["2025-03-31", 90.0, -20.0, -25.0, -2.0],
                  ["2026-03-31", 95.0, 5.0, 3.0, 0.3]],
            "t": "2026-07-07",
        }}}
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", financials=financials)
        types = {a["type"] for a in out if a["type"].startswith("surprise_")}
        self.assertIn("surprise_turnaround", types)
        self.assertNotIn("surprise_record_profit", types)  # 過去(50.0)を超えていない

    # T-3: 決算サプライズ判定へのXBRL(決算短信サマリー)統合 + フォールバック -----

    XBRL_HIGH_OP_INCOME = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:tse-ed-t="http://www.xbrl.tdnet.info/jp/br/tdnet/t/ed/2013-08-31">
  <tse-ed-t:OperatingIncome contextRef="CurrentYearDuration_ConsolidatedMember" unitRef="JPY">999</tse-ed-t:OperatingIncome>
</xbrli:xbrl>"""

    def test_xbrl_confirmed_value_overrides_yahoo_for_surprise_detection(self):
        """XBRLから取得できた営業利益がYahoo由来値より優先されること。
        Yahoo由来 (5.0) では過去最高 (50.0) を更新しないが、XBRL確定値 (999) では
        更新する — override が実際に判定へ反映されることを確認する。"""
        financials = {"stocks": {"1301": {
            "a": [["2024-03-31", 100.0, 50.0, 40.0, 3.0],
                  ["2025-03-31", 90.0, -20.0, -25.0, -2.0],
                  ["2026-03-31", 95.0, 5.0, 3.0, 0.3]],
            "t": "2026-07-07",
        }}}
        lookup = lambda code: self.XBRL_HIGH_OP_INCOME if code == "1301" else None
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", financials=financials, xbrl_lookup=lookup)
        types = {a["type"] for a in out if a["type"].startswith("surprise_")}
        self.assertIn("surprise_record_profit", types)
        detail = next(a["detail"] for a in out if a["type"] == "surprise_record_profit")
        self.assertIn("999", detail)  # XBRL確定値が採用されている

    def test_xbrl_parse_failure_falls_back_to_yahoo_and_logs(self):
        """不正なXBRL(パース不能)を返す xbrl_lookup を渡しても例外にならず、
        Yahoo由来値にフォールバックし、その旨がログに出ること(黙殺しない)。"""
        financials = {"stocks": {"1301": {
            "a": [["2024-03-31", 100.0, 50.0, 40.0, 3.0],
                  ["2025-03-31", 90.0, -20.0, -25.0, -2.0],
                  ["2026-03-31", 95.0, 5.0, 3.0, 0.3]],
            "t": "2026-07-07",
        }}}
        lookup = lambda code: b"<not-well-formed"
        logs = []
        orig_log = ga.log
        ga.log = lambda msg: logs.append(msg)
        try:
            out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                              "2026-07-07", financials=financials, xbrl_lookup=lookup)
        finally:
            ga.log = orig_log
        types = {a["type"] for a in out if a["type"].startswith("surprise_")}
        # Yahoo由来 (5.0) は過去最高 (50.0) を更新しないのでrecord_profitは出ない
        self.assertNotIn("surprise_record_profit", types)
        self.assertIn("surprise_turnaround", types)  # Yahoo由来値でのフォールバック判定は継続する
        self.assertTrue(any("XBRL解析失敗" in m and "フォールバック" in m for m in logs))

    def test_xbrl_lookup_exception_falls_back_to_yahoo_and_logs(self):
        """xbrl_lookup が例外を送出しても generate() 全体は落ちず、Yahoo由来値へ
        フォールバックしてログに残ること。"""
        financials = {"stocks": {"1301": {
            "a": [["2025-03-31", 100.0, -10.0, -10.0, -1.0],
                  ["2026-03-31", 130.0, 20.0, 15.0, 1.5]],
            "t": "2026-07-07",
        }}}

        def boom(code):
            raise ConnectionError("TDnetに接続できません")

        logs = []
        orig_log = ga.log
        ga.log = lambda msg: logs.append(msg)
        try:
            out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                              "2026-07-07", financials=financials, xbrl_lookup=boom)
        finally:
            ga.log = orig_log
        types = {a["type"] for a in out if a["type"].startswith("surprise_")}
        self.assertIn("surprise_turnaround", types)  # Yahoo由来値でのフォールバック判定は継続する
        self.assertTrue(any("XBRL取得失敗" in m and "フォールバック" in m for m in logs))

    def test_xbrl_lookup_returns_none_falls_back_silently(self):
        """該当銘柄のXBRLが無い (None) 場合も例外にならず、Yahoo由来値で
        通常どおり判定されること。"""
        financials = {"stocks": {"1301": {
            "a": [["2025-03-31", 100.0, -10.0, -10.0, -1.0],
                  ["2026-03-31", 130.0, 20.0, 15.0, 1.5]],
            "t": "2026-07-07",
        }}}
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", financials=financials, xbrl_lookup=lambda code: None)
        types = {a["type"] for a in out if a["type"].startswith("surprise_")}
        self.assertIn("surprise_turnaround", types)

    def test_xbrl_lookup_not_provided_matches_prior_behavior(self):
        """xbrl_lookup 省略時 (既定 None) は従来どおり Yahoo由来値のみで判定される
        (後方互換の確認)。"""
        financials = {"stocks": {"1301": {
            "a": [["2024-03-31", 100.0, -50.0, -60.0, -1.0],
                  ["2025-03-31", 110.0, 10.0, 5.0, 0.5],
                  ["2026-03-31", 130.0, 200.0, 150.0, 15.0]],
            "t": "2026-07-07",
        }}}
        out = ga.generate(PRICES, [], [{"code": "1301", "importance": 3}], None,
                          "2026-07-07", financials=financials)
        types = {a["type"] for a in out if a["type"].startswith("surprise_")}
        self.assertIn("surprise_record_profit", types)
        self.assertNotIn("surprise_turnaround", types)

    def test_should_include_price_alerts_gate(self):
        # 15時より前は株価系アラート不可 (force無し)
        morning = datetime.datetime(2026, 7, 7, 9, 5, tzinfo=ga.JST)
        self.assertFalse(ga.should_include_price_alerts(morning, False))
        # 15時ちょうど以降は可
        after = datetime.datetime(2026, 7, 7, 15, 35, tzinfo=ga.JST)
        self.assertTrue(ga.should_include_price_alerts(after, False))
        # --force なら午前でも可
        self.assertTrue(ga.should_include_price_alerts(morning, True))

    def test_morning_run_generates_earnings_only(self):
        """午前 (prices=None) の呼び出しでも決算(earnings)通知は生成され、
        株価系・開示系・サプライズ系アラートは出ないこと。"""
        my = [{"code": "1301", "importance": 5}, {"code": "7203", "importance": 5}]
        settings = {"levels": {"5": all_on()}}
        out = ga.generate(None, SCHEDULE, my, settings, "2026-07-07")
        types = {a["type"] for a in out}
        self.assertEqual(types, {"earnings"})
        earn = {a["code"]: a["title"] for a in out}
        self.assertIn("本日", earn["1301"])
        self.assertIn("明日", earn["7203"])

    def test_earnings_alert_idempotent_morning_then_afterhours(self):
        """当日朝の実行で生成された earnings 通知が、同日の場後(15時以降)の
        再実行で二重に登録されないこと (main() の alert_key ベース dedupe を再現)。"""
        my = [{"code": "1301", "importance": 5}, {"code": "7203", "importance": 5}]
        settings = {"levels": {"5": all_on()}}

        # 朝の実行 (prices=None)
        morning_alerts = ga.generate(None, SCHEDULE, my, settings, "2026-07-07")
        self.assertTrue(morning_alerts)
        known = {ga.alert_key(a) for a in morning_alerts}

        # 場後 (15時以降) の実行: 同じ earnings に加え株価系アラートも評価される
        afterhours_alerts = ga.generate(PRICES, SCHEDULE, my, settings, "2026-07-07")
        new_only = [a for a in afterhours_alerts if ga.alert_key(a) not in known]
        # 朝と同じ earnings は再通知されない
        self.assertFalse(any(a["type"] == "earnings" for a in new_only))
        # 朝には無かった株価系アラートは新規として残る
        self.assertTrue(any(a["type"] == "price_move" for a in new_only))

    def test_surprise_alerts_idempotent_across_runs(self):
        """同一開示・同一財務更新を複数回のパイプライン実行で処理しても
        二重アラートにならないこと (main() の alert_key ベース dedupe を再現)。"""
        discs = [{"code": "1301", "doc_type": "業績予想修正",
                  "title": "通期業績予想の上方修正に関するお知らせ",
                  "published_at": "2026-07-07T15:00:00"}]
        financials = {"stocks": {"1301": {
            "a": [["2025-03-31", 100.0, -10.0, -10.0, -1.0],
                  ["2026-03-31", 130.0, 20.0, 15.0, 1.5]],
            "t": "2026-07-07",
        }}}
        my = [{"code": "1301", "importance": 3}]
        first_run = ga.generate(PRICES, [], my, None, "2026-07-07",
                                disclosures=discs, financials=financials)
        known = {ga.alert_key(a) for a in first_run}
        self.assertTrue(known)  # 何かしらアラートが出ていることを前提とする
        second_run = ga.generate(PRICES, [], my, None, "2026-07-07",
                                 disclosures=discs, financials=financials)
        new_only = [a for a in second_run if ga.alert_key(a) not in known]
        self.assertEqual(new_only, [])


class TestMainXbrlWiring(unittest.TestCase):
    """main() が xbrl_fetch.make_lookup() で構築した xbrl_lookup を generate() に
    実際に配線していること (W2-1d: 本番配線)。xbrl_fetch 自体はモックし、実
    ネットワークアクセスはしない。"""

    def test_xbrl_lookup_from_disclosures_overrides_yahoo_value_in_alerts_json(self):
        today = datetime.datetime.now(ga.JST).strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "frontend", "data")
            os.makedirs(data_dir)
            with open(os.path.join(data_dir, "schedule.json"), "w", encoding="utf-8") as f:
                json.dump([], f)
            with open(os.path.join(data_dir, "stocks.json"), "w", encoding="utf-8") as f:
                json.dump([{"code": "1301", "name": "極洋"}], f)
            with open(os.path.join(data_dir, "prices.json"), "w", encoding="utf-8") as f:
                json.dump({"date": today, "stocks": {"1301": [4000.0, 0.5, 5000.0, 3000.0, 50_000, 60_000]}}, f)
            with open(os.path.join(data_dir, "disclosures.json"), "w", encoding="utf-8") as f:
                json.dump([{"key": "K1", "code": "1301", "doc_type": "決算短信",
                            "title": "決算短信のお知らせ", "xbrl_url": "https://example/1.zip",
                            "published_at": f"{today}T15:00:00"}], f)
            with open(os.path.join(data_dir, "financials.json"), "w", encoding="utf-8") as f:
                json.dump({"stocks": {"1301": {
                    "a": [["2024-03-31", 100.0, 50.0, 40.0, 3.0],
                          ["2025-03-31", 90.0, -20.0, -25.0, -2.0],
                          ["2026-03-31", 95.0, 5.0, 3.0, 0.3]],
                    "t": today}}}, f)
            config_dir = os.path.join(tmp, "config")
            os.makedirs(config_dir)
            user_path = os.path.join(config_dir, "user_data.json")
            with open(user_path, "w", encoding="utf-8") as f:
                json.dump({"data": {"kessan_local_v1": json.dumps(
                    {"mystocks": [{"code": "1301", "importance": 3}]})}}, f)

            # xbrl_fetch.make_lookup が返す lookup を差し替え、渡された disclosures/today
            # が本物であること、かつ generate() が実際にその lookup を使うことを検証する。
            captured = {}

            def fake_make_lookup(disclosures, d_today, cache_dir=None):
                captured["disclosures"] = disclosures
                captured["today"] = d_today
                return lambda code: TestGenerate.XBRL_HIGH_OP_INCOME if code == "1301" else None

            with mock.patch.object(ga.xbrl_fetch, "make_lookup", side_effect=fake_make_lookup), \
                 mock.patch.object(sys, "argv",
                                    ["generate_alerts.py", "--data", data_dir, "--user", user_path,
                                     "--force"]):
                ga.main()

            self.assertEqual(captured["today"], today)
            self.assertTrue(any(d.get("code") == "1301" for d in captured["disclosures"]))

            with open(os.path.join(data_dir, "alerts.json"), encoding="utf-8") as f:
                alerts = json.load(f)["alerts"]
            record_profit = [a for a in alerts if a["type"] == "surprise_record_profit"]
            self.assertTrue(record_profit)
            # XBRL確定値 (999) がYahoo由来値 (5.0) より優先されていることを確認する
            self.assertIn("999", record_profit[0]["detail"])


class TestLoadJsonFailLoud(unittest.TestCase):
    """alerts.json 等の冪等性台帳が壊れている場合に黙殺せず例外送出すること
    (未存在時は従来どおり fallback を返す)。"""

    def test_missing_file_returns_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "alerts.json")
            self.assertEqual(ga.load_json(path, {"alerts": []}), {"alerts": []})

    def test_corrupt_existing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "alerts.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not valid json")
            with self.assertRaises(RuntimeError):
                ga.load_json(path, {"alerts": []})

    def test_valid_existing_file_parses_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "alerts.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"alerts": [{"code": "1301"}]}')
            self.assertEqual(ga.load_json(path, {}), {"alerts": [{"code": "1301"}]})


if __name__ == "__main__":
    unittest.main()
