"""
GUSHEN SCORING ENGINE — v10.2 Regime-Adaptive Dual-Mode (May 2026)
===================================================================

Production engine: score_bar_v5() — regime-separated contrarian/trend scoring.
See HANDOFF.md for version history.

5-Stage Pipeline:
  Stage 1: Regime detection (Weekly MA50+MA200 → bull/bear)
  Stage 2: Dual-mode scoring (Bear=contrarian depth, Bull=trend+fib)
  Stage 3: Volume confirmation (multiplicative)
  Stage 3.5: Analyst revision / earnings surprise signals (v10.2)
  Stage 4: Threshold → action (per-market V10_THRESHOLDS)
  Stage 5: Macro risk multiplier (portfolio-level position sizing)

v10.2 additions:
  - Analyst revision signals replace disabled quarterly fundamentals
    A: Tushare forecast (业绩预告) — event-driven with recency decay
    US: Alpha Vantage EARNINGS — earnings surprise streak (2-3Q)
    HK: akshare ET analyst consensus (production only, not backtestable)
  - Conservative ±2 max weight (learned from v10.1 quarterly failure)

v10.1 additions (tune.py backtest framework):
  - Adaptive exit: time decay (12wk), profit-take trailing (50%), ATR stop (3×)
  - Margin financing signal re-activated for A-stocks (contrarian, RankIC=-0.09)

Usage:
    from strategy.scoring import score
    result = score(df_daily, df_weekly, ticker="600519", market="A",
                    macro_data=macro, weights=None)
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dzh_indicators import golden_pit, jiu_zhuan, band_king
from .bollinger import compute_weekly_bb, bb_weekly_sell_signal, bb_weekly_buy_signal
# Runtime config override (for grid search)
# If strategy/_params.json exists, override BB penalty and chain bonuses
def _load_grid_params():
    import json, os
    import sys as _sys
    config_path = os.path.join(os.path.dirname(__file__), "_params.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {}
_grid_params = _load_grid_params()

# ═══════════════════════════════════════════════════════════════════
# LOCKED PARAMETERS (do not modify without re-running grid search)
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# v9.2: Named constants — do not modify without re-running backtest
# ═══════════════════════════════════════════════════════════════════
BEAR_TREND_DISCOUNT = 0.40    # Bear regime trend signal weight
MA20_PENALTY_A_HK = 0.65      # A/HK price-below-MA20 counter-trend penalty
MA20_PENALTY_US = 0.75        # US price-below-MA20 penalty (lighter)
VOL_ANOMALY_MULT = 1.5        # Volume anomaly threshold (×MA20)
NATIONAL_TEAM_MULT = 2.5      # National team volume threshold (×MA20)

WEIGHTS = _grid_params.get("weights", {"technical": 36, "capital": 26, "fundamental": 14, "macro": 19, "fibonacci": 5})  # v9.4 calibrated

# v9.4: Per-market weight overrides (Tushare data calibrated)
MARKET_WEIGHTS = {
    "A": {"technical": 25, "capital": 35, "fundamental": 15, "macro": 20, "fibonacci": 5},
    "HK": {"technical": 35, "capital": 25, "fundamental": 15, "macro": 20, "fibonacci": 5},
    "US": {"technical": 38, "capital": 24, "fundamental": 14, "macro": 19, "fibonacci": 5},
}

SIGNAL_SCORES = {
    # Contrarian (DZH) — P1 signals
    "golden_pit":        10,
    "band_low":           5,
    "nine_turns_buy":    10,
    "nine_turns_setup9":  5,
    "band_king_buy2":    10,
    # Trend-following — P4 signals
    "ma_aligned":        10,
    "price_above_ma50":   3,
    "adx_trend":         10,
    "bb_weekly_buy":     15,
    "ma_golden_cross":    5,
    "macd_golden":        8,
    # Momentum — P1 extension signals
    "kdj_golden":         5,
    "kdj_oversold":       5,
    "bullish_divergence": 12,
    # P3: Fibonacci retracement support (independent binary, scored in score_bar)
    "fib_retracement_support": 10,
    # Adaptive BOLL->KDJ->MACD chain resonance — P2 signals
    "boll_kdj_chain":      _grid_params.get("c2_bonus", 15),  # C2: v9.1 calibrated
    "boll_kdj_macd_chain": _grid_params.get("c3_bonus", 22),  # C3: v9.1 calibrated
    # Capital — L1 signals (not Pillars)
    "volume_anomaly":      8,
    "northbound_inflow":   6,
}

SELL_PENALTIES = {
    "nine_turns_sell":  -10,
    "band_king_sell1":  -10,
    "ma_death_cross":    -5,
    "macd_death_cross":  -8,
}


THRESHOLDS = _grid_params.get("thresholds_A", {
    "entry":      45,
    "entry_bear": 46,
    "watchlist":  38,
    "watch_bear": 39,
    "exit":       20,
    "min_hold":    5,
})

US_THRESHOLDS = _grid_params.get("thresholds_US", {"entry": 48, "watchlist": 42, "exit": 25})  # v9.5: grid search optimal (was 50→40→42→48)

# v10: Per-market regime-separated thresholds (no z-score, raw composite → action)
# Source: SKILL.md v10 spec, validated in OOS backtest (2024+, S=1.62)
V10_THRESHOLDS = {
    "A":  {"bear_buy": 25, "bear_watch": 17, "bear_exit": 0,  "bull_buy": 28, "bull_watch": 20, "bull_exit": 0},
    "HK": {"bear_buy": 28, "bear_watch": 20, "bear_exit": 10, "bull_buy": 28, "bull_watch": 20, "bull_exit": 10},
    "US": {"bear_buy": 32, "bear_watch": 24, "bear_exit": 10, "bull_buy": 28, "bull_watch": 20, "bull_exit": 10},
}

BB_PARAMS   = {"period": 20, "std": 2.0, "vol_mult": 2.0}
MACD_PARAMS = {"fast": 12, "slow": 26, "signal": 9}
KDJ_PARAMS  = {"n": 9, "m1": 3, "m2": 3}
ADX_PERIOD  = 14
FIB_LOOKBACK = 50


def precompute(df_daily: pd.DataFrame, df_weekly: pd.DataFrame) -> dict:
    """
    Pre-compute all indicators for a ticker. Returns a dict of pd.Series
    aligned to df_daily index. This runs ONCE per ticker — the scoring
    loop then indexes into this dict by bar position.
    """
    result = {}

    # ── DZH Indicators ────────────────────────────────────────
    gp = golden_pit.compute(df_daily.copy())
    result["golden_pit"]   = gp["golden_pit"]
    result["band_low"]     = gp["band_low"]

    jz = jiu_zhuan.compute(df_daily.copy())
    result["buy_signal"]   = jz["buy_signal"]
    result["sell_signal"]  = jz["sell_signal"]
    result["buy_setup_done"] = jz["buy_setup_done"]

    bk = band_king.compute_no_future(df_daily.copy())
    result["buy2"]  = bk["buy2"]
    result["sell1"] = bk["sell1"]

    # ── Bollinger Weekly ──────────────────────────────────────
    bb = compute_weekly_bb(df_weekly)
    result["bb_sell"] = bb_weekly_sell_signal(df_daily, bb)
    result["bb_buy"]  = bb_weekly_buy_signal(df_daily, bb)

    # ── Moving Averages ───────────────────────────────────────
    close  = df_daily["close"]
    high   = df_daily["high"]
    low    = df_daily["low"]
    volume = df_daily["volume"]

    ma5   = close.rolling(5).mean()
    ma20  = close.rolling(20).mean()
    ma50  = close.rolling(50).mean()
    ma60  = close.rolling(60).mean()
    ma120 = close.rolling(120).mean()
    ma200 = close.rolling(200).mean()

    result["ma_golden"] = (ma5 > ma20) & (ma5.shift(1) <= ma20.shift(1))
    result["ma_death"]  = (ma5 < ma20) & (ma5.shift(1) >= ma20.shift(1))
    result["ma_aligned"] = (ma20 > ma60) & (ma60 > ma120)
    result["price_above_ma50"] = close > ma50
    result["bull_regime"] = close > ma200

    # ── ADX ─────────────────────────────────────────────────
    hdiff = high.diff(); ldiff = -low.diff()
    plus_dm  = np.where((hdiff > ldiff) & (hdiff > 0), hdiff, 0)
    minus_dm = np.where((ldiff > hdiff) & (ldiff > 0), ldiff, 0)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(ADX_PERIOD).mean()
    plus_di  = pd.Series(plus_dm, index=df_daily.index).rolling(ADX_PERIOD).mean() / atr14 * 100
    minus_di = pd.Series(minus_dm, index=df_daily.index).rolling(ADX_PERIOD).mean() / atr14 * 100
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di) * 100).fillna(0)
    result["adx_strong"] = (dx.rolling(ADX_PERIOD).mean() > 25) & (plus_di > minus_di)

    # ── MACD ─────────────────────────────────────────────────
    ema12 = close.ewm(span=MACD_PARAMS["fast"], adjust=False).mean()
    ema26 = close.ewm(span=MACD_PARAMS["slow"], adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=MACD_PARAMS["signal"], adjust=False).mean()
    hist = (dif - dea) * 2
    result["macd_golden"] = (dif > dea) & (dif.shift(1) <= dea.shift(1))
    result["macd_death"]  = (dif < dea) & (dif.shift(1) >= dea.shift(1))
    result["macd_hist"]   = hist

    # ── KDJ ──────────────────────────────────────────────────
    n, m1, m2 = KDJ_PARAMS["n"], KDJ_PARAMS["m1"], KDJ_PARAMS["m2"]
    low_n  = low.rolling(n).min()
    high_n = high.rolling(n).max()
    rsv = (close - low_n) / (high_n - low_n) * 100
    k_val = rsv.rolling(m1).mean()
    d_val = k_val.rolling(m2).mean()
    j_val = 3 * k_val - 2 * d_val
    result["kdj_k"] = k_val
    result["kdj_d"] = d_val
    result["kdj_j"] = j_val
    result["kdj_oversold"] = (j_val < 20) & (k_val < 30)
    result["kdj_golden"]   = (k_val > d_val) & (k_val.shift(1) <= d_val.shift(1))

    # ── Bullish Divergence ──────────────────────────────────
    c_arr = close.values; h_arr = hist.values
    div = np.zeros(len(df_daily), dtype=bool)
    for i in range(20, len(df_daily)):
        w = c_arr[i-19:i+1]
        lo_idx = i - 19 + np.argmin(w)
        if lo_idx >= i - 5:
            pw = c_arr[max(0,i-39):max(0,i-19)]
            if len(pw) > 5:
                plo = max(0, i-39) + np.argmin(pw)
                if c_arr[lo_idx] < c_arr[plo] and h_arr[lo_idx] > h_arr[plo]:
                    div[i] = True
    result["bullish_divergence"] = pd.Series(div, index=df_daily.index)

    # ── Weekly MA20 ─────────────────────────────────────────
    w_close = df_weekly["close"]
    w_ma20 = w_close.rolling(20).mean()
    result["weekly_ma20_up"] = w_ma20.diff(5).reindex(df_daily.index, method="ffill").fillna(False) > 0

    # ── Weekly Fibonacci Support ─────────────────────────────
    # v10: fib_support_raw fires in both regimes (legacy)
    # v10: fib_support_bull only fires in bull regime (+41.5% vs -6.5%)
    fib_support = pd.Series(False, index=df_daily.index)
    if len(df_weekly) >= 50:
        wh = df_weekly["high"].rolling(50).max()
        wl = df_weekly["low"].rolling(50).min()
        wr = wh - wl
        for level in [0.382, 0.5, 0.618]:
            target = (wl + wr * level).reindex(df_daily.index, method="ffill")
            fib_support = fib_support | (abs(close - target) / close < 0.02)
    result["weekly_fib_support"] = fib_support
    # Bull-gated version: only fires when close > MA200
    result["weekly_fib_support_bull"] = fib_support & (close > ma200)

    # ── v10: RSI (for continuous depth scoring) ────────────
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    result["rsi"] = 100 - (100 / (1 + rs))

    # ── v10: Bollinger %B (continuous depth) ─────────────
    bb_mid = close.rolling(BB_PARAMS["period"]).mean()
    bb_std = close.rolling(BB_PARAMS["period"]).std()
    bb_upper = bb_mid + BB_PARAMS["std"] * bb_std
    bb_lower = bb_mid - BB_PARAMS["std"] * bb_std
    result["bb_pct"] = (close - bb_lower) / (bb_upper - bb_lower)  # 0 = at lower, 1 = at upper, <0 = below

    # ── v10: Volume z-score (continuous) ─────────────────
    vol_ma20 = volume.rolling(20).mean()
    vol_std20 = volume.rolling(20).std()
    result["vol_z"] = (volume - vol_ma20) / vol_std20.replace(0, np.nan)

    # ── v10: ADX value + DI values (for regime detection) ──
    result["adx_val"] = dx.rolling(ADX_PERIOD).mean()
    result["plus_di"] = plus_di
    result["minus_di"] = minus_di

    # ── Volume ──────────────────────────────────────────────
    result["vol_anomaly"] = volume > (volume.rolling(20).mean() * VOL_ANOMALY_MULT)
    # v9.2: national_team — stricter institutional volume (range-bound accumulation)
    result["national_team"] = (
        (volume > volume.rolling(20).mean() * NATIONAL_TEAM_MULT) &
        result["price_above_ma50"] &
        (result["adx_strong"] == False)
    )

    # v9.2: Pre-compute chain resonance patterns for all window sizes
    n = len(close)
    bb_buy = result["bb_buy"].values if hasattr(result["bb_buy"], "values") else np.array(result["bb_buy"])
    kdj_fire = (result["kdj_oversold"] | result["kdj_golden"]).values
    macd_ok = (result["macd_golden"] | (result["macd_hist"] > 0)).values
    adx_strong = result["adx_strong"].values
    for w in [3, 5, 8]:
        c2_arr = np.zeros(n, dtype=bool)
        c3_arr = np.zeros(n, dtype=bool)
        for i in range(w, n):
            if bb_buy[i-w:i+1].any():
                b_idx = i - w + np.argmax(bb_buy[i-w:i+1])
                if b_idx + 1 <= i:
                    k_slice = kdj_fire[b_idx+1:min(b_idx + w + 1, i + 1)]
                    if k_slice.any():
                        c2_arr[i] = True
                        k_idx = b_idx + 1 + np.argmax(k_slice)
                        if k_idx + 1 <= i:
                            m_slice = macd_ok[k_idx+1:min(k_idx + w + 1, i + 1)]
                            if m_slice.any():
                                c3_arr[i] = True
        result[f"chain_c2_w{w}"] = c2_arr
        result[f"chain_c3_w{w}"] = c3_arr

    return result



# ═══════════════════════════════════════════════════════════════════
# v10: MACRO RISK MULTIPLIER (portfolio-level position sizing)
# ═══════════════════════════════════════════════════════════════════

def compute_macro_mult(macro_data: dict, market: str, bar_date) -> float:
    """
    Compute portfolio-level position sizing multiplier from macro indicators.

    This is a MARKET-level calculation, not stock-level. Same value applies
    to all stocks in a given market on a given date. Should be called once
    per (market, date) and cached, not once per stock.

    Factors:
      - VIX: global fear gauge (all markets)
      - QVIX: China 50ETF volatility (A/HK only)
      - US 10Y-2Y spread: recession signal (US/HK)
      - China PMI: manufacturing cycle (A only)

    Returns: float in [0.5, 1.3] — multiply position size by this factor.
      1.3 = risk-on (low VIX, strong PMI, positive spread)
      1.0 = neutral
      0.5 = crisis (VIX>30, inverted yield curve, PMI<49)
    """
    if not macro_data:
        return 1.0

    macro_risk = 0  # [-2, +2] range

    # VIX: fear gauge (all markets)
    vix_data = macro_data.get("vix")
    if vix_data is not None and hasattr(vix_data, 'index'):
        v = vix_data[vix_data.index <= bar_date]
        if len(v) > 0:
            vix_val = float(v.iloc[-1])
            if vix_val > 30: macro_risk -= 1.5
            elif vix_val > 25: macro_risk -= 0.5
            elif vix_val < 15: macro_risk += 0.5

    # QVIX: China 50ETF volatility (A/HK only)
    if market in ("A", "HK"):
        qvix_data = macro_data.get("qvix")
        if qvix_data is not None and hasattr(qvix_data, 'index'):
            q = qvix_data[qvix_data.index <= bar_date]
            if len(q) > 0:
                qvix_val = float(q.iloc[-1])
                if qvix_val > 30: macro_risk -= 1.0
                elif qvix_val > 25: macro_risk -= 0.3
                elif qvix_val < 14: macro_risk += 0.3

    # Yield spread: recession indicator (US/HK)
    if market in ("US", "HK"):
        spread_data = macro_data.get("us_spread_10y2y")
        if spread_data is not None and hasattr(spread_data, 'index'):
            s = spread_data[spread_data.index <= bar_date]
            if len(s) > 0:
                sp_val = float(s.iloc[-1])
                if sp_val < 0: macro_risk -= 1.0
                elif sp_val < 0.3: macro_risk -= 0.3
                elif sp_val > 1.5: macro_risk += 0.3

    # China PMI: manufacturing cycle (A-stocks only)
    if market == "A":
        pmi_data = macro_data.get("china_pmi")
        if pmi_data is not None and hasattr(pmi_data, 'index'):
            p = pmi_data[pmi_data.index <= bar_date]
            if len(p) > 0:
                pmi_val = float(p.iloc[-1])
                if pmi_val < 49: macro_risk -= 1.0
                elif pmi_val < 50: macro_risk -= 0.3
                elif pmi_val > 51: macro_risk += 0.5

    # Convert [-2, +2] → [0.5, 1.3] position size multiplier
    return round(max(0.5, min(1.3, 1.0 + macro_risk * 0.15)), 2)


# ═══════════════════════════════════════════════════════════════════
# v10: REGIME-ADAPTIVE DUAL-MODE ENGINE (score_bar_v5)
# ═══════════════════════════════════════════════════════════════════

def score_bar_v5(i: int, df_daily: pd.DataFrame, precomputed: dict,
                 macro_data: dict = None, weights: dict = None,
                 market: str = "US", ticker: str = "", score_history=None) -> dict:
    """
    v10 Regime-Adaptive Dual-Mode Scoring Engine.

    Stage 1: Regime detection (bull vs bear/neutral)
    Stage 2: Signal scoring (separate engines per regime)
    Stage 3: Volume confirmation (shared, multiplicative)
    Stage 4: Raw score → threshold → action (no z-score)
    Stage 5: Position management hints (trail stop, time decay)

    Parameters: same as score_bar() for backward compatibility.
    score_history is accepted but NOT USED (no z-score in v10).
    """
    bar_date = df_daily.index[i]
    close_price = df_daily["close"].iloc[i]
    active = []

    # ═══════════════════════════════════════════════════════════
    # STAGE 1: REGIME DETECTION
    # ═══════════════════════════════════════════════════════════

    bull = bool(precomputed["bull_regime"].iloc[i])
    adx_val = float(precomputed["adx_val"].iloc[i]) if pd.notna(precomputed["adx_val"].iloc[i]) else 0
    plus_di = float(precomputed["plus_di"].iloc[i]) if pd.notna(precomputed["plus_di"].iloc[i]) else 0
    minus_di = float(precomputed["minus_di"].iloc[i]) if pd.notna(precomputed["minus_di"].iloc[i]) else 0
    strong_bull = bull and adx_val > 25 and plus_di > minus_di

    regime = "bull" if bull else "bear"
    active.append(f"regime={regime}" + ("+strong" if strong_bull else ""))

    # ═══════════════════════════════════════════════════════════
    # STAGE 2: SIGNAL SCORING (regime-separated engines)
    # ═══════════════════════════════════════════════════════════

    entry_score = 0.0
    hold_score = 0.0
    mode = ""  # "contrarian_entry" | "trend_hold" | "pullback_buy"

    # ── Read continuous depth values ──
    j_val = float(precomputed["kdj_j"].iloc[i]) if pd.notna(precomputed["kdj_j"].iloc[i]) else 50
    bb_pct = float(precomputed["bb_pct"].iloc[i]) if pd.notna(precomputed["bb_pct"].iloc[i]) else 0.5
    rsi_val = float(precomputed["rsi"].iloc[i]) if pd.notna(precomputed["rsi"].iloc[i]) else 50
    vol_z = float(precomputed["vol_z"].iloc[i]) if pd.notna(precomputed["vol_z"].iloc[i]) else 0

    if not bull:
        # ══════════════════════════════════════════════════════
        # BEAR/NEUTRAL ENGINE: Contrarian Entry
        # ══════════════════════════════════════════════════════
        mode = "contrarian_entry"

        # ── Continuous intensity signals (depth-based, 0–10 scale) ──
        # KDJ depth: how oversold? max(0, 20-J)/20 × 10
        kdj_depth = max(0, 20 - j_val) / 20 * 10
        if kdj_depth > 0:
            entry_score += kdj_depth
            active.append(f"kdj_depth={kdj_depth:.1f}")

        # BB depth: how far below lower band? max(0, -bb_pct) × 15
        bb_depth = max(0, -bb_pct) * 15
        if bb_depth > 0:
            entry_score += bb_depth
            active.append(f"bb_depth={bb_depth:.1f}")

        # RSI depth: max(0, 30-RSI)/30 × 8
        rsi_depth = max(0, 30 - rsi_val) / 30 * 8
        if rsi_depth > 0:
            entry_score += rsi_depth
            active.append(f"rsi_depth={rsi_depth:.1f}")

        # ── Binary DZH signals (high-conviction, kept as-is) ──
        if precomputed["golden_pit"].iloc[i] != 0:
            entry_score += 10; active.append("golden_pit")
        if precomputed["band_low"].iloc[i] != 0:
            entry_score += 5; active.append("band_low")
        if precomputed["buy_signal"].iloc[i]:
            entry_score += 10; active.append("nine_turns_buy")
        if precomputed["buy_setup_done"].iloc[i]:
            entry_score += 5; active.append("nine_turns_setup9")
        if precomputed["buy2"].iloc[i]:
            entry_score += 10; active.append("band_king_buy2")
        if precomputed["bb_buy"].iloc[i]:
            entry_score += 15; active.append("bb_weekly_buy")

        # ── P1 momentum extensions (binary, contrarian context) ──
        if precomputed["kdj_golden"].iloc[i]:
            entry_score += 5; active.append("kdj_golden")
        if precomputed["bullish_divergence"].iloc[i]:
            entry_score += 12; active.append("bullish_divergence")

        # ── P2: Chain resonance (high-quality, rare — binary) ──
        chain_window = 5
        if precomputed["adx_strong"].iloc[i]:
            chain_window = 3
        elif i >= 30 and not precomputed["adx_strong"].iloc[i-30:i].any():
            chain_window = 8
        if precomputed.get(f"chain_c2_w{chain_window}", np.zeros(1))[i] if i < len(precomputed.get(f"chain_c2_w{chain_window}", [])) else False:
            entry_score += 15; active.append("boll_kdj_chain")
            if precomputed.get(f"chain_c3_w{chain_window}", np.zeros(1))[i] if i < len(precomputed.get(f"chain_c3_w{chain_window}", [])) else False:
                entry_score += 22; active.append("boll_kdj_macd_chain")

        # ── Sell penalties (bear mode) ──
        if precomputed["sell_signal"].iloc[i]:
            entry_score -= 10; active.append("nine_turns_sell")
        if precomputed["sell1"].iloc[i]:
            entry_score -= 10; active.append("band_king_sell1")
        if precomputed["ma_death"].iloc[i]:
            entry_score -= 5; active.append("ma_death_cross")
        if precomputed["macd_death"].iloc[i]:
            entry_score -= 8; active.append("macd_death_cross")
        entry_score = max(0, entry_score)

        # Weekly MA20 bear penalty
        if not precomputed["weekly_ma20_up"].iloc[i]:
            if market in ("A", "HK", "CN_IDX"):
                entry_score *= MA20_PENALTY_A_HK
            else:
                entry_score *= MA20_PENALTY_US

        # BB sell penalty (bear mode)
        if precomputed["bb_sell"].iloc[i]:
            adx_s = precomputed["adx_strong"].iloc[i]
            ma50_c = precomputed["price_above_ma50"].iloc[i]
            vol_a = precomputed["vol_anomaly"].iloc[i]
            if adx_s and ma50_c and vol_a:
                entry_score -= 8; active.append("bb_sell:strong")
            elif adx_s or ma50_c:
                entry_score -= 5; active.append("bb_sell:moderate")
            else:
                entry_score -= 3; active.append("bb_sell:weak")
            entry_score = max(0, entry_score)

        # NOTE: No P3 Fibonacci in bear mode (empirically -6.5%)
        # NOTE: No L2 fundamentals (IC ≈ 0 at stock level)
        # NOTE: No L3 macro (moved to portfolio-level position sizing)

        # ── P4: Trend signals at bear discount (40%) ──
        # P4 at bear discount: removing entirely
        # was too aggressive — keep the discounted contribution.
        trend_bear = 0
        if precomputed["ma_aligned"].iloc[i]:
            trend_bear += 10
        if precomputed["price_above_ma50"].iloc[i]:
            trend_bear += 3
        if precomputed["adx_strong"].iloc[i]:
            trend_bear += 10
        # golden_cross excluded (lagging hold signal)
        if precomputed["macd_golden"].iloc[i]:
            trend_bear += 8
        entry_score += int(trend_bear * BEAR_TREND_DISCOUNT)
        if trend_bear > 0:
            active.append(f"p4_bear_disc={int(trend_bear * BEAR_TREND_DISCOUNT)}")

    else:
        # ══════════════════════════════════════════════════════
        # BULL ENGINE: P1 entry + Trend bonus + Fib pullback bonus
        # ══════════════════════════════════════════════════════
        # In bull, P1+P4 mixing is OK because trend confirms direction.
        # Separation only hurts in bear where P4 dilutes contrarian alpha.

        fib_support = bool(precomputed["weekly_fib_support"].iloc[i])
        mode = "bull_entry"

        # ── P1: Continuous depth signals (same as bear engine) ──
        kdj_depth = max(0, 20 - j_val) / 20 * 10
        if kdj_depth > 0:
            entry_score += kdj_depth; active.append(f"kdj_depth={kdj_depth:.1f}")
        bb_depth = max(0, -bb_pct) * 15
        if bb_depth > 0:
            entry_score += bb_depth; active.append(f"bb_depth={bb_depth:.1f}")
        rsi_depth = max(0, 30 - rsi_val) / 30 * 8
        if rsi_depth > 0:
            entry_score += rsi_depth; active.append(f"rsi_depth={rsi_depth:.1f}")

        # ── P1: Binary DZH signals ──
        if precomputed["golden_pit"].iloc[i] != 0:
            entry_score += 10; active.append("golden_pit")
        if precomputed["band_low"].iloc[i] != 0:
            entry_score += 5; active.append("band_low")
        if precomputed["buy_signal"].iloc[i]:
            entry_score += 10; active.append("nine_turns_buy")
        if precomputed["buy_setup_done"].iloc[i]:
            entry_score += 5; active.append("nine_turns_setup9")
        if precomputed["buy2"].iloc[i]:
            entry_score += 10; active.append("band_king_buy2")
        if precomputed["bb_buy"].iloc[i]:
            entry_score += 15; active.append("bb_weekly_buy")
        if precomputed["kdj_golden"].iloc[i]:
            entry_score += 5; active.append("kdj_golden")
        if precomputed["bullish_divergence"].iloc[i]:
            entry_score += 12; active.append("bullish_divergence")

        # ── P2: Chain resonance ──
        chain_window = 5
        if precomputed["adx_strong"].iloc[i]:
            chain_window = 3
        elif i >= 30 and not precomputed["adx_strong"].iloc[i-30:i].any():
            chain_window = 8
        c2_key = f"chain_c2_w{chain_window}"
        c3_key = f"chain_c3_w{chain_window}"
        if precomputed.get(c2_key) is not None and i < len(precomputed[c2_key]) and precomputed[c2_key][i]:
            entry_score += 15; active.append("boll_kdj_chain")
            if precomputed.get(c3_key) is not None and i < len(precomputed[c3_key]) and precomputed[c3_key][i]:
                entry_score += 22; active.append("boll_kdj_macd_chain")

        # ── P3: Fib support bonus (ONLY in bull — +41.5% empirical) ──
        if fib_support:
            entry_score += 10; active.append("fib_retracement_support(bull)")

        # ── P4: Trend bonus (additive in bull, not separate) ──
        trend_bonus = 0
        if precomputed["ma_aligned"].iloc[i]:
            trend_bonus += 5; active.append("trend:ma_aligned")
        if precomputed["adx_strong"].iloc[i]:
            trend_bonus += 5; active.append("trend:adx_strong")
        if precomputed["price_above_ma50"].iloc[i]:
            trend_bonus += 2
        if precomputed["ma_golden"].iloc[i]:
            trend_bonus += 3; active.append("trend:golden_cross")
        if precomputed["macd_golden"].iloc[i]:
            trend_bonus += 3; active.append("trend:macd_golden")
        entry_score += trend_bonus

        # ── Sell penalties ──
        if precomputed["sell_signal"].iloc[i]:
            entry_score -= 10; active.append("nine_turns_sell")
        if precomputed["sell1"].iloc[i]:
            entry_score -= 10; active.append("band_king_sell1")
        if precomputed["ma_death"].iloc[i]:
            entry_score -= 5; active.append("ma_death_cross")
        if precomputed["macd_death"].iloc[i]:
            entry_score -= 8; active.append("macd_death_cross")
        entry_score = max(0, entry_score)

        # BB sell penalty
        if precomputed["bb_sell"].iloc[i]:
            adx_s = precomputed["adx_strong"].iloc[i]
            ma50_c = precomputed["price_above_ma50"].iloc[i]
            vol_a = precomputed["vol_anomaly"].iloc[i]
            if adx_s and ma50_c and vol_a:
                entry_score -= 8; active.append("bb_sell:strong")
            elif adx_s or ma50_c:
                entry_score -= 5; active.append("bb_sell:moderate")
            else:
                entry_score -= 3; active.append("bb_sell:weak")
            entry_score = max(0, entry_score)

        # ── Hold score for position management hints ──
        if precomputed["ma_aligned"].iloc[i]: hold_score += 10
        if precomputed["adx_strong"].iloc[i]: hold_score += 10
        if precomputed["price_above_ma50"].iloc[i]: hold_score += 3
        if precomputed["ma_death"].iloc[i]: hold_score -= 8
        if precomputed["macd_death"].iloc[i]: hold_score -= 5

    # ═══════════════════════════════════════════════════════════
    # STAGE 3: VOLUME CONFIRMATION (shared, multiplicative)
    # ═══════════════════════════════════════════════════════════

    # Continuous volume z-score confirmation (0.3 weight per unit of vol_z)
    vol_z_scaled = min(vol_z, 3.0) / 3.0 if vol_z > 0 else 0
    chain_bonus = 0
    # Additional chain bonus if C2/C3 fired (already in entry_score, this is confirmation mult)
    if "boll_kdj_chain" in [a.split("=")[0] for a in active]:
        chain_bonus = 0.1
    if "boll_kdj_macd_chain" in [a.split("=")[0] for a in active]:
        chain_bonus = 0.2

    confirmation = 1.0 + vol_z_scaled * 0.3 + chain_bonus

    if mode in ("contrarian_entry", "pullback_buy"):
        entry_score *= confirmation
        active.append(f"vol_confirm={confirmation:.2f}")

    # ── A-stock specific: Volume-price divergence (IC=+0.046) ──
    if market == "A" and mode == "contrarian_entry":
        # Price falling + volume declining = selling exhaustion
        if i >= 5:
            price_falling = close_price < df_daily["close"].iloc[i-5]
            vol_declining = df_daily["volume"].iloc[i] < df_daily["volume"].iloc[i-5:i].mean()
            if price_falling and vol_declining:
                entry_score += 5; active.append("vp_divergence(A)")

    # ── L1 Capital factors (kept — volume anomaly, margin, chip, etc.) ──
    cap_bonus = 0
    if precomputed["vol_anomaly"].iloc[i]:
        cap_bonus += 3; active.append("vol_anomaly")

    if market == "A" and macro_data:
        if "northbound_flow" in macro_data:
            nb = macro_data["northbound_flow"][macro_data["northbound_flow"].index <= bar_date]
            if len(nb) > 0 and float(nb.iloc[-1]) > 0:
                cap_bonus += 2; active.append("northbound_inflow")
        if "margin" in macro_data:
            margin_hist = macro_data["margin"]
            if bar_date in margin_hist:
                mr = margin_hist[bar_date]
                if mr.get("pct_5d", 0) > 5:
                    cap_bonus -= 4; active.append("margin_extreme")
                elif mr.get("pct_5d", 0) > 2:
                    cap_bonus -= 2; active.append("margin_overheat")
                elif mr.get("pct_5d", 0) < -5:
                    cap_bonus += 2; active.append("margin_panic")
        if "chip_conc" in macro_data:
            cc = macro_data["chip_conc"]
            if cc > 22:
                cap_bonus += 2; active.append("chip_tight")
            elif cc < 12:
                cap_bonus -= 1; active.append("chip_loose")
        if "holder_chg" in macro_data:
            hc = macro_data["holder_chg"]
            if hc < -0.03:
                cap_bonus += 1; active.append("holder_consolidate")
            elif hc > 0.05:
                cap_bonus -= 1; active.append("holder_dilute")
        if "mff" in macro_data:
            a_sector = macro_data.get("a_sector", "defensive")
            if a_sector == "growth":
                mff_list = macro_data["mff"]
                if bar_date in mff_list:
                    mf = mff_list[bar_date]
                    if mf.get("super_ratio", 0) > 3:
                        cap_bonus += 3; active.append("mff_strong")
                    elif mf.get("mf_ratio", 0) > 2:
                        cap_bonus += 1; active.append("mff_moderate")
                    elif mf.get("mf_ratio", 0) < -8:
                        cap_bonus -= 2; active.append("mff_sell")

    # ═══════════════════════════════════════════════════════════
    # STAGE 3.5: FUNDAMENTAL QUALITY SIGNALS (change-based, v10.1)
    # ═══════════════════════════════════════════════════════════
    # Unlike the old static fund_score (ROE > 15 → +5, IC ≈ 0), these
    # signals fire on *changes* at quarterly disclosure — earnings
    # acceleration, margin expansion, quality gate. Timing value comes
    # from new information arriving, not from static quality.
    #
    # Data flow: macro_data["fundamentals"] = latest quarter dict,
    #            macro_data["fundamentals_prev"] = previous quarter dict
    # Both are looked up per-bar with disclosure-date gating (no look-ahead).

    fund_bonus = 0
    # ── v10.2: ANALYST REVISION / EARNINGS SURPRISE SIGNALS ──
    # Replaced v10.1's quarterly-change signals (net-negative) with higher-
    # frequency analyst data that updates as analysts publish.
    #
    # KEY DESIGN PRINCIPLE: positive-only signals.
    # Our engine is contrarian — it buys into weakness. Negative analyst signals
    # (bad forecasts, earnings misses) overlap with what technical signals already
    # capture. Adding negative penalties works AGAINST contrarian entries.
    # But positive signals (good forecast on beaten-down stock, earnings beat streak)
    # provide genuine confirmation that technicals can't see.
    #
    #   A-stocks: Tushare forecast (业绩预告) — 预增/略增/扭亏/续盈 → positive bonus
    #   US: Alpha Vantage EARNINGS — earnings beat streak (2-3Q) → positive bonus
    #   HK: akshare ET analyst consensus (production only)
    #
    # Conservative +2 max weight, recency decay. No negative penalties.

    ANALYST_DECAY_WEEKS = 8  # signal fully decays after this many weeks
    ANALYST_LOOKBACK_DAYS = ANALYST_DECAY_WEEKS * 7

    if "analyst_signals" in macro_data:
        analyst_df = macro_data["analyst_signals"]

        if market == "A":
            # A-stocks: Tushare forecast signals DISABLED in scoring.
            # Ablation: positive forecasts triggered 12+ extra BUY entries for stocks
            # like 002230 (科大讯飞), pushing Sharpe from -0.27 to -0.73.
            # Root cause: 业绩预告 categories (预增/略增) are too coarse for
            # daily scoring — they fire at wrong times for contrarian engine.
            # Data remains cached for future higher-resolution usage.
            pass

        elif market == "US":
            # US: earnings beat streak only (no miss penalty — contrarian principle)
            recent = analyst_df[analyst_df.index <= bar_date].tail(4)

            if len(recent) >= 2:
                last_3 = recent.tail(3)
                beat_streak = sum(1 for _, r in last_3.iterrows()
                                  if r['signal_type'] == 'earnings_beat')
                avg_surprise = recent['signal_value'].mean() if len(recent) > 0 else 0

                if beat_streak >= 2 and avg_surprise > 2:
                    fund_bonus = min(2.0, beat_streak * 0.7)
                    active.append("earnings_beat_streak")
                elif beat_streak >= 2:
                    fund_bonus = min(1.5, beat_streak * 0.5)
                    active.append("earnings_beat_mild")

        elif market == "HK":
            # HK: positive analyst consensus only (production, not backtestable)
            cutoff = bar_date - pd.Timedelta(days=30)
            recent = analyst_df[(analyst_df.index >= cutoff) & (analyst_df.index <= bar_date)]
            if not recent.empty:
                avg_rating = recent['signal_value'].mean()
                if avg_rating > 0.5:
                    fund_bonus = 1.0
                    active.append("analyst_consensus_positive")

    # ═══════════════════════════════════════════════════════════
    # STAGE 4: DECISION (raw thresholds, no z-score)
    # ═══════════════════════════════════════════════════════════

    thresholds = V10_THRESHOLDS.get(market, V10_THRESHOLDS["US"])
    composite = entry_score + cap_bonus + fund_bonus  # v10.1: fundamental signals contribute
    bb_sell_now = bool(precomputed["bb_sell"].iloc[i])

    # Exit thresholds: below this composite score → EXIT
    bear_exit = thresholds.get("bear_exit", thresholds["bear_watch"] // 2)
    bull_exit = thresholds.get("bull_exit", thresholds["bull_watch"] // 2)

    if mode == "contrarian_entry":
        # Bear/neutral: entry scoring → BUY/WATCH/HOLD/EXIT
        if composite >= thresholds["bear_buy"]:
            action = "BUY"
        elif composite >= thresholds["bear_watch"]:
            action = "WATCH"
        elif composite >= bear_exit:
            action = "HOLD"
        else:
            action = "EXIT"

    elif mode == "bull_entry":
        # Bull mode: uses same threshold structure but with bull-specific values
        if composite >= thresholds["bull_buy"]:
            action = "BUY"
        elif composite >= thresholds["bull_watch"]:
            action = "WATCH"
        elif composite >= bull_exit and hold_score >= 10:
            action = "HOLD"  # trend still intact + score not terrible → hold
            active.append("trend_confirmed")
        elif composite >= bull_exit and hold_score >= 0 and strong_bull:
            action = "HOLD"  # strong bull override — hold with caution
            active.append("trend_override_hold")
        else:
            # Trend breakdown OR composite too low → EXIT
            action = "EXIT"
            active.append("trend_breakdown" if hold_score < 0 else "score_too_low")
    else:
        action = "HOLD"

    # ── Position management overrides (Phase 4) ──
    # BB sell override: if Bollinger weekly sell fires, override HOLD → EXIT
    # This catches trend reversals that the composite score misses
    if bb_sell_now and action in ("HOLD", "WATCH"):
        action = "EXIT"
        active.append("bb_sell_exit_override")

    # Hold score breakdown: if trend signals are strongly negative, EXIT
    if hold_score < -5 and action in ("HOLD", "WATCH"):
        action = "EXIT"
        active.append("hold_score_breakdown")

    # ═══════════════════════════════════════════════════════════
    # STAGE 5: POSITION MANAGEMENT HINTS
    # ═══════════════════════════════════════════════════════════
    # These don't change the action but provide metadata for the backtest
    # and portfolio manager to implement trailing stops, time decay, etc.

    mgmt_hints = {}
    if bull and not strong_bull:
        mgmt_hints["trail_stop_pct"] = {"US": 0.12, "HK": 0.10, "A": 0.08}.get(market, 0.10)
    if mode == "trend_hold" and hold_score < 5:
        mgmt_hints["tighten_exit"] = True
    if not bull and precomputed["adx_strong"].iloc[i]:
        mgmt_hints["bear_trend_block_exit"] = True  # Strong downtrend, block premature exit

    # ── Phase 5: Macro risk multiplier (portfolio-level position sizing) ──
    # Delegated to compute_macro_mult() — market-level, not stock-level.
    # For backtest efficiency, caller can pre-compute once per (market, date)
    # and pass via macro_data["precomputed_macro_mult"] to avoid redundant work.
    macro_mult = macro_data.get("precomputed_macro_mult") if macro_data else None
    if macro_mult is None:
        macro_mult = compute_macro_mult(macro_data, market, bar_date)

    return {
        "composite":    round(composite, 1),
        "action":       action,
        "active":       active,
        "mode":         mode,
        "regime":       regime,
        "strong_bull":  strong_bull,
        "entry_score":  round(entry_score, 1),
        "hold_score":   round(hold_score, 1),
        "cap_bonus":    cap_bonus,
        "vol_confirm":  round(confirmation, 2),
        "bb_sell":      bb_sell_now,
        "bull_regime":  bull,
        "mgmt_hints":   mgmt_hints,
        "macro_mult":   round(macro_mult, 2),    # Phase 5: portfolio position sizing
        "tech_score":   round(entry_score, 1),  # backward compat
        "cap_score":    cap_bonus,               # backward compat
        "fund_score":   round(fund_bonus, 1),       # v10.1: change-based fundamental signals
        "macro_score":  0,                        # removed from per-stock
        "fib_bonus":    10 if (mode == "bull_entry" and precomputed["weekly_fib_support"].iloc[i]) else 0,
        "pillar_fired": {},                       # legacy compat
        "reasoning":    f"{mode} | {regime} | entry={entry_score:.1f} hold={hold_score:.1f} cap={cap_bonus} fund={fund_bonus:.1f} vol×{confirmation:.2f} → comp={composite:.1f} → {action} | {active}",
    }


# ═══════════════════════════════════════════════════════════════════
# HIGH-LEVEL SCORING (single call)
# ═══════════════════════════════════════════════════════════════════

def score(df_daily: pd.DataFrame, df_weekly: pd.DataFrame,
          ticker: str = "", market: str = "US",
          macro_data: dict = None, weights: dict = None) -> dict:
    """
    Score the most recent bar for a ticker. This is the main entry point.

    Parameters
    ----------
    df_daily, df_weekly : OHLCV DataFrames (must have open/high/low/close/volume)
    market : "A" | "CN_IDX" | "HK" | "US"
    macro_data : dict from data_fetcher.fetch_macro_data()

    Returns: same dict as score_bar() for the LAST bar
    """
    if len(df_daily) < 50:
        return {"error": "Need at least 50 bars"}
    precomputed = precompute(df_daily, df_weekly)
    # v10: regime-adaptive dual-mode is now the default scoring engine
    return score_bar_v5(len(df_daily) - 1, df_daily, precomputed, macro_data, weights, market, ticker)

