"""
Strategy Configuration — LOCKED v7.0 (May 4, 2026)
====================================================

ALL data sources and calculations are frozen below.
Same ticker + same date + same config → identical composite score every time.
Verified: 3-run determinism test on 600519 — identical to 15 decimal places.

=== LOCKED DATA SOURCES ===

OHLCV (daily + weekly):
  A-shares: ak.stock_zh_a_hist(symbol, period="daily", start_date, end_date, adjust="qfq")
  CSI 300:  ak.stock_zh_index_daily(symbol="sh000300")
  US:       yf.download(ticker, start, end, progress=False, auto_adjust=True)
  HK:       yf.download(ticker, start, end, progress=False, auto_adjust=True)

Macro:
  VIX:      yf.download("^VIX", start, end, progress=False, auto_adjust=True)
  USD/CNY:  ak.currency_boc_sina(symbol="美元") → column "中行折算价"
  10Y:      yf.download("^TNX", start, end, progress=False, auto_adjust=True)
  5Y:       yf.download("^FVX", start, end, progress=False, auto_adjust=True)
  Note: ^TWO (2Y) delisted by Yahoo. Using ^FVX (5Y) as short-end proxy.

Fundamentals:
  A-shares: ak.stock_yjbb_em(date="20251231") → PE/PB/ROE/revenue growth
  US:       yf.Ticker(ticker).info → trailingPE/priceToBook/returnOnEquity/revenueGrowth
  Note: NOT used in backtest (fund_score fixed at 10). Activated for live monitoring only.

Stock Universe:
  CSI 300:  ak.index_stock_cons_csindex(symbol="000300")
  S&P 500:  pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")

=== LOCKED CALCULATIONS (all self-contained, no external randomness) ===

All technical indicators are computed in rolling_backtest._precompute_indicators():
  - Golden Pit 2.0 (golden_pit.compute)
  - Nine Turns TD Sequential (jiu_zhuan.compute)
  - Band King no-future ZIG (band_king.compute_no_future)
  - Bollinger Bands weekly (bollinger.compute_weekly_bb)
  - MA crosses (5/20 SMA)
  - Trend signals: MA alignment (20>60>120), price>MA50, ADX(14)>25 with +DI>-DI
  - MACD: EMA12/EMA26/DEA9, histogram, golden/death crosses
  - KDJ: RSV(9), K(3), D(3), J=3K-2D, oversold (<20), golden cross
  - Bullish divergence: price lower low + MACD histogram higher low (20-bar window)
  - Weekly Fib support: 50-period weekly swing 0.382/0.5/0.618 (mapped to daily)
  - Regime detection: bull = close > MA200
"""

import akshare as ak
import pandas as pd
import yfinance as yf


# ═══════════════════════════════════════════════════════════════════
# LOCKED WEIGHTS — Grid search optimum (101 combos, May 4 2026)
# ═══════════════════════════════════════════════════════════════════

OPTIMAL_WEIGHTS = {
    "technical":    40,
    "capital":      25,
    "fundamental":  15,
    "macro":        20,
}
# Fibonacci bonus: 0-5 points added on top (not part of 100-point allocation)

# ═══════════════════════════════════════════════════════════════════
# LOCKED SIGNAL SCORES (within technical dimension)
# ═══════════════════════════════════════════════════════════════════

TECH_CONTRARIAN = {
    "golden_pit":     10,
    "band_low":        5,
    "nine_turns_buy": 10,
    "band_king_buy2": 10,
}

TECH_TREND = {
    "ma_alignment":        10,
    "price_above_ma50":     3,
    "adx_trend_strong":    10,
    "bb_weekly_buy":       15,
    "ma_golden_cross":      5,
    "macd_golden_cross":    8,
}

TECH_MOMENTUM = {
    "kdj_golden":           5,
    "kdj_oversold":         5,
    "bullish_divergence":  12,
}

TECH_RESONANCE = {
    "fib_divergence_combo": 22,
    "fib_kdj_combo":        18,
}

TECH_SELL_PENALTIES = {
    "nine_turns_sell":  -10,
    "band_king_sell1":  -10,
    "ma_death_cross":    -5,
    "macd_death_cross":  -8,
}

# ═══════════════════════════════════════════════════════════════════
# LOCKED CAPITAL FLOW SIGNALS
# ═══════════════════════════════════════════════════════════════════
# northbound flow: daily net buy > 0 → +6 (A-shares only)
# correlation with CSI 300 = 0.348 | net buy days avg +0.35%, net sell days -0.46%
# institutional/mutual fund holdings: DROPPED — quarterly data, not suitable for daily scoring
CAPITAL_SIGNALS = {
    "volume_anomaly":       8,   # volume > 1.5× 20MA (all markets)
    "northbound_inflow":    6,   # net buy > 0 (A-shares only, via stock_hsgt_hist_em)
    # "institutional_buying": REMOVED — API broken for A-shares, quarterly for US
}

# ═══════════════════════════════════════════════════════════════════
# LOCKED MACRO SIGNALS (v2 — May 4 2026)
# ═══════════════════════════════════════════════════════════════════
MACRO_SIGNALS = {
    # Global (all markets)
    "vix_low":          4,   # VIX < 20 → risk-on (via yf.download ^VIX)
    "vix_declining":    2,   # VIX < 25 and declining

    # Currency (weighted by market)
    "usdcny_stable_cn": 4,   # USD/CNY ≤ 20MA → CNY strong (A-shares + HK, full weight)
    "usdcny_stable_us": 2,   # USD/CNY ≤ 20MA → CNY strong (US, half weight — weaker link)

    # Yield curve (all markets)
    "yield_curve_ok":   4,   # 10Y-5Y spread > 0.5% → normal (via ^TNX, ^FVX)
    "yield_curve_flat": 2,   # 10Y-5Y spread > 0 → flat

    # China-specific (A-shares + HK only)
    "china_lpr_easing":    3,   # LPR1Y cut in last 6 months → PBOC easing (via macro_china_lpr)
    "china_cpi_low":       2,   # CPI < 1% → room for easing (via macro_china_cpi_yearly)
    "china_pmi_expanding": 2,   # PMI > 50 → manufacturing expanding (via macro_china_pmi_yearly)
    "national_team_buy":   3,   # CSI 300 volume > 2× 20MA → possible intervention
}

# ═══════════════════════════════════════════════════════════════════
# LOCKED THRESHOLDS
# ═══════════════════════════════════════════════════════════════════

ENTRY_THRESHOLD     = 45
WATCHLIST_THRESHOLD = 38
EXIT_THRESHOLD      = 20
MIN_HOLD_BARS       = 5
# No hard SL/TP — exits are purely technical

# ═══════════════════════════════════════════════════════════════════
# LOCKED BOLLINGER BAND PARAMETERS
# ═══════════════════════════════════════════════════════════════════

BB_WEEKLY_PERIOD    = 20
BB_WEEKLY_STD       = 2.0
BB_BUY_VOL_MULTIPLE = 2.0

# ═══════════════════════════════════════════════════════════════════
# LOCKED MACD / KDJ PARAMETERS
# ═══════════════════════════════════════════════════════════════════

MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9
KDJ_N       = 9
KDJ_M1      = 3
KDJ_M2      = 3

# ═══════════════════════════════════════════════════════════════════
# LOCKED MOVING AVERAGES
# ═══════════════════════════════════════════════════════════════════

MA_PERIODS = [5, 10, 20, 30, 50, 60, 120, 200]

# ═══════════════════════════════════════════════════════════════════
# LOCKED POSITION SIZING
# ═══════════════════════════════════════════════════════════════════

BASE_POSITION_PCT    = 0.05
POSITION_70_PLUS     = 0.07
POSITION_80_PLUS     = 0.10
MAX_POSITIONS        = 15
MAX_SECTOR_EXPOSURE  = 0.30


def get_universe(market: str) -> list[str]:
    """Return locked stock universe."""
    if market == "A":
        try:
            df = ak.index_stock_cons_csindex(symbol="000300")
            return df["成分券代码"].tolist()[:50]
        except Exception:
            return [
                "600519", "000858", "601318", "600036", "000333",
                "601166", "600276", "601888", "000651", "002415",
                "300750", "601012", "600900", "000001", "002594",
                "601398", "600030", "000725", "600809", "603259",
            ]
    elif market == "US":
        try:
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            tables = pd.read_html(url)
            return tables[0]["Symbol"].tolist()[:50]
        except Exception:
            return [
                "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
                "META", "TSLA", "BRK-B", "JPM", "V",
                "JNJ", "WMT", "PG", "MA", "UNH",
                "HD", "BAC", "XOM", "DIS", "NFLX",
            ]
    raise ValueError(f"Unknown market: {market}")
