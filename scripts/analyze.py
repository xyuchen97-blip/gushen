#!/usr/bin/env python3
"""
Single-Stock Analyzer — runs the locked multi-factor scoring engine.
"""
import sys, os, json
from datetime import datetime, timedelta
from pathlib import Path

# Find strategy module — check multiple locations
STRATEGY_PATHS = [
    os.environ.get("STRATEGY_PATH", ""),
    str(Path.home() / "WorkBuddy" / "20260410124849"),
]
strategy_root = next((p for p in STRATEGY_PATHS if p and Path(p).exists()), None)
if not strategy_root:
    print("❌ 找不到策略模块路径。请设置 STRATEGY_PATH 环境变量。", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, strategy_root)

from strategy.scoring import score
from strategy.data_fetcher import fetch_ohlcv, fetch_macro_data, clear_cache

MARKET_NAMES = {"A": "A股", "HK": "港股", "US": "美股", "CN_IDX": "指数"}

def analyze(ticker, market, start="2021-01-01"):
    """Run full scoring analysis on a single stock."""
    end = datetime.now().strftime("%Y-%m-%d")
    
    # Fetch data
    try:
        macro = fetch_macro_data(start, end)
        df_daily = fetch_ohlcv(ticker, market, start, end, freq="daily")
        if df_daily is None or df_daily.empty or len(df_daily) < 50:
            return {"error": f"{ticker}: 数据不足（需要至少50个交易日）"}
        df_weekly = fetch_ohlcv(ticker, market, start, end, freq="weekly")
        if df_weekly is None or df_weekly.empty:
            return {"error": f"{ticker}: 周线数据不足"}
    except Exception as e:
        return {"error": f"数据获取失败: {e}"}
    finally:
        clear_cache()

    # Score
    result = score(df_daily, df_weekly, ticker=ticker, market=market, macro_data=macro)
    current_price = float(df_daily["close"].iloc[-1])
    result["current_price"] = current_price
    result["ticker"] = ticker
    result["market_name"] = MARKET_NAMES.get(market, market)
    return result

def format_analysis(result):
    """Format the result as a readable text block."""
    if "error" in result:
        return f"⚠️ {result['error']}"

    action_icons = {"BUY": "🟢 买入", "WATCH": "🟡 观察", "HOLD": "⚪ 持有", "EXIT": "🔴 卖出"}
    regime_icons = {True: "🐂 牛市（价格>MA200）", False: "🐻 熊市（价格<MA200）"}

    lines = [
        f"🐉 股神分析：{result.get('ticker','')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 综合评分：{result['composite']:.1f} / 105",
        f"🎯 操作建议：{action_icons.get(result['action'], result['action'])}",
        f"📈 市场环境：{regime_icons.get(result.get('bull_regime', False))}",
    ]
    
    if result.get('active'):
        lines.append(f"🔍 触发信号：{', '.join(result['active'])}")
    
    if result.get('current_price'):
        lines.append(f"💰 当前价格：¥{result['current_price']:.2f}" if result.get('market_name') in ('A股','指数') else f"💰 当前价格：${result['current_price']:.2f}")

    lines.append("")
    lines.append(f"评分明细：技术={result.get('tech_score',0):.0f} | 资金流={result.get('cap_score',0):.0f} | 基本面={result.get('fund_score',0):.0f} | 宏观={result.get('macro_score',0):.0f} | 斐波那契+{result.get('fib_bonus',0)}")
    lines.append(f"理由：{result.get('reasoning','')}")
    lines.append("")

    if result['action'] == 'BUY':
        lines.append("⚠️ 注意：此为技术面分析建议，不构成投资意见。请结合基本面和自身风险承受能力综合判断。")
    elif result['action'] == 'EXIT':
        lines.append("⚠️ 综合评分低于退出阈值。建议关注风险。")
    
    return "\n".join(lines)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python analyze.py <ticker> <market>")
        print("  market: A (A股), HK (港股), US (美股), CN_IDX (指数)")
        sys.exit(1)
    
    ticker = sys.argv[1].strip().upper()
    market = sys.argv[2].strip()
    result = analyze(ticker, market)
    print(format_analysis(result))
