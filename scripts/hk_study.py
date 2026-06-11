#!/usr/bin/env python3
"""HK structural study (June 2026) — three experiments on existing data.

E1  Per-stock HK diagnosis from the v14 pass (which names drag, exit churn).
E2  HK exempt from hold-model exits (prior evidence: v10.1 adaptive-exit ablation
    showed HK -0.27 — "HK trends run longer, exits hurt"). Env: GUSHEN_HOLD_EXIT_SKIP_MKTS=HK.
E3  AH-premium entry gate for dual-listed H shares: allow H-share entry only when the
    H side is cheap vs its own A share (AH ratio z-score >= 0 over 52w). Uses A-share
    closes already in the DB; FX via USDCNY (HKD pegged → constant 7.8 / usdcny).

Usage: python3 scripts/hk_study.py [e1|e2|e3]
Outputs HK sub-portfolio Sharpe (era1/era2) for baseline vs variant.
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

mode = sys.argv[1] if len(sys.argv) > 1 else 'e1'
if mode == 'e2':
    os.environ['GUSHEN_HOLD_EXIT_SKIP_MKTS'] = 'HK'
# e4: southbound-flow gate — block new HK entries when 20d southbound flow z < -1
# (event study June 2026: z<-1 → -2.28%/20d fwd vs +1.01% base, t=-22)

from strategy.scoring import score_bar_v5
from strategy.buckets import classify_buckets
from strategy.gushen_cache import get_ohlcv

macro = pickle.load(open(os.path.join(GUSHEN, 'data', 'macro_snapshot.pkl'), 'rb'))
cache = pickle.load(open(os.path.join(GUSHEN, 'data', 'precomp_cache.pkl'), 'rb'))
pcs = {k[0]: v for k, v in cache.items()}
PROG = os.path.join(GUSHEN, 'data', 'xsel_progress.pkl')
prog = pickle.load(open(PROG, 'rb'))
ERA = pd.Timestamp('2021-06-01')
all_weeks = pd.date_range('2016-06-03','2026-06-05',freq='W-FRI')

HK_NAMES = sorted({c for c, rec in prog.items()
                   for d, x in (rec or {}).items() if x[0] == 'HK'})

# AH pairs: H share -> A share
AH = {'1211.HK':'002594.SZ', '2318.HK':'601318.SH', '1398.HK':'601398.SH',
      '2628.HK':'601628.SH', '0386.HK':'600028.SH', '0857.HK':'601857.SH',
      '0939.HK':None}  # 601939 not in universe — skip

def sharpe(a):
    a = np.asarray(a, dtype=float)
    return round(float(np.sqrt(52)*a.mean()/a.std()), 3) if len(a) >= 8 and a.std() > 0 else 0.0

def hk_port(records):
    out = []
    for d in all_weeks:
        rows = [x for c in records for x in [records[c].get(d)] if x and x[2] and x[0]=='HK']
        if rows:
            ws = np.array([x[4] for x in rows]); rs = np.array([x[1]*x[5] for x in rows])
            out.append(float((ws*rs).sum()/ws.sum()))
        else:
            out.append(0.0)
    s = pd.Series(out, index=all_weeks)
    eq = (1+s).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
    return sharpe(s.values), sharpe(s[s.index<ERA].values), sharpe(s[s.index>=ERA].values), dd

if mode == 'e1':
    print('═══ E1: HK per-stock (v14 frozen stack) ═══')
    for c in HK_NAMES:
        rec = prog[c]
        rets = [x[1]*x[5] for x in rec.values() if x[2]]
        ent = sum(1 for d, x in sorted(rec.items()) if x[2] and not rec.get(pd.Timestamp(d)-pd.Timedelta(days=7), (0,0,False))[2])
        print(f"  {c:10s} in-pos weeks {len(rets):4d}  S {sharpe(rets):+.3f}  entries~{ent}")
    a, e1, e2, dd = hk_port(prog)
    print(f"\nHK baseline: S {a:+.3f} (era1 {e1:+.3f} / era2 {e2:+.3f}) dd {dd:.0%}")
    sys.exit(0)

# E2/E3: rerun HK names with the variant
def ah_z(hcode):
    acode = AH.get(hcode)
    if not acode: return None
    ha = get_ohlcv(hcode, 'HK'); aa = get_ohlcv(acode, 'A')
    if ha is None or aa is None: return None
    h = ha.sort_index()['close'].resample('W-FRI').last()
    a = aa.sort_index()['close'].resample('W-FRI').last()
    fx = macro.get('usdcny')
    fxw = fx.resample('W-FRI').last().reindex(h.index, method='ffill') if fx is not None else pd.Series(7.0, index=h.index)
    cnyhkd = 7.8 / fxw   # HKD per CNY via USD peg
    ratio = (a.reindex(h.index, method='ffill') * cnyhkd) / h   # premium of A over H; high => H cheap
    z = (ratio - ratio.rolling(52).mean()) / ratio.rolling(52).std()
    return z

variant = {}
for code in HK_NAMES:
    df = get_ohlcv(code, 'HK')
    if df is None or code not in pcs:
        variant[code] = prog[code]; continue
    df = df.sort_index()
    pc = pcs[code]
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    bkt = classify_buckets(dfw['close'])
    vol13 = dfw['close'].pct_change().rolling(13).std()
    z = ah_z(code) if mode == 'e3' else None
    if mode in ('e4', 'e5'):
        sf = macro['south_flow']
        s20 = sf.rolling(20).sum()
        z = (s20 - s20.rolling(250).mean()) / s20.rolling(250).std()
    rec = {}
    in_pos = False
    start_i = max(1, (dfw.index >= '2015-06-01').argmax() if (dfw.index >= '2015-06-01').any() else 1)
    for i in range(start_i, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i]) - 1
        r = score_bar_v5(di, df, pc, macro_data=macro, market='HK')
        b = bkt.iloc[i]
        if r['action'] == 'BUY':
            blocked = (b in ('FRAGILE','NA') and not in_pos)
            if mode == 'e3' and not in_pos and z is not None:
                zz = z.reindex([wk], method='ffill').iloc[0]
                if pd.notna(zz) and zz < 0:   # H expensive vs A → block entry
                    blocked = True
            if mode == 'e4' and not in_pos and z is not None:
                zz = z.reindex([wk], method='ffill').iloc[0]
                if pd.notna(zz) and zz < -1:  # heavy southbound outflow → block entry
                    blocked = True
            if not blocked:
                in_pos = True
        elif r['action'] == 'EXIT':
            in_pos = False
        # e5: southbound risk-off — EXIT held HK positions during heavy outflows (z<-1)
        if mode == 'e5' and in_pos and z is not None:
            zz = z.reindex([wk], method='ffill').iloc[0]
            if pd.notna(zz) and zz < -1:
                in_pos = False
        w = 1.0
        if pd.notna(vol13.iloc[i]) and vol13.iloc[i] > 0:
            w = min(3.0, 0.04/float(vol13.iloc[i]))
        rec[dfw.index[i+1]] = ('HK', float(ret), bool(in_pos),
                               float(r['composite']), float(w), float(r.get('macro_mult',1.0)))
    variant[code] = rec

base = {c: prog[c] for c in HK_NAMES}
a0, e10, e20, dd0 = hk_port(base)
a1, e11, e21, dd1 = hk_port(variant)
print(f'═══ {mode.upper()} — HK sub-portfolio ═══')
print(f'baseline: S {a0:+.3f} (era1 {e10:+.3f} / era2 {e20:+.3f}) dd {dd0:.0%}')
print(f'variant:  S {a1:+.3f} (era1 {e11:+.3f} / era2 {e21:+.3f}) dd {dd1:.0%}')
