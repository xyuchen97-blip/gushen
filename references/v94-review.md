# Gushen v9.4 — Final Strategy Reference for Review

> Send this to Claude. Complete pipeline + performance documentation.

---

## Strategy Overview

Multi-market (US/HK/A) quantitative stock scoring. Weekly signal, 5-year backtest (2021-2026). One pipeline, market-specific weights. Tushare Pro as primary data source.

**Current: v9.4 | S=1.57 | 21 stocks | Tushare OHLCV**

---

## Architecture

```
score_bar(i, df_daily, precomputed, macro_data, market)
  ├─ Contrarian signals (golden_pit, band_low, nine_turns, bb_buy)
  ├─ Trend signals (MA alignment, ADX, MACD golden) — regime-weighted  
  ├─ Momentum (KDJ, divergence)
  ├─ Adaptive Chain Resonance (BOLL→KDJ→MACD, 3-8 bar window)
  ├─ BB Sell Penalty (trend-graded 8/5/3)
  ├─ Triple Confirm (contrarian ∩ volume ∩ momentum → +3pt)
  ├─ Capital (volume, northbound, margin financial)
  ├─ Fundamental (ROE, earnings, PE/PB classification)
  ├─ Macro (VIX, QVIX, CPI, PMI, M2, LPR, yield spread, USD/CNY)
  └─ Composite normalization → BUY/WATCH/HOLD/EXIT
```

## Data Pipeline

```
Production (live analysis):
  data_fetcher.py → Tushare primary → akshare fallback → yfinance fallback

Tune Mode (backtest/calibration):
  GUSHEN_TUNE=1 → strategy/tune.py → SQLite cache → build/IC/backtest/reinforce
  Cache NEVER used in production
```

---

## v9.4 Final Performance (21 stocks, Tushare OHLCV)

| Market | Stocks | Sharpe | Positive |
|:---|:---:|:---:|:---:|
| **US** | 7 | **2.54** | 5/7 |
| **HK** | 6 | **1.03** | 4/6 |
| **A** | 8 | **1.14** | 4/8 |
| **ALL** | **21** | **1.57** | **13/21** |

### Per Stock
```
US:  GOOGL 7.37 | NVDA 7.18 | MSFT 2.59 | AAPL 1.82 | JPM 1.16 | AMZN -0.33 | META -2.00
HK:  阿里 2.41 | 美团 2.33 | 小米 1.94 | 腾讯 1.42 | 比亚迪 -0.25 | 港交所 -1.66
A:   宁德 7.08 | 恒瑞 3.18 | 平安 0.80 | 比亚迪 0.51 | 茅台 -0.37 | 五粮液 -0.24 | 招行 -0.53 | 紫金 -1.31
```

---

## Parameters

| Parameter | US | HK | A |
|:---|:---:|:---:|:---:|
| **Weights T/C/F/M/Fib** | 38/24/14/19/5 | 35/25/15/20/5 | 25/35/15/20/5 |
| BB penalty (strong/mod/weak) | 8/5/3 | 8/5/3 | 8/5/3 |
| Chain bonus C2/C3 | 15/22 | 15/22 | 15/22 |
| Entry threshold | 48 | 45 | 45 |
| Bear entry | — | 46 | 46 |
| Exit threshold | 22 | 20 | 20 |

---

## Factor Inventory

| Signal | Market | Score | Notes |
|:---|:---|:---:|:---|
| bb_buy (weekly BOLL) | ALL | +15 | Strongest single signal |
| golden_pit | ALL | +10 | DZH indicator |
| nine_turns_buy | ALL | +10 | DZH indicator (self-computed) |
| band_king_buy2 | ALL | +10 | DZH indicator |
| boll_kdj_chain (C2) | ALL | +15 | Adaptive window 3-8 |
| boll_kdj_macd_chain (C3) | ALL | +22 | Adaptive window 3-8 |
| fib_divergence_combo | ALL | +22 | Fibonacci + divergence |
| bullish_divergence | ALL | +12 | Price-MACD divergence |
| volume_anomaly | ALL | +8 | >1.5× MA20 |
| northbound_inflow | A | +6 | 沪深港通 northbound |
| mff_strong | A | +6 | Main force flow |
| mff_moderate | A | +3 | Main force flow |
| margin_overheat | A | -5 | 融资 +5% over 5d |
| margin_extreme | A | -8 | 融资 +200% spike |
| triple_confirm | ALL | +3 | Contrarian ∩ vol ∩ momentum |
| spread_inverted | ALL | -5 | Yield curve inverted |
| chip_tight | A | +3 | >50% within ±10% price |
| holder_consolidate | A | +2 | Holders decreased >3% |
| repurchase | A | +4 | Quarterly buyback event |
| institutional_survey | A | +2 | Institutional visit |

---

## Lessons from Build Process

1. **Per-market weights are essential** — unified T36/C26 crashed A-stocks to S=-0.70. A-specific T25/C35 brought them to S=+1.14.
2. **Data source matters** — Tushare qfq vs yfinance different OHLCV. Same code, S=0.63→0.32 shift just from data source.
3. **Weak signals are better than strong ones** — ±2-3pt additions improve calibration. ±5-6pt additions disrupt it.
4. **Not all patterns can be automated** — Elliott Wave fired 10-37× per stock, killed all BUYs. Kept as diagnostic only.
5. **Isolation is safety** — 修炼模式 cache NEVER touches production pipeline.

## Files Changed in v9.4

| File | Changes |
|:---|:---|
| `strategy/scoring.py` | Per-market weights, triple_confirm, margin_extreme, chip/holder/events |
| `strategy/data_fetcher.py` | Tushare primary for all OHLCV |
| `strategy/elliot_wave.py` | NEW — Wave5 + right shoulder detection (diagnostic only) |
| `strategy/gushen_cache.py` | NEW — SQLite cache (tune mode only, GUSHEN_TUNE=1 guard) |
| `strategy/tune.py` | NEW — 修炼模式 workflow (build→IC→backtest→reinforce) |
| `scripts/normalize.py` | Zhipu LLM stock name resolver (replaced hardcoded map) |
| `strategy/a_factors.py` | PB + MFF data (production, unchanged logic) |
| `strategy/config.py` | Updated to v9.4 data sources |
| `SKILL.md` | v9.4 strategy section, 修炼模式, per-market weights |
| `references/` | 5 reference docs (factor report, pipeline plan, roadmaps) |
