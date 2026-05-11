# Gushen v9.2 — Filtered Optimization Plan (English)

> From external AI review of v9.1 (Sharpe=1.38, 17 stocks, 2021-2026).
> 13 suggestions received → 9 accepted, 4 rejected with rationale.

---

## Accepted (9 tasks, ordered by impact)

### 🚨 Bug Fixes

**0.1 NaN Guards in Macro Scoring**
- Problem: FRED/akshare returns NaN. `float(NaN)` slips through silently. Macro signal is lost.
- Fix: `pd.notna()` check before every macro value read. Track `macro_coverage` field in output.
- Risk: Zero. Identical behavior when data is present.

**0.2 Compensate for Missing Macro Data**
- Problem: Macro normalization uses fixed `/ 35.0` denominator. When FRED fails (frequent), macro weight shrinks and tech/capital weights implicitly inflate.
- Fix: Dynamic `macro_max` based on which indicators are available. Zero data → default 50% neutral, not 0%.
- Risk: Low. Full-data case identical to v9.1.

### 🔥 Signal Quality

**1.1 Yield Curve Inversion Penalty**
- Problem: Yield spread rewards positive values (+4/+2) but zero penalty for inversion (10Y-2Y < 0). Yield curve inversion is historically the strongest recession predictor.
- Fix: Add `"spread_inverted": -5` to MACRO_SCORES.
- Expected: Better drawdown control during 2022-2023 inversion period.
- Risk: Low. Single branch addition to existing logic.

**1.2 Decouple Volume Double-Count**
- Problem: `vol_anomaly` scored +8 in capital AND +3 in macro (as national_team). Capital=25% + macro=20% = volume signal gets 45% composite influence. Real inflation.
- Fix: Create dedicated `national_team` precompute signal: volume > 2.5× MA20 AND above MA50 AND low ADX (range-bound accumulation pattern). Remove vol_anomaly from macro scoring.
- Expected: Cleaner signal separation. A-stock scores drop 1-3pt (bubble removed).
- Risk: Medium — new signal correlation must be backtest-validated.

**1.3 Fibonacci Normalization into Weight System**
- Problem: Fibonacci bonus (0-5 raw pts) added AFTER weight normalization, bypassing the {T/C/F/M} system. Hidden ~5% fib weight.
- Fix: Add `"fibonacci": 5` to WEIGHTS, reduce others proportionally. Normalize: `fib_n = (bonus / 5.0) × weight`.
- Impact: Architecture becomes consistent. Composite delta < 3pts.

### 🔧 Architecture

**2.1 Vectorize Chain Detection in precompute()**
- Problem: Chain resonance detection uses nested for-loops per bar in `score_bar()`. O(window³).
- Fix: Pre-compute C2/C3 patterns for all 3 window sizes (3/5/8) during `precompute()`. `score_bar()` becomes O(1) lookup.
- Expected: ~30% faster backtest. Zero Sharpe impact.
- Gate: Results must be bit-for-bit identical to v9.1 on same data.

**2.2 Adaptive QVIX Thresholds**
- Problem: QVIX thresholds are hardcoded absolute values (14.2 / 16.2 / 30.9) from a specific historical distribution.
- Fix: Rolling 252-day percentile → P15/P35/P75 instead of absolute thresholds. Fallback to fixed values when < 60 days of data.
- Risk: Medium — current thresholds were grid-searched and work. May shift signal frequency.
- Gate: Merge only if Sharpe >= 1.30 AND signal frequency within 20% of v9.1.

### 📋 Process

**3.1 Grid Search Cross-Validation**
- Problem: Grid search trains and evaluates on same data. No hold-out.
- Fix: Time-based split (train=2021-2024, test=2025-2026). Flag if test Sharpe < 0.5× train Sharpe.
- Impact: Better parameter confidence.

**3.2 Code Cleanup**
- Name all magic numbers. Remove dead code. Validate `_grid_params` on load.

---

## Rejected (4 items, with rationale)

**❌ Window-Dependent Chain Bonuses** (external 1.1)
- Suggestion: scale C2/C3 bonuses based on adaptive window size (3=lower, 8=higher).
- Rejection: Adds 6 new parameters. Current fixed 15/22 already calibrated on 25-combo grid search. Risk of over-tuning without guaranteed benefit.

**❌ Regime-Aware Sell Penalties** (external 2.2)
- Suggestion: heavier sell penalties in bear markets (-13 vs -8).
- Rejection: Bear regime already has stricter entry threshold (46 vs 45). Additional sell penalty = over-selling during bears. The system already protects against bear markets through entry discipline, not exit aggression.

**❌ Parallel Watchlist Scanning** (external 4.2)
- Suggestion: ThreadPoolExecutor for concurrent stock scoring.
- Rejection: akshare RateLimiter is not thread-safe. Debugging race conditions costs more than 10 seconds saved. Premature optimization.

**❌ Vectorize Bullish Divergence** (external 4.3)
- Suggestion: replace for-loop with `rolling().min()` operations.
- Rejection: Current implementation is correct and functional. Vectorized rewrite saves ~5% precompute time but risks introducing boundary condition errors. Cost/benefit unfavorable.

---

## Execution Order

```
Phase 0: 0.1 NaN guards → 0.2 macro compensation
Phase 1: 1.2 volume decouple → 1.1 yield inversion → 1.3 fib normalize
Phase 2: 2.1 chain vectorize → 2.2 QVIX adaptive (with merge gate)
Phase 3: 3.1 cross-validation → 3.2 code cleanup
```

**Estimated total: ~150 minutes + backtest runs**

## Gate Check After Each Phase

```
Overall Sharpe >= 1.30
No market Sharpe drops below 0.40
MaxDD no worse than -16%
Signal frequency within 20% of v9.1 baseline
```

## Expected Summary

| Phase | Tasks | Sharpe Delta | Risk |
|:------|:------|:---:|:---:|
| 0. Bugs | 2 | 0 (safety) | None |
| 1. Signals | 3 | +0.05 to +0.15 | Low-Med |
| 2. Architecture | 2 | 0 to +0.05 | Low-Med |
| 3. Process | 2 | 0 (confidence) | None |

**Target: v9.2 Sharpe 1.40-1.50, with improved regime robustness and drawdown control.**
