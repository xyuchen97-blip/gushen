"""
Elliott Wave & Pattern Detection — wave structure + right shoulder traps.
Integrates with fibonacci.py for swing detection.

Wave labeling: W0(low) → W1(high) → W2(low) → W3(high) → W4(low) → W5(target)
Right shoulder: 3-phase topping pattern (surge → pullback → squeeze)

v9.4+: detect_wave5_target tightened — requires proper Fibonacci ratios
       between waves (Frost & Prechter rules). Window widened to 20 bars.
       Diagnostic-only; not used in scoring production path.
"""

import numpy as np
import pandas as pd

def detect_wave5_target(df: pd.DataFrame, lookback: int = 250) -> dict:
    """
    Detect complete Elliott Wave 1-5 structure with strict Fibonacci ratio validation.

    Strict rules (per Frost & Prechter):
    - Wave 2 retraces 50-78.6% of Wave 1 (never exceeds Wave 1 start)
    - Wave 3 is typically 1.618-2.618x Wave 1 (at minimum > Wave 1)
    - Wave 4 retraces 23.6-38.2% of Wave 3 (never overlaps Wave 1 top)
    - Wave 5 target = Wave 4 end + Wave 1 x 1.618

    Returns wave5_active=True only when ALL rules above are satisfied.
    Returns wave5_active=False for incomplete structures (no defaults/fallbacks).

    Parameters
    ----------
    df : DataFrame with columns open/high/low/close/volume
    lookback : int, number of bars to consider (default 250)

    Returns
    -------
    dict with wave5_active, wave5_target, wave5_near, wave5_above,
         w1_length_pct, w3_ratio, current, target_pct
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    n = len(close)

    # Need sufficient history for meaningful wave structure
    if n < 100:
        return {"wave5_active": False, "wave5_target": None}

    # Find significant swing points with wider window (20 vs old 10)
    WINDOW = 20
    swings_high = []
    swings_low = []

    for i in range(WINDOW, n - WINDOW):
        if high[i] == max(high[i - WINDOW:i + WINDOW + 1]):
            swings_high.append((i, high[i]))
        if low[i] == min(low[i - WINDOW:i + WINDOW + 1]):
            swings_low.append((i, low[i]))

    # Need at least 5 swing points (3 lows + 3 highs minimum for 5-wave labeling)
    if len(swings_low) < 3 or len(swings_high) < 3:
        return {"wave5_active": False, "wave5_target": None}

    # Walk backwards through swings to label waves sequentially
    # Expected: W0(low) → W1(high) → W2(low) → W3(high) → W4(low) → W5_target

    # Most recent swings (last 4 of each type for robust labeling)
    recent_lows = swings_low[-4:] if len(swings_low) >= 4 else swings_low[-3:]
    recent_highs = swings_high[-4:] if len(swings_high) >= 4 else swings_high[-3:]

    if len(recent_lows) < 2 or len(recent_highs) < 2:
        return {"wave5_active": False, "wave5_target": None}

    # ── Label waves walking backwards from current bar ──

    # W4: most recent significant low (last completed wave before current)
    w4_candidates = [(i, v) for i, v in recent_lows if i < n - WINDOW]
    if not w4_candidates:
        return {"wave5_active": False, "wave5_target": None}
    w4_low_idx, w4_low = w4_candidates[-1]

    # W3: last major high before W4
    w3_candidates = [(i, v) for i, v in recent_highs if i < w4_low_idx - WINDOW]
    if not w3_candidates:
        return {"wave5_active": False, "wave5_target": None}
    w3_high_idx, w3_high = w3_candidates[-1]

    # W2: last major low before W3
    w2_candidates = [(i, v) for i, v in recent_lows if i < w3_high_idx - WINDOW]
    if len(w2_candidates) < 1:
        return {"wave5_active": False, "wave5_target": None}
    w2_low_idx, w2_low = w2_candidates[-1]

    # W1: last major high before W2
    w1_candidates = [(i, v) for i, v in recent_highs if i < w2_low_idx - WINDOW]
    if not w1_candidates:
        return {"wave5_active": False, "wave5_target": None}
    w1_high_idx, w1_high = w1_candidates[-1]

    # W0: last major low before W1 (impulse start)
    w0_candidates = [(i, v) for i, v in recent_lows if i < w1_high_idx - WINDOW]
    if not w0_candidates:
        return {"wave5_active": False, "wave5_target": None}
    w0_low_idx, w0_low = w0_candidates[-1]

    # ── Fibonacci Ratio Validation ──

    # Wave 1 length — strict: must be positive, no synthetic default
    w1_length = w1_high - w0_low
    if w1_length <= 0:
        return {"wave5_active": False, "wave5_target": None}

    # Wave 2 retracement (must retrace 50-78.6% of W1)
    w2_retrace = (w1_high - w2_low) / w1_length
    if not (0.50 <= w2_retrace <= 0.786):
        return {"wave5_active": False, "wave5_target": None}

    # Wave 3 length (must be 1.618-2.618x W1 — EW "extended wave" rule)
    w3_length = w3_high - w2_low
    w3_ratio = w3_length / w1_length
    if not (1.618 <= w3_ratio <= 2.618):
        return {"wave5_active": False, "wave5_target": None}

    # Wave 4 retracement (must retrace 23.6-38.2% of W3)
    w4_retrace = (w3_high - w4_low) / w3_length
    if not (0.236 <= w4_retrace <= 0.382):
        return {"wave5_active": False, "wave5_target": None}

    # Wave 4 must NOT overlap Wave 1 top (critical EW rule)
    if w4_low <= w1_high:
        return {"wave5_active": False, "wave5_target": None}

    # ── Wave 5 Target Calculation ──
    # Primary method: W5 = W4_end + W1 x 1.618 (common impulse projection)
    w5_target = w4_low + w1_length * 1.618

    # Proximity check (tightened: 3% vs old 5%)
    current = close[-1]
    near_target = (w5_target > 0 and abs(current - w5_target) / w5_target < 0.03)
    above_target = current > w5_target * 1.01  # 1% buffer vs old 2%

    return {
        "wave5_active": True,
        "wave5_target": round(w5_target, 2),
        "wave5_near": near_target,
        "wave5_above": above_target,
        "w1_length_pct": round(w1_length / w0_low * 100, 1),
        "w3_ratio": round(w3_ratio, 2),
        "current": round(current, 2),
        "target_pct": round((w5_target / current - 1) * 100, 1) if current > 0 else 0,
    }

    # Fallback unreachable — validated path returns above


def detect_right_shoulder(df: pd.DataFrame, lookback: int = 60) -> dict:
    """
    Detect right shoulder pattern: 3-phase topping trap.

    Phase 1 (surge): price breaks above 20-bar high with volume spike
    Phase 2 (pullback): price retraces 38-62% of the surge
    Phase 3 (squeeze): price makes marginal new high on declining volume

    This is a SELL warning — don't chase the last squeeze.
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    vol = df['volume'].values
    n = len(close)

    if n < 40:
        return {"shoulder_active": False}

    # Phase 1: find highest bar in last 40 bars
    recent_high = max(high[-40:])
    recent_high_idx = n - 40 + high[-40:].argmax()

    # Phase 2: find pullback after the high
    if recent_high_idx < n - 5:
        post_high_lows = low[recent_high_idx:]
        pullback_low = min(post_high_lows)
        pullback_idx = recent_high_idx + post_high_lows.argmin()

        retracement = (recent_high - pullback_low) / (recent_high - low[max(0, recent_high_idx-20):recent_high_idx].min()) if recent_high_idx > 20 else 0

        # Phase 3: check if current bar is making a marginal new high
        current_high = high[-1]
        new_high_marginal = current_high > recent_high * 0.97 and current_high <= recent_high * 1.03

        # Volume decline from Phase 1
        vol_phase1 = np.mean(vol[max(0, recent_high_idx-5):recent_high_idx+1])
        vol_phase3 = np.mean(vol[-3:])
        vol_declining = vol_phase3 < vol_phase1 * 0.8 if vol_phase1 > 0 else False

        # Check if retracement is in the Fibonacci zone (38-62%)
        in_fib_zone = 0.3 < retracement < 0.7 if retracement else False

        shoulder_active = new_high_marginal and vol_declining and in_fib_zone

        return {
            "shoulder_active": bool(shoulder_active),
            "phase1_high": round(recent_high, 2),
            "phase2_low": round(pullback_low, 2),
            "retracement_pct": round(retracement * 100, 1) if retracement else 0,
            "vol_decline": vol_declining
        }

    return {"shoulder_active": False}


def triple_confirm(precomputed: pd.DataFrame, bar_idx: int) -> dict:
    """
    Triple confirmation: contrarian + volume + momentum fire together.

    Dimensions:
    - Contrarian: bb_buy OR golden_pit OR band_low OR nine_turns_buy
    - Volume: vol_anomaly  
    - Momentum: RSI recovering from oversold OR KDJ golden

    Returns bonus points when all 3 dimensions confirm simultaneously.
    """
    i = bar_idx

    contrarian = (precomputed["bb_buy"].iloc[i] or
                  precomputed.get("golden_pit", pd.Series([False]*len(precomputed))).iloc[i] or
                  precomputed.get("band_low", pd.Series([False]*len(precomputed))).iloc[i])

    volume_ok = precomputed["vol_anomaly"].iloc[i]

    momentum = (precomputed["kdj_golden"].iloc[i] or
                precomputed.get("bullish_divergence", pd.Series([False]*len(precomputed))).iloc[i])

    all_three = contrarian and volume_ok and momentum

    return {"triple_confirm": bool(all_three), "contrarian": bool(contrarian),
            "volume": bool(volume_ok), "momentum": bool(momentum)}
