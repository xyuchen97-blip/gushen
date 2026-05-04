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
# US STOCK SYMBOL MAPPING (on-demand via Eastmoney search API)
# ═══════════════════════════════════════════════════════════════════

def _get_us_symbol(ticker: str) -> str:
    """Convert plain ticker (MSFT) to Eastmoney symbol (105.MSFT).
    Uses disk cache → search API → spot_em fallback, in that order."""
    global _us_symbol_map
    
    # Check in-memory cache
    key = ticker.upper().strip()
    if key in _us_symbol_map:
        return _us_symbol_map[key]
    
    # Check disk cache
    cache_file = CACHE_DIR / "us_symbol_map.json"
    if cache_file.exists():
        try:
            import json
            with open(cache_file) as f:
                disk_map = json.load(f)
            if key in disk_map:
                _us_symbol_map[key] = disk_map[key]
                return disk_map[key]
        except Exception:
            pass
    
    # Eastmoney search API (~300ms, on-demand)
    try:
        import requests
        url = "https://searchadapter.eastmoney.com/api/suggest/get"
        params = {"input": key, "type": "14", "token": "D43BF722C8E33BDC906FB84D85E326E8", "count": "5"}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        items = data.get("QuotationCodeTable", {}).get("Data", [])
        for item in items:
            if item.get("Code") and item.get("MktNum"):
                symbol = f'{item["MktNum"]}.{item["Code"]}'
                name = item.get("Name", "").upper()
                code = item["Code"].upper()
                if code == key or name == key:
                    _us_symbol_map[key] = symbol
                    # Persist to disk
                    try:
                        import json
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        existing = {}
                        if cache_file.exists():
                            with open(cache_file) as f:
                                existing = json.load(f)
                        existing[key] = symbol
                        with open(cache_file, "w") as f:
                            json.dump(existing, f)
                    except Exception:
                        pass
                    return symbol
    except Exception as e:
        pass  # Fall through to pass-through
    
    # Fallback: pass plain ticker (may work on some akshare versions)
    _us_symbol_map[key] = ticker
    return ticker


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

    # ── USD/CNY (FRED API DEXCHUS — replaces stale currency_boc_sina) ──
    try:
        _limiter_fred.acquire()
        resp = requests.get(FRED_BASE_URL, params={
            "series_id": "DEXCHUS",
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "asc",
            "observation_start": start,
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        obs = data.get("observations", [])
        if obs:
            vals = [float(o["value"]) if o["value"] != "." else np.nan for o in obs]
            dates = [pd.Timestamp(o["date"]) for o in obs]
            macro["usdcny"] = pd.Series(vals, index=pd.DatetimeIndex(dates), name="usdcny").sort_index().dropna()
    except Exception as e:
        print(f"  [WARN] USD/CNY (FRED) fetch: {e}")

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

    # ── US Unemployment (FRED UNRATE — replaces stale macro_usa_unemployment_rate) ──
    try:
        _limiter_fred.acquire()
        resp = requests.get(FRED_BASE_URL, params={
            "series_id": "UNRATE",
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "asc",
            "observation_start": start,
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        obs = data.get("observations", [])
        if obs:
            vals = [float(o["value"]) if o["value"] != "." else np.nan for o in obs]
            dates = [pd.Timestamp(o["date"]) for o in obs]
            macro["us_unemployment"] = pd.Series(vals, index=pd.DatetimeIndex(dates), name="unrate").sort_index().dropna()
    except Exception as e:
        print(f"  [WARN] US Unemployment (FRED) fetch: {e}")

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

    # ── China CPI (akshare macro_china_cpi — live to 2026-03) ──
    try:
        _limiter_ak.acquire()
        cpi = ak.macro_china_cpi()
        if not cpi.empty:
            cpi["date"] = pd.to_datetime(
                cpi["月份"].str.replace("年", "-").str.replace("月份", ""),
                format="%Y-%m", errors="coerce"
            )
            cpi = cpi.dropna(subset=["date"]).set_index("date").sort_index()
            macro["china_cpi"] = cpi["全国-同比增长"].astype(float)
    except Exception as e:
        print(f"  [WARN] China CPI fetch: {e}")

    # ── China PMI (Caixin Manufacturing — replaces stale macro_china_pmi_yearly) ──
    try:
        _limiter_ak.acquire()
        pmi = ak.index_pmi_man_cx()
        if not pmi.empty:
            pmi["date"] = pd.to_datetime(pmi["日期"])
            pmi = pmi.set_index("date").sort_index()
            macro["china_pmi"] = pmi["制造业PMI"].astype(float)
    except Exception as e:
        print(f"  [WARN] China PMI (Caixin) fetch: {e}")

    # ── Northbound Flow (stock_hsgt_fund_flow_summary_em — live daily snapshot) ──
    try:
        _limiter_ak.acquire()
        nb = ak.stock_hsgt_fund_flow_summary_em()
        if not nb.empty:
            # Filter: 沪股通+深股通, 北向 only, sum net buy
            nb_flow = nb[(nb["板块"].isin(["沪股通", "深股通"])) & (nb["资金方向"] == "北向")]
            if not nb_flow.empty:
                date = pd.Timestamp(nb_flow.iloc[0]["交易日"])
                net = nb_flow["成交净买额"].sum()
                macro["northbound_flow"] = pd.Series([net], index=[date], name="northbound")
    except Exception as e:
        print(f"  [WARN] Northbound flow fetch: {e}")

    return macro
