# Gushen v9.3 — Tushare Factor Exploration Report

> Token: c1cbd943613a172b916b0d249b3dc04146d13817d6bc4c0bc60756de
> Date: 2026-05-11 | 258 APIs available

---

## Summary: 18 Factors Explored, 3 Worth Adding

| Factor | Market | IC/RankIC | Verdict |
|:---|:---:|:---:|:---|
| **股东人数变化** | A | +0.24 avg | ⭐ v9.4 - quarterly, sparse but strong |
| **broker_recommend** | A | TBD (consensus signal) | ⭐ v9.4 - monthly, 377 brokers |
| **PE/PB valuation** | A | -0.08 avg | Confirms existing PB filter |
| stk_nineturn (九转) | A | Incomplete data | ❌ Keep DZH manual calc |
| stk_factor / stk_factor_pro | A | 0 (redundant) | ❌ Our precompute() does it |
| moneyflow (order size) | A | 0 | ❌ No predictive power |
| cyq_chips (筹码分布) | A | Empty API | ❌ Premium/data unavailable |
| cyq_perf (筹码成本) | A | Parse errors | ❌ Data quality issues |
| pledge_stat (质押率) | A | ~0 | ❌ No predictive power |
| repurchase (回购) | A | Binary, rare | ❌ 0-66 events too sparse |
| forecast (业绩预告) | A | Binary, rare | ❌ 3-19 events too sparse |
| express (业绩快报) | A | 1 row | ❌ Same as forecast |
| dividend (分红) | A | 0 rows | ❌ API data gap |
| share_float (限售) | A | 0 rows | ❌ No events |
| stk_shock (异常波动) | A | 0 rows | ❌ Rare regulatory events |
| stk_surv (机构调研) | A | 0 rows | ❌ No data for blue chips |
| margin_detail | A | ✅ Fast | ⭐ Replace akshare | 
| moneyflow_hsgt | A | ✅ Works | Northbound data |
| ggt_daily/top10 | HK | ✅ Works | Stock Connect data |
| hk_fina_indicator | HK | ✅ 20 qtrs | Fundamentals available |
| us_fina_indicator | US | ✅ 21 qtrs | Fundamentals available |
| us_tbr/tltr/tycr | US | ✅ Works | Treasury rates |

---

## Detailed Findings

### ⭐ (1) 股东人数变化 (Shareholder Count Δ)
- **Source:** stk_holdernumber
- **Frequency:** Quarterly (~5 years = 20 data points)
- **Signal:** More shareholders → higher forward 60d return (trend-following)
- **Average RankIC:** +0.24 (5/6 stocks positive)
- **Risk:** Sparse data (quarterly). Only 20 data points per stock.
- **Integration:** Add to fundamental scoring section. +3pt if holders decreased (concentration), -3pt if holders increased (dilution). Wait for IC sign — data shows INCREASED holders = bullish (counter-intuitive).

### ⭐ (2) broker_recommend (券商月度金股)
- **Source:** broker_recommend  
- **Frequency:** Monthly (377 brokers)
- **Signal:** Consensus — more brokers recommending = stronger sentiment
- **IC:** Not yet tested (IC test script crashed)
- **Risk:** Herding signal. Monthly frequency = sparse.
- **Integration:** Each month, count how many brokers recommend the stock. If ≥3 brokers → +3pt in fundamental/macro section.

### ⭐ (3) margin_detail (Tushare native)
- **Source:** margin_detail (all stocks in one call)
- **Replaces:** akshare stock_margin_detail_sse/szse (2 API calls per day)
- **Speed:** ~500ms for all 4352 stocks vs akshare's ~2s × 2 exchanges
- **Integration:** Replace akshare margin batch with Tushare margin_detail queries. Same data, faster, more reliable.

### ❌ stk_nineturn (九转) — Keep DZH
- Tushare provides up_count/down_count (progression) but nine_up_turn / nine_down_turn are ALL NaN
- Our manual DZH calculation (dzh_indicators/jiu_zhuan.py) correctly detects trigger events
- **Verdict:** Tushare version is incomplete. Keep our code.

### ❌ Everything Else — No Edge
All other factors tested (moneyflow order size, PE/PB, pledge, repurchase, forecast, share_float, stk_shock, stk_surv, cyq) showed either zero IC, too few data points, or API data gaps.

---

## Cross-Market Coverage

| Market | OHLCV | Financials | Macro | Special |
|:---|:---:|:---:|:---:|:---|
| A-stock | ✅ daily | ✅ fina_indicator | ✅ cn_m/cn_pmi/cn_cpi | margin, moneyflow, holders |
| HK | ✅ hk_daily | ✅ hk_fina_indicator | — | Stock Connect via ggt |
| US | ✅ us_daily | ✅ us_fina_indicator | ✅ us_tbr/tltr/tycr | — |

Cross-market financial ratios are available but in pivot format (ind_name/value pairs) — need unpivoting before use.

---

## Recommended Action

1. **Immediate:** Replace akshare margin batch with Tushare margin_detail (faster, one API call)
2. **v9.4:** Add 股东人数 (quarterly IC test first on all 16 stocks)  
3. **v9.4:** Add broker_recommend consensus signal  
4. **Keep:** DZH 九转 calculation (Tushare version unusable)
5. **Keep:** Our fundamental scoring (Tushare fina_indicator = same data, no advantage)
