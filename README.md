# Gushen v18 — self-contained package

> Created June 10, 2026. The validated quant engine + GLM-4.7 agent layer.
> Full provenance: ARCHITECTURE_v12_PROPOSAL.md (all experiments, gates, rejections).

## Version ledger
| v | What |
|---|---|
| v10.2 | inherited production (May 2026) — baseline later found look-ahead-inflated |
| v11 | decontamination (band_king removed) + hold-health exits + regime hysteresis |
| v12 | behavior buckets + FRAGILE/NA gate + vol sizing; 10y true-OOS validation PASSED |
| v13/v14 | universe 21→54→132 + cross-sectional top-30 selection |
| v15 | self-contained package + embedded keys + GLM agent shell (sentinel/deep-dive/normalizer) |
| v16 | co-pilot layer: calibration, exit contracts, portfolio-first view, discretion ledger |
| v17 | council — grounded bull/bear/judge pre-mortem, outcome-logged |
| **v18** | **current**: Man portfolio vol-target (validated §6i + prod-wired), context-brief knowledge layer, cost-validated daily cadence (**~1.5 S after costs, ~-15% maxDD**) |

## Quick start (zero setup — keys are embedded)

```bash
cd gushen_v18

# Your daily assessment — FIRST RUN creates data/my_list.json + my_positions.json
# as templates and asks you to fill them in (instructions inside each file):
python3 agents/daily_driver.py

# Discovery scan: universe-wide BUYs + calibrated top-30 ranking (★ = not your list):
python3 agents/daily_driver.py --scan

# Deep dive on one stock (engine + GLM-4.7 second opinion):
python3 agents/daily_driver.py --deep NVDA

# Discretion ledger review (you vs the engine, grows with history):
python3 agents/daily_driver.py --review

# v17 Council — grounded bull/bear/judge pre-mortem for ONE name before you act
# (3 GLM calls; verdicts logged to council_log.jsonl for outcome grading):
python3 agents/council.py NVDA      # or: python3 agents/council.py 茅台

# Risk sentinel over the latest shadow run (veto-only, shadow mode):
python3 agents/risk_sentinel.py

# Refresh the expert-context brief (Citadel/Man/SemiAnalysis/Bridgewater → grounds
# the GLM deep-dive & sentinel; paste paywalled articles into data/context_inbox/):
python3 agents/context_brief.py

# Wide-universe shadow run (132 names) / canonical backtests:
python3 scripts/shadow_run.py
python3 scripts/m4_portfolio.py --volsize --universe v13
python3 scripts/daily_wide.py
```

**ONE thing to fill in**: `strategy/gushen_keys.py` → `ZHIPU_API_KEY` (same key as
GLM-4; copy from your WorkBuddy config). Without it the quant engine works fully;
only GLM features are disabled. Embedded keys: Tushare, FRED, Alpha Vantage, Tiingo.
⚠ Keys live in code by owner choice — never publish this folder.

## Architecture (v15 = v12 engine + LLM shell)

```
L1  QUANT CORE (deterministic, frozen, backtested 2015-2026)
    contrarian depth×confirmation entries · hold-health exits · regime hysteresis
    · TREND/REVERT/FRAGILE buckets with FRAGILE/NA entry gate · vol-targeted sizing
    · top-30 cross-sectional selection · macro multiplier
    → the ONLY layer that initiates positions
L2  RISK SENTINEL (GLM-4.7, veto-only, SHADOW MODE)
    earnings proximity + event risk on changed/flagged names (<10 LLM calls/day)
    verdicts CLEAR/CAUTION/VETO logged to data/sentinel_log.jsonl — 6 months of
    shadow evaluation required before any real authority
L3  RESEARCH OPS (process rules in ARCHITECTURE doc §5: gates, repaint tests,
    frozen rules, one change per experiment)
L4  EXPLANATION (GLM deep dives / commentary — zero decision authority)
```

Authority rule everywhere: **LLM may reduce risk, never add it.**

## Validated numbers (honest data, see ARCHITECTURE doc for full provenance)

- Daily cadence, 132 names, vol-weighted: **S +1.45** (era1 +1.65 / era2 +1.23), maxDD -20.6%
- 10-year true-OOS validation passed; beats buy-and-hold in A and HK, lags in US bull
- Character: pays a toll in runaway bulls, gets paid in hard markets, half the drawdown
- NOT modeled: transaction costs (owner decision; daily cadence makes this the largest
  unquantified risk). Backtest ≠ live: expect live Sharpe materially lower until the
  shadow log proves otherwise.

## Universes

- `data/my_list.json` — YOUR list; the daily driver scores exactly this (edit freely)
- 132-name wide universe (universe_v13_new + universe_v14_breadth) — validation sample
  + opportunity scan (top-30 selection); not automatically your portfolio

## Compute budget

Scoring = milliseconds/stock (cached indicators). Full daily pass = seconds, no LLM.
GLM-4.7 is invoked only for: action changes, BUY/EXIT review, earnings-window holds,
and on-demand deep dives. Typical day: <10 calls.

## Maintenance

- Data refresh + precompute rebuild: see scripts/warm_precomp.py and the weekly
  scheduled task ("gushen-weekly-shadow-run") in the parent folder's setup
- Re-pin macro deliberately: delete data/macro_snapshot.pkl + rerun scripts/run_bt_cached.py --snapshot-only
- All experiment results append-only: data/bt2_results.jsonl, shadow_log.jsonl,
  sentinel_log.jsonl, daily_log.jsonl
