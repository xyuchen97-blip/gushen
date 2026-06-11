"""
Unified Data Pipeline — fetches daily + weekly OHLCV for A-shares, HK, and US stocks.
Primary: Tushare Pro (258 APIs, reliable). Fallback: akshare/yfinance/FRED.

--- DATA SOURCES (priority order) ---
| Market     | Primary            | Fallback               |
|------------|--------------------|------------------------|
| A-share    | ts.pro_api().daily | ak.stock_zh_a_hist()   |
| HK         | ts.pro_api().hk_daily | ak.stock_hk_hist()  |
| US         | ts.pro_api().us_daily | yfinance            |
| China macro| ts.pro_api().cn_*  | ak.macro_china_*       |
| US macro   | FRED API           | —                      |
| Margin     | ts.pro_api().margin | ak.stock_margin_*     |
| Fundamentals| tradingview-screener | akshare/Tushare     |
| Validation | tradingview-ta     | —                      |
"""

import os, time, threading, pickle
import pandas as pd, numpy as np, requests
import akshare as ak, tushare as ts
from pathlib import Path

# Tushare token — read from environment variable
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
if TUSHARE_TOKEN:
    ts.set_token(TUSHARE_TOKEN)
_pro = None

def _ts():
    global _pro
    if _pro is None: _pro = ts.pro_api()
    return _pro
from datetime import datetime, timedelta

# ═══════════════════════════════════════════════════════════════════
# LOCKED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# FRED API (St. Louis Fed) — read from environment variable
from .gushen_keys import KEYS as _GK  # v15: embedded keys (sets env)
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
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
        params = {"input": key, "type": "14", "token": os.environ.get("EASTMONEY_SEARCH_TOKEN", "D43BF722C8E33BDC906FB84D85E326E8"), "count": "5"}
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

def _ts_to_ak_code(ticker: str, market: str) -> str:
    """Convert ticker to Tushare format."""
    if market == "A":
        return f"{ticker}.{'SH' if ticker.startswith('6') else 'SZ'}"
    return ticker

def _fetch_a_share(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch A-share OHLCV: Tushare primary, akshare fallback."""
    ts_code = _ts_to_ak_code(ticker, "A")
    start_fmt = start.replace("-", ""); end_fmt = end.replace("-", "")
    
    # Try Tushare first
    try:
        _limiter_ak.acquire()
        df = _ts().daily(ts_code=ts_code, start_date=start_fmt, end_date=end_fmt)
        if len(df) > 10:
            df = df.rename(columns={'trade_date':'date','vol':'volume'})
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')[['open','high','low','close','volume']]
            df = df.astype(float).sort_index()
            return df
    except Exception: pass
    
    # Fallback to akshare
    df = ak.stock_zh_a_hist(symbol=ticker, period="daily", start_date=start_fmt, end_date=end_fmt, adjust="qfq")
    return _standardize_columns(df, "A")

def _fetch_hk(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch HK OHLCV: Tushare primary, akshare fallback."""
    code = ticker.replace(".HK","").replace(".hk","").zfill(5) + ".HK"
    start_fmt = start.replace("-", ""); end_fmt = end.replace("-", "")
    
    try:
        _limiter_ak.acquire()
        df = _ts().hk_daily(ts_code=code, start_date=start_fmt, end_date=end_fmt)
        if len(df) > 10:
            df = df.rename(columns={'trade_date':'date','vol':'volume'})
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')[['open','high','low','close','volume']]
            df = df.astype(float).sort_index()
            return df
    except Exception: pass
    
    code2 = ticker.replace(".HK","").replace(".hk","").zfill(5)
    df = ak.stock_hk_hist(symbol=code2, period="daily", start_date=start_fmt, end_date=end_fmt, adjust="qfq")
    return _standardize_columns(df, "HK")

def _fetch_us(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Fetch US OHLCV (qfq-adjusted): akshare → yfinance → Tiingo → Alpha Vantage.
    
    Tier 1: ak.stock_us_daily(ticker, adjust="qfq") — Sina, qfq-adjusted, FREE, full history
    Tier 2: yfinance.download(auto_adjust=True) — free, reliable, adjusted prices
    Tier 3: Tiingo tiingo/daily/{ticker}/prices — adjOHLCV, free tier, verified matches qfq
    Tier 4: Alpha Vantage TIME_SERIES_DAILY_ADJUSTED — API key, free tier backup
    
    CRITICAL: All tiers return adjusted prices (matching A/HK qfq convention).
    Tiingo verified: AAPL adjClose=287.25 matches akshare qfq close=287.24
    """
    start_fmt = start.replace("-", ""); end_fmt = end.replace("-", "")

    # ── Tier 1: akshare Sina (qfq-adjusted, free, full history) ──
    try:
        _limiter_ak.acquire()
        df = ak.stock_us_daily(symbol=ticker, adjust="qfq")
        if df is not None and len(df) > 10:
            df = _standardize_columns(df, "US")
            return df.loc[start:end]
    except Exception:
        pass

    # ── Tier 2: yfinance (free, reliable, adjusted prices) ──
    try:
        import yfinance as yf
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(df.columns.levels[-1][0], axis=1, level=-1)
        m = {'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}
        df = df.rename(columns={k: v for k, v in m.items() if k in df.columns})
        cols = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in df.columns]
        df = df[cols]
        df.index = pd.to_datetime(df.index)
        if len(df) > 10:
            return df.sort_index()
    except Exception:
        pass

    # ── Tier 3: Tiingo (adjOHLCV, free tier, verified matches qfq) ──
    tiingo_key = os.environ.get("TIINGO_KEY", "")
    try:
        import requests
        url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
        params = {"token": tiingo_key, "startDate": start, "endDate": end}
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if isinstance(data, list) and len(data) > 10:
            rows = []
            for d in data:
                rows.append({
                    "date": d["date"][:10],
                    "open": float(d.get("adjOpen", d["open"])),
                    "high": float(d.get("adjHigh", d["high"])),
                    "low": float(d.get("adjLow", d["low"])),
                    "close": float(d.get("adjClose", d["close"])),
                    "volume": float(d.get("adjVolume", d["volume"])),
                })
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return df[["open", "high", "low", "close", "volume"]]
    except Exception:
        pass

    # ── Tier 4: Alpha Vantage (free tier backup) ──
    av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
    if av_key:
        try:
            import requests
            url = "https://www.alphavantage.co/query"
            params = {
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": ticker,
                "outputsize": "compact",
                "apikey": av_key,
                "datatype": "json",
            }
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
            ts = data.get("Time Series (Daily)", {})
            if ts and "Error Message" not in data:
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
                if len(df) > 10:
                    return df.loc[start:end][["open", "high", "low", "close", "volume"]]
        except Exception:
            pass

    raise RuntimeError(f"Failed to fetch US data for {ticker} from all sources")


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


# ═══════════════════════════════════════════════════════════════════
# TRADINGVIEW SCREENER — fast fundamentals (no auth, ~0.25s/batch)
# ═══════════════════════════════════════════════════════════════════

# TV screener columns for fundamental data
_TV_FUND_FIELDS = [
    'return_on_equity',              # percent (31.3 = 31.3%)
    'net_margin',                    # percent (47.2 = 47.2%)
    'earnings_per_share_basic_ttm',  # absolute (e.g. 66.04 CNY)
    'total_revenue_yoy_growth_fy',   # percent (6.4 = 6.4% growth)
    'net_income_yoy_growth_fy',      # percent (19.5 = 19.5% growth)
]


def _gushen_to_tv(ticker: str, market: str) -> tuple:
    """Map Gushen ticker → (tv_market, tv_symbol) for tradingview-screener.

    Gushen format → TV format:
      A:  600519.SH → china, 600519
      HK: 0700.HK  → hongkong, 700 (unpadded)
      US: AAPL      → america, AAPL
    """
    if market == "A":
        code = ticker.replace(".SH", "").replace(".SZ", "")
        return "china", code
    elif market == "HK":
        code = ticker.replace(".HK", "").lstrip("0") or "0"
        return "hongkong", code
    elif market == "US":
        return "america", ticker
    return "", ticker


def _fetch_fundamental_tv(ticker: str, market: str) -> dict:
    """Fetch fundamentals from TradingView screener (no auth, ~0.25s).

    Returns unified format matching fetch_fundamental() output contract:
      roe:            PERCENT scale (31.3 = 31.3%)
      profit_growth:  RATIO (0.195 = 19.5%)
      revenue_growth: RATIO (0.064 = 6.4%)
      profit_margin:  RATIO (0.472 = 47.2%)
      eps:            absolute (66.04)
    """
    try:
        from tradingview_screener import Query, Column
    except ImportError:
        return {}

    tv_market, tv_sym = _gushen_to_tv(ticker, market)
    if not tv_market:
        return {}

    try:
        q = (Query()
             .set_markets(tv_market)
             .select(*_TV_FUND_FIELDS)
             .where(Column('name') == tv_sym))
        count, df = q.get_scanner_data()

        if count == 0 or df is None or df.empty:
            return {}

        row = df.iloc[0]
        result = {}

        # ROE: TV percent → Gushen percent (keep as-is)
        v = row.get('return_on_equity')
        if pd.notna(v):
            result['roe'] = float(v)

        # Net margin: TV percent → Gushen RATIO (/100)
        v = row.get('net_margin')
        if pd.notna(v):
            result['profit_margin'] = float(v) / 100

        # EPS: absolute → absolute (keep as-is)
        v = row.get('earnings_per_share_basic_ttm')
        if pd.notna(v):
            result['eps'] = float(v)

        # Revenue growth YoY: TV percent → Gushen RATIO (/100)
        v = row.get('total_revenue_yoy_growth_fy')
        if pd.notna(v):
            result['revenue_growth'] = float(v) / 100

        # Profit growth YoY: TV percent → Gushen RATIO (/100)
        v = row.get('net_income_yoy_growth_fy')
        if pd.notna(v):
            result['profit_growth'] = float(v) / 100

        return result

    except Exception as e:
        print(f"  [TV] Fundamental fetch failed for {ticker}: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════
# FUNDAMENTAL DATA FETCHER (v8.3 — replaces fixed 10-point neutral)
# ═══════════════════════════════════════════════════════════════════
#
# ─── FORMAT COMPARISON: akshare vs tushare ───
#
# CRITICAL: akshare and tushare return different scales for the SAME metric.
# Both functions below normalize to a UNIFIED OUTPUT FORMAT consumed by
# scoring.py fund_score logic. The thresholds in scoring.py are:
#
#   roe > 15 → +5pt, roe > 10 → +3pt           (PERCENT SCALE: 15 = 15%)
#   profit_growth > 0.2 → +4pt, > 0 → +2pt     (RATIO: 0.2 = 20%)
#   revenue_growth > 0.15 → +4pt, > 0 → +2pt   (RATIO: 0.15 = 15%)
#   profit_margin > 0.15 → +3pt, > 0.05 → +1pt (RATIO: 0.15 = 15%)
#
# UNIFIED OUTPUT FORMAT (both functions return this):
#   roe:            PERCENT scale (e.g. 14.2 = 14.2%, 33.0 = 33.0%)
#   profit_growth:  RATIO (e.g. 0.15 = 15% growth)
#   revenue_growth: RATIO (e.g. 0.10 = 10% growth)
#   profit_margin:  RATIO (e.g. 0.30 = 30% margin)
#   eps:            Absolute value (currency-specific)
#
# ┌─────────────────┬──────────────────────────┬──────────────────────────────┐
# │ Field           │ akshare scale            │ tushare fina_indicator scale │
# ├─────────────────┼──────────────────────────┼──────────────────────────────┤
# │ ROE             │ A: percent (ROEJQ)       │ percent (roe=10.93 → 10.93%)│
# │                 │ HK: percent (ROE_AVG)    │ N/A (no fina_indicator)      │
# │                 │ US: percent (ROE_AVG)    │ N/A (no fina_indicator)      │
# ├─────────────────┼──────────────────────────┼──────────────────────────────┤
# │ Net profit YoY  │ A: computed from raw     │ percent (netprofit_yoy=11.56)│
# │                 │ HK/US: YOY field/100     │ → divide by 100 → ratio     │
# ├─────────────────┼──────────────────────────┼──────────────────────────────┤
# │ Revenue YoY     │ A: computed from raw     │ percent (or_yoy=10.54)       │
# │                 │ HK/US: YOY field/100     │ → divide by 100 → ratio     │
# ├─────────────────┼──────────────────────────┼──────────────────────────────┤
# │ Net profit margin│ A: computed from raw    │ percent (netprofit_margin=54.89)│
# │                 │ HK/US: field/100         │ → divide by 100 → ratio     │
# ├─────────────────┼──────────────────────────┼──────────────────────────────┤
# │ EPS             │ A: EPSJB (absolute)      │ eps (absolute, same scale)   │
# │                 │ HK: BASIC_EPS (absolute) │ N/A                          │
# │                 │ US: N/A                  │ N/A                          │
# └─────────────────┴──────────────────────────┴──────────────────────────────┘
#
# TUSHARE COVERAGE:
#   A-stock:  fina_indicator (full 108 columns, percent-scale, has ann_date)
#   HK:       hk_income/hk_balancesheet/hk_cashflow (long-format, NO computed metrics)
#             → Must compute ROE/YoY from raw financials, or fall back to akshare
#   US:       NO financial API in tushare → Must use akshare or yfinance
#
# PRODUCTION ROUTING (GUTS engine):
#   A-stock:  tushare fina_indicator → normalize to unified format
#   HK:       akshare (tushare HK financials lack computed metrics)
#   US:       akshare (tushare has no US financial data)
#
# BACKTEST ROUTING (tune.py):
#   All markets: akshare (consistent API, no tushare rate-limit issues)

@with_retry(max_retries=2)
def fetch_fundamental(ticker: str, market: str, source: str = "akshare") -> dict:
    """
    Fetch latest earnings quality metrics.

    OUTPUT FORMAT (unified — same regardless of source):
      - roe:            PERCENT scale (e.g. 14.2 = 14.2%)
      - profit_growth:  RATIO (e.g. 0.15 = 15% growth)
      - revenue_growth: RATIO (e.g. 0.10 = 10% growth)
      - profit_margin:  RATIO (e.g. 0.30 = 30% margin)
      - eps:            Absolute value

    Parameters
    ----------
    source : "akshare" (default) or "tushare" (production GUTS engine)

    Routing (default path):
      1. TradingView screener (fast ~0.25s, no auth, no rate-limit)
      2. Fallback: akshare (slower, rate-limited, fragile Chinese column names)

    Tushare routing: A-stock uses fina_indicator; HK/US fall back to akshare.
    """
    if source == "tushare" and market == "A":
        return _fetch_fundamental_tushare_a(ticker)

    # ── Primary: TradingView screener (fast, no rate-limit, no auth) ──
    tv = _fetch_fundamental_tv(ticker, market)
    if tv and tv.get('roe') is not None:
        return tv

    # ── Fallback: akshare (slower, rate-limited, fragile column names) ──
    result = {}
    _limiter_ak.acquire()
    
    try:
        if market == "A":
            code = f"{ticker}.{'SH' if ticker.startswith('6') else 'SZ'}"
            df = ak.stock_financial_analysis_indicator_em(symbol=code)
            if df is None or df.empty:
                return {}
            latest = df.iloc[0]
            # akshare ROEJQ is percent-scale → keep as-is
            result["roe"] = float(latest.get("ROEJQ", 0)) if latest.get("ROEJQ") and str(latest["ROEJQ"]).strip() and str(latest["ROEJQ"]) != "nan" else 0
            result["eps"] = float(latest.get("EPSJB", 0)) if latest.get("EPSJB") and str(latest["EPSJB"]).strip() and str(latest["EPSJB"]) != "nan" else 0
            # akshare A-stock: YoY must be computed from raw figures (ratio format)
            if len(df) > 4:
                prev = df.iloc[4]
                cur_np = float(latest.get("PARENTNETPROFIT", 0)) if latest.get("PARENTNETPROFIT") and str(latest["PARENTNETPROFIT"]).strip() != "nan" else 0
                prev_np = float(prev.get("PARENTNETPROFIT", 0)) if prev.get("PARENTNETPROFIT") and str(prev["PARENTNETPROFIT"]).strip() != "nan" else 0
                if prev_np > 0:
                    result["profit_growth"] = (cur_np - prev_np) / prev_np
                cur_rev = float(latest.get("OPERATEREVE", 0)) if latest.get("OPERATEREVE") and str(latest["OPERATEREVE"]).strip() != "nan" else 0
                prev_rev = float(prev.get("OPERATEREVE", 0)) if prev.get("OPERATEREVE") and str(prev["OPERATEREVE"]).strip() != "nan" else 0
                if prev_rev > 0:
                    result["revenue_growth"] = (cur_rev - prev_rev) / prev_rev
            # akshare A-stock: profit margin from raw figures (ratio format)
            cur_np2 = float(latest.get("PARENTNETPROFIT", 0)) if latest.get("PARENTNETPROFIT") and str(latest["PARENTNETPROFIT"]).strip() != "nan" else 0
            cur_rev2 = float(latest.get("OPERATEREVE", 0)) if latest.get("OPERATEREVE") and str(latest["OPERATEREVE"]).strip() != "nan" else 0
            if cur_rev2 > 0:
                result["profit_margin"] = cur_np2 / cur_rev2
                
        elif market == "HK":
            code = ticker.replace(".HK", "").zfill(5)
            df = ak.stock_financial_hk_analysis_indicator_em(symbol=code)
            if df is None or df.empty:
                return {}
            latest = df.iloc[0]
            # akshare HK: ROE_AVG is percent-scale → keep as-is
            result["roe"] = float(latest.get("ROE_AVG", 0)) if latest.get("ROE_AVG") and str(latest["ROE_AVG"]).strip() != "nan" else 0
            # akshare HK: YOY fields are percent → /100 to ratio
            result["revenue_growth"] = float(latest.get("OPERATE_INCOME_YOY", 0))/100 if latest.get("OPERATE_INCOME_YOY") and str(latest["OPERATE_INCOME_YOY"]).strip() != "nan" else 0
            # akshare HK: NET_PROFIT_RATIO is percent → /100 to ratio
            result["profit_margin"] = float(latest.get("NET_PROFIT_RATIO", 0))/100 if latest.get("NET_PROFIT_RATIO") and str(latest["NET_PROFIT_RATIO"]).strip() != "nan" else 0
            result["eps"] = float(latest.get("BASIC_EPS", 0)) if latest.get("BASIC_EPS") and str(latest["BASIC_EPS"]).strip() != "nan" else 0
            
        elif market == "US":
            df = ak.stock_financial_us_analysis_indicator_em(symbol=ticker)
            if df is None or df.empty:
                return {}
            # akshare US: ROE_AVG is percent-scale → keep as-is
            for i in range(len(df)):
                row = df.iloc[i]
                roe = row.get("ROE_AVG", 0)
                if roe and str(roe).strip() and str(roe) != "nan":
                    result["roe"] = float(roe)
                    break
            # akshare US: NET_PROFIT_RATIO is percent → /100 to ratio
            for i in range(len(df)):
                row = df.iloc[i]
                marg = row.get("NET_PROFIT_RATIO", 0)
                if marg and str(marg).strip() and str(marg) != "nan":
                    result["profit_margin"] = float(marg)/100
                    break
            # akshare US: OPERATE_INCOME_YOY is percent → /100 to ratio
            for i in range(len(df)):
                row = df.iloc[i]
                rev_g = row.get("OPERATE_INCOME_YOY", 0)
                if rev_g and str(rev_g).strip() and str(rev_g) != "nan":
                    result["revenue_growth"] = float(rev_g)/100
                    break
    except Exception as e:
        print(f"  [WARN] Fundamental fetch for {ticker}: {e}")
        return {}
    
    return result


def _fetch_fundamental_tushare_a(ticker: str) -> dict:
    """
    Fetch latest A-stock fundamentals from Tushare fina_indicator.
    
    INTERNAL — called by fetch_fundamental(source="tushare") for A-stock only.
    
    Tushare fina_indicator format (all percentage fields are PERCENT-SCALE):
      roe=10.9255 (→ 10.93%, keep as-is for scoring.py >15 threshold)
      netprofit_yoy=11.5611 (→ /100 = 0.1156 ratio)
      or_yoy=10.5415 (→ /100 = 0.1054 ratio)
      netprofit_margin=54.8895 (→ /100 = 0.5489 ratio)
      eps=21.38 (absolute)
    """
    result = {}
    ticker = ticker.replace(".SH", "").replace(".SZ", "")
    ts_code = f"{ticker}.{'SH' if ticker.startswith('6') else 'SZ'}"
    
    try:
        df = _ts().fina_indicator(ts_code=ts_code, fields='ts_code,ann_date,end_date,roe,netprofit_yoy,or_yoy,netprofit_margin,eps')
        if df is None or df.empty:
            return {}
        
        latest = df.iloc[0]
        
        # ROE: tushare percent-scale → keep as-is (matches scoring.py >15 threshold)
        roe = latest.get("roe", None)
        result["roe"] = float(roe) if roe is not None and str(roe) not in ("nan", "None", "") else 0
        
        # Profit growth: tushare netprofit_yoy is PERCENT → /100 to ratio
        np_yoy = latest.get("netprofit_yoy", None)
        result["profit_growth"] = float(np_yoy) / 100.0 if np_yoy is not None and str(np_yoy) not in ("nan", "None", "") else 0
        
        # Revenue growth: tushare or_yoy is PERCENT → /100 to ratio
        or_yoy = latest.get("or_yoy", None)
        result["revenue_growth"] = float(or_yoy) / 100.0 if or_yoy is not None and str(or_yoy) not in ("nan", "None", "") else 0
        
        # Profit margin: tushare netprofit_margin is PERCENT → /100 to ratio
        npm = latest.get("netprofit_margin", None)
        result["profit_margin"] = float(npm) / 100.0 if npm is not None and str(npm) not in ("nan", "None", "") else 0
        
        # EPS: absolute value
        eps = latest.get("eps", None)
        result["eps"] = float(eps) if eps is not None and str(eps) not in ("nan", "None", "") else 0
        
    except Exception as e:
        print(f"  [WARN] Tushare fundamental fetch for {ticker}: {e}")
        return {}
    
    return result


def fetch_fundamental_timeseries(ticker: str, market: str) -> pd.DataFrame:
    """
    Fetch quarterly fundamental time series for backtest.
    
    Returns DataFrame indexed by NOTICE_DATE (disclosure date) with columns:
      - roe: Return on Equity (%)
      - profit_growth: Net profit YoY growth (ratio, e.g. 0.15 = 15%)
      - revenue_growth: Revenue YoY growth (ratio)
      - profit_margin: Net profit margin (ratio, e.g. 0.30 = 30%)
      - eps: Basic EPS
    
    Uses NOTICE_DATE (disclosure date) as index to avoid look-ahead bias.
    Falls back to REPORT_DATE + 45 days if NOTICE_DATE unavailable.
    """
    rows = []
    # Normalize A-stock ticker: strip existing suffix to avoid double-appending
    if market == "A":
        ticker = ticker.replace(".SH", "").replace(".SZ", "")
    
    try:
        if market == "A":
            code = f"{ticker}.{'SH' if ticker.startswith('6') else 'SZ'}"
            _limiter_ak.acquire()
            df = ak.stock_financial_analysis_indicator_em(symbol=code)
            if df is None or df.empty:
                return pd.DataFrame()
            
            for idx in range(min(len(df), 20)):  # last 20 quarters (5 years)
                row = df.iloc[idx]
                # Use NOTICE_DATE (actual disclosure), fallback to REPORT_DATE + 45 days
                notice = row.get("NOTICE_DATE", row.get("UPDATE_DATE", None))
                report = row.get("REPORT_DATE", None)
                if notice and str(notice).strip() and str(notice) != "nan":
                    disc_date = pd.Timestamp(notice)
                elif report and str(report).strip() and str(report) != "nan":
                    disc_date = pd.Timestamp(report) + pd.Timedelta(days=45)
                else:
                    continue
                
                rec = {"disc_date": disc_date}
                rec["roe"] = float(row.get("ROEJQ", 0)) if row.get("ROEJQ") and str(row["ROEJQ"]).strip() and str(row["ROEJQ"]) != "nan" else None
                
                # YoY growth: compare with same quarter last year (row+4)
                if idx + 4 < len(df):
                    cur_np = row.get("PARENTNETPROFIT", 0)
                    prev_np = df.iloc[idx+4].get("PARENTNETPROFIT", 0)
                    if cur_np and prev_np and str(cur_np) != "nan" and str(prev_np) != "nan":
                        cur_np, prev_np = float(cur_np), float(prev_np)
                        if prev_np > 0:
                            rec["profit_growth"] = (cur_np - prev_np) / prev_np
                    
                    cur_rev = row.get("OPERATEREVE", 0)
                    prev_rev = df.iloc[idx+4].get("OPERATEREVE", 0)
                    if cur_rev and prev_rev and str(cur_rev) != "nan" and str(prev_rev) != "nan":
                        cur_rev, prev_rev = float(cur_rev), float(prev_rev)
                        if prev_rev > 0:
                            rec["revenue_growth"] = (cur_rev - prev_rev) / prev_rev
                
                # Profit margin
                np_val = row.get("PARENTNETPROFIT", 0)
                rev_val = row.get("OPERATEREVE", 0)
                if np_val and rev_val and str(np_val) != "nan" and str(rev_val) != "nan":
                    np_val, rev_val = float(np_val), float(rev_val)
                    if rev_val > 0:
                        rec["profit_margin"] = np_val / rev_val
                
                rec["eps"] = float(row.get("EPSJB", 0)) if row.get("EPSJB") and str(row["EPSJB"]).strip() and str(row["EPSJB"]) != "nan" else None
                rows.append(rec)
                
        elif market == "HK":
            code = ticker.replace(".HK", "").zfill(5)
            _limiter_ak.acquire()
            df = ak.stock_financial_hk_analysis_indicator_em(symbol=code)
            if df is None or df.empty:
                return pd.DataFrame()
            
            for idx in range(min(len(df), 20)):
                row = df.iloc[idx]
                # HK data may not have NOTICE_DATE; use REPORT_DATE + 60 days
                report = row.get("REPORT_DATE", None)
                if report and str(report).strip() and str(report) != "nan":
                    disc_date = pd.Timestamp(report) + pd.Timedelta(days=60)
                else:
                    continue
                
                rec = {"disc_date": disc_date}
                rec["roe"] = float(row.get("ROE_AVG", 0)) if row.get("ROE_AVG") and str(row["ROE_AVG"]).strip() != "nan" else None  # percent scale
                rec["revenue_growth"] = float(row.get("OPERATE_INCOME_YOY", 0))/100 if row.get("OPERATE_INCOME_YOY") and str(row["OPERATE_INCOME_YOY"]).strip() != "nan" else None
                rec["profit_margin"] = float(row.get("NET_PROFIT_RATIO", 0))/100 if row.get("NET_PROFIT_RATIO") and str(row["NET_PROFIT_RATIO"]).strip() != "nan" else None
                rec["eps"] = float(row.get("BASIC_EPS", 0)) if row.get("BASIC_EPS") and str(row["BASIC_EPS"]).strip() != "nan" else None
                rows.append(rec)
                
        elif market == "US":
            _limiter_ak.acquire()
            df = ak.stock_financial_us_analysis_indicator_em(symbol=ticker)
            if df is None or df.empty:
                return pd.DataFrame()
            
            for idx in range(min(len(df), 20)):
                row = df.iloc[idx]
                report = row.get("REPORT_DATE", None)
                if report and str(report).strip() and str(report) != "nan":
                    disc_date = pd.Timestamp(report) + pd.Timedelta(days=45)
                else:
                    continue
                
                rec = {"disc_date": disc_date}
                roe = row.get("ROE_AVG", 0)
                rec["roe"] = float(roe) if roe and str(roe).strip() and str(roe) != "nan" else None  # percent scale
                marg = row.get("NET_PROFIT_RATIO", 0)
                rec["profit_margin"] = float(marg)/100 if marg and str(marg).strip() and str(marg) != "nan" else None
                rev_g = row.get("OPERATE_INCOME_YOY", 0)
                rec["revenue_growth"] = float(rev_g)/100 if rev_g and str(rev_g).strip() and str(rev_g) != "nan" else None
                rows.append(rec)
    except Exception as e:
        # Common: akshare upstream returns None for some US stocks (e.g. JPM)
        if 'NoneType' in str(e):
            print(f"  [WARN] Fundamental TS fetch for {ticker}: upstream data unavailable (akshare returned None)")
        else:
            print(f"  [WARN] Fundamental TS fetch for {ticker}: {e}")
        return pd.DataFrame()
    
    if not rows:
        return pd.DataFrame()
    
    result = pd.DataFrame(rows).set_index("disc_date").sort_index()
    return result


# ═══════════════════════════════════════════════════════════════════
# TUSHARE FUNDAMENTAL TIMESERIES (Production GUTS engine — A-stock only)
# ═══════════════════════════════════════════════════════════════════
#
# This function uses Tushare fina_indicator for A-stock fundamental data.
# It is the PRODUCTION path for the GUTS engine.
#
# KEY FORMAT DIFFERENCES from akshare version:
#   1. Tushare provides ann_date (disclosure date) natively — no need to estimate
#   2. All percentage fields are PERCENT-SCALE in tushare (e.g. roe=10.93 = 10.93%)
#      → ROE: keep as-is (percent), matching scoring.py threshold >15
#      → netprofit_yoy / or_yoy / netprofit_margin: DIVIDE BY 100 → ratio format
#   3. Tushare provides computed YoY directly (netprofit_yoy, or_yoy) — no need
#      to manually calculate from raw profit/revenue figures
#   4. Tushare HK/US: NO fina_indicator coverage — must use akshare fallback
#
# For HK and US stocks in production, the akshare fetch_fundamental_timeseries()
# function above remains the data source.

@with_retry(max_retries=2)
def fetch_fundamental_timeseries_tushare(ticker: str, market: str) -> pd.DataFrame:
    """
    Fetch quarterly fundamental time series from Tushare fina_indicator.
    
    PRODUCTION GUTS ENGINE — A-stock only.
    HK/US stocks fall back to akshare (fetch_fundamental_timeseries).
    
    Returns DataFrame indexed by ann_date (disclosure date) with columns:
      - roe:            PERCENT scale (e.g. 14.2 = 14.2%) — matches scoring.py threshold >15
      - profit_growth:  RATIO (e.g. 0.15 = 15% growth) — from netprofit_yoy / 100
      - revenue_growth: RATIO (e.g. 0.10 = 10% growth) — from or_yoy / 100
      - profit_margin:  RATIO (e.g. 0.30 = 30% margin) — from netprofit_margin / 100
      - eps:            Absolute value (currency-specific)
    
    Tushare fina_indicator format reference (A-stock, verified 2026-05-13):
      roe              = 10.9255  (percent: 10.93%)
      netprofit_yoy    = 11.5611  (percent: 11.56% → /100 = 0.1156 ratio)
      or_yoy           = 10.5415  (percent: 10.54% → /100 = 0.1054 ratio)
      netprofit_margin = 54.8895  (percent: 54.89% → /100 = 0.5489 ratio)
      eps              = 21.38    (absolute: ¥21.38 per share)
      ann_date         = 20250430 (YYYYMMDD — actual disclosure date, no estimation needed)
      end_date         = 20250331 (YYYYMMDD — fiscal period end)
    """
    # ── HK/US: Fall back to akshare (tushare has no fina_indicator for these) ──
    if market in ("HK", "US"):
        return fetch_fundamental_timeseries(ticker, market)
    
    # ── A-stock: Tushare fina_indicator ──
    # Normalize ticker: strip existing suffix
    ticker = ticker.replace(".SH", "").replace(".SZ", "")
    ts_code = f"{ticker}.{'SH' if ticker.startswith('6') else 'SZ'}"
    
    rows = []
    try:
        df = _ts().fina_indicator(ts_code=ts_code, start_date="20200101", end_date="20991231")
        if df is None or df.empty:
            # Fallback to akshare
            return fetch_fundamental_timeseries(ticker, "A")
        
        for idx in range(min(len(df), 20)):  # last 20 quarters (5 years)
            row = df.iloc[idx]
            
            # Disclosure date: ann_date is the ACTUAL disclosure date from exchange
            ann_date = row.get("ann_date", None)
            if ann_date and str(ann_date).strip() and str(ann_date) != "nan":
                disc_date = pd.Timestamp(str(ann_date))
            else:
                # Fallback: end_date + 45 days (similar to akshare logic)
                end_d = row.get("end_date", None)
                if end_d and str(end_d).strip() and str(end_d) != "nan":
                    disc_date = pd.Timestamp(str(end_d)) + pd.Timedelta(days=45)
                else:
                    continue
            
            rec = {"disc_date": disc_date}
            
            # ROE: tushare percent-scale → keep as-is (matches scoring.py >15 threshold)
            roe = row.get("roe", None)
            rec["roe"] = float(roe) if roe is not None and str(roe) not in ("nan", "None", "") else None
            
            # Profit growth: tushare netprofit_yoy is PERCENT → /100 to ratio
            np_yoy = row.get("netprofit_yoy", None)
            rec["profit_growth"] = float(np_yoy) / 100.0 if np_yoy is not None and str(np_yoy) not in ("nan", "None", "") else None
            
            # Revenue growth: tushare or_yoy is PERCENT → /100 to ratio
            or_yoy = row.get("or_yoy", None)
            rec["revenue_growth"] = float(or_yoy) / 100.0 if or_yoy is not None and str(or_yoy) not in ("nan", "None", "") else None
            
            # Profit margin: tushare netprofit_margin is PERCENT → /100 to ratio
            npm = row.get("netprofit_margin", None)
            rec["profit_margin"] = float(npm) / 100.0 if npm is not None and str(npm) not in ("nan", "None", "") else None
            
            # EPS: absolute value, same scale
            eps = row.get("eps", None)
            rec["eps"] = float(eps) if eps is not None and str(eps) not in ("nan", "None", "") else None
            
            rows.append(rec)
    
    except Exception as e:
        print(f"  [WARN] Tushare fundamental TS fetch for {ticker}: {e}")
        # Fallback to akshare
        return fetch_fundamental_timeseries(ticker, "A")
    
    if not rows:
        return pd.DataFrame()
    
    result = pd.DataFrame(rows).set_index("disc_date").sort_index()
    return result
