#!/usr/bin/env python3
"""v18 tiered OHLCV refresh — yfinance demoted to last resort (owner distrust, justified).

Source tiers (verified June 2026):
  A-share: Tushare pro_bar qfq (official-grade)  → yfinance .SS/.SZ fallback
  HK:      akshare stock_hk_hist qfq (eastmoney) → yfinance fallback
           (Tushare hk_daily is quota-capped 5/day on this token — not viable)
  US:      Tiingo daily adjusted (official API)  → yfinance fallback

Incremental: fetches only dates after each ticker's max(date). SQLite via /tmp to
dodge mounted-FS I/O errors. Prints a per-source health report at the end.
Usage: python3 scripts/refresh_data.py [A|HK|US|all]
"""
import os, sys, json, sqlite3, shutil, time, warnings
import pandas as pd, requests
warnings.filterwarnings('ignore')
V18 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V18)
import strategy.gushen_keys  # noqa: F401

DB = os.path.join(V18, 'data', 'gushen.db')
TMP = '/tmp/gushen_refresh.db'
LEGACY = {'600519.SH':'A','000858.SZ':'A','300750.SZ':'A','002594.SZ':'A','601318.SH':'A',
'600036.SH':'A','002230.SZ':'A','300015.SZ':'A','0700.HK':'HK','9988.HK':'HK','3690.HK':'HK',
'1810.HK':'HK','1211.HK':'HK','0388.HK':'HK','AAPL':'US','NVDA':'US','MSFT':'US','GOOGL':'US',
'AMZN':'US','META':'US','JPM':'US'}
UNI = dict(LEGACY)
for fn in ('universe_v13_new.json', 'universe_v14_breadth.json'):
    p = os.path.join(V18, 'data', fn)
    if os.path.exists(p):
        u = json.load(open(p))
        for m in ('A', 'HK', 'US'):
            for c in u[m]:
                UNI.setdefault(c, m)

want = sys.argv[1] if len(sys.argv) > 1 else 'all'
health = {}

def upsert(conn, code, mkt, rows):
    n = 0
    for d, o, h, l, c, v in rows:
        conn.execute("INSERT OR REPLACE INTO ohlcv VALUES(?,?,?,?,?,?,?,?)",
                     (code, d, mkt, o, h, l, c, v))
        n += 1
    return n

def last_date(conn, code):
    r = conn.execute("SELECT MAX(date) FROM ohlcv WHERE ticker=?", (code,)).fetchone()[0]
    return (r or '2015-01-01')[:10]

def fetch_a_tushare(code, start):
    import tushare as ts
    pro = ts.pro_api(os.environ['TUSHARE_TOKEN'])
    df = ts.pro_bar(ts_code=code, adj='qfq', start_date=start.replace('-', ''), api=pro)
    if df is None or df.empty: return []
    return [(f"{r.trade_date[:4]}-{r.trade_date[4:6]}-{r.trade_date[6:]}",
             float(r.open), float(r.high), float(r.low), float(r.close), float(r.vol))
            for r in df.itertuples()]

def fetch_hk_akshare(code, start):
    import akshare as ak
    sym = code.replace('.HK', '').zfill(5)
    df = ak.stock_hk_hist(symbol=sym, period='daily',
                          start_date=start.replace('-', ''), end_date='20991231', adjust='qfq')
    if df is None or df.empty: return []
    df = df.rename(columns={'日期':'date','开盘':'o','最高':'h','最低':'l','收盘':'c','成交量':'v'})
    return [(str(r.date)[:10], float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
            for r in df.itertuples()]

def fetch_us_tiingo(code, start):
    r = requests.get(f'https://api.tiingo.com/tiingo/daily/{code}/prices',
                     params={'startDate': start, 'token': os.environ['TIINGO_KEY']},
                     timeout=15).json()
    if not isinstance(r, list): return []
    return [(x['date'][:10], x['adjOpen'], x['adjHigh'], x['adjLow'], x['adjClose'], x['adjVolume'])
            for x in r]

def fetch_yf(code, mkt, start):
    import yfinance as yf
    sym = code.replace('.SH', '.SS') if mkt == 'A' else code
    df = yf.download(sym, start=start, progress=False, auto_adjust=True)
    if df is None or df.empty: return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return [(str(i)[:10], float(r['Open']), float(r['High']), float(r['Low']),
             float(r['Close']), float(r['Volume'])) for i, r in df.iterrows()]

TIERS = {'A': [('tushare', fetch_a_tushare), ('yfinance', lambda c, s: fetch_yf(c, 'A', s))],
         'HK': [('akshare', fetch_hk_akshare), ('yfinance', lambda c, s: fetch_yf(c, 'HK', s))],
         'US': [('tiingo', fetch_us_tiingo), ('yfinance', lambda c, s: fetch_yf(c, 'US', s))]}

shutil.copy(DB, TMP)
conn = sqlite3.connect(TMP)
_since_ckpt = 0
def checkpoint():
    conn.commit()
    shutil.copy(TMP, DB)
    jp = DB + '-journal'
    if os.path.exists(jp):
        open(jp, 'w').close()
for code, mkt in UNI.items():
    if want != 'all' and mkt != want:
        continue
    start = last_date(conn, code)
    if start >= str(pd.Timestamp.now().date() - pd.Timedelta(days=1)):
        health.setdefault('up_to_date', []).append(code)
        continue
    done = False
    for src, fn in TIERS[mkt]:
        try:
            rows = fn(code, start)
            if rows:
                n = upsert(conn, code, mkt, rows)
                health.setdefault(src, []).append(code)
                print(f'  {code} +{n} rows via {src}', flush=True)
                done = True
                break
        except Exception as e:
            print(f'  {code} {src} failed: {type(e).__name__} {str(e)[:50]}', flush=True)
        time.sleep(0.3 if mkt != 'A' else 0.15)
    if not done:
        health.setdefault('FAILED', []).append(code)
    _since_ckpt += 1
    if _since_ckpt >= 8:          # survive timeouts/crashes: persist every 8 names
        checkpoint(); _since_ckpt = 0
checkpoint()
conn.close()

print('\n═══ SOURCE HEALTH ═══')
for src, codes in health.items():
    flag = '⚠ ' if src in ('FAILED', 'yfinance') else ''
    print(f'  {flag}{src}: {len(codes)}' + (f' → {codes[:6]}' if src in ('FAILED', 'yfinance') else ''))
if health.get('FAILED'):
    print('  🔴 some names failed ALL sources — investigate before trusting signals')
