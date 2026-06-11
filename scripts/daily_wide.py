#!/usr/bin/env python3
"""Daily cadence at full scale (132 names, vol sizing) + trailing-stop risk layer.

Pass 1 (resumable): store per stock per day: (mkt, fwd ret, action, bucket, vol_w,
macro_mult). Pass 2 simulates position variants WITHOUT rescoring:
  - daily baseline: BUY enters (FRAGILE/NA gated), EXIT exits
  - daily + trail: additionally exit when close drops 15% from peak-since-entry
Weekly-cadence 132-name baseline for comparison: +1.241 (era1 +1.394 / era2 +1.074,
dd -18.8%) from xsel.py hold-all.

Usage: python3 scripts/daily_wide.py   (rerun until summary prints)
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
STOCKS = list(LEGACY)
for f in ('universe_v13_new.json', 'universe_v14_breadth.json'):
    u = json.load(open(os.path.join(GUSHEN, 'data', f)))
    seen = {s[0] for s in STOCKS}
    STOCKS += [(c, m) for m in ('A','HK','US') for c in u[m] if c not in seen]

PROG = os.path.join(GUSHEN, 'data', 'dailywide_progress.pkl')
try:
    prog = pickle.load(open(PROG, 'rb')) if os.path.exists(PROG) else {}
except Exception:
    prog = {}

for code, mkt in STOCKS:
    if code in prog:
        continue
    df = get_ohlcv(code, mkt)
    if df is None or len(df) < 400 or code not in pcs:
        prog[code] = None
        continue
    df = df.sort_index()
    pc = pcs[code]
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    bkt = classify_buckets(dfw['close']).reindex(df.index, method='ffill').fillna('NA')
    vw = dfw['close'].pct_change().rolling(13).std()
    volw = (0.04/vw).clip(upper=3.0).reindex(df.index, method='ffill')
    closes = df['close'].values
    dstart = max(252, (df.index >= '2015-06-01').argmax())
    dates, rows = [], []
    for i in range(dstart, len(df)-1):
        r = score_bar_v5(i, df, pc, macro_data=macro, market=mkt)
        rows.append((mkt, float(closes[i+1]/closes[i]-1), r['action'], str(bkt.iloc[i]),
                     float(volw.iloc[i]) if pd.notna(volw.iloc[i]) else 1.0,
                     float(r.get('macro_mult',1.0))))
        dates.append(df.index[i+1])
    prog[code] = (dates, rows)
    tmp = PROG + '.tmp'
    pickle.dump(prog, open(tmp, 'wb')); os.replace(tmp, PROG)
    print(f'  {code} done', flush=True)

if sum(1 for v in prog.values()) < len(STOCKS):
    print(f'progress {len(prog)}/{len(STOCKS)} — rerun to continue')
    sys.exit(0)

# ── Pass 2: simulate variants ──
ERA = pd.Timestamp('2021-06-01')
def simulate(trail=None):
    port = {}
    for code, v in prog.items():
        if v is None: continue
        dates, rows = v
        in_pos = False; cum = 1.0; peak = 1.0
        for d, (mkt, ret, act, b, w, mm) in zip(dates, rows):
            if act == 'BUY':
                if not (b in ('FRAGILE','NA') and not in_pos):
                    if not in_pos: cum = peak = 1.0
                    in_pos = True
            elif act == 'EXIT':
                in_pos = False
            if in_pos and trail and peak > 0 and cum < peak * (1-trail):
                in_pos = False
            if in_pos:
                port.setdefault(d, []).append(ret*mm*w if w else ret*mm)
                port[d][-1] = (ret*mm, w)
                cum *= (1+ret); peak = max(peak, cum)
        # note: cum/peak tracked only while in_pos
    grid = pd.bdate_range('2016-06-03','2026-06-09')
    out = []
    for d in grid:
        rows2 = port.get(d)
        if rows2:
            ws = np.array([x[1] for x in rows2]); rs = np.array([x[0] for x in rows2])
            out.append(float((ws*rs).sum()/ws.sum()))
        else:
            out.append(0.0)
    s = pd.Series(out, index=grid)
    def sh(x):
        return round(float(np.sqrt(252)*x.mean()/x.std()), 3) if len(x) >= 8 and x.std() > 0 else 0.0
    eq = (1+s).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
    return sh(s), sh(s[s.index<ERA]), sh(s[s.index>=ERA]), dd

print('\n═══ DAILY CADENCE, 132 names, vol-weighted ═══')
print('weekly-cadence reference (xsel hold-all): +1.241 (era1 +1.394 / era2 +1.074) dd -18.8%')
for label, tr in [('daily baseline', None), ('daily + trail 15%', 0.15), ('daily + trail 20%', 0.20)]:
    a, e1, e2, dd = simulate(tr)
    print(f'{label:18s} S {a:+.3f} (era1 {e1:+.3f} / era2 {e2:+.3f}) maxDD {dd:.1%}')
