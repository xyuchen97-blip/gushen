# guts/tests/test_sensitivity.py
"""Test style sensitivity module."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from guts.macro.sensitivity import get_sensitivity, StockStyle


def test_has_styles():
    assert StockStyle.GROWTH.value == "growth"
    assert StockStyle.DEFENSIVE.value == "defensive"


def test_gushen_tickers():
    """All 21 Gushen universe tickers should resolve."""
    tickers = ['600519','002594','601318','000858','600036','002230','300015','300750',
               '0700.HK','9988.HK','3690.HK','1810.HK','1211.HK','0388.HK']
    for t in tickers:
        r = get_sensitivity(t)
        assert r is not None, f"{t} should resolve"
        assert isinstance(r.style, StockStyle)


if __name__ == '__main__':
    test_has_styles(); print('.', end='')
    test_gushen_tickers(); print(' PASS')
