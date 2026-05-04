"""
Unified Data Pipeline — fetches daily + weekly OHLCV for A-shares and US stocks.

Caching: fetched data stored in memory dict to avoid repeated API calls during backtests.
"""

import os
import pickle
import pandas as pd
import numpy as np
import akshare as ak
import yfinance as yf
from pathlib import Path
from datetime import datetime, timedelta

CACHE_DIR = Path(__file__).parent / "_cache"

# In-memory cache: { "ticker:market:daily|weekly": DataFrame }
_cache: dict[str, pd.DataFrame] = {}


def _cache_key(ticker: str, market: str, freq: str) -> str:
    return f"{ticker}:{market}:{freq}"


def _standardize_columns(df: pd.DataFrame, market: str) -> pd.DataFrame:
    """Rename columns to uniform OHLCV format."""
    col_map = {}
    for col in df.columns:
        low = col.lower()
        if "日期" in col or "date" in low:
            col_map[col] = "date"
        elif "开盘" in col or "open" in low:
            col_map[col] = "open"
        elif "最高" in col or "high" in low:
            col_map[col] = "high"
        elif "最低" in col or "low" in low:
            col_map[col] = "low"
        elif "收盘" in col or "close" in low:
            col_map[col] = "close"
        elif "成交量" in col or "volume" in low:
            col_map[col] = "volume"
    df = df.rename(columns=col_map)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    # Keep only needed columns
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep]


def _fetch_a_share(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch A-share daily OHLCV via akshare."""
    try:
        df = ak.stock_zh_a_hist(
            symbol=ticker, period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="qfq"
        )
        return _standardize_columns(df, "A")
    except Exception as e:
        print(f"  [WARN] akshare fetch failed for {ticker}: {e}")
        return pd.DataFrame()


def _fetch_us(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch US daily OHLCV via yfinance."""
    try:
        stock = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if stock.empty:
            return pd.DataFrame()
        if isinstance(stock.columns, pd.MultiIndex):
            stock.columns = stock.columns.droplevel(1)
        stock.columns = [c.lower() for c in stock.columns]
        stock.index = pd.to_datetime(stock.index)
        stock.index.name = "date"
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in stock.columns]
        return stock[keep]
    except Exception as e:
        print(f"  [WARN] yfinance fetch failed for {ticker}: {e}")
        return pd.DataFrame()


def fetch_ohlcv(ticker: str, market: str, start: str, end: str,
                freq: str = "daily") -> pd.DataFrame:
    """
    Fetch OHLCV data for a single ticker.

    Parameters
    ----------
    ticker : stock code (e.g. "600519", "AAPL")
    market : "A" or "US"
    start  : start date "YYYY-MM-DD"
    end    : end date "YYYY-MM-DD"
    freq   : "daily" or "weekly"

    Returns
    -------
    DataFrame with columns: open, high, low, close, volume, date index
    """
    # Check in-memory cache
    key = _cache_key(ticker, market, freq)
    if key in _cache:
        df = _cache[key]
        # Slice to requested date range
        return df.loc[start:end].copy()

    # Fetch daily data
    daily_key = _cache_key(ticker, market, "daily")
    if daily_key in _cache:
        df_daily = _cache[daily_key]
    else:
        if market == "A":
            df_daily = _fetch_a_share(ticker, start, end)
        else:
            df_daily = _fetch_us(ticker, start, end)

        if df_daily.empty:
            return df_daily
        _cache[daily_key] = df_daily

    if freq == "daily":
        return df_daily.loc[start:end].copy()

    # Resample to weekly
    df_weekly = df_daily.resample("W-FRI").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

    _cache[key] = df_weekly
    return df_weekly.loc[start:end].copy()


def fetch_universe(universe: list[str], market: str, start: str, end: str,
                   freq: str = "daily", verbose: bool = True) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for an entire stock universe.

    Returns dict: {ticker: DataFrame}
    """
    data = {}
    total = len(universe)
    for i, ticker in enumerate(universe):
        df = fetch_ohlcv(ticker, market, start, end, freq=freq)
        if not df.empty and len(df) > 50:  # skip stocks with too little data
            data[ticker] = df
        if verbose and (i + 1) % 10 == 0:
            print(f"  [{market}] Fetched {i+1}/{total}...")
    if verbose:
        print(f"  [{market}] Done: {len(data)}/{total} stocks with data")
    return data


def fetch_macro_data(start: str, end: str) -> dict[str, pd.Series]:
    """
    Fetch macro indicators: VIX, USD/CNY, 10Y, 5Y yields.

    Returns dict with keys: vix, usdcny, yield10y, yield5y
    """
    macro = {}
    try:
        vix = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=True)
        if not vix.empty:
            macro["vix"] = vix["Close"].squeeze()
    except Exception:
        pass

    try:
        cny = ak.currency_boc_sina(symbol="美元")
        if not cny.empty:
            cny["date"] = pd.to_datetime(cny["日期"]) if "日期" in cny.columns else pd.to_datetime(cny["date"])
            cny = cny.set_index("date").sort_index()
            macro["usdcny"] = cny["中行折算价"].astype(float)
    except Exception:
        pass

    try:
        t10 = yf.download("^TNX", start=start, end=end, progress=False, auto_adjust=True)
        if not t10.empty:
            macro["yield10y"] = t10["Close"].squeeze()
    except Exception:
        pass

    try:
        t2 = yf.download("^FVX", start=start, end=end, progress=False, auto_adjust=True)
        if not t2.empty:
            macro["yield5y"] = t2["Close"].squeeze()
    except Exception:
        pass

    # ── US Macro ──────────────────────────────────────────────
    try:
        cpi = ak.macro_usa_cpi_yoy()
        if "时间" in cpi.columns:
            cpi["date"] = pd.to_datetime(cpi["时间"])
        else:
            cpi["date"] = pd.to_datetime(cpi["日期"])
        cpi = cpi.set_index("date").sort_index()
        val_col = "现值" if "现值" in cpi.columns else "今值"
        macro["us_cpi_yoy"] = cpi[val_col].astype(float)
    except Exception:
        pass

    try:
        unemp = ak.macro_usa_unemployment_rate()
        unemp["date"] = pd.to_datetime(unemp["日期"])
        unemp = unemp.set_index("date").sort_index()
        macro["us_unemployment"] = unemp["今值"].astype(float)
    except Exception:
        pass

    # ── China M2 ─────────────────────────────────────────────
    try:
        m2 = ak.macro_china_money_supply()
        m2["date"] = pd.to_datetime(
            m2["月份"].str.replace("年", "-").str.replace("月份", ""),
            format="%Y-%m", errors="coerce"
        )
        m2 = m2.dropna(subset=["date"]).set_index("date").sort_index()
        m2_col = "货币和准货币(M2)-同比增长"
        if m2_col in m2.columns:
            macro["china_m2_yoy"] = pd.to_numeric(m2[m2_col], errors="coerce")
    except Exception:
        pass

    # ── China-Specific Macro ──────────────────────────────────
    try:
        lpr = ak.macro_china_lpr()
        lpr["date"] = pd.to_datetime(lpr["TRADE_DATE"])
        lpr = lpr.set_index("date").sort_index()
        macro["china_lpr1y"] = lpr["LPR1Y"].astype(float)
    except Exception:
        pass

    try:
        cpi = ak.macro_china_cpi_yearly()
        cpi["date"] = pd.to_datetime(cpi["日期"])
        cpi = cpi.set_index("date").sort_index()
        macro["china_cpi"] = cpi["今值"].astype(float)
    except Exception:
        pass

    try:
        pmi = ak.macro_china_pmi_yearly()
        pmi["date"] = pd.to_datetime(pmi["日期"])
        pmi = pmi.set_index("date").sort_index()
        macro["china_pmi"] = pmi["今值"].astype(float)
    except Exception:
        pass

    # ── Northbound Flow (A-share only) ────────────────────────
    try:
        nb = ak.stock_hsgt_hist_em(symbol="沪股通")
        nb["date"] = pd.to_datetime(nb["日期"])
        nb = nb.set_index("date").sort_index()
        nb["net_buy"] = pd.to_numeric(nb["当日成交净买额"], errors="coerce")
        macro["northbound_flow"] = nb["net_buy"]
    except Exception:
        pass

    return macro


def clear_cache():
    """Clear in-memory cache between backtest runs."""
    _cache.clear()
