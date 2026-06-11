#!/usr/bin/env python3
"""Daily vs weekly decision cadence under the FROZEN v12 stack (June 2026).

Owner runs Gushen daily, so the engine should be evaluated at daily cadence too.
Prior evidence (v10.1, pre-v12): daily was worse (S 1.095). Re-test under v12.

Both cadences in one harness, identical universe (v13 54 names), EQUAL WEIGHT
(no vol sizing) so the comparison isolates cadence. FRAGILE/NA gate applied to both.
Daily Sharpe annualized sqrt(252); weekly sqrt(52). Resumable per stock.

Usage: python3 scripts/daily_cadence.py     (rerun until it prints the summary)
"""
import os, sys, json, pickle, numpy as np, pandas as pd, warnings
warnings.filterwarnings('ignore')
GUSHEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GUSHEN)
os.environ['GUSHEN_TUNE'] = '1'
os.environ.setdefault('GUSHEN_DB_PATH', os.path.join(GUSHEN, 'data', 'gushen.db'))
os.environ['GUSHEN_HOLD_EXIT'] = '1'
os.environ['GUSHEN_HOLD_EXIT_THRESH'] = '-2'
os.environ['GUSHEN_REGIME_HYST'] = '1'

from strategy.scoring import score_bar_v5
from strategy.buckets import classify_buckets
from strategy.gushen_cache import get_ohlcv

macro = pickle.load(open(os.path.join(GUSHEN, 'data', 'macro_snapshot.pkl'), 'rb'))
cache = pickle.load(open(os.path.join(GUSHEN, 'data', 'precomp_cache.pkl'), 'rb'))
pcs = {k[0]: v for k, v in cache.items()}

LEGACY = [('600519.SH','A'),('000858.SZ','A'),('300750.SZ','A'),('002594.SZ','A'),('601318.SH','A'),
('600036.SH','A'),('002230.SZ','A'),('300015.SZ','A'),('0700.HK','HK'),('9988.HK','HK'),
('3690.HK','HK'),('1810.HK','HK'),('1211.HK','HK'),('0388.HK','HK'),('AAPL','US'),('NVDA','US'),
('MSFT','US'),('GOOGL','US'),('AMZN','US'),('META','US'),('JPM','US')]
uni = json.load(open(os.path.join(GUSHEN, 'data', 'universe_v13_new.json')))
STOCKS = LEGACY + [(c, m) for m in ('A','HK','US') for c in uni[m]
                   if c not in {s[0] for s in LEGACY}]

PROG = os.path.join(GUSHEN, 'data', 'cadence_progress.pkl')
try:
    prog = pickle.load(open(PROG, 'rb')) if os.path.exists(PROG) else {}
except Exception:
    prog = {}

ERA = pd.Timestamp('2021-06-01')

for code, mkt in STOCKS:
    if code in prog:
        continue
    df = get_ohlcv(code, mkt)
    if df is None or len(df) < 400 or code not in pcs:
        prog[code] = {'d': {}, 'w': {}}
        continue
    df = df.sort_index()
    pc = pcs[code]
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    bkt_w = classify_buckets(dfw['close'])
    bkt_d = bkt_w.reindex(df.index, method='ffill').fillna('NA')

    # ── daily cadence ──
    d_rec = {}
    in_pos = False
    dstart = max(252, (df.index >= '2015-06-01').argmax())
    closes = df['close'].values
    for i in range(dstart, len(df)-1):
        ret = closes[i+1]/closes[i] - 1
        r = score_bar_v5(i, df, pc, macro_data=macro, market=mkt)
        b = bkt_d.iloc[i]
        if r['action'] == 'BUY':
            if not (b in ('FRAGILE','NA') and not in_pos):
                in_pos = True
        elif r['action'] == 'EXIT':
            in_pos = False
        if in_pos:
            d_rec[df.index[i+1]] = (mkt, float(ret*r.get('macro_mult',1.0)))

    # ── weekly cadence ──
    w_rec = {}
    in_pos = False
    wstart = max(1, (dfw.index >= '2015-06-01').argmax())
    for i in range(wstart, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        ret = dfw['close'].iloc[i+1]/dfw['close'].iloc[i] - 1
        r = score_bar_v5(di, df, pc, macro_data=macro, market=mkt)
        b = bkt_w.iloc[i]
        if r['action'] == 'BUY':
            if not (b in ('FRAGILE','NA') and not in_pos):
                in_pos = True
        elif r['action'] == 'EXIT':
            in_pos = False
        if in_pos:
            w_rec[dfw.index[i+1]] = (mkt, float(ret*r.get('macro_mult',1.0)))

    prog[code] = {'d': d_rec, 'w': w_rec}
    tmp = PROG + '.tmp'
    pickle.dump(prog, open(tmp, 'wb')); os.replace(tmp, PROG)
    print(f'  {code} done (d={len(d_rec)} w={len(w_rec)})', flush=True)

if len(prog) < len(STOCKS):
    print(f'progress {len(prog)}/{len(STOCKS)} — rerun to continue')
    sys.exit(0)

def stats(kind, ann, grid):
    out = []
    for d in grid:
        rows = [prog[c][kind][d][1] for c in prog if d in prog[c][kind]]
        out.append(float(np.mean(rows)) if rows else 0.0)
    s = pd.Series(out, index=grid)
    def sh(x):
        return round(float(np.sqrt(ann)*x.mean()/x.std()), 3) if len(x) >= 8 and x.std() > 0 else 0.0
    eq = (1+s).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
    return sh(s), sh(s[s.index < ERA]), sh(s[s.index >= ERA]), dd

wgrid = pd.date_range('2016-06-03','2026-06-05',freq='W-FRI')
dgrid = pd.bdate_range('2016-06-03','2026-06-09')
wa, w1, w2, wdd = stats('w', 52, wgrid)
da, d1, d2, ddd = stats('d', 252, dgrid)
tw = sum(len(prog[c]['w']) for c in prog); td = sum(len(prog[c]['d']) for c in prog)
print('\n═══ DECISION CADENCE — frozen v12 stack, 54 names, equal weight ═══')
print(f"weekly: S {wa:+.3f} (era1 {w1:+.3f} / era2 {w2:+.3f}) maxDD {wdd:.1%} | stock-weeks {tw}")
print(f"daily:  S {da:+.3f} (era1 {d1:+.3f} / era2 {d2:+.3f}) maxDD {ddd:.1%} | stock-days  {td}")
