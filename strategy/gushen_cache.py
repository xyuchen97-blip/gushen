#!/usr/bin/env python3
"""Gushen Cache Layer — 股神修炼模式专用 (Tune mode only).
       ⛔ NEVER import in data_fetcher, daily_digest, or analyze.
       ✅ Only used by strategy/tune.py when TUNE_MODE = True.

       Production scoring uses live APIs via data_fetcher.py.
       Cache exists solely for fast backtest iteration during 修炼.

       v2.0 — Full macro + fundamental + OHLCV caching.
       All backtest data lives in SQLite; no re-downloading on repeat runs."""

import sqlite3, pandas as pd, numpy as np, tushare as ts, os, requests
from pathlib import Path
from datetime import datetime

TUNE_MODE = os.environ.get("GUSHEN_TUNE", "0") == "1"
if not TUNE_MODE:
    raise RuntimeError("gushen_cache is 修炼模式专用. Set GUSHEN_TUNE=1 to use.")

DB_PATH = Path(os.environ.get("GUSHEN_DB_PATH", str(Path(__file__).parent.parent / "data" / "gushen.db")))
TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# FRED API (for VIX, USD/CNY, US unemployment)
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

if TOKEN:
    ts.set_token(TOKEN)
_pro = None

def pro():
    global _pro
    if _pro is None: _pro = ts.pro_api()
    return _pro

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            ticker TEXT, date TEXT, market TEXT,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (ticker, date)
        );
        CREATE TABLE IF NOT EXISTS margin (
            code TEXT, date TEXT,
            margin_balance REAL, margin_buy REAL,
            PRIMARY KEY (code, date)
        );
        CREATE TABLE IF NOT EXISTS mff (
            code TEXT, date TEXT,
            super_lg_net REAL, lg_net REAL,
            PRIMARY KEY (code, date)
        );
        CREATE TABLE IF NOT EXISTS macro (
            series TEXT, date TEXT,
            value REAL,
            PRIMARY KEY (series, date)
        );
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker TEXT, disc_date TEXT, market TEXT,
            roe REAL, profit_growth REAL, revenue_growth REAL,
            profit_margin REAL, eps REAL,
            PRIMARY KEY (ticker, disc_date)
        );
        CREATE TABLE IF NOT EXISTS valuation (
            code TEXT, date TEXT,
            pe REAL, pb REAL, total_mv REAL,
            PRIMARY KEY (code, date)
        );
        CREATE TABLE IF NOT EXISTS holders (
            code TEXT, end_date TEXT,
            holder_num REAL, holder_chg REAL,
            PRIMARY KEY (code, end_date)
        );
        CREATE TABLE IF NOT EXISTS cyq_chips (
            code TEXT, trade_date TEXT,
            price REAL, percent REAL,
            PRIMARY KEY (code, trade_date, price)
        );
        CREATE TABLE IF NOT EXISTS events (
            code TEXT, event_date TEXT, event_type TEXT,
            detail TEXT,
            PRIMARY KEY (code, event_date, event_type)
        );
        CREATE TABLE IF NOT EXISTS analyst_signals (
            ticker TEXT, signal_date TEXT, market TEXT,
            signal_type TEXT, signal_value REAL, detail TEXT,
            PRIMARY KEY (ticker, signal_date, signal_type)
        );
        CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date);
        CREATE INDEX IF NOT EXISTS idx_margin_date ON margin(date);
        CREATE INDEX IF NOT EXISTS idx_macro_date ON macro(date);
        CREATE INDEX IF NOT EXISTS idx_macro_series ON macro(series);
        CREATE INDEX IF NOT EXISTS idx_fund_ticker ON fundamentals(ticker);
        CREATE INDEX IF NOT EXISTS idx_analyst_ticker ON analyst_signals(ticker, market);
    """)
    conn.commit(); conn.close()
    print("DB initialized.")

def build_ohlcv_cache(stocks_a, stocks_hk, stocks_us):
    """Build full OHLCV cache from Tushare (primary) + yfinance (fallback)."""
    conn = sqlite3.connect(str(DB_PATH))
    
    # A-stocks from Tushare
    for code in stocks_a:
        ts_code = f"{code}.{'SH' if code.startswith('6') else 'SZ'}"
        existing = conn.execute("SELECT MAX(date) FROM ohlcv WHERE ticker=?", (ts_code,)).fetchone()[0]
        if existing: continue
        try:
            df = pro().daily(ts_code=ts_code, start_date='20210101', end_date='20260506')
            if len(df) > 0:
                df = df.rename(columns={'trade_date':'date'})
                df['date'] = df['date'].astype(str)
                for _, row in df.iterrows():
                    conn.execute("INSERT OR REPLACE INTO ohlcv VALUES(?,?,?,?,?,?,?,?)",
                        (ts_code, row['date'], 'A', float(row['open']), float(row['high']),
                         float(row['low']), float(row['close']), float(row['vol'])))
                print(f"  {ts_code}: {len(df)} rows from Tushare")
        except Exception as e: print(f"  {ts_code}: {e}")

    # HK from yfinance (akshare stock_hk_hist as primary in production)
    import yfinance as yf
    for ticker in stocks_hk:
        existing = conn.execute("SELECT MAX(date) FROM ohlcv WHERE ticker=?", (ticker,)).fetchone()[0]
        if existing: continue
        try:
            df = yf.download(ticker, start='2021-01-01', end='2026-05-06', progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex): df = df.xs(df.columns.levels[-1][0], axis=1, level=-1)
            m = {'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'}
            df = df.rename(columns={k:v for k,v in m.items() if k in df.columns})
            for idx, row in df.iterrows():
                conn.execute("INSERT OR REPLACE INTO ohlcv VALUES(?,?,?,?,?,?,?,?)",
                    (ticker, str(idx.date()), 'HK', float(row['open']), float(row['high']),
                     float(row['low']), float(row['close']), float(row['volume'])))
            print(f"  {ticker}: {len(df)} rows from yfinance")
        except Exception as e: print(f"  {ticker}: {e}")

    # US from akshare stock_us_daily (qfq-adjusted, trimmed to 2021+)
    import yfinance as yf
    try:
        import akshare as ak
    except ImportError:
        ak = None
    for ticker in stocks_us:
        existing = conn.execute("SELECT MAX(date) FROM ohlcv WHERE ticker=?", (ticker,)).fetchone()[0]
        if existing: continue
        df = None
        try:
            if ak:
                df = ak.stock_us_daily(symbol=ticker, adjust="qfq")
                if df is not None and len(df) > 10:
                    df = df.rename(columns={
                        "date": "date", "open": "open", "high": "high",
                        "low": "low", "close": "close", "volume": "volume"
                    })
                    if "date" in df.columns:
                        df["date"] = pd.to_datetime(df["date"])
                        df = df.set_index("date")
                    else:
                        df.index = pd.to_datetime(df.index)
                    # Trim to 2021+ — same as A/HK backtest range
                    df = df[df.index >= '2021-01-01']
                    print(f"  {ticker}: {len(df)} rows from akshare (qfq, 2021+)")
        except Exception:
            df = None
        if df is None:
            df = yf.download(ticker, start='2021-01-01', end='2026-05-06', progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex): df = df.xs(df.columns.levels[-1][0], axis=1, level=-1)
            m = {'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'}
            df = df.rename(columns={k:v for k,v in m.items() if k in df.columns})
            print(f"  {ticker}: {len(df)} rows from yfinance (backup)")
        for idx, row in df.iterrows():
            conn.execute("INSERT OR REPLACE INTO ohlcv VALUES(?,?,?,?,?,?,?,?)",
                (ticker, str(idx.date()), 'US',
                 float(row.iloc[0]), float(row.iloc[1]),
                 float(row.iloc[2]), float(row.iloc[3]),
                 float(row.iloc[4])))

    conn.commit(); conn.close()

def get_ohlcv(ticker, market):
    """Read OHLCV from cache. Returns DataFrame or None."""
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql("SELECT date,open,high,low,close,volume FROM ohlcv WHERE ticker=? ORDER BY date", 
                     conn, params=(ticker,), parse_dates=['date'], index_col='date')
    conn.close()
    return df if len(df) > 0 else None

def _fetch_fred_series(series_id, start='2021-01-01'):
    """Fetch a FRED series and return list of (date_str, value) tuples."""
    try:
        resp = requests.get(FRED_BASE_URL, params={
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "asc",
            "observation_start": start,
        }, timeout=30)
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        return [(o["date"], float(o["value"]) if o["value"] != "." else None) for o in obs]
    except Exception as e:
        print(f"  [WARN] FRED {series_id}: {e}")
        return []

def build_macro_cache(force=False):
    """Build full macro cache — all 13 series used by scoring engine.
    
    Cached series: vix, usdcny, yield10y, yield5y, us_spread_10y2y,
    us_cpi_yoy, us_unemployment, china_lpr1y, china_cpi, china_pmi,
    china_m2_yoy, china_qvix, northbound_flow
    
    If force=True, re-fetch even if cache exists."""
    import akshare as ak
    conn = sqlite3.connect(str(DB_PATH))
    
    def _has(series):
        if force: return False
        return conn.execute("SELECT 1 FROM macro WHERE series=? LIMIT 1", (series,)).fetchone() is not None
    
    def _write(series, rows):
        """Write list of (date_str, value) pairs to macro table. Skip None values."""
        n = 0
        for date_str, val in rows:
            if val is not None:
                conn.execute("INSERT OR REPLACE INTO macro VALUES(?,?,?)", (series, date_str, float(val)))
                n += 1
        print(f"  {series}: {n} rows cached")
    
    # ── VIX (FRED) ──
    if not _has('vix'):
        _write('vix', _fetch_fred_series('VIXCLS'))
    
    # ── USD/CNY (FRED DEXCHUS) ──
    if not _has('usdcny'):
        _write('usdcny', _fetch_fred_series('DEXCHUS'))
    
    # ── US Unemployment (FRED UNRATE) ──
    if not _has('us_unemployment'):
        _write('us_unemployment', _fetch_fred_series('UNRATE'))
    
    # ── US/CN Bond Yields (akshare) ──
    for series_name in ('yield10y', 'yield5y', 'us_spread_10y2y'):
        if not _has(series_name):
            break
    else:
        series_name = None
    if series_name is not None or force:
        try:
            bonds = ak.bond_zh_us_rate(start_date='20210101')
            if not bonds.empty:
                bonds["date"] = pd.to_datetime(bonds["日期"])
                bonds = bonds.set_index("date").sort_index()
                for col, series_key in [("美国国债收益率10年", "yield10y"), 
                                         ("美国国债收益率5年", "yield5y"),
                                         ("美国国债收益率10年-2年", "us_spread_10y2y")]:
                    if col in bonds.columns and not _has(series_key):
                        rows = [(str(d.date()), float(v)) for d, v in bonds[col].dropna().items()]
                        _write(series_key, rows)
        except Exception as e:
            print(f"  [WARN] Bond yields: {e}")
    
    # ── US CPI YoY (akshare) ──
    if not _has('us_cpi_yoy'):
        try:
            cpi = ak.macro_usa_cpi_yoy()
            if not cpi.empty:
                date_col = "日期" if "日期" in cpi.columns else ("时间" if "时间" in cpi.columns else cpi.columns[0])
                val_col = "现值" if "现值" in cpi.columns else "今值"
                if val_col in cpi.columns:
                    cpi["date"] = pd.to_datetime(cpi[date_col])
                    cpi = cpi.set_index("date").sort_index()
                    rows = [(str(d.date()), float(v)) for d, v in cpi[val_col].dropna().items()]
                    _write('us_cpi_yoy', rows)
        except Exception as e:
            print(f"  [WARN] US CPI: {e}")
    
    # ── China M2 YoY (akshare) ──
    if not _has('china_m2_yoy'):
        try:
            m2 = ak.macro_china_money_supply()
            if not m2.empty:
                m2["date"] = pd.to_datetime(
                    m2["月份"].str.replace("年", "-").str.replace("月份", ""),
                    format="%Y-%m", errors="coerce"
                )
                m2 = m2.dropna(subset=["date"]).set_index("date").sort_index()
                m2_col = "货币和准货币(M2)-同比增长"
                if m2_col in m2.columns:
                    rows = [(str(d.date()), float(v)) for d, v in pd.to_numeric(m2[m2_col], errors="coerce").dropna().items()]
                    _write('china_m2_yoy', rows)
        except Exception as e:
            print(f"  [WARN] China M2: {e}")
    
    # ── China LPR 1Y (akshare) ──
    if not _has('china_lpr1y'):
        try:
            lpr = ak.macro_china_lpr()
            if not lpr.empty:
                lpr["date"] = pd.to_datetime(lpr["TRADE_DATE"])
                lpr = lpr.set_index("date").sort_index()
                rows = [(str(d.date()), float(v)) for d, v in lpr["LPR1Y"].dropna().items()]
                _write('china_lpr1y', rows)
        except Exception as e:
            print(f"  [WARN] China LPR: {e}")
    
    # ── China CPI (akshare) ──
    if not _has('china_cpi'):
        try:
            cpi = ak.macro_china_cpi()
            if not cpi.empty:
                cpi["date"] = pd.to_datetime(
                    cpi["月份"].str.replace("年", "-").str.replace("月份", ""),
                    format="%Y-%m", errors="coerce"
                )
                cpi = cpi.dropna(subset=["date"]).set_index("date").sort_index()
                rows = [(str(d.date()), float(v)) for d, v in cpi["全国-同比增长"].dropna().items()]
                _write('china_cpi', rows)
        except Exception as e:
            print(f"  [WARN] China CPI: {e}")
    
    # ── China PMI (Caixin, akshare) ──
    if not _has('china_pmi'):
        try:
            pmi = ak.index_pmi_man_cx()
            if not pmi.empty:
                pmi["date"] = pd.to_datetime(pmi["日期"])
                pmi = pmi.set_index("date").sort_index()
                rows = [(str(d.date()), float(v)) for d, v in pmi["制造业PMI"].dropna().items()]
                _write('china_pmi', rows)
        except Exception as e:
            print(f"  [WARN] China PMI: {e}")
    
    # ── China QVIX (50ETF options vol) ──
    if not _has('china_qvix'):
        try:
            qvix = ak.index_option_50etf_qvix()
            if not qvix.empty:
                qvix["date"] = pd.to_datetime(qvix["date"])
                qvix = qvix.set_index("date").sort_index()
                rows = [(str(d.date()), float(v)) for d, v in qvix["close"].dropna().items()]
                _write('china_qvix', rows)
        except Exception as e:
            print(f"  [WARN] China QVIX: {e}")
    
    # ── Northbound Flow (akshare — latest daily snapshot) ──
    # This is a point-in-time snapshot, always refresh
    try:
        nb = ak.stock_hsgt_fund_flow_summary_em()
        if not nb.empty:
            nb_flow = nb[(nb["板块"].isin(["沪股通", "深股通"])) & (nb["资金方向"] == "北向")]
            if not nb_flow.empty:
                date = str(pd.Timestamp(nb_flow.iloc[0]["交易日"]).date())
                net = float(nb_flow["成交净买额"].sum())
                conn.execute("INSERT OR REPLACE INTO macro VALUES(?,?,?)", ('northbound_flow', date, net))
                print(f"  northbound_flow: 1 row cached ({date})")
    except Exception as e:
        print(f"  [WARN] Northbound flow: {e}")
    
    conn.commit(); conn.close()


def get_macro_data(start='2021-01-01', end='2026-05-06'):
    """Read macro data from cache. Returns dict[str, pd.Series].
    
    Format matches data_fetcher.fetch_macro_data() output.
    Falls back to build_macro_cache() on empty cache."""
    conn = sqlite3.connect(str(DB_PATH))
    
    # Check if we have any macro data
    count = conn.execute("SELECT COUNT(*) FROM macro").fetchone()[0]
    if count == 0:
        conn.close()
        print("  [macro cache empty — building...]")
        build_macro_cache()
        conn = sqlite3.connect(str(DB_PATH))
    
    macro = {}
    for series_name in ['vix', 'usdcny', 'yield10y', 'yield5y', 'us_spread_10y2y',
                         'us_cpi_yoy', 'us_unemployment', 'china_lpr1y', 'china_cpi',
                         'china_pmi', 'china_m2_yoy', 'china_qvix', 'northbound_flow']:
        df = pd.read_sql("SELECT date, value FROM macro WHERE series=? AND date >= ? AND date <= ? ORDER BY date",
                         conn, params=(series_name, start, end), parse_dates=['date'])
        if not df.empty:
            macro[series_name] = pd.Series(df['value'].values, 
                                           index=pd.DatetimeIndex(df['date']),
                                           name=series_name).dropna()
    
    conn.close()
    return macro

def build_holders_cache(stocks_a):
    """Build 股东人数 cache."""
    conn = sqlite3.connect(str(DB_PATH))
    for code in stocks_a:
        ts_code = f"{code}.{'SH' if code.startswith('6') else 'SZ'}"
        existing = conn.execute("SELECT MAX(end_date) FROM holders WHERE code=?", (ts_code,)).fetchone()[0]
        try:
            df = pro().stk_holdernumber(ts_code=ts_code, start_date='20210101', end_date='20260506')
            if len(df) > 0:
                df = df.sort_values('end_date')
                df['holder_chg'] = df['holder_num'].astype(float).pct_change()
                for _, row in df.iterrows():
                    conn.execute("INSERT OR REPLACE INTO holders VALUES(?,?,?,?)",
                        (ts_code, str(row['end_date']), float(row.get('holder_num',0)),
                         float(row.get('holder_chg',0)) if pd.notna(row.get('holder_chg')) else 0))
                print(f"  holders {ts_code}: {len(df)} rows")
        except Exception as e: print(f"  holders {ts_code}: {e}")
    conn.commit(); conn.close()

def build_cyq_cache(stocks_a):
    """Build daily chip distribution cache."""
    conn = sqlite3.connect(str(DB_PATH))
    for code in stocks_a:
        ts_code = f"{code}.{'SH' if code.startswith('6') else 'SZ'}"
        existing = conn.execute("SELECT MAX(trade_date) FROM cyq_chips WHERE code=?", (ts_code,)).fetchone()[0]
        if existing: continue
        try:
            # Pull last 252 trading days (1 year) of chip data
            df = pro().cyq_chips(ts_code=ts_code, trade_date='20260506')
            if len(df) > 0:
                for _, row in df.iterrows():
                    conn.execute("INSERT OR REPLACE INTO cyq_chips VALUES(?,?,?,?)",
                        (ts_code, str(row['trade_date']), float(row['price']), float(row['percent'])))
                print(f"  cyq {ts_code}: {len(df)} levels")
        except Exception as e: print(f"  cyq {ts_code}: {e}")
    conn.commit(); conn.close()

def get_chip_concentration(code, current_price=None):
    """Get chip concentration: % of shares within ±10% of current price."""
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql("SELECT price, percent FROM cyq_chips WHERE code=? AND trade_date=(SELECT MAX(trade_date) FROM cyq_chips WHERE code=?)",
                     conn, params=(code, code))
    conn.close()
    if len(df) == 0: return 0
    if current_price:
        nearby = df[(df['price'] >= current_price * 0.9) & (df['price'] <= current_price * 1.1)]
        return float(nearby['percent'].sum())
    return float(df['percent'].max())

def get_holder_chg(code):
    """Get latest shareholder count change."""
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT holder_chg FROM holders WHERE code=? ORDER BY end_date DESC LIMIT 1",
                       (code,)).fetchone()
    conn.close()
    return float(row[0]) if row else 0


# ═══════════════════════════════════════════════════════════════════
# FUNDAMENTAL DATA CACHE (v2.0)
# ═══════════════════════════════════════════════════════════════════

def build_fundamental_cache(stocks_a, stocks_hk, stocks_us, force=False):
    """Build fundamental time series cache for all stocks.
    
    Caches quarterly fundamentals: roe, profit_growth, revenue_growth,
    profit_margin, eps — indexed by disclosure date (no look-ahead bias).
    
    Uses same data sources as data_fetcher.py:
      A-stock: akshare stock_financial_analysis_indicator_em
      HK:      akshare stock_financial_hk_analysis_indicator_em
      US:      akshare stock_financial_us_analysis_indicator_em
    """
    import akshare as ak
    conn = sqlite3.connect(str(DB_PATH))
    
    all_stocks = [(c, 'A') for c in stocks_a] + [(c, 'HK') for c in stocks_hk] + [(c, 'US') for c in stocks_us]
    
    for ticker, mkt in all_stocks:
        # Check cache
        if not force:
            existing = conn.execute("SELECT COUNT(*) FROM fundamentals WHERE ticker=? AND market=?", 
                                   (ticker, mkt)).fetchone()[0]
            if existing > 0:
                continue
        
        # Clear old data for this ticker (in case of refresh)
        conn.execute("DELETE FROM fundamentals WHERE ticker=? AND market=?", (ticker, mkt))
        
        rows = []
        try:
            if mkt == "A":
                # Normalize: strip .SH/.SZ suffix if present
                code = ticker.replace(".SH", "").replace(".SZ", "")
                ts_code = f"{code}.{'SH' if code.startswith('6') else 'SZ'}"
                ak_code = ts_code
                df = ak.stock_financial_analysis_indicator_em(symbol=ak_code)
                if df is None or df.empty:
                    print(f"  {ticker}: no A-stock fundamental data")
                    continue
                
                for idx in range(min(len(df), 20)):
                    row = df.iloc[idx]
                    notice = row.get("NOTICE_DATE", row.get("UPDATE_DATE", None))
                    report = row.get("REPORT_DATE", None)
                    if notice and str(notice).strip() and str(notice) != "nan":
                        disc_date = pd.Timestamp(notice)
                    elif report and str(report).strip() and str(report) != "nan":
                        disc_date = pd.Timestamp(report) + pd.Timedelta(days=45)
                    else:
                        continue
                    
                    rec = {"disc_date": str(disc_date.date()), "ticker": ticker, "market": mkt}
                    rec["roe"] = float(row["ROEJQ"]) if row.get("ROEJQ") and str(row["ROEJQ"]).strip() not in ("nan", "") else None
                    
                    if idx + 4 < len(df):
                        cur_np = row.get("PARENTNETPROFIT", 0)
                        prev_np = df.iloc[idx+4].get("PARENTNETPROFIT", 0)
                        if cur_np and prev_np and str(cur_np) != "nan" and str(prev_np) != "nan":
                            cur_np, prev_np = float(cur_np), float(prev_np)
                            rec["profit_growth"] = (cur_np - prev_np) / prev_np if prev_np > 0 else None
                        
                        cur_rev = row.get("OPERATEREVE", 0)
                        prev_rev = df.iloc[idx+4].get("OPERATEREVE", 0)
                        if cur_rev and prev_rev and str(cur_rev) != "nan" and str(prev_rev) != "nan":
                            cur_rev, prev_rev = float(cur_rev), float(prev_rev)
                            rec["revenue_growth"] = (cur_rev - prev_rev) / prev_rev if prev_rev > 0 else None
                    
                    np_val = row.get("PARENTNETPROFIT", 0)
                    rev_val = row.get("OPERATEREVE", 0)
                    if np_val and rev_val and str(np_val) != "nan" and str(rev_val) != "nan":
                        np_val, rev_val = float(np_val), float(rev_val)
                        rec["profit_margin"] = np_val / rev_val if rev_val > 0 else None
                    
                    eps = row.get("EPSJB", 0)
                    rec["eps"] = float(eps) if eps and str(eps).strip() not in ("nan", "") else None
                    rows.append(rec)
            
            elif mkt == "HK":
                code = ticker.replace(".HK", "").zfill(5)
                df = ak.stock_financial_hk_analysis_indicator_em(symbol=code)
                if df is None or df.empty:
                    print(f"  {ticker}: no HK fundamental data")
                    continue
                
                for idx in range(min(len(df), 20)):
                    row = df.iloc[idx]
                    report = row.get("REPORT_DATE", None)
                    if report and str(report).strip() and str(report) != "nan":
                        disc_date = pd.Timestamp(report) + pd.Timedelta(days=60)
                    else:
                        continue
                    
                    rec = {"disc_date": str(disc_date.date()), "ticker": ticker, "market": mkt}
                    roe = row.get("ROE_AVG", 0)
                    rec["roe"] = float(roe) if roe and str(roe).strip() != "nan" else None
                    rev_g = row.get("OPERATE_INCOME_YOY", 0)
                    rec["revenue_growth"] = float(rev_g)/100 if rev_g and str(rev_g).strip() != "nan" else None
                    marg = row.get("NET_PROFIT_RATIO", 0)
                    rec["profit_margin"] = float(marg)/100 if marg and str(marg).strip() != "nan" else None
                    eps = row.get("BASIC_EPS", 0)
                    rec["eps"] = float(eps) if eps and str(eps).strip() != "nan" else None
                    rows.append(rec)
            
            elif mkt == "US":
                df = ak.stock_financial_us_analysis_indicator_em(symbol=ticker)
                if df is None or df.empty:
                    print(f"  {ticker}: no US fundamental data (empty response)")
                    continue
                
                for idx in range(min(len(df), 20)):
                    row = df.iloc[idx]
                    report = row.get("REPORT_DATE", None)
                    if report and str(report).strip() and str(report) != "nan":
                        disc_date = pd.Timestamp(report) + pd.Timedelta(days=45)
                    else:
                        continue
                    
                    rec = {"disc_date": str(disc_date.date()), "ticker": ticker, "market": mkt}
                    roe = row.get("ROE_AVG", 0)
                    rec["roe"] = float(roe) if roe and str(roe).strip() != "nan" else None
                    marg = row.get("NET_PROFIT_RATIO", 0)
                    rec["profit_margin"] = float(marg)/100 if marg and str(marg).strip() != "nan" else None
                    rev_g = row.get("OPERATE_INCOME_YOY", 0)
                    rec["revenue_growth"] = float(rev_g)/100 if rev_g and str(rev_g).strip() != "nan" else None
                    rows.append(rec)
        
        except Exception as e:
            # Common: akshare upstream returns None for some US stocks (e.g. JPM)
            err = str(e)
            if 'NoneType' in err:
                print(f"  {ticker} ({mkt}): upstream data unavailable (akshare returned None)")
            else:
                print(f"  {ticker} ({mkt}): {e}")
            continue
        
        # Write to DB
        for rec in rows:
            conn.execute("INSERT OR REPLACE INTO fundamentals VALUES(?,?,?,?,?,?,?,?)",
                        (rec["ticker"], rec["disc_date"], rec["market"],
                         rec.get("roe"), rec.get("profit_growth"), rec.get("revenue_growth"),
                         rec.get("profit_margin"), rec.get("eps")))
        
        if rows:
            print(f"  {ticker} ({mkt}): {len(rows)} quarters cached")
        else:
            print(f"  {ticker} ({mkt}): no rows")
    
    conn.commit(); conn.close()


def get_fundamental_timeseries(ticker, market):
    """Read fundamental time series from cache. Returns DataFrame or None.
    
    Format matches data_fetcher.fetch_fundamental_timeseries() output:
    DataFrame indexed by disc_date with columns: roe, profit_growth, 
    revenue_growth, profit_margin, eps.
    """
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql(
        "SELECT disc_date, roe, profit_growth, revenue_growth, profit_margin, eps "
        "FROM fundamentals WHERE ticker=? AND market=? ORDER BY disc_date",
        conn, params=(ticker, market), parse_dates=['disc_date'], index_col='disc_date'
    )
    conn.close()
    return df if len(df) > 0 else None

# ═══════════════════════════════════════════════════════════════════
# ANALYST SIGNALS CACHE (v10.2)
# ═══════════════════════════════════════════════════════════════════
# Higher-frequency fundamental data that updates as analysts publish,
# not just quarterly. Three sources:
#   A-stocks: Tushare forecast (业绩预告) — event-driven, historical ann_date
#   HK: akshare stock_hk_profit_forecast_et — analyst EPS/rating/target (snapshot)
#   US: Alpha Vantage EARNINGS — historical reportedEPS vs estimatedEPS
#
# Signal types stored:
#   "forecast_positive"  (A): 预增/略增/扭亏 announced
#   "forecast_negative"  (A): 预减/略减/首亏/续亏 announced
#   "earnings_beat"      (US): positive surprise%
#   "earnings_miss"      (US): negative surprise%
#   "analyst_upgrade"    (HK): analyst consensus trend (snapshot, production only)

ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")

def build_analyst_cache(stocks_a, stocks_hk, stocks_us, force=False):
    """Fetch and cache analyst revision signals for all markets."""
    conn = sqlite3.connect(str(DB_PATH))

    # ── A-stocks: Tushare forecast (业绩预告) ──
    print("  📊 A-stocks: Tushare forecast (业绩预告)...")
    POSITIVE_TYPES = {'预增', '略增', '扭亏', '续盈'}
    NEGATIVE_TYPES = {'预减', '略减', '首亏', '续亏', '增亏'}

    for code in stocks_a:
        try:
            # Convert to Tushare ts_code format
            if '.' not in code:
                if code.startswith('6'):
                    ts_code = f"{code}.SH"
                else:
                    ts_code = f"{code}.SZ"
            else:
                ts_code = code

            df = pro().forecast(ts_code=ts_code,
                               fields='ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max')
            if df is None or df.empty:
                print(f"    {code}: no forecast data")
                continue

            rows = 0
            for _, row in df.iterrows():
                ann = row.get('ann_date')
                ftype = row.get('type', '')
                if not ann or pd.isna(ann):
                    continue

                signal_date = str(pd.Timestamp(ann).date())
                p_min = row.get('p_change_min', 0) or 0
                p_max = row.get('p_change_max', 0) or 0
                p_avg = (float(p_min) + float(p_max)) / 2 if p_min and p_max else 0

                if ftype in POSITIVE_TYPES:
                    conn.execute(
                        "INSERT OR REPLACE INTO analyst_signals VALUES(?,?,?,?,?,?)",
                        (ts_code, signal_date, 'A', 'forecast_positive', p_avg,
                         f"{ftype} p_change={p_avg:.1f}% end={row.get('end_date','')}"))
                    rows += 1
                elif ftype in NEGATIVE_TYPES:
                    conn.execute(
                        "INSERT OR REPLACE INTO analyst_signals VALUES(?,?,?,?,?,?)",
                        (ts_code, signal_date, 'A', 'forecast_negative', p_avg,
                         f"{ftype} p_change={p_avg:.1f}% end={row.get('end_date','')}"))
                    rows += 1

            print(f"    {code}: {rows} forecast signals cached")
            import time; time.sleep(0.3)  # Tushare rate limit
        except Exception as e:
            print(f"    {code}: error — {e}")

    # ── US stocks: Alpha Vantage EARNINGS ──
    print("  📊 US stocks: Alpha Vantage EARNINGS...")
    if ALPHA_VANTAGE_KEY:
        for ticker in stocks_us:
            try:
                url = "https://www.alphavantage.co/query"
                params = {
                    "function": "EARNINGS",
                    "symbol": ticker,
                    "apikey": ALPHA_VANTAGE_KEY,
                }
                resp = requests.get(url, params=params, timeout=15)
                data = resp.json()

                quarterly = data.get("quarterlyEarnings", [])
                if not quarterly:
                    print(f"    {ticker}: no earnings data")
                    continue

                rows = 0
                for q in quarterly:
                    report_date = q.get("reportedDate", "")
                    surprise_pct = q.get("surprisePercentage", "")
                    reported_eps = q.get("reportedEPS", "")
                    estimated_eps = q.get("estimatedEPS", "")

                    if not report_date or not surprise_pct or surprise_pct == "None":
                        continue

                    try:
                        surprise_val = float(surprise_pct)
                    except (ValueError, TypeError):
                        continue

                    if surprise_val > 0:
                        sig_type = "earnings_beat"
                    else:
                        sig_type = "earnings_miss"

                    conn.execute(
                        "INSERT OR REPLACE INTO analyst_signals VALUES(?,?,?,?,?,?)",
                        (ticker, report_date, 'US', sig_type,
                         surprise_val,
                         f"reported={reported_eps} est={estimated_eps} surprise={surprise_pct}%"))
                    rows += 1

                print(f"    {ticker}: {rows} earnings signals cached")
                import time; time.sleep(12.5)  # AV free tier: 5 calls/min
            except Exception as e:
                print(f"    {ticker}: error — {e}")
    else:
        print("    ⚠ No ALPHA_VANTAGE_KEY set, skipping US earnings")

    # ── HK stocks: akshare ET forecast (snapshot only, not backtestable) ──
    # We cache the current snapshot for production use. For backtesting, HK
    # analyst signals will not contribute (no historical data).
    print("  📊 HK stocks: akshare ET forecast (snapshot)...")
    import akshare as ak
    for ticker in stocks_hk:
        try:
            hk_code = ticker.replace('.HK', '').zfill(5)
            df = ak.stock_hk_profit_forecast_et(symbol=hk_code)
            if df is None or df.empty:
                print(f"    {ticker}: no ET forecast data")
                continue

            # Count upgrades/downgrades from current analyst ratings
            # Columns: 财政年度, 每股盈利, 证券商, 评级, 目标价, 更新日期
            rows = 0
            for _, row in df.iterrows():
                update_date = row.get('更新日期', '')
                rating = row.get('评级', '')
                target = row.get('目标价', 0)
                eps = row.get('每股盈利', 0)
                broker = row.get('证券商', '')

                if not update_date or pd.isna(update_date):
                    continue

                try:
                    signal_date = str(pd.Timestamp(update_date).date())
                except:
                    continue

                # Map Chinese ratings to numeric signal
                positive_ratings = {'买入', '增持', '强烈推荐', '推荐', '优于大市', '跑赢大市', 'Buy', 'Outperform'}
                negative_ratings = {'卖出', '减持', '回避', '落后大市', 'Sell', 'Underperform'}

                if rating in positive_ratings:
                    sig_val = 1.0
                    sig_type = 'analyst_upgrade'
                elif rating in negative_ratings:
                    sig_val = -1.0
                    sig_type = 'analyst_upgrade'
                else:
                    sig_val = 0.0
                    sig_type = 'analyst_upgrade'

                try:
                    target_val = float(target) if target and str(target) != 'nan' else 0
                    eps_val = float(eps) if eps and str(eps) != 'nan' else 0
                except:
                    target_val = 0; eps_val = 0

                conn.execute(
                    "INSERT OR REPLACE INTO analyst_signals VALUES(?,?,?,?,?,?)",
                    (ticker, signal_date, 'HK', sig_type, sig_val,
                     f"broker={broker} rating={rating} target={target_val:.2f} eps={eps_val:.3f}"))
                rows += 1

            print(f"    {ticker}: {rows} analyst ratings cached")
        except Exception as e:
            print(f"    {ticker}: error — {e}")

    conn.commit(); conn.close()
    print("  ✅ Analyst signals cache complete")


def get_analyst_signals(ticker, market):
    """Read analyst signals from cache. Returns DataFrame indexed by signal_date.

    Columns: signal_type, signal_value, detail
    For backtesting, only A-stocks and US have historical data.
    HK has current snapshot only (not backtestable).
    """
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql(
        "SELECT signal_date, signal_type, signal_value, detail "
        "FROM analyst_signals WHERE ticker=? AND market=? ORDER BY signal_date",
        conn, params=(ticker, market), parse_dates=['signal_date'], index_col='signal_date'
    )
    conn.close()
    return df if len(df) > 0 else None


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Gushen Cache Builder")
    p.add_argument("--force", action="store_true", help="Force re-download even if cache exists")
    p.add_argument("--macro-only", action="store_true", help="Only build macro cache")
    p.add_argument("--fund-only", action="store_true", help="Only build fundamental cache")
    p.add_argument("--analyst-only", action="store_true", help="Only build analyst signals cache")
    args = p.parse_args()

    A  = ['600519','000858','300750','002594','601318','600036','002230','300015','600809','000625']
    HK = ['0700.HK','9988.HK','3690.HK','1810.HK','1211.HK','0388.HK']
    US = ['AAPL','NVDA','MSFT','GOOGL','AMZN','META','JPM']

    init_db()

    if args.analyst_only:
        print("📊 Building analyst signals cache...")
        A_ts = [f"{c}.{'SH' if c.startswith('6') else 'SZ'}" for c in A]
        build_analyst_cache(A_ts, HK, US, force=args.force)
        print("✅ Done.")
        import sys; sys.exit(0)

    if not args.fund_only:
        print("📊 Building OHLCV cache...")
        build_ohlcv_cache(A, HK, US)
        print("📊 Building macro cache...")
        build_macro_cache(force=args.force)
        print("📊 Building holders cache...")
        build_holders_cache(A)
        print("📊 Building CYQ chips cache...")
        build_cyq_cache(A)

    if not args.macro_only:
        print("📊 Building fundamental cache...")
        A_ts = [f"{c}.{'SH' if c.startswith('6') else 'SZ'}" for c in A]
        build_fundamental_cache(A_ts, HK, US, force=args.force)

    # Always build analyst signals at the end (new in v10.2)
    print("📊 Building analyst signals cache...")
    A_ts = [f"{c}.{'SH' if c.startswith('6') else 'SZ'}" for c in A]
    build_analyst_cache(A_ts, HK, US, force=args.force)

    print("✅ Done.")
