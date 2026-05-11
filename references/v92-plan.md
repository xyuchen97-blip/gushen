# Gushen v9.2 — Filtered Optimization Plan

> v9.1 Sharpe=1.38 baseline. Filtered from external AI review. 
> Only items with clear defect → fix mapping or strong signal logic rationale.

---

## 🚨 Phase 0: Bug Fixes (0 Sharpe impact, high safety impact)

### 0.1 — NaN Guards in Macro Scoring
**Problem:** FRED/akshare returns NaN. `float(NaN)` silently skips scoring. Real gap.
**Fix:** `pd.notna()` check before every macro value. Track `macro_coverage` in output.
**Risk:** Zero. No scoring change when data is present.
**Estimate:** 15 min

### 0.2 — Compensate for Missing Macro Data
**Problem:** Macro normalization uses `/ 35.0` regardless of available data. When FRED fails (frequent), macro weight shrinks silently.
**Fix:** Dynamic `macro_max` based on which indicators have data. Zero macro → default 50%.
**Risk:** Low. Full-data case identical to v9.1.
**Estimate:** 10 min

---

## 🔥 Phase 1: Signal Quality (Real Sharpe Impact)

### 1.1 — Yield Curve Inversion Penalty
**Problem:** Yield spread rewards positive values (+4/+2) but ZERO penalty for inversion (10Y-2Y < 0). Historically strongest recession signal — missing entirely.
**Fix:** Add `"spread_inverted": -5` to MACRO_SCORES.
**Impact:** Better drawdown control during 2022-2023 inversion period.
**Risk:** Low. Single branch addition to existing yield spread logic.
**Estimate:** 5 min code + 10 min backtest

### 1.2 — Decouple Volume Double-Count
**Problem:** `vol_anomaly` scores +8 in capital AND +3 in macro (national_team). Capital=25% + Macro=20% → volume gets 45% composite weight. Real inflation.
**Fix:** Create separate `national_team` signal: volume > 2.5× MA20 AND above MA50 AND low ADX.
**Impact:** Cleaner signal separation. A-stock scores drop 1-3pt (bubble removed).
**Risk:** Medium — new signal correlation unknown. Backtest required.
**Estimate:** 15 min code + 10 min backtest

### 1.3 — Fibonacci Normalization into Weight System
**Problem:** Fib bonus (0-5 raw points) added POST weight normalization. Hidden ~5% fib weight.
**Fix:** Add `"fibonacci": 5` to WEIGHTS dict, reduce others proportionally. Normalize: `fib_n = (fib_bonus/5.0) * w["fibonacci"]`.
**Impact:** Zero — weight redistribution is minor (< 3pt composite delta). But architecture becomes consistent.
**Risk:** Zero. Identical results on same data.
**Estimate:** 10 min

---

## 🔧 Phase 2: Architecture Cleanup

### 2.1 — Vectorize Chain Detection in precompute()
**Problem:** Chain detection uses nested for-loops per bar in score_bar(). O(window³).
**Fix:** Pre-compute C2/C3 for all 3 window sizes (3/5/8) in precompute(). score_bar() becomes O(1) lookup.
**Impact:** Zero Sharpe. But ~30% faster backtest + cleaner code.
**Risk:** Low. Results must be bit-for-bit identical.
**Estimate:** 20 min
**Acceptance criteria:** Backtest results identical to v9.1 on same data.

### 2.2 — Adaptive QVIX Thresholds (rolling percentile)
**Problem:** Hardcoded QVIX thresholds from specific historical distribution.
**Fix:** Rolling 252-day percentile: P15/P35/P75 instead of absolute 14.2/16.2/30.9.
**Risk:** MEDIUM. Existing thresholds were grid-searched and work. Rolling percentiles may shift signal frequency. Need backtest validation.
**Impact:** Theoretical improvement for regime shifts. Realistic: 0 to +0.05 Sharpe.
**Estimate:** 15 min code + 15 min backtest
**Decision gate:** Only merge if Sharpe >= 1.30 and signal frequency within 20% of v9.1.

---

## 📋 Phase 3: Process & Code Quality

### 3.1 — Grid Search Cross-Validation
**Problem:** Grid search trains on same data it evaluates. No hold-out.
**Fix:** Time-based train/test split (train=2021-2024, test=2025-2026). Flag if test Sharpe < 0.5× train Sharpe.
**Impact:** Zero Sharpe. Better confidence that params generalize.
**Estimate:** 15 min

### 3.2 — Magic Number Documentation
**Clean:** Add named constants for all magic numbers (BEAR_TREND_DISCOUNT=0.40, WEEKLY_MA20_PENALTY_AHK=0.65, etc). Remove dead code.
**Estimate:** 10 min

---

## ❌ Items NOT Included — With Reasons

| External Suggestion | Why Skipped |
|:---|:---|
| Window-dependent chain bonuses (1.1) | Adds 3× params. Current fixed bonuses already calibrated. Risk of over-tuning. |
| Regime-aware sell penalties (2.2) | Bear exit already stricter (entry 46 vs 45). Heavier sells in bear = over-selling. |
| Parallel watchlist (4.2) | Thread safety issues with akshare RateLimiter. Low payoff. |
| Vectorize divergence (4.3) | Current implementation works. Rewrite risk > benefit. |

---

## Execution Order

```
Phase 0:  0.1 NaN guards (15min)  →  0.2 macro compensation (10min)
Phase 1:  1.2 volume decouple (25min)  →  1.1 yield inversion (15min)  →  1.3 fib normalize (10min)  
Phase 2:  2.1 chain vectorize (20min)  →  2.2 QVIX adaptive (30min, with gate)
Phase 3:  3.1 cross-validation (15min)  →  3.2 docs (10min)
```

**Total: ~150 min + backtest runs**

## Gate Check After Each Phase

```
Overall Sharpe >= 1.30
No market Sharpe drops below 0.40
MaxDD no worse than -16%
Signal frequency within 20% of v9.1
```
