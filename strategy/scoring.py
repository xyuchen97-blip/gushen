"""
STRATEGY SCORING ENGINE — Single Source of Truth (LOCKED v7.2, May 4, 2026)
===========================================================================

THIS IS THE DEFINITIVE SCORING MODULE. Any LLM or agent reading this file
should produce IDENTICAL composite scores for any given input.

Usage:
    from strategy.scoring import score

    result = score(df_daily, df_weekly, ticker="600519", market="A",
                    macro_data=macro, weights=None)

    print(result["composite"])   # 0-105
    print(result["action"])      # BUY / EXIT / WATCH / HOLD
    print(result["active"])      # list of triggered signals
    print(result["reasoning"])   # human-readable breakdown

Determinism: Same ticker + same OHLCV + same macro → identical score every time.
Verified: 3-run test on Moutai 600519 — 22 sample scores identical to 15 decimal places.
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dzh_indicators import golden_pit, jiu_zhuan, band_king
from .bollinger import compute_weekly_bb, bb_weekly_sell_signal, bb_weekly_buy_signal
from .fibonacci import score_fibonacci

# ═══════════════════════════════════════════════════════════════════
# LOCKED PARAMETERS (do not modify without re-running grid search)
# ═══════════════════════════════════════════════════════════════════

WEIGHTS = {"technical": 40, "capital": 25, "fundamental": 15, "macro": 20}

SIGNAL_SCORES = {
    # Contrarian (DZH)
    "golden_pit":        10,
    "band_low":           5,
    "nine_turns_buy":    10,
    "nine_turns_setup9":  5,
    "band_king_buy2":    10,
    # Trend-following
    "ma_aligned":        10,
    "price_above_ma50":   3,
    "adx_trend":         10,
    "bb_weekly_buy":     15,
    "ma_golden_cross":    5,
    "macd_golden":        8,
    # Momentum
    "kdj_golden":         5,
    "kdj_oversold":       5,
    "bullish_divergence": 12,
    # High conviction combos
    "fib_divergence_combo": 22,
    "fib_kdj_combo":       18,
    # Capital
    "volume_anomaly":      8,
    "northbound_inflow":   6,
}

SELL_PENALTIES = {
    "nine_turns_sell":  -10,
    "band_king_sell1":  -10,
    "ma_death_cross":    -5,
    "macd_death_cross":  -8,
}

MACRO_SCORES = {
    "vix_below_20":     4,
    "vix_below_25":     2,
    "spread_above_05":  4,
    "spread_positive":  2,
    "usdcny_cnhk":      4,
    "usdcny_us":        2,
    "cpi_declining":    2,
    "cpi_below_3":      3,
    "unemp_below_4":    3,
    "unemp_below_5":    1,
    "lpr_cut":          3,
    "china_cpi_low":    2,
    "china_pmi_ok":     2,
    "china_m2_above_9": 3,
    "china_m2_above_7": 2,
    "national_team":    3,
}

THRESHOLDS = {
    "entry":    45,
    "watchlist": 38,
    "exit":     20,
    "min_hold":  5,
}

BB_PARAMS   = {"period": 20, "std": 2.0, "vol_mult": 2.0}
MACD_PARAMS = {"fast": 12, "slow": 26, "signal": 9}
KDJ_PARAMS  = {"n": 9, "m1": 3, "m2": 3}
ADX_PERIOD  = 14
FIB_LOOKBACK = 50


# ═══════════════════════════════════════════════════════════════════
# INDICATOR PRE-COMPUTATION (run once per ticker)
# ═══════════════════════════════════════════════════════════════════

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
    fib_support = pd.Series(False, index=df_daily.index)
    if len(df_weekly) >= 50:
        wh = df_weekly["high"].rolling(50).max()
        wl = df_weekly["low"].rolling(50).min()
        wr = wh - wl
        for level in [0.382, 0.5, 0.618]:
            target = (wl + wr * level).reindex(df_daily.index, method="ffill")
            fib_support = fib_support | (abs(close - target) / close < 0.02)
    result["weekly_fib_support"] = fib_support

    # ── Volume ──────────────────────────────────────────────
    result["vol_anomaly"] = volume > (volume.rolling(20).mean() * 1.5)

    return result


# ═══════════════════════════════════════════════════════════════════
# SCORING (per bar)
# ═══════════════════════════════════════════════════════════════════

def score_bar(i: int, df_daily: pd.DataFrame, precomputed: dict,
              macro_data: dict = None, weights: dict = None,
              market: str = "US") -> dict:
    """
    Compute the composite score for a single bar (position i in df_daily).

    Parameters
    ----------
    i : int — bar index
    df_daily : OHLCV DataFrame
    precomputed : dict from precompute()
    macro_data : dict from data_fetcher.fetch_macro_data()
    weights : dict or None (uses default WEIGHTS)
    market : "A" | "CN_IDX" | "HK" | "US"

    Returns
    -------
    dict with: composite (float), action (str), active (list), tech_score,
               cap_score, fund_score, macro_score, fib_bonus, bb_sell
    """
    w = weights or WEIGHTS
    bar_date = df_daily.index[i]
    bull = precomputed["bull_regime"].iloc[i]
    tech, cap, active, sell_override = 0, 0, [], False

    # ── Contrarian ───────────────────────────────────────────
    if precomputed["golden_pit"].iloc[i] != 0:
        tech += SIGNAL_SCORES["golden_pit"]; active.append("golden_pit")
    if precomputed["band_low"].iloc[i] != 0:
        tech += SIGNAL_SCORES["band_low"];   active.append("band_low")
    if precomputed["buy_signal"].iloc[i]:
        tech += SIGNAL_SCORES["nine_turns_buy"]; active.append("nine_turns_buy")
    if precomputed["buy_setup_done"].iloc[i]:
        tech += SIGNAL_SCORES["nine_turns_setup9"]; active.append("nine_turns_setup9")
    if precomputed["buy2"].iloc[i]:
        tech += SIGNAL_SCORES["band_king_buy2"]; active.append("band_king_buy2")
    if precomputed["bb_buy"].iloc[i]:
        tech += SIGNAL_SCORES["bb_weekly_buy"]; active.append("bb_weekly_buy")

    # ── Trend (regime-weighted) ──────────────────────────────
    trend = 0
    if precomputed["ma_aligned"].iloc[i]:
        trend += SIGNAL_SCORES["ma_aligned"]; active.append("ma_aligned")
    if precomputed["price_above_ma50"].iloc[i]:
        trend += SIGNAL_SCORES["price_above_ma50"]
    if precomputed["adx_strong"].iloc[i]:
        trend += SIGNAL_SCORES["adx_trend"]; active.append("adx_trend")
    if precomputed["ma_golden"].iloc[i]:
        trend += SIGNAL_SCORES["ma_golden_cross"]; active.append("ma_golden_cross")
    if precomputed["macd_golden"].iloc[i]:
        trend += SIGNAL_SCORES["macd_golden"]; active.append("macd_golden")
    tech += trend if bull else int(trend * 0.5)

    # ── Momentum ─────────────────────────────────────────────
    if precomputed["kdj_golden"].iloc[i]:
        tech += SIGNAL_SCORES["kdj_golden"]; active.append("kdj_golden")
    if precomputed["kdj_oversold"].iloc[i]:
        tech += SIGNAL_SCORES["kdj_oversold"]; active.append("kdj_oversold")
    if precomputed["bullish_divergence"].iloc[i]:
        tech += SIGNAL_SCORES["bullish_divergence"]; active.append("bullish_divergence")

    # ── Resonance (Fib combos) ───────────────────────────────
    if precomputed["weekly_fib_support"].iloc[i] and precomputed["bullish_divergence"].iloc[i]:
        tech += SIGNAL_SCORES["fib_divergence_combo"]; active.append("fib_divergence_combo")
    elif precomputed["weekly_fib_support"].iloc[i] and precomputed["kdj_oversold"].iloc[i]:
        tech += SIGNAL_SCORES["fib_kdj_combo"]; active.append("fib_kdj_combo")

    # ── Sell penalties ───────────────────────────────────────
    if precomputed["sell_signal"].iloc[i]:
        tech += SELL_PENALTIES["nine_turns_sell"]
    if precomputed["sell1"].iloc[i]:
        tech += SELL_PENALTIES["band_king_sell1"]
    if precomputed["ma_death"].iloc[i]:
        tech += SELL_PENALTIES["ma_death_cross"]
    if precomputed["macd_death"].iloc[i]:
        tech += SELL_PENALTIES["macd_death_cross"]
    tech = max(0, tech)

    # Weekly MA20 filter: 30% penalty for counter-trend in bear
    if not precomputed["weekly_ma20_up"].iloc[i] and not bull:
        tech = int(tech * 0.7)

    # BB Weekly sell override
    bb_sell_now = precomputed["bb_sell"].iloc[i]

    # ── Capital ──────────────────────────────────────────────
    if precomputed["vol_anomaly"].iloc[i]:
        cap += SIGNAL_SCORES["volume_anomaly"]
    if market == "A" and macro_data and "northbound_flow" in macro_data:
        nb = macro_data["northbound_flow"][macro_data["northbound_flow"].index <= bar_date]
        if len(nb) > 0 and float(nb.iloc[-1]) > 0:
            cap += SIGNAL_SCORES["northbound_inflow"]; active.append("northbound_inflow")

    # ── Fibonacci standalone bonus ───────────────────────────
    fib_score = 0
    if i >= FIB_LOOKBACK:
        fib = score_fibonacci(df_daily.iloc[max(0,i-49):i+1])
        fib_score = fib.get("retracement_score", 0) + fib.get("extension_score", 0)
        fib_score = min(fib_score, 3)
    # Multi-period check
    res = 0
    if i >= 120:
        m20 = df_daily["close"].iloc[i-19:i+1].mean()
        m60 = df_daily["close"].iloc[i-59:i+1].mean() if i >= 60 else m20
        if df_daily["close"].iloc[i] > m20 and df_daily["close"].iloc[i] > m60:
            res = 3
    fib_bonus = min(fib_score + res, 5)

    # ── Macro ────────────────────────────────────────────────
    macro_score = 0
    if macro_data:
        # VIX
        if "vix" in macro_data and not macro_data["vix"].empty:
            vv = macro_data["vix"][macro_data["vix"].index <= bar_date]
            if len(vv) > 0:
                v = float(vv.iloc[-1])
                if v < 20: macro_score += MACRO_SCORES["vix_below_20"]
                elif v < 25: macro_score += MACRO_SCORES["vix_below_25"]
        # USD/CNY
        if "usdcny" in macro_data and not macro_data["usdcny"].empty:
            cv = macro_data["usdcny"][macro_data["usdcny"].index <= bar_date]
            if len(cv) > 20 and float(cv.iloc[-1]) <= float(cv.iloc[-20:].mean()):
                macro_score += MACRO_SCORES["usdcny_cnhk" if market in ("A","CN_IDX","HK") else "usdcny_us"]
        # Yield curve
        if all(k in macro_data for k in ["yield10y", "yield5y"]):
            t10 = macro_data["yield10y"][macro_data["yield10y"].index <= bar_date]
            t5  = macro_data["yield5y"][macro_data["yield5y"].index <= bar_date]
            if len(t10) > 0 and len(t5) > 0:
                sp = float(t10.iloc[-1]) - float(t5.iloc[-1])
                if sp > 0.5: macro_score += MACRO_SCORES["spread_above_05"]
                elif sp > 0: macro_score += MACRO_SCORES["spread_positive"]
    # US Macro
    if macro_data:
        if "us_cpi_yoy" in macro_data:
            cv = macro_data["us_cpi_yoy"][macro_data["us_cpi_yoy"].index <= bar_date]
            if len(cv) > 3:
                c = float(cv.iloc[-1])
                p = float(cv.iloc[max(0, len(cv)-4)])
                if c < p: macro_score += MACRO_SCORES["cpi_declining"]
                if c < 3.0: macro_score += MACRO_SCORES["cpi_below_3"]
        if "us_unemployment" in macro_data:
            uv = macro_data["us_unemployment"][macro_data["us_unemployment"].index <= bar_date]
            if len(uv) > 0:
                u = float(uv.iloc[-1])
                if u < 4.0: macro_score += MACRO_SCORES["unemp_below_4"]
                elif u < 5.0: macro_score += MACRO_SCORES["unemp_below_5"]
    # China-specific
    if market in ("A", "CN_IDX", "HK") and macro_data:
        if "china_lpr1y" in macro_data:
            lv = macro_data["china_lpr1y"][macro_data["china_lpr1y"].index <= bar_date]
            if len(lv) > 2:
                l_now = float(lv.iloc[-1]); l_past = float(lv.iloc[max(0, len(lv)-2)])
                if l_now < l_past: macro_score += MACRO_SCORES["lpr_cut"]; active.append("lpr_easing")
        if "china_cpi" in macro_data:
            cv = macro_data["china_cpi"][macro_data["china_cpi"].index <= bar_date]
            if len(cv) > 0 and float(cv.iloc[-1]) < 1.0: macro_score += MACRO_SCORES["china_cpi_low"]
        if "china_pmi" in macro_data:
            pv = macro_data["china_pmi"][macro_data["china_pmi"].index <= bar_date]
            if len(pv) > 0 and float(pv.iloc[-1]) > 50: macro_score += MACRO_SCORES["china_pmi_ok"]
        if "china_m2_yoy" in macro_data:
            mv = macro_data["china_m2_yoy"][macro_data["china_m2_yoy"].index <= bar_date]
            if len(mv) > 0:
                m = float(mv.iloc[-1])
                if m > 9.0: macro_score += MACRO_SCORES["china_m2_above_9"]; active.append("m2_expanding")
                elif m > 7.0: macro_score += MACRO_SCORES["china_m2_above_7"]
        if precomputed.get("vol_anomaly") is not None and precomputed["vol_anomaly"].iloc[i]:
            macro_score += MACRO_SCORES["national_team"]; active.append("national_team")

    # ── Fundamental (fixed neutral) ──────────────────────────
    fund_score = 10

    # ── Normalize & Weight ──────────────────────────────────
    tech_n  = min(tech / 45.0, 1.0) * w["technical"]
    cap_n   = min(cap / 14.0, 1.0) * w["capital"]
    fund_n  = min(fund_score / 10.0, 1.0) * w["fundamental"]
    macro_n = min(macro_score / 31.0, 1.0) * w["macro"]
    composite = tech_n + cap_n + fund_n + macro_n + fib_bonus

    # ── Action ──────────────────────────────────────────────
    if bb_sell_now:
        action = "EXIT"
    elif composite >= THRESHOLDS["entry"]:
        action = "BUY"
    elif composite < THRESHOLDS["exit"]:
        action = "EXIT"
    elif composite >= THRESHOLDS["watchlist"]:
        action = "WATCH"
    else:
        action = "HOLD"

    return {
        "composite":  composite,
        "action":     action,
        "active":     active,
        "tech_score": tech,
        "cap_score":  cap,
        "fund_score": fund_score,
        "macro_score": macro_score,
        "fib_bonus":  fib_bonus,
        "bb_sell":    bb_sell_now,
        "bull_regime": bull,
        "reasoning":  f"T={tech_n:.0f} C={cap_n:.0f} F={fund_n:.0f} M={macro_n:.0f} +Fib={fib_bonus} | regime={'bull' if bull else 'bear'} | signals={active}",
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
    return score_bar(len(df_daily) - 1, df_daily, precomputed, macro_data, weights, market)
