#!/usr/bin/env python3
"""
Data Source Sensitivity Test — Gushen v10 (originally v9.4+)
===========================================

Runs the scoring engine on 4 representative US stocks, fetching OHLCV from
three sources (akshare, Alpha Vantage, yfinance), then compares Sharpe ratios.

Threshold: |Δ Sharpe| < 0.3 for PASS — if larger, signal may be fitting to
data artifacts rather than real market structure.

Usage:
    python3 strategy/sensitivity_test.py
    ALPHA_VANTAGE_KEY=xxx python3 strategy/sensitivity_test.py
"""

import os, sys, json, warnings
import numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

GUSHEN = Path(os.environ.get("GUSHEN_HOME", "/Users/alafat/.workbuddy/skills/gushen"))
sys.path.insert(0, str(GUSHEN))

from strategy.scoring import precompute, score_bar_v5
from strategy.data_fetcher import fetch_macro_data

# ── Config ──
DELTA_THRESHOLD = 0.3
START_DATE = "2021-01-01"
END_DATE = "2026-05-06"
US_TICKERS = ["AAPL", "NVDA", "MSFT", "JPM"]
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")


def fetch_akshare_us(ticker):
    """Fetch US OHLCV from akshare (qfq-adjusted)."""
    try:
        import akshare as ak
        df = ak.stock_us_daily(symbol=ticker, adjust="qfq")
        if df is None or len(df) < 50:
            return None
        df = df.rename(columns={
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume"
        })
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
        else:
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols].loc[START_DATE:END_DATE]
    except Exception as e:
        print(f"    akshare failed: {e}")
        return None


def fetch_alphavantage_us(ticker):
    """Fetch US OHLCV from Alpha Vantage (adjusted close)."""
    if not ALPHA_VANTAGE_KEY:
        return None
    try:
        import requests
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": ticker,
            "outputsize": "compact",
            "apikey": ALPHA_VANTAGE_KEY,
            "datatype": "json",
        }
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        ts = data.get("Time Series (Daily)", {})
        if not ts or "Error Message" in data:
            print(f"    Alpha Vantage error: {data.get('Error Message', data.get('Note', 'unknown'))}")
            return None
        rows = []
        for d, v in ts.items():
            rows.append({
                "date": d, "open": float(v["1. open"]), "high": float(v["2. high"]),
                "low": float(v["3. low"]), "close": float(v["5. adjusted close"]),
                "volume": float(v["6. volume"]),
            })
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        return df.loc[START_DATE:END_DATE]
    except Exception as e:
        print(f"    Alpha Vantage failed: {e}")
        return None


def fetch_yfinance_us(ticker):
    """Fetch US OHLCV from yfinance (adjusted, old baseline)."""
    try:
        import yfinance as yf
        df = yf.download(ticker, start=START_DATE, end=END_DATE, progress=False, auto_adjust=True)
        if len(df) < 50:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(df.columns.levels[-1][0], axis=1, level=-1)
        m = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        df = df.rename(columns={k: v for k, v in m.items() if k in df.columns})
        df = df[[c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]]
        df.index = pd.to_datetime(df.index)
        return df.sort_index()
    except Exception as e:
        print(f"    yfinance failed: {e}")
        return None


def run_backtest(df_daily, macro_data, market="US"):
    """Run Gushen backtest on a DataFrame. Returns annualized Sharpe & trade count."""
    dfw = df_daily.resample("W-FRI").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()

    buys = []
    for i in range(50, len(dfw) - 1):
        wk = dfw.index[i]
        di = df_daily.index.get_indexer([wk], method="ffill")[0]
        if di < 252:
            continue
        try:
            pc = precompute(df_daily.iloc[:di + 1], dfw.iloc[:i + 1])
            r = score_bar_v5(di, df_daily.iloc[:di + 1], pc, macro_data=macro_data, market=market)
        except Exception:
            continue
        if r["action"] == "BUY":
            ret = (dfw["close"].iloc[i + 1] / dfw["close"].iloc[i]) - 1
            buys.append(ret)

    bu = np.array(buys) if buys else np.zeros(1)
    sharpe = round(float(np.sqrt(52) * bu.mean() / bu.std()), 3) if len(bu) >= 3 and bu.std() > 0 else 0
    return sharpe, len(bu)


def main():
    print("\n  Data Source Sensitivity Test (Gushen v9.4+)")
    print("  " + "=" * 56)
    print(f"  Threshold: |Delta Sharpe| < {DELTA_THRESHOLD}")
    print(f"  Period: {START_DATE} → {END_DATE}\n")

    macro_data = fetch_macro_data(START_DATE, END_DATE)

    sources = [
        ("akshare", fetch_akshare_us),
        ("AlphaVantage", fetch_alphavantage_us),
        ("yfinance", fetch_yfinance_us),
    ]

    all_results = []
    for ticker in US_TICKERS:
        print(f"  {ticker}...")
        row = {"ticker": ticker}
        sharpe_vals = {}

        for src_name, fetch_fn in sources:
            print(f"    Fetching {src_name}...", end=" ", flush=True)
            df = fetch_fn(ticker)
            if df is None or len(df) < 100:
                print(f"SKIP (insufficient data: {len(df) if df is not None else 0} bars)")
                row[f"S_{src_name}"] = None
                continue
            print(f"{len(df)} bars", end=" ", flush=True)
            s, n = run_backtest(df, dict(macro_data), "US")
            print(f"S={s} Trades={n}")
            row[f"S_{src_name}"] = s
            row[f"Trades_{src_name}"] = n
            sharpe_vals[src_name] = s

        all_results.append(row)

        # Pairwise deltas
        names = [n for n, _ in sources if row.get(f"S_{n}") is not None]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                sa, sb = sharpe_vals.get(a), sharpe_vals.get(b)
                if sa is not None and sb is not None:
                    delta = abs(sa - sb)
                    status = "PASS" if delta < DELTA_THRESHOLD else "FAIL"
                    print(f"      Delta({a} vs {b}) = {delta:.3f}  {status}")

    # ── Summary ──
    print("\n  Summary:")
    header = f"  {'Ticker':<8}"
    for name, _ in sources:
        header += f" {'S_'+name:>12}"
    header += "  Result"
    print(header)
    print("  " + "-" * len(header))

    for row in all_results:
        line = f"  {row['ticker']:<8}"
        for name, _ in sources:
            s = row.get(f"S_{name}")
            line += f" {s:>12.3f}" if s is not None else f" {'N/A':>12}"
        # Determine overall pass/fail
        fails = []
        names = [n for n, _ in sources]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                sa = row.get(f"S_{names[i]}")
                sb = row.get(f"S_{names[j]}")
                if sa is not None and sb is not None and abs(sa - sb) >= DELTA_THRESHOLD:
                    fails.append(f"{names[i]}-{names[j]}")
        line += f"  {'PASS' if not fails else 'FAIL(' + ','.join(fails) + ')'}"
        print(line)

    # Overall verdict
    total_fails = sum(
        1 for row in all_results
        for i in range(len([n for n, _ in sources]))
        for j in range(i + 1, len([n for n, _ in sources]))
        if row.get(f"S_{[n for n, _ in sources][i]}") is not None
        and row.get(f"S_{[n for n, _ in sources][j]}") is not None
        and abs(row[f"S_{[n for n, _ in sources][i]}"] - row[f"S_{[n for n, _ in sources][j]}"]) >= DELTA_THRESHOLD
    )

    print(f"\n  {'★ ALL PASS' if total_fails == 0 else '⚠ ' + str(total_fails) + ' delta(s) exceed threshold'}")
    print(f"  {'✓ US data is source-stable' if total_fails == 0 else '⚠ US signal may be fitting to data artifacts'}")

    # Save results
    snap = {
        "date": str(datetime.now())[:10],
        "threshold": DELTA_THRESHOLD,
        "results": [{k: v for k, v in r.items() if not isinstance(v, (np.floating,))} for r in all_results],
    }
    snap_path = GUSHEN / f"data/sensitivity_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    with open(snap_path, "w") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"  Results saved to {snap_path}")


if __name__ == "__main__":
    main()
