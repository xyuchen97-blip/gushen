# guts/scoring/normalize.py
"""
Z-Score Normalization & Score History.

DEPRECATED by v10 (May 20, 2026): v10 eliminated z-score normalization entirely.
Kept for reference and v9.7 regression testing only.

ScoreHistory maintains a per-ticker rolling window of raw composite scores
and normalizes them to a z-score distribution centered at μ=50, σ=16.7.

This solved the A-stock problem in v9.6-v9.7, but z-score destroyed 27%
of raw signal predictive power in v10 testing. v10 uses per-market fixed
thresholds (V10_THRESHOLDS) instead.
"""

import numpy as np
from collections import defaultdict
from typing import Optional, Dict, List


class ScoreHistory:
    """
    Per-ticker rolling window of raw composite scores.
    
    Usage in backtest loop:
        sh = ScoreHistory(window=52, min_history=12)
        for ticker in universe:
            for each_bar:
                raw = score_bar(...)
                normalized = sh.normalize(ticker, raw)
                sh.record(ticker, raw)
    """
    
    def __init__(self, window: int = 52, min_history: int = 12):
        """
        Args:
            window: Rolling window size for mean/std calculation (weeks)
            min_history: Minimum observations before normalization kicks in
        """
        self.window = window
        self.min_history = min_history
        self._scores: Dict[str, List[float]] = defaultdict(list)
        # Per-ticker cached stats
        self._mean: Dict[str, float] = {}
        self._std: Dict[str, float] = {}
        self._count: Dict[str, int] = {}
    
    def record(self, ticker: str, score: float):
        """Record a raw composite score. Call every bar in chronological order."""
        self._scores[ticker].append(score)
        # Keep only the last `window` scores
        if len(self._scores[ticker]) > self.window:
            self._scores[ticker] = self._scores[ticker][-self.window:]
    
    def normalize(self, ticker: str, score: float, 
                  target_mean: float = 50.0, target_std: float = 16.7) -> float:
        """
        Normalize a raw score to z-score distribution.
        
        If history < min_history, returns raw score unchanged (no normalization).
        Scale: z-score × target_std + target_mean → centered at 50 with σ≈16.7
        
        This means: BUY at ~62 (z≈+0.72), EXIT at ~35 (z≈-0.90)
        """
        scores = self._scores.get(ticker, [])
        n = len(scores)
        
        if n < self.min_history:
            return score  # Not enough history — pass through raw
        
        # Compute rolling stats
        arr = np.array(scores[-self.window:])
        mean = float(arr.mean())
        std = float(arr.std(ddof=1)) if len(arr) >= 2 else 1.0
        
        if std < 0.01:
            return target_mean  # Degenerate case — no variance
        
        # Z-score normalization
        z = (score - mean) / std
        normalized = z * target_std + target_mean
        
        return round(normalized, 2)
    
    def get_stats(self, ticker: str) -> Optional[dict]:
        """Get current per-ticker statistics for diagnostics."""
        scores = self._scores.get(ticker, [])
        if len(scores) < self.min_history:
            return None
        arr = np.array(scores[-self.window:])
        return {
            'ticker': ticker,
            'count': len(arr),
            'mean': round(float(arr.mean()), 2),
            'std': round(float(arr.std(ddof=1)), 2),
            'min': round(float(arr.min()), 2),
            'max': round(float(arr.max()), 2),
        }
    
    def reset_ticker(self, ticker: str):
        """Reset history for a specific ticker."""
        self._scores.pop(ticker, None)
        self._mean.pop(ticker, None)
        self._std.pop(ticker, None)
        self._count.pop(ticker, None)
    
    def reset_all(self):
        """Reset all history."""
        self._scores.clear()
        self._mean.clear()
        self._std.clear()
        self._count.clear()


def adaptive_zscore(value: float, history: np.ndarray, 
                    target_mean: float = 50.0, target_std: float = 16.7) -> float:
    """
    Stateless z-score normalization against a pre-computed history array.
    
    Args:
        value: The raw score to normalize
        history: Array of historical scores (e.g., last 52 weeks)
        target_mean: Center of normalized distribution
        target_std: Standard deviation of normalized distribution
    
    Returns:
        Normalized score, or value unchanged if history < 2
    """
    if len(history) < 2:
        return value
    
    mean = float(history.mean())
    std = float(history.std(ddof=1))
    
    if std < 0.01:
        return target_mean
    
    z = (value - mean) / std
    return round(z * target_std + target_mean, 2)
