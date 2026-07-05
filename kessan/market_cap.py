"""時価総額レンジの定義と判定ロジック。

要件定義 4.2 の絞り込み条件に対応する。金額はすべて「円」単位で扱う。
"""

OKU = 100_000_000  # 1億円
CHO = 10_000 * OKU  # 1兆円

# 各レンジ: key, ラベル, 下限(以上, 円), 上限(未満, 円 or None)
MARKET_CAP_RANGES = [
    {"key": "lt100oku", "label": "100億円未満", "min": 0, "max": 100 * OKU},
    {"key": "100to300oku", "label": "100億円以上〜300億円未満", "min": 100 * OKU, "max": 300 * OKU},
    {"key": "300to1000oku", "label": "300億円以上〜1,000億円未満", "min": 300 * OKU, "max": 1000 * OKU},
    {"key": "1000to3000oku", "label": "1,000億円以上〜3,000億円未満", "min": 1000 * OKU, "max": 3000 * OKU},
    {"key": "3000okuto1cho", "label": "3,000億円以上〜1兆円未満", "min": 3000 * OKU, "max": CHO},
    {"key": "gte1cho", "label": "1兆円以上", "min": CHO, "max": None},
]

_RANGE_BY_KEY = {r["key"]: r for r in MARKET_CAP_RANGES}


def get_range(key):
    """レンジキーから定義を返す。存在しなければ None。"""
    return _RANGE_BY_KEY.get(key)


def range_bounds(key):
    """レンジキーから (min, max) を返す。max は None のことがある。

    未知のキーの場合は (None, None) を返す。
    """
    r = _RANGE_BY_KEY.get(key)
    if not r:
        return None, None
    return r["min"], r["max"]


def classify(market_cap):
    """時価総額(円)が属するレンジの key を返す。"""
    if market_cap is None:
        return None
    for r in MARKET_CAP_RANGES:
        lo, hi = r["min"], r["max"]
        if market_cap >= lo and (hi is None or market_cap < hi):
            return r["key"]
    return None


def format_oku(market_cap):
    """時価総額(円)を「◯◯億円」「◯.◯兆円」の読みやすい表記に整形する。"""
    if market_cap is None:
        return "-"
    if market_cap >= CHO:
        return f"{market_cap / CHO:.2f}兆円"
    return f"{market_cap / OKU:,.0f}億円"
