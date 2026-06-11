#!/usr/bin/env python3
"""M3 experiment — FRAGILE gate: block contrarian BUY entries in the FRAGILE bucket
unless the 26-week slope of weekly MA20 is non-negative (structural downtrend guard).

Stacks on M2 (TREND×bull hold-with-trail, 15%) unless --no-m2.
Usage: python3 scripts/m3_fragile_gate.py [--no-m2] [--no-m3]
"""
import os, sys, json, pickle, argparse, numpy as np, pandas as pd, warnings
warnings.filterwarnings('ignore')
GUSHEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GUSHEN)
os.environ['GUSHEN_TUNE'] = '1'
os.environ.setdefault('GUSHEN_DB_PATH', os.path.join(GUSHEN, 'data', 'gushen.db'))
os.environ['GUSHEN_HOLD_EXIT'] = '1'
os.environ['GUSHEN_HOLD_EXIT_THRESH'] = '-2'
os.environ['GUSHEN_REGIME_HYST'] = '1'

ap = argparse.ArgumentParser()
ap.add_argument('--no-m2', action='store_true')
ap.add_argument('--no-m3', action='store_true')
ap.add_argument('--trail', type=float, default=0.15)
ap.add_argument('--block-all', action='store_true', help='block ALL FRAGILE entries (parameter-free)')
args = ap.parse_args()

from strategy.scoring import score_bar_v5
from strategy.buckets import classify_buckets
from strategy.gushen_cache import get_ohlcv, get_chip_concentration, get_holder_chg, get_analyst_signals

macro = pickle.load(open(os.path.join(GUSHEN, 'data', 'macro_snapshot.pkl'), 'rb'))
cache = pickle.load(open(os.path.join(GUSHEN, 'data', 'precomp_cache.pkl'), 'rb'))
pcs = {k[0]: v for k, v in cache.items()}
SPLIT = pd.Timestamp('2024-07-01')
STOCKS = [('600519.SH','A'),('000858.SZ','A'),('300750.SZ','A'),('002594.SZ','A'),('601318.SH','A'),
('600036.SH','A'),('002230.SZ','A'),('300015.SZ','A'),('0700.HK','HK'),('9988.HK','HK'),
('3690.HK','HK'),('1810.HK','HK'),('1211.HK','HK'),('0388.HK','HK'),('AAPL','US'),('NVDA','US'),
('MSFT','US'),('GOOGL','US'),('AMZN','US'),('META','US'),('JPM','US')]

def sharpe(a):
    a = np.asarray(a, dtype=float)
    return round(float(np.sqrt(52)*a.mean()/a.std()), 3) if len(a) >= 3 and a.std() > 0 else 0.0

port = {}
blocked = 0
total_weeks = 0
for code, mkt in STOCKS:
    df = get_ohlcv(code, mkt).sort_index()
    pc = pcs[code]
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    bkt = classify_buckets(dfw['close'])
    ma20w = dfw['close'].rolling(20).mean()
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

    weekly = {}
    in_pos = False
    peak = None
    start_i = max(50, (df.index >= '2021-06-01').argmax() if (df.index >= '2021-06-01').any() else 50)
    for i in range(start_i, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i]) - 1
        r = score_bar_v5(di, df, pc, macro_data=m2d, market=mkt)
        bull = bool(r['bull_regime'])
        b = bkt.iloc[i]
        px = dfw['close'].iloc[i]
        in_cell = (not args.no_m2) and bull and (b == 'TREND')

        if in_cell:
            if not in_pos:
                in_pos, peak = True, px
            else:
                peak = px if peak is None else max(peak, px)
                if px < peak * (1 - args.trail):
                    in_pos, peak = False, None
        else:
            peak = None
            if r['action'] == 'BUY':
                allow = True
                if (not args.no_m3) and b == 'FRAGILE' and not in_pos:
                    if args.block_all:
                        slope_ok = False
                    else:
                        slope_ok = (i >= 46 and pd.notna(ma20w.iloc[i]) and pd.notna(ma20w.iloc[i-26])
                                    and ma20w.iloc[i] >= ma20w.iloc[i-26])
                    if not slope_ok:
                        allow = False
                        blocked += 1
                if allow:
                    in_pos = True
            elif r['action'] == 'EXIT':
                in_pos = False
        if in_pos:
            weekly[dfw.index[i+1]] = ret * r.get('macro_mult', 1.0)
    total_weeks += len(weekly)
    for d, v in weekly.items():
        port.setdefault(d, []).append(v)
    if os.environ.get('M3_AUDIT') == '1':
        rets = np.array(list(weekly.values()))
        s = sharpe(rets) if len(rets) else 0.0
        print(f"  {code:10s} weeks={len(rets):4d} S={s:+.3f}")

all_weeks = pd.date_range('2021-06-04','2026-05-08',freq='W-FRI')
p = pd.Series([np.mean(port[d]) if d in port else 0.0 for d in all_weeks], index=all_weeks)
eq = (1+p).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
parts = []
if not args.no_m2: parts.append('m2')
if not args.no_m3: parts.append('m3')
label = '+'.join(parts) or 'control'
print(f"═══ {label} ═══")
print(f"PORTFOLIO S = {sharpe(p.values):+.3f} (IS {sharpe(p[p.index<SPLIT].values):+.3f} / OOS {sharpe(p[p.index>=SPLIT].values):+.3f}) | maxDD {dd:.1%} | weeks {total_weeks} | blocked entries {blocked}")
with open(os.path.join(GUSHEN,'data','bt2_results.jsonl'), 'a') as f:
    f.write(json.dumps({'label': f'm3-{label}', 'port': sharpe(p.values),
        'port_is': sharpe(p[p.index<SPLIT].values), 'port_oos': sharpe(p[p.index>=SPLIT].values),
        'maxdd': round(dd,3), 'weeks': total_weeks, 'blocked': blocked}) + '\n')
