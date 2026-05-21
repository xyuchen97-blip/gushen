#!/usr/bin/env python3
"""
Factor Correlation Matrix — Gushen v10 (originally v9.4+)
========================================

Computes pairwise correlation of signal fire rates across the 21-stock universe.
Flags signal pairs with |r| > 0.7 as potentially redundant (multicollinearity risk).

Methodology:
  For each stock, record which signals fire on each weekly bar (binary vector).
  Compute per-stock fire rate = (# bars fired) / total_bars.
  Correlate signal fire rates across stocks (N=21 observations per pair, N=8 for A-only).

Usage:
  GUSHEN_TUNE=1 python3 strategy/correlation_matrix.py
"""

import os, sys, warnings
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

GUSHEN = Path(os.environ.get("GUSHEN_HOME", "/Users/alafat/.workbuddy/skills/gushen"))
sys.path.insert(0, str(GUSHEN))

from strategy.scoring import precompute, score_bar_v5, SIGNAL_SCORES
from strategy.data_fetcher import fetch_macro_data
from strategy.gushen_cache import get_ohlcv, get_chip_concentration, get_holder_chg

# ── Config ──
CORR_THRESHOLD = 0.7
START_DATE = "2021-01-01"
END_DATE = "2026-05-06"

UNIVERSE = [
    ("600519.SH", "茅台", "A"), ("000858.SZ", "五粮液", "A"),
    ("300750.SZ", "宁德时代", "A"), ("002594.SZ", "比亚迪", "A"),
    ("601318.SH", "平安", "A"), ("600036.SH", "招行", "A"),
    ("002230.SZ", "科大讯飞", "A"), ("300015.SZ", "爱尔眼科", "A"),
    ("0700.HK", "腾讯", "HK"), ("9988.HK", "阿里", "HK"),
    ("3690.HK", "美团", "HK"), ("1810.HK", "小米", "HK"),
    ("1211.HK", "比亚迪", "HK"), ("0388.HK", "港交所", "HK"),
    ("AAPL", "苹果", "US"), ("NVDA", "英伟达", "US"),
    ("MSFT", "微软", "US"), ("GOOGL", "谷歌", "US"),
    ("AMZN", "亚马逊", "US"), ("META", "Meta", "US"),
    ("JPM", "摩根大通", "US"),
]

SIGNAL_LIST = [
    # Technical — Contrarian
    "golden_pit", "band_low", "nine_turns_buy", "nine_turns_setup9", "band_king_buy2",
    # Technical — Trend
    "ma_aligned", "price_above_ma50", "adx_trend", "bb_weekly_buy",
    "ma_golden_cross", "macd_golden",
    # Technical — Momentum
    "kdj_golden", "kdj_oversold", "bullish_divergence",
    # Technical — Combo
    "fib_divergence_combo", "fib_kdj_combo", "boll_kdj_chain", "boll_kdj_macd_chain",
    # Capital
    "volume_anomaly", "northbound_inflow",
    # Capital — A-stock only
    "chip_tight", "holder_consolidate", "mff_strong", "triple_confirm",
]


def fetch_data(code, market):
    """Fetch OHLCV for a stock (cache → akshare → yfinance)."""
    df = get_ohlcv(code, market)
    if df is None or len(df) < 50:
        import yfinance as yf
        ticker = code.replace(".SH", ".SS").replace(".SZ", ".SZ") if market == "A" else code
        df = yf.download(ticker, start="2021-01-01", end="2026-05-06", progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(df.columns.levels[-1][0], axis=1, level=-1)
        m = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        df = df.rename(columns={k: v for k, v in m.items() if k in df.columns})
        df = df[[c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]]
        df.index = pd.to_datetime(df.index)
    return df.sort_index()


def get_fire_rates(df_daily, macro_data, market, code):
    """Collect per-stock signal fire rates from weekly bar scoring."""
    dfw = df_daily.resample("W-FRI").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()

    fire_counts = {sig: 0 for sig in SIGNAL_LIST}
    total_bars = 0

    for i in range(50, len(dfw)):
        wk = dfw.index[i]
        di = df_daily.index.get_indexer([wk], method="ffill")[0]
        if di < 252:
            continue
        try:
            pc = precompute(df_daily.iloc[:di + 1], dfw.iloc[:i + 1])
            r = score_bar_v5(di, df_daily.iloc[:di + 1], pc, macro_data=macro_data, market=market)
            total_bars += 1
            active = r.get("active", [])

            # Map active signals to our signal list
            for sig in SIGNAL_LIST:
                if sig in active:
                    fire_counts[sig] += 1
        except Exception:
            continue

    if total_bars == 0:
        return {sig: 0.0 for sig in SIGNAL_LIST}

    return {sig: fire_counts[sig] / total_bars for sig in SIGNAL_LIST}


def main():
    print("\n  Factor Correlation Matrix (Gushen v9.4+)")
    print("  " + "=" * 56)
    print(f"  Universe: {len(UNIVERSE)} stocks (8 A + 6 HK + 7 US)")
    print(f"  Signals: {len(SIGNAL_LIST)} tracked")
    print(f"  Threshold: |r| > {CORR_THRESHOLD}")
    print(f"  Period: {START_DATE} → {END_DATE}\n")

    macro_data = fetch_macro_data(START_DATE, END_DATE)

    # Step 1: Collect per-stock fire rates
    fire_rates = {}  # signal → [rate_stock1, rate_stock2, ...]
    for sig in SIGNAL_LIST:
        fire_rates[sig] = []

    stock_names = []

    for code, name, market in UNIVERSE:
        short = name
        stock_names.append(short)
        print(f"  Scoring {code:<12} ({short:<6} {market})...", end=" ", flush=True)

        try:
            df = fetch_data(code, market)
            m2 = dict(macro_data)
            if market == "A":
                try:
                    m2["chip_conc"] = get_chip_concentration(code)
                    m2["holder_chg"] = get_holder_chg(code)
                except Exception:
                    pass
            rates = get_fire_rates(df, m2, market, code)
            for sig in SIGNAL_LIST:
                fire_rates[sig].append(rates[sig])
            total = sum(1 for v in rates.values() if v > 0)
            print(f"{total} active signals")
        except Exception as e:
            print(f"ERROR: {e}")
            for sig in SIGNAL_LIST:
                fire_rates[sig].append(0.0)

    # Step 2: Filter to signals that fire on at least 2 stocks
    active_signals = [sig for sig in SIGNAL_LIST if sum(1 for v in fire_rates[sig] if v > 0) >= 2]
    print(f"\n  Active signals (>0 rate on ≥2 stocks): {len(active_signals)}/{len(SIGNAL_LIST)}")

    # Step 3: Build correlation matrix
    n = len(active_signals)
    corr_matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            si, sj = active_signals[i], active_signals[j]
            ri = np.array(fire_rates[si])
            rj = np.array(fire_rates[sj])
            valid = ~(np.isnan(ri) | np.isnan(rj))
            if valid.sum() >= 3:
                corr_matrix[i, j] = np.corrcoef(ri[valid], rj[valid])[0, 1]

    # Step 4: Flag high-correlation pairs
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            r = corr_matrix[i, j]
            if abs(r) > CORR_THRESHOLD:
                pairs.append((active_signals[i], active_signals[j], r))

    # Step 5: Output
    print(f"\n  {'='*70}")
    print(f"  Correlation Results (|r| > {CORR_THRESHOLD})")
    print(f"  {'='*70}")

    if pairs:
        pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        print(f"  {'Signal A':<25} {'Signal B':<25} {'r':>8}  {'Flag'}")
        print(f"  {'-'*70}")
        for a, b, r in pairs:
            flag = "HIGH" if abs(r) > 0.8 else "WARN"
            print(f"  {a:<25} {b:<25} {r:>+8.3f}  {flag}")

        # Domain analysis
        ownership_pairs = [p for p in pairs if "chip" in p[0].lower() or "holder" in p[0].lower()
                          or "chip" in p[1].lower() or "holder" in p[1].lower()]
        trend_pairs = [p for p in pairs if "ma_" in p[0] or "adx" in p[0] or "ma_" in p[1] or "adx" in p[1]]
        combo_pairs = [p for p in pairs if "combo" in p[0] or "chain" in p[0] or "confirm" in p[0]
                       or "combo" in p[1] or "chain" in p[1] or "confirm" in p[1]]

        print(f"\n  Analysis:")
        print(f"    Ownership pairs (chip/holder): {len(ownership_pairs)}")
        print(f"    Trend pairs (MA/ADX): {len(trend_pairs)}")
        print(f"    Combo pairs (resonance/confirm): {len(combo_pairs)}")

        if ownership_pairs:
            print(f"    → Consider merging chip_tight + holder_consolidate into ownership_factor")
        if combo_pairs:
            print(f"    → Combo signals naturally correlate with constituents — expected")
        if trend_pairs:
            print(f"    → Trend signals overlap; consider reducing weights or picking best IC")

        print(f"\n  ★ {len(pairs)} pairs exceed r={CORR_THRESHOLD}")
    else:
        print(f"  ★ No pairs exceed r={CORR_THRESHOLD} — all signals reasonably independent")

    # Save full matrix
    result = {
        "date": str(datetime.now())[:10],
        "threshold": CORR_THRESHOLD,
        "n_stocks": len(UNIVERSE),
        "n_signals": len(active_signals),
        "high_corr_pairs": [{"a": a, "b": b, "r": round(float(r), 3)} for a, b, r in pairs],
        "full_matrix": {active_signals[i]: {active_signals[j]: round(float(corr_matrix[i, j]), 3)
                       for j in range(n)} for i in range(n)},
    }
    csv_path = GUSHEN / f"data/factor_correlation_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    with open(csv_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  Full matrix saved to {csv_path}")


if __name__ == "__main__":
    main()
