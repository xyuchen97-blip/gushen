#!/usr/bin/env python3
"""Optimized full backtest — runs precompute once per stock, scores all bars.
   v10.2 (May 2026): Regime-adaptive scoring with analyst signals.
"""
import sys, os, warnings, json, numpy as np, pandas as pd
from pathlib import Path
from datetime import datetime
warnings.filterwarnings('ignore')

os.environ['GUSHEN_TUNE'] = '1'
# Use this script's own directory for imports + DB, fall back to workbuddy skills path
GUSHEN = Path(os.environ.get("GUSHEN_HOME", str(Path(__file__).resolve().parent.parent)))
sys.path.insert(0, str(GUSHEN))
# Ensure DB path resolves to gushen_handoff/data/ regardless of GUSHEN_HOME
os.environ.setdefault('GUSHEN_DB_PATH', str(GUSHEN / 'data' / 'gushen.db'))

from strategy.scoring import precompute, score_bar_v5
from strategy.data_fetcher import fetch_macro_data
from strategy.gushen_cache import get_ohlcv, get_chip_concentration, get_holder_chg

macro = fetch_macro_data('2021-01-01', '2026-05-06')

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

R = {}
for code, name, mkt in STOCKS:
    print(f'  {code} ({name})...', end=' ', flush=True)
    try:
        df = get_ohlcv(code, mkt)
        if df is None or len(df) < 100:
            print('NO DATA')
            R[code] = {'s': 0, 'n': 0}
            continue

        df = df.sort_index()
        dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()

        m2 = dict(macro)
        if mkt == 'A':
            try: m2['chip_conc'] = get_chip_concentration(code)
            except: pass
            try: m2['holder_chg'] = get_holder_chg(code)
            except: pass

        # Precompute ONCE
        pc = precompute(df, dfw)

        buys = []
        in_position = False
        start_i = max(50, (df.index >= '2021-06-01').argmax() if (df.index >= '2021-06-01').any() else 50)

        for i in range(start_i, len(dfw) - 1):
            wk = dfw.index[i]
            di = df.index.get_indexer([wk], method='ffill')[0]
            if di < 252: continue
            ret = (dfw['close'].iloc[i+1] / dfw['close'].iloc[i]) - 1

            try:
                r = score_bar_v5(di, df, pc, macro_data=m2, market=mkt)
                act = r['action']
                macro_mult = r.get('macro_mult', 1.0)
                if act == 'BUY':
                    in_position = True
                elif act == 'EXIT':
                    in_position = False
            except:
                macro_mult = 1.0
            if in_position:
                buys.append(ret * macro_mult)

        bu = np.array(buys) if buys else np.zeros(1)
        sa = round(float(np.sqrt(52)*bu.mean()/bu.std()), 3) if len(bu) >= 3 and bu.std() > 0 else 0
        R[code] = {'s': sa, 'n': len(bu)}
        print(f'S={sa} B={len(bu)}')
    except Exception as e:
        print(f'ERROR: {e}')
        R[code] = {'s': 0, 'n': 0}

# Summary
by_mkt = {}
for code, name, mkt in STOCKS:
    by_mkt.setdefault(mkt, []).append(R[code]['s'])

print(f'\n  === Results (v10.2 regime-adaptive + analyst signals) ===')
for mkt in ['A', 'HK', 'US']:
    vals = by_mkt.get(mkt, [])
    pos = sum(1 for s in vals if s > 0)
    avg = np.mean(vals) if vals else 0
    print(f'  {mkt}: avg S={avg:.3f} ({pos}/{len(vals)}>0)')

all_s = np.mean([v['s'] for v in R.values()])
total_b = sum(v['n'] for v in R.values())
print(f'  ★ Overall avg S = {all_s:.3f} | Total BUY signals: {total_b}')
print(f'  v10.2 baseline (Jun 2026 macro): ALL S=1.324, A=-0.056, HK=1.570, US=2.689')
print(f'  Δ = {all_s - 1.324:+.3f}')

# Save
snap = {
    'date': str(datetime.now())[:10],
    'version': 'v10.2',
    'sharpe': round(all_s, 3),
    'by_market': {m: round(np.mean(v), 3) for m, v in by_mkt.items()},
    'total_buy_signals': total_b,
    'stocks': {k: v for k, v in R.items()},
}
snap_path = GUSHEN / f"data/tune_snapshot_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
snap_path.parent.mkdir(parents=True, exist_ok=True)
with open(snap_path, 'w') as f:
    json.dump(snap, f, indent=2, default=str)
print(f'\n  Snapshot: {snap_path}')
