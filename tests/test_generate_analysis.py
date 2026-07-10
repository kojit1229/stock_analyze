"""scripts/generate_analysis.py のテスト。Claude API は呼ばず call_fn を差し替えてモックする。"""
import datetime
import importlib.util
import json
import os
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location(
    "generate_analysis",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "generate_analysis.py"))
gan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gan)

JST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime(2026, 7, 10, 9, 0, tzinfo=JST)
ANNUAL = [["2025-03-31", 1000.0, 400.0, 300.0, 10.0], ["2026-03-31", 1200.0, 500.0, 380.0, 12.0]]


def _setup_data_dir(tmp, codes=("8125",)):
    data_dir = os.path.join(tmp, "frontend", "data")
    os.makedirs(os.path.join(data_dir, "market"), exist_ok=True)
    with open(os.path.join(data_dir, "financials.json"), "w", encoding="utf-8") as f:
        json.dump({"stocks": {c: {"a": ANNUAL, "q": []} for c in codes}}, f)
    discs = [{"key": f"K{c}", "code": c, "title": "決算短信のお知らせ", "doc_type": "決算短信",
              "published_at": "2026-07-09T16:00:00", "pdf_url": "https://example/dummy.pdf"} for c in codes]
    with open(os.path.join(data_dir, "disclosures.json"), "w", encoding="utf-8") as f:
        json.dump(discs, f)
    with open(os.path.join(data_dir, "market", "2026-07-09.md"), "w", encoding="utf-8") as f:
        f.write("# 市況概況\n値上がり優勢。\n")
    with open(os.path.join(data_dir, "market", "index.json"), "w", encoding="utf-8") as f:
        json.dump({"dates": ["2026-07-09"]}, f)
    with open(os.path.join(data_dir, "stocks.json"), "w", encoding="utf-8") as f:
        json.dump([{"code": c, "name": f"テスト{c}"} for c in codes], f)
    return data_dir


def _setup_config(tmp, watchlist_codes=(), mystock_codes=()):
    config_dir = os.path.join(tmp, "config")
    os.makedirs(config_dir, exist_ok=True)
    wl_path = os.path.join(config_dir, "pdf_watchlist.json")
    with open(wl_path, "w", encoding="utf-8") as f:
        json.dump({"codes": list(watchlist_codes)}, f)
    user_path = os.path.join(config_dir, "user_data.json")
    if mystock_codes:
        local = json.dumps({"mystocks": [{"code": c, "importance": 3} for c in mystock_codes]})
        with open(user_path, "w", encoding="utf-8") as f:
            json.dump({"data": {"kessan_local_v1": local}}, f)
    return wl_path, user_path


class TestTargetCodes(unittest.TestCase):
    def test_unions_watchlist_and_mystocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            wl_path, user_path = _setup_config(tmp, watchlist_codes=["6506"], mystock_codes=["8125"])
            self.assertEqual(gan.target_codes(wl_path, user_path), {"6506", "8125"})

    def test_missing_files_returns_empty_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                gan.target_codes(os.path.join(tmp, "nope.json"), os.path.join(tmp, "nope2.json")), set())


class TestRun(unittest.TestCase):
    def test_generates_saves_and_dedupes_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = _setup_data_dir(tmp)
            wl_path, user_path = _setup_config(tmp, watchlist_codes=["8125"])
            calls = []

            def fake_call(material, api_key):
                calls.append(material["code"])
                return "分析結果テキスト"

            saved = gan.run(data_dir, wl_path, user_path, "dummy-key", NOW, call_fn=fake_call)
            self.assertEqual(len(saved), 1)
            self.assertEqual(calls, ["8125"])
            out_path = os.path.join(data_dir, "analysis", "8125_K8125.md")
            self.assertTrue(os.path.exists(out_path))
            with open(out_path, encoding="utf-8") as f:
                self.assertIn("分析結果テキスト", f.read())

            # 同一disclosure_idの再実行では処理済み(seen.json)により何もしない(冪等)
            saved2 = gan.run(data_dir, wl_path, user_path, "dummy-key", NOW, call_fn=fake_call)
            self.assertEqual(saved2, [])
            self.assertEqual(len(calls), 1)

    def test_no_target_codes_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = _setup_data_dir(tmp)
            wl_path, user_path = _setup_config(tmp)
            self.assertEqual(gan.run(data_dir, wl_path, user_path, "k", NOW, call_fn=lambda m, k: "x"), [])

    def test_api_failure_is_not_swallowed(self):
        """API失敗時はexit≠0につながるよう例外をそのまま伝播する(黙殺しない)。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = _setup_data_dir(tmp)
            wl_path, user_path = _setup_config(tmp, watchlist_codes=["8125"])

            def failing_call(material, api_key):
                raise RuntimeError("API error")

            with self.assertRaises(RuntimeError):
                gan.run(data_dir, wl_path, user_path, "k", NOW, call_fn=failing_call)
            # 失敗時はmanifestを更新しない(中途半端な処理済み記録を残さない)
            self.assertEqual(gan.seen_ids_of(data_dir), set())


if __name__ == "__main__":
    unittest.main()
