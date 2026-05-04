"""
Unified Data Pipeline — fetches daily + weekly OHLCV for A-shares, HK, and US stocks.
Data sources: akshare (stocks + macro), FRED API (VIX).
yfinance fully eliminated (2026-05-05).

--- LOCKED DATA SOURCES (do not modify without audit trail) ---
| Market | Daily OHLCV | Source |
|--------|-------------|--------|
| A-share | ak.stock_zh_a_hist() | Eastmoney via akshare |
| HK      | ak.stock_hk_hist()   | Eastmoney via akshare |
| US      | ak.stock_us_hist()   | Eastmoney via akshare |
| VIX     | FRED API (VIXCLS)    | api.stlouisfed.org |
| US 10Y/5Y | ak.bond_zh_us_rate() | Eastmoney via akshare |
| China macro | ak.macro_china_*  | Eastmoney via akshare |
| US macro | ak.macro_usa_*      | Eastmoney via akshare |
| China QVIX | ak.index_option_50etf_qvix() | optbbs.com via akshare |
"""

import os
import time
import threading
import pickle
import pandas as pd
import numpy as np
import requests
import akshare as ak
from pathlib import Path
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════════════════════════
# LOCKED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# FRED API (St. Louis Fed) — for VIX
FRED_API_KEY = "d2e91bd96a2baac24f998f4aa7afbe5b"
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

CACHE_DIR = Path(__file__).parent / "_cache"

# ═══════════════════════════════════════════════════════════════════
# RATE-LIMIT OPTIMIZER (Token Bucket)
# ═══════════════════════════════════════════════════════════════════

class RateLimiter:
    """Token bucket rate limiter for API calls.
    
    Prevents triggering rate limits on Eastmoney (akshare) and FRED APIs.
    Thread-safe for concurrent fetch operations.
    """
    
    def __init__(self, rate: float = 3.0, burst: int = 5):
        self.rate = rate          # tokens per second
        self.burst = burst        # max tokens
        self.tokens = burst
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()
    
    def acquire(self):
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens < 1:
                wait = (1 - self.tokens) / self.rate
                time.sleep(wait)
                self.tokens = 0
            else:
                self.tokens -= 1

# Global rate limiter instances
_limiter_ak = RateLimiter(rate=3.0, burst=5)    # Eastmoney (lenient, ~3 req/s)
_limiter_fred = RateLimiter(rate=1.0, burst=3)   # FRED (stricter, 120/min = 2/s but conservative)


def with_retry(max_retries=3, base_delay=1.0, backoff=2.0):
    """Decorator: retry on transient errors with exponential backoff."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt < max_retries - 1:
                        delay = base_delay * (backoff ** attempt)
                        print(f"  [RATE-LIMIT] {func.__name__}: retry {attempt+1}/{max_retries} in {delay:.1f}s — {e}")
                        time.sleep(delay)
            raise last_err
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════
# IN-MEMORY CACHE
# ═══════════════════════════════════════════════════════════════════

_cache: dict[str, pd.DataFrame] = {}
_us_symbol_map: dict[str, str] = {}  # MSFT → 105.MSFT


def _cache_key(ticker: str, market: str, freq: str) -> str:
    return f"{ticker}:{market}:{freq}"


def clear_cache():
    """Clear in-memory cache between analysis runs."""
    _cache.clear()


# ═══════════════════════════════════════════════════════════════════
# COLUMN STANDARDIZATION
# ═══════════════════════════════════════════════════════════════════

def _standardize_columns(df: pd.DataFrame, market: str = "") -> pd.DataFrame:
    """Rename varied column names to uniform OHLCV format (open/high/low/close/volume)."""
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
    
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
    elif df.index.name is None or df.index.name != "date":
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        df = df.sort_index()
    
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep]


# ═══════════════════════════════════════════════════════════════════
# US STOCK SYMBOL MAPPING
# ═══════════════════════════════════════════════════════════════════

def _load_us_symbol_map():
    """Lazily build MSFT → 105.MSFT mapping from Eastmoney US spot data."""
    global _us_symbol_map
    if _us_symbol_map:
        return
    try:
        _limiter_ak.acquire()
        spot = ak.stock_us_spot_em()
        for _, row in spot.iterrows():
            code = str(row.get("代码", ""))
            if "." in code:
                _, name = code.split(".", 1)
                _us_symbol_map[name.strip().upper()] = code
    except Exception as e:
        print(f"  [WARN] US symbol map build failed: {e}")
        # Map will remain empty — fallback to direct pass-through


def _get_us_symbol(ticker: str) -> str:
    """Convert plain ticker (MSFT) to Eastmoney symbol (105.MSFT)."""
    _load_us_symbol_map()
    return _us_symbol_map.get(ticker.upper().strip(), ticker)


# ═══════════════════════════════════════════════════════════════════
# MARKET-SPECIFIC FETCHERS
# ═══════════════════════════════════════════════════════════════════

@with_retry(max_retries=3)
def _fetch_a_share(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch A-share daily OHLCV via akshare (Eastmoney)."""
    _limiter_ak.acquire()
    df = ak.stock_zh_a_hist(
        symbol=ticker, period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust="qfq"
    )
    return _standardize_columns(df, "A")


@with_retry(max_retries=3)
def _fetch_hk(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch HK stock daily OHLCV via akshare (Eastmoney).
    Ticker format: "0700.HK" → code "00700".
    """
    _limiter_ak.acquire()
    code = ticker.replace(".HK", "").replace(".hk", "").zfill(5)
    df = ak.stock_hk_hist(
        symbol=code, period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust="qfq"
    )
    return _standardize_columns(df, "HK")


@with_retry(max_retries=3)
def _fetch_us(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch US stock daily OHLCV via akshare (Eastmoney).
    Uses symbol mapping: MSFT → 105.MSFT.
    """
    _limiter_ak.acquire()
    symbol = _get_us_symbol(ticker)
    df = ak.stock_us_hist(
        symbol=symbol, period="daily",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust="qfq"
    )
    return _standardize_columns(df, "US")


# ═══════════════════════════════════════════════════════════════════
# MAIN FETCH API
# ═══════════════════════════════════════════════════════════════════

def fetch_ohlcv(ticker: str, market: str, start: str, end: str,
                freq: str = "daily") -> pd.DataFrame:
    """
    Fetch OHLCV data for a single ticker.

    Parameters
    ----------
    ticker : stock code (e.g. "600519", "AAPL", "0700.HK")
    market : "A" | "HK" | "US"
    start  : start date "YYYY-MM-DD"
    end    : end date "YYYY-MM-DD"
    freq   : "daily" or "weekly"

    Returns
    -------
    DataFrame with columns: open, high, low, close, volume, date index
    """
    key = _cache_key(ticker, market, freq)
    if key in _cache:
        return _cache[key].loc[start:end].copy()

    daily_key = _cache_key(ticker, market, "daily")
    if daily_key in _cache:
        df_daily = _cache[daily_key]
    else:
        market_map = {"A": _fetch_a_share, "HK": _fetch_hk, "US": _fetch_us}
        fetcher = market_map.get(market)
        if fetcher is None:
            raise ValueError(f"Unsupported market: {market}. Use 'A', 'HK', or 'US'.")
        
        df_daily = fetcher(ticker, start, end)
        if df_daily.empty:
            return df_daily
        _cache[daily_key] = df_daily

    if freq == "daily":
        return df_daily.loc[start:end].copy()

    # Resample to weekly (Friday)
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
    """Fetch OHLCV for an entire stock universe. Returns {ticker: DataFrame}."""
    data = {}
    total = len(universe)
    for i, ticker in enumerate(universe):
        df = fetch_ohlcv(ticker, market, start, end, freq=freq)
        if not df.empty and len(df) > 50:
            data[ticker] = df
        if verbose and (i + 1) % 10 == 0:
            print(f"  [{market}] Fetched {i+1}/{total}...")
    if verbose:
        print(f"  [{market}] Done: {len(data)}/{total} stocks with data")
    return data


# ═══════════════════════════════════════════════════════════════════
# MACRO DATA FETCHER
# ═══════════════════════════════════════════════════════════════════

@with_retry(max_retries=3)
def _fetch_vix_fred(start: str, end: str) -> pd.Series | None:
    """Fetch CBOE VIX from FRED API (VIXCLS series).
    FRED requires YYYY-MM-DD format (with dashes).
    """
    _limiter_fred.acquire()
    try:
        resp = requests.get(FRED_BASE_URL, params={
            "series_id": "VIXCLS",
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "asc",
            "observation_start": start,  # Keep dashes — FRED requires YYYY-MM-DD
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        observations = data.get("observations", [])
        if not observations:
            return None
        
        values = []
        dates = []
        for obs in observations:
            v = obs["value"]
            if v != ".":
                values.append(float(v))
                dates.append(pd.Timestamp(obs["date"]))
        
        return pd.Series(values, index=pd.DatetimeIndex(dates), name="vix").sort_index()
    except Exception as e:
        print(f"  [WARN] FRED VIX fetch failed: {e}")
        return None


def fetch_macro_data(start: str, end: str) -> dict[str, pd.Series]:
    """
    Fetch macro indicators for scoring engine.
    
    Returns dict with keys: vix, usdcny, yield10y, yield5y, us_spread_10y2y,
    us_cpi_yoy, us_unemployment, china_lpr1y, china_cpi, china_pmi, china_m2_yoy,
    china_qvix, northbound_flow
    """
    macro = {}
    
    # ── VIX (FRED API) ────────────────────────────────────────
    vix = _fetch_vix_fred(start, end)
    if vix is not None and not vix.empty:
        macro["vix"] = vix

    # ── USD/CNY (akshare) ───────────────────────────────────
    try:
        _limiter_ak.acquire()
        cny = ak.currency_boc_sina(symbol="美元")
        if not cny.empty:
            date_col = "日期" if "日期" in cny.columns else "date"
            cny["date"] = pd.to_datetime(cny[date_col])
            cny = cny.set_index("date").sort_index()
            macro["usdcny"] = cny["中行折算价"].astype(float)
    except Exception as e:
        print(f"  [WARN] USD/CNY fetch: {e}")

    # ── US/CN Bond Yields (akshare bond_zh_us_rate) ──────────
    # Replaces: yf.download("^TNX"), yf.download("^FVX")
    try:
        _limiter_ak.acquire()
        bonds = ak.bond_zh_us_rate(start_date=start.replace("-", ""))
        if not bonds.empty:
            bonds["date"] = pd.to_datetime(bonds["日期"])
            bonds = bonds.set_index("date").sort_index()
            macro["yield10y"] = bonds["美国国债收益率10年"].astype(float)
            macro["yield5y"]  = bonds["美国国债收益率5年"].astype(float)
            macro["us_spread_10y2y"] = bonds["美国国债收益率10年-2年"].astype(float)
    except Exception as e:
        print(f"  [WARN] US/CN bond yield fetch: {e}")

    # ── China QVIX (50ETF options volatility index) ──────────
    try:
        _limiter_ak.acquire()
        qvix = ak.index_option_50etf_qvix()
        if not qvix.empty:
            qvix["date"] = pd.to_datetime(qvix["date"])
            qvix = qvix.set_index("date").sort_index()
            macro["china_qvix"] = qvix["close"].astype(float)
    except Exception as e:
        print(f"  [WARN] China QVIX fetch: {e}")

    # ── US Macro (akshare) ──────────────────────────────────
    try:
        _limiter_ak.acquire()
        cpi = ak.macro_usa_cpi_yoy()
        if not cpi.empty:
            date_col = "日期" if "日期" in cpi.columns else ("时间" if "时间" in cpi.columns else cpi.columns[0])
            cpi["date"] = pd.to_datetime(cpi[date_col])
            cpi = cpi.set_index("date").sort_index()
            val_col = "现值" if "现值" in cpi.columns else "今值"
            if val_col in cpi.columns:
                macro["us_cpi_yoy"] = cpi[val_col].astype(float)
    except Exception as e:
        print(f"  [WARN] US CPI fetch: {e}")

    try:
        _limiter_ak.acquire()
        unemp = ak.macro_usa_unemployment_rate()
        if not unemp.empty:
            date_col = "日期" if "日期" in unemp.columns else unemp.columns[0]
            unemp["date"] = pd.to_datetime(unemp[date_col])
            unemp = unemp.set_index("date").sort_index()
            macro["us_unemployment"] = unemp["今值"].astype(float)
    except Exception as e:
        print(f"  [WARN] US unemployment fetch: {e}")

    # ── China M2 (akshare) ──────────────────────────────────
    try:
        _limiter_ak.acquire()
        m2 = ak.macro_china_money_supply()
        if not m2.empty:
            m2["date"] = pd.to_datetime(
                m2["月份"].str.replace("年", "-").str.replace("月份", ""),
                format="%Y-%m", errors="coerce"
            )
            m2 = m2.dropna(subset=["date"]).set_index("date").sort_index()
            m2_col = "货币和准货币(M2)-同比增长"
            if m2_col in m2.columns:
                macro["china_m2_yoy"] = pd.to_numeric(m2[m2_col], errors="coerce")
    except Exception as e:
        print(f"  [WARN] China M2 fetch: {e}")

    # ── China LPR (akshare) ─────────────────────────────────
    try:
        _limiter_ak.acquire()
        lpr = ak.macro_china_lpr()
        if not lpr.empty:
            lpr["date"] = pd.to_datetime(lpr["TRADE_DATE"])
            lpr = lpr.set_index("date").sort_index()
            macro["china_lpr1y"] = lpr["LPR1Y"].astype(float)
    except Exception as e:
        print(f"  [WARN] China LPR fetch: {e}")

    # ── China CPI (akshare) ─────────────────────────────────
    try:
        _limiter_ak.acquire()
        cpi = ak.macro_china_cpi_yearly()
        if not cpi.empty:
            date_col = "日期" if "日期" in cpi.columns else cpi.columns[0]
            cpi["date"] = pd.to_datetime(cpi[date_col])
            cpi = cpi.set_index("date").sort_index()
            macro["china_cpi"] = cpi["今值"].astype(float)
    except Exception as e:
        print(f"  [WARN] China CPI fetch: {e}")

    # ── China PMI (akshare) ─────────────────────────────────
    try:
        _limiter_ak.acquire()
        pmi = ak.macro_china_pmi_yearly()
        if not pmi.empty:
            date_col = "日期" if "日期" in pmi.columns else pmi.columns[0]
            pmi["date"] = pd.to_datetime(pmi[date_col])
            pmi = pmi.set_index("date").sort_index()
            macro["china_pmi"] = pmi["今值"].astype(float)
    except Exception as e:
        print(f"  [WARN] China PMI fetch: {e}")

    # ── Northbound Flow (A-share only) ───────────────────────
    try:
        _limiter_ak.acquire()
        nb = ak.stock_hsgt_hist_em(symbol="沪股通")
        if not nb.empty:
            date_col = "日期" if "日期" in nb.columns else nb.columns[0]
            nb["date"] = pd.to_datetime(nb[date_col])
            nb = nb.set_index("date").sort_index()
            nb["net_buy"] = pd.to_numeric(nb["当日成交净买额"], errors="coerce")
            macro["northbound_flow"] = nb["net_buy"]
    except Exception as e:
        print(f"  [WARN] Northbound flow fetch: {e}")

    return macro
