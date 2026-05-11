# Gushen — Data Pipeline Reliability Plan

> Problem: Eastmoney blocks evenings/weekends. yfinance rate limits. FRED 500s.
> Solution: Offline-first cache + multi-source fallback. Zero new costs.

---

## Current State (What Breaks)

| Source | What It Provides | Failure Mode | Frequency |
|:---|:---|:---|:---:|
| Eastmoney (akshare) | A-stock OHLCV, MFF, margin, northbound, macro | Connection refused | Evenings, weekends |
| yfinance | US/HK/A OHLCV | Rate limit | After 6-8 calls |
| FRED | VIX, unemployment, CPI | 500 errors | Random (~30% of calls) |
| Baidu (akshare) | PB/PE valuation | Works | Rare |

## The Fix: Offline-First Architecture

```
                    ┌──────────────────┐
                    │   Local Cache    │
                    │  data/ohlcv/     │
                    │  data/margin/    │
                    │  data/macro/     │
                    └────────┬─────────┘
                             │ first try
                    ┌────────▼─────────┐
  Backtest ────────▶│  cache hit?      │──── YES ──▶ use cache
                    └────────┬─────────┘
                             │ NO (new data)
                    ┌────────▼─────────┐
                    │ Fetch from API   │
                    │ with retry×3     │
                    │ + backoff        │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ Save to cache    │
                    └──────────────────┘
```

### Phase 1: One-Time Cache Build (Today)

Pull ALL historical data once, cache forever. Incremental only for new trading days.

| Data | Source | Period | Time | Cache Path |
|:---|:---|:---|:---:|:---|
| US/HK OHLCV | yfinance | 2021-2026 | 2 min × 13 stocks | `data/ohlcv/` |
| A-stock OHLCV | akshare (market hours) | 2021-2026 | 2 min × 10 stocks | `data/ohlcv/` |
| Margin balance | akshare (market hours) | 2023-2026 | 40 min (batch) | `data/margin_history/` ✅ done |
| MFF flow | akshare | rolling 120d | 30s × 10 = 5 min | `data/mff/` |
| PB/PE | baidu (akshare) | 1 year daily | 5s × 10 = 1 min | `data/valuation/` |
| US macro | FRED | 2021-2026 | 3s × 5 series = 15s | `data/macro/` |
| China macro | akshare | 2021-2026 | 1 min | `data/macro/` |

**Total one-time build: ~60 minutes. Done once, never repeat.**

### Phase 2: Daily Incremental (Automation)

Run once after market close (3:30 PM HK). Only pull today's data, append to cache.

```python
# Daily: 10 lines of new data per stock
for stock in universe:
    cache = load_csv(f"data/ohlcv/{stock}.csv")
    last_date = cache['date'].max()
    if last_date < today:
        new_rows = api.fetch(stock, start=last_date+1, end=today)
        cache = pd.concat([cache, new_rows])
        cache.to_csv(...)
```

**Daily cost: 30 seconds. 10 API calls total.**

### Phase 3: Multi-Source Fallback (Scoring Resilience)

When scoring, try sources in order:

```python
def get_ohlcv(ticker, market):
    # 1. Local cache (always first — 0ms)
    cache = load_csv(f"data/ohlcv/{ticker}.csv")
    if len(cache) >= 250:  # enough for 1yr lookback
        return cache
    
    # 2. yfinance (secondary)
    try: return yf.download(ticker, ...)
    except: pass
    
    # 3. akshare (tertiary, A-stocks only)
    if market == 'A':
        try: return ak.stock_zh_a_hist(...)
        except: pass
    
    # 4. Stale cache (last resort)
    return cache  # use what we have, even if outdated
```

---

## baostock Assessment

baostock provides A-stock OHLCV and financial statements — but:
- Hangs on Python 3.13 (SIGTERM on login)
- Uses Sina/Baidu backends — same network path as akshare
- No margin, MFF, macro, northbound data
- **Verdict: Not worth adding.** Adds a dependency without covering gaps. Same network issue, fewer features.

---

## Cost Breakdown

| Option | Cost | What You Get |
|:---|:---:|:---|
| **Local cache (this plan)** | **$0** | All data, self-hosted, offline |
| Tushare Pro (2000积分) | ¥200/year | Faster API, more stable |
| JoinQuant | Free (reg required) | A-stock OHLCV + fundamentals |
| Wind | $2000+/year | Professional, everything |

**Recommended: Local cache ($0).** Already built for margin. Extend to OHLCV, macro, valuation. One 60-minute batch run today covers all backtesting forever.

---

## Implementation

Need to run one batch today:
1. Pull A-stock OHLCV to `data/ohlcv/` (10 stocks, market hours)
2. Pull macro to `data/macro/` (FRED + akshare)  
3. Pull PB/PE to `data/valuation/` (already works, reliable)

Then update `data_fetcher.py` to read from cache first. Done.
