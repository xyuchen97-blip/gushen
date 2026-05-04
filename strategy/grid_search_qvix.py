#!/usr/bin/env python3
"""
QVIX Threshold Grid Search — Optimizes China QVIX factor for A-share scoring.

Two-stage optimization:
  Stage 1: Find best percentile thresholds (80 combos)
  Stage 2: Fix thresholds, find best scores (27 combos)

Total: ~107 combos × 3 stocks × 500 bars = ~160k scoring ops

Strategy: This module is self-contained — imports the scoring engine directly
and monkey-patches QVIX_THRESHOLDS to test each combination.

Output: Writes optimized thresholds back to scoring.py.
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.data_fetcher import fetch_ohlcv, fetch_macro_data, clear_cache
from strategy import scoring


# ── Configuration ──────────────────────────────────────────────────

BENCHMARK_STOCKS = [
    ("600519", "A", "茅台"),
    ("000858", "A", "五粮液"),
    ("300750", "A", "宁德时代"),
]

START_DATE = "2018-01-01"
END_DATE   = datetime.now().strftime("%Y-%m-%d")

# Stage 1: percentile thresholds
PCT_VERY_LOW = [10, 15, 20, 25]
PCT_LOW      = [25, 30, 35, 40]
PCT_HIGH     = [70, 75, 80, 85, 90]

# Stage 2: scores (with best thresholds fixed)
SCORE_VERY_LOW = [1, 2, 3]
SCORE_LOW      = [2, 3, 4]
SCORE_HIGH     = [-2, -3, -5]


# ── Metrics ───────────────────────────────────────────────────────

def compute_metrics(scores: pd.DataFrame, prices: pd.DataFrame) -> dict:
    """
    Compute signal quality metrics for a set of scores across stocks.
    
    scores: DataFrame indexed by (date, ticker) with column 'composite'
    prices: DataFrame indexed by (date, ticker) with column 'close'
    
    Returns dict with: corr_20d, sharpe_buy, win_rate, buy_count
    """
    # Forward 20-day returns
    fwd_ret = prices.groupby(level=1)["close"].transform(
        lambda s: s.shift(-20) / s - 1
    )
    
    # Merge
    merged = scores.join(fwd_ret.rename("fwd_20d"), how="inner").dropna()
    
    if len(merged) < 50:
        return {"corr_20d": 0, "sharpe_buy": 0, "win_rate": 0, "buy_count": 0, "total_bars": len(merged)}
    
    # Correlation
    corr = merged["composite"].corr(merged["fwd_20d"])
    
    # BUY signal performance
    buys = merged[merged["action"] == "BUY"]
    if len(buys) < 10:
        return {"corr_20d": corr, "sharpe_buy": 0, "win_rate": 0, "buy_count": 0, "total_bars": len(merged)}
    
    buy_rets = buys["fwd_20d"]
    sharpe = buy_rets.mean() / buy_rets.std() if buy_rets.std() > 0 else 0
    win_rate = (buy_rets > 0).mean()
    
    return {
        "corr_20d":    round(corr, 4),
        "sharpe_buy":  round(sharpe, 4),
        "win_rate":    round(win_rate, 4),
        "buy_count":   len(buys),
        "total_bars":  len(merged),
    }


# ── Data Loading (once) ──────────────────────────────────────────

def load_data():
    """Load benchmark data once. Returns (daily_dfs, weekly_dfs, macro_data)."""
    print("Loading benchmark data...")
    daily_dfs = {}
    weekly_dfs = {}
    
    macro = fetch_macro_data(START_DATE, END_DATE)
    print(f"  Macro: {len(macro)} indicators loaded")
    
    for ticker, market, name in BENCHMARK_STOCKS:
        clear_cache()
        d = fetch_ohlcv(ticker, market, START_DATE, END_DATE, "daily")
        w = fetch_ohlcv(ticker, market, START_DATE, END_DATE, "weekly")
        daily_dfs[ticker] = d
        weekly_dfs[ticker] = w
        print(f"  {name} ({ticker}): {len(d)} daily, {len(w)} weekly bars")
    
    return daily_dfs, weekly_dfs, macro


# ── Scoring Loop ──────────────────────────────────────────────────

def score_stocks(daily_dfs, weekly_dfs, macro, thresholds, macro_scores_override=None):
    """
    Score all benchmark stocks with given thresholds.
    
    Returns DataFrame with columns: date, ticker, composite, action
    """
    # Monkey-patch thresholds
    orig_thresholds = scoring.QVIX_THRESHOLDS.copy()
    scoring.QVIX_THRESHOLDS = thresholds.copy()
    
    orig_macro_scores = None
    if macro_scores_override:
        orig_macro_scores = {
            "china_qvix_very_low": scoring.MACRO_SCORES["china_qvix_very_low"],
            "china_qvix_low":      scoring.MACRO_SCORES["china_qvix_low"],
            "china_qvix_high":     scoring.MACRO_SCORES["china_qvix_high"],
        }
        scoring.MACRO_SCORES.update(macro_scores_override)
    
    results = []
    for ticker, market, name in BENCHMARK_STOCKS:
        df_d = daily_dfs[ticker]
        df_w = weekly_dfs[ticker]
        
        if macro is None:
            m = fetch_macro_data(START_DATE, END_DATE)
        else:
            m = macro
        
        precomputed = scoring.precompute(df_d, df_w)
        
        # Sample every 5 bars to speed up (still statistically valid)
        for i in range(100, len(df_d), 5):
            r = scoring.score_bar(i, df_d, precomputed, m, market=market)
            results.append({
                "date":   df_d.index[i],
                "ticker": ticker,
                "composite": r["composite"],
                "action":  r["action"],
                "close":   float(df_d["close"].iloc[i]),
            })
    
    # Restore
    scoring.QVIX_THRESHOLDS = orig_thresholds
    if macro_scores_override and orig_macro_scores:
        scoring.MACRO_SCORES.update(orig_macro_scores)
    
    df = pd.DataFrame(results)
    df = df.set_index(["date", "ticker"]).sort_index()
    return df


# ── Stage 1: Threshold Optimization ──────────────────────────────

def stage1_thresholds(daily_dfs, weekly_dfs, macro):
    """Grid search for best percentile thresholds."""
    print("\n" + "=" * 60)
    print("STAGE 1: Threshold Percentile Optimization")
    print("=" * 60)
    
    # Load QVIX distribution for percentile mapping
    qvix = macro.get("china_qvix")
    if qvix is None or qvix.empty:
        print("ERROR: China QVIX data not available")
        return None
    
    combos = list(product(PCT_VERY_LOW, PCT_LOW, PCT_HIGH))
    print(f"Testing {len(combos)} threshold combos...")
    
    best = None
    best_score = -999
    
    for idx, (vl_pct, l_pct, h_pct) in enumerate(combos):
        vl = np.percentile(qvix.dropna(), vl_pct)
        l  = np.percentile(qvix.dropna(), l_pct)
        h  = np.percentile(qvix.dropna(), h_pct)
        
        if vl >= l or l >= h:
            continue  # invalid: thresholds must be monotonic
        
        thresholds = {"very_low": round(vl, 1), "low": round(l, 1), "high": round(h, 1)}
        df = score_stocks(daily_dfs, weekly_dfs, macro, thresholds)
        prices = df[["close"]]
        scores = df[["composite", "action"]]
        metrics = compute_metrics(scores, prices)
        
        # Composite metric: 0.6 * corr + 0.4 * sharpe
        composite = 0.6 * metrics["corr_20d"] + 0.4 * metrics["sharpe_buy"]
        
        if composite > best_score:
            best_score = composite
            best = {"thresholds": thresholds, "metrics": metrics, "pcts": (vl_pct, l_pct, h_pct)}
            print(f"  [{idx+1}/{len(combos)}] ★ P{vl_pct}/P{l_pct}/P{h_pct} → "
                  f"({vl:.1f}/{l:.1f}/{h:.1f}): "
                  f"corr={metrics['corr_20d']:.3f} sharpe={metrics['sharpe_buy']:.3f} "
                  f"win={metrics['win_rate']:.1%} composite={composite:.4f}")
    
    print(f"\n  Best thresholds: {best['thresholds']}")
    print(f"  Metrics: corr={best['metrics']['corr_20d']} sharpe={best['metrics']['sharpe_buy']} "
          f"win_rate={best['metrics']['win_rate']}")
    return best


# ── Stage 2: Score Optimization ──────────────────────────────────

def stage2_scores(daily_dfs, weekly_dfs, macro, best_thresholds):
    """Grid search for best QVIX score values."""
    print("\n" + "=" * 60)
    print("STAGE 2: Score Value Optimization")
    print("=" * 60)
    
    combos = list(product(SCORE_VERY_LOW, SCORE_LOW, SCORE_HIGH))
    print(f"Testing {len(combos)} score combos with thresholds={best_thresholds}...")
    
    best = None
    best_score = -999
    
    for idx, (svl, sl, sh) in enumerate(combos):
        scores_override = {
            "china_qvix_very_low": svl,
            "china_qvix_low":      sl,
            "china_qvix_high":     sh,
        }
        
        df = score_stocks(daily_dfs, weekly_dfs, macro, best_thresholds, scores_override)
        prices = df[["close"]]
        sc = df[["composite", "action"]]
        metrics = compute_metrics(sc, prices)
        
        composite = 0.6 * metrics["corr_20d"] + 0.4 * metrics["sharpe_buy"]
        
        if composite > best_score:
            best_score = composite
            best = {"scores": scores_override, "metrics": metrics}
            print(f"  [{idx+1}/{len(combos)}] ★ very_low={svl} low={sl} high={sh}: "
                  f"corr={metrics['corr_20d']:.3f} sharpe={metrics['sharpe_buy']:.3f} "
                  f"win={metrics['win_rate']:.1%} composite={composite:.4f}")
    
    print(f"\n  Best scores: {best['scores']}")
    print(f"  Metrics: corr={best['metrics']['corr_20d']} sharpe={best['metrics']['sharpe_buy']} "
          f"win_rate={best['metrics']['win_rate']}")
    return best


# ── Write Results ─────────────────────────────────────────────────

def apply_results(best_thresholds, best_scores):
    """Write optimized thresholds and scores back to scoring.py."""
    scoring_path = Path(__file__).parent / "scoring.py"
    content = scoring_path.read_text()
    
    # Replace QVIX_THRESHOLDS
    old_block_start = "QVIX_THRESHOLDS = {"
    new_block = f"QVIX_THRESHOLDS = {{\n    \"very_low\": {best_thresholds['very_low']:.1f},   # ~P15 — extremely low volatility\n    \"low\":      {best_thresholds['low']:.1f},   # ~P35 — below median volatility\n    \"high\":     {best_thresholds['high']:.1f},   # ~P75 — above-normal fear\n}}"
    
    # Find and replace in scoring.py
    lines = content.split("\n")
    new_lines = []
    in_qvix_block = False
    for line in lines:
        if line.strip().startswith("QVIX_THRESHOLDS"):
            new_lines.append(new_block)
            in_qvix_block = True
        elif in_qvix_block:
            if line.strip().startswith("BB_PARAMS") or line.strip().startswith("MACD_PARAMS"):
                new_lines.append(line)
                in_qvix_block = False
            # skip QVIX lines
        else:
            new_lines.append(line)
    
    scoring_path.write_text("\n".join(new_lines))
    
    # Replace MACRO_SCORES QVIX entries
    content2 = scoring_path.read_text()
    content2 = content2.replace(
        '"china_qvix_very_low": 3,',
        f'"china_qvix_very_low": {best_scores["china_qvix_very_low"]},'
    )
    content2 = content2.replace(
        '"china_qvix_low":      2,',
        f'"china_qvix_low":      {best_scores["china_qvix_low"]},'
    )
    content2 = content2.replace(
        '"china_qvix_high":    -3,',
        f'"china_qvix_high":     {best_scores["china_qvix_high"]},'
    )
    scoring_path.write_text(content2)
    
    print(f"\n✅ Optimized thresholds and scores written to {scoring_path}")


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("QVIX Grid Search — v8.0 Optimization")
    print(f"Benchmark: {len(BENCHMARK_STOCKS)} stocks, {START_DATE} → {END_DATE}")
    
    # Load data once
    daily, weekly, macro = load_data()
    
    # Stage 1: Thresholds
    result1 = stage1_thresholds(daily, weekly, macro)
    if result1 is None:
        print("Stage 1 failed.")
        sys.exit(1)
    
    # Stage 2: Scores
    result2 = stage2_scores(daily, weekly, macro, result1["thresholds"])
    if result2 is None:
        print("Stage 2 failed.")
        sys.exit(1)
    
    # Apply
    apply_results(result1["thresholds"], result2["scores"])
    
    print("\n" + "=" * 60)
    print("GRID SEARCH COMPLETE")
    print(f"Thresholds: very_low={result1['thresholds']['very_low']:.1f} "
          f"low={result1['thresholds']['low']:.1f} "
          f"high={result1['thresholds']['high']:.1f}")
    print(f"Scores:     very_low=+{result2['scores']['china_qvix_very_low']} "
          f"low=+{result2['scores']['china_qvix_low']} "
          f"high={result2['scores']['china_qvix_high']}")
    print(f"Final corr_20d: {result2['metrics']['corr_20d']:.4f}")
    print(f"Final sharpe_buy: {result2['metrics']['sharpe_buy']:.4f}")
