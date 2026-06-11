#!/usr/bin/env python3
"""Diagnosis: engine performance by style group x regime x market, vs buy-and-hold.
Uses the recommended v11 variant (hold-exit + hysteresis) and the pinned macro snapshot.
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
from strategy.gushen_cache import get_ohlcv, get_chip_concentration, get_holder_chg, get_analyst_signals

macro = pickle.load(open(os.path.join(GUSHEN, 'data', 'macro_snapshot.pkl'), 'rb'))
cache = pickle.load(open(os.path.join(GUSHEN, 'data', 'precomp_cache.pkl'), 'rb'))
pcs = {k[0]: v for k, v in cache.items()}

GROUPS = {
    '600519.SH': ('staples', 'A'), '000858.SZ': ('staples', 'A'),
    '300750.SZ': ('ev_battery', 'A'), '002594.SZ': ('ev_battery', 'A'), '1211.HK': ('ev_battery', 'HK'),
    '601318.SH': ('financials', 'A'), '600036.SH': ('financials', 'A'),
    'JPM': ('financials', 'US'), '0388.HK': ('financials', 'HK'),
    '002230.SZ': ('volatile_tech', 'A'), '300015.SZ': ('healthcare', 'A'),
    '0700.HK': ('cn_platform', 'HK'), '9988.HK': ('cn_platform', 'HK'), '3690.HK': ('cn_platform', 'HK'),
    '1810.HK': ('hardware', 'HK'), 'AAPL': ('hardware', 'US'),
    'NVDA': ('us_megacap', 'US'), 'MSFT': ('us_megacap', 'US'), 'GOOGL': ('us_megacap', 'US'),
    'AMZN': ('us_megacap', 'US'), 'META': ('us_megacap', 'US'),
}

def sharpe(a):
    a = np.asarray(a, dtype=float)
    return round(float(np.sqrt(52)*a.mean()/a.std()), 2) if len(a) >= 8 and a.std() > 0 else None

rec = []      # (code, group, mkt, date, ret, regime, active)
bnh = []      # (code, group, mkt, date, ret, regime)
entries = []  # (code, mkt, group, regime) per BUY event

for code, (grp, mkt) in GROUPS.items():
    df = get_ohlcv(code, mkt).sort_index()
    pc = pcs.get(code)
    if pc is None: continue
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
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
    in_pos = False
    prev_act = None
    start_i = max(50, (df.index >= '2021-06-01').argmax() if (df.index >= '2021-06-01').any() else 50)
    for i in range(start_i, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i]) - 1
        r = score_bar_v5(di, df, pc, macro_data=m2, market=mkt)
        regime = r['regime']
        bnh.append((code, grp, mkt, dfw.index[i+1], ret, regime))
        if r['action'] == 'BUY':
            if not in_pos:
                entries.append((code, mkt, grp, regime))
            in_pos = True
        elif r['action'] == 'EXIT':
            in_pos = False
        if in_pos:
            rec.append((code, grp, mkt, dfw.index[i+1], ret*r.get('macro_mult',1.0), regime))

E = pd.DataFrame(rec, columns=['code','grp','mkt','date','ret','regime'])
B = pd.DataFrame(bnh, columns=['code','grp','mkt','date','ret','regime'])
EN = pd.DataFrame(entries, columns=['code','mkt','grp','regime'])

def port_sharpe(df_):
    p = df_.groupby('date')['ret'].mean()
    full = pd.date_range('2021-06-04','2026-05-08',freq='W-FRI')
    p = p.reindex(full).fillna(0.0)
    return sharpe(p.values)

print('═══ ENGINE (hold-exit+hyst) vs BUY-AND-HOLD, portfolio Sharpe ═══')
print(f"engine ALL: {port_sharpe(E)}   B&H ALL: {port_sharpe(B)}")
for mkt in ['A','HK','US']:
    print(f"  {mkt}: engine {port_sharpe(E[E.mkt==mkt])}  vs B&H {port_sharpe(B[B.mkt==mkt])}")

print('\n═══ BY STYLE GROUP: engine in-position weeks Sharpe | B&H Sharpe | weeks ═══')
for g in sorted(set(x[0] for x in GROUPS.values())):
    e, b = E[E.grp==g], B[B.grp==g]
    print(f"  {g:13s} engine {str(sharpe(e.ret)):>5s} ({len(e):4d}w) | B&H {str(sharpe(b.ret)):>5s} ({len(b):4d}w)")

print('\n═══ BY STYLE GROUP x REGIME (engine in-position weeks) ═══')
for g in sorted(set(x[0] for x in GROUPS.values())):
    row = f"  {g:13s}"
    for reg in ['bull','bear']:
        e = E[(E.grp==g) & (E.regime==reg)]
        b = B[(B.grp==g) & (B.regime==reg)]
        row += f"  {reg}: eng {str(sharpe(e.ret)):>5s}/{len(e):4d}w (B&H {str(sharpe(b.ret)):>5s})"
    print(row)

print('\n═══ ENTRY EVENTS BY MARKET x REGIME ═══')
print(EN.groupby(['mkt','regime']).size().unstack(fill_value=0))
print('\n═══ ENTRY EVENTS BY GROUP ═══')
print(EN.groupby('grp').size().sort_values(ascending=False).to_string())

print('\n═══ CONCENTRATION: simultaneous positions ═══')
conc = E.groupby('date')['code'].nunique()
mc = E.groupby('date').apply(lambda x: x['mkt'].value_counts().max()/len(x))
print(f"avg simultaneous positions: {conc.mean():.1f}, max: {conc.max()}")
print(f"avg single-market share of open positions: {mc.mean():.0%}")
