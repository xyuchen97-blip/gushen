#!/usr/bin/env python3
"""
Strategy Compatibility Validation — v10 (updated May 20, 2026)
=========================================
Proves the strategy works as a risk-managed downside-protection overlay
across a diverse universe, not a stock-picking lottery.

Metrics:
  1. Max Drawdown vs Buy-and-Hold (should be <50% of B&H)
  2. Upside Capture Ratio (should capture >30% of market upside)
  3. Positive Alpha % (>60% of stocks should have positive alpha)
  4. Per-Style Sharpe Breakdown
  5. Signal Consistency (buy rate std across universe)

Run: GUSHEN_TUNE=1 python3 strategy/validate_universe.py
"""

import sys, os, warnings, numpy as np, pandas as pd, json, time
os.environ['GUSHEN_TUNE'] = '1'
warnings.filterwarnings('ignore')
sys.modules.pop('strategy.scoring', None)

from strategy.scoring import precompute, score_bar_v5
from strategy.gushen_cache import get_ohlcv, get_chip_concentration, get_holder_chg
from strategy.data_fetcher import fetch_macro_data
from guts.scoring.normalize import ScoreHistory
from guts.macro.sensitivity import STOCK_STYLES, StockStyle, get_sensitivity
from datetime import datetime
from pathlib import Path

GUSHEN = Path('/Users/alafat/.workbuddy/skills/gushen')

# ═══ Expanded Universe (30 stocks across style × market) ═══

UNIVERSE = [
    # A — Growth
    ('300750.SZ','宁德时代','A','growth'), ('002230.SZ','科大讯飞','A','growth'),
    ('300015.SZ','爱尔眼科','A','growth'),
    # A — Value
    ('601318.SH','中国平安','A','value'), ('600036.SH','招商银行','A','value'),
    ('601398.SH','工商银行','A','value'),
    # A — Defensive
    ('600519.SH','贵州茅台','A','defensive'), ('000858.SZ','五粮液','A','defensive'),
    ('600276.SH','恒瑞医药','A','defensive'),
    # A — Cyclical
    ('002594.SZ','比亚迪A','A','cyclical'), ('601899.SH','紫金矿业','A','cyclical'),
    
    # HK — Growth
    ('0700.HK','腾讯','HK','growth'), ('9988.HK','阿里','HK','growth'),
    ('3690.HK','美团','HK','growth'),
    # HK — Value
    ('0388.HK','港交所','HK','value'), ('0005.HK','汇丰','HK','value'),
    # HK — Cyclical
    ('1810.HK','小米','HK','cyclical'), ('1211.HK','比亚迪H','HK','cyclical'),
    
    # US — Growth
    ('AAPL','苹果','US','blend'), ('NVDA','英伟达','US','growth'),
    ('MSFT','微软','US','growth'), ('GOOGL','谷歌','US','growth'),
    ('AMZN','亚马逊','US','growth'), ('META','Meta','US','growth'),
    # US — Value
    ('JPM','摩根大通','US','value'), ('BRK-B','伯克希尔','US','value'),
    # US — Cyclical
    ('XOM','埃克森美孚','US','cyclical'),
    # US — Defensive
    ('JNJ','强生','US','defensive'), ('PG','宝洁','US','defensive'),
]


def compute_drawdown(returns):
    """Max drawdown from peak."""
    cum = (1 + pd.Series(returns)).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    return float(dd.min())


def compute_upside_capture(strategy_returns, benchmark_returns):
    """% of benchmark upside captured by strategy."""
    up_mask = benchmark_returns > 0
    if up_mask.sum() == 0:
        return 0
    strat_up = strategy_returns[up_mask].mean() * 52
    bench_up = benchmark_returns[up_mask].mean() * 52
    return round(float(strat_up / bench_up) * 100, 1) if bench_up > 0 else 0


def buy_and_hold_returns(prices):
    """Weekly buy-and-hold returns."""
    return prices.pct_change().dropna().values


def main():
    t0 = time.time()
    macro = fetch_macro_data('2021-01-01', '2026-05-06')
    sh = ScoreHistory(window=52, min_history=12)
    
    results = []
    
    for code, name, mkt, style in UNIVERSE:
        print(f'{code} ({name})...', end=' ', flush=True)
        st = time.time()
        try:
            df = get_ohlcv(code, mkt)
            if df is None or len(df) < 100:
                print('NODATA'); continue
            df = df.sort_index()
            dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
            
            m2 = dict(macro)
            if mkt == 'A':
                try: m2['chip_conc'] = get_chip_concentration(code)
                except: pass
                try: m2['holder_chg'] = get_holder_chg(code)
                except: pass
            
            # Strategy signals
            strat_returns = []
            for i in range(50, len(dfw)-1):
                wk = dfw.index[i]; di = df.index.get_indexer([wk], method='ffill')[0]
                if di < 252: continue
                try:
                    pc = precompute(df.iloc[:di+1], dfw.iloc[:i+1])
                    r = score_bar_v5(di, df.iloc[:di+1], pc, macro_data=m2, market=mkt,
                                  ticker=code, score_history=sh)
                    if r['action'] == 'BUY':
                        strat_returns.append((dfw['close'].iloc[i+1]/dfw['close'].iloc[i])-1)
                except: pass
            
            strat_ret = np.array(strat_returns) if strat_returns else np.zeros(1)
            bnh_ret = buy_and_hold_returns(dfw['close'])
            
            sharpe = float(np.sqrt(52)*strat_ret.mean()/strat_ret.std()) if len(strat_ret)>=3 and strat_ret.std()>0 else 0
            bnh_sharpe = float(np.sqrt(52)*bnh_ret.mean()/bnh_ret.std()) if bnh_ret.std()>0 else 0
            alpha = sharpe - bnh_sharpe  # excess over buy-and-hold
            
            max_dd = compute_drawdown(strat_ret)
            bnh_dd = compute_drawdown(bnh_ret)
            dd_ratio = round(max_dd / bnh_dd * 100, 1) if bnh_dd < 0 else 100
            
            upside = compute_upside_capture(strat_ret, bnh_ret)
            
            buy_rate = len(strat_ret) / (len(dfw) - 252) * 100 if len(dfw) > 252 else 0
            
            results.append({
                'ticker': code, 'name': name, 'market': mkt, 'style': style,
                'sharpe': round(sharpe, 3), 'bnh_sharpe': round(bnh_sharpe, 3),
                'alpha': round(alpha, 3), 'buy_count': len(strat_ret),
                'max_dd_pct': round(max_dd*100, 1),
                'bnh_dd_pct': round(bnh_dd*100, 1),
                'dd_ratio_pct': dd_ratio,
                'upside_capture_pct': upside,
                'buy_rate_pct': round(buy_rate, 1),
            })
            
            marker = '✓' if alpha > 0 else '✗'
            print(f'S={sharpe:.2f} α={alpha:+.2f} DD={abs(max_dd)*100:.0f}% UC={upside}% {marker} ({time.time()-st:.0f}s)')
            
            sh.reset_ticker(code)
        except Exception as e:
            print(f'ERR: {e}')
    
    # ═══ Aggregate Metrics ═══
    if not results:
        print('No results'); return
    
    n = len(results)
    pos_alpha = sum(1 for r in results if r['alpha'] > 0)
    avg_sharpe = np.mean([r['sharpe'] for r in results])
    avg_alpha = np.mean([r['alpha'] for r in results])
    avg_dd_ratio = np.mean([r['dd_ratio_pct'] for r in results])
    avg_upside = np.mean([r['upside_capture_pct'] for r in results])
    buy_rates = [r['buy_rate_pct'] for r in results]
    buy_std = np.std(buy_rates)
    
    # By style
    by_style = {}
    for r in results:
        s = r['style']
        by_style.setdefault(s, []).append(r['alpha'])
    
    print(f'\n{"="*70}')
    print(f'  Universe Compatibility Report — v9.6')
    print(f'{"="*70}')
    print(f'  Universe: {n} stocks ({sum(1 for r in results if r["market"]=="A")}A + '
          f'{sum(1 for r in results if r["market"]=="HK")}HK + '
          f'{sum(1 for r in results if r["market"]=="US")}US)')
    print(f'')
    
    print(f'  Positive Alpha:    {pos_alpha}/{n} = {pos_alpha/n*100:.0f}%  [PASS: {"✓" if pos_alpha/n>=0.6 else "✗"}]')
    print(f'  Avg Sharpe:        {avg_sharpe:.3f}')
    print(f'  Avg Alpha:         {avg_alpha:+.3f}')
    print(f'  Avg DD Ratio:      {avg_dd_ratio:.0f}% of B&H  [PASS: {"✓" if avg_dd_ratio<50 else "✗"}]')
    print(f'  Avg Upside Capture: {avg_upside:.0f}%  [PASS: {"✓" if avg_upside>30 else "✗"}]')
    print(f'  Signal Consistency: σ={buy_std:.1f}% buy rate  [PASS: {"✓" if buy_std<15 else "✗"}]')
    print(f'')
    
    print(f'  Per-Style Alpha:')
    for style in ['growth','value','cyclical','defensive','blend']:
        vals = by_style.get(style, [])
        if vals:
            print(f'    {style:<12}: {np.mean(vals):+.3f}  (n={len(vals)}, {sum(1 for v in vals if v>0)}/{len(vals)}>0)')
    
    print(f'')
    total_pass = int(pos_alpha/n>=0.6) + int(avg_dd_ratio<50) + int(avg_upside>30) + int(buy_std<15)
    print(f'  Overall: {total_pass}/4 criteria passed')
    
    if total_pass >= 3:
        print(f'  VERDICT: Strategy is universe-compatible ✓')
    elif total_pass >= 2:
        print(f'  VERDICT: Strategy partially compatible — needs work on failing criteria')
    else:
        print(f'  VERDICT: Strategy is NOT universe-compatible — overfit to narrow set')
    
    # Save
    snap = {
        'date': str(datetime.now())[:10],
        'version': 'v9.6-compat',
        'n_stocks': n,
        'positive_alpha_pct': round(pos_alpha/n*100, 1),
        'avg_sharpe': round(avg_sharpe, 3),
        'avg_alpha': round(avg_alpha, 3),
        'avg_dd_ratio_pct': round(avg_dd_ratio, 1),
        'avg_upside_capture_pct': round(avg_upside, 1),
        'buy_rate_std': round(buy_std, 1),
        'by_style': {s: {'mean': round(np.mean(v), 3), 'n': len(v),
                          'positive': sum(1 for x in v if x>0)} for s, v in by_style.items()},
        'results': results,
        'runtime_s': round(time.time()-t0),
    }
    snap_path = GUSHEN / f"data/compat_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    with open(snap_path, 'w') as f:
        json.dump(snap, f, indent=2, default=str)
    print(f'\n  Saved: {snap_path}')
    print(f'  Runtime: {(time.time()-t0)/60:.1f} min')


if __name__ == '__main__':
    main()
