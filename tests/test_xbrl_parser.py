"""scripts/xbrl_parser.py (決算短信サマリーXBRLパーサ) のユニットテスト。

実XBRLサンプルはこのリポジトリに無いため、tests/fixtures/ に置いた最小の
合成XBRLフィクスチャを使う。検証観点は T-3 の設計原則に対応する:
  - 連結優先・単体劣後のコンテキスト選択 ("Member" を含むことを理由に
    一律除外しない)
  - △/▲ 負数表記の変換
  - 不正な XBRL での例外送出
"""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "xbrl_parser", os.path.join(_HERE, "..", "scripts", "xbrl_parser.py"))
xp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xp)


def _read_fixture(name):
    with open(os.path.join(_HERE, "fixtures", name), "rb") as f:
        return f.read()


class TestXbrlParser(unittest.TestCase):
    def test_consolidated_value_with_member_context_is_not_excluded(self):
        """'ConsolidatedMember' のように contextRef に 'Member' を含んでいても
        連結値として正しく採用され、単体値 (NonConsolidatedMember) に
        フォールバックしないこと (設計原則2の既知バグ回避)。"""
        xbrl = _read_fixture("xbrl_summary_consolidated_member.xml")
        result = xp.parse_summary(xbrl)
        self.assertEqual(result["net_sales"], 1234567890.0)
        self.assertTrue(result["consolidated"])

    def test_negative_value_with_geta_mark_is_converted(self):
        """△ 付きの営業利益が負数に変換されること。"""
        xbrl = _read_fixture("xbrl_summary_consolidated_member.xml")
        result = xp.parse_summary(xbrl)
        self.assertEqual(result["operating_income"], -12345.0)

    def test_revision_flag_detected(self):
        xbrl = _read_fixture("xbrl_summary_consolidated_member.xml")
        result = xp.parse_summary(xbrl)
        self.assertIs(result["forecast_revised"], True)

    def test_nonconsolidated_fallback_when_no_consolidated_context(self):
        """連結コンテキストが1つも無ければ単体値にフォールバックし、
        consolidated=False を返すこと (単体劣後)。"""
        xbrl = _read_fixture("xbrl_summary_nonconsolidated_only.xml")
        result = xp.parse_summary(xbrl)
        self.assertEqual(result["net_sales"], 500000000.0)
        self.assertEqual(result["operating_income"], -1000.0)  # ▲1,000 も負数変換される
        self.assertFalse(result["consolidated"])

    def test_prior_year_fact_appearing_first_is_not_mistakenly_picked(self):
        """同一タグに前期・当期の両方のfactがあり、前期の値が文書順で先に
        出現しても、contextRefの期間定義(endDate)を見て当期の値を採用する
        こと(C-4: 文書順で最初のfactを採用してしまう既知バグの再発防止)。"""
        xbrl = _read_fixture("xbrl_summary_prior_year_first.xml")
        result = xp.parse_summary(xbrl)
        self.assertEqual(result["net_sales"], 900000000.0)  # 当期(900M)。前期(700M)ではない
        self.assertEqual(result["operating_income"], 80000.0)  # 当期(80,000)。前期(50,000)ではない
        self.assertTrue(result["consolidated"])

    def test_invalid_xbrl_raises(self):
        xbrl = _read_fixture("xbrl_invalid.xml")
        with self.assertRaises(xp.XbrlParseError):
            xp.parse_summary(xbrl)

    def test_empty_input_raises(self):
        with self.assertRaises(xp.XbrlParseError):
            xp.parse_summary(b"")
        with self.assertRaises(xp.XbrlParseError):
            xp.parse_summary(None)

    # インラインXBRL (ix:nonFraction/ix:nonNumeric) 対応 (W2-1: 実データ検証で判明した形式) --

    def test_inline_xbrl_nonfraction_scale_and_period_context_are_applied(self):
        """ix:nonFraction は name属性で要素を識別し、scale属性 (10のべき乗) を
        掛けて実際の値にすること。前期の値が文書順で先に出現しても、期間定義
        (endDate) を見て当期の値 (3,700 * 10^6) を採用すること。"""
        xbrl = _read_fixture("xbrl_summary_inline_ixbrl.xml")
        result = xp.parse_summary(xbrl)
        self.assertEqual(result["net_sales"], 3_700_000_000.0)
        self.assertTrue(result["consolidated"])

    def test_inline_xbrl_nonfraction_sign_attribute_is_negated(self):
        """ix:nonFraction の負数は △/▲ ではなく sign="-" 属性で表されること。"""
        xbrl = _read_fixture("xbrl_summary_inline_ixbrl.xml")
        result = xp.parse_summary(xbrl)
        self.assertEqual(result["operating_income"], -240_000_000.0)

    def test_inline_xbrl_revision_flag_detected_via_name_attribute(self):
        """ix:nonNumeric の業績予想修正フラグは name属性 (実データの正式タグ名
        CorrectionOfConsolidatedFinancialForecastInThisQuarter) で検出すること。"""
        xbrl = _read_fixture("xbrl_summary_inline_ixbrl.xml")
        result = xp.parse_summary(xbrl)
        self.assertIs(result["forecast_revised"], True)

    def test_to_number_handles_plain_and_comma(self):
        self.assertEqual(xp._to_number("1,234"), 1234.0)
        self.assertEqual(xp._to_number("△1,234"), -1234.0)
        self.assertEqual(xp._to_number("▲500"), -500.0)
        self.assertEqual(xp._to_number(""), None)
        self.assertEqual(xp._to_number(None), None)
        self.assertEqual(xp._to_number("N/A"), None)


if __name__ == "__main__":
    unittest.main()
