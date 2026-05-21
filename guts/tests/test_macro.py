# guts/tests/test_macro.py
"""Test macro state computation."""
import sys, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from guts.macro.state import MacroState, MacroRegime, Region
from guts.macro.compute import score_vix, score_yield_curve, score_qvix


def test_default_vix():
    assert score_vix(15) > 0, "Low VIX = bullish"
    assert score_vix(25) < 0, "High VIX = bearish"
    assert score_vix(30) < score_vix(20), "Monotonic"


def test_yield_curve():
    assert score_yield_curve(1.5) > 0, "Normal curve = +"
    assert score_yield_curve(-0.5) < 0, "Inverted = -"


def test_qvix():
    assert score_qvix(10) > 0, "Low QVIX = calm"
    assert score_qvix(60) < 0, "High QVIX = stress"


if __name__ == '__main__':
    test_default_vix(); print('.', end='')
    test_yield_curve(); print('.', end='')
    test_qvix(); print(' PASS')
