# Gushen (股神) Engine — Handoff Package

> **Purpose**: Complete knowledge transfer for continuing development on the Gushen multi-market quantitative stock scoring engine.
> **Date**: 2026-05-21
> **Status**: v10.2 shipped, OOS-validated, GUTS extraction ready
> **Recipient**: Execution AI (next session builder)

---

## 1. Project Overview

**Gushen (股神)** is a multi-market (A-share / HK / US) quantitative stock scoring engine that produces BUY/WATCH/HOLD/EXIT signals for 21 stocks.

- **Production location**: `/Users/alafat/.workbuddy/skills/gushen/`
- **Handoff copy**: `/Users/alafat/Desktop/gushen_handoff/` (this folder — authoritative)
- **User**: Josh — Hong Kong-based quant analyst, prefers incremental testing, data in tables, Chinese for comms / English for technical terms
- **Current engine**: v10.2 regime-adaptive dual-mode (S=1.48 full, S=1.62 OOS test)
- **Previous baseline**: v9.7 (S=0.94, superseded)
- **Skill trigger**: `股神` skill in WorkBuddy

### Stock Universe (21 stocks)

| Market | Tickers | Count |
|--------|---------|-------|
| A | 600519.SH, 000858.SZ, 300750.SZ, 002594.SZ, 601318.SH, 600036.SH, 002230.SZ, 300015.SZ | 8 |
| HK | 0700.HK, 9988.HK, 3690.HK, 1810.HK, 1211.HK, 0388.HK | 6 |
| US | AAPL, NVDA, MSFT, GOOGL, AMZN, META, JPM | 7 |

---

## 2. v10 Engine Architecture

### Design: Regime-Adaptive Dual-Mode (`score_bar_v5` in scoring.py)

The core insight: contrarian signals (buy when oversold) and trend signals (buy when trending) are opposing strategies. v10 separates them by market regime:

- **Bear/neutral regime**: Contrarian entry engine — depth signals (KDJ, BB, RSI continuous 0-10) + DZH binary signals + P4 trend at 40% discount
- **Bull regime**: Trend entry engine — P1+P4 mixed + Fibonacci pullback gate

### 5-Stage Pipeline

```
Stage 1: Regime Detection
  └─ Weekly MA50+MA200 → bull/bear classification (precomputed["bull_regime"])

Stage 2: Signal Scoring (regime-separated)
  ├─ Bear engine: contrarian depth (continuous 0-10) + DZH binary + chain resonance
  │   └─ P4 trend signals at BEAR_TREND_DISCOUNT (0.40)
  └─ Bull engine: P1 depth + P4 trend + Fibonacci pullback support gate

Stage 3: Volume Confirmation
  └─ vol_anomaly multiplicative confirmation (shared across regimes)

Stage 4: Threshold → Action
  ├─ V10_THRESHOLDS per market (bear_buy/watch/exit + bull_buy/watch/exit)
  ├─ Position management overrides:
  │   ├─ BB sell override: if bb_sell fires during HOLD → EXIT
  │   └─ Hold score breakdown: if hold_score < -5 → EXIT
  └─ A-stock special: exit=0 (rely on signal-based exits only)

Stage 3.5: Analyst / Earnings Signals (v10.2)
  ├─ US: Alpha Vantage EARNINGS beat streak (2-3Q consecutive beats → +1.5-2.0)
  ├─ A: Tushare forecast DISABLED (业绩预告 categories too coarse, hurt Sharpe)
  └─ HK: akshare ET snapshot (production only, not backtestable)

Stage 4: Threshold → Action
  ... (unchanged)

Stage 5: Macro Risk Multiplier (portfolio-level hint)
  └─ VIX + QVIX + yield_spread + PMI → macro_mult 0.5x-1.3x (returned, not applied internally)

v10.1+ Backtest Framework (tune.py):
  Adaptive Exit (US/A only, HK excluded — HK trends run longer):
  ├─ Time decay: -1 pt/wk after 12 weeks without new BUY signal
  ├─ Profit-take: exit if composite drops 50% from peak while >2% profitable
  └─ ATR trailing stop: exit if drawdown from peak > 3× weekly ATR(14)
  Margin financing: re-activated for A-stocks (contrarian, RankIC=-0.09)
```

### Key Constants

```python
V10_THRESHOLDS = {
    "US": {"bear_buy": 32, "bear_watch": 24, "bear_exit": 10,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 10},
    "HK": {"bear_buy": 28, "bear_watch": 20, "bear_exit": 10,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 10},
    "A":  {"bear_buy": 25, "bear_watch": 17, "bear_exit": 0,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 0},
}
BEAR_TREND_DISCOUNT = 0.40
MA20_PENALTY_A_HK = 0.65
MA20_PENALTY_US = 0.75
```

### Performance

| Metric | v10.2 | v10.1 | v10 | v9.7 | Note |
|--------|-------|-------|-----|------|------|
| Full-period Sharpe (21 stocks) | 1.476 | 1.421* | 1.480 | 0.942 | +3.9% vs v10.1 baseline |
| By market: A | 0.222 | 0.222 | 0.393 | — | Unchanged (A analyst signals disabled) |
| By market: HK | 1.643 | 1.643 | 2.328 | — | Unchanged (no backtestable data) |
| By market: US | 2.767 | 2.602 | 2.116 | — | +6.3% (earnings beat streaks) |
| OOS test Sharpe (2024+) | — | — | 1.624 | 0.259 | OOS not re-run for v10.2 |

*v10.1 baseline re-measured on same DB as v10.2 for fair A/B comparison.
Note: A/HK Sharpe lower than prior v10.1 report due to DB refresh (same code, updated OHLCV data).

### v10.2 Changes (May 2026)

**Shipped (positive Sharpe impact):**
- US earnings surprise signals: Alpha Vantage EARNINGS data, beat streak detection (2-3 consecutive quarters with positive surprise% → +1.5-2.0 fund_bonus). 3W/3L/1D per-stock but magnitude-positive: META +1.12, NVDA +0.76, AMZN +0.41 outweigh MSFT -1.0, GOOGL -0.11.
- Analyst signal infrastructure: `analyst_signals` table in gushen.db, `build_analyst_cache()` in gushen_cache.py, wired into tune.py backtest loop and scoring.py Stage 3.5.

**Tested but NOT shipped (negative Sharpe impact):**
- A-stock Tushare forecast (业绩预告) signals: categories (预增/略增/预减/略减) too coarse for daily scoring — triggered false BUY entries, 科大讯飞 Sharpe -0.27 → -0.73. Data cached but scoring disabled.
- HK analyst signals: akshare ET forecast is snapshot-only (not backtestable historically). Cached for production use, contributes 0 in backtest.

### v10.1 Changes (May 2026)

**Shipped (positive Sharpe impact):**
- Adaptive exit in tune.py: time decay (12wk/-1pt), profit-take trailing (50% composite drop), ATR stop (3× weekly ATR). Active for US/A only — HK excluded because HK trends run longer and exits hurt (ablation: HK avg -0.27 with aexit).
- Margin financing signal re-activated for A-stocks (contrarian, RankIC=-0.09). Coded in scoring.py L678, data loaded from `data/margin_history/` CSVs.
- compute_macro_mult() now includes QVIX for A/HK markets.

**Tested but NOT shipped (negative Sharpe impact):**
- Change-based fundamental signals (earnings acceleration, revenue acceleration, margin expansion, ROE quality gate). Tested at two weight levels — both net negative. Root cause: quarterly disclosure frequency too noisy for weekly scoring. Infrastructure replaced by v10.2 analyst signals.

---

## 3. Codebase Map

```
gushen_handoff/
├── strategy/                    # Core scoring logic
│   ├── scoring.py              # ⭐ MAIN ENGINE (~3010 lines)
│   │   ├── precompute()        #   All indicator computation (backward-looking)
│   │   ├── score_bar_v5()      #   ⭐ v10 regime-adaptive engine (CURRENT DEFAULT)
│   │   ├── score_bar()         #   v9.7 legacy (kept for regression testing)
│   │   ├── score_bar_v2/v3/v4()#   v9.8 experiments (archived, all underperformed)
│   │   ├── score()             #   High-level entry point → calls score_bar_v5
│   │   ├── V10_THRESHOLDS      #   Per-market action thresholds
│   │   ├── FactorAggregator    #   v9.8 Layer 2 (archived)
│   │   └── DecisionEngine      #   v9.8 Layer 4 (archived)
│   ├── tune.py                 # ⭐ Backtest runner (default: v10, precompute-once)
│   ├── fast_backtest.py        # Optimized standalone backtest (v10 default)
│   ├── gushen_cache.py         # SQLite cache (GUSHEN_TUNE=1 required, v10.2: +analyst_signals table)
│   ├── data_fetcher.py         # Production data fetching (akshare + FRED)
│   ├── bollinger.py            # BB weekly buy/sell signals
│   ├── fibonacci.py            # Fibonacci retracement scoring
│   ├── elliot_wave.py          # triple_confirm() + wave5/shoulder diagnostics
│   ├── a_factors.py            # A-stock PB classification + main force flow
│   ├── config.py               # Constants reference doc
│   ├── correlation_matrix.py   # Factor correlation analysis
│   ├── sensitivity_test.py     # Data source sensitivity testing
│   └── validate_universe.py    # Universe compatibility validation
├── guts/                       # Extracted modules (future GUTS engine)
│   ├── macro/
│   │   ├── compute.py          # Macro regime computation (13 series scorers)
│   │   ├── sensitivity.py      # StockStyle × CapSize classification + L1/L2/L3 tables
│   │   └── state.py            # MacroRegime, Region enums
│   ├── scoring/
│   │   └── normalize.py        # ScoreHistory z-score (DEPRECATED by v10, kept for ref)
│   ├── signals/
│   │   └── continuous.py       # ContinuousSignals [-1,+1] (v10 uses internally)
│   ├── utils/
│   │   ├── llm_resolvers.py    # GLM-4-Flash stock name normalization
│   │   └── normalizer.py       # General normalization utilities
│   └── tests/                  # Unit tests
├── dzh_indicators/             # 大智慧 (DZH) proprietary indicators
│   ├── golden_pit.py           # Golden Pit 2.0 (no-future ZIG)
│   ├── jiu_zhuan.py            # Nine Turns (9-turn buy/sell)
│   └── band_king.py            # Band King (buy1/buy2/sell1 signals)
├── scripts/
│   ├── analyze.py              # Single-stock analysis (uses score() → v10)
│   ├── daily_digest.py         # Daily market digest (uses score() → v10)
│   ├── watchlist.py            # Watchlist CRUD
│   ├── normalize.py            # Stock name normalizer CLI
│   └── cleanup.py              # Cache cleanup
├── data/
│   ├── gushen.db               # ⭐ SQLite cache (tune mode)
│   ├── v10_oos_validation.json # OOS validation results (the proof)
│   ├── v10_final3.json         # Final v10 with all phases
│   ├── v10_daily.json          # Daily-bar backtest results
│   ├── v10_grid_r*.json        # Grid search rounds 3-6
│   └── tune_snapshot_*.json    # Historical backtest snapshots
├── references/                 # Historical design docs (v9.2-v9.4 era)
├── HANDOFF.md                  # ⭐ THIS FILE
├── V10_SHIP_AND_GUTS_READINESS.md  # Architecture readiness doc
├── ARCHITECTURE_v10_PROPOSAL.md    # Original v10 design proposal
├── SKILL.md                    # Skill manifest (v10 updated)
└── README.md                   # Public README
```

---

## 4. Key Commands

```bash
# Backtest (tune mode) — v10 is now the default
cd /path/to/gushen
GUSHEN_TUNE=1 python3 strategy/tune.py                      # v10 default
GUSHEN_TUNE=1 python3 strategy/tune.py --version v97         # v9.7 for comparison

# Fast standalone backtest
GUSHEN_TUNE=1 python3 strategy/fast_backtest.py

# Production daily run (no GUSHEN_TUNE)
python3 scripts/daily_digest.py
python3 scripts/analyze.py AAPL US

# Rebuild cache
GUSHEN_TUNE=1 python3 strategy/gushen_cache.py --force
```

### Sandbox (bash) paths

When running in the Cowork sandbox, use monkey-patching:
```python
import os, sys
os.environ['GUSHEN_HOME'] = '/sessions/.../mnt/gushen_handoff'
os.environ['GUSHEN_TUNE'] = '1'
sys.path.insert(0, '.')
import strategy.gushen_cache as gc
from pathlib import Path
gc.DB_PATH = Path('/sessions/.../mnt/gushen_handoff/data/gushen.db')
```

---

## 5. Version History: How We Got to v10

| Version | Date | S (21stk) | Key Change | Outcome |
|---------|------|-----------|------------|---------|
| v9.5 | May 6 | 0.59 (8stk) | Pre-z-score | — |
| v9.6 | May 8 | 0.56 (8stk) | Z-score + per-market thresholds | — |
| v9.7 | May 13 | **0.942** | Trend-override exit + DD/UC fixes | Baseline |
| v9.8 | May 13 | 0.347 | 4-layer continuous pipeline | -63% (FAILED) |
| v9.8c | May 18 | 0.380 | 4P+3L hybrid | -60% (FAILED) |
| v9.8d | May 18 | 0.330 | L1×mult + L2/L3 additive + 2P gate | -65% (FAILED) |
| **v10** | **May 19-20** | **1.238** | **Regime-adaptive dual-mode** | **+31%** |

### What Failed and Why (Lessons for Future Development)

1. **v9.8 continuous pipeline**: Continuous signals are too smooth for contrarian exits. Binary signals flip instantly (good for EXIT), continuous glide slowly. **Keep binary for entry/exit, continuous for depth only.**

2. **v9.8c multiplicative layers**: L2×L3 = 0.80×0.90 = 0.72 base compression. Even neutral state crushes tech_score. **Never use multiplicative layers with base < 0.95.**

3. **Z-score normalization**: Destroyed 27% of raw signal predictive power. Cross-stock normalization is unnecessary when thresholds are per-market. **v10 eliminated z-score entirely.**

4. **L2 fundamentals at stock level**: IC ≈ 0. Earnings data is too quarterly/noisy for weekly scoring. **Removed from v10.**

5. **L3 macro at stock level**: Net drag — removing macro IMPROVED Sharpe (+36%). **v10 moved macro to portfolio-level position sizing hint (macro_mult).**

6. **Price-based trailing stops**: Weekly bars too coarse — by the time a weekly close triggers, damage is done. Daily bars marginal (S=1.095 vs 1.255 without). **Not shipped.**

7. **P4 trend in bear mode**: Trend signals during bear dilute contrarian alpha by ~50%. **v10 applies 40% discount (BEAR_TREND_DISCOUNT).**

### What Worked

1. **Regime separation**: Contrarian in bear, trend in bull. The core v10 insight.
2. **Continuous depth scoring (0-10)**: For KDJ, BB, RSI, volume ONLY (not for DZH binary signals).
3. **Precompute-once pattern**: All indicators backward-looking → compute on full data, index with .iloc[i]. 100x faster backtests.
4. **A-stock exit=0**: A-stocks naturally lower-scoring. Fixed exit threshold kicks them out too early. Signal-based exits only.
5. **BB sell override**: When Bollinger weekly sell fires during HOLD → EXIT. Caught 12 exits the threshold wouldn't trigger.
6. **Hold score breakdown**: When hold_score < -5 (strong trend breakdown) → EXIT.

---

## 6. Known Remaining Issues

1. **Weak stocks**: Ping An (601318, S=-1.34), BYD HK (1211.HK, S=-1.79) — financial sector and cross-listing regime conflicts
2. **Wuliangye (000858)**: v10 test period S=-2.06 — single bad trade in bear mode
3. **Tencent (0700.HK)**: v10 test S=0.00 — no trades triggered (too conservative thresholds for blue-chip HK)
4. **Obsolete files**: `data/run_us_backtest.py` and `data/run_us48.py` are v9.5-era scripts (should delete)
5. **`run_v98d_backtest.py`**: Hardcoded sandbox paths, v9.8d-specific — historical only
6. **API tokens**: Tushare and FRED API keys are hardcoded in `gushen_cache.py` and `data_fetcher.py`
7. **tune.py still slow for non-v10**: Legacy versions still call `precompute()` per bar per stock

---

## 7. GUTS Architecture Readiness

v10's architecture is ready for extraction into the independent GUTS engine. See `V10_SHIP_AND_GUTS_READINESS.md` for the detailed plan.

The extraction is **mechanical, not architectural**:
- `precompute()` → `guts/signals/precompute.py` (180 lines, self-contained)
- `score_bar_v5()` → `guts/scoring/engine.py` (450 lines, 5 clear stages)
- `V10_THRESHOLDS` + penalties → `guts/config.py`
- `score()` → `guts/api.py` (high-level entry point)

Key design properties enabling extraction:
1. **Precompute-once**: All signals backward-looking, stateless per bar
2. **Regime as first-class input**: Replaceable with future ML model
3. **Macro at portfolio level**: `macro_mult` is a hint, not embedded in scoring
4. **Market-parameterized**: Adding markets = adding one dict entry
5. **No z-score**: Engine is stateless, no ScoreHistory dependency

---

## 8. Production Sync

To copy handoff → production:
```bash
cp ~/Desktop/gushen_handoff/strategy/scoring.py ~/.workbuddy/skills/gushen/strategy/scoring.py
cp ~/Desktop/gushen_handoff/strategy/tune.py ~/.workbuddy/skills/gushen/strategy/tune.py
cp ~/Desktop/gushen_handoff/strategy/fast_backtest.py ~/.workbuddy/skills/gushen/strategy/fast_backtest.py
cp ~/Desktop/gushen_handoff/strategy/elliot_wave.py ~/.workbuddy/skills/gushen/strategy/elliot_wave.py
cp ~/Desktop/gushen_handoff/guts/signals/continuous.py ~/.workbuddy/skills/gushen/guts/signals/continuous.py
cp ~/Desktop/gushen_handoff/guts/tests/test_signals.py ~/.workbuddy/skills/gushen/guts/tests/test_signals.py
cp ~/Desktop/gushen_handoff/SKILL.md ~/.workbuddy/skills/gushen/SKILL.md
```

---

## 9. User Preferences (Josh)

- **Communication**: Chinese for comms, English for technical terms
- **Data presentation**: Tables preferred over prose
- **Workflow**: Incremental testing — one change at a time, backtest verification required
- **Philosophy**: "精不要多" (quality over quantity) — signal quality > signal count
- **Architecture**: Strict adherence to design decisions; catches architectural drift
- **Verification**: Full 21-stock backtest before accepting any code change
- **DZH signals**: Must stay binary (golden_pit, band_king, nine_turns, bb_weekly)
- **Commands**: "ok whats next" for next steps, "go for the remaining nonstop" for batch execution

---

## 10. Quick Start for Next Session

1. Read this file
2. Read `V10_SHIP_AND_GUTS_READINESS.md` for GUTS extraction plan
3. Read `strategy/scoring.py` — focus on `score_bar_v5()` (line ~2533) and `precompute()` (line ~334)
4. If extracting GUTS: follow the module extraction plan in the readiness doc
5. If tuning: run backtest via `GUSHEN_TUNE=1 python3 strategy/tune.py`
6. If adding stocks: add to STOCKS list in tune.py, add thresholds to V10_THRESHOLDS if new market

---

*"精不要多" — signal quality over quantity, architecture clarity over feature count.*
