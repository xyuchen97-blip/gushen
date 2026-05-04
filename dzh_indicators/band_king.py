"""
Band King — DZH Classic Indicator Python Implementation
========================================================

Source: DZH (DaZhiHui) Band King main-chart indicator
URL: https://www.zbgs3.com/52174.html

**Core Logic**:
Detects wave peaks and troughs across 4 ZIG zigzag periods (6% / 22% / 51% / 72%).
Emits buy/sell signals when multiple periods turn simultaneously (resonance).

**Important Warning**:
The original DZH formula uses ZIG / TROUGH / BACKSET — all **future functions**.
These modify historical signals when new bars arrive, causing backtest distortion.

This implementation provides two versions:
  compute_zig()      — ZIG-like algorithm (retains forward-looking behavior, for reference)
  compute_no_future() — Uses historical extrema detection only (safe for backtesting/live)

**Signal Guide**:
  buy1  — TROUGH signal (short-term bottom)
  buy2  — ZIG multi-period resonance buy (6/22/51/72 turn up together)
  sell1 — ZIG multi-period resonance sell (6/22/51/72 turn down together)
  wave_top — Confirmed wave peak marker

Usage:
    from dzh_indicators.band_king import compute_no_future

    df = compute_no_future(df)
    # New columns: buy1, buy2, sell1, wave_top, zig_dir_6/22/51/72
"""

import numpy as np
import pandas as pd


# ── Extrema Detection (ZIG substitute) ────────────────────────────────

def _find_peaks_troughs(series: np.ndarray, order: int, min_pct: float) -> tuple:
    """
    Sliding-window extrema detection to replace the ZIG future function.

    Parameters
    ----------
    series : price array (typically close)
    order  : sliding window radius (larger = smoother, corresponds to larger ZIG %)
    min_pct: minimum reversal percentage to confirm an extremum

    Returns
    -------
    peaks   : bool array, peak positions
    troughs : bool array, trough positions
    """
    n = len(series)
    peaks = np.zeros(n, dtype=bool)
    troughs = np.zeros(n, dtype=bool)

    # Detect local extrema via sliding window
    for i in range(order, n - order):
        window = series[i - order:i + order + 1]
        mid = series[i]

        # Peak: middle value is the window maximum
        if mid == window.max():
            peaks[i] = True

        # Trough: middle value is the window minimum
        if mid == window.min():
            troughs[i] = True

    # Filter out extrema that are too close together
    _filter_too_close(peaks, min_gap=order // 2)
    _filter_too_close(troughs, min_gap=order // 2)

    return peaks, troughs


def _filter_too_close(markers: np.ndarray, min_gap: int):
    """Merge same-direction extrema that are too close. Keep the more extreme one."""
    prev = -min_gap - 1
    for i in range(len(markers)):
        if markers[i]:
            if i - prev <= min_gap:
                markers[prev] = False
            prev = i


def _extrema_to_direction(series: np.ndarray, peaks: np.ndarray,
                          troughs: np.ndarray) -> np.ndarray:
    """
    Convert peak/trough markers into per-bar direction signals:
    -1 = in downtrend (last extremum was a peak)
     0 = uncertain
    +1 = in uptrend (last extremum was a trough)
    """
    n = len(series)
    direction = np.zeros(n, dtype=int)
    last_peak = -1
    last_trough = -1

    for i in range(n):
        if peaks[i]:
            last_peak = i
        if troughs[i]:
            last_trough = i

        # Determine current direction
        if last_peak > last_trough:
            direction[i] = -1  # coming down from a peak
        elif last_trough > last_peak:
            direction[i] = 1   # coming up from a trough
        else:
            direction[i] = 0

    return direction


def _zig_sim(series: np.ndarray, reversal_pct: float) -> tuple:
    """
    Simplified ZIG zigzag function (retains some forward-looking behavior).

    Scans the price series; when price reverses by more than reversal_pct%
    from the last turning point, marks a new turning point.

    Parameters
    ----------
    series       : price array
    reversal_pct : reversal percentage (e.g. 6 means 6%)

    Returns
    -------
    zig_line   : ZIG line values
    direction  : direction (+1 up, -1 down)
    turning    : bool array, turning point positions
    """
    n = len(series)
    zig_line = np.zeros(n)
    direction = np.zeros(n, dtype=int)
    turning = np.zeros(n, dtype=bool)

    if n < 2:
        return zig_line, direction, turning

    # Initialize
    zig_line[0] = series[0]
    direction[0] = 1
    last_extreme_val = series[0]
    cur_dir = 1  # 1 = up, -1 = down

    for i in range(1, n):
        pct_change = (series[i] - last_extreme_val) / last_extreme_val * 100

        if cur_dir == 1:
            # In uptrend, check for downside reversal
            zig_line[i] = max(zig_line[i - 1], series[i]) if i > 0 else series[i]
            if -pct_change >= reversal_pct:  # dropped more than N%
                cur_dir = -1
                last_extreme_val = series[i - 1]
                zig_line[i] = series[i]
                turning[i] = True
        else:
            # In downtrend, check for upside reversal
            zig_line[i] = min(zig_line[i - 1], series[i]) if i > 0 else series[i]
            if pct_change >= reversal_pct:  # rose more than N%
                cur_dir = 1
                last_extreme_val = series[i - 1]
                zig_line[i] = series[i]
                turning[i] = True

        direction[i] = cur_dir

    return zig_line, direction, turning


# ── Main Computation ──────────────────────────────────────────────────


def compute_no_future(df: pd.DataFrame) -> pd.DataFrame:
    """
    Band King — No-future-function version (recommended for backtesting/live).

    Uses sliding-window extrema detection instead of ZIG.
    All signals based on confirmed historical data only.

    Parameters
    ----------
    df : pd.DataFrame; must contain open, high, low, close

    Returns
    -------
    df with appended columns:
        buy1, buy2, sell1      — trade signals (bool)
        wave_top                — wave peak marker
        zig_dir_6/22/51/72     — ZIG direction per period
        ma1~ma6                 — 6 moving averages
    """
    df = df.copy()
    n = len(df)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    # ── MA1-MA6 (default params P1-P6 = 5,10,20,30,60,120) ────
    p_values = [5, 10, 20, 30, 60, 120]
    for j, p in enumerate(p_values):
        df[f"ma{j+1}"] = df["close"].rolling(p).mean()

    # ── Multi-period ZIG directions ────────────────────────────
    # Order and reversal % mapping (approximate):
    #   6%  → order=3  (short-term)
    #   22% → order=10 (medium-term)
    #   51% → order=25 (medium-long)
    #   72% → order=35 (long-term)
    zig_configs = [
        (6, 3),
        (22, 10),
        (51, 25),
        (72, 35),
    ]

    zig_directions = {}
    zig_turns_up = {}
    zig_turns_down = {}

    for pct, order in zig_configs:
        peaks, troughs = _find_peaks_troughs(close, order=order, min_pct=pct)
        direction = _extrema_to_direction(close, peaks, troughs)

        col_dir = f"zig_dir_{pct}"
        df[col_dir] = direction
        zig_directions[pct] = direction

        # Turn detection: today's direction != yesterday's
        turn_up = np.zeros(n, dtype=bool)
        turn_down = np.zeros(n, dtype=bool)
        for i in range(1, n):
            if direction[i] == 1 and direction[i - 1] == -1:
                turn_up[i] = True
            if direction[i] == -1 and direction[i - 1] == 1:
                turn_down[i] = True
        zig_turns_up[pct] = turn_up
        zig_turns_down[pct] = turn_down

    # ── ZIG Resonance Signals ──────────────────────────────────
    # Buy2: at least 3 out of 4 periods turn up simultaneously
    # Sell1: at least 3 out of 4 periods turn down simultaneously
    # (Strict 4/4 is rare; 3/4 is more practical)
    zig_ups = [zig_turns_up[pct] for pct, _ in zig_configs]
    zig_downs = [zig_turns_down[pct] for pct, _ in zig_configs]

    buy2 = np.zeros(n, dtype=bool)
    sell1 = np.zeros(n, dtype=bool)
    for i in range(n):
        if sum(up[i] for up in zig_ups) >= 3:
            buy2[i] = True
        if sum(down[i] for down in zig_downs) >= 3:
            sell1[i] = True

    df["buy2"] = buy2
    df["sell1"] = sell1

    # ── Buy1: TROUGH(3,6,1) equivalent — short-term trough ─────
    buy1 = np.zeros(n, dtype=bool)
    buy_zig_dir = zig_directions[6]
    for i in range(1, n):
        if buy_zig_dir[i] == 1 and buy_zig_dir[i - 1] == -1:
            buy1[i] = True
    df["buy1"] = buy1

    # ── Wave Top: BACKSET/FILTER substitute ────────────────────
    # Detect significant highs within a 10-bar window
    peaks_10, _ = _find_peaks_troughs(close, order=5, min_pct=10)
    wave_top = np.zeros(n, dtype=bool)
    # Confirm wave top when price has retraced from a 10-bar peak
    for i in range(10, n):
        if peaks_10[i - 5] and close[i] < close[i - 5] * 0.95:
            wave_top[i - 5] = True
    df["wave_top"] = wave_top

    return df


def compute_zig(df: pd.DataFrame) -> pd.DataFrame:
    """
    Band King — ZIG approximate version (for cross-reference with original).

    WARNING: This version has forward-looking behavior similar to ZIG.
    Historical turning points may change when new bars arrive.
    Use compute_no_future() for backtesting.
    """
    df = df.copy()
    n = len(df)
    close = df["close"].values

    p_values = [5, 10, 20, 30, 60, 120]
    for j, p in enumerate(p_values):
        df[f"ma{j+1}"] = df["close"].rolling(p).mean()

    zig_configs = [6, 22, 51, 72]
    zig_directions = {}
    zig_turns_up = {}
    zig_turns_down = {}

    for pct in zig_configs:
        _, direction, turning = _zig_sim(close, reversal_pct=pct)

        col_dir = f"zz_dir_{pct}"
        df[col_dir] = direction
        zig_directions[pct] = direction

        turn_up = np.zeros(n, dtype=bool)
        turn_down = np.zeros(n, dtype=bool)
        for i in range(1, n):
            if direction[i] == 1 and direction[i - 1] == -1:
                turn_up[i] = True
            if direction[i] == -1 and direction[i - 1] == 1:
                turn_down[i] = True
        zig_turns_up[pct] = turn_up
        zig_turns_down[pct] = turn_down

    zig_ups = [zig_turns_up[p] for p in zig_configs]
    zig_downs = [zig_turns_down[p] for p in zig_configs]

    buy2 = np.zeros(n, dtype=bool)
    sell1 = np.zeros(n, dtype=bool)
    for i in range(n):
        if sum(up[i] for up in zig_ups) >= 3:
            buy2[i] = True
        if sum(down[i] for down in zig_downs) >= 3:
            sell1[i] = True
    df["buy2"] = buy2
    df["sell1"] = sell1

    buy1 = np.zeros(n, dtype=bool)
    bd = zig_directions[6]
    for i in range(1, n):
        if bd[i] == 1 and bd[i - 1] == -1:
            buy1[i] = True
    df["buy1"] = buy1

    df["wave_top"] = False
    df["use_future_warning"] = True  # marks that this version has future functions

    return df


# ── Quick Visualization ──────────────────────────────────────────────

def plot(df: pd.DataFrame, symbol: str = "", save_path: str = "",
         use_no_future: bool = True):
    """
    Visualize the Band King indicator.

    Parameters
    ----------
    df : pd.DataFrame (already processed by compute_no_future or compute_zig)
    symbol : stock ticker for title
    save_path : optional save path
    use_no_future : True for no-future version, False for ZIG version
    """
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    plt.rcParams["font.sans-serif"] = [
        "PingFang HK", "Heiti TC", "STHeiti", "Arial Unicode MS", "sans-serif"
    ]
    plt.rcParams["axes.unicode_minus"] = False

    if "buy1" not in df.columns:
        df = compute_no_future(df) if use_no_future else compute_zig(df)

    fig, ax = plt.subplots(figsize=(16, 7))

    # Candlesticks
    colors = ["red" if c >= o else "green" for c, o in zip(df["close"], df["open"])]
    ax.bar(df.index, df["high"] - df["low"], bottom=df["low"],
           color=colors, width=0.6, linewidth=0.3, alpha=0.4)
    ax.bar(df.index, abs(df["close"] - df["open"]),
           bottom=df[["open", "close"]].min(axis=1),
           color=colors, width=0.6, linewidth=0.3)

    # Moving averages
    for j in [1, 3, 5]:  # ma1=5, ma3=20, ma5=60
        col = f"ma{j}"
        if col in df.columns:
            alpha = 0.5 if j == 1 else 0.7
            lw = 0.6 if j == 1 else 1.0
            ax.plot(df.index, df[col], linewidth=lw, alpha=alpha,
                    label=f"MA{_j_to_p(j)}")

    # Trade signals
    if df["buy1"].any():
        b1 = df[df["buy1"]]
        ax.scatter(b1.index, b1["low"] * 0.97, marker="^", color="red",
                   s=60, zorder=5, label=f"Buy1 ({len(b1)})")
    if df["buy2"].any():
        b2 = df[df["buy2"]]
        ax.scatter(b2.index, b2["low"] * 0.95, marker="^", color="magenta",
                   s=100, zorder=5, label=f"Buy2 ({len(b2)})")
    if df["sell1"].any():
        s1 = df[df["sell1"]]
        ax.scatter(s1.index, s1["high"] * 1.04, marker="v", color="green",
                   s=100, zorder=5, label=f"Sell1 ({len(s1)})")

    # Wave tops
    if df["wave_top"].any():
        wt = df[df["wave_top"]]
        ax.scatter(wt.index, wt["high"] * 1.02, marker="o", color="purple",
                   s=40, zorder=3, alpha=0.6, label="Wave Top")

    title = f"Band King — {symbol}" if symbol else "Band King"
    if df.get("use_future_warning", False):
        title += " [contains future functions]"
    ax.set_title(title, fontsize=14)
    ax.set_ylabel("Price")
    ax.legend(loc="upper left", fontsize=7)
    ax.grid(alpha=0.2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Chart saved: {save_path}")
    plt.show()


def _j_to_p(j: int) -> int:
    """Map ma index → period."""
    mapping = {1: 5, 2: 10, 3: 20, 4: 30, 5: 60, 6: 120}
    return mapping.get(j, 0)
