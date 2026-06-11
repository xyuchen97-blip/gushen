#!/usr/bin/env python3
"""Warm the precompute cache for v13 universe names (incremental, timeout-safe)."""
import os, sys, json, pickle, warnings
import pandas as pd
warnings.filterwarnings('ignore')
GUSHEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GUSHEN)
os.environ['GUSHEN_TUNE'] = '1'
os.environ.setdefault('GUSHEN_DB_PATH', os.path.join(GUSHEN, 'data', 'gushen.db'))
from strategy.scoring import precompute
from strategy.gushen_cache import get_ohlcv

PC = os.path.join(GUSHEN, 'data', 'precomp_cache.pkl')
LEGACY = [('600519.SH','A'),('000858.SZ','A'),('300750.SZ','A'),('002594.SZ','A'),('601318.SH','A'),
('600036.SH','A'),('002230.SZ','A'),('300015.SZ','A'),('0700.HK','HK'),('9988.HK','HK'),
('3690.HK','HK'),('1810.HK','HK'),('1211.HK','HK'),('0388.HK','HK'),('AAPL','US'),('NVDA','US'),
('MSFT','US'),('GOOGL','US'),('AMZN','US'),('META','US'),('JPM','US')]
uni = json.load(open(os.path.join(GUSHEN, 'data', 'universe_v13_new.json')))
todo = LEGACY + [(c, m) for m in ('A', 'HK', 'US') for c in uni[m]
                 if c not in {s[0] for s in LEGACY}]
v14p = os.path.join(GUSHEN, 'data', 'universe_v14_breadth.json')
if os.path.exists(v14p):
    u14 = json.load(open(v14p))
    seen = {s[0] for s in todo}
    todo += [(c, m) for m in ('A', 'HK', 'US') for c in u14[m] if c not in seen]
try:
    cache = pickle.load(open(PC, 'rb')) if os.path.exists(PC) else {}
except Exception:
    print('  [WARN] cache unreadable (truncated write?) — rebuilding from empty')
    cache = {}
# Stale-key purge: keep only entries whose (code, len, last_date) still matches the DB.
def fresh(k):
    df = get_ohlcv(k[0], 'A')
    return df is not None and len(df) == k[1] and str(df.sort_index().index[-1]) == k[2]
if os.environ.get('WARM_REBUILD') == '1':
    cache = {}
done = {k[0] for k in cache}
for code, mkt in todo:
    if code in done:
        continue
    df = get_ohlcv(code, mkt)
    if df is None or len(df) < 300:
        print(f'  {code}: no data', flush=True)
        continue
    df = df.sort_index()
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min',
                                    'close':'last','volume':'sum'}).dropna()
    key = (code, len(df), str(df.index[-1]))
    cache[key] = precompute(df, dfw)
    # atomic save: timeout mid-write must not truncate the cache
    tmp = PC + '.tmp'
    with open(tmp, 'wb') as f:
        pickle.dump(cache, f)
    os.replace(tmp, PC)
    print(f'  {code} warmed', flush=True)
print('cache codes:', len({k[0] for k in cache}))
