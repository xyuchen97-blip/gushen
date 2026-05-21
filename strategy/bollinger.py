"""
Bollinger Band Weekly Rules — Hard constraints that override composite scores.

Rules:
  SELL: Daily close > weekly BB upper band (2 std, 20-period) → exit immediately
  BUY:  Daily close < weekly BB lower band AND volume > 2x 20-day MA → contrarian entry

These apply to both A-shares and US stocks.
"""

import pandas as pd
import numpy as np
from .config import BB_WEEKLY_PERIOD, BB_WEEKLY_STD, BB_BUY_VOL_MULTIPLE


def compute_weekly_bb(df_weekly: pd.DataFrame) -> pd.DataFrame:
    """
    Compute weekly Bollinger Bands.

    Parameters
    ----------
    df_weekly : weekly OHLCV DataFrame (resampled)

    Returns
    -------
    DataFrame with added columns: bb_mid, bb_upper, bb_lower
    """
    df = df_weekly.copy()
    df["bb_mid"] = df["close"].rolling(BB_WEEKLY_PERIOD).mean()
    std = df["close"].rolling(BB_WEEKLY_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_WEEKLY_STD * std
    df["bb_lower"] = df["bb_mid"] - BB_WEEKLY_STD * std
    return df


def bb_weekly_sell_signal(df_daily: pd.DataFrame, df_weekly_bb: pd.DataFrame) -> pd.Series:
    """
    Check if daily close breaches the weekly BB upper band.

    Returns boolean Series (aligned to daily index).
    True = exit position immediately.
    """
    # Align weekly BB to daily bars: carry forward the weekly band values
    bb_upper_daily = df_weekly_bb["bb_upper"].reindex(df_daily.index, method="ffill")
    sell = df_daily["close"] > bb_upper_daily
    return sell


def bb_weekly_buy_signal(df_daily: pd.DataFrame, df_weekly_bb: pd.DataFrame) -> pd.Series:
    """
    Check BB weekly buy conditions:
    1. Daily close < weekly BB lower band
    2. Volume > 2x 20-day MA volume

    Returns boolean Series.
    True = contrarian entry signal.
    """
    bb_lower_daily = df_weekly_bb["bb_lower"].reindex(df_daily.index, method="ffill")
    close_below_lower = df_daily["close"] < bb_lower_daily
    vol_ma20 = df_daily["volume"].rolling(20).mean()
    volume_surge = df_daily["volume"] > (vol_ma20 * BB_BUY_VOL_MULTIPLE)
    return close_below_lower & volume_surge


def bb_weekly_position_pct(row: pd.Series) -> float:
    """
    Return position sizing for BB weekly trades.
    Capped at 50% of normal to reflect contrarian nature.
    """
    return row.get("base_position_pct", 0.05) * 0.5
