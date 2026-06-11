#!/usr/bin/env python3
"""bt2 — v11 experiment harness.

Improvements over fast_backtest.py:
  * Pinned macro snapshot (data/macro_snapshot.pkl) — reproducible baselines
  * Pickled precompute cache — reruns skip indicator computation (~10x faster)
  * Portfolio-level equity curve Sharpe (equal-weight across active positions),
    in addition to the legacy per-stock active-week Sharpe
  * Walk-forward split: IS (start..2024-06-30) vs OOS (2024-07-01..end)
  * No silent exceptions — scoring errors are counted and reported
  * Variant flags for structural experiments

Usage:
  python3 scripts/bt2.py                       # baseline (must reproduce fast_backtest)
  python3 scripts/bt2.py --fund                # add fund_bonus to composite (GUSHEN_FUND_IN_COMPOSITE)
  python3 scripts/bt2.py --hyst                # regime hysteresis (GUSHEN_REGIME_HYST)
  python3 scripts/bt2.py --hold-exit           # entry/hold separation: exits from hold model
  python3 scripts/bt2.py --disable SIGNAL      # leave-one-out signal ablation
  python3 scripts/bt2.py --list-signals        # show ablatable signals
  python3 scripts/bt2.py --rebuild-cache       # force precompute cache rebuild
  python3 scripts/bt2.py --label NAME          # tag the result row
"""
import os, sys, json, pickle, argparse, warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
GUSHEN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GUSHEN))
os.environ['GUSHEN_TUNE'] = '1'
os.environ.setdefault('GUSHEN_DB_PATH', str(GUSHEN / 'data' / 'gushen.db'))

ap = argparse.ArgumentParser()
ap.add_argument('--fund', action='store_true')
ap.add_argument('--hyst', action='store_true')
ap.add_argument('--hold-exit', action='store_true')
ap.add_argument('--hold-exit-thresh', type=float, default=-4.0)
ap.add_argument('--disable', default='')
ap.add_argument('--list-signals', action='store_true')
ap.add_argument('--rebuild-cache', action='store_true')
ap.add_argument('--label', default='')
ap.add_argument('--quiet', action='store_true')
args = ap.parse_args()

if args.fund:
    os.environ['GUSHEN_FUND_IN_COMPOSITE'] = '1'
if args.hyst:
    os.environ['GUSHEN_REGIME_HYST'] = '1'
if args.hold_exit:
    # v11: hold-model exits now live IN scoring.py (production path).
    # bt2 just sets the env flags — backtest and production share the code.
    os.environ['GUSHEN_HOLD_EXIT'] = '1'
    os.environ['GUSHEN_HOLD_EXIT_THRESH'] = str(args.hold_exit_thresh)

from strategy.scoring import precompute, score_bar_v5
from strategy.gushen_cache import (get_ohlcv, get_chip_concentration, get_holder_chg,
                                   get_analyst_signals)

SNAP = GUSHEN / 'data' / 'macro_snapshot.pkl'
PC_CACHE = GUSHEN / 'data' / 'precomp_cache.pkl'
SPLIT_DATE = pd.Timestamp('2024-07-01')

with open(SNAP, 'rb') as f:
    macro = pickle.load(f)

STOCKS = [
    ('600519.SH','茅台','A'),('000858.SZ','五粮液','A'),('300750.SZ','宁德时代','A'),
    ('002594.SZ','比亚迪','A'),('601318.SH','平安','A'),('600036.SH','招行','A'),
    ('002230.SZ','科大讯飞','A'),('300015.SZ','爱尔眼科','A'),
    ('0700.HK','腾讯','HK'),('9988.HK','阿里','HK'),('3690.HK','美团','HK'),
    ('1810.HK','小米','HK'),('1211.HK','比亚迪','HK'),('0388.HK','港交所','HK'),
    ('AAPL','苹果','US'),('NVDA','英伟达','US'),('MSFT','微软','US'),
    ('GOOGL','谷歌','US'),('AMZN','亚马逊','US'),('META','Meta','US'),
    ('JPM','摩根大通','US'),
]

# ── Ablatable signals: name → list of precomputed keys to neutralize ──
FALSE_KEYS = {
    'golden_pit':   ['golden_pit', 'band_low'],
    'nine_turns':   ['buy_signal', 'buy_setup_done'],
    'band_king':    ['buy2', 'sell1'],  # both contaminated by centered-window look-ahead
    'bb_buy':       ['bb_buy'],
    'kdj_golden':   ['kdj_golden'],
    'divergence':   ['bullish_divergence'],
    'chain':        [f'chain_c{c}_w{w}' for c in (2, 3) for w in (3, 5, 8)],
    'fib':          ['weekly_fib_support', 'weekly_fib_support_bull'],
    'vol_anomaly':  ['vol_anomaly'],
    'ma_aligned':   ['ma_aligned'],
    'adx_strong':   ['adx_strong'],
    'macd_golden':  ['macd_golden'],
    'ma_golden':    ['ma_golden'],
}
NEUTRAL_KEYS = {  # continuous depth signals: key → neutral value
    'kdj_depth': ('kdj_j', 50.0),
    'bb_depth':  ('bb_pct', 0.5),
    'rsi_depth': ('rsi', 50.0),
}

if args.list_signals:
    print(','.join(list(FALSE_KEYS) + list(NEUTRAL_KEYS)))
    sys.exit(0)

def neutralize(pc, sig):
    if sig in FALSE_KEYS:
        for k in FALSE_KEYS[sig]:
            v = pc.get(k)
            if v is None:
                continue
            if isinstance(v, pd.Series):
                pc[k] = pd.Series(np.zeros(len(v), dtype=v.dtype if v.dtype == bool else float),
                                  index=v.index).astype(v.dtype)
            else:
                pc[k] = np.zeros(len(v), dtype=bool)
    elif sig in NEUTRAL_KEYS:
        k, val = NEUTRAL_KEYS[sig]
        pc[k] = pd.Series(val, index=pc[k].index)
    else:
        raise SystemExit(f'unknown signal: {sig}')

# ── Load OHLCV + precompute (cached) ─────────────────────────────
def load_all():
    cache = {}
    if PC_CACHE.exists() and not args.rebuild_cache:
        with open(PC_CACHE, 'rb') as f:
            cache = pickle.load(f)
    data, dirty = {}, False
    for code, name, mkt in STOCKS:
        df = get_ohlcv(code, mkt)
        if df is None or len(df) < 100:
            print(f'  [WARN] no data: {code}')
            continue
        df = df.sort_index()
        dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min',
                                        'close':'last','volume':'sum'}).dropna()
        key = (code, len(df), str(df.index[-1]))
        if key in cache:
            pc = cache[key]
        else:
            pc = precompute(df, dfw)
            cache = {k: v for k, v in cache.items() if k[0] != code}
            cache[key] = pc
            dirty = True
        data[code] = (df, dfw, pc, mkt)
    if dirty:
        with open(PC_CACHE, 'wb') as f:
            pickle.dump(cache, f)
    return data

# ── Hold model (entry/hold separation experiment) ────────────────
def hold_health(pc, di):
    g = lambda k: bool(pc[k].iloc[di]) if pd.notna(pc[k].iloc[di]) else False
    hh = 0.0
    if g('ma_aligned'):        hh += 3
    if g('price_above_ma50'):  hh += 2
    if g('adx_strong'):        hh += 2
    if g('weekly_ma20_up'):    hh += 2
    if g('ma_death'):          hh -= 3
    if g('macd_death'):        hh -= 2
    if g('bb_sell'):           hh -= 4
    if g('sell_signal'):       hh -= 2   # nine turns sell
    if g('sell1'):             hh -= 2   # band king sell
    return hh

# ── Backtest one stock ───────────────────────────────────────────
def run_stock(code, df, dfw, pc, mkt):
    if args.disable:
        pc = dict(pc)
        for sig in args.disable.split(','):
            neutralize(pc, sig.strip())

    m2 = dict(macro)
    # Analyst signals from local SQLite cache (mirrors tune.py; fast_backtest
    # never supplied these, so Stage 3.5 was unreachable there).
    try:
        ats = get_analyst_signals(code, mkt)
        if ats is not None and len(ats) > 0:
            m2['analyst_signals'] = ats
    except Exception:
        pass
    if mkt == 'A':
        try: m2['chip_conc'] = get_chip_concentration(code)
        except Exception: pass
        try: m2['holder_chg'] = get_holder_chg(code)
        except Exception: pass

    weekly = {}   # week_end_date -> scaled return while in position
    errors = 0
    in_position = False
    neg_streak = 0
    # v12 fix: start index on the WEEKLY grid (legacy daily-index bug, fixed here too)
    start_i = max(1, (dfw.index >= '2021-06-01').argmax() if (dfw.index >= '2021-06-01').any() else 1)

    for i in range(start_i, len(dfw) - 1):
        wk = dfw.index[i]
        di = df.index.get_indexer([wk], method='ffill')[0]
        if di < 252:
            continue
        ret = (dfw['close'].iloc[i+1] / dfw['close'].iloc[i]) - 1
        macro_mult = 1.0
        try:
            r = score_bar_v5(di, df, pc, macro_data=m2, market=mkt)
            act = r['action']
            macro_mult = r.get('macro_mult', 1.0)
            # v11: with --hold-exit, scoring.py itself applies the hold-model
            # exit logic (GUSHEN_HOLD_EXIT=1), so action semantics are uniform.
            if act == 'BUY':
                in_position = True
            elif act == 'EXIT':
                in_position = False
        except Exception:
            errors += 1
        if in_position:
            weekly[dfw.index[i+1]] = ret * macro_mult
    return weekly, errors

def sharpe(arr):
    a = np.asarray(arr, dtype=float)
    if len(a) < 3 or a.std() == 0:
        return 0.0
    return round(float(np.sqrt(52) * a.mean() / a.std()), 3)

# ── Run ──────────────────────────────────────────────────────────
data = load_all()
R, port = {}, {}
total_err = 0
for code, name, mkt in STOCKS:
    if code not in data:
        R[code] = {'s': 0, 'n': 0}
        continue
    df, dfw, pc, _ = data[code]
    weekly, errors = run_stock(code, df, dfw, pc, mkt)
    total_err += errors
    rets = list(weekly.values())
    R[code] = {'s': sharpe(rets), 'n': len(rets),
               's_is': sharpe([v for d, v in weekly.items() if d < SPLIT_DATE]),
               's_oos': sharpe([v for d, v in weekly.items() if d >= SPLIT_DATE]),
               'err': errors}
    for d, v in weekly.items():
        port.setdefault(d, []).append(v)
    if not args.quiet:
        e = f' ERR={errors}' if errors else ''
        print(f'  {code:10s} S={R[code]["s"]:+.3f} (IS {R[code]["s_is"]:+.3f} / OOS {R[code]["s_oos"]:+.3f}) B={len(rets)}{e}')

# Legacy per-market averages
by_mkt = {}
for code, name, mkt in STOCKS:
    by_mkt.setdefault(mkt, []).append(R[code]['s'])

# Portfolio equity curve: equal weight across active positions; cash weeks = 0
all_weeks = pd.date_range(start='2021-06-04', end='2026-05-08', freq='W-FRI')
port_ret = pd.Series([np.mean(port[d]) if d in port else 0.0 for d in all_weeks], index=all_weeks)
def port_stats(s):
    if len(s) < 3 or s.std() == 0: return 0.0, 0.0
    eq = (1 + s).cumprod()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    return round(float(np.sqrt(52) * s.mean() / s.std()), 3), round(dd, 3)
p_all, dd_all = port_stats(port_ret)
p_is, _ = port_stats(port_ret[port_ret.index < SPLIT_DATE])
p_oos, _ = port_stats(port_ret[port_ret.index >= SPLIT_DATE])
exposure = round(float(np.mean([1 if d in port else 0 for d in all_weeks])), 2)

legacy_all = round(float(np.mean([v['s'] for v in R.values()])), 3)
label = args.label or ('+'.join(filter(None, [
    'fund' if args.fund else '', 'hyst' if args.hyst else '',
    'holdexit' if args.hold_exit else '', f'no-{args.disable}' if args.disable else ''])) or 'baseline')

print(f'\n  ═══ {label} ═══')
for mkt in ['A', 'HK', 'US']:
    print(f'  {mkt}: legacy avg S={np.mean(by_mkt[mkt]):+.3f}')
print(f'  LEGACY avg-of-stocks S = {legacy_all:+.3f} | total in-position weeks: {sum(v["n"] for v in R.values())}')
print(f'  PORTFOLIO S = {p_all:+.3f} (IS {p_is:+.3f} / OOS {p_oos:+.3f}) | maxDD {dd_all:.1%} | exposure {exposure:.0%}')
if total_err:
    print(f'  ⚠ scoring errors: {total_err}')

row = {'label': label, 'legacy': legacy_all, 'port': p_all, 'port_is': p_is,
       'port_oos': p_oos, 'maxdd': dd_all,
       'mkts': {m: round(float(np.mean(v)), 3) for m, v in by_mkt.items()},
       'weeks': sum(v['n'] for v in R.values()), 'errors': total_err,
       'stocks': R}
out = GUSHEN / 'data' / 'bt2_results.jsonl'
with open(out, 'a') as f:
    f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
