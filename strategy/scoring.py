"""
EXPLORER SCORING ENGINE — Experimental (v9.0-alpha, May 6, 2026)
=================================================================

THIS IS THE EXPERIMENTAL EXPLORER. Changes from v8.3:
  v9.0-alpha: BB sell -> trend-graded penalty (removed EXIT override)
              Adaptive chain resonance (BOLL->KDJ->MACD, 3-8 bar window)
              Chain bonuses: C2 +12pt, C3 +18pt (re-validated for adaptive windows v9.2)
              QVIX thresholds: adaptive rolling percentiles (v9.2)

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
from .fibonacci import score_fibonacci
from .elliot_wave import detect_wave5_target, detect_right_shoulder, triple_confirm

# v9.0-alpha: Runtime config override (for grid search)
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

WEIGHTS = _grid_params.get("weights", {"technical": 38, "capital": 24, "fundamental": 14, "macro": 19, "fibonacci": 5})  # v9.2

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
    # v9.0-alpha: adaptive BOLL->KDJ->MACD chain resonance
    "boll_kdj_chain":      _grid_params.get("c2_bonus", 15),  # C2: v9.1 calibrated
    "boll_kdj_macd_chain": _grid_params.get("c3_bonus", 22),  # C3: v9.1 calibrated
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
    "spread_inverted": -5,  # v9.2: yield curve inversion penalty
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
    # v8.0: China QVIX (50ETF options volatility index)
    "china_qvix_very_low": 3,  # < P15 → extreme stability premium
    "china_qvix_low":      2,  # < P35 → stability premium
    "china_qvix_high":     -2,  # > P75 → fear penalty
}

THRESHOLDS = _grid_params.get("thresholds_A", {
    "entry":      45,
    "entry_bear": 46,
    "watchlist":  38,
    "watch_bear": 39,
    "exit":       20,
    "min_hold":    5,
})

US_THRESHOLDS = _grid_params.get("thresholds_US", {"entry": 50, "watchlist": 42, "exit": 22})

# v8.0: China QVIX thresholds (percentile-based from full history)
QVIX_THRESHOLDS = {
    "very_low": 14.2,   # ~P15 — extremely low volatility
    "low":      16.2,   # ~P35 — below median volatility
    "high":     30.9,   # ~P75 — above-normal fear
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
    tech += trend if bull else int(trend * BEAR_TREND_DISCOUNT)

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

    # v9.2: Chain resonance — O(1) lookup from precomputed (vectorized in precompute)
    chain_window = 5  # default
    if precomputed["adx_strong"].iloc[i]:
        chain_window = 3
    elif i >= 30 and not precomputed["adx_strong"].iloc[i-30:i].any():
        chain_window = 8
    if precomputed.get(f"chain_c2_w{chain_window}", [False])[i]:
        tech += SIGNAL_SCORES["boll_kdj_chain"]; active.append("boll_kdj_chain")
        if precomputed.get(f"chain_c3_w{chain_window}", [False])[i]:
            tech += SIGNAL_SCORES["boll_kdj_macd_chain"]; active.append("boll_kdj_macd_chain")

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

    # Weekly MA20 filter: penalty for counter-trend in bear (v8.2: market-specific)
    if not precomputed["weekly_ma20_up"].iloc[i] and not bull:
        if market in ("A", "HK", "CN_IDX"):
            tech = int(tech * MA20_PENALTY_A_HK)  # A/HK counter-trend
        else:
            tech = int(tech * MA20_PENALTY_US)      # US counter-trend (lighter)

    # v9.0-alpha: BB sell as trend-graded penalty (removed EXIT override)
    bb_sell_now = precomputed["bb_sell"].iloc[i]
    if bb_sell_now:
        adx_strong = precomputed["adx_strong"].iloc[i]
        ma50_check = precomputed["price_above_ma50"].iloc[i]
        vol_burst = precomputed["vol_anomaly"].iloc[i]
        if adx_strong and ma50_check and vol_burst:
            tech -= _grid_params.get("bb_strong", 8); active.append("bb_sell:strong")
        elif adx_strong or ma50_check:
            tech -= _grid_params.get("bb_moderate", 5); active.append("bb_sell:moderate")
        else:
            tech -= _grid_params.get("bb_weak", 3); active.append("bb_sell:weak")
        tech = max(0, tech)

    # ── Capital ──────────────────────────────────────────────
    # v9.4: Triple confirmation bonus (contrarian ∩ volume ∩ momentum)
    triple = triple_confirm(precomputed, i)
    if triple["triple_confirm"]:
        cap += 3; active.append("triple_confirm")
    if precomputed["vol_anomaly"].iloc[i]:
        cap += SIGNAL_SCORES["volume_anomaly"]
    if market == "A" and macro_data and "northbound_flow" in macro_data:
        nb = macro_data["northbound_flow"][macro_data["northbound_flow"].index <= bar_date]
        if len(nb) > 0 and float(nb.iloc[-1]) > 0:
            cap += SIGNAL_SCORES["northbound_inflow"]; active.append("northbound_inflow")

    # v9.3: A-stock margin financing contarian factor
    if market == "A" and macro_data and "margin" in macro_data:
        margin_hist = macro_data["margin"]
        if bar_date in margin_hist:
            mr = margin_hist[bar_date]
            if mr.get("pct_5d", 0) > 5:
                cap -= 5; active.append("margin_overheat")
            elif mr.get("pct_5d", 0) > 2:  # v9.4: +200% extreme (still contarian)
                cap -= 8; active.append("margin_extreme")  # stronger penalty
            elif mr.get("pct_5d", 0) < -5:
                cap += 3; active.append("margin_panic")

    # v9.4: Chip concentration (Tushare cyq_chips)
    if market == "A" and macro_data and "chip_conc" in macro_data:
        cc = macro_data["chip_conc"]
        if cc > 50:
            cap += 3; active.append("chip_tight")  # concentrated holdings
        elif cc < 20:
            cap -= 2; active.append("chip_loose")  # scattered holders

    # v9.4: Shareholder count change (Tushare stk_holdernumber)
    if market == "A" and macro_data and "holder_chg" in macro_data:
        hc = macro_data["holder_chg"]
        if hc < -0.03:  # holders decreased >3% = concentration
            cap += 2; active.append("holder_consolidate")
        elif hc > 0.05:  # holders increased >5% = dilution
            cap -= 2; active.append("holder_dilute")

    # v9.1: A-stock main force flow factor — only for growth/tech sectors
    # Growth stocks (科技/消费/制造/医药): MFF ΔS=+1.09~+2.15
    # Defensive stocks (银行/公用/矿业): MFF hurts, skip
    if market == "A" and macro_data and "mff" in macro_data:
        a_sector = macro_data.get("a_sector", "defensive")  # default: skip
        if a_sector == "growth":
            mff_list = macro_data["mff"]
            if bar_date in mff_list:
                mf = mff_list[bar_date]
                avg_super = mf.get("super_ratio", 0)
                avg_mf = mf.get("mf_ratio", 0)
                if avg_super > 3:
                    cap += 6; active.append("mff_strong")
                elif avg_mf > 2:
                    cap += 3; active.append("mff_moderate")
                elif avg_mf < -8:
                    cap -= 4; active.append("mff_sell")

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
            if len(vv) > 0 and pd.notna(vv.iloc[-1]):
                v = float(vv.iloc[-1])
                if v < 20: macro_score += MACRO_SCORES["vix_below_20"]
                elif v < 25: macro_score += MACRO_SCORES["vix_below_25"]
        # USD/CNY
        if "usdcny" in macro_data and not macro_data["usdcny"].empty:
            cv = macro_data["usdcny"][macro_data["usdcny"].index <= bar_date]
            if len(cv) > 20 and pd.notna(cv.iloc[-1]) and pd.notna(cv.iloc[-20:]).all():
                if float(cv.iloc[-1]) <= float(cv.iloc[-20:].mean()):
                    macro_score += MACRO_SCORES["usdcny_cnhk" if market in ("A","CN_IDX","HK") else "usdcny_us"]
        # Yield curve spread (10Y-2Y from bond_zh_us_rate)
        if "us_spread_10y2y" in macro_data:
            sv = macro_data["us_spread_10y2y"][macro_data["us_spread_10y2y"].index <= bar_date]
            if len(sv) > 0 and pd.notna(sv.iloc[-1]):
                sp = float(sv.iloc[-1])
                if sp > 0.5: macro_score += MACRO_SCORES["spread_above_05"]
                elif sp > 0: macro_score += MACRO_SCORES["spread_positive"]
                else: macro_score += MACRO_SCORES.get("spread_inverted", -5); active.append("yield_curve_inverted")  # v9.2
    # US Macro
    if macro_data:
        if "us_cpi_yoy" in macro_data:
            cv = macro_data["us_cpi_yoy"][macro_data["us_cpi_yoy"].index <= bar_date]
            if len(cv) > 3 and pd.notna(cv.iloc[-1]) and pd.notna(cv.iloc[max(0, len(cv)-4)]):
                c = float(cv.iloc[-1])
                p = float(cv.iloc[max(0, len(cv)-4)])
                if c < p: macro_score += MACRO_SCORES["cpi_declining"]
                if c < 3.0: macro_score += MACRO_SCORES["cpi_below_3"]
        if "us_unemployment" in macro_data:
            uv = macro_data["us_unemployment"][macro_data["us_unemployment"].index <= bar_date]
            if len(uv) > 0 and pd.notna(uv.iloc[-1]):
                u = float(uv.iloc[-1])
                if u < 4.0: macro_score += MACRO_SCORES["unemp_below_4"]
                elif u < 5.0: macro_score += MACRO_SCORES["unemp_below_5"]
    # China-specific
    if market in ("A", "CN_IDX", "HK") and macro_data:
        if "china_lpr1y" in macro_data:
            lv = macro_data["china_lpr1y"][macro_data["china_lpr1y"].index <= bar_date]
            if len(lv) > 2 and pd.notna(lv.iloc[-1]) and pd.notna(lv.iloc[max(0, len(lv)-2)]):
                l_now = float(lv.iloc[-1]); l_past = float(lv.iloc[max(0, len(lv)-2)])
                if l_now < l_past: macro_score += MACRO_SCORES["lpr_cut"]; active.append("lpr_easing")
        if "china_cpi" in macro_data:
            cv = macro_data["china_cpi"][macro_data["china_cpi"].index <= bar_date]
            if len(cv) > 0 and pd.notna(cv.iloc[-1]) and float(cv.iloc[-1]) < 1.0: macro_score += MACRO_SCORES["china_cpi_low"]
        if "china_pmi" in macro_data:
            pv = macro_data["china_pmi"][macro_data["china_pmi"].index <= bar_date]
            if len(pv) > 0 and pd.notna(pv.iloc[-1]) and float(pv.iloc[-1]) > 50: macro_score += MACRO_SCORES["china_pmi_ok"]
        if "china_m2_yoy" in macro_data:
            mv = macro_data["china_m2_yoy"][macro_data["china_m2_yoy"].index <= bar_date]
            if len(mv) > 0 and pd.notna(mv.iloc[-1]):
                m = float(mv.iloc[-1])
                if m > 9.0: macro_score += MACRO_SCORES["china_m2_above_9"]; active.append("m2_expanding")
                elif m > 7.0: macro_score += MACRO_SCORES["china_m2_above_7"]
        if precomputed.get("national_team") is not None and precomputed["national_team"].iloc[i]:
            macro_score += MACRO_SCORES["national_team"]; active.append("national_team")
        # v9.2: China QVIX — adaptive rolling percentiles (keep fixed as fallback)
        if "china_qvix" in macro_data:
            qv = macro_data["china_qvix"][macro_data["china_qvix"].index <= bar_date]
            if len(qv) > 0 and pd.notna(qv.iloc[-1]):
                qv_val = float(qv.iloc[-1])
                if len(qv) >= 60:
                    lookback = qv.tail(252)
                    QVIX_P15 = 0.15; QVIX_P35 = 0.35; QVIX_P75 = 0.75  # v9.2 named
                    very_low = lookback.quantile(QVIX_P15)
                    low = lookback.quantile(QVIX_P35)
                    high = lookback.quantile(QVIX_P75)
                else:
                    very_low = QVIX_THRESHOLDS["very_low"]
                    low = QVIX_THRESHOLDS["low"]
                    high = QVIX_THRESHOLDS["high"]
                if qv_val < very_low:
                    macro_score += MACRO_SCORES["china_qvix_very_low"]; active.append("qvix_very_low")
                elif qv_val < low:
                    macro_score += MACRO_SCORES["china_qvix_low"]; active.append("qvix_low")
                elif qv_val > high:
                    macro_score += MACRO_SCORES["china_qvix_high"]; active.append("qvix_fear")

    # ── Fundamental (v8.3: dynamic earnings quality) ────────
    fund_score = 10  # base neutral
    if macro_data and "fundamentals" in macro_data:
        f = macro_data["fundamentals"]
        if f:
            if f.get("roe", 0) > 15: fund_score += 5
            elif f.get("roe", 0) > 10: fund_score += 3
            if f.get("profit_growth", -999) > 0.2: fund_score += 4
            elif f.get("profit_growth", -999) > 0: fund_score += 2
            if f.get("revenue_growth", -999) > 0.15: fund_score += 4
            elif f.get("revenue_growth", -999) > 0: fund_score += 2
            if f.get("profit_margin", 0) > 0.15: fund_score += 3
            elif f.get("profit_margin", 0) > 0.05: fund_score += 1

    # v9.4: Event-based bonuses (sparse but real signals)
    if macro_data and "events" in macro_data:
        ev = macro_data["events"]
        if bar_date in ev:
            for etype in ev[bar_date]:
                if etype == "repurchase":
                    fund_score += 3; active.append("repurchase")
                elif etype == "forecast_up":
                    fund_score += 2; active.append("forecast_up")
                elif etype == "survey":
                    fund_score += 2; active.append("institutional_survey")
                elif etype == "buyback_ratio":
                    fund_score += 4; active.append("large_buyback")

    # ── Normalize & Weight ──────────────────────────────────
    tech_n  = min(tech / 45.0, 1.0) * w["technical"]
    cap_n   = min(cap / 14.0, 1.0) * w["capital"]
    fund_n  = min(min(fund_score, 15) / 15.0, 1.0) * w["fundamental"]  # v8.3: base 10=neutral, max 15
    macro_n = min(macro_score / 35.0, 1.0) * w["macro"]
    if macro_score == 0 and macro_data:
        macro_n = 0.5 * w["macro"]  # v9.2: no macro data available → neutral 50%
    composite = tech_n + cap_n + fund_n + macro_n + min(fib_bonus / 5.0, 1.0) * w.get("fibonacci", 0)  # v9.2: fib normalized

    # ── Action (v8.2: regime-aware + market-specific) ──────
    # Pick thresholds based on market and regime
    t = US_THRESHOLDS if market == "US" else THRESHOLDS
    entry_thresh = t["entry"] if bull else t.get("entry_bear", t["entry"])
    watch_thresh = t["watchlist"] if bull else t.get("watch_bear", t["watchlist"])
    exit_thresh  = t["exit"]
    
    # v9.0-alpha: Action determination (no BB override — all score-based)
    if composite >= entry_thresh:
        action = "BUY"
    elif composite < exit_thresh:
        action = "EXIT"  # v8.2: unified EXIT — removed bear exception (too lenient)
    elif composite >= watch_thresh:
        action = "WATCH"
    else:
        action = "HOLD"
    
    # v8.2: contrarian confirmation removed — kept trend discount + bear thresholds as primary levers
    # (contrarian check was too restrictive, dropping BEAR exposure from 71% to 15%)

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
