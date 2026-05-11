"""
Strategy Configuration Reference — LOCKED v9.4 (May 11, 2026)
==============================================================

Single source of truth: strategy/scoring.py + strategy/data_fetcher.py
Tune mode: strategy/tune.py + strategy/gushen_cache.py (GUSHEN_TUNE=1 only)

=== PRODUCTION DATA SOURCES (data_fetcher.py) ===

OHLCV (daily + weekly):
  A-shares: ak.share → yfinance fallback
  US/HK:    yfinance primary

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
"""

# All constants below match scoring.py v8.1 — DO NOT MODIFY independently

OPTIMAL_WEIGHTS = {"technical": 40, "capital": 25, "fundamental": 15, "macro": 20}

ENTRY_THRESHOLD     = 45
WATCHLIST_THRESHOLD = 38
EXIT_THRESHOLD      = 20
QVIX_THRESHOLDS     = {"very_low": 14.2, "low": 16.2, "high": 30.9}

# Bollinger Band params (used by bollinger.py)
BB_WEEKLY_PERIOD    = 20
BB_WEEKLY_STD       = 2.0
BB_BUY_VOL_MULTIPLE = 2.0
