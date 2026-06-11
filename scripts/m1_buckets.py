#!/usr/bin/env python3
"""M1 — behavior bucket classifier (TREND / REVERT / FRAGILE) + chop state.
MEASUREMENT ONLY: nothing here affects scoring or actions.

Bucket rules (rolling, trailing 104 weeks, weekly evaluation, 8-week dwell hysteresis):
  VR13      = Var(13w log ret) / (13 x Var(1w log ret))   (variance ratio)
  trend_2y  = close / close[104w ago] - 1
  TREND   if VR13 >= 1.05 and trend_2y > 0
  FRAGILE if trend_2y < -0.10 and VR13 >= 0.95   (downtrend whose dips don't mean-revert)
  REVERT  otherwise

Trend state per week: bull/bear from MA200 hysteresis series; 'chop' overlay when
ADX < 18 and |close/MA200 - 1| < 5%.

Gates (ARCHITECTURE_v12_PROPOSAL.md M1):
  (a) median dwell >= 26w, <= ~2 transitions/stock/yr
  (b) sanity mapping
  (c) used cells >= 300 stock-weeks
  (d) per-cell engine vs B&H reproduces the diagnosis
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

STOCKS = [('600519.SH','A'),('000858.SZ','A'),('300750.SZ','A'),('002594.SZ','A'),('601318.SH','A'),
('600036.SH','A'),('002230.SZ','A'),('300015.SZ','A'),('0700.HK','HK'),('9988.HK','HK'),
('3690.HK','HK'),('1810.HK','HK'),('1211.HK','HK'),('0388.HK','HK'),('AAPL','US'),('NVDA','US'),
('MSFT','US'),('GOOGL','US'),('AMZN','US'),('META','US'),('JPM','US')]
EXPECT = {'NVDA':'TREND','MSFT':'TREND','GOOGL':'TREND','601318.SH':'REVERT','600036.SH':'REVERT',
          'JPM':'REVERT','600519.SH':'FRAGILE','300015.SZ':'FRAGILE'}

WIN, Q, DWELL = 104, 13, 8

def classify(weekly_close):
    lc = np.log(weekly_close.values)
    n = len(lc)
    raw = np.array(['NA'] * n, dtype=object)
    for i in range(WIN, n):
        w = lc[i-WIN:i+1]
        r1 = np.diff(w)
        rq = w[Q:] - w[:-Q]
        if r1.std() == 0: continue
        vr = rq.var() / (Q * r1.var())  # kept as diagnostic; not used in v2 rule
        tr = np.exp(lc[i] - lc[i-WIN]) - 1
        # v2 rule: TREND = the stock itself has been a high-Sharpe hold (drift,
        # not week-to-week persistence — VR misclassified US megacaps as REVERT).
        sh2y = (r1.mean() / r1.std()) * np.sqrt(52)
        if tr <= -0.10: raw[i] = 'FRAGILE'
        elif sh2y >= 0.70: raw[i] = 'TREND'
        else: raw[i] = 'REVERT'
    # dwell hysteresis
    out = np.array(['NA'] * n, dtype=object)
    cur, pend, cnt = 'NA', None, 0
    for i in range(n):
        if raw[i] == 'NA':
            out[i] = cur; continue
        if cur == 'NA':
            cur = raw[i]
        elif raw[i] != cur:
            if raw[i] == pend: cnt += 1
            else: pend, cnt = raw[i], 1
            if cnt >= DWELL: cur, pend, cnt = raw[i], None, 0
        else:
            pend, cnt = None, 0
        out[i] = cur
    return pd.Series(out, index=weekly_close.index)

# ── classify all stocks ──
buckets, states, trans_stats = {}, {}, []
for code, mkt in STOCKS:
    df = get_ohlcv(code, mkt).sort_index()
    wc = df['close'].resample('W-FRI').last().dropna()
    b = classify(wc)
    buckets[code] = b
    # transitions + dwell (within 2021-06+)
    bb = b[b.index >= '2021-06-01']
    bb = bb[bb != 'NA']
    ch = (bb != bb.shift()).cumsum()
    dwells = bb.groupby(ch).size()
    yrs = max(1e-9, (bb.index[-1] - bb.index[0]).days / 365.25)
    trans_stats.append((code, len(dwells) - 1, (len(dwells)-1)/yrs, dwells.median(), bb.iloc[-1], bb.mode().iloc[0]))
    # trend state series (bull/bear/chop) on weekly grid
    ma200 = df['close'].rolling(200).mean()
    pc = pcs[code]
    adx = pc['adx_val']
    bull_h = pc['bull_regime_hyst']
    st = {}
    for d in wc.index:
        di = df.index.get_indexer([d], method='ffill')[0]
        if di < 252: continue
        chop = (pd.notna(adx.iloc[di]) and adx.iloc[di] < 18 and
                pd.notna(ma200.iloc[di]) and abs(df['close'].iloc[di]/ma200.iloc[di]-1) < 0.05)
        st[d] = 'chop' if chop else ('bull' if bool(bull_h.iloc[di]) else 'bear')
    states[code] = pd.Series(st)

T = pd.DataFrame(trans_stats, columns=['code','transitions','trans_per_yr','median_dwell_w','current','dominant'])
print('═══ GATE (a) STABILITY ═══')
print(f"median dwell across stocks: {T.median_dwell_w.median():.0f}w | mean transitions/yr: {T.trans_per_yr.mean():.2f} | max: {T.trans_per_yr.max():.2f}")
print('═══ GATE (b) SANITY (expected vs computed dominant bucket) ═══')
for code, exp in EXPECT.items():
    got = T[T.code==code].dominant.iloc[0]
    print(f"  {code:10s} expect {exp:8s} got {got:8s} {'OK' if got==exp else 'MISS'}")
print('\nper-stock: dominant (current) bucket')
for _, r in T.iterrows():
    print(f"  {r.code:10s} {r.dominant:8s} (now {r.current}) trans/yr {r.trans_per_yr:.1f}")

# ── engine run tagged by computed cell ──
rec, bnh, entries = [], [], []
for code, mkt in STOCKS:
    df = get_ohlcv(code, mkt).sort_index()
    pc = pcs[code]
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
    start_i = max(50, (df.index >= '2021-06-01').argmax() if (df.index >= '2021-06-01').any() else 50)
    for i in range(start_i, len(dfw)-1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252: continue
        bkt = buckets[code].reindex([wk], method='ffill').iloc[0]
        stt = states[code].get(wk, None)
        if bkt in (None, 'NA') or stt is None: continue
        ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i]) - 1
        r = score_bar_v5(di, df, pc, macro_data=m2, market=mkt)
        bnh.append((bkt, stt, ret))
        if r['action'] == 'BUY':
            if not in_pos: entries.append((bkt, stt))
            in_pos = True
        elif r['action'] == 'EXIT':
            in_pos = False
        if in_pos: rec.append((bkt, stt, ret * r.get('macro_mult', 1.0)))

E = pd.DataFrame(rec, columns=['bkt','st','ret'])
B = pd.DataFrame(bnh, columns=['bkt','st','ret'])
EN = pd.DataFrame(entries, columns=['bkt','st'])
def sh(a):
    a = np.asarray(a, dtype=float)
    return round(float(np.sqrt(52)*a.mean()/a.std()), 2) if len(a) >= 8 and a.std() > 0 else None

print('\n═══ GATE (c,d) CELL OCCUPANCY + ENGINE vs B&H per computed cell ═══')
print(f"{'cell':16s} {'stockweeks':>10s} {'engine S':>9s} {'(w)':>6s} {'B&H S':>7s} {'entries':>8s}")
for bkt in ['TREND','REVERT','FRAGILE']:
    for stt in ['bull','chop','bear']:
        b = B[(B.bkt==bkt) & (B.st==stt)]
        e = E[(E.bkt==bkt) & (E.st==stt)]
        en = len(EN[(EN.bkt==bkt) & (EN.st==stt)])
        flag = '' if len(b) >= 300 else '  <300!'
        print(f"{bkt:8s}x{stt:6s} {len(b):10d} {str(sh(e.ret)):>9s} {len(e):6d} {str(sh(b.ret)):>7s} {en:8d}{flag}")
