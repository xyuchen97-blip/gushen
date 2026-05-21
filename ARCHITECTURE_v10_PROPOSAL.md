# Gushen v10 Architecture Proposal — Regime-Adaptive Dual-Mode Engine

**Date**: 2026-05-18
**Status**: Proposal, backed by empirical analysis on 12,302 bars across 10 stocks

---

## 1. Diagnosis: Why the Current Architecture Underperforms

### The Core Contradiction

The current system adds contrarian signals (P1: "buy when oversold") and trend signals (P4: "buy when trending up") into one composite score. These are opposing philosophies. Empirically:

| Condition | US ann ret | HK ann ret | A ann ret | Bars |
|-----------|-----------|-----------|----------|------|
| P1 only (contrarian) | **+63.9%** | **+28.0%** | +7.5% | 2,963 |
| P4 only (trend) | +34.2% | -20.8% | +10.1% | 5,153 |
| P1 + P4 together | +33.0% | +19.4% | -0.1% | 1,698 |

**Adding P4 to P1 cuts contrarian alpha by ~50%.** P4 contributes 49% of tech_score but has IC = 0.010 (vs P1's 0.048). It's the loudest voice with the least to say.

### Regime Analysis Confirms It

| Regime | Contrarian (J<20) | Trend (ADX strong) |
|--------|-------------------|-------------------|
| BULL | +14.0% (weak — nothing is oversold) | **+24.8%** (trend works here) |
| BEAR | **+19.8%** (contrarian shines) | -6.5% (following downtrend = destructive) |

The strategy is mixing a bear-optimal tool (contrarian) with a bull-optimal tool (trend) and getting mediocre results in both.

### Components That Actively Hurt

| Component | Problem | Evidence |
|-----------|---------|----------|
| P3 Fibonacci | Fires in bear regime where it predicts losses | Bear: -6.5% vs +10.6% baseline. **Bull: +41.5%** — it works, just in wrong context |
| `ma_golden_cross` | Scored as BUY (+5) but empirically a SELL signal | -20.1% overall, HK: -122% |
| Z-score normalization | Destroys 27% of raw signal predictive power | RankIC: raw 0.0323 → normalized 0.0237 |
| L2 Fundamentals | Near-zero IC, adds complexity | IC = +0.010, RankIC = -0.002 |
| L3 Macro (per-stock) | Wrong level — macro is portfolio-wide, not stock-specific | IC = +0.002 |
| 3-pillar combos | P1+P3+P4 identifies bull traps, not entries | -31.7% annualized |

### Components That Work

| Component | IC | Return when active | Verdict |
|-----------|----|--------------------|---------|
| P1 contrarian signals | +0.048 | +30.1% (P1-only) | **Core alpha source** |
| P2 chain resonance | +0.033 | +180.2% (C2) | **High quality, rare** |
| L1 capital (volume) | +0.031 | +78.0% (vol anomaly) | **Genuine confirmation** |
| golden_pit | — | +147.7% | Star signal |
| band_king_buy2 | — | +830.0% | Star signal (rare) |
| bb_weekly_buy | — | +287.2% | Star signal (rare) |
| boll_kdj_chain | — | +180.2% | Star signal |
| kdj_oversold | — | +48.7% | Workhorse signal (22% fire rate) |
| Fib in BULL only | — | +41.5% (vs +20% baseline) | **Works when direction-filtered** |

---

## 2. Proposed Architecture: Regime-Adaptive Dual-Mode

### Design Principles

1. **Separate entry logic from position management.** "Should we buy?" and "should we keep holding?" are different questions requiring different signals.
2. **Match strategy to regime.** Contrarian in bear/neutral, trend-hold in bull. Never blend.
3. **Continuous intensity over binary triggers.** "How oversold?" matters more than "oversold or not?"
4. **Capital confirmation is universal.** Volume confirms both contrarian reversals and trend continuations.
5. **Macro at portfolio level, not stock level.** VIX/QVIX scales position sizing, not per-stock composite.

### Architecture Diagram

```
┌──────────────────────────────────────────────────┐
│               REGIME SELECTOR                     │
│  bull  = close > MA200                            │
│  strong_bull = bull AND ADX>25 AND +DI>-DI        │
│  bear  = close < MA200                            │
│  (NOT scored — selects which engine runs)          │
└──────────┬───────────────────┬────────────────────┘
           │                   │
     BEAR/NEUTRAL         BULL REGIME
           │                   │
           ▼                   ▼
┌─────────────────────┐ ┌──────────────────────────┐
│  CONTRARIAN ENGINE   │ │  TREND + PULLBACK ENGINE  │
│  (Entry-focused)     │ │  (Hold + Dip-buy)         │
├─────────────────────┤ ├──────────────────────────┤
│ Signals:             │ │ Signals:                  │
│  P1 contrarian (all) │ │  P4 trend (hold only)     │
│  P2 chain resonance  │ │  Fib retracement (bull)   │
│  L1 volume confirm   │ │  L1 volume confirm        │
│                      │ │                           │
│ Scoring:             │ │ Scoring:                  │
│  contrarian_intensity│ │  IF pullback:             │
│  = kdj_depth         │ │    P1 + Fib → BUY dip    │
│  + bb_depth          │ │  IF no pullback:          │
│  + rsi_depth         │ │    P4 active → HOLD       │
│  + DZH_signals       │ │    P4 breaks → EXIT       │
│  × (1 + vol_confirm) │ │                           │
│                      │ │ Exit:                     │
│ Entry: score > thresh│ │  Trail stop from peak     │
│ Exit: sell penalties  │ │  P4 breakdown → tighten   │
│       or regime shift │ │  Time decay (20 bars)     │
└─────────────────────┘ └──────────────────────────┘

┌──────────────────────────────────────────────────┐
│             PORTFOLIO LEVEL (shared)              │
│  Macro regime → position sizing                   │
│    Crisis (VIX>30): max 30% exposure              │
│    Stress (VIX 20-30): max 60% exposure           │
│    Normal (VIX<20): max 100% exposure             │
│  No macro in per-stock scoring                    │
└──────────────────────────────────────────────────┘
```

### Detailed Signal Design

#### Contrarian Engine (Bear/Neutral Mode)

**Continuous intensity signals** (replace binary on/off):

| Signal | Current (binary) | Proposed (continuous 0-10) | IC improvement |
|--------|-----------------|---------------------------|---------------|
| KDJ depth | J<20 → +5 | `max(0, 20-J) / 20 × 10` | +0.025 → +0.029 RankIC |
| BB depth | below lower → +15 | `max(0, -bb_pct) × 15` | +0.024 IC |
| RSI depth | (not used) | `max(0, 30-RSI) / 30 × 8` | +0.014 IC (new) |
| Volume z-score | vol > 1.5×MA → +8 | `min(vol_z, 3) / 3 × 8` | +0.030 IC (continuous) |

**Binary signals kept as-is** (rare, high-conviction events):

| Signal | Score | Rationale |
|--------|-------|-----------|
| golden_pit | +10 | DZH proprietary, +148% when active |
| band_king_buy2 | +10 | +830% (rare but powerful) |
| nine_turns_buy | +10 | Structural pattern, keep binary |
| bb_weekly_buy | +15 | Weekly timeframe confirmation |
| boll_kdj_chain (C2) | +15 | Sequential confirmation |
| boll_kdj_macd_chain (C3) | +22 | Triple confirmation |
| bullish_divergence | +12 | Price-indicator divergence |

**Removed from entry scoring:**
- `ma_golden_cross` — empirically a sell signal (-20.1%)
- `ma_aligned`, `adx_trend`, `macd_golden` — move to trend engine (hold logic)
- `fib_retracement_support` — move to bull pullback engine only
- All L2 fundamentals — IC ≈ 0
- All L3 macro — move to portfolio level

**New additions:**

| Signal | Type | IC | Market | Purpose |
|--------|------|-----|--------|---------|
| Volume-price divergence | Binary | +0.046 (A) | A-specific | Price falling + volume declining = selling exhaustion |
| OBV trend | Binary | +0.004 | All | Smart money accumulation/distribution |

#### Trend + Pullback Engine (Bull Mode)

**Hold signals** (determines whether to keep existing position):
- `ma_aligned`: MA20 > MA60 > MA120 → HOLD
- `adx_strong`: ADX > 25 with +DI > -DI → HOLD
- Price > MA50 → HOLD
- Any death cross or ADX breakdown → tighten exit to trailing stop

**Pullback BUY** (new entry in bull market):
The highest-value combined signal: bull regime + fib retracement support + P1 contrarian trigger = "buying the dip in an uptrend." This is where Fibonacci becomes valuable.

Conditions:
1. Bull regime (close > MA200)
2. Pullback detected (price dropped to fib 0.382/0.5/0.618 of recent swing)
3. At least one P1 contrarian signal fires (KDJ oversold, bb_buy, bullish_divergence)
4. Volume confirmation (vol_z > 1.0)

Expected return: +41.5% from fib-in-bull alone, amplified by P1 confirmation.

**Fibonacci redesign:**
- Current: rolling 50-week max/min → fires in both trends → negative IC
- Proposed: swing-based, direction-aware
  - Detect actual swing high/low points (not rolling window)
  - In bull: measure retracement from swing low to swing high
  - Support at 0.382/0.5/0.618 levels
  - Only fires when price pulls back to these levels AND is still in bull regime
  - Tighten tolerance from 2% to 1.5%

#### Entry Score Formula

**Bear/Neutral mode:**
```
contrarian_intensity = kdj_depth + bb_depth + rsi_depth + DZH_binary_signals
confirmation = 1 + vol_z_scaled × 0.3 + chain_bonus
entry_score = contrarian_intensity × confirmation
```

**Bull mode (pullback buy):**
```
pullback_score = P1_signals + fib_support_bonus
confirmation = 1 + vol_z_scaled × 0.3
entry_score = pullback_score × confirmation
```

No z-score normalization. Raw score → threshold → action.

#### Position Management (Separate from Entry)

| Rule | Condition | Action |
|------|-----------|--------|
| Trail stop | Position up > 15% from entry | Set stop at entry + 10% |
| Tighten on P4 break | Death cross or ADX < 20 | Move stop to entry price (breakeven) |
| Time decay | No P1/P2 re-confirmation in 20 bars | Tighten exit threshold |
| Regime shift | Bull → Bear | Tighten to trailing 8% |
| Trend override | Bear + ADX strong (existing) | Block EXIT → HOLD |

### Per-Market Adjustments

| Parameter | US | HK | A |
|-----------|-----|-----|---|
| Entry threshold (bear) | Higher (fewer, higher quality) | Medium | Lower (weaker signals) |
| Pullback fib (bull) | Yes | Yes | Yes |
| VP divergence | No | No | **Yes** (IC = +0.046) |
| ma_golden_cross | Remove from scoring | **Flip to sell signal** | Remove |
| Trail stop % | 12% | 10% | 8% (higher volatility) |

---

## 3. Expected Impact

### Quantitative Estimates (from empirical data)

| Change | Estimated Sharpe impact | Confidence |
|--------|------------------------|------------|
| Remove P3 from bear scoring | +0.02–0.04 | High (negative IC proven) |
| Remove ma_golden_cross (or flip) | +0.02–0.03 | High (HK especially) |
| Regime-separate P1/P4 | +0.05–0.10 | Medium (based on mode simulation) |
| Fib bull-only | +0.03–0.05 | High (41.5% vs 6.7% proven) |
| Continuous intensity signals | +0.02–0.04 | Medium (IC improvement measured) |
| Remove z-score (raw thresholds) | +0.01–0.03 | Medium (27% IC recovery) |
| Remove L2/L3 from stock scoring | +0.01–0.02 | Medium (near-zero IC) |
| Trail stop + time exit | +0.05–0.10 | Medium (no backtest yet, structural argument) |
| Portfolio-level macro sizing | TBD | Low (needs implementation) |

**Conservative total estimate: +0.15–0.25 Sharpe improvement**, taking current 0.35–0.38 to 0.50–0.63 range.

### What We're NOT Changing

- DZH indicators (golden_pit, nine_turns, band_king) — they work, keep them
- Chain resonance (P2) — high IC, rare, keeps its role
- Volume anomaly concept — works, just making it continuous
- Precomputation architecture — efficient, no reason to change
- Data pipeline / cache system — production-proven

---

## 4. Implementation Phases

### Phase 1: Strip What Hurts (1 day, immediate Sharpe lift)
- Remove `ma_golden_cross` from P4 scoring (or flip sign for HK)
- Add bull-regime gate to P3 Fibonacci (`if bull and fib_support → score`)
- Remove L2 fundamentals from composite (set to neutral)
- Backtest → expect +0.05–0.08 vs current

### Phase 2: Regime Separation (2–3 days)
- Create `score_bar_v5()` with dual-mode architecture
- Bear mode: P1 + P2 + L1 only
- Bull mode: P4 for hold, P1 + Fib for pullback buy
- Separate entry thresholds per mode (no z-score)
- Backtest → expect additional +0.05–0.10

### Phase 3: Continuous Signals (1–2 days)
- Replace binary KDJ/BB/volume with continuous variants
- Add RSI depth (new signal)
- Add VP divergence for A-stocks
- Recalibrate entry thresholds
- Backtest → expect additional +0.02–0.04

### Phase 4: Position Management (2 days)
- Implement trailing stop logic in backtest
- Add time-based exit tightening
- Add regime-shift exit rules
- Backtest → expect additional +0.05–0.10

### Phase 5: Portfolio-Level Macro (1 day)
- Extract macro to position sizing layer
- VIX/QVIX regime → max exposure limits
- Remove L3 macro from per-stock scoring entirely

---

## 5. Risk Considerations

- **Overfitting**: All analysis is on 2021–2026 in-sample. Phase 2+ should include walk-forward validation (train 2021–2024, test 2025–2026).
- **Regime transition whipsaw**: MA200 cross can generate false signals. Consider requiring N consecutive closes above/below MA200 before regime switch.
- **Reduced signal count in bear**: Bear mode uses fewer signals → fewer BUY entries → higher concentration risk. Acceptable if per-trade quality improves.
- **A-stock alpha**: A-stocks consistently show weakest signal quality across all architectures. May need fundamentally different approach (cross-sectional ranking) as a separate Phase 6.
