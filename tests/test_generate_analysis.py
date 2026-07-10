"""scripts/generate_analysis.py のテスト。Claude API は呼ばず call_fn を差し替えてモックする。"""
import datetime
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

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
    def test_generates_saves_notifies_and_dedupes_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = _setup_data_dir(tmp)
            wl_path, user_path = _setup_config(tmp, watchlist_codes=["8125"])
            calls, notified = [], []

            def fake_call(material, api_key):
                calls.append(material["code"])
                return "分析結果テキスト"

            def fake_notify(item, now):
                notified.append(item["disclosure_id"])
                return True

            saved = gan.run(data_dir, wl_path, user_path, "dummy-key", NOW,
                             call_fn=fake_call, notify_fn=fake_notify)
            self.assertEqual(len(saved), 1)
            self.assertEqual(calls, ["8125"])
            self.assertEqual(notified, ["K8125"])  # Issue通知は新規生成時のみ呼ばれる
            out_path = os.path.join(data_dir, "analysis", "8125_K8125.md")
            self.assertTrue(os.path.exists(out_path))
            with open(out_path, encoding="utf-8") as f:
                self.assertIn("分析結果テキスト", f.read())

            # 同一disclosure_idの再実行では処理済み(seen.json)により何もしない(冪等・二重通知なし)
            saved2 = gan.run(data_dir, wl_path, user_path, "dummy-key", NOW,
                              call_fn=fake_call, notify_fn=fake_notify)
            self.assertEqual(saved2, [])
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(notified), 1)

    def test_no_target_codes_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = _setup_data_dir(tmp)
            wl_path, user_path = _setup_config(tmp)
            saved = gan.run(data_dir, wl_path, user_path, "k", NOW,
                             call_fn=lambda m, k: "x", notify_fn=lambda item, now: True)
            self.assertEqual(saved, [])

    def test_api_failure_is_not_swallowed(self):
        """API失敗時はexit≠0につながるよう例外をそのまま伝播する(黙殺しない)。"""
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = _setup_data_dir(tmp)
            wl_path, user_path = _setup_config(tmp, watchlist_codes=["8125"])

            def failing_call(material, api_key):
                raise RuntimeError("API error")

            with self.assertRaises(RuntimeError):
                gan.run(data_dir, wl_path, user_path, "k", NOW,
                        call_fn=failing_call, notify_fn=lambda item, now: True)
            # 失敗時はmanifestを更新しない(中途半端な処理済み記録を残さない)
            self.assertEqual(gan.seen_ids_of(data_dir), set())


class TestCallClaudeCli(unittest.TestCase):
    """claude-cli バックエンド (ローカルタスクスケジューラ用)。subprocess.run をモックする。"""

    MATERIAL = {
        "code": "8125",
        "disclosure": {"title": "決算短信のお知らせ", "doc_type": "決算短信", "published_at": "2026-07-09T16:00:00"},
        "actuals": {"annual": [], "quarterly": []},
        "growth_signal": None, "margin_signal": None, "progress": None,
        "revision_signal": None, "revision_disclosures": [],
        "market_context_md": "値上がり優勢。", "market_context_date": "2026-07-09",
    }

    def _run_result(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)

    def test_parses_result_field_from_json_output(self):
        raw = json.dumps({"is_error": False, "result": "分析結果テキスト"})
        with mock.patch("subprocess.run", return_value=self._run_result(0, raw)) as m:
            text = gan.call_claude_cli(self.MATERIAL)
        self.assertEqual(text, "分析結果テキスト")
        cmd = m.call_args.args[0]
        self.assertIn("--output-format", cmd)
        self.assertIn("--system-prompt", cmd)

    def test_uses_claude_bin_env_override(self):
        raw = json.dumps({"is_error": False, "result": "ok"})
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": "/custom/claude"}):
            with mock.patch("subprocess.run", return_value=self._run_result(0, raw)) as m:
                gan.call_claude_cli(self.MATERIAL)
        self.assertEqual(m.call_args.args[0][0], "/custom/claude")

    def test_nonzero_exit_raises(self):
        with mock.patch("subprocess.run", return_value=self._run_result(1, "", "boom")):
            with self.assertRaises(RuntimeError):
                gan.call_claude_cli(self.MATERIAL)

    def test_is_error_response_raises(self):
        raw = json.dumps({"is_error": True, "result": "エラー内容"})
        with mock.patch("subprocess.run", return_value=self._run_result(0, raw)):
            with self.assertRaises(RuntimeError):
                gan.call_claude_cli(self.MATERIAL)

    def test_invalid_json_output_raises(self):
        with mock.patch("subprocess.run", return_value=self._run_result(0, "not json")):
            with self.assertRaises(RuntimeError):
                gan.call_claude_cli(self.MATERIAL)

    def test_empty_result_raises(self):
        raw = json.dumps({"is_error": False, "result": "   "})
        with mock.patch("subprocess.run", return_value=self._run_result(0, raw)):
            with self.assertRaises(RuntimeError):
                gan.call_claude_cli(self.MATERIAL)

    def test_launch_failure_raises(self):
        with mock.patch("subprocess.run", side_effect=OSError("not found")):
            with self.assertRaises(RuntimeError):
                gan.call_claude_cli(self.MATERIAL)


class TestCreateIssue(unittest.TestCase):
    def test_no_token_or_repo_skips_without_network_call(self):
        """GITHUB_TOKEN/GITHUB_REPOSITORYが無い環境ではネットワークに出ずFalseを返す
        (generate_alerts.create_issueと同じフェイルセーフ)。"""
        backup = {k: os.environ.pop(k, None) for k in ("GITHUB_TOKEN", "GITHUB_REPOSITORY")}
        try:
            item = {"code": "8125", "name": "テスト", "path": "8125_K8125.md", "title": "決算短信のお知らせ",
                    "doc_type": "決算短信", "published_at": "2026-07-09T16:00:00"}
            self.assertFalse(gan.create_issue(item, NOW))
        finally:
            for k, v in backup.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
