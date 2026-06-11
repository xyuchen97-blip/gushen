# Gushen Strategy Optimization — Session Handoff

> **Date**: 2026-06-10
> **From**: Opus session (May-June 2026, ~68 tasks completed)
> **To**: Next AI session (strategy optimization focus)
> **Status**: v10.2 stable, data pipeline upgraded, ready for next optimization cycle

---

## Quick Start (v12 — June 10, 2026)

```bash
cd ~/Desktop/gushen_handoff

# CANONICAL backtest (frozen v12 stack, 54-name universe, portfolio metric):
python3 scripts/m4_portfolio.py --volsize --universe v13

# 10-year true-OOS validation (frozen stack, 2016-2026):
python3 scripts/longrun_2015.py

# v12 reference: v13 universe ALL S=+1.04 dd -12.4% | 10y S=+1.49 dd -18%
# era1 2016-21 (true OOS) +1.64 vs B&H +1.60 — validation PASSED (proposal §6d)

# PRODUCTION: score() in strategy/scoring.py now applies the full v12 stack
# (hold-exits + hysteresis DEFAULT ON, FRAGILE/NA gate, sizing hint).
# Sync to WorkBuddy:  cp -r strategy dzh_indicators ~/.workbuddy/skills/gushen/

# LEGACY (deprecated for headline numbers — no bucket gate, per-stock metric):
GUSHEN_TUNE=1 python3 strategy/fast_backtest.py
```

## NEXT STEPS (as of June 10, 2026 — read ARCHITECTURE_v12_PROPOSAL.md §6b-6e first)

**Owner decisions pending:**
1. Promote 132-name universe + top-30 cross-sectional selection to production/shadow?
   (v14 evidence in §6e; changes what Gushen watches — owner's call)
2. Eyeball trade sequences (data/longrun_progress.pkl, data/xsel_progress.pkl)
3. Sync to WorkBuddy: cp -r strategy dzh_indicators ~/.workbuddy/skills/gushen/
4. Regenerate FRED + Alpha Vantage API keys (were pasted in chat June 10)

**Automated:** weekly shadow run Saturdays 9am ("gushen-weekly-shadow-run" scheduled
task) appends to data/shadow_log.jsonl. Click "Run now" once to pre-approve tools.

**v16 BUILT (June 10, late evening) — co-pilot decision layer, all in gushen_v18/:**
- Calibration (`scripts/build_calibration.py` → data/calibration.json): KEY FINDING —
  absolute composite is NOT a return scale (flat ~55% P(+4w) across bins). Its real
  information: entry timing (531 hist entries → 54% pos 4w, avg +1.2%) and
  cross-sectional rank (top30 +0.39%/wk > 31-60 +0.26% > rest +0.16%, monotonic).
  Daily driver now displays rank-tier + entry stats instead of naked points.
- Exit contracts: positions carry {stop, hh_below, max_weeks}; driver enforces (🔴 flags).
- Portfolio-first header: market split + concentration warning before any signal.
- `--review`: discretion ledger report (you-vs-engine; outcome attribution grows with history).

Original v16 plan (for reference):
1. CALIBRATION LAYER (centerpiece): map composite ranges → empirical forward-return
   distributions from stored passes (data/dailywide_progress.pkl, xsel_progress.pkl)
   so the tool speaks probabilities ("comp 40+ → 68% positive 20d") not points.
2. EXIT CONTRACTS: every position in my_positions.json carries exit rules from day
   one; daily driver enforces/reminds.
3. PORTFOLIO-FIRST morning view: exposure, concentration, dd state before signals.
4. Discretion ledger review tooling (already logging since June 10).
Rationale: the behavior gap (1.5-4%/yr) is worth more than any feasible Sharpe gain.
Deferred but alive: breadth-to-300, point-in-time fundamentals, sentinel evaluation.

**Research queue (gated, one at a time, rules otherwise FROZEN):**
1. HK structural study — engine lags B&H in HK in BOTH eras; test southbound flow
   and AH-premium signals (akshare has the data)
2. Daily risk layer — thin stop/gap check under the weekly brain (tail protection)
3. Cost sensitivity one-off before any live size-up
4. Point-in-time fundamentals + index membership (kills survivorship, enables
   value/quality factors at 132-name scale)

**Standing rules:** portfolio metric primary; IS/OOS + per-market gates on every
change; repaint test for anything with t>5; pinned snapshots; one change per
experiment; log to data/bt2_results.jsonl; no new rules without new information.

> Historical note: the pre-June-10 documented baseline (ALL S=1.324) was inflated ~30%
> by band_king look-ahead bias. Honest v10.2 baseline: legacy 0.899 / portfolio +0.445.
> Primary metric is portfolio Sharpe. Current references live in the Quick Start above
> and ARCHITECTURE_v12_PROPOSAL.md late-session corrections (superseding §6c/6d where noted).
Goal: improve portfolio Sharpe (IS and OOS both) without regressing any market.

---

## 1. What Is Gushen

A multi-market quantitative stock scoring engine covering 21 stocks across A-share (8), Hong Kong (6), and US (7). It runs weekly, producing BUY/WATCH/HOLD/EXIT signals per stock based on technical indicators, proprietary DZH signals, fundamental data, and macro regime classification.

**Owner**: Josh — Hong Kong-based, uses this for real investment decisions.

### Stock Universe

| Market | Tickers | Current Sharpe |
|--------|---------|---------------|
| A-share | 600519 (茅台), 000858 (五粮液), 300750 (宁德时代), 002594 (BYD), 601318 (平安), 600036 (招行), 002230 (讯飞), 300015 (爱尔眼科) | -0.056 |
| HK | 0700 (腾讯), 9988 (阿里), 3690 (美团), 1810 (小米), 1211 (BYD-H), 0388 (港交所) | 1.570 |
| US | AAPL, NVDA, MSFT, GOOGL, AMZN, META, JPM | 2.689 |

A-share Sharpe is negative — the biggest optimization opportunity. HK and US are strong.

---

## 2. Architecture (score_bar_v5 in scoring.py)

The core design separates contrarian and trend strategies by market regime:

**Stage 1 — Regime Detection**: Daily `close > MA200` classifies each stock as bull or bear. (Doc previously claimed weekly MA50+MA200 — corrected June 2026 after code audit.)

**Stage 2 — Dual-Mode Scoring**:
- Bear engine: contrarian depth signals (KDJ, Bollinger, RSI scored 0-10 continuously) + DZH binary signals (Golden Pit, Nine Turns, Band King) + P4 trend at 40% discount
- Bull engine: P1+P4 trend-following + Fibonacci pullback gate

**Stage 3 — Volume Confirmation**: Volume anomaly multiplier (shared).

**Stage 3.5 — Analyst/Earnings Signals** (v10.2): US earnings beat streaks (+1.5-2.0 bonus). A-stock and HK analyst signals disabled (hurt Sharpe in testing). **⚠ June 2026 audit: `fund_bonus` is computed but NEVER added to `composite` — Stage 3.5 has zero decision impact in all current code paths. Also, only tune.py supplies `analyst_signals`; fast_backtest.py never did. The v10.2 Sharpe claims need re-validation. Env flag `GUSHEN_FUND_IN_COMPOSITE=1` enables inclusion for testing.**

**Stage 4 — Threshold → Action**: Per-market thresholds convert raw composite score to BUY/WATCH/HOLD/EXIT:
```python
V10_THRESHOLDS = {
    "US": {"bear_buy": 32, "bear_watch": 24, "bear_exit": 10,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 10},
    "HK": {"bear_buy": 28, "bear_watch": 20, "bear_exit": 10,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 10},
    "A":  {"bear_buy": 25, "bear_watch": 17, "bear_exit": 0,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 0},
}
```

**Stage 5 — Macro Multiplier**: VIX + QVIX + yield spread + PMI → 0.5x-1.3x position sizing hint (not applied to scoring).

### Key Design Principles

1. **Contrarian in bear, trend in bull** — the core v10 insight that drove S from 0.94 to 1.24
2. **Binary for entry/exit, continuous for depth** — don't smoothify entry signals
3. **Precompute once** — all indicators backward-looking, compute on full data, index per bar
4. **A-stock exit=0** — A-stocks score low naturally; fixed exit threshold kicks them out too early
5. **Macro at portfolio level only** — stock-level macro scoring was net drag (-36%)

---

## 3. File Map (What to Read/Edit)

| File | Lines | Purpose | Touch for optimization? |
|------|-------|---------|------------------------|
| `strategy/scoring.py` | ~3010 | **Main engine**: precompute() + score_bar_v5() | YES — this is where signals live |
| `strategy/fast_backtest.py` | ~120 | Standalone backtest runner | YES — run this to test changes |
| `strategy/tune.py` | ~300 | Full backtest with ablation support | Maybe — for deeper experiments |
| `strategy/config.py` | ~100 | Constants documentation | Reference only |
| `strategy/data_fetcher.py` | ~1100 | Data pipeline (TV screener → akshare → FRED) | Only if adding new data sources |
| `strategy/gushen_cache.py` | ~400 | SQLite OHLCV cache (tune mode) | Only if adding new cache tables |
| `strategy/bollinger.py` | ~80 | BB weekly buy/sell signals | Maybe |
| `strategy/fibonacci.py` | ~100 | Fibonacci retracement scoring | Maybe |
| `strategy/elliot_wave.py` | ~120 | Wave5/shoulder/triple-confirm | Maybe |
| `strategy/a_factors.py` | ~80 | A-stock PB + main force flow | Maybe — A-stock optimization |
| `dzh_indicators/golden_pit.py` | ~100 | Golden Pit 2.0 signal | Advanced |
| `dzh_indicators/jiu_zhuan.py` | ~80 | Nine Turns (9-turn) signal | Advanced |
| `dzh_indicators/band_king.py` | ~80 | Band King buy/sell signal | Advanced |
| `research_hypotheses.py` | ~200 | H1(cross-mkt)+H2(OpEx) research | Reference — both hypotheses tested, both failed |
| `HANDOFF.md` | ~310 | Full project history | Reference — detailed version history |
| `data/gushen.db` | ~50MB | SQLite OHLCV+indicators cache | Don't modify manually |

### How scoring.py is organized internally

```
Lines 1-40:     Header, imports
Lines 40-100:   precompute() — all indicator computation
Lines 100-200:  Continuous signal functions (KDJ depth, BB depth, RSI depth)
Lines 200-300:  DZH binary signal wrappers
Lines 300-390:  compute_macro_mult() — Stage 5
Lines 393-850:  score_bar_v5() — THE MAIN ENGINE (5 stages)
Lines 850-900:  score() — high-level entry point
Lines 900+:     Legacy v9.x functions (archived, do not use)
```

---

## 4. Per-Stock Sharpe Breakdown (latest snapshot: June 7, 2026)

### Strong performers (S > 1.0)
| Stock | Sharpe | Trades | Notes |
|-------|--------|--------|-------|
| NVDA | 5.644 | 4 | Best — few but high-quality signals |
| GOOGL | 4.771 | 14 | Consistently strong |
| 0388.HK (港交所) | 4.170 | 17 | HK star |
| JPM | 3.066 | 7 | Finance sector works in US |
| MSFT | 3.123 | 10 | Strong |
| 3690.HK (美团) | 2.613 | 21 | High trade count + good Sharpe |
| 1810.HK (小米) | 1.967 | 11 | Solid |
| 002594.SZ (BYD-A) | 1.243 | 19 | A-stock bright spot |
| AMZN | 1.065 | 10 | Decent |

### Weak performers (S < 0)
| Stock | Sharpe | Trades | Problem |
|-------|--------|--------|---------|
| 601318.SH (平安) | -1.772 | 25 | Insurance/financial sector — contrarian signals don't work |
| 300015.SZ (爱尔眼科) | -0.821 | 82 | Way too many signals fired — threshold too low? |
| 002230.SZ (讯飞) | -0.581 | 25 | AI sector volatile, signals unreliable |
| 0700.HK (腾讯) | 0.000 | 2 | Too few signals — thresholds too conservative |
| 1211.HK (BYD-H) | 0.000 | 2 | Same — cross-listing with BYD-A creates regime confusion |

### Key observation
- A-stock average Sharpe = -0.056 (dragging overall down)
- A-stock 爱尔眼科 fires 82 trades vs US average ~10 — signal noise problem
- Ping An is structurally bad (financial sector ≠ contrarian tech signals)
- Tencent and BYD-H have too few signals (2 trades each)

---

## 5. What Has Been Tried (and FAILED — Do Not Repeat)

These are expensive lessons. Each took 2-8 hours of research + implementation + backtest.

| Attempt | Result | Root Cause | Lesson |
|---------|--------|-----------|--------|
| Z-score normalization | -27% signal power | Cross-stock normalization unnecessary with per-market thresholds | Don't normalize across stocks |
| Multiplicative layer stacking (L2×L3) | -60% Sharpe | base < 0.95 crushes scores even when neutral | Never multiply factors with base < 0.95 |
| Continuous signals for entry/exit | -63% Sharpe | Continuous glides too slowly for EXIT decisions | Keep binary for entry/exit triggers |
| Stock-level fundamental scoring | IC ≈ 0 | Quarterly data too noisy for weekly scoring | Fundamentals at portfolio level only |
| Stock-level macro scoring | -36% drag | Over-penalizes individual stocks | Macro at portfolio sizing level only |
| Price-based trailing stops | Marginal | Weekly bars too coarse; daily bars only S=1.095 | Not worth the complexity |
| Cross-market momentum (HK→US) | -0.385 US Sharpe | Momentum conflicts with contrarian engine | Statistical significance ≠ profitable implementation |
| OpEx Friday gate | Not significant | Only 62 samples, p=0.23 | Need >>100 samples for calendar signals |
| A-stock Tushare earnings forecast | -0.46 Sharpe on some stocks | 业绩预告 categories too coarse for daily scoring | Disabled in production |
| Change-based fundamentals (ΔQ/Q) | Net negative | Quarterly frequency too noisy for weekly engine | Replaced by analyst signals |

### The Cardinal Rule
**Never bolt a momentum signal onto a contrarian engine as an additive score bonus.** The v10.3 cross-market experiment proved this conclusively: statistically significant correlation (r=+0.114, p=0.0001) but implementation hurt US Sharpe from 2.689 to 2.304 because it pushes marginal signals over the BUY threshold.

---

## 6. Promising Optimization Directions

These are untested ideas that have theoretical merit:

### 6a. Fix A-Stock Sharpe (highest impact)
A-stock Sharpe is -0.056, dragging overall from ~1.8 to 1.3. Possible approaches:
- **Sector-aware thresholds**: Ping An (insurance) and 招行 (banking) behave differently from tech stocks. Per-sector or per-stock threshold tuning could help, BUT be careful of overfitting — only 8 A-stocks.
- **Signal frequency control for 爱尔眼科**: 82 trades suggest the A-stock bear_buy threshold (25) is too low for some stocks. A signal cooldown period (min N bars between BUY signals) could reduce noise.
- **A-stock-specific indicators**: Main force flow (主力资金流向), margin financing ratio trends, and northbound flow (北向资金) are already partially implemented. Deeper integration could help.

### 6b. Add tradingview-ta Validation Layer
The `tradingview-ta` library returns 91 pre-computed indicators per stock in ~360ms. It could be used to:
- Cross-validate our KDJ/RSI/MACD calculations against TV's versions
- Add new indicators we don't compute (Ichimoku, Williams %R, Stochastic RSI, CCI)
- Build an ensemble signal: "buy only when both our engine AND TV's analysis agree"

### 6c. Regime Detection Improvements
Current regime = MA50+MA200 on weekly. This is simple but lags. Possible improvements:
- ADX+DI-based regime (already computed, partially used for "strong_bull" but not for regime switching)
- Volatility regime (high vol = different strategy than low vol)
- Sector rotation detection (A-stock sectors rotate on different cycles)

### 6d. Exit Optimization
Current exits are the weakest part — BB sell override and hold_score breakdown are crude:
- Trailing ATR stop is active but only in tune.py backtest, not in scoring.py production
- Volume-based exit signals (distribution day counting)
- Divergence-based exits (price up + RSI down = distribution)

### 6e. Additional TradingView Data
The tradingview-screener integration opened up access to 200+ fundamental fields. Currently only using 5 (ROE, net margin, EPS, revenue growth, income growth). Could add:
- P/E ratio for valuation-based filtering
- Debt/equity for risk screening
- Institutional ownership changes
- Short interest (US only)

---

## 7. How to Run Experiments

### Step 1: Make a scoring change
Edit `strategy/scoring.py` — typically inside `score_bar_v5()`. The function has clearly marked stages.

### Step 2: Run backtest
```bash
cd ~/Desktop/gushen_handoff
GUSHEN_TUNE=1 python3 strategy/fast_backtest.py
```
This outputs per-stock and per-market Sharpe. Compare against baseline:
```
v10.2 baseline (Jun 2026 macro): ALL S=1.324, A=-0.056, HK=1.570, US=2.689
```

### Step 3: Evaluate
- Overall Sharpe must not decrease
- No market should regress more than -0.1 Sharpe
- Check per-stock: did the change help weak stocks without hurting strong ones?
- Look at trade count: more trades ≠ better (see 爱尔眼科 82-trade problem)

### Step 4: If positive, sync to production
```bash
cp strategy/scoring.py ~/.workbuddy/skills/gushen/strategy/scoring.py
# Also copy any other changed files
```

### Important: Macro Data Drift
The backtest Sharpe drifts over time because FRED/akshare API responses change (they update their historical data). Same code gave S=1.476 in May, S=1.324 in June. **Always compare your changes against a fresh baseline run**, not against historical numbers.

---

## 8. Data Pipeline Summary

```
OHLCV:
  A-shares:  Tushare Pro (primary) → akshare (fallback)
  HK:        Tushare Pro (primary) → akshare (fallback)
  US:        Tushare Pro (primary) → yfinance (fallback)

Fundamentals:
  All markets: TradingView screener (primary, ~0.25s) → akshare (fallback)
  A-shares:    Tushare fina_indicator (production GUTS mode only)

Macro:
  US: FRED API (VIX, yield curve, unemployment, fed funds rate)
  China: akshare (SHIBOR, PMI, northbound flow, QVIX)

Analyst signals:
  US: Alpha Vantage EARNINGS API (quarterly surprise)
  A/HK: Tushare/akshare (cached but scoring disabled)

Dependencies: pip install tradingview-screener tradingview-ta akshare tushare yfinance
```

---

## 9. Environment Setup

```bash
# Required environment variables
export TUSHARE_TOKEN="your_tushare_pro_token"
export FRED_API_KEY="your_fred_api_key"
export ALPHA_VANTAGE_KEY="your_av_key"  # for US earnings signals

# Required packages
pip install tradingview-screener tradingview-ta
pip install akshare tushare yfinance
pip install pandas numpy requests

# Paths
# Handoff (authoritative): ~/Desktop/gushen_handoff/
# Production (workbuddy):   ~/.workbuddy/skills/gushen/
# These should be identical. After any change, sync handoff → workbuddy.
```

---

## 10. Glossary

| Term | Meaning |
|------|---------|
| Sharpe (S) | Risk-adjusted return. S > 1 = good, S > 2 = excellent, S < 0 = losing money |
| DZH (大智慧) | Chinese stock analysis platform; provides proprietary indicators (Golden Pit, Nine Turns, Band King) |
| Contrarian | Buy when oversold (KDJ < 20, BB lower band touch). Works in bear markets. |
| Regime | Bull vs bear classification per stock per bar. Determines which scoring engine runs. |
| Precompute | All indicators calculated on full history upfront, then indexed per bar during backtest. 100x faster. |
| macro_mult | Portfolio-level position sizing hint (0.5x to 1.3x). Not part of stock scoring. |
| P1/P4 | Pillar 1 (KDJ-based entry), Pillar 4 (trend-following). Legacy naming from v9.x architecture. |
| fund_score | Fundamental contribution to composite score. Based on ROE, growth, margins. |
| composite | Final raw score before threshold comparison. composite = entry_score + cap_bonus. |
| cap_bonus | Score bonus from fundamental quality (ROE > 15%, margins, growth). |

---

## 11. v11 Structural Audit & Experiments (June 10, 2026 session)

New harness: `scripts/bt2.py` (pinned macro snapshot, precompute cache, portfolio-level
equity-curve Sharpe, IS/OOS split at 2024-07-01, no silent exceptions, variant flags).
`scripts/run_bt_cached.py` wraps fast_backtest with the pinned macro snapshot.
Full results log: `data/bt2_results.jsonl`. Macro snapshot here lacks FRED series
(no API key in sandbox) — re-pin with `rm data/macro_snapshot.pkl` on a machine with keys.

### ⚠⚠ CRITICAL: band_king look-ahead bias (June 10, 2026 — signals REMOVED)
`band_king.compute_no_future()` is NOT future-free: `_find_peaks_troughs` uses a **centered
window** (`series[i-order:i+order+1]`), so a trough at bar i requires the NEXT 3–35 bars.
Consequences, all verified empirically:
- buy2 event study: +12.1%/20d (t=18) — pure artifact. **0/20 sampled events were visible
  in real time** on the day the backtest credits them (prefix-recomputation repaint test).
- A causal version (trough confirmed `order` bars later) has NO edge (t≈0.5). Not fixable.
- At the live edge the centered window can never fire → production NEVER saw buy2/sell1.
  Backtests got +10 at perfect bottoms; live trading got nothing.
- buy2/sell1 removed from score_bar_v5 (both engines) and from compute_hold_health.
  This is a production no-op but deflates backtest numbers to honest levels:

**Honest official numbers (post-removal, pinned macro, June 10 2026):**
- baseline v10.2 exits: legacy avg S = 0.899 (the documented 1.324 was ~30% look-ahead
  inflation), portfolio S = +0.445, IS -0.302 / OOS +1.118, maxDD -27.1%
- hold-exit + hysteresis (recommended): portfolio S = +0.609, IS +0.354 / OOS +0.903,
  maxDD -17.0%
- golden_pit.py and jiu_zhuan.py audited for the same pattern: clean.
- The old "Quick Start baseline ALL S=1.324" at the top of this file is OBSOLETE.

### Audit findings (bugs/inconsistencies)
1. **Stage 3.5 dead code**: `fund_bonus` never added to `composite`; analyst data only ever
   supplied by tune.py (and the handoff DB has no `analyst_signals` table). v10.2's headline
   feature has zero decision impact. Re-validate v10.2 claims.
2. **Silent except fixed** in fast_backtest.py — scoring crashes previously kept prior
   position state and were invisible.
3. **Three exit models coexist**: fast_backtest (none), tune.py (time decay + profit-take +
   3×ATR stop), production scoring.py (advisory hints only). Headline Sharpe ≠ traded system.
4. **Metric problem**: legacy "avg of per-stock in-position-week Sharpes" (1.284) vastly
   overstates reality. Actual equal-weight portfolio: **S=0.45, maxDD -31.5%, IS (2021-24)
   Sharpe NEGATIVE (-0.34)**. Legacy and portfolio metrics disagree on which signals matter.

### Experiment results (portfolio S / IS / OOS / maxDD)
| Variant | Port S | IS | OOS | maxDD |
|---|---|---|---|---|
| baseline v10.2 | +0.452 | -0.343 | +1.193 | -31.5% |
| regime hysteresis alone (±3% band, `GUSHEN_REGIME_HYST=1`) | +0.440 | -0.287 | +1.059 | -27.7% |
| **hold-model exits** (`--hold-exit --hold-exit-thresh -2`) | +0.588 | +0.327 | +0.901 | -15.7% |
| **hold-exits + hysteresis** | **+0.648** | **+0.458** | +0.878 | **-15.6%** |
| **prune 5 dead signals** (kdj_golden, ma_golden, macd_golden, vol_anomaly, ma_aligned) | +0.609 | -0.037 | +1.246 | -27.2% |

### Leave-one-out signal attribution (Δ portfolio S when removed)
Critical: kdj_depth (-0.35, maxDD→-62%), fib (-0.24), rsi_depth (-0.16), divergence (-0.16),
chain (-0.14), golden_pit (-0.11). Dead weight: kdj_golden, ma_golden, macd_golden,
vol_anomaly (~0); ma_aligned removal actually HELPS (+0.12).

### Key structural insight
The composite measures entry-attractiveness but Stage 4 reuses it for exits — winners get
ejected on strength. Replacing composite-based exits with a trend-health hold model
(entry/hold separation) turned IS Sharpe positive and halved maxDD. This + hysteresis is
the most robust variant found (positive in both IS and OOS).

### v11 port status (PORTED, opt-in via env flags — default behavior unchanged)
`compute_hold_health()` + hold-exit action logic now live in scoring.py:
- `GUSHEN_HOLD_EXIT=1` — exits from trend-health model (stateless 2-week confirm:
  exit when hh ≤ GUSHEN_HOLD_EXIT_THRESH (default -2) or hh<0 on both current bar and i-5)
- `GUSHEN_REGIME_HYST=1` — regime hysteresis (bull >1.03×MA200, bear <0.97×, else hold state)
- `GUSHEN_FUND_IN_COMPOSITE=1` — include Stage 3.5 fund_bonus in composite (untested: no analyst data here)

bt2.py `--hold-exit --hyst` now exercises the PRODUCTION code path (parity verified exactly:
port S +0.648, IS +0.458, OOS +0.878, maxDD -15.6%, 2564 weeks). With all flags off,
fast_backtest reproduces v10.2 baseline exactly (S=1.284, 584 weeks).

**Ping An note**: under hold-exits 601318 flips S=-1.786 → +0.274. The June-10 "blacklist
Ping An" plan is superseded — its losses were an exit-logic artifact, not a stock problem.

To go live: enable the two env flags in WorkBuddy's environment, then sync handoff → workbuddy.
Re-validate first on a machine with real API keys (re-pin macro: `rm data/macro_snapshot.pkl`,
run `python3 scripts/run_bt_cached.py`). CAUTION: hold-exit holds positions ~4× longer
(2564 vs 584 stock-weeks) — qualitatively different trading behavior; review a few
per-stock trade sequences before trusting it with real money.

## 12. Production Deployment Note

The Gushen skill runs in WorkBuddy (Josh's daily assistant). When Josh says "股神帮我分析 AAPL", the skill:
1. Fetches live OHLCV from data_fetcher.py
2. Fetches fundamentals (TV screener first, akshare fallback)
3. Fetches macro data (FRED + akshare)
4. Runs scoring.py score() → score_bar_v5()
5. Returns BUY/WATCH/HOLD/EXIT with detailed reasoning

Changes to scoring.py directly affect real investment decisions. Test thoroughly before syncing to production.
