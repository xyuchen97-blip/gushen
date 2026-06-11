#!/usr/bin/env python3
"""2015+ validation of the FROZEN v12/v13 stack (no new rules, no tuning).

Stack: v11 hold-exits + regime hysteresis + FRAGILE/NA entry block + vol-targeted sizing.
Era split: era1 = grid start → 2021-05-31 (TRUE OOS — no rule ever saw this data),
           era2 = 2021-06 → 2026-05 (the discovery window).
Resumable: per-stock results cached in data/longrun_progress.pkl; rerun until complete.
"""
import os, sys, pickle, numpy as np, pandas as pd, warnings
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
from strategy.gushen_cache import get_ohlcv, get_chip_concentration, get_holder_chg, get_analyst_signals

import json
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

PROG = os.path.join(GUSHEN, 'data', 'longrun_progress.pkl')
prog = pickle.load(open(PROG, 'rb')) if os.path.exists(PROG) else {}

START = '2015-06-01'
ERA = pd.Timestamp('2021-06-01')

for code, mkt in STOCKS:
    if code in prog:
        continue
    df = get_ohlcv(code, mkt)
    if df is None or len(df) < 400:
        prog[code] = {'eng': {}, 'bnh': {}}
        continue
    df = df.sort_index()
    pc = pcs[code]
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    bkt = classify_buckets(dfw['close'])
    vol13 = dfw['close'].pct_change().rolling(13).std()
    m2d = dict(macro)
    try:
        ats = get_analyst_signals(code, mkt)
        if ats is not None and len(ats) > 0: m2d['analyst_signals'] = ats
    except Exception: pass
    if mkt == 'A':
        try: m2d['chip_conc'] = get_chip_concentration(code)
        except Exception: pass
        try: m2d['holder_chg'] = get_holder_chg(code)
        except Exception: pass
    eng, bnh = {}, {}
    in_pos = False
    # v12 fix: start index on the WEEKLY grid (legacy daily-index bug)
    start_i = max(1, (dfw.index >= START).argmax() if (dfw.index >= START).any() else 1)
    for i in range(start_i, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i]) - 1
        r = score_bar_v5(di, df, pc, macro_data=m2d, market=mkt)
        b = bkt.iloc[i]
        nd = dfw.index[i+1]
        bnh[nd] = (mkt, ret)
        if r['action'] == 'BUY':
            if not (b in ('FRAGILE','NA') and not in_pos):
                in_pos = True
        elif r['action'] == 'EXIT':
            in_pos = False
        if in_pos:
            w = 1.0
            if pd.notna(vol13.iloc[i]) and vol13.iloc[i] > 0:
                w = min(3.0, 0.04/float(vol13.iloc[i]))
            eng[nd] = (mkt, ret*r.get('macro_mult',1.0), w)
    prog[code] = {'eng': eng, 'bnh': bnh}
    pickle.dump(prog, open(PROG, 'wb'))
    print(f'  {code} done ({len(eng)} eng-weeks)', flush=True)

if len(prog) < len(STOCKS):
    print(f'progress: {len(prog)}/{len(STOCKS)} — rerun to continue')
    sys.exit(0)

# ── finalize ──
def sharpe(a):
    a = np.asarray(a, dtype=float)
    return round(float(np.sqrt(52)*a.mean()/a.std()), 3) if len(a) >= 8 and a.std() > 0 else 0.0
all_weeks = pd.date_range('2016-06-03','2026-05-08',freq='W-FRI')
def port(kind, mkt=None):
    out = []
    for d in all_weeks:
        rows = []
        for code, v in prog.items():
            x = v[kind].get(d)
            if x is None: continue
            if mkt and x[0] != mkt: continue
            rows.append(x)
        if not rows:
            out.append(0.0)
        elif kind == 'eng':
            ws = np.array([x[2] for x in rows]); rs = np.array([x[1] for x in rows])
            out.append(float((ws*rs).sum()/ws.sum()))
        else:
            out.append(float(np.mean([x[1] for x in rows])))
    return pd.Series(out, index=all_weeks)
def stats(s):
    eq = (1+s).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
    return sharpe(s.values), dd
print('\n═══ FROZEN STACK — 2016-2026 (era1 = TRUE OOS for all v12 rules) ═══')
for name, mfilter in [('ALL', None), ('A','A'), ('HK','HK'), ('US','US')]:
    e = port('eng', mfilter); b = port('bnh', mfilter)
    e1, e2 = e[e.index < ERA], e[e.index >= ERA]
    b1, b2 = b[b.index < ERA], b[b.index >= ERA]
    s_all, dd_all = stats(e)
    print(f"{name:4s} eng: era1 S {sharpe(e1.values):+.3f} (B&H {sharpe(b1.values):+.3f}) | "
          f"era2 S {sharpe(e2.values):+.3f} (B&H {sharpe(b2.values):+.3f}) | "
          f"full S {s_all:+.3f} dd {dd_all:.0%} (B&H dd {stats(b)[1]:.0%})")
