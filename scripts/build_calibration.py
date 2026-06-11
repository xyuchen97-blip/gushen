#!/usr/bin/env python3
"""v16 calibration layer — composite score → EMPIRICAL forward-return distributions.

Built from the stored 132-name weekly pass (data/xsel_progress.pkl, 2016-2026):
for every stock-week, the engine composite and the realized forward 1w and 4w returns.
Output: data/calibration.json — bins with P(positive), mean/median fwd returns, n.

This turns "comp 34.2" into "historically 64% positive over 4w, avg +2.1% (n=1842)".
No new research, no new parameters — pure empirical translation of what already exists.
"""
import os, json, pickle, numpy as np, pandas as pd

GUSHEN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
prog = pickle.load(open(os.path.join(GUSHEN, 'data', 'xsel_progress.pkl'), 'rb'))

BINS = [(0, 10), (10, 17), (17, 24), (24, 28), (28, 35), (35, 45), (45, 999)]

rows = []  # (mkt, comp, fwd1, fwd4)
for code, rec in prog.items():
    if not rec:
        continue
    dates = sorted(rec)
    rets = np.array([rec[d][1] for d in dates])
    comps = np.array([rec[d][3] for d in dates])
    mkts = [rec[d][0] for d in dates]
    n = len(dates)
    for i in range(n - 4):
        f1 = rets[i]                               # stored ret IS the next-week return
        f4 = float(np.prod(1 + rets[i:i+4]) - 1)
        rows.append((mkts[i], float(comps[i]), float(f1), f4))

df = pd.DataFrame(rows, columns=['mkt', 'comp', 'f1', 'f4'])
print(f'{len(df)} stock-weeks from {len(prog)} names')

def table(sub):
    out = []
    for lo, hi in BINS:
        b = sub[(sub.comp >= lo) & (sub.comp < hi)]
        if len(b) < 200:
            continue
        out.append({
            'bin': f'{lo}-{hi if hi < 999 else "+"}',
            'lo': lo, 'hi': hi, 'n': int(len(b)),
            'p_pos_4w': round(float((b.f4 > 0).mean()), 3),
            'mean_4w': round(float(b.f4.mean()), 4),
            'median_4w': round(float(b.f4.median()), 4),
            'p_pos_1w': round(float((b.f1 > 0).mean()), 3),
            'mean_1w': round(float(b.f1.mean()), 4),
        })
    return out

# ── (2) ENTRY-EVENT calibration: what happens after the engine actually enters ──
ev = []
for code, rec in prog.items():
    if not rec: continue
    dates = sorted(rec)
    for i in range(1, len(dates) - 4):
        prev_in = rec[dates[i-1]][2]
        cur_in = rec[dates[i]][2]
        if cur_in and not prev_in:  # entry week
            f4 = float(np.prod([1 + rec[dates[i+k]][1] for k in range(4)]) - 1)
            ev.append((rec[dates[i]][0], float(rec[dates[i]][3]), f4))
EV = pd.DataFrame(ev, columns=['mkt', 'comp', 'f4'])
entry_cal = {'n': int(len(EV)), 'p_pos_4w': round(float((EV.f4 > 0).mean()), 3),
             'mean_4w': round(float(EV.f4.mean()), 4), 'median_4w': round(float(EV.f4.median()), 4),
             'by_market': {m: {'n': int((EV.mkt == m).sum()),
                               'p_pos_4w': round(float((EV[EV.mkt == m].f4 > 0).mean()), 3),
                               'mean_4w': round(float(EV[EV.mkt == m].f4.mean()), 4)}
                           for m in ('A', 'HK', 'US') if (EV.mkt == m).sum() > 30}}

# ── (3) RANK-TIER calibration: weekly cross-sectional rank among in-position names ──
by_date = {}
for code, rec in prog.items():
    if not rec: continue
    for d, x in rec.items():
        if x[2]:
            by_date.setdefault(d, []).append((x[3], x[1]))   # (comp, fwd 1w ret)
tiers = {'top30': [], 'rank31_60': [], 'rest': []}
for d, lst in by_date.items():
    lst = sorted(lst, key=lambda t: -t[0])
    tiers['top30'].extend(r for _, r in lst[:30])
    tiers['rank31_60'].extend(r for _, r in lst[30:60])
    tiers['rest'].extend(r for _, r in lst[60:])
rank_cal = {k: {'n': len(v), 'p_pos_1w': round(float(np.mean(np.array(v) > 0)), 3),
                'mean_1w': round(float(np.mean(v)), 4),
                'ann_sharpe': round(float(np.sqrt(52)*np.mean(v)/np.std(v)), 2)}
            for k, v in tiers.items() if len(v) > 200}

cal = {'built': '2026-06-10', 'source': 'xsel_progress 132 names 2016-2026',
       'NOTE': ('Absolute composite level is NOT a return scale (flat ~55% across bins). '
                'Its value is (a) entry timing — see entry_events — and (b) cross-sectional '
                'ranking — see rank_tiers. Present those, not the raw bins.'),
       'global_bins': table(df),
       'entry_events': entry_cal,
       'rank_tiers': rank_cal,
       'by_market': {m: table(df[df.mkt == m]) for m in ('A', 'HK', 'US')}}
out_path = os.path.join(GUSHEN, 'data', 'calibration.json')
json.dump(cal, open(out_path, 'w'), indent=1)
print(f'written: {out_path}')
print(f"\n{'bin':8s} {'n':>7s} {'P(+4w)':>7s} {'avg4w':>7s} {'med4w':>7s}")
for r in cal['global_bins']:
    print(f"{r['bin']:8s} {r['n']:7d} {r['p_pos_4w']:7.0%} {r['mean_4w']:+7.2%} {r['median_4w']:+7.2%}")
