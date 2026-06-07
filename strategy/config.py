"""
Strategy Configuration Reference — v10.2 (May 2026)
==============================================================

Current engine: v10.2 regime-adaptive dual-mode (score_bar_v5 in scoring.py)
Single source of truth: strategy/scoring.py + strategy/data_fetcher.py
Tune mode: strategy/tune.py + strategy/gushen_cache.py (GUSHEN_TUNE=1 only)

Pipeline: 5-stage scoring
  Stage 1: Technical indicators (precompute)
  Stage 2: Capital flow + macro + fundamental
  Stage 3: Fibonacci support levels
  Stage 3.5: Analyst signals (earnings beat streak, consensus)
  Stage 4: Regime-adaptive weighting + normalization
  Stage 5: Decision (BUY/WATCH/HOLD/EXIT) with adaptive exits

=== PRODUCTION DATA SOURCES (data_fetcher.py) ===

OHLCV (daily + weekly):
  A-shares: ak.stock_zh_a_hist(adjust="qfq") → yfinance fallback
  HK:       ak.stock_hk_hist(adjust="qfq") → yfinance fallback
  US:       ak.stock_us_daily(adjust="qfq") → ak.stock_us_hist_fu(adjust="qfq") → Alpha Vantage → yfinance(adjusted)

Macro:
  VIX + USD/CNY + US Unemp: FRED API
  US Yields / China macro: akshare (Eastmoney)

A-stock special factors:
  PB:      akshare stock_zh_valuation_baidu
  MFF:     akshare stock_individual_fund_flow
  Margin:  akshare stock_margin_detail_sse/szse

=== TUNE MODE DATA SOURCES (gushen_cache.py) ===

  All OHLCV + macro + factors: Tushare Pro (258 APIs)
  SQLite cache: data/gushen.db
  Guard: GUSHEN_TUNE=1 required

=== LOCKED CALCULATIONS (see scoring.py for implementation) ===

All technical indicators in scoring.precompute():
  - Golden Pit 2.0, Nine Turns, Band King (no-future ZIG)
  - Bollinger Bands weekly (20, 2.0 std)
  - MA crosses (5/20, 20/60/120 alignment, MA200 regime)
  - ADX(14) with +DI/-DI
  - MACD(12/26/9), KDJ(9/3/3)
  - Bullish divergence (20-bar window)
  - Weekly Fibonacci support (50-period, 0.382/0.5/0.618)

=== SCORING ARCHITECTURE ===

  Composite = tech_n + cap_n (entry_score + cap_bonus)
  Action mapping: per-market raw thresholds + adaptive exits (time decay, profit-take, ATR stop)
"""

# Constants match scoring.py v10.2

OPTIMAL_WEIGHTS = {"technical": 36, "capital": 26, "fundamental": 14, "macro": 19, "fibonacci": 5}

# v9.6: Per-market weights (z-score normalized, thresholds are the primary lever)
MARKET_WEIGHTS = {
    "A":  {"technical": 25, "capital": 35, "fundamental": 15, "macro": 20, "fibonacci": 5},
    "HK": {"technical": 35, "capital": 25, "fundamental": 15, "macro": 20, "fibonacci": 5},
    "US": {"technical": 38, "capital": 24, "fundamental": 14, "macro": 19, "fibonacci": 5},
}

# Per-market z-score thresholds (BUY, WATCH, EXIT)
# With trend-override: bull_regime → EXIT becomes HOLD
THRESHOLDS_V96 = {
    "A":  {"buy": 65, "watch": 55, "exit": 35},
    "HK": {"buy": 68, "watch": 58, "exit": 38},
    "US": {"buy": 72, "watch": 62, "exit": 42},
}

# Legacy thresholds (fallback when no score_history)
ENTRY_THRESHOLD     = 45
WATCHLIST_THRESHOLD = 38
EXIT_THRESHOLD      = 20

QVIX_THRESHOLDS     = {"very_low": 14.2, "low": 16.2, "high": 30.9}

# Bollinger Band params (used by bollinger.py)
BB_WEEKLY_PERIOD    = 20
BB_WEEKLY_STD       = 2.0
BB_BUY_VOL_MULTIPLE = 2.0
