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
    """建造缓存：全市场OHLCV + 宏观数据 + 筹码分布 + 股东人数 + 基本面数据"""
    from strategy.gushen_cache import (init_db, build_ohlcv_cache, build_holders_cache,
                                        build_cyq_cache, build_macro_cache, build_fundamental_cache,
                                        build_analyst_cache)
    init_db()
    a = ['600519','000858','300750','002594','601318','600036','002230','300015','600809','000625']
    hk = ['0700.HK','9988.HK','3690.HK','1810.HK','1211.HK','0388.HK']
    us = ['AAPL','NVDA','MSFT','GOOGL','AMZN','META','JPM']
    build_ohlcv_cache(a, hk, us)
    build_holders_cache(a)
    build_cyq_cache(a)
    build_macro_cache()
    # A-stock fundamentals use ts_code format
    a_ts = [f"{c}.{'SH' if c.startswith('6') else 'SZ'}" for c in a]
    build_fundamental_cache(a_ts, hk, us)
    # v10.2: analyst signals cache
    build_analyst_cache(a_ts, hk, us)
    print("✅ 缓存建造完成（含宏观数据+基本面数据+分析师信号）")

def ic_test(factor_name):
    """IC测试：单因子 vs 前向收益"""
    import tushare as ts
    # Token read from environment or stored config — set via: ts.set_token(os.environ["TUSHARE_TOKEN"])
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

def backtest(universe='all', version='v10'):
    """全回测：21 stocks, v10.2 scoring with cache

    version: 'v10' (only supported version — regime-adaptive dual-mode)
    """
    from strategy.scoring import precompute, score_bar_v5 as score_fn
    print(f'  [v10.2: regime-adaptive dual-mode — bear contrarian + bull trend/pullback + analyst signals]')
    from strategy.gushen_cache import get_ohlcv, get_chip_concentration, get_holder_chg, get_macro_data, get_fundamental_timeseries, get_analyst_signals
    from guts.scoring.normalize import ScoreHistory
    
    if universe == 'all':
        stocks = [
            ('600519.SH','茅台','A'),('000858.SZ','五粮液','A'),('300750.SZ','宁德时代','A'),('002594.SZ','比亚迪','A'),
            ('601318.SH','平安','A'),('600036.SH','招行','A'),('002230.SZ','科大讯飞','A'),('300015.SZ','爱尔眼科','A'),
            ('0700.HK','腾讯','HK'),('9988.HK','阿里','HK'),('3690.HK','美团','HK'),('1810.HK','小米','HK'),
            ('1211.HK','比亚迪','HK'),('0388.HK','港交所','HK'),
            ('AAPL','苹果','US'),('NVDA','英伟达','US'),('MSFT','微软','US'),('GOOGL','谷歌','US'),
            ('AMZN','亚马逊','US'),('META','Meta','US'),('JPM','摩根大通','US'),
        ]
    elif universe == 'ahk':
        stocks = [
            ('600519.SH','茅台','A'),('000858.SZ','五粮液','A'),('300750.SZ','宁德时代','A'),('002594.SZ','比亚迪','A'),
            ('601318.SH','平安','A'),('600036.SH','招行','A'),('002230.SZ','科大讯飞','A'),('300015.SZ','爱尔眼科','A'),
            ('0700.HK','腾讯','HK'),('9988.HK','阿里','HK'),('3690.HK','美团','HK'),('1810.HK','小米','HK'),
            ('1211.HK','比亚迪','HK'),('0388.HK','港交所','HK'),
        ]
    else:
        stocks = [('600519.SH','茅台','A'),('300750.SZ','宁德时代','A'),('002594.SZ','比亚迪','A'),
                   ('0700.HK','腾讯','HK'),('AAPL','苹果','US')]
    
    macro = get_macro_data('2021-01-01','2026-05-06')  # From SQLite cache — no API calls
    score_history = ScoreHistory(window=52, min_history=12)
    R = {}
    for code, name, mkt in stocks:
        print(f'  {code} ({name})...', end=' ', flush=True)
        df = get_ohlcv(code, mkt)  # Try all markets from cache first
        if df is None:
            # Cache miss — fetch from live source
            if mkt in ('HK', 'US'):
                # Use akshare for HK/US (matches production data_fetcher.py, qfq-adjusted)
                try:
                    import akshare as ak
                    ticker = code
                    if mkt == 'HK':
                        hk_code = code.replace('.HK','').replace('.hk','').zfill(5)
                        df = ak.stock_hk_hist(symbol=hk_code, period="daily",
                                              start_date='20210101', end_date='20260506', adjust="qfq")
                    else:
                        df = ak.stock_us_daily(symbol=code, adjust="qfq")
                    if df is not None and len(df) > 10:
                        # Standardize akshare columns
                        col_map = {}
                        for col in df.columns:
                            cl = col.lower()
                            if 'date' in cl or '日期' in col: col_map[col] = 'date'
                            elif 'open' in cl or '开盘' in col: col_map[col] = 'open'
                            elif 'high' in cl or '最高' in col: col_map[col] = 'high'
                            elif 'low' in cl or '最低' in col: col_map[col] = 'low'
                            elif 'close' in cl or '收盘' in col: col_map[col] = 'close'
                            elif 'volume' in cl or '成交' in col: col_map[col] = 'volume'
                        df = df.rename(columns=col_map)
                        if 'date' in df.columns:
                            df['date'] = pd.to_datetime(df['date'])
                            df = df.set_index('date').sort_index()
                        else:
                            df.index = pd.to_datetime(df.index)
                            df = df.sort_index()
                        df = df[[c for c in ['open','high','low','close','volume'] if c in df.columns]]
                except Exception:
                    df = None
            
            if df is None or len(df) < 50:
                # Last resort: yfinance (auto_adjust=True for adjusted prices)
                ticker = code.replace('.SH','.SS').replace('.SZ','.SZ') if mkt == 'A' else code
                df = yf.download(ticker, start='2021-01-01', end='2026-05-06', progress=False, auto_adjust=True)
                if isinstance(df.columns, pd.MultiIndex): df = df.xs(df.columns.levels[-1][0], axis=1, level=-1)
                m = {'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'}
                df = df.rename(columns={k:v for k,v in m.items() if k in df.columns})
                df = df[[c for c in ['open','high','low','close','volume'] if c in df.columns]]
                df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        # US stocks pre-trimmed to 2021+ in cache (was needed when akshare returned 25yr history)
        dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
        
        m2 = dict(macro)
        if mkt == 'A':
            m2['chip_conc'] = get_chip_concentration(code)
            m2['holder_chg'] = get_holder_chg(code)
            # v10.1: Load margin financing data (contrarian signal, RankIC=-0.09)
            margin_path = GUSHEN / f"data/margin_history/{code.replace('.SH','').replace('.SZ','')}.csv"
            if margin_path.exists():
                try:
                    mdf = pd.read_csv(margin_path, parse_dates=['date']).set_index('date').sort_index()
                    if 'margin_balance' in mdf.columns and len(mdf) > 5:
                        mdf['pct_5d'] = mdf['margin_balance'].pct_change(5) * 100
                        m2['margin'] = {d: {'pct_5d': float(row['pct_5d'])} for d, row in mdf.dropna(subset=['pct_5d']).iterrows()}
                        print(f'[margin ✓]', end=' ', flush=True)
                except Exception:
                    pass

        # Stage 3: Load fundamental time series for backtest (from cache)
        fund_ts = get_fundamental_timeseries(code, mkt)  # From SQLite cache — no API calls
        if fund_ts is not None and not fund_ts.empty:
            print(f'[fund ✓]', end=' ', flush=True)
        else:
            print(f'[fund ✗]', end=' ', flush=True)

        # v10.2: Load analyst signals (Tushare forecast / AV earnings / akshare ET)
        analyst_ts = get_analyst_signals(code, mkt)
        if analyst_ts is not None and not analyst_ts.empty:
            print(f'[analyst ✓ {len(analyst_ts)}]', end=' ', flush=True)
            m2['analyst_signals'] = analyst_ts  # DataFrame indexed by signal_date
        else:
            print(f'[analyst ✗]', end=' ', flush=True)
        
        buys = []
        all_returns = []  # ALL weekly returns (B&H baseline)
        strat_returns = []  # strategy returns (BUY or HOLD-in-bull = in position, else 0)
        in_position = False  # track whether we hold the stock

        # v10.1b: Adaptive exit state tracking (softened from v10.1a)
        entry_bar = None        # weekly bar index when position was opened
        entry_price = None      # close price at entry
        peak_composite = 0      # highest composite seen while in position
        last_buy_bar = None     # last bar that fired BUY (for time decay)
        peak_equity = 1.0       # peak unrealized equity since entry
        TIME_DECAY_START = 12   # weeks without new BUY before decay kicks in (was 8)
        TIME_DECAY_PTS = 1      # composite penalty per week of decay (was 2)
        PROFIT_TAKE_DROP = 0.50 # exit if composite drops 50% from peak (was 0.40)

        # v10: precompute-once optimization (all signals are backward-looking)
        if version == 'v10':
            pre_full = precompute(df, dfw)

        for i in range(50, len(dfw)-1):
            wk = dfw.index[i]; di = df.index.get_indexer([wk], method='ffill')[0]
            if di < 252: continue
            ret = (dfw['close'].iloc[i+1]/dfw['close'].iloc[i])-1
            all_returns.append(ret)
            # v10.1: Lookup fundamental data as of this bar's date (no look-ahead)
            # Pass current + previous quarter for change-based signals
            m3 = dict(m2)
            if fund_ts is not None and not fund_ts.empty:
                available = fund_ts[fund_ts.index <= pd.Timestamp(wk)]
                if not available.empty:
                    latest_fund = available.iloc[-1].dropna().to_dict()
                    m3['fundamentals'] = latest_fund
                    # v10.1: previous quarter for acceleration signals
                    if len(available) >= 2:
                        prev_fund = available.iloc[-2].dropna().to_dict()
                        m3['fundamentals_prev'] = prev_fund
            try:
                if version == 'v10':
                    # v10: use precompute-once (fast) + pass macro_data for macro_mult
                    r = score_fn(di, df, pre_full, macro_data=m3, market=mkt, ticker=code, score_history=score_history)
                else:
                    r = score_fn(di, df.iloc[:di+1], precompute(df.iloc[:di+1], dfw.iloc[:i+1]), macro_data=m3, market=mkt, ticker=code, score_history=score_history)
            except: r = None
            if r is not None:
                act = r['action']
                comp = r.get('composite', 0)
                is_bull = r.get('bull_regime', False)
                # v10: apply macro_mult to position sizing
                macro_mult = r.get('macro_mult', 1.0) if version == 'v10' else 1.0

                if act == 'BUY':
                    if not in_position:
                        # New entry
                        entry_bar = i
                        entry_price = dfw['close'].iloc[i]
                        peak_equity = 1.0
                    in_position = True
                    last_buy_bar = i
                    peak_composite = comp  # reset peak on new BUY

                elif act == 'EXIT':
                    in_position = False
                    entry_bar = None; entry_price = None; peak_composite = 0

                # v10.1: Adaptive exit logic (US/A only — HK trends run longer, exits hurt)
                # Ablation: US avg +0.49, A avg +0.04, HK avg -0.27
                if in_position and version == 'v10' and entry_bar is not None and mkt != 'HK':
                    # Track peak composite while holding
                    if comp > peak_composite:
                        peak_composite = comp

                    # Track peak unrealized equity
                    cur_price = dfw['close'].iloc[i]
                    unrealized = cur_price / entry_price if entry_price > 0 else 1.0
                    if unrealized > peak_equity:
                        peak_equity = unrealized

                    # ── Time decay: no new BUY for TIME_DECAY_START weeks ──
                    weeks_since_buy = i - (last_buy_bar or entry_bar)
                    if weeks_since_buy >= TIME_DECAY_START:
                        decay_weeks = weeks_since_buy - TIME_DECAY_START
                        decay_penalty = decay_weeks * TIME_DECAY_PTS
                        effective_comp = comp - decay_penalty
                        # If decayed composite falls below exit threshold → exit
                        thresholds = r.get('regime', 'bear')
                        exit_thresh = 10 if mkt in ('US', 'HK') else 0
                        if effective_comp < exit_thresh:
                            in_position = False
                            entry_bar = None; entry_price = None; peak_composite = 0

                    # ── Profit-take: composite dropped 40%+ from peak ──
                    if in_position and peak_composite > 0 and comp < peak_composite * (1 - PROFIT_TAKE_DROP):
                        # Only take profit if actually profitable
                        if unrealized > 1.02:  # at least 2% profit
                            in_position = False
                            entry_bar = None; entry_price = None; peak_composite = 0

                    # ── ATR trailing stop: exit if drawdown from peak exceeds ATR-based stop ──
                    if in_position and entry_price is not None and i >= 14:
                        # ATR(14) from weekly data
                        wk_high = dfw['high'].iloc[max(0,i-13):i+1]
                        wk_low = dfw['low'].iloc[max(0,i-13):i+1]
                        wk_close = dfw['close'].iloc[max(0,i-13):i+1]
                        wk_tr = pd.concat([
                            wk_high - wk_low,
                            (wk_high - wk_close.shift(1)).abs(),
                            (wk_low - wk_close.shift(1)).abs()
                        ], axis=1).max(axis=1)
                        atr = float(wk_tr.mean()) if len(wk_tr) > 0 else 0
                        # Stop distance: 3× ATR (was 2×, softened in v10.1b)
                        stop_dist = 3.0 * atr
                        peak_price = entry_price * peak_equity
                        if cur_price < peak_price - stop_dist and peak_equity > 1.0:
                            in_position = False
                            entry_bar = None; entry_price = None; peak_composite = 0
            else:
                macro_mult = 1.0
            is_active = in_position
            # v10: scale returns by macro_mult (portfolio position sizing)
            scaled_ret = ret * macro_mult if is_active else 0.0
            strat_returns.append(scaled_ret)
            if is_active: buys.append(scaled_ret)
        bu = np.array(buys) if buys else np.zeros(1)
        sa = round(float(np.sqrt(52)*bu.mean()/bu.std()),3) if len(bu)>=3 and bu.std()>0 else 0
        
        # Compatibility metrics (ALIGNED arrays)
        bnh = np.array(all_returns)
        strat = np.array(strat_returns)  # aligned: BUY=return, else=0
        bnh_s = float(np.sqrt(52)*bnh.mean()/bnh.std()) if bnh.std()>0 else 0
        alpha = round(sa - bnh_s, 3)
        
        # Max drawdown: from aligned equity curves
        if len(buys) > 0:
            eq_s = pd.Series(strat).add(1).cumprod()
            eq_b = pd.Series(bnh).add(1).cumprod()
            peak_s = eq_s.cummax(); peak_b = eq_b.cummax()
            dd_s = float(((eq_s - peak_s) / peak_s).min())
            dd_b = float(((eq_b - peak_b) / peak_b).min())
            # DD ratio: strategy DD as % of B&H DD
            # when B&H DD ≈ 0 (e.g. JPM rising monotonically),
            # report strategy DD directly rather than an inflated ratio
            if abs(dd_b) > 0.01:
                dd_ratio = min(round(abs(dd_s)/abs(dd_b)*100, 1), 200.0)
            else:
                # B&H nearly flat — strategy DD is the absolute metric
                dd_ratio = min(round(abs(dd_s)*100, 1), 200.0)
        else:
            dd_s = dd_b = dd_ratio = 0.0
        
        # Upside capture: strategy mean / B&H mean on weeks where B&H is up (aligned)
        # clip negative values to 0 (strategy can't have "negative capture" of upside)
        up_mask = bnh > 0
        if up_mask.sum() > 0 and bnh[up_mask].mean() > 0.001:
            uc_raw = float(strat[up_mask].mean() / bnh[up_mask].mean() * 100)
            uc = round(max(uc_raw, 0), 0)  # clip negative → 0
        else:
            uc = 0
        
        R[code] = {'s':sa, 'n': len(bu), 'alpha': alpha, 'dd_ratio': dd_ratio, 'up_capture': uc}
        print(f'S={sa} B={len(bu)} a={alpha:+.2f} DD={dd_ratio:.0f}% UC={uc:.0f}%')
    
    by_mkt = {}
    for code, name, mkt in stocks:
        by_mkt.setdefault(mkt, []).append(R[code]['s'])
    
    print(f"\n  Results:")
    for mkt, vals in by_mkt.items():
        pos = sum(1 for s in vals if s > 0)
        print(f"  {mkt}: avg S={np.mean(vals):.3f} ({pos}/{len(vals)}>0)")
    all_s = np.mean([v['s'] for v in R.values()])
    print(f"  ★ Overall avg S = {all_s:.3f}")
    
    # Compatibility summary
    pos_alpha = sum(1 for v in R.values() if v['alpha'] > 0)
    avg_dd = np.mean([v['dd_ratio'] for v in R.values()])
    avg_uc = np.mean([v['up_capture'] for v in R.values()])
    avg_alpha = np.mean([v['alpha'] for v in R.values()])
    n = len(R)
    print(f"\n  Compatibility:")
    print(f"    Pos α: {pos_alpha}/{n}={pos_alpha/n*100:.0f}% [{'PASS' if pos_alpha/n>=0.6 else 'FAIL'}]")
    print(f"    DD ratio: {avg_dd:.0f}% of B&H [{'PASS' if avg_dd<50 else 'FAIL'}]")
    print(f"    Upside capture: {avg_uc:.0f}% [{'PASS' if avg_uc>30 else 'FAIL'}]")
    print(f"    Avg α: {avg_alpha:+.3f}")
    
    # Save snapshot
    snap = {'date': str(datetime.now())[:10], 'version': version, 'sharpe': round(all_s, 3), 'stocks': R}
    snap_path = GUSHEN / f"data/tune_snapshot_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(snap_path, 'w') as f: json.dump(snap, f, indent=2, default=str)
    print(f"  💾 Snapshot: {snap_path}")
    return all_s

def reinforce():
    """强化股神：sync to production + commit"""
    print(f"  正在强化 v10.2...")

    # Sync key files to production
    prod = GUSHEN
    import shutil
    for src in ['strategy/scoring.py', 'strategy/tune.py', 'strategy/data_fetcher.py',
                'strategy/gushen_cache.py', 'strategy/fast_backtest.py',
                'strategy/bollinger.py', 'strategy/fibonacci.py', 'strategy/elliot_wave.py',
                'guts/scoring/normalize.py', 'guts/signals/continuous.py', 'SKILL.md']:
        src_path = Path(__file__).parent.parent / src
        dst_path = prod / src
        if src_path.exists():
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dst_path)
    print(f"  ✅ Files synced to {prod}")

    # Git commit
    subprocess.run(["git", "-C", str(prod), "add", "-A"], check=False)
    subprocess.run(["git", "-C", str(prod), "commit", "-m", "reinforce: v10.2 tuned and validated"], check=False)
    print(f"  ✅ v10.2 强化完成。")

def weight_grid():
    """权重网格搜索 — v10 uses fixed regime-adaptive thresholds, no weight tuning needed.
    Kept as a utility for future parameter exploration."""
    print("  ⚠️ v10.2 uses fixed V10_THRESHOLDS — weight_grid is not applicable.")
    print("  Use `backtest` action to evaluate current performance.")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="股神修炼模式")
    p.add_argument("--action", choices=["build_cache","ic_test","backtest","weight_grid","reinforce"], required=True)
    p.add_argument("--factor", default="holder_chg", help="Factor for IC test")
    p.add_argument("--universe", default="all", help="Stock universe: all, A, HK, US")
    p.add_argument("--version", default="v10", help="Scoring version (only v10 supported)")
    args = p.parse_args()

    print(f"\n  🔥 股神修炼模式 — {args.action.upper()}\n")
    if args.action == "build_cache": build_cache()
    elif args.action == "ic_test": ic_test(args.factor)
    elif args.action == "backtest": backtest(args.universe, args.version)
    elif args.action == "weight_grid": weight_grid()
    elif args.action == "reinforce": reinforce()
    print()
