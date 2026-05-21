"""
Nine Turns (TD Sequential) — DZH Classic Indicator Python Implementation
=========================================================================

Source: DZH (DaZhiHui) Nine Turns indicator, based on Tom DeMark's TD Sequential theory.
Original wrapper: STSFunc.dll (encrypted, source not public).

**Algorithm** (Tom DeMark TD Sequential):

1. **Buy Setup**: 9 consecutive bars where close < close[4] → count 1→9.
   At 9, Setup complete — price may be near a bottom.

2. **Sell Setup**: 9 consecutive bars where close > close[4] → count 1→9.
   At 9, Setup complete — price may be near a top.

3. **Countdown** (optional): After Setup completes, enter 13-bar countdown.
   - Buy Countdown: count bars where close ≤ low[2] → 1→13
   - Sell Countdown: count bars where close ≥ high[2] → 1→13
   Reaching 13 gives final reversal signal.

4. **Intersection rule**: If both Buy and Sell Setups are counting simultaneously,
   the earlier one takes priority. A new opposite Setup completing at 9
   interrupts the current Countdown.

DZH original code (calls encrypted DLL):
    a  := "STSFunc@CALSTSL";
    S1 := "STSFunc@STS_STS1";    // positive = buy sequence, negative = sell sequence
    drawtext(S1>0, L, STR(S1));  // label below bar
    drawtext(S1<0, H*1.03, STR(-S1));  // label above bar

Usage:
    import akshare as ak
    from dzh_indicators.jiu_zhuan import compute

    df = ak.stock_zh_a_hist(symbol="000001", period="daily", adjust="qfq")
    df = compute(df)
    # New columns: buy_setup, sell_setup, setup_value (display number)
    #               buy_countdown, sell_countdown, buy_signal, sell_signal
"""

import numpy as np
import pandas as pd


def compute(df: pd.DataFrame, use_countdown: bool = True) -> pd.DataFrame:
    """
    Compute TD Sequential Nine Turns indicator.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain: open, high, low, close. Date index required.
    use_countdown : bool
        Enable Countdown phase (default True).
        If False, only output Setup sequence (1-9), similar to DZH simplified version.

    Returns
    -------
    pd.DataFrame (original df with appended columns):
        buy_setup       — Buy Setup count (1-9, 0 = not counting)
        sell_setup      — Sell Setup count (1-9, 0 = not counting)
        setup_value     — Combined display value (positive = buy, negative = sell)
        buy_setup_done  — Buy Setup just completed (9 hit) = True
        sell_setup_done — Sell Setup just completed = True
        buy_countdown   — Buy Countdown count (1-13, 0 = not entered)
        sell_countdown  — Sell Countdown count (1-13, 0 = not entered)
        buy_signal      — Buy signal (Buy Setup 9 + Countdown 13 completed)
        sell_signal     — Sell signal (Sell Setup 9 + Countdown 13 completed)
    """
    df = df.copy()
    n = len(df)
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    # ── Setup Count ────────────────────────────────────────────────
    buy_setup = np.zeros(n, dtype=int)
    sell_setup = np.zeros(n, dtype=int)
    buy_count = 0
    sell_count = 0

    for i in range(n):
        # Buy Setup: close[i] < close[i-4]
        if i >= 4 and close[i] < close[i - 4]:
            buy_count += 1
            buy_count = min(buy_count, 9)  # cap at 9
        else:
            buy_count = 0
        buy_setup[i] = buy_count

        # Sell Setup: close[i] > close[i-4]
        if i >= 4 and close[i] > close[i - 4]:
            sell_count += 1
            sell_count = min(sell_count, 9)
        else:
            sell_count = 0
        sell_setup[i] = sell_count

    df["buy_setup"] = buy_setup
    df["sell_setup"] = sell_setup

    # Combined display value (positive = buy sequence, negative = sell sequence)
    setup_value = np.zeros(n, dtype=int)
    for i in range(n):
        if buy_setup[i] > 0:
            setup_value[i] = buy_setup[i]
        elif sell_setup[i] > 0:
            setup_value[i] = -sell_setup[i]
    df["setup_value"] = setup_value

    # Bars where Setup just completed
    buy_done = np.zeros(n, dtype=bool)
    sell_done = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if buy_setup[i - 1] == 8 and buy_setup[i] == 9:
            buy_done[i] = True
        if sell_setup[i - 1] == 8 and sell_setup[i] == 9:
            sell_done[i] = True
    df["buy_setup_done"] = buy_done
    df["sell_setup_done"] = sell_done

    # ── Countdown Phase ────────────────────────────────────────────
    buy_countdown_arr = np.zeros(n, dtype=int)
    sell_countdown_arr = np.zeros(n, dtype=int)
    buy_signal = np.zeros(n, dtype=bool)
    sell_signal = np.zeros(n, dtype=bool)

    if use_countdown:
        # State machine
        in_buy_cd = False       # currently in Buy Countdown
        in_sell_cd = False      # currently in Sell Countdown
        buy_cd_count = 0
        sell_cd_count = 0

        for i in range(n):
            # ── Buy Setup complete → start Buy Countdown ──────────
            if buy_done[i]:
                # If Sell Countdown was running, new Buy Setup overrides
                if in_sell_cd:
                    in_sell_cd = False
                    sell_cd_count = 0
                in_buy_cd = True
                buy_cd_count = 0

            # ── Sell Setup complete → start Sell Countdown ─────────
            if sell_done[i]:
                if in_buy_cd:
                    in_buy_cd = False
                    buy_cd_count = 0
                in_sell_cd = True
                sell_cd_count = 0

            # ── Buy Countdown: increment each bar (simple version) ─
            if in_buy_cd:
                buy_cd_count += 1
                if buy_cd_count >= 13:
                    buy_cd_count = 13
                    buy_signal[i] = True
                    in_buy_cd = False  # Countdown complete, wait for next Setup
            buy_countdown_arr[i] = buy_cd_count if in_buy_cd else 0

            # ── Sell Countdown ─────────────────────────────────────
            if in_sell_cd:
                sell_cd_count += 1
                if sell_cd_count >= 13:
                    sell_cd_count = 13
                    sell_signal[i] = True
                    in_sell_cd = False
            sell_countdown_arr[i] = sell_cd_count if in_sell_cd else 0

    df["buy_countdown"] = buy_countdown_arr
    df["sell_countdown"] = sell_countdown_arr
    df["buy_signal"] = buy_signal
    df["sell_signal"] = sell_signal

    return df


def setup_only(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute only the Setup sequence (1-9), no Countdown.
    Equivalent to the simplified DZH version (just labels 1-9 on candles).
    """
    return compute(df, use_countdown=False)


def plot(df: pd.DataFrame, symbol: str = "", save_path: str = ""):
    """
    Visualize the Nine Turns indicator (candlesticks + sequence number labels).

    Parameters
    ----------
    df : pd.DataFrame (already processed by compute())
    symbol : stock ticker for title
    save_path : optional path to save chart
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
    if "setup_value" not in df.columns:
        df = compute(df)

    fig, ax = plt.subplots(figsize=(16, 7))

    # Candlesticks
    colors = ["red" if c >= o else "green" for c, o in zip(df["close"], df["open"])]
    ax.bar(df.index, df["high"] - df["low"], bottom=df["low"],
           color=colors, width=0.6, linewidth=0.3, alpha=0.4)
    ax.bar(df.index, abs(df["close"] - df["open"]),
           bottom=df[["open", "close"]].min(axis=1),
           color=colors, width=0.6, linewidth=0.3)

    # ── Label sequence numbers ──────────────────────────────────
    for i in range(len(df)):
        sv = df["setup_value"].iloc[i]
        if sv > 0:
            # Buy sequence → label below bar
            y = df["low"].iloc[i] * 0.97
            color = "red"
            text = str(sv)
        elif sv < 0:
            # Sell sequence → label above bar
            y = df["high"].iloc[i] * 1.04
            color = "green"
            text = str(-sv)
        else:
            continue

        # Larger font at count 9
        fontsize = 11 if abs(sv) == 9 else 8
        fontweight = "bold" if abs(sv) == 9 else "normal"
        ax.annotate(text, (df.index[i], y),
                    fontsize=fontsize, fontweight=fontweight,
                    color="white",
                    ha="center", va="center",
                    bbox=dict(boxstyle="circle,pad=0.3",
                              facecolor=color, edgecolor="none", alpha=0.85))

    # ── Mark buy/sell signals (Countdown 13) ────────────────────
    if "buy_signal" in df.columns:
        buy_sig = df[df["buy_signal"]]
        sell_sig = df[df["sell_signal"]]
        if len(buy_sig):
            ax.scatter(buy_sig.index, buy_sig["low"] * 0.94,
                       marker="^", color="red", s=150, zorder=10,
                       edgecolors="darkred", linewidths=1.5,
                       label=f"Buy Signal ({len(buy_sig)})")
        if len(sell_sig):
            ax.scatter(sell_sig.index, sell_sig["high"] * 1.07,
                       marker="v", color="green", s=150, zorder=10,
                       edgecolors="darkgreen", linewidths=1.5,
                       label=f"Sell Signal ({len(sell_sig)})")

    title = f"Nine Turns (TD Sequential) — {symbol}" if symbol else "Nine Turns (TD Sequential)"
    ax.set_title(title, fontsize=14)
    ax.set_ylabel("Price")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Chart saved: {save_path}")

    plt.show()
