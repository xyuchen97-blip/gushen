"""
Strategy Configuration Reference — LOCKED v8.1 (May 5, 2026)
==============================================================

Single source of truth: strategy/scoring.py + strategy/data_fetcher.py
This file is a human-readable reference only. All constants are defined
in scoring.py (v8.1+) and data_fetcher.py. Do NOT modify values here —
they must match scoring.py exactly.

=== LOCKED DATA SOURCES (see data_fetcher.py for implementation) ===

OHLCV (daily + weekly):
  A-shares: ak.stock_zh_a_hist(symbol, period="daily", start_date, end_date, adjust="qfq")
  US:       ak.stock_us_hist(symbol, period="daily", start_date, end_date, adjust="qfq")
             → symbol lookup via Eastmoney search API (105.MSFT format)
  HK:       ak.stock_hk_hist(symbol, period="daily", start_date, end_date, adjust="qfq")
  CSI 300:  ak.stock_zh_index_daily(symbol="sh000300")

Macro:
  VIX:      FRED API (VIXCLS series) — api.stlouisfed.org
  USD/CNY:  FRED API (DEXCHUS series)
  US Yields:ak.bond_zh_us_rate() → 美国国债收益率10年/5年/10年-2年
  China PMI:ak.index_pmi_man_cx() → Caixin Manufacturing PMI
  China CPI:ak.macro_china_cpi() → 全国-同比增长
  China M2: ak.macro_china_money_supply()
  China LPR:ak.macro_china_lpr()
  China QVIX:ak.index_option_50etf_qvix() → close
  Northbound:ak.stock_hsgt_fund_flow_summary_em() → 沪股通+深股通 北向 sum
  US CPI:   ak.macro_usa_cpi_yoy()
  US Unemp: FRED API (UNRATE series)

yfinance: Fully removed (May 5, 2026). All data from akshare + FRED API.

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
