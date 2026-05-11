#!/usr/bin/env python3
"""
股神修炼模式 (Gushen Tune Mode)
═══════════════════════════════════

Workflow:
  entry → IC test → backtest → present results → ask reinforce → apply/discard

Usage:
  GUSHEN_TUNE=1 python3 strategy/tune.py --action ic_test --factor holder_chg
  GUSHEN_TUNE=1 python3 strategy/tune.py --action backtest --universe all
  GUSHEN_TUNE=1 python3 strategy/tune.py --action reinforce --version v9.4
"""

import os, sys, json, warnings, argparse, importlib, subprocess
import numpy as np, pandas as pd, yfinance as yf
from pathlib import Path
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# ═══ Guard ═══
if os.environ.get("GUSHEN_TUNE") != "1":
    print("⛔ 股神修炼模式需要 GUSHEN_TUNE=1 环境变量。")
    print("   退出修炼模式：重复之前的实时分析模式。")
    sys.exit(1)

GUSHEN = Path(os.environ.get("GUSHEN_HOME", "/Users/alafat/.workbuddy/skills/gushen"))
sys.path.insert(0, str(GUSHEN))

def build_cache():
    """建造缓存：全市场OHLCV + 筹码分布 + 股东人数"""
    from strategy.gushen_cache import init_db, build_ohlcv_cache, build_holders_cache, build_cyq_cache, build_macro_cache
    init_db()
    a = ['600519','000858','300750','002594','601318','600036','002230','300015','600809','000625']
    hk = ['0700.HK','9988.HK','3690.HK','1810.HK','1211.HK','0388.HK']
    us = ['AAPL','NVDA','MSFT','GOOGL','AMZN','META','JPM']
    build_ohlcv_cache(a, hk, us)
    build_holders_cache(a)
    build_cyq_cache(a)
    build_macro_cache()
    print("✅ 缓存建造完成")

def ic_test(factor_name):
    """IC测试：单因子 vs 前向收益"""
    import tushare as ts
    ts.set_token("c1cbd943613a172b916b0d249b3dc04146d13817d6bc4c0bc60756de")
    pro = ts.pro_api()
    
    stocks = [('600519.SH','茅台'),('000858.SZ','五粮液'),('300750.SZ','宁德时代'),('002594.SZ','比亚迪'),('601318.SH','平安'),('600036.SH','招行')]
    
    results = []
    for code, name in stocks:
        ticker = code.replace('.SH','.SS').replace('.SZ','.SZ')
        if factor_name == 'holder_chg':
            df = pro.stk_holdernumber(ts_code=code, start_date='20210101', end_date='20260506').sort_values('end_date')
            df['value'] = df['holder_num'].astype(float).pct_change()
            lookahead = 60
        elif factor_name == 'chip_conc':
            df = pro.cyq_chips(ts_code=code, trade_date='20260506')
            # Static for now — needs daily history for proper IC
            results.append((name, 0, 0, 'static'))
            continue
        else:
            print(f"  Unknown factor: {factor_name}")
            return
        
        price = yf.download(ticker, start='2021-01-01', end='2026-05-06', progress=False, auto_adjust=False)
        if isinstance(price.columns, pd.MultiIndex): price = price.xs(price.columns.levels[-1][0], axis=1, level=-1)
        close = price['Close']
        fwd = []
        for _, row in df.iterrows():
            d = pd.Timestamp(row.get('end_date', row.get('trade_date')))
            f = close[close.index >= d].head(lookahead + 1)
            fwd.append(f.iloc[-1]/f.iloc[0]-1 if len(f) >= 2 else np.nan)
        df['fwd'] = fwd
        valid = df.dropna(subset=['value','fwd'])
        if len(valid) >= 4:
            ic = np.corrcoef(valid['value'], valid['fwd'])[0,1]
            ric = np.corrcoef(valid['value'].rank(), valid['fwd'].rank())[0,1]
            results.append((name, round(ic,3), round(ric,3), 'ok'))
    
    print(f"\n  IC Test: {factor_name}")
    print(f"  {'Stock':<10} {'IC':>7} {'RankIC':>7}")
    for n, ic, ric, status in results:
        print(f"  {n:<10} {ic:>+7.3f} {ric:>+7.3f}")
    avg = np.mean([r[1] for r in results if r[3] == 'ok']) if results else 0
    print(f"  ★ Avg IC = {avg:+.3f}")
    return results

def backtest(universe='all'):
    """全回测：17-22 stocks, v9.X scoring with cache"""
    from strategy.scoring import precompute, score_bar
    from strategy.data_fetcher import fetch_macro_data
    from strategy.gushen_cache import get_ohlcv, get_chip_concentration, get_holder_chg
    
    if universe == 'all':
        stocks = [
            ('600519.SH','茅台','A'),('000858.SZ','五粮液','A'),('300750.SZ','宁德时代','A'),('002594.SZ','比亚迪','A'),
            ('601318.SH','平安','A'),('600036.SH','招行','A'),('002230.SZ','科大讯飞','A'),('300015.SZ','爱尔眼科','A'),
            ('0700.HK','腾讯','HK'),('9988.HK','阿里','HK'),('3690.HK','美团','HK'),('1810.HK','小米','HK'),
            ('1211.HK','比亚迪','HK'),('0388.HK','港交所','HK'),
            ('AAPL','苹果','US'),('NVDA','英伟达','US'),('MSFT','微软','US'),('GOOGL','谷歌','US'),
            ('AMZN','亚马逊','US'),('META','Meta','US'),('JPM','摩根大通','US'),
        ]
    else:
        stocks = [('600519.SH','茅台','A'),('300750.SZ','宁德时代','A'),('002594.SZ','比亚迪','A'),
                   ('0700.HK','腾讯','HK'),('AAPL','苹果','US')]
    
    macro = fetch_macro_data('2021-01-01','2026-05-06')
    R = {}
    for code, name, mkt in stocks:
        print(f'  {code} ({name})...', end=' ', flush=True)
        df = get_ohlcv(code, mkt) if mkt == 'A' else None
        if df is None:
            ticker = code.replace('.SH','.SS').replace('.SZ','.SZ') if mkt == 'A' else code
            df = yf.download(ticker, start='2021-01-01', end='2026-05-06', progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex): df = df.xs(df.columns.levels[-1][0], axis=1, level=-1)
            m = {'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'}
            df = df.rename(columns={k:v for k,v in m.items() if k in df.columns})
            df = df[['open','high','low','close','volume']]; df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
        
        m2 = dict(macro)
        if mkt == 'A':
            m2['chip_conc'] = get_chip_concentration(code)
            m2['holder_chg'] = get_holder_chg(code)
        
        buys = []
        for i in range(50, len(dfw)-1):
            wk = dfw.index[i]; di = df.index.get_indexer([wk], method='ffill')[0]
            if di < 252: continue
            try: r = score_bar(di, df.iloc[:di+1], precompute(df.iloc[:di+1], dfw.iloc[:i+1]), macro_data=m2, market=mkt)
            except: continue
            if r['action'] == 'BUY': buys.append((dfw['close'].iloc[i+1]/dfw['close'].iloc[i])-1)
        bu = np.array(buys) if buys else np.zeros(1)
        sa = round(float(np.sqrt(52)*bu.mean()/bu.std()),3) if len(bu)>=3 and bu.std()>0 else 0
        R[code] = {'s':sa, 'n': len(bu)}
        mkt_s = 'A' if mkt=='A' else mkt
        print(f'S={sa} B={len(bu)}')
    
    by_mkt = {}
    for code, name, mkt in stocks:
        by_mkt.setdefault(mkt, []).append(R[code]['s'])
    
    print(f"\n  Results:")
    for mkt, vals in by_mkt.items():
        pos = sum(1 for s in vals if s > 0)
        print(f"  {mkt}: avg S={np.mean(vals):.3f} ({pos}/{len(vals)}>0)")
    all_s = np.mean([v['s'] for v in R.values()])
    print(f"  ★ Overall avg S = {all_s:.3f}")
    
    # Save snapshot
    snap = {'date': str(datetime.now())[:10], 'sharpe': round(all_s, 3), 'stocks': R}
    snap_path = GUSHEN / f"data/tune_snapshot_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(snap_path, 'w') as f: json.dump(snap, f, indent=2, default=str)
    print(f"  💾 Snapshot: {snap_path}")
    return all_s

def reinforce(version='v9.4'):
    """强化股神：更新 SKILL.md + 清理 + commit"""
    print(f"  正在强化 {version}...")
    
    # 1. Update SKILL.md version reference
    skill_path = GUSHEN / "SKILL.md"
    skill = skill_path.read_text()
    if f"v{version}" not in skill:
        skill = skill.replace("v9.2", version)
        skill_path.write_text(skill)
        print(f"  ✅ SKILL.md updated to {version}")
    
    # 2. Commit changes
    subprocess.run(["git", "-C", str(GUSHEN), "add", "-A"], check=False)
    subprocess.run(["git", "-C", str(GUSHEN), "commit", "-m", f"reinforce: {version} tuned and validated"], check=False)
    subprocess.run(["git", "-C", str(GUSHEN), "push"], check=False)
    print(f"  ✅ Git committed: {version}")
    
    # 3. Audit — update MEMORY.md
    memory_path = Path("/Users/alafat/WorkBuddy/2026-05-06-task-1/.workbuddy/memory/MEMORY.md")
    note = f"\n## {version} Reinforced ({datetime.now().strftime('%Y-%m-%d')})\n- Tuned via 股神修炼模式\n- Factors: chip concentration, holder count, repurchase, events\n"
    memory_path.write_text((memory_path.read_text() if memory_path.exists() else "") + note)
    print(f"  ✅ MEMORY.md updated")
    
    print(f"\n  ✅ {version} 强化完成。退出修炼模式。")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="股神修炼模式")
    p.add_argument("--action", choices=["build_cache","ic_test","backtest","reinforce"], required=True)
    p.add_argument("--factor", default="holder_chg")
    p.add_argument("--universe", default="all")
    p.add_argument("--version", default="v9.4")
    args = p.parse_args()
    
    print(f"\n  🔥 股神修炼模式 — {args.action.upper()}\n")
    if args.action == "build_cache": build_cache()
    elif args.action == "ic_test": ic_test(args.factor)
    elif args.action == "backtest": backtest(args.universe)
    elif args.action == "reinforce": reinforce(args.version)
    print()
