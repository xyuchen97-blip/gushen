#!/usr/bin/env python3
"""M4 — portfolio layer on the adopted stack (v11 hold-exits + hyst + M3 FRAGILE block).

Per-market sub-portfolio Sharpe (A/HK/US) is reported for EVERY run and is part of the
acceptance gate: no market may regress > 0.10 vs the stack baseline.

Flags (test one at a time per protocol):
  --forgive       FRAGILE fast-forgiveness: allow entries if trailing 26w return >= +15%
                  (targets HK recovery capture, e.g. 0700 2024-26)
  --volsize       vol-targeted sizing: position weight ∝ 1/trailing 13w vol (weighted mean)
  --kill          drawdown kill-switch: halve exposure when portfolio DD < -10%,
                  restore when DD recovers above -5%
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
ap.add_argument('--forgive', action='store_true')
ap.add_argument('--volsize', action='store_true')
ap.add_argument('--kill', action='store_true')
ap.add_argument('--m2', action='store_true', help='TREND-bull hold-with-trail (15%) re-test')
ap.add_argument('--breakout', action='store_true', help='52w-high + volume breakout entries (validated: +2.27%%/5d t=3.3)')
ap.add_argument('--label', default='')
ap.add_argument('--universe', default='legacy', choices=['legacy','v13'])
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
if args.universe == 'v13':
    uni = json.load(open(os.path.join(GUSHEN, 'data', 'universe_v13_new.json')))
    STOCKS = STOCKS + [(c, m) for m in ('A','HK','US') for c in uni[m]
                       if c not in {s[0] for s in STOCKS}]

def sharpe(a):
    a = np.asarray(a, dtype=float)
    return round(float(np.sqrt(52)*a.mean()/a.std()), 3) if len(a) >= 3 and a.std() > 0 else 0.0

# rec[date] = list of (mkt, scaled_ret, weight)
rec, bnh = {}, {}
for code, mkt in STOCKS:
    df = get_ohlcv(code, mkt).sort_index()
    pc = pcs[code]
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    bkt = classify_buckets(dfw['close'])
    wret = dfw['close'].pct_change()
    vol13 = wret.rolling(13).std()
    ret26 = dfw['close'].pct_change(26)
    hi52 = dfw['close'].rolling(52).max().shift(1)
    bo_w = (dfw['close'] > hi52) & (dfw['volume'] > dfw['volume'].rolling(20).mean()*1.5)
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

    in_pos = False
    peak = None
    # v12 fix: start index must be computed on the WEEKLY grid (legacy bug computed it
    # on the daily index — harmless with 2021+ data, catastrophic with 2015+ history).
    start_i = max(1, (dfw.index >= '2021-06-01').argmax() if (dfw.index >= '2021-06-01').any() else 1)
    for i in range(start_i, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i]) - 1
        r = score_bar_v5(di, df, pc, macro_data=m2d, market=mkt)
        b = bkt.iloc[i]
        nd = dfw.index[i+1]
        bnh.setdefault(nd, []).append((mkt, ret))
        px = dfw['close'].iloc[i]
        in_cell = args.m2 and bool(r['bull_regime']) and (b == 'TREND')
        if in_cell:
            if not in_pos:
                in_pos, peak = True, px
            else:
                peak = px if peak is None else max(peak, px)
                if px < peak * 0.85:
                    in_pos, peak = False, None
        else:
            peak = None
            if r['action'] == 'BUY':
                # FRAGILE blocked; 'NA' (insufficient history to classify) also blocked —
                # no context, no new position.
                blocked = (b in ('FRAGILE', 'NA') and not in_pos)
                if blocked and args.forgive and pd.notna(ret26.iloc[i]) and ret26.iloc[i] >= 0.15:
                    blocked = False
                if not blocked:
                    in_pos = True
            elif r['action'] == 'EXIT':
                in_pos = False
            # 52w-high breakout entry (orthogonal momentum trigger; exits via hold model)
            # TREND-bucket only: breakout in an established high-Sharpe trend =
            # continuation; in REVERT/FRAGILE names it is often exhaustion.
            if args.breakout and not in_pos and bool(bo_w.iloc[i]) and b == 'TREND':
                in_pos = True
        if in_pos:
            w = 1.0
            if args.volsize and pd.notna(vol13.iloc[i]) and vol13.iloc[i] > 0:
                w = min(3.0, 0.04 / float(vol13.iloc[i]))  # target ~4% weekly vol, capped
            rec.setdefault(nd, []).append((mkt, ret * r.get('macro_mult', 1.0), w))

all_weeks = pd.date_range('2021-06-04','2026-05-08',freq='W-FRI')
def port_series(filter_mkt=None):
    out = []
    for d in all_weeks:
        rows = [x for x in rec.get(d, []) if filter_mkt is None or x[0] == filter_mkt]
        if rows:
            ws = np.array([x[2] for x in rows]); rs = np.array([x[1] for x in rows])
            out.append(float((ws*rs).sum()/ws.sum()))
        else:
            out.append(0.0)
    return pd.Series(out, index=all_weeks)

p = port_series()
if args.kill:
    scaled, eqv, peak, scale = [], 1.0, 1.0, 1.0
    for v in p.values:
        scaled.append(v*scale)
        eqv *= (1+v*scale); peak = max(peak, eqv)
        ddn = eqv/peak - 1
        if ddn < -0.10: scale = 0.5
        elif ddn > -0.05: scale = 1.0
    p = pd.Series(scaled, index=all_weeks)

def stats(s):
    eq = (1+s).cumprod(); dd = float(((eq-eq.cummax())/eq.cummax()).min())
    return sharpe(s.values), sharpe(s[s.index<SPLIT].values), sharpe(s[s.index>=SPLIT].values), dd

def bnh_series(mkt=None):
    out = []
    for d in all_weeks:
        rows = [x[1] for x in bnh.get(d, []) if mkt is None or x[0] == mkt]
        out.append(float(np.mean(rows)) if rows else 0.0)
    return pd.Series(out, index=all_weeks)

label = args.label or ('+'.join([f for f, on in
    [('forgive',args.forgive),('volsize',args.volsize),('kill',args.kill)] if on]) or 'stack-base')
ps, pis, pos_, dd = stats(p)
print(f"═══ m4:{label} ═══")
print(f"ALL: S {ps:+.3f} (IS {pis:+.3f} / OOS {pos_:+.3f}) maxDD {dd:.1%}")
for mkt in ['A','HK','US']:
    ms, mis, mos, mdd = stats(port_series(mkt))
    bs, _, _, bdd = stats(bnh_series(mkt))
    print(f"  {mkt}: S {ms:+.3f} (IS {mis:+.3f}/OOS {mos:+.3f}) dd {mdd:.0%} | B&H S {bs:+.3f} dd {bdd:.0%}")
with open(os.path.join(GUSHEN,'data','bt2_results.jsonl'),'a') as f:
    f.write(json.dumps({'label': f'm4-{label}', 'port': ps, 'port_is': pis, 'port_oos': pos_,
                        'maxdd': round(dd,3)})+'\n')
