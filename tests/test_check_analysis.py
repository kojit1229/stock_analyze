"""scripts/check_analysis.py のテスト (roadmap P3-2)。"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location(
    "check_analysis",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "check_analysis.py"))
ca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ca)


def _item(**overrides):
    base = {
        "disclosure_id": "K1", "code": "8125", "name": "テスト", "title": "決算短信のお知らせ",
        "doc_type": "決算短信", "published_at": "2026-07-09T16:00:00", "path": "8125_K1.md",
        "generated_at": "2026-07-10T09:00:00+09:00",
    }
    base.update(overrides)
    return base


def _write_manifest(data_dir, items):
    analysis_dir = os.path.join(data_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    with open(os.path.join(analysis_dir, "seen.json"), "w", encoding="utf-8") as f:
        json.dump({"items": items}, f)
    return analysis_dir


def _write_md(analysis_dir, filename, text="本文"):
    with open(os.path.join(analysis_dir, filename), "w", encoding="utf-8") as f:
        f.write(text)


class TestMissingManifest(unittest.TestCase):
    def test_missing_seen_json_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "frontend", "data")
            os.makedirs(data_dir, exist_ok=True)
            violations, warnings = ca.check_manifest(data_dir)
            self.assertEqual(violations, [])
            self.assertEqual(warnings, [])

    def test_missing_analysis_dir_entirely_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            violations, warnings = ca.check_manifest(tmp)
            self.assertEqual(violations, [])
            self.assertEqual(warnings, [])


class TestValidManifest(unittest.TestCase):
    def test_valid_entry_has_no_violations_or_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = _write_manifest(tmp, [_item()])
            _write_md(analysis_dir, "8125_K1.md")
            violations, warnings = ca.check_manifest(tmp)
            self.assertEqual(violations, [])
            self.assertEqual(warnings, [])


class TestRequiredFields(unittest.TestCase):
    def test_missing_required_field_is_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = _item()
            del item["doc_type"]
            analysis_dir = _write_manifest(tmp, [item])
            _write_md(analysis_dir, "8125_K1.md")
            violations, _ = ca.check_manifest(tmp)
            self.assertEqual(len(violations), 1)
            self.assertIn("doc_type", violations[0])

    def test_empty_disclosure_id_is_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = _write_manifest(tmp, [_item(disclosure_id="")])
            _write_md(analysis_dir, "8125_K1.md")
            violations, _ = ca.check_manifest(tmp)
            self.assertTrue(any("disclosure_id" in v for v in violations))

    def test_empty_name_or_title_is_allowed(self):
        """name/title/doc_type/published_at はキー存在のみ必須、値の空は後方互換で許容する。"""
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = _write_manifest(tmp, [_item(name="", title="")])
            _write_md(analysis_dir, "8125_K1.md")
            violations, _ = ca.check_manifest(tmp)
            self.assertEqual(violations, [])


class TestDuplicateDisclosureId(unittest.TestCase):
    def test_duplicate_disclosure_id_is_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = _write_manifest(tmp, [
                _item(disclosure_id="K1", path="8125_K1.md"),
                _item(disclosure_id="K1", path="8125_K1b.md"),
            ])
            _write_md(analysis_dir, "8125_K1.md")
            _write_md(analysis_dir, "8125_K1b.md")
            violations, _ = ca.check_manifest(tmp)
            self.assertTrue(any("重複" in v for v in violations))


class TestMissingFile(unittest.TestCase):
    def test_missing_md_file_is_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_manifest(tmp, [_item(path="does_not_exist.md")])
            violations, _ = ca.check_manifest(tmp)
            self.assertTrue(any("存在しません" in v for v in violations))


class TestMaxChars(unittest.TestCase):
    def test_over_max_chars_is_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = _write_manifest(tmp, [_item()])
            _write_md(analysis_dir, "8125_K1.md", text="a" * 100)
            violations, _ = ca.check_manifest(tmp, max_chars=50)
            self.assertTrue(any("文字数上限超過" in v for v in violations))

    def test_within_max_chars_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = _write_manifest(tmp, [_item()])
            _write_md(analysis_dir, "8125_K1.md", text="a" * 40)
            violations, _ = ca.check_manifest(tmp, max_chars=50)
            self.assertEqual(violations, [])


class TestOrphanFiles(unittest.TestCase):
    def test_orphan_md_is_warning_not_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = _write_manifest(tmp, [_item()])
            _write_md(analysis_dir, "8125_K1.md")
            _write_md(analysis_dir, "9999_orphan.md")
            violations, warnings = ca.check_manifest(tmp)
            self.assertEqual(violations, [])
            self.assertTrue(any("孤児ファイル" in w and "9999_orphan.md" in w for w in warnings))


class TestCorruptManifest(unittest.TestCase):
    def test_corrupt_seen_json_is_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = os.path.join(tmp, "analysis")
            os.makedirs(analysis_dir, exist_ok=True)
            with open(os.path.join(analysis_dir, "seen.json"), "w", encoding="utf-8") as f:
                f.write("{not valid json")
            violations, _ = ca.check_manifest(tmp)
            self.assertEqual(len(violations), 1)


class TestMain(unittest.TestCase):
    def test_main_exits_zero_when_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "frontend", "data")
            analysis_dir = _write_manifest(data_dir, [_item()])
            _write_md(analysis_dir, "8125_K1.md")
            buf = io.StringIO()
            from unittest import mock
            with mock.patch.object(sys, "argv", ["check_analysis.py", "--data", data_dir]), \
                 contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    ca.main()
            self.assertEqual(cm.exception.code, 0)
            self.assertIn("OK", buf.getvalue())

    def test_main_exits_one_when_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "frontend", "data")
            _write_manifest(data_dir, [_item(path="missing.md")])
            buf = io.StringIO()
            from unittest import mock
            with mock.patch.object(sys, "argv", ["check_analysis.py", "--data", data_dir]), \
                 contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    ca.main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("整合エラー", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
