#!/usr/bin/env python3
"""Cost sensitivity — applies per-market transaction costs to the STORED daily-cadence
pass (data/dailywide_progress.pkl). No rescoring; pure finalize-layer simulation.

Cost assumptions (per SIDE, includes commission + stamp duty + slippage):
  A:  buy 0.15%, sell 0.25% (0.1% stamp on sells) | HK: 0.20% each side (0.1% stamp
  + costs) | US: 0.05% each side. A 'stress' variant doubles everything.
"""
import os, sys, pickle, numpy as np, pandas as pd, warnings
warnings.filterwarnings('ignore')
GUSHEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(GUSHEN, 'data', 'dailywide_progress.pkl')
prog = pickle.load(open(PROG, 'rb'))
ERA = pd.Timestamp('2021-06-01')

COSTS = {'A': (0.0015, 0.0025), 'HK': (0.0020, 0.0020), 'US': (0.0005, 0.0005)}

def simulate(cost_mult=0.0):
    port, trades = {}, 0
    for code, v in prog.items():
        if v is None: continue
        dates, rows = v
        in_pos = False
        for d, (mkt, ret, act, b, w, mm) in zip(dates, rows):
            buy_c, sell_c = COSTS[mkt]
            entry_cost = exit_cost = 0.0
            if act == 'BUY' and not (b in ('FRAGILE','NA') and not in_pos):
                if not in_pos:
                    entry_cost = buy_c * cost_mult; trades += 1
                in_pos = True
            elif act == 'EXIT' and in_pos:
                in_pos = False
                exit_cost = sell_c * cost_mult; trades += 1
                port.setdefault(d, []).append(((-exit_cost), w))
                continue
            if in_pos:
                port.setdefault(d, []).append((ret*mm - entry_cost, w))
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
        return round(float(np.sqrt(252)*x.mean()/x.std()), 3) if x.std() > 0 else 0.0
    eq = (1+s).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
    return sh(s), sh(s[s.index<ERA]), sh(s[s.index>=ERA]), dd, trades

print('═══ COST SENSITIVITY — daily cadence, 132 names, vol-weighted ═══')
for label, m in [('no costs (reference)', 0.0), ('base costs', 1.0), ('2x stress', 2.0)]:
    a, e1, e2, dd, tr = simulate(m)
    print(f'{label:22s} S {a:+.3f} (era1 {e1:+.3f} / era2 {e2:+.3f}) maxDD {dd:.1%} | round-trip legs {tr}')
