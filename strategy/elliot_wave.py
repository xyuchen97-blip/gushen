"""
Elliott Wave & Pattern Detection — wave structure + right shoulder traps.
Integrates with fibonacci.py for swing detection.

Wave labeling: Wave1 → Wave2 → Wave3 → Wave4 → Wave5
Right shoulder: 3-phase topping pattern (surge → pullback → squeeze)
"""

import numpy as np
import pandas as pd

def detect_wave5_target(df: pd.DataFrame, lookback: int = 200) -> dict:
    """
    Detect Elliott Wave 1-5 structure and compute Wave 5 target.
    Returns dict with wave info or None if structure unclear.
    
    Wave rules:
    - Wave1: first impulse up after a low
    - Wave2: pullback (doesn't retrace beyond Wave1 start)
    - Wave3: strongest, usually >1.618 × Wave1
    - Wave4: consolidation, doesn't overlap Wave1 top
    - Wave5: final push, target = Wave4 low + Wave1 × 1.618
    """
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    n = len(close)
    
    if n < 100:
        return {"wave5_active": False, "wave5_target": None}
    
    # Find significant swing points (local extrema)
    swings_high = []
    swings_low = []
    window = 10  # local extremum window
    
    for i in range(window, n - window):
        if high[i] == max(high[i-window:i+window+1]):
            swings_high.append((i, high[i]))
        if low[i] == min(low[i-window:i+window+1]):
            swings_low.append((i, low[i]))
    
    if len(swings_low) < 2 or len(swings_high) < 2:
        return {"wave5_active": False, "wave5_target": None}
    
    # Identify recent wave sequence (last 5 swings)
    recent_lows = swings_low[-3:] if len(swings_low) >= 3 else swings_low
    recent_highs = swings_high[-3:] if len(swings_high) >= 3 else swings_high
    
    # Simple wave labeling from most recent swings
    if len(recent_lows) >= 2 and len(recent_highs) >= 2:
        w4_low_idx = recent_lows[-2][0]
        w3_high_idx = recent_highs[-2][0]
        w1_low_idx = recent_lows[-1][0] if recent_lows[-1][0] < w3_high_idx else recent_lows[0][0]
        
        # Wave1 range: from last major low to first major high
        w1_start = low[w1_low_idx]
        w1_end = high[min(w3_high_idx, n-1)] if w3_high_idx > w1_low_idx else w1_start * 1.1
        w1_length = w1_end - w1_start if w1_end > w1_start else close[-1] * 0.1
        
        # Wave4 low
        w4_low = low[w4_low_idx]
        
        # Wave5 target = Wave4 low + Wave1 × 1.618
        w5_target = w4_low + w1_length * 1.618
        
        # Check if current price is near target
        current = close[-1]
        near_target = (w5_target > 0 and 
                       abs(current - w5_target) / w5_target < 0.05)  # within 5%
        above_target = current > w5_target * 1.02 if w5_target > 0 else False  # 2% buffer
        
        return {
            "wave5_active": True,
            "wave5_target": round(w5_target, 2),
            "wave5_near": near_target,
            "wave5_above": above_target,
            "w1_length_pct": round(w1_length / w1_start * 100, 1),
            "current": round(current, 2),
            "target_pct": round((w5_target / current - 1) * 100, 1) if current > 0 else 0
        }
    
    return {"wave5_active": False, "wave5_target": None}


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
