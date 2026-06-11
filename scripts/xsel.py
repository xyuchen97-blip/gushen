#!/usr/bin/env python3
"""v14 cross-sectional selection experiment (FROZEN engine, new portfolio layer only).

Pass 1 (resumable): run the frozen v12 stack per stock over 2015-2026, recording for
every week: market, fwd return, in_position, composite, vol weight, macro mult.
Pass 2 (when complete): evaluate portfolio variants WITHOUT rescoring:
  - hold-all (baseline: current behavior — hold everything in position)
  - top-K by composite among in-position names (K=10/20/30), raw and
    threshold-normalized ranking (composite / market bull_buy threshold)
Gates: top-K must beat hold-all full-period portfolio S; no era degrades > 0.05.
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

from strategy.scoring import score_bar_v5, V10_THRESHOLDS
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

PROG = os.path.join(GUSHEN, 'data', 'xsel_progress.pkl')
try:
    prog = pickle.load(open(PROG, 'rb')) if os.path.exists(PROG) else {}
except Exception:
    prog = {}

for code, mkt in STOCKS:
    if code in prog:
        continue
    df = get_ohlcv(code, mkt)
    if df is None or len(df) < 400 or code not in pcs:
        prog[code] = {}
        continue
    df = df.sort_index()
    pc = pcs[code]
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    bkt = classify_buckets(dfw['close'])
    vol13 = dfw['close'].pct_change().rolling(13).std()
    rec = {}
    in_pos = False
    start_i = max(1, (dfw.index >= '2015-06-01').argmax() if (dfw.index >= '2015-06-01').any() else 1)
    for i in range(start_i, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i]) - 1
        r = score_bar_v5(di, df, pc, macro_data=macro, market=mkt)
        b = bkt.iloc[i]
        if r['action'] == 'BUY':
            if not (b in ('FRAGILE','NA') and not in_pos):
                in_pos = True
        elif r['action'] == 'EXIT':
            in_pos = False
        w = 1.0
        if pd.notna(vol13.iloc[i]) and vol13.iloc[i] > 0:
            w = min(3.0, 0.04/float(vol13.iloc[i]))
        rec[dfw.index[i+1]] = (mkt, float(ret), bool(in_pos),
                               float(r['composite']), float(w), float(r.get('macro_mult',1.0)))
    prog[code] = rec
    tmp = PROG + '.tmp'
    pickle.dump(prog, open(tmp, 'wb')); os.replace(tmp, PROG)
    print(f'  {code} done', flush=True)

if len(prog) < len(STOCKS):
    print(f'progress {len(prog)}/{len(STOCKS)} — rerun to continue')
    sys.exit(0)

# ── Pass 2: portfolio variants ──
def sharpe(a):
    a = np.asarray(a, dtype=float)
    return round(float(np.sqrt(52)*a.mean()/a.std()), 3) if len(a) >= 8 and a.std() > 0 else 0.0
THRESH = {m: V10_THRESHOLDS[m]['bull_buy'] for m in ('A','HK','US')}
all_weeks = pd.date_range('2016-06-03','2026-06-05',freq='W-FRI')
ERA = pd.Timestamp('2021-06-01')

def evaluate(topk=None, norm=False):
    out = []
    for d in all_weeks:
        cands = []
        for code, rec in prog.items():
            x = rec.get(d)
            if x and x[2]:  # in position
                mkt, ret, _, comp, w, mm = x
                rank = comp / THRESH[mkt] if norm else comp
                cands.append((rank, ret*mm, w))
        if topk and len(cands) > topk:
            cands = sorted(cands, key=lambda t: -t[0])[:topk]
        if cands:
            ws = np.array([c[2] for c in cands]); rs = np.array([c[1] for c in cands])
            out.append(float((ws*rs).sum()/ws.sum()))
        else:
            out.append(0.0)
    s = pd.Series(out, index=all_weeks)
    eq = (1+s).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
    return (sharpe(s.values), sharpe(s[s.index<ERA].values), sharpe(s[s.index>=ERA].values), dd)

print('\n═══ v14 (132 names) cross-sectional selection — frozen engine ═══')
print(f"{'variant':22s} {'S':>7s} {'era1':>7s} {'era2':>7s} {'maxDD':>7s}")
a, e1, e2, dd = evaluate(None)
print(f"{'hold-all (baseline)':22s} {a:+7.3f} {e1:+7.3f} {e2:+7.3f} {dd:6.1%}")
for k in (10, 20, 30):
    for nm in (False, True):
        a, e1, e2, dd = evaluate(k, nm)
        nmtag = 'norm' if nm else 'raw'
        print(f"{f'top-{k} ({nmtag})':22s} {a:+7.3f} {e1:+7.3f} {e2:+7.3f} {dd:6.1%}")
