# guts/tests/test_zscore.py
"""Test z-score normalization (ScoreHistory)."""
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from guts.scoring.normalize import adaptive_zscore


def test_zscore_above_mean():
    scores = np.array([30., 40., 50., 50., 60., 70.])
    hi = adaptive_zscore(55., scores)
    lo = adaptive_zscore(35., scores)
    assert hi > lo, f"Higher raw → higher output: hi={hi:.1f} lo={lo:.1f}"


def test_no_crash_single():
    assert adaptive_zscore(40., np.array([40.])) is not None


def test_zscore_range():
    np.random.seed(42)
    scores = np.random.normal(70, 10, 100).astype(float)
    results = [adaptive_zscore(scores[i], scores[:i+1]) for i in range(50, 100)]
    # Output should be in reasonable score range (0-100)
    for r in results:
        assert 0 <= r <= 100, f"Score {r} out of range"


if __name__ == '__main__':
    test_zscore_above_mean(); print('.', end='')
    test_no_crash_single(); print('.', end='')
    test_zscore_range(); print(' PASS')
