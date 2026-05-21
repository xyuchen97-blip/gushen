# guts/tests/test_normalizer.py
"""Test shared stock name normalizer."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from guts.utils.normalizer import create_normalizer, AssetType


def test_exact_match():
    norm = create_normalizer()
    assert norm.resolve("茅台").ticker == "600519.SH"
    assert norm.resolve("nvidia").ticker == "NVDA"
    assert norm.resolve("腾讯").ticker == "0700.HK"
    assert norm.resolve("GOOGL").ticker == "GOOGL"


def test_alias_match():
    norm = create_normalizer()
    assert norm.resolve("苹果").ticker == "AAPL"
    assert norm.resolve("baba").ticker == "9988.HK"


def test_market_filter():
    norm = create_normalizer()
    r = norm.resolve("比亚迪", market="A")
    assert r.ticker == "002594.SZ"


def test_reverse_lookup():
    norm = create_normalizer()
    assert norm.reverse_lookup("600519.SH") == "贵州茅台"


def test_passthrough():
    norm = create_normalizer()
    assert norm.resolve_or_passthrough("UNKNOWN_XYZ") == "UNKNOWN_XYZ"


def test_asset_type():
    norm = create_normalizer()
    assert norm.get_asset_type("600519.SH") == AssetType.STOCK
    assert norm.get_asset_type("FAKE") == AssetType.UNKNOWN


if __name__ == '__main__':
    test_exact_match(); print('.', end='')
    test_alias_match(); print('.', end='')
    test_market_filter(); print('.', end='')
    test_reverse_lookup(); print('.', end='')
    test_passthrough(); print('.', end='')
    test_asset_type(); print(' PASS')
