#!/usr/bin/env python3
"""Gushen Cache Layer — 股神修炼模式专用 (Tune mode only).
       ⛔ NEVER import in data_fetcher, daily_digest, or analyze.
       ✅ Only used by strategy/tune.py when TUNE_MODE = True.

       Production scoring uses live APIs via data_fetcher.py.
       Cache exists solely for fast backtest iteration during 修炼."""

import sqlite3, pandas as pd, numpy as np, tushare as ts, os
from pathlib import Path
from datetime import datetime

TUNE_MODE = os.environ.get("GUSHEN_TUNE", "0") == "1"
if not TUNE_MODE:
    raise RuntimeError("gushen_cache is 修炼模式专用. Set GUSHEN_TUNE=1 to use.")

DB_PATH = Path("/Users/alafat/.workbuddy/skills/gushen/data/gushen.db")
TOKEN = "c1cbd943613a172b916b0d249b3dc04146d13817d6bc4c0bc60756de"

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
        CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date);
        CREATE INDEX IF NOT EXISTS idx_margin_date ON margin(date);
        CREATE INDEX IF NOT EXISTS idx_macro_date ON macro(date);
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

    # HK + US from yfinance
    import yfinance as yf
    for ticker, mkt in [*[(t,'HK') for t in stocks_hk], *[(t,'US') for t in stocks_us]]:
        existing = conn.execute("SELECT MAX(date) FROM ohlcv WHERE ticker=?", (ticker,)).fetchone()[0]
        if existing: continue
        try:
            df = yf.download(ticker, start='2021-01-01', end='2026-05-06', progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex): df = df.xs(df.columns.levels[-1][0], axis=1, level=-1)
            m = {'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'}
            df = df.rename(columns={k:v for k,v in m.items() if k in df.columns})
            for idx, row in df.iterrows():
                conn.execute("INSERT OR REPLACE INTO ohlcv VALUES(?,?,?,?,?,?,?,?)",
                    (ticker, str(idx.date()), mkt, float(row['open']), float(row['high']),
                     float(row['low']), float(row['close']), float(row['volume'])))
            print(f"  {ticker}: {len(df)} rows from yfinance")
        except Exception as e: print(f"  {ticker}: {e}")

    conn.commit(); conn.close()

def get_ohlcv(ticker, market):
    """Read OHLCV from cache. Returns DataFrame or None."""
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql("SELECT date,open,high,low,close,volume FROM ohlcv WHERE ticker=? ORDER BY date", 
                     conn, params=(ticker,), parse_dates=['date'], index_col='date')
    conn.close()
    return df if len(df) > 0 else None

def build_macro_cache():
    """Build macro cache from Tushare."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # M2
        if not conn.execute("SELECT 1 FROM macro WHERE series='china_m2' LIMIT 1").fetchone():
            df = pro().cn_m(start_date='202101', end_date='202605')
            for _, row in df.iterrows():
                conn.execute("INSERT OR REPLACE INTO macro VALUES('china_m2',?,?)", 
                           (str(row['month'])+'01', float(row.get('m2',0))))
            print(f"  china_m2: {len(df)} rows")
    except Exception as e: print(f"  china_m2: {e}")
    
    try:
        # PMI
        if not conn.execute("SELECT 1 FROM macro WHERE series='china_pmi' LIMIT 1").fetchone():
            df = pro().cn_pmi(start_date='202101', end_date='202605')
            for _, row in df.iterrows():
                conn.execute("INSERT OR REPLACE INTO macro VALUES('china_pmi',?,?)",
                           (str(row['month'])+'01', float(row.get('pmi',0))))
            print(f"  china_pmi: {len(df)} rows")
    except Exception as e: print(f"  china_pmi: {e}")

    conn.commit(); conn.close()

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

if __name__ == "__main__":
    init_db()
    build_ohlcv_cache(
        stocks_a=['600519','000858','300750','002594','601318','600036','002230','300015','600809','000625'],
        stocks_hk=['0700.HK','9988.HK','3690.HK','1810.HK','1211.HK','0388.HK'],
        stocks_us=['AAPL','NVDA','MSFT','GOOGL','AMZN','META','JPM']
    )
    build_macro_cache()
    print("Done.")
