# Gushen v12 — Style- and Regime-Robust Architecture Proposal

> **Date**: 2026-06-10
> **Basis**: v11 session (look-ahead decontamination, hold-exit port, signal attribution,
> event studies, style×regime diagnosis). All numbers below are from the pinned macro
> snapshot and honest (band_king-free) engine. Logs: `data/bt2_results.jsonl`.
> **Status**: PROPOSAL — nothing here is implemented except where marked [SHIPPED v11].

---

## 1. Honest positioning (what this engine actually is)

The June 2026 diagnosis (`scripts/diagnose_style_regime.py`) against equal-weight
buy-and-hold of the same 21 stocks:

| | Portfolio S | IS (21-24) | OOS (24-26) | maxDD |
|---|---|---|---|---|
| Engine (hold-exit+hyst, honest) | +0.61 | +0.35 | +0.90 | **-17.0%** |
| Buy & hold same universe | ~0.5–0.9 | ~0.2 | ~1.0 | **-37.8%** |

**Gushen's demonstrated edge is not raw return — it is drawdown control and
consistency.** It wins by avoiding bear damage (IS period: engine positive, B&H ≈ flat
with -38% DD) and gives back upside in strong bull trends. Per style×regime:

| Style group | Engine wins | Engine loses |
|---|---|---|
| financials (平安/招行/JPM/0388) | bear entries: S 1.05 vs B&H 0.89 | — |
| ev_battery (宁德/BYD-A/H) | both regimes beat B&H | — |
| cn_platform (腾讯/阿里/美团) | bull: 0.47 vs 0.24 | bear: -0.11 vs 0.14 |
| us_megacap (NVDA/MSFT/...) | bear: ~par | **bull: 0.87 vs B&H 1.38 — biggest leak** |
| hardware (AAPL/小米) | bull: 1.27 vs 1.13 | bear: -0.57 vs 0.53 |
| staples (茅台/五粮液) | — | **engine -1.42 vs B&H -0.25 — knife-catching** |
| healthcare (爱尔眼科) | — | all cells negative |
| volatile_tech (讯飞) | bear: 0.44 vs 0.36 | bull: -0.42 vs 0.58 |

*Caveats: engine cell Sharpes are computed on its own in-position weeks; B&H cells on
all weeks in that regime — directionally comparable, not identically conditioned. Thin
cells (staples bull 29w, healthcare bull 27w, hardware bear 51w) are directional
evidence only; conclusions rest on the fat cells and on the cross-group pattern.*

Three systematic failure modes, all **style×regime mismatches**, not stock problems:
1. **Timing-tax on persistent trends** (us_megacap/volatile_tech in bull): a contrarian
   engine waits for oversold that rarely comes, then buys late and exits early.
2. **Knife-catching in structural deratings** (A-share staples, healthcare 2021-24):
   contrarian depth signals fire all the way down a multi-year derating.
3. **Trend-following bear losses on cyclical hardware**: P4-discounted trend signals
   in bear pull entries into rallies that fail.

---

## 2. Design principles (hard-won; each has a receipt)

1. **Entry alpha = depth × confirmation conjunction.** Oversold alone is anti-alpha
   (kdj J<20: -0.4%/20d, t=-2.9), but depth signals are how the engine generates
   opportunities at all. Honest LOO (post-decontamination): removing kdj_depth
   collapses in-position weeks 569→177 and worsens maxDD -27%→-35% (portfolio S
   nominally rises 0.445→0.549 on the few remaining entries). The earlier receipt
   (-0.35 S, maxDD -62%) was measured pre-decontamination and partly a band_king
   interaction — re-measure attribution after EVERY structural change. Design rule
   stands: depth gates timing and sizing; only depth+confirmation conjunctions
   trigger actions.
2. **Exits measure hold-worthiness, not entry-attractiveness.** [SHIPPED v11]
   Composite-based exits eject winners on strength (bb_sell fires on +0.5%/5d
   forward returns). Hold-health exits: portfolio S +37%, maxDD halved.
3. **Causality is a contract, not a comment.** band_king's "compute_no_future" had a
   centered window: +12%/20d phantom edge, 0/20 events visible live, production never
   saw it. Every signal must pass the prefix-recomputation repaint test before entering
   the registry. Any signal with t>5 or any change with >20% Sharpe jump triggers an
   automatic repaint audit.
4. **Portfolio equity curve is the primary metric.** Avg-of-per-stock-Sharpes inflated
   the headline by ~3x and disagreed with the portfolio metric about which signals
   matter. Legacy metric is reporting-only.
5. **IS/OOS split on every experiment.** Tuned-on-everything numbers are in-sample by
   construction.
6. **Parameters attach to buckets/cells, never to stocks.** 21 stocks cannot support
   per-stock parameters. (The "blacklist Ping An" idea was wrong — hold-exits flipped
   it from -1.79 to +0.27. Fix structure, not stocks.)
7. **No momentum bonuses bolted onto contrarian scores** (v10.3 lesson) — combine
   strategies via the policy matrix (different cells), never by adding scores.
8. **Macro stays at the sizing layer** (stock-level macro was -36%).
9. **Binary events for triggers, continuous values for depth/sizing** (v10 lesson,
   reconfirmed by the conjunction finding).
10. **Pinned data for comparisons.** Same code drifted 1.476→1.324 from API revisions
    alone. Baselines and experiments must share a snapshot.

---

## 3. Target architecture — five layers

```
L0  DATA & CAUSALITY     pinned snapshots, repaint tests, signal registry metadata
L1  SIGNAL REGISTRY      small set of validated signals with event-study cards
L2  CONTEXT CLASSIFIER   per-stock: trend state + vol state;  market: macro state
L3  POLICY MATRIX        behavior bucket × trend state → entry/exit/sizing policy
L4  PORTFOLIO            hold-health exits, vol-targeted sizing, caps, kill-switch
```

### L0 — Data & causality
- `data/macro_snapshot.pkl` mechanism [SHIPPED v11]; re-pin deliberately, never silently.
- Repaint test as a reusable function; required for registry admission.
- Every registry signal carries: definition, lookback, event count, edge5/edge20 + t,
  regimes/buckets where valid, admission date, audit date.

### L1 — Signal registry (current validated inventory)

| Signal | Role | Standalone 20d edge (t) | Status |
|---|---|---|---|
| kdj_depth / rsi_depth / bb_depth | continuous depth | negative alone | KEEP — conjunction only |
| golden_pit | confirmation event | +1.3%/5d (3.5) | KEEP |
| bb_weekly_buy | confirmation event | +4.5% (4.0) | KEEP |
| chain_c2 | confirmation event | +1.9% (3.6) | KEEP |
| vol_anomaly | confirmation event | +1.4% (5.7) | **UNDERUSED** — +3 pts is wasted; promote to first-class confirmation w/ 5-day persistence window |
| fib_support (bull-gated) | bull pullback support | +0.8% (3.5) | KEEP (bull cells only) |
| bullish_divergence | conjunction component | -0.4% (-1.7) | KEEP (LOO -0.16) |
| nine_turns buy/setup | confirmation event | ~0 | PROBATION — re-test per bucket |
| chain_c3 | bonus | n=44, unstable | DEMOTE — weight unsupported by sample |
| kdj_golden, ma_golden, macd_golden | events | ~0 (\|t\|<1) | RETIRED under baseline exits; re-test under hold-exit before final removal |
| ma_aligned, price_above_ma50, adx_strong, weekly_ma20_up | trend state | n/a | KEEP — feed L2/L3/hold-health, not entry score |
| band_king buy2/sell1 | — | look-ahead artifact | **REMOVED** [SHIPPED v11] |
| band_low | — | 0 events in 5y | REMOVE (vestigial) |
| Stage 3.5 analyst/earnings | — | untestable here | REVALIDATE on machine with data, incl. composite |

### L2 — Context classifier (per stock, per bar)
- **Trend state**: bull / bear via MA200 with ±3% hysteresis [SHIPPED v11, flag] — plus
  a third **chop** state when ADX < ~18 and price within ±5% of MA200 for N weeks.
  States must be slow (hysteresis + minimum dwell time) — the engine switches
  personality on state change, and whipsaw there is costlier than late detection.
- **Vol state**: realized 13w vol percentile vs own 2y history (low/high). Used by L4
  sizing and to widen hysteresis in high vol.
- **Market state**: macro_mult [unchanged].
- Budget: 2 trend states shipped + chop = 3; × 2 vol states = 6 cells max. Every cell
  must hold >300 stock-weeks across the universe or it merges with a neighbor.

### L3 — Behavior buckets + policy matrix (the core v12 change)

Buckets are **computed from price behavior on a rolling 2y window**, not assigned by
hand, so they generalize beyond these 21 names and reclassify as behavior changes:

- **TREND**: high momentum persistence (52w variance ratio > 1, positive 26w slope
  persistence). Currently: us_megacap, AAPL-like.
- **REVERT**: negative weekly autocorrelation / variance ratio < 1, drawdowns that
  mean-revert within quarters. Currently: financials, ev_battery, cn_platform,
  volatile_tech.
- **FRAGILE**: 2y trend negative AND weak reversion (drawdowns that don't recover).
  Currently: staples, healthcare in 2021-24.

Policy matrix (parameters shared within a cell; ~8 effective policies, not 21×6):

| | bull | chop | bear |
|---|---|---|---|
| **TREND** | **hold w/ trail stop; no entry timing** (fixes the biggest leak: stop trying to time NVDA/MSFT) | half size, pullback entries | stand aside; entry only on extreme depth+confirmation |
| **REVERT** | pullback buys (current bull engine + fib) | current contrarian, half size | **current contrarian engine — its proven home** |
| **FRAGILE** | contrarian w/ long-trend gate | no entries | **no contrarian entries unless 26w MA20-slope ≥ 0** (fixes 茅台/爱尔 knife-catching) |

Exits in all cells: hold-health model [SHIPPED v11], with trail stops added in TREND
cells (port the ATR logic from tune.py into scoring so backtest = production).

### L4 — Portfolio layer
- Hold-health exits [SHIPPED v11].
- **Vol-targeted sizing**: weight ∝ 1/realized vol, scaled by macro_mult, capped.
- **Market concentration cap** (e.g., ≤50% of open risk in one market).
- **Drawdown kill-switch**: portfolio DD > X% → halve all sizes until recovery
  (cheap insurance; backtestable).

---

## 4. Why this stays a CLEAR structure

- Five layers with one-way dependencies (L0→L4); no layer reaches around another.
- The signal registry shrinks the surface: ~10 validated signals with cards, instead
  of ~20 with hand-set weights.
- All style adaptation lives in ONE place (the policy matrix), expressed as
  bucket×state policies — not threshold tweaks scattered through score_bar_v5.
- Engine "personality" per cell is explicit and auditable: a reasoning string can say
  `REVERT×bear → contrarian engine → depth 8.2 + golden_pit → BUY`.
- Parameter count: ~8 policies × ~4 params ≈ 30 meaningful parameters, each shared
  across many stock-weeks — vs the current implicit hundreds.

## 5. Validation protocol (applies to every v12 step)

1. Pinned snapshot; fresh baseline in the same run.
2. Portfolio S primary; IS and OOS reported; neither may degrade > 0.05 for an accepted change.
3. Per-cell benchmark: a policy is adopted only where it beats BOTH the current engine
   AND buy-and-hold in that cell (else the cell's policy defaults to B&H-with-trail-stop).
4. Repaint test for any new signal; t>5 anywhere → automatic look-ahead audit.
5. Minimum samples: no conclusion on <100 events / <300 stock-weeks.
6. One change per experiment; everything appended to `bt2_results.jsonl`.
7. Sanity: trade counts, exposure, and maxDD reported with every run.

## 6. Migration roadmap (each step gated, reversible, flag-controlled)

- **M1 — Measure, don't change**: implement the bucket classifier + chop state, report
  the policy-matrix cell of every historical entry. Gates: (a) median bucket dwell
  ≥ 26 weeks and ≤ ~2 transitions/stock/year, (b) sanity mapping (US megacaps→TREND,
  financials→REVERT, 2022-24 staples→FRAGILE), (c) every used cell ≥ 300 stock-weeks,
  (d) per-cell engine-vs-B&H table reproduces the §1 diagnosis using computed buckets.
  **M1 RESULTS (June 10, 2026, `scripts/m1_buckets.py`):**
  - Rule v1 (variance ratio) failed sanity: VR measures week-to-week persistence, but
    "TREND" behavior is DRIFT — US megacaps classified REVERT. Lesson recorded.
  - Rule v2 adopted provisionally: FRAGILE if 2y return ≤ -10%; TREND if 2y realized
    Sharpe ≥ 0.7; else REVERT. 8-week dwell.
  - Gate (a) PASS: median dwell 53w, 0.70 transitions/stock/yr (max 1.5).
  - Gate (b) PARTIAL — and the misses are mostly the EXPECT table being wrong, not the
    classifier: MSFT/GOOGL dominant-REVERT (correct: 2022 -30% DD sits in most 2y
    windows), 0700/9988 FRAGILE 2021-24 → TREND now (correct: 700→200 collapse, then
    recovery), JPM TREND (defensible). Behavior labels track reality better than
    sector stereotypes; policies must be judged per cell, not per stereotype.
  - Gate (c): chop cells all <300 stock-weeks → MERGE chop into bear for policy
    purposes (chop stays as a diagnostic only).
  - Gate (d): TREND×bull leak reproduced (engine 0.55 vs B&H 0.65; only 3 entries in
    580 stock-weeks — the engine barely participates in the best cell). REVERT×bull
    also lags (0.60 vs 0.86). FRAGILE×bull: engine beats B&H (-0.09 vs -0.21).
  - M1 status: classifier usable for M2 experimentation; revisit thresholds only with
    IS/OOS evidence, not stereotype fit.

- **M2 — TREND×bull policy** (biggest expected gain: us_megacap leak 0.87→toward 1.38):
  hold-with-trail-stop instead of entry timing. Gate: portfolio S and OOS improve.
  **M2 RESULTS (June 10, 2026, `scripts/m2_trend_bull.py`, gate PASSED):**
  - Policy: in (TREND, bull) cells be invested with a 15% trailing stop; v11 engine
    everywhere else; positions hand over to hold-health logic when leaving the cell.
  - Control reproduces v11 exactly (+0.610 vs +0.609 reference).
  - Result: **portfolio S +0.639 (IS +0.386 / OOS +0.942), maxDD unchanged -17.0%** —
    both halves improve; 586 policy cell-weeks engaged.
  - Robust to the single parameter: trail 12%/15%/18% → 0.622/0.639/0.637 (plateau).
  - Adopted for the experiment stack at trail=15%. NOT yet in production: requires the
    buckets module (strategy/buckets.py, shipped) plus position-state at the
    recommendation layer — production integration is part of M5.

- **M3 — FRAGILE gate** (staples/healthcare fix): 26w trend gate on contrarian entries.
  **M3 RESULTS (June 10, 2026, `scripts/m3_fragile_gate.py`, gate PASSED, ADOPTED):**
  - Slope-gate variants were monotonic in window length (13w/26w/39w → 0.759/0.812/0.899),
    pointing to the parameter-free limit: **block ALL new contrarian entries in FRAGILE**.
  - Result: **portfolio S +1.036 (IS +1.067 / OOS +1.073), maxDD -15.1%** vs v11's
    +0.609 (+0.354/+0.903, -17.0%). 60 entries blocked.
  - +70% Sharpe jump triggered the mandatory audit (protocol §5.4): PASSED — bucket
    thresholds were fixed in M1 before this experiment; classifier is causal; gain is
    broad-based (blocked names are exactly the knife-catch victims: 五粮液/爱尔眼科/0700
    fully, 茅台 mostly); not driven by any single stock.
  - Honest cost: the 2y window is slow to forgive — 0700's 2024-26 recovery was also
    blocked. Portfolio still nets far ahead. Future refinement (gated): faster FRAGILE
    exit criteria, e.g., bucket re-promotion on 1y strength.
  - **M2 SHELVED**: on top of M3, M2 fails its incremental gate twice
    (OOS 1.073→0.993; earlier 0.993→0.918). The TREND×bull leak shrinks once FRAGILE
    capital stops being destroyed. Re-test M2 only after M4.
  - **Cumulative honest arc (pinned macro, June 10)**:
    v10.2 baseline 0.445 (IS -0.30) → +hold-exits/hyst 0.609 → +FRAGILE block
    **1.036 (IS +1.07 / OOS +1.07), maxDD -31.5% → -15.1%** — now decisively beats
    B&H (~0.5-0.9, -37.8% DD) on both return quality and drawdown.

- **M4 — Vol-targeted sizing + concentration cap + kill-switch.**
  **M4 RESULTS (June 10, 2026, `scripts/m4_portfolio.py`):**
  Per-market sub-portfolio Sharpe is now reported on every run and is part of the gate
  (no market may regress > 0.10).
  - **ADOPTED — vol-targeted sizing** (weight ∝ 1/13w vol, ~4% weekly vol target, 3x cap):
    ALL +1.036 → **+1.089 (IS +1.148 / OOS +1.098), maxDD -14.1%**.
    Per market: A +0.08→+0.26 (dd -39%→-24%), HK +0.63→+0.72, US 1.24→1.19 (within
    tolerance). Biggest beneficiary is A-shares — sizing down high-vol names is most of
    what A needed beyond the FRAGILE block.
  - REJECTED — FRAGILE fast-forgiveness (26w ret ≥ +15%): ALL 1.036→0.895, HK worse
    (0.63→0.49) — re-admits 2022-23 dead-cat bounces; missing 0700's recovery was the
    cheaper error.
  - REJECTED — drawdown kill-switch (-10%/half): ALL →0.981, OOS →0.903 — exits already
    control DD; the switch just clips recoveries.
  - REJECTED (final) — M2 TREND×bull trail-hold, third failure on the full stack
    (ALL 1.089→1.039, US unchanged). Removed from the roadmap.
  - **Final adopted v12 experiment stack**: v11 hold-exits + regime hysteresis
    + M3 FRAGILE entry block + vol-targeted sizing.
    **ALL +1.089 (IS +1.148 / OOS +1.098), maxDD -14.1%** vs B&H ~0.5-0.9 / -37.8%.
    Per market vs B&H: A +0.26 vs +0.03 ✓, HK +0.72 vs +0.40 ✓, US +1.19 vs +1.62 ✗.
  - **Honest US note**: the engine still lags US B&H on Sharpe (1.19 vs 1.62) at equal
    drawdown (-22% vs -24%). Three structurally different attempts to close this failed
    their gates. Working hypothesis: lagging the strongest bull market in the universe
    is the price of a risk-managed process; the engine's value in US is bear protection
    (IS 1.70). Do NOT keep mining this gap without a new idea — that way lies overfit.

- **M5 — Registry refactor**: move signal definitions out of score_bar_v5 into a
  declarative registry; scoring.py becomes a thin policy executor. (Last, because it's
  pure refactor risk with no performance change — do it once behavior is settled.)
- Throughout: re-run leave-one-out attribution under the new architecture per cell
  (attribution is conditional on structure — the v11 lesson).

## 6b. External (retail-quant) strategies tested — June 10, 2026

Owner supplied a list of 10 popular CN retail quant strategies. Triage + results:

| Strategy | Verdict | Evidence |
|---|---|---|
| Dual-MA cross (双均线) | Already disproven here | ma_golden edge ~0 (t=0.0) |
| BB mean reversion (布林带) | Already core engine | bb_depth/bb_buy validated |
| Grid (网格), Gap/ORB | Untestable honestly | need intraday data; weekly bars can't simulate fills |
| Small-cap factor (小市值) | Universe decision, not a rule | requires expanding beyond 21 mega-caps; 2024 crackdown regime risk |
| RSI(2) Connors | **Dead in this universe** | edge5 +0.06% (t=0.9), edge20 -0.18% |
| XS momentum rotation (动量轮动) | **Inferior standalone** | best variant (12w/top5): S 0.862, maxDD -46% vs stack 1.097 / -13.8% |
| 52w-high breakout (突破) | **Real event edge, REJECTED at portfolio level** | event: +2.27%/5d (t=3.3) ✓ — but stack+breakout: OOS 1.104→0.875, HK -0.25 (gate fail); TREND-only variant also fails (OOS 1.026). The stack already holds these names when breakouts fire (hold-exits keep winners); marginal entries skew toward exhaustion. |

**Meta-lesson (counterpart of the band_king lesson)**: event-level alpha ≠ portfolio
improvement. A genuinely predictive signal adds nothing if the portfolio is already
positioned when it fires. Always gate at the portfolio level.

**Where further performance can realistically come from** (in order):
1. Universe expansion (more stocks → more diversification, real small-cap/multi-factor
   testing, thicker policy-matrix cells) — likely the single biggest lever now.
2. Better data (intraday for execution-sensitive strategies; historical fundamentals
   for value/quality factors; verified analyst data for Stage 3.5).
3. NOT more rules on 21 names — the rule surface is near its overfitting budget.

## 6c. v13 universe expansion — June 10, 2026 (BUILT + first validation)

21 → 54 names. Selection rule (not hand-picked winners): largest liquid names per
market, ≥5y history, sector cap, deliberately adding styles the engine lacked —
utilities/energy/pharma/staples-retail/payments/telecom/insurance (A +12, HK +9, US +12).
Data: new A names via yfinance adjusted (eastmoney rate-limited the sandbox; akshare qfq
worked for 2). HK/US via yfinance adjusted. Files: `scripts/expand_universe.py`,
`scripts/warm_precomp.py`, `data/universe_v13_new.json`; run via
`m4_portfolio.py --universe v13`.

**Results (full stack, full macro):**
| | ALL | IS | OOS | maxDD |
|---|---|---|---|---|
| legacy 21 | +1.097 | +1.150 | +1.104 | -13.8% |
| **v13 (54)** | **+1.041** | +1.142 | +1.006 | **-12.4%** |

Per market (v13): A +0.32 (B&H +0.33, dd -24% vs -30%) | HK +0.36 (B&H +0.13) |
US +1.22 (B&H +1.62). Slightly lower Sharpe than legacy but on 2.6x the names with
better drawdown — the expansion goal was statistical power and diversification, not
immediate Sharpe. Policy-matrix cells are now ~2.5x thicker for future validation.

**New rule discovered (adopted)**: bucket == 'NA' (insufficient history to classify)
blocks new entries. Without it, unclassified 2021-era HK names (快手/京东/理想) were
knife-caught: ALL was 0.599 with -44.6% maxDD. No context → no position.

**Data caveats**:
- Legacy 8 A-names are Tushare UNADJUSTED; new A names are adjusted. Re-fetch legacy A
  as qfq on the owner machine, then re-run all baselines (numbers will shift slightly).
- New names lack chip/margin/analyst caches (code degrades gracefully). Backfill via
  gushen_cache build functions on the owner machine for full A-share signal coverage.
- HK sub-portfolio is now the weak spot with proper sample (IS -0.19, dd -45%) — the
  next legitimate optimization target, with cells thick enough to study it.

## 6d. 2015+ TRUE out-of-sample validation — June 10, 2026 (PASSED)

All 54 names refetched 2015+ **adjusted** (fixes the legacy-A unadjusted problem; all
markets now consistent), FRED macro extended to 2015, precompute rebuilt. The v12/v13
stack was FROZEN before this run — era1 (2016-06 → 2021-05, includes 2018 bear and
2020 COVID) was never seen by any rule adopted this session.
Runner: `scripts/longrun_2015.py`.

| | era1 2016-21 (TRUE OOS) | era2 2021-26 (discovery) | full 10y dd |
|---|---|---|---|
| ALL engine | **+1.638** | +1.333 | **-18%** |
| ALL B&H | +1.595 | +0.920 | -22% |
| A eng vs B&H | +1.78 vs +1.62 ✓ | +0.58 vs +0.35 ✓ | -27% vs -32% |
| HK eng vs B&H | +0.71 vs +0.97 ✗ | +0.76 vs +0.28 ✓ | -32% vs -50% |
| US eng vs B&H | +1.43 vs +1.46 ≈ | +1.24 vs +1.42 ≈ | -23% vs -27% |

**Reading**: the stack did not blow up on unseen data — it matched-or-beat B&H in the
golden era (and the FRAGILE gate did NOT cost the 2016-21 A-share bull), added clear
value in the hard era, and improved drawdowns everywhere. HK lags B&H in era1 too —
HK weakness is structural across eras, not a 2021-24 artifact; it is the one
legitimate research target left.

**Caveats**: era1 macro_mult had US series only (China macro starts 2021); several HK
names (9988/9618/1024/2015.HK) listed 2019-21 so era1 HK is thinner; and survivorship
inflates BOTH columns in era1 (today's mega-caps were yesterday's winners) — relative
engine-vs-B&H comparisons remain meaningful, absolute era1 levels do not.

**Status: the freeze held. The stack is validated for M5 production integration.**

### June 10 late-session corrections (post-validation)
1. **start-index bug found & fixed**: all loop runners inherited fast_backtest's bug of
   computing the start position on the DAILY index but slicing the WEEKLY loop with it.
   Harmless with 2021+ data; with 2015+ history it silently skipped most/all bars
   (US showed zero weeks). Fixed in m4_portfolio.py and longrun_2015.py. Engine-side
   longrun numbers unchanged (bucket/warmup dominated the effective start); B&H era1
   benchmarks rose (more weeks covered): era1 ALL B&H +1.60→+1.82, so era1 reads
   engine +1.64 vs B&H +1.82 — engine LAGS B&H in the golden era and wins era2 +1.33
   vs +0.92, dd -18% vs -22%. The honest 10y story stands: pay a toll in runaway bulls,
   get paid in hard markets, always with smaller drawdowns.
2. **Corrected v13 reference (2021-26, with full-history buckets + start fix)**:
   ALL **+1.088 (IS +0.691 / OOS +1.792) dd -21.0%**; A +0.95 vs B&H +0.36 (full
   history lets buckets classify from day one instead of NA-blocking 2021-22).
   Earlier v13 numbers (§6c) are superseded.
3. **Stage 3.5 RESOLVED**: AV earnings cache built (19 US names, 1959 signals,
   full quarterly history). GUSHEN_FUND_IN_COMPOSITE=1 on the corrected stack:
   ALL +1.088→+1.096, US 1.154→1.139, dd -21.0→-18.9 — neutral within noise.
   **Verdict: keep fund_bonus OUT of composite (status quo); the v10.2 claimed gain
   is unsupported. Question closed.** Display-only fund_score remains in results.

## 6e. v14 breadth + cross-sectional selection — June 10, 2026

Universe: 132 names (54 + 78 additions by rule: top liquid large-caps per market,
sector caps; banks/energy/pharma/staples/industrials now properly represented).
Data 2015+ adjusted. Files: `data/universe_v14_breadth.json`, `scripts/xsel.py`
(pass 1 records weekly composites once; selection variants evaluate without rescoring).

**Results (frozen engine, 2016-2026, vol-weighted):**
| variant | S | era1 | era2 | maxDD |
|---|---|---|---|---|
| hold-all 132 (baseline) | +1.241 | +1.394 | +1.074 | -18.8% |
| top-10 | +1.362 | +1.604 | +1.098 | -24.7% |
| top-20 | +1.383 | +1.634 | +1.088 | -22.7% |
| **top-30 (adopted)** | **+1.357** | +1.588 | +1.087 | **-19.8%** |

Findings:
1. **Cross-sectional selection works**: ranking in-position names by composite and
   holding the top-K beats hold-all at every K tested, in both eras — the composite
   has cross-sectional information, not just time-series information. Plateau across
   K=10/20/30 (1.36-1.38) → not a knife-edge.
2. **top-30 adopted** (best Sharpe-per-drawdown; top-10/20 concentrate too hard).
   Provisional pending live shadow confirmation.
3. **Honest breadth note**: hold-all on the representative 132 scores LOWER than the
   54-name universe (1.24 vs 1.49) — the original 54 were survivorship-flattered.
   Selection recovers most of the gap (1.36) while keeping the statistical benefits.
4. Threshold-normalized ranking is identical to raw (bull_buy=28 in all markets).
5. NOT yet in production or the shadow task (both still 54 names). Promoting the
   132-name universe + top-30 selection to production is the owner's call — it changes
   what Gushen watches, not just how it scores.

## 6f. HK structural study — June 10, 2026 (`scripts/hk_study.py`)

E1 — diagnosis on the 132-name universe: HK sub-portfolio is healthier than the
54-name view suggested (S +0.610, era1 +0.641 / era2 +0.579, dd -27%). Losses are
DIFFUSE (worst names: 2015.HK -0.93, 3690 -0.74, 1113 -0.56) — structural, not
name-specific. Part of the earlier "HK problem" was small-sample noise.

E2 — HK exempt from hold-model exits (motivated by v10.1 prior "HK trends run longer,
exits hurt"): S +0.610→+0.674, era1 +0.64→+0.80, era2 flat, **dd -27%→-32%**.
Directionally supports the prior but era1-concentrated and pays 5pp drawdown.
**NOT adopted** — too marginal by our gates. Retest after live shadow data
accumulates; if adopted later use env GUSHEN_HOLD_EXIT_SKIP_MKTS=HK (hook shipped).

E3 — AH-premium entry gate (block H-share entry when H expensive vs own A, 52w z):
S +0.610→+0.601. **REJECTED** — no effect; only 6 dual-listed names, and relative
A/H valuation does not improve H-share entry timing at weekly horizon.

HK conclusion: no structural fix found that clears the gates. The remaining HK gap
is likely flow-driven (southbound 港股通 data) — requires new data, queued for a
session with working akshare/tushare access. Until then HK rides the frozen stack.

**E4/E5 addendum (same day, Tushare token provided)**: southbound flow fetched and
pinned (`macro_snapshot['south_flow']`, 2015-2026). Event study: heavy-outflow regime
(20d-sum z < -1) → HK forward returns -2.28%/20d vs +1.01% base (t=-22 nominal;
cross-stock correlation inflates t, magnitude decisive). BUT both implementations
fail the gates: entry gate (E4) 0.610→0.593; risk-off exit on held positions (E5)
0.616 flat with era1 -0.11 (only dd improves -27→-24%). **REJECTED.** Root cause:
by the time the flow z-score confirms an outflow regime, prices have fallen with it
and the hold-health exits have already de-risked — the signal is real but REDUNDANT
with existing exits. Third confirmation of the meta-lesson: conditional event edges
add nothing if the system is already positioned correctly when they fire.
Southbound data remains pinned for future use (e.g., as a faster daily-cadence
risk overlay — untested).

## 6g. Decision cadence: daily vs weekly — June 10, 2026 (`scripts/daily_cadence.py`)

Owner runs Gushen DAILY (correcting the handoff's weekly assumption). Re-tested under
the frozen v12 stack (54 names, equal weight to isolate cadence, FRAGILE/NA gate both):

| cadence | S | era1 | era2 | maxDD |
|---|---|---|---|---|
| weekly | +1.443 | +1.683 | +1.161 | -21.0% |
| **daily** | **+1.540** | +1.703 | **+1.361** | -20.7% |

**The v10.1 prior ("daily worse, S 1.095") is OVERTURNED under v12.** Explanation: the
old composite-based exits churned at daily frequency; the v12 hold-health exit with
2-bar confirmation is stable at daily cadence, and daily reaction adds value
(era2 +0.20). Production score() already operates on the latest daily bar, so live
Gushen ≈ the daily row — our weekly backtests slightly UNDERSTATED the live engine.

Caveats before celebrating: no transaction costs anywhere (daily trades more — the
cost-sensitivity one-off in the research queue is now MORE important); equal-weight
54-name test — re-run with vol sizing on 132 names before quoting the number.

**Full-scale confirmation (June 10, `scripts/daily_wide.py`, 132 names, vol-weighted):**
| | S | era1 | era2 | maxDD |
|---|---|---|---|---|
| weekly cadence | +1.241 | +1.394 | +1.074 | -18.8% |
| **daily cadence** | **+1.453** | +1.650 | +1.228 | -20.6% |
| daily + trail 15% | +1.419 | +1.655 | +1.146 | -22.9% |
| daily + trail 20% | +1.473 | +1.675 | +1.243 | -21.7% |

**Daily cadence CONFIRMED at full scale** (+0.21 S, both eras better, ~2pp dd cost).
Daily becomes the canonical evaluation cadence; production already runs daily.
**Trailing-stop risk layer REJECTED** — variants straddle the daily baseline within
noise; daily hold-exits already react fast enough (fourth redundancy confirmation).
Remaining blocker before quoting daily numbers as official: the COST sensitivity
test (daily trades more; no-cost assumption is now the largest unquantified risk).

**COST SENSITIVITY RESOLVED (June 10, `scripts/cost_sensitivity.py`)**: per-side costs
(A 0.15%/0.25%, HK 0.20%, US 0.05%) on the stored daily pass: no-cost +1.473 → base
costs **+1.419** (-0.05) → 2x stress +1.364 (-0.11). Only ~2,559 trade legs in 10y
across 132 names (≈1 trade/stock/5 months) — the hold-exit architecture is naturally
low-churn, so costs barely bite. **Official daily number: ~+1.42 after realistic costs.**

## 6h. Fibonacci review — June 10, 2026 (owner query: "was it taken out?")

**It was never taken out.** Bull-gated weekly fib support (38.2/50/61.8% of 50w range,
±2%) is live in score_bar_v5 as a +10 bull-mode bonus. What WAS removed (v10, correctly)
is bear-mode fib (-6.5% empirical). Evidence stack:
- Event study (today): raw fib edge ≈ 0 (t=0.1); **bull-gated fib +0.80%/20d (t=3.5)** ✓
- LOO is configuration-dependent: cost -0.24 under the contaminated v11 stack; GAINS
  +0.06 under decontaminated v11 on adjusted data (bt2 fibref pair). Marginal portfolio
  value ≈ noise within the modern stack; conditional edge real.
- Interpretation: the alpha is not the golden ratio — raw levels have no edge. What
  works is *disciplined pullback-buying within confirmed uptrends*; fib merely supplies
  level definitions. The bull gate IS the signal.
**Verdict: keep exactly as-is. No expansion** (bear mode already disproven; bigger
weight unsupported). Also fixed bt2.py's daily-index start bug (same as m4/longrun).

## 6i. Methodology imports from owner sources — June 10, 2026
(Citadel Securities / Man Group / SemiAnalysis / Bridgewater — owner asked whether
their METHODS, not just views, can improve the quant stack. Read + tested same day.)

**Three-way split:**
1. **Already embodied** (independently converged): vol-aware sizing (Man), regime/
   environment conditioning (Bridgewater), drawdown-first design, OOS discipline,
   cost realism (Man AHL research hygiene). Good external validation of v12 choices.
2. **Tested, does NOT transfer with our data:**
   - Citadel "spot up, vol up" euphoria flag (from Flow Fragility, May 2026, read in
     full): price+VIX proxy over 35y of SPX → edge -0.52%, t=-1.3, tail risk no worse.
     Their framework is real but its edge lives in PROPRIETARY inputs (CTA exposure
     models, skew, ETF tape %, retail flow) we structurally cannot replicate. The
     published CONCLUSIONS enter via the context brief instead.
   - Narrow-breadth regime: fwd drag mild (-0.5%), not actionable.
3. **ADOPTED — Man-style portfolio-level vol targeting** (`stored-pass finalize test`):
   scale total exposure by target/realized 21d vol, shift(1), DE-RISK ONLY (cap 1.0x):
   | | S | era1 | era2 | maxDD |
   |---|---|---|---|---|
   | per-position sizing only | +1.453 | +1.650 | +1.228 | -20.6% |
   | **+ portfolio vol-target 10% (cap 1x)** | **+1.555** | +1.894 | +1.211 | **-15.4%** |
   | + same with 1.5x cap (leverage) | +1.622 | +2.030 | +1.236 | -16.4% |
   No-leverage variant adopted for the experiment stack (1.5x also passes gates but
   introducing leverage is an owner decision). Production integration: daily driver
   needs portfolio-level realized vol → scale all suggested_position_mult — queued.
4. ~~Queued~~ **RESOLVED same day:**
   - Bridgewater growth/inflation quadrant: REJECTED — beats no-macro (1.355 vs 1.304)
     but LOSES to the existing macro_mult (1.453; quadrant era2 notably worse 1.04).
     Bonus finding: first isolation of macro_mult's value = **+0.15 Sharpe** vs none —
     the original VIX/QVIX/spread/PMI design is validated.
   - Man vol-target PRODUCTION WIRING shipped: daily driver computes your positions'
     trailing 21d realized vol (needs ≥3 positions) → portfolio scale (de-risk only,
     ≤1.0, 10% target) shown in the header and applied to all suggested sizes.
     Smoke-tested: 3-position demo correctly showed "vol-target scale ×0.4 ⚠ DE-RISK".

**Final experiment-stack numbers (June 10, end of day): daily cadence, 132 names,
FRAGILE/NA gate, per-position vol sizing + portfolio vol-target 10%, after base costs
≈ S +1.52, maxDD ≈ -15%.** (cost drag -0.05 from §cost-sensitivity applied to 1.555)

## 6j. v17 Council — multi-agent question revisited (June 10, late night)

Owner asked to re-examine the multi-agent reference (13 roles / 5 phases / bull-bear
debate / veteran personas) with fresh eyes. Revised position vs the first answer:

STILL REJECTED: many-role panels without distinct data (roles converge to one prior),
personas (theater), LLM as final decider for systematic flow (unbacktestable).

REVISED — ADOPTED as `agents/council.py`: the bull-bear debate reframed as a GROUNDED
PRE-MORTEM at the single-stock decision moment. Three calls: Bull steelman → Bear
steelman ("assume this long lost money in 6 months — why?") → Judge. Both sides argue
ONLY from a factual dossier (engine verdict, calibration base rates incl. the honest
"~54% entries positive" constraint, rank tiers, southbound flow z for HK, earnings
proximity, context brief). Judge may only counsel equal-or-more caution than the
engine. Every verdict logged (stance/conviction/price) to council_log.jsonl →
OUTCOME-GRADED after ~3 months, same trust gradient as the sentinel. The grading loop
is what the reference design lacks and what makes this defensible.

STATUS: built; dossier assembly verified end-to-end; first live deliberation pending
Zhipu daily quota reset (key exhausted by June-10 testing).

**Fundamentals in the council (owner question, resolved)**: the quant model correctly
excludes fundamentals (4 tests, all negative at trading horizon) — but the COUNCIL
dossier was missing them entirely. Now wired: TradingView screener live fetch (~0.7s,
all 3 markets, no auth) at deliberation time → P/E(TTM, computed vs live price), ROE,
net margin, revenue/profit growth. Verified: 茅台 P/E 19.0 ROE 31% rev -1.7%;
NVDA P/E 31.8 ROE 114% rev +65.5%. The "high-frequency" reality: statement data
updates quarterly, but valuation (P/E vs price) updates DAILY and the fetch is
sub-second — so every council session sees current numbers. Higher-frequency channels
if ever needed: Tushare daily_basic (A-share PE/PB daily series), AV OVERVIEW (US,
25/day), analyst estimate revisions (weekly cadence) — all reachable with embedded keys.

**News in the council (owner question, resolved same night)**: yes — news is THE
high-frequency layer (fundamentals are quarterly; headlines are hourly) and the council
is its correct home (advisory + graded; never the quant model). Wired: US via Alpha
Vantage NEWS_SENTIMENT (headlines + sentiment labels, dated), HK/A-share via yfinance
news (verified live: it surfaced "Kweichow Moutai's Annual Profit, Revenue Fall for
First Time" for 600519 — exactly the factual material a bear case needs). Design rule:
headlines enter the dossier as FACTS for bull/bear to interpret; AV's sentiment label
is shown but is one input, never a verdict — LLM-side interpretation of facts beats
pre-computed sentiment scores at this horizon. The council dossier now grounds all six
lenses: technicals, calibration base rates, flows, events/earnings, fundamentals, news,
plus the institutional context brief. QUEUED: FRAGILE-recovery
scout (monthly fundamentals/news review of bucket-blocked names — the one place
non-price evidence legitimately leads the 2y price window; flag-only, logged).

## 7. Risks and open questions

- **Small universe**: 21 stocks → some cells will be thin. Mitigation: cell-merge rule
  (§3 L2), per-cell sample minimums, and consider expanding the universe purely for
  validation (signals can be tested on stocks Gushen doesn't trade).
- **Bucket instability**: behavior classification may oscillate. Mitigation: hysteresis
  on bucket transitions, M1 measures this before anything depends on it.
- **Survivorship in B&H benchmark**: this universe is hand-picked winners, so "beat
  B&H" is a HARD benchmark — failing it in TREND×bull cells is acceptable if DD
  control compensates; the per-cell default (B&H+trail) handles this gracefully.
- ~~Macro snapshot lacks FRED here~~ **RESOLVED June 10**: snapshot re-pinned with full
  FRED data (VIX/USDCNY/UNRATE/T10Y2Y through 2026-06-08). All conclusions re-validated;
  macro-data sensitivity proved small. **Official full-macro numbers**:
  v10.2 baseline +0.407 (IS -0.33/OOS +1.09, dd -28.7%) → v11 +0.609 (+0.35/+0.92,
  -18.1%) → final stack **+1.097 (IS +1.150 / OOS +1.104), maxDD -13.8%**.
  Per market: A +0.25 vs B&H +0.03 | HK +0.77 vs +0.40 | US +1.22 vs +1.62 (dd -22% vs -24%).
- **Costs**: deliberately out of scope per owner decision (June 2026), but note the
  TREND×bull policy reduces churn, so costs would only strengthen its case.
