# BitBrave ← Gushen v15/v16 Leverage Analysis

> Date: 2026-06-10 · Cross-read of Gushen v15 build (SESSION_HANDOFF.md, ARCHITECTURE_v12_PROPOSAL.md, strategy/, agents/) against BitBrave's GUTS plan + open questions.

---

## 0. The urgent one: Band King is settled (BitBrave Q2)

BitBrave suspects its Band King Buy2/Sell1 "100% hit rate in every regime" is a confirmation-lag artifact and estimates realistic edge ~+200bps. **Gushen v15 already ran this to ground on June 10, and the answer is worse:**

- `band_king.compute_no_future()._find_peaks_troughs` uses a **centered window** (`series[i-order:i+order+1]`) — a trough at bar *i* requires the next 3–35 bars. Not lag; look-ahead.
- Repaint test (prefix recomputation): **0/20 sampled buy2 events were visible in real time** on the day the backtest credits them.
- A causal version (trough confirmed `order` bars later) has **no edge at all** (t≈0.5). Not fixable.
- At the live edge the centered window can never fire → production never traded these signals. Backtests bought perfect bottoms; live got nothing.
- Removing it deflated Gushen's headline Sharpe ~30% (1.324 → honest 0.899 legacy / +0.445 portfolio).

**Consequences for BitBrave:**
1. Reopen the locked "TA signal panel (post-regime-backtest)" decision. The Band King keep is invalid. Realistic edge is ~0, not +200bps.
2. Re-run `ta_signal_backtest.py` / `ta_signal_regime_backtest.py` with the causal version (or drop Band King) before Phase 1 ships the Trader panel.
3. Good news: Gushen audited `golden_pit.py` and `jiu_zhuan.py` for the same pattern — **clean**. The Golden Pit and TD Sequential buy keeps stand.
4. Adopt the repaint test as a standard gate: any signal with t>5 must pass prefix recomputation (signal computed on data[:i] must match signal computed on full history at bar i).

## 1. Fix the GUTS parity target: v9.4 → v15

`GUTS_INDICATORS_SPEC.md` plans parity tests against **Gushen v9.4**. That version contains the band_king look-ahead, dead Stage 3.5 code (fund_bonus computed but never added to composite), and the inflated per-stock metric. Parity against v9.4 = locking bugs into the shared library with a test suite that enforces them.

**Parity target should be v15 `strategy/scoring.py` post-June-10 audit** (band_king removed from score paths, hold-health ported, hysteresis ported).

## 2. What to extract — concrete inventory from the v15 tree

The GUTS extraction decision (indicator math yes, ScoringEngine no) is right. Here is exactly what exists and is worth taking:

### guts/indicators/ — from `strategy/scoring.py::precompute()` (~200 lines of math)
- MACD (12/26/9, golden/death cross), KDJ depth, RSI depth, Bollinger weekly buy/sell, ADX/±DI (`adx_strong`), MA stack (5/20/50/60/120/200, golden/death/aligned), bullish divergence, chain resonance, ATR.
- **Regime + hysteresis regime**: plain `close > MA200` and the ±3% band version (`bull_regime_hyst`: bull >1.03×MA200, bear <0.97×, else hold state). The hysteresis variant is validated (see §4-Q1/Q5) and BitBrave's SPY regime classifier should get the same treatment.
- `compute_hold_health()` — trend-health for held positions. Small, self-contained, and the single highest-value piece of Gushen's 2026 research (see §4-Q1, Q3).

### guts/dzh/ — from `dzh_indicators/`
- `golden_pit.py`, `jiu_zhuan.py` as-is (audited clean).
- `band_king.py` only as the **causal** rewrite, shipped with a docstring warning and the repaint test attached. Or don't ship it — it has no edge.

### guts/macro/ — from `scoring.py::compute_macro_mult()` + `data_fetcher.py::fetch_macro_data()`
- VIX bands, QVIX bands, 10y-2y spread, China PMI → [-2,+2] risk score → sizing multiplier. This is structurally the same shape as BitBrave's 5-factor FRED scorecard — one scorecard primitive (factor → band → score → aggregate) serves both products with different factor configs.

### guts/data/ — from `strategy/data_fetcher.py` (1191 lines, the most mature module)
- `RateLimiter`, `with_retry` decorator, multi-source fallback chains (Tushare→akshare→yfinance), column standardization, OHLCV cache (`gushen_cache.py` SQLite pattern), FRED + akshare macro fetchers, TradingView screener fundamentals (~0.25s, 200+ fields).
- This directly serves the GUTS "mutualize data fetching" goal across all four products. BitBrave's ETF universe needs only the US/yfinance + FRED paths initially — extraction is subtractive, not new work.

### guts/validation/ — NEW module, arguably the most valuable export
Gushen's hard-won methodology, none of it product-specific:
- **Repaint/prefix-recomputation harness** (the test that killed band_king).
- **Pinned macro snapshot** (`bt2.py` pattern): FRED/akshare revise history, so the same code gave S=1.476 in May and 1.324 in June. BitBrave's fortress_backtest CAGR 10.87% has the same exposure — pin snapshots or accept drifting baselines.
- IS/OOS split gates, per-market non-regression gates, one-change-per-experiment, results JSONL log.
- The calibration-table builder (composite → empirical P(positive), mean fwd return per bin — `scripts/build_calibration.py`, 102 lines, generic).

## 3. Repo layout (BitBrave open question: pending design #3)

**Own package.** Standalone `guts` repo, pip-installable, semver, parity + repaint tests in CI; the four products pin versions. Submodules are friction × 4 products; vendoring recreates exactly the drift that left BitBrave's spec pointing at v9.4 while Gushen moved to v15. The drift already happened once — in documentation. Don't let it happen in code.

Sequencing for the ~5-day plan: extract `guts/validation/` **first** (day 1), then indicators with parity-vs-v15 tests, then macro, then data. Validation first means every later extraction lands with its tests.

## 4. Gushen evidence applied to BitBrave's open questions

**Q1 — Tilt trigger calibration (too lax/too strict?).** Gushen's biggest 2026 result says this is the wrong knob. Threshold tuning experiments were marginal; what moved the needle was **entry/hold separation + hysteresis**: hold-model exits + regime hysteresis took portfolio IS Sharpe from −0.34 to +0.46 and halved maxDD (−31.5% → −15.6%). Translation for tilts:
- Add hysteresis to the trigger conditions: tilt ON at price >1.02×200d SMA, OFF at <0.98×, hold state between. Without this, "price > 200d SMA" whipsaws exactly when it matters.
- The de-tilt condition should NOT be the negation of the entry condition. Use a separate deterioration measure (hold-health style) with 2-bar confirmation.
- Per-tier: differentiate **tilt size** (CrazyGain larger overlay, Turtle ~0), not trigger logic. One signal, three exposure caps — keeps one codepath testable.

**Q1b — Fundamental feed for tilts.** Two Gushen receipts: (a) stock-level fundamental scoring at weekly cadence had IC≈0 — quarterly data is too noisy for fast triggers; if fundamentals enter tilts, use slow percentile bands (P/E percentile vs 5y), not fresh prints. (b) Don't buy FactSet yet — Gushen already has working **TradingView screener** integration (200+ fundamental fields, ~0.25s, free) plus an Alpha Vantage earnings key. Lift it via guts/data/ and see if it covers Phase 2.5 before paying for a feed.

**Q2 — Band King validation.** Answered, see §0. Methodology answer for "how to validate this honestly": prefix-recomputation repaint test + causal re-implementation + live-edge check ("can this signal ever fire on the most recent bar?"). All three are now standard Gushen gates; ship them in guts/validation/.

**Q3 — TD-13 sell.** Don't salvage it. Gushen's structural finding: sell-side needs *different machinery* than buy-side — its composite measured entry-attractiveness, and reusing it for exits ejected winners on strength. Same family of error: TD-13 sell asks an entry-style exhaustion count to do an exit job. Replace sell-side TA with a hold-health/trend-deterioration measure rather than gating TD-13 differently. (Also matches Gushen keeping Nine Turns buy and never trusting the sell side.) The "post-2009 sample" worry is real but untestable — you'd be tuning gates on the same unrepresentative sample.

**Q4 — Trader LLM scope.** Gushen v16 converged independently on the same boundary: deterministic engine, LLM only writes commentary on changed/flagged names, never alters schedule. Keep BitBrave's narrow scope. The upgrade path is not more LLM agency — it's **better inputs to the explainer**: Gushen's calibration layer found absolute composite is not a return scale (flat ~55% P(+4w) across bins); its real information is rank and entry timing. Give BitBrave's Trader LLM empirical stats ("this signal: 64% positive 4w, n=1842") instead of raw scores, and the `ta_reasoning` text becomes honestly calibrated rather than narrative.

**Q5 — CIO 48h cooldown.** Wrong dichotomy (too long vs too short). Whipsaw protection belongs in the *signal* (hysteresis bands on the regime classifier), thrash protection in the *process* (cooldown). With a hysteretic SPY classifier, regime flips are already rare and meaningful, so 48h cooldown costs little; without hysteresis, no cooldown value is right. Add hysteresis first, keep 48h, revisit only if a real regime shift gets missed.

**Q6 — Off-strategy holdings.** "Flag, don't force" is right, and Gushen v16 shows the stronger version: every position (legacy included) carries an **exit contract** ({stop, health threshold, max_weeks}) set by the user, which the engine then *enforces as reminders*; divergences go to a **discretion ledger** (you-vs-engine, outcome attribution over time). User agency preserved, but drift becomes visible and measurable. Port both patterns into the Advisor.

**Q7 — PRIV at 10%.** Outside Gushen's evidence base. One note: Gushen's standing rule "no new rules without new information" suggests making the 2027 ramp **CIO-proposed, not automatic** — auto-ramp is a future config change scheduled before the information (14 more months of PRIV history) exists. Let the CIO propose it with the data in hand. (Not financial advice; sizing is your call.)

## 5. Where BitBrave is "a tool of good use" — the strategic point

Gushen's v16 pivot is the most transferable insight in the repo: after exhausting signal research, the team concluded **the behavior gap (1.5–4%/yr) is worth more than any feasible Sharpe gain**, and rebuilt the product as a co-pilot decision layer — calibration (probabilities, not points), exit contracts, portfolio-first morning view, discretion ledger.

BitBrave's end users are a tax-agnostic individual + ~10 friends. For that audience the alpha was never going to come from tilt-trigger calibration — it comes from preventing behavioral errors: panic de-risking, idle cash, legacy-position drift, abandoning the plan in drawdowns. BitBrave's architecture already points this way (deterministic engine, Advisor surfaces, forced fill on deadline). Lean into it:

1. **Calibration everywhere a number is shown.** Never display a score or signal without its empirical record (P(positive), mean, n, regime). `build_calibration.py` is 102 lines and generic.
2. **Exit/commitment contracts at the bucket level.** SAA changes and tilts carry pre-committed unwind conditions from day one; the Advisor enforces them as reminders.
3. **Discretion ledger per user.** Every user override of a Trader schedule or Advisor recommendation is logged with outcome attribution. After a year, each friend sees *their own* behavior gap in bps. No other consumer product shows this.
4. **Portfolio-first view** before any signal: exposure, concentration, drawdown state (Gushen v16's header, directly portable to the dashboard).

This also retroactively validates the GUTS decision: what's shared is primitives + validation + data; what's product-specific is composition — and BitBrave's composition advantage is the advisory layer, not the signals.

## 6. Action list

1. **Now:** reopen the TA panel decision; re-run regime backtests with causal Band King (expect: drop it).
2. **Spec fix:** GUTS_INDICATORS_SPEC parity target v9.4 → v15 post-audit scoring.py.
3. **GUTS build order:** validation harness → indicators (parity vs v15) → macro scorecard primitive → data fetcher. Own-package repo, version-pinned per product.
4. **BitBrave Phase 1 additions (cheap, high-value):** hysteresis on the SPY regime classifier; hysteresis + separate de-tilt condition on tilt triggers; pinned macro snapshots in all backtests.
5. **BitBrave Phase 2 (the "good use" layer):** calibration tables for every panel signal; exit contracts on tilts/SAA changes; per-user discretion ledger; portfolio-first dashboard header.
6. **Try TV screener via guts/data/ before contracting a fundamental feed.**
