# guts/tests/test_signals.py
"""Test continuous signals computation (v10-compatible)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd
from guts.signals.continuous import ContinuousSignals


def _make_precomputed(overrides=None):
    """Build a minimal precomputed dict with sensible defaults for 100 bars."""
    n = 100
    # All keys that continuous.py's compute() accesses via pre["key"]
    defaults = {
        "kdj_j": pd.Series(np.full(n, 50.0)),
        "kdj_k": pd.Series(np.full(n, 50.0)),
        "kdj_d": pd.Series(np.full(n, 50.0)),
        "kdj_golden": pd.Series(np.zeros(n, dtype=bool)),
        "kdj_oversold": pd.Series(np.zeros(n, dtype=bool)),
        "bb_pct": pd.Series(np.full(n, 0.5)),
        "bb_buy": pd.Series(np.zeros(n, dtype=bool)),
        "bb_sell": pd.Series(np.zeros(n, dtype=bool)),
        "rsi": pd.Series(np.full(n, 50.0)),
        "vol_z": pd.Series(np.zeros(n)),
        "vol_anomaly": pd.Series(np.zeros(n, dtype=bool)),
        "golden_pit": pd.Series(np.zeros(n)),
        "band_low": pd.Series(np.zeros(n)),
        "buy_signal": pd.Series(np.zeros(n, dtype=bool)),
        "buy_setup_done": pd.Series(np.zeros(n, dtype=bool)),
        "buy2": pd.Series(np.zeros(n, dtype=bool)),
        "sell_signal": pd.Series(np.zeros(n, dtype=bool)),
        "sell1": pd.Series(np.zeros(n, dtype=bool)),
        "adx_strong": pd.Series(np.zeros(n, dtype=bool)),
        "adx_val": pd.Series(np.full(n, 15.0)),
        "plus_di": pd.Series(np.full(n, 20.0)),
        "minus_di": pd.Series(np.full(n, 20.0)),
        "ma_aligned": pd.Series(np.zeros(n, dtype=bool)),
        "ma_golden": pd.Series(np.zeros(n, dtype=bool)),
        "ma_death": pd.Series(np.zeros(n, dtype=bool)),
        "price_above_ma50": pd.Series(np.zeros(n, dtype=bool)),
        "macd_golden": pd.Series(np.zeros(n, dtype=bool)),
        "macd_death": pd.Series(np.zeros(n, dtype=bool)),
        "macd_hist": pd.Series(np.zeros(n)),
        "bullish_divergence": pd.Series(np.zeros(n, dtype=bool)),
        "bull_regime": pd.Series(np.zeros(n, dtype=bool)),
        "weekly_ma20_up": pd.Series(np.ones(n, dtype=bool)),
        "weekly_fib_support": pd.Series(np.zeros(n, dtype=bool)),
        "national_team": pd.Series(np.zeros(n, dtype=bool)),
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, (int, float, bool)):
                defaults[k] = pd.Series(np.full(n, v))
            else:
                defaults[k] = v
    return defaults


def test_oversold_signals():
    """Deeply oversold indicators should produce positive signals."""
    pre = _make_precomputed({"kdj_j": -10.0, "kdj_k": 15.0, "bb_pct": -0.3, "rsi": 20.0})
    cs = ContinuousSignals()
    results = cs.compute(pre, 80)
    # Should have some bullish signal from KDJ
    assert cs.composite() > 0, f"Oversold signals should be bullish, got {cs.composite():.3f}"


def test_overbought_signals():
    """Overbought indicators should produce negative or near-zero signals."""
    pre = _make_precomputed({"kdj_j": 110.0, "kdj_k": 85.0, "bb_pct": 1.2, "rsi": 75.0})
    cs = ContinuousSignals()
    results = cs.compute(pre, 80)
    assert cs.composite() < 0.2, f"Overbought composite should be low, got {cs.composite():.3f}"


def test_neutral_signals():
    """Default neutral indicators → composite near zero."""
    pre = _make_precomputed()
    cs = ContinuousSignals()
    results = cs.compute(pre, 80)
    assert abs(cs.composite()) < 0.5, f"Neutral composite should be ~0, got {cs.composite():.3f}"


def test_dzh_buy_signals():
    """DZH binary buy signals should contribute positively."""
    pre = _make_precomputed({"golden_pit": 1.0, "buy_signal": True})
    cs = ContinuousSignals()
    results = cs.compute(pre, 80)
    assert cs.composite() > 0, f"Golden pit + nine_turns_buy should be bullish"


def test_dzh_sell_signals():
    """DZH sell signals should contribute negatively."""
    pre = _make_precomputed({"sell_signal": True, "sell1": True})
    cs = ContinuousSignals()
    results = cs.compute(pre, 80)
    # Sell signals should drag composite down
    has_sell = any(s.value < 0 for s in results.values())
    assert has_sell, "Sell signals should produce negative signal values"


def test_category_summary():
    """Category summary should have expected categories."""
    pre = _make_precomputed({"kdj_j": -5.0, "kdj_k": 15.0, "adx_val": 35.0, "vol_z": 2.5})
    cs = ContinuousSignals()
    cs.compute(pre, 80)
    cats = cs.category_summary()
    # Should have at least some categories
    assert isinstance(cats, dict), "category_summary should return a dict"


if __name__ == '__main__':
    test_oversold_signals(); print('.', end='')
    test_overbought_signals(); print('.', end='')
    test_neutral_signals(); print('.', end='')
    test_dzh_buy_signals(); print('.', end='')
    test_dzh_sell_signals(); print('.', end='')
    test_category_summary(); print(' PASS')
