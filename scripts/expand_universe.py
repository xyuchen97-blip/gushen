#!/usr/bin/env python3
"""v13 universe expansion — backfill OHLCV for 33 new names (21 → 54).

Selection rule (documented, not hand-picked winners): largest liquid names per market
with >=5y history, sector cap <=3, deliberately adding styles the current universe
lacks (utilities, energy, pharma, staples-retail, payments, telecom, insurance).

Sources: A = akshare qfq (no token needed; NOTE: legacy 8 A-names are Tushare
UNADJUSTED — flagged in doc, re-fetch qfq on owner machine eventually).
HK/US = yfinance (batch).

Usage: python3 scripts/expand_universe.py [A|HK|US]   (one market per call)
"""
import os, sys, json, sqlite3, warnings
import pandas as pd
warnings.filterwarnings('ignore')
GUSHEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(GUSHEN, 'data', 'gushen.db')

NEW_A = {  # code -> name (akshare symbol = bare code)
    '600900.SH': '长江电力', '601012.SH': '隆基绿能', '600276.SH': '恒瑞医药',
    '000333.SZ': '美的集团', '600887.SH': '伊利股份', '601899.SH': '紫金矿业',
    '600028.SH': '中国石化', '601398.SH': '工商银行', '002475.SZ': '立讯精密',
    '600030.SH': '中信证券', '000651.SZ': '格力电器', '688981.SH': '中芯国际',
}
NEW_HK = {
    '0005.HK': 'HSBC', '1299.HK': 'AIA', '0941.HK': '中国移动', '0883.HK': '中海油',
    '9618.HK': '京东', '9999.HK': '网易', '2015.HK': '理想汽车', '1024.HK': '快手',
    '0027.HK': '银河娱乐',
}
NEW_US = {
    'XOM': 'Exxon', 'JNJ': 'J&J', 'LLY': 'Eli Lilly', 'UNH': 'UnitedHealth',
    'V': 'Visa', 'WMT': 'Walmart', 'COST': 'Costco', 'PG': 'P&G',
    'TSLA': 'Tesla', 'AVGO': 'Broadcom', 'BRK-B': 'Berkshire', 'CAT': 'Caterpillar',
}

def upsert(conn, ticker, mkt, df):
    n = 0
    for idx, row in df.iterrows():
        try:
            conn.execute("INSERT OR REPLACE INTO ohlcv VALUES(?,?,?,?,?,?,?,?)",
                (ticker, str(idx)[:10], mkt, float(row['open']), float(row['high']),
                 float(row['low']), float(row['close']), float(row['volume'])))
            n += 1
        except Exception:
            pass
    return n

def have(conn, t):
    return conn.execute("SELECT COUNT(*) FROM ohlcv WHERE ticker=?", (t,)).fetchone()[0] > 200

mkt = sys.argv[1] if len(sys.argv) > 1 else 'A'
conn = sqlite3.connect(DB)

if mkt == 'A':
    import akshare as ak, time
    only = sys.argv[2].split(',') if len(sys.argv) > 2 else None
    for code, name in NEW_A.items():
        if only and code not in only: continue
        if have(conn, code):
            print(f'  {code} cached', flush=True); continue
        time.sleep(2.0)  # eastmoney rate limit
        try:
            sym = code.split('.')[0]
            df = ak.stock_zh_a_hist(symbol=sym, period='daily', start_date='20200101',
                                    end_date='20260506', adjust='qfq')
            df = df.rename(columns={'日期':'date','开盘':'open','最高':'high','最低':'low',
                                    '收盘':'close','成交量':'volume'})
            df['date'] = pd.to_datetime(df['date']); df = df.set_index('date')
            print(f'  {code} {name}: {upsert(conn, code, "A", df)} rows (qfq)', flush=True)
        except Exception as e:
            print(f'  {code} FAIL: {e}', flush=True)
        conn.commit()
else:
    import yfinance as yf
    src = NEW_HK if mkt == 'HK' else NEW_US
    todo = [t for t in src if not have(conn, t)]
    if todo:
        data = yf.download(todo, start='2020-01-01', end='2026-05-06', progress=False,
                           auto_adjust=True, group_by='ticker')
        for t in todo:
            try:
                df = data[t].dropna() if len(todo) > 1 else data.dropna()
                df = df.rename(columns={'Open':'open','High':'high','Low':'low',
                                        'Close':'close','Volume':'volume'})
                print(f'  {t}: {upsert(conn, t, mkt, df)} rows')
            except Exception as e:
                print(f'  {t} FAIL: {e}')
        conn.commit()
    else:
        print('  all cached')
conn.close()

# write/refresh universe config
uni = {'version': 'v13', 'legacy21': True,
       'A': NEW_A, 'HK': NEW_HK, 'US': NEW_US}
with open(os.path.join(GUSHEN, 'data', 'universe_v13_new.json'), 'w') as f:
    json.dump(uni, f, ensure_ascii=False, indent=1)
print('config written: data/universe_v13_new.json')
