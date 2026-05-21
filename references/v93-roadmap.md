# Gushen v9.3 — Data-Driven Sharpe Roadmap

> v9.2 locked at Sharpe 1.38. Next improvements need new data, not scoring tweaks.

---

## The Three Data Gaps

| # | Data | Source | Why It Matters | Status |
|:---:|:---|:---|:---|:---:|
| 1 | **Margin financing history** | akshare stock_margin_detail | RankIC=-0.09 (6/6 A-stocks). Contarian signal: margin↑=bearish. | ⏳ per-day API, need batch |
| 2 | **MFF long window** | akshare stock_individual_fund_flow | 120-day snapshot exists. 2yr history for proper walkforward. | ⏳ need 500-day accumulation |
| 3 | **Northbound individual** | akshare stock_hsgt_hold_stock_em | Snapshot only (Aug 2024). No historical API found. | ❌ blocked |

---

## Build Plan

### Step 1: Margin Data Pipeline (Week 1)

```
Problem: stock_margin_detail_sse(date=D) is per-day per-exchange. 
         500 days × 6 stocks × 2 exchanges = too slow live.
Solution: One-time batch script. Run once, cache, never repeat.
```

**Script:**
```python
# Pull 2 years of daily margin data for 10 A-stocks
# From akshare stock_margin_detail_sse/szse, one day at a time
# Cache to CSV per stock in data/margin_history/
# ~1000 API calls, 25 minutes. Run once, done.
```

**Output:**
```
data/margin_history/
  600519.csv  → 500 rows: date, margin_balance, margin_buy, short_balance
  000858.csv
  ... (10 stocks)
```

### Step 2: Margin IC + Backtest (Week 1)

Once the CSV cache exists, the integration is 3 lines:

```python
# In score_bar(), A-stock capital section:
margin_5d = (margin_now - margin_5d_ago) / margin_5d_ago
if margin_5d > 0.05: cap -= 5  # contarian: overheating
elif margin_5d < -0.05: cap += 3  # contarian: panic
```

**Expected:** A-stock Sharpe +0.10-0.20 (RankIC=-0.09 is strong).

### Step 3: MFF Long Window (Weeks 2-4, automatic)

**Problem:** stock_individual_fund_flow returns only current 120-day window. Need to accumulate daily snaps.

**Solution:** Daily cron job appending to CSV:
```python
# Run every market close, append to data/mff_history/{code}.csv
# After 3 months → 190 days. After 1 year → 380 days for walkforward.
```

**Expected:** MFF IC currently +0.025 (weak alone). With 1yr window: enables proper walkforward. Combined with margin factor: institutional + retail flow separation.

### Step 4: Northbound — Research Alternative (Ongoing)

stock_hsgt_hold_stock_em returns only current snapshot (Aug 2024 date). Options:
- **A)** Search akshare for undocumented params (date= parameter)
- **B)** yfinance `.T` suffix for A-stock institutional data
- **C)** Eastmoney direct API discovery (sniff network calls)
- **D)** Accept snapshot-only: use current northbound ownership % as static bias

---

## Timeline

```
Week 1:   Margin batch pull script → CSV cache → IC + backtest
Week 2:   If margin IC confirmed → integrate into scoring → v9.3
Weeks 2-4: MFF daily cron accumulates silently
Month 2:   MFF 1yr window → full walkforward backtest
Ongoing:   Northbound research
```

---

## Immediate Next Step

Write and run the margin batch pull. ~25 minutes, one-shot. Want to go?
