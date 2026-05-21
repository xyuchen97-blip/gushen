"""
Fibonacci Retracement & Extension — support/resistance level detection.

Computes Fibonacci levels from the most recent 50-bar swing high/low.
Scores proximity to key levels: 0.382, 0.5, 0.618 (retracement) and
1.272, 1.618 (extension).
"""

import numpy as np
import pandas as pd


# Key Fibonacci levels
RETRACEMENT_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
EXTENSION_LEVELS   = [1.0, 1.272, 1.382, 1.618, 2.0, 2.618]

# Proximity threshold (% of price to be considered "near" a level)
PROXIMITY_PCT = 0.02  # 2%


def find_swing_points(df: pd.DataFrame, lookback: int = 50) -> tuple:
    """
    Find the most recent swing high and swing low within the lookback window.

    Returns (swing_high, swing_high_date, swing_low, swing_low_date)
    """
    window = df.tail(lookback)
    if len(window) < 10:
        # Use full range if not enough data
        window = df

    high_idx = window["high"].idxmax()
    low_idx = window["low"].idxmin()

    return (
        window.loc[high_idx, "high"], high_idx,
        window.loc[low_idx, "low"], low_idx,
    )


def compute_fib_levels(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Compute Fibonacci retracement and extension levels from the 50-bar swing range.

    For a downtrend (swing high → swing low): retracement = low + (high-low) * level
    For an uptrend (swing low → swing high): retracement = high - (high-low) * level

    Returns dict with:
        retracement_levels : dict {0.382: price, ...}
        extension_levels   : dict {1.272: price, ...}
        trend_direction    : "up" or "down"
        swing_high, swing_low
    """
    swing_high, sh_date, swing_low, sl_date = find_swing_points(df, lookback)

    if swing_low >= swing_high:
        return {"error": "Swing low >= swing high, cannot compute"}

    # Determine direction: if swing low is more recent → uptrend
    trend = "up" if sl_date > sh_date else "down"
    diff = swing_high - swing_low

    retracement = {}
    extension = {}

    if trend == "down":
        # Price fell from swing_high to swing_low → retrace up
        for level in RETRACEMENT_LEVELS:
            retracement[level] = swing_low + diff * level
        for level in EXTENSION_LEVELS:
            extension[level] = swing_low - diff * (level - 1)
    else:
        # Price rose from swing_low to swing_high → retrace down
        for level in RETRACEMENT_LEVELS:
            retracement[level] = swing_high - diff * level
        for level in EXTENSION_LEVELS:
            extension[level] = swing_high + diff * (level - 1)

    return {
        "retracement_levels": retracement,
        "extension_levels": extension,
        "trend_direction": trend,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "swing_range": diff,
    }


def score_fibonacci(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Score current price proximity to key Fibonacci levels.

    Returns dict with:
        retracement_score (0-3): proximity to 0.382/0.5/0.618 support
        extension_score (0-3): proximity to 1.272/1.618 extension
        nearest_level: which level is closest
    """
    levels = compute_fib_levels(df, lookback)
    if "error" in levels:
        return {"retracement_score": 0, "extension_score": 0, "details": levels["error"]}

    current_price = df["close"].iloc[-1]

    retrace_score = 0
    extension_score = 0

    # Check proximity to key retracement levels
    for key_level in [0.382, 0.5, 0.618]:
        target = levels["retracement_levels"][key_level]
        proximity = abs(current_price - target) / current_price
        if proximity < PROXIMITY_PCT:
            retrace_score = 3
            break
        elif proximity < PROXIMITY_PCT * 2:
            retrace_score = max(retrace_score, 2)
        elif proximity < PROXIMITY_PCT * 3:
            retrace_score = max(retrace_score, 1)

    # Check proximity to key extension levels
    for key_level in [1.272, 1.618]:
        target = levels["extension_levels"][key_level]
        proximity = abs(current_price - target) / current_price
        if proximity < PROXIMITY_PCT:
            extension_score = 3
            break
        elif proximity < PROXIMITY_PCT * 2:
            extension_score = max(extension_score, 2)

    return {
        "retracement_score": retrace_score,
        "extension_score": extension_score,
        "trend": levels["trend_direction"],
        "swing_high": levels["swing_high"],
        "swing_low": levels["swing_low"],
    }
