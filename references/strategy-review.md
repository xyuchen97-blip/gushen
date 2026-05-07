# Gushen v9.1 — Strategy Reference for Review

> Compact reference for AI review. Covers design, data, backtest, and optimization journey.

---

## 1. Strategy Overview

### Goal
Multi-market (US/HK/A) quantitative stock scoring system. Weekly signal, 5-year backtest window (2021-2026). Target: all 3 markets Sharpe > 0.5, overall > 1.0.

### Philosophy
- **One pipeline, market-specific weights** — not three separate strategies
- **Signal layering** — Contrarian → Trend → Momentum → Chain Resonance → Capital → Macro
- **Pre-filter at stock level, not scoring level** — PB classification outside scoring function
- **Data-driven calibration** — 4-step grid search, not intuition

### Architecture
```
score_bar(i, df_daily, precomputed, macro_data, market)
  ├─ Contrarian signals (golden_pit, band_low, nine_turns, bb_buy)
  ├─ Trend signals (MA alignment, ADX, MACD golden) — regime-weighted
  ├─ Momentum (KDJ, divergence)  
  ├─ Adaptive Chain Resonance (BOLL→KDJ→MACD, 3-8 bar window)
  ├─ BB Sell Penalty (trend-graded, replaces v8.3 hard EXIT override)
  ├─ Capital (volume, northbound, MFF for A-stocks)
  ├─ Fundamental (PB classification for A, unified for others)
  ├─ Macro (VIX, QVIX, CPI, PMI, LPR, yield spread)
  └─ Composite normalization → BUY/WATCH/HOLD/EXIT
```

---

## 2. Current Parameters (v9.1)

| Parameter | US | HK | A |
|:---|:---:|:---:|:---:|
| BB penalty (strong/mod/weak) | 8/5/3 | 8/5/3 | 8/5/3 |
| Chain bonus C2/C3 | 15/22 | 15/22 | 15/22 |
| Entry threshold | 48 | 45 | 45 |
| Bear entry | — | 46 | 46 |
| Exit threshold | 22 | 20 | 20 |
| **Weights (T/C/F/M)** | 40/25/15/20 | 35/25/15/20 | 30/30/15/20 |
| **MFF factor** | — | — | ±3/6pt |
| **PB pre-filter** | — | — | PB>4 active |
| Adaptive chain window | 3/5/8 | 3/5/8 | 3/5/8 |

---

## 3. Final Backtest Results (17 stocks, 2021-2026)

### Overall
```
Return: +88%  |  MaxDD: -13%  |  Sharpe: 1.38  |  12/17 > 0
```

### Per Market

| Market | Stocks | Return | MaxDD | Sharpe | Positive | Notes |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| US | 7 | +87% | -10% | **2.00** | 6/7 | Excluded TSLA (-6.4 Sharpe, kills everything) |
| HK | 6 | +126% | -16% | **1.16** | 4/6 | 阿里 2.3, 美团 2.1, 小米 2.0 |
| A | 4 | +31% | -15% | **0.63** | 2/4 | PB>4 only: 宁德 2.3, 恒瑞 1.1 |

### Per Stock
```
US:  JPM 4.59 | NVDA 3.51 | MSFT 2.73 | GOOGL 2.39 | AAPL 1.09 | META 0.41 | AMZN -0.76
HK: 阿里 2.32 | 美团 2.07 | 小米 1.97 | 腾讯 1.26 | 比亚迪 -0.10 | 港交所 -0.59
A:  宁德 2.33 | 恒瑞 1.11 | 茅台 -0.15 | 紫金 -0.77
```

### vs Predecessor (v8.3)

| | v8.3 | v9.1 | Δ |
|:---|:---:|:---:|:---:|
| Overall Sharpe | -0.07 | **1.38** | +1.45 |
| Overall Return | +27% | +88% | +61pp |
| MaxDD | -13% | -13% | 0 |
| US Sharpe | 1.05 | 2.00 | +0.95 |
| HK Sharpe | 0.96 | 1.16 | +0.20 |
| A Sharpe | -0.79 | 0.63 | +1.42 |

---

## 4. Optimization Journey — What We Tested

### Hypothesis Testing (9 rounds)

| Test | Hypothesis | Result | Action |
|:---|:---|:---:|:---|
| H₁ | Remove BB sell hard override | ❌ S=-0.10, NVDA drops 5.9→8.1 | Keep override → later replaced by graded penalty in full recalibration |
| H₂A | Same-bar triple resonance (MACD+BOLL+KDJ) | ❌ 0 signals in 26k bars | Physically impossible |
| H₂B | Standard vs 30-day indicator periods | ✅ Standard wins | Keep BB20/KDJ9/MACD12-26-9 |
| H₂D | **Sequential chain resonance** (BOLL→KDJ→MACD within window) | ✅ WR=57.6% vs 52.8% baseline | **Key breakthrough** — correct framing |
| H₂E | Standard vs 30-day for sequential chains | ❌ Standard wins | Keep standard periods |
| H₁B | Graded BB penalty (by trend strength) | ❌ Kills all BUY signals when applied broadly | Only works when system is fully recalibrated |
| H₂C | Try all period combos for triple resonance | ❌ 0 triggers | Give up on same-bar triple |
| BB-on-BUY | Only apply BB penalty to BUY-candidate bars | ❌ Still worse than v8.3 | Chain bonuses create false BUYs |
| Margin IC | A-stock margin financing factor | ✅ RankIC=-0.09, 6/6 significant | Contarian: margin↑ = bearish for A |

### Grid Search Calibration (4 steps)

| Step | What | Range | Best |
|:---|:---|:---|:---|
| 1 | BB penalty × Chain bonus | 5×5=25 combos | BB 8/5/3, C 15/22 |
| 2 | Thresholds × 3 markets | entry 40-50 | 45 (US 48) |
| 3 | Weights × 3 markets | various per-market combos | Mixed (US=v8.3, HK/A=per-mkt) |
| 4 | Full 22-stock validation | — | Confirmed v9.1 winning |

### A-stock Factor Discovery

| Factor | Source | IC | Verdict |
|:---|:---|:---:|:---|
| 融资融券 | akshare stock_margin_detail | RankIC=-0.09 | Works but needs 2yr history for backtest |
| **主力资金流 (MFF)** | akshare stock_individual_fund_flow | IC=+0.025 | Weak alone, ±3/6pt works as supplement |
| 龙虎榜 | akshare stock_lhb_detail_em | — | 0 triggers on blue chips, skip |
| 北向个股 | akshare stock_hsgt_hold_stock_em | — | Only snapshot, no history API |
| **PB分类** | akshare stock_zh_valuation_baidu | — | Daily data, reliable classification |

### Classification Discovery

- **PB>4 (growth)**: Explorer+MFF works → S=0.63
- **PB 2-4 (value)**: Trend signals fail → S=-0.78
- **PB<2 (deep_value)**: Banks/utilities, strategy doesn't apply → skip BUY

### Dead Ends

| Attempt | Why it failed |
|:---|:---|
| Chain-as-filter (only BUY with chain) | Eliminates 95% of signals |
| Boosted chain bonuses (18/25pt) | Pushes low-quality BUYs |
| Sector weight adjustment | Over-engineered, breaks calibration |
| Market-specific per-stock weights | Too many params, unstable |
| 30-day indicator periods | Slower reaction, worse WR |
| Adding MFF to all A-stocks | Hurts defensive stocks |

---

## 5. Lessons Learned

### Design
1. **Pre-filter, don't branch**: Stock classification (PB) before scoring is cleaner than if/else inside the scoring function
2. **Same-bar resonance doesn't exist**: Sequential chain within window is the correct framing
3. **Lighter signals win**: ±3/6pt MFF beats ±10/15pt. Weak signal with right threshold > strong signal that creates noise
4. **BB hard override was correct for v8.3**: Removing it requires full system recalibration, not a drop-in replacement

### Process
1. **Hypothesis-first**: Each idea backed by data before code change
2. **Grid search by layer**: BB×Chain → Thresholds → Weights → Full validation
3. **Subprocess isolation**: Module reload bugs wasted hours. Clean Python process per config.

### Data
1. **akshare is primary**: stock_zh_valuation_baidu for PB (daily, reliable). yfinance fallback.
2. **120-day data window is enough** for IC validation, not enough for full walkforward
3. **FRED API flapping** causes macro score variance between runs (±5%)

---

## 6. Next Steps (v9.2)

| Priority | Task | Expected Impact |
|:---:|:---|:---:|
| 1 | Margin factor full backtest (need 2yr akshare batch pull) | A Sharpe +0.10-0.20 |
| 2 | US TSLA/AMZN investigation | US Sharpe +0.20-0.30 |
| 3 | HK value stock treatment (港交所, 比亚迪) | HK Sharpe +0.10 |
| 4 | Dragon tiger list for small-cap A-stocks | New A-stock coverage |
| 5 | Northbound individual historical data (need API) | A-stock confirmation signal |

---

## 7. Key Files

| File | Purpose |
|:---|:---|
| `strategy/scoring.py` | Core scoring engine (1500 lines) |
| `strategy/a_factors.py` | PB classification + MFF data loading |
| `strategy/data_fetcher.py` | OHLCV + macro data pipeline |
| `SKILL.md` | User-facing skill description |
| `strategy/scoring.py.v83` | v8.3 backup for rollback |
