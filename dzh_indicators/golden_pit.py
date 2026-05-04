"""
Golden Pit 2.0 — DZH Classic Indicator Python Implementation
============================================================

Source: DZH (DaZhiHui) Golden Pit 2.0 sub-chart indicator formula
URL: https://zbgs3.com/91826.html

**Core Logic**:
1. Golden Pit signal: When price deviates more than -10% from the 27-period MA
   and the oversold condition persists for more than 3 bars, indicating a potential
   reversal zone after deep overselling (yellow histogram).
2. Band Low signal: When the short-term momentum indicator (RSI-like) drops below 15,
   indicating a band bottom zone.

**No future functions** — all calculations use only historical data.
Backtest signals will not drift.

Original formula:
    Var1 := (CLOSE-LLV(LOW,35))/(HHV(HIGH,35)-LLV(LOW,35))*100;
    Var2 := SMA(Var1,3,1);  Var3 := SMA(Var2,3,1);  Var4 := SMA(Var3,3,1);
    VAR8 := MA(CLOSE,27);
    VAR9 := (CLOSE-VAR8)/VAR8*100;
    VARA := MA(VAR9,2);
    VARB := BARSLAST(CROSS(-10,VARA)=1);
    VARD := VARA<-10 AND VARB>3;
    GoldenPit := IF(VARD, VARA, 0);
    BandLow := IF(a48<15, 19, 0);    // a48 = 7-bar RSI-like

Usage:
    import akshare as ak
    from dzh_indicators.golden_pit import compute

    df = ak.stock_zh_a_hist(symbol="000001", period="daily", adjust="qfq")
    df = compute(df)
    # New columns: golden_pit, band_low, curve_h1, curve_h2, curve_h3,
    #               dev_pct_27ma, dev_pct_2ma
"""

import numpy as np
import pandas as pd


# ── DZH Built-in Function Equivalents ─────────────────────────────────

def _sma(series: pd.Series, n: int, m: float) -> np.ndarray:
    """
    DZH SMA(X,N,M): Weighted moving average.
    SMA[i] = (X[i]*M + SMA[i-1]*(N-M)) / N
    First value = X[0]
    """
    result = np.zeros(len(series), dtype=float)
    result[0] = float(series.iloc[0])
    for i in range(1, len(series)):
        result[i] = (float(series.iloc[i]) * m + result[i - 1] * (n - m)) / n
    return result


def _barslast(condition: pd.Series) -> pd.Series:
    """
    DZH BARSLAST(X): Bars since condition was last true.
    Returns 0 on the bar where condition is true, then counts up.
    """
    result = pd.Series(0, index=condition.index, dtype=int)
    last_true_idx = -1
    for i in range(len(condition)):
        if condition.iloc[i]:
            result.iloc[i] = 0
            last_true_idx = i
        elif last_true_idx >= 0:
            result.iloc[i] = i - last_true_idx
        else:
            result.iloc[i] = -1  # never triggered
    return result


def _cross(a, b) -> pd.Series:
    """
    DZH CROSS(A,B): A crosses above B from below.
    For CROSS(-10, VARA): -10 crosses above VARA -> VARA drops below -10.
    """
    a = pd.Series(a, index=b.index) if isinstance(a, (int, float)) else a
    prev_a_lt_b = a.shift(1) < b.shift(1)
    curr_a_ge_b = a >= b
    result = prev_a_lt_b & curr_a_ge_b
    result.iloc[0] = False
    return result


# ── Main Computation ──────────────────────────────────────────────────

def compute(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all Golden Pit 2.0 indicator values.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: open, high, low, close
        Date index required.

    Returns
    -------
    pd.DataFrame (original df with appended columns):
        curve_h1, curve_h2, curve_h3   — triple-smoothed price position curves
        dev_pct_27ma                    — close deviation from 27-period MA in %
        dev_pct_2ma                     — 2-period MA of deviation
        golden_pit                      — Golden Pit signal value (nonzero = triggered)
        rsi_7                           — 7-bar RSI-like momentum value
        band_low                        — Band Low signal (nonzero = triggered)
    """
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # ── Var1: 35-bar price position percentage ────────────────────
    llv_low_35 = low.rolling(35).min()
    hhv_high_35 = high.rolling(35).max()
    var1 = (close - llv_low_35) / (hhv_high_35 - llv_low_35) * 100

    # ── Var2-4: Triple SMA(3,1) smoothing ─────────────────────────
    var2_arr = _sma(pd.Series(var1, index=df.index), 3, 1)
    var3_arr = _sma(pd.Series(var2_arr, index=df.index), 3, 1)
    var4_arr = _sma(pd.Series(var3_arr, index=df.index), 3, 1)
    df["curve_h1"] = var3_arr   # green curve
    df["curve_h2"] = var4_arr   # further smoothed

    # ── DD/H3: 30-bar range EMA-smoothed price strength ───────────
    aa = low.rolling(30).min()
    bb = high.rolling(30).max()
    dd_raw = (close - aa) / (bb - aa) * 4
    dd_ema = dd_raw.ewm(span=4, adjust=False).mean()
    df["curve_h3"] = dd_ema * 25  # blue curve

    # ── Deviation from 27-period MA ───────────────────────────────
    ma27 = close.rolling(27).mean()
    var9 = (close - ma27) / ma27 * 100   # deviation %
    df["dev_pct_27ma"] = var9
    vara = var9.rolling(2).mean()         # 2-bar MA of deviation
    df["dev_pct_2ma"] = vara

    # ── VARB: bars since VARA last crossed below -10 ─────────────
    cross_below_minus10 = _cross(-10, vara)
    varb = _barslast(cross_below_minus10)

    # ── VARD: sustained deep oversold (VARA < -10 for > 3 bars) ──
    vard = (vara < -10) & (varb > 3)

    # ── Golden Pit signal ─────────────────────────────────────────
    df["golden_pit"] = np.where(vard, vara, 0.0)

    # ── a47/a48: 7-bar RSI-like indicator (based on 2-bar diff) ──
    a47 = close.shift(2)                     # close 2 bars ago
    pos_diff = (close - a47).clip(lower=0)   # positive price changes
    abs_diff = (close - a47).abs()           # absolute price changes
    rsi_pos_arr = _sma(pos_diff, 7, 1)
    rsi_abs_arr = _sma(abs_diff, 7, 1)
    # Avoid division by zero
    with np.errstate(divide="ignore", invalid="ignore"):
        a48_val = np.where(rsi_abs_arr > 0, rsi_pos_arr / rsi_abs_arr * 100, 50.0)
    df["rsi_7"] = a48_val

    # ── Band Low signal ───────────────────────────────────────────
    df["band_low"] = np.where(a48_val < 15, 19.0, 0.0)

    return df


def golden_pit_signal(df: pd.DataFrame) -> pd.Series:
    """
    Return Golden Pit signal as boolean, for use in stock screening.

    Returns True when Golden Pit is triggered.
    """
    result = compute(df)
    return result["golden_pit"] != 0


def band_low_signal(df: pd.DataFrame) -> pd.Series:
    """
    Return Band Low signal as boolean.

    Returns True when Band Low is triggered.
    """
    result = compute(df)
    return result["band_low"] != 0


# ── Quick Visualization ──────────────────────────────────────────────

def plot(df: pd.DataFrame, symbol: str = "", save_path: str = ""):
    """
    Quick visual of the Golden Pit indicator.

    Parameters
    ----------
    df : pd.DataFrame (already processed by compute())
    symbol : stock ticker for the chart title
    save_path : if provided, save chart to this path
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        plt.rcParams["font.sans-serif"] = [
            "PingFang HK", "Heiti TC", "STHeiti", "Arial Unicode MS", "sans-serif"
        ]
        plt.rcParams["axes.unicode_minus"] = False
    except ImportError:
        print("Please install matplotlib: pip install matplotlib")
        return

    df = df.copy()
    if "golden_pit" not in df.columns:
        df = compute(df)

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1.5, 1.5]})

    # ── Subplot 1: Candlestick + Golden Pit markers ───────────────
    ax1 = axes[0]
    colors = ["red" if c >= o else "green" for c, o in zip(df["close"], df["open"])]
    ax1.bar(df.index, df["high"] - df["low"], bottom=df["low"],
            color=colors, width=0.8, linewidth=0.5, alpha=0.3)
    ax1.bar(df.index, abs(df["close"] - df["open"]),
            bottom=df[["open", "close"]].min(axis=1),
            color=colors, width=0.8, linewidth=0.5)

    # Mark Golden Pit signals
    pit_mask = df["golden_pit"] != 0
    if pit_mask.any():
        ax1.scatter(df.index[pit_mask], df["low"][pit_mask] * 0.97,
                    marker="v", color="gold", s=80, zorder=5,
                    edgecolors="darkorange", linewidths=1,
                    label=f"Golden Pit ({pit_mask.sum()} signals)")

    ax1.set_title(f"Golden Pit 2.0 — {symbol}" if symbol else "Golden Pit 2.0", fontsize=13)
    ax1.set_ylabel("Price")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)

    # ── Subplot 2: Deviation from 27MA + Golden Pit ───────────────
    ax2 = axes[1]
    ax2.fill_between(df.index, df["dev_pct_2ma"], 0,
                     where=df["dev_pct_2ma"] < 0,
                     color="green", alpha=0.15)
    ax2.fill_between(df.index, df["dev_pct_2ma"], 0,
                     where=df["dev_pct_2ma"] > 0,
                     color="red", alpha=0.15)
    ax2.plot(df.index, df["dev_pct_2ma"], color="#333", linewidth=0.8, label="VARA (deviation from 27MA)")
    ax2.axhline(y=-10, color="red", linestyle="--", linewidth=0.8, alpha=0.6, label="-10% oversold line")
    ax2.axhline(y=0, color="gray", linestyle="-", linewidth=0.5)
    ax2.axhline(y=10, color="red", linestyle="--", linewidth=0.8, alpha=0.6, label="+10% overbought line")

    # Mark Golden Pit zones
    pit_mask2 = df["golden_pit"] != 0
    if pit_mask2.any():
        ax2.scatter(df.index[pit_mask2], df["dev_pct_2ma"][pit_mask2],
                    color="gold", s=50, zorder=5, marker="s")

    ax2.set_ylabel("Deviation %")
    ax2.legend(loc="upper left", fontsize=7)
    ax2.grid(alpha=0.3)

    # ── Subplot 3: Band Low (RSI-like) ────────────────────────────
    ax3 = axes[2]
    ax3.plot(df.index, df["rsi_7"], color="#5856d6", linewidth=0.8, label="a48 (7-bar RSI-like)")
    ax3.axhline(y=15, color="red", linestyle="--", linewidth=0.8, alpha=0.6, label="Low line (15)")
    ax3.fill_between(df.index, 15, 0, color="red", alpha=0.06)

    low_mask = df["band_low"] != 0
    if low_mask.any():
        ax3.scatter(df.index[low_mask], df["rsi_7"][low_mask],
                    color="red", s=30, zorder=5, marker="^",
                    label=f"Band Low ({low_mask.sum()} signals)")

    ax3.set_ylabel("RSI-like")
    ax3.set_xlabel("Date")
    ax3.legend(loc="upper left", fontsize=7)
    ax3.grid(alpha=0.3)

    # Date formatting
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Chart saved: {save_path}")

    plt.show()
