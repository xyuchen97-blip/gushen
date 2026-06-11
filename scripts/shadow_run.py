#!/usr/bin/env python3
"""Weekly shadow run — score all 54 names with the production engine (score()),
append one JSON line per stock to data/shadow_log.jsonl, print a signal summary.
Observation only: never modifies strategy code or rules.
"""
import os, sys, json, pickle, warnings
from datetime import datetime
import pandas as pd
warnings.filterwarnings('ignore')
GUSHEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GUSHEN)
os.environ['GUSHEN_TUNE'] = '1'
os.environ.setdefault('GUSHEN_DB_PATH', os.path.join(GUSHEN, 'data', 'gushen.db'))

from strategy.scoring import score
from strategy.gushen_cache import get_ohlcv

macro = pickle.load(open(os.path.join(GUSHEN, 'data', 'macro_snapshot.pkl'), 'rb'))
LEGACY = [('600519.SH','A'),('000858.SZ','A'),('300750.SZ','A'),('002594.SZ','A'),('601318.SH','A'),
('600036.SH','A'),('002230.SZ','A'),('300015.SZ','A'),('0700.HK','HK'),('9988.HK','HK'),
('3690.HK','HK'),('1810.HK','HK'),('1211.HK','HK'),('0388.HK','HK'),('AAPL','US'),('NVDA','US'),
('MSFT','US'),('GOOGL','US'),('AMZN','US'),('META','US'),('JPM','US')]
STOCKS = list(LEGACY)
for _f in ('universe_v13_new.json', 'universe_v14_breadth.json'):
    _p = os.path.join(GUSHEN, 'data', _f)
    if os.path.exists(_p):
        _u = json.load(open(_p))
        _seen = {s[0] for s in STOCKS}
        STOCKS += [(c, m) for m in ('A','HK','US') for c in _u[m] if c not in _seen]

run_ts = datetime.now().strftime('%Y-%m-%d %H:%M')
rows = []
for code, mkt in STOCKS:
    try:
        df = get_ohlcv(code, mkt).sort_index()
        dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min',
                                        'close':'last','volume':'sum'}).dropna()
        r = score(df, dfw, ticker=code, market=mkt, macro_data=macro)
        rows.append({'run': run_ts, 'data_date': str(df.index[-1])[:10], 'code': code,
                     'mkt': mkt, 'action': r['action'], 'composite': r['composite'],
                     'bucket': r['bucket'], 'hold_health': r['hold_health'],
                     'vol_weight': r['vol_weight'], 'pos_mult': r['suggested_position_mult']})
    except Exception as e:
        rows.append({'run': run_ts, 'code': code, 'mkt': mkt, 'action': 'ERROR',
                     'error': f'{type(e).__name__}: {str(e)[:100]}'})

with open(os.path.join(GUSHEN, 'data', 'shadow_log.jsonl'), 'a') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

errs = [r for r in rows if r['action'] == 'ERROR']
print(f'shadow run {run_ts}: {len(rows)} scored, {len(errs)} errors')
for r in errs:
    print(' ⚠', r['code'], r.get('error'))
for act in ['BUY','WATCH','EXIT']:
    sel = [r for r in rows if r['action'] == act]
    if sel:
        print(f'\n{act} ({len(sel)}):')
        for r in sorted(sel, key=lambda x: -x['composite']):
            print(f"  {r['code']:10s} {r['mkt']:2s} comp={r['composite']:5.1f} "
                  f"bucket={r['bucket']:7s} hh={r['hold_health']:+.0f} pos_mult={r['pos_mult']}")
hold = [r for r in rows if r['action'] == 'HOLD']
print(f'\nHOLD: {len(hold)} names (not listed)')
