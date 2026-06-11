#!/usr/bin/env python3
"""M2 experiment — TREND×bull policy: hold-with-trailing-stop instead of entry timing.

Policy (single new parameter: TRAIL=15%):
  cell == (TREND, bull):  be invested; exit only on trailing stop (peak close -15%)
  any other cell:         v11 engine unchanged (hold-exit + hysteresis)
  positions carried out of the cell hand over to the v11 hold-health logic.

Gate (vs v11-official-holdexit): portfolio S > 0.609 AND OOS >= 0.903 - 0.05.
Usage: python3 scripts/m2_trend_bull.py [--trail 0.15] [--off]   (--off = control rerun)
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
ap.add_argument('--trail', type=float, default=0.15)
ap.add_argument('--off', action='store_true', help='disable policy (control)')
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

port, R = {}, {}
policy_weeks = 0
for code, mkt in STOCKS:
    df = get_ohlcv(code, mkt).sort_index()
    pc = pcs[code]
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    bkt = classify_buckets(dfw['close'])
    m2 = dict(macro)
    try:
        ats = get_analyst_signals(code, mkt)
        if ats is not None and len(ats) > 0: m2['analyst_signals'] = ats
    except Exception: pass
    if mkt == 'A':
        try: m2['chip_conc'] = get_chip_concentration(code)
        except Exception: pass
        try: m2['holder_chg'] = get_holder_chg(code)
        except Exception: pass

    weekly = {}
    in_pos = False
    pol_pos = False          # position owned by the TREND×bull policy
    peak = None
    start_i = max(50, (df.index >= '2021-06-01').argmax() if (df.index >= '2021-06-01').any() else 50)
    for i in range(start_i, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i]) - 1
        r = score_bar_v5(di, df, pc, macro_data=m2, market=mkt)
        bull = bool(r['bull_regime'])
        in_cell = (not args.off) and bull and (bkt.iloc[i] == 'TREND')
        px = dfw['close'].iloc[i]

        if in_cell:
            policy_weeks += 1
            if not in_pos:
                in_pos, pol_pos, peak = True, True, px
            else:
                pol_pos = True
                peak = px if peak is None else max(peak, px)
                if px < peak * (1 - args.trail):
                    in_pos, pol_pos, peak = False, False, None
        else:
            # outside the cell: v11 engine semantics (policy position hands over)
            pol_pos, peak = False, None
            if r['action'] == 'BUY':
                in_pos = True
            elif r['action'] == 'EXIT':
                in_pos = False
        if in_pos:
            weekly[dfw.index[i+1]] = ret * r.get('macro_mult', 1.0)
    rets = list(weekly.values())
    R[code] = {'s': sharpe(rets), 'n': len(rets)}
    for d, v in weekly.items():
        port.setdefault(d, []).append(v)

all_weeks = pd.date_range('2021-06-04','2026-05-08',freq='W-FRI')
p = pd.Series([np.mean(port[d]) if d in port else 0.0 for d in all_weeks], index=all_weeks)
eq = (1+p).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
ps, pis, pos_ = sharpe(p.values), sharpe(p[p.index<SPLIT].values), sharpe(p[p.index>=SPLIT].values)
label = f"m2-trend-bull-trail{int(args.trail*100)}" if not args.off else 'm2-control'
print(f"═══ {label} ═══")
print(f"PORTFOLIO S = {ps:+.3f} (IS {pis:+.3f} / OOS {pos_:+.3f}) | maxDD {dd:.1%} | weeks {sum(v['n'] for v in R.values())} | policy cell-weeks {policy_weeks}")
print(f"v11 reference: +0.609 (IS +0.354 / OOS +0.903) | maxDD -17.0% | weeks 2452")
row = {'label': label, 'port': ps, 'port_is': pis, 'port_oos': pos_, 'maxdd': round(dd,3),
       'weeks': sum(v['n'] for v in R.values()), 'stocks': R}
with open(os.path.join(GUSHEN,'data','bt2_results.jsonl'), 'a') as f:
    f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
