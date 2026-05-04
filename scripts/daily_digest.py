#!/usr/bin/env python3
"""
Daily Digest — OpenClaw-compatible market overview + watchlist stock analysis.

Runs as a self-contained automation task. Outputs plain text report by default,
or JSON with --json flag for programmatic consumption by any agent/LLM.

Usage:
    python daily_digest.py                      # text report to stdout
    python daily_digest.py --json               # JSON to stdout (for agent consumption)
    python daily_digest.py --strategy-path /x   # override strategy discovery
"""
import sys, os, json, time as time_mod
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd


# ═══════════════════════════════════════════════════════════════════
# STRATEGY DISCOVERY — finds strategy/scoring.py across workspaces
# ═══════════════════════════════════════════════════════════════════

def _find_strategy_root() -> str | None:
    """Scan ~/WorkBuddy/ for the latest workspace containing strategy/scoring.py"""
    explicit = os.environ.get("STRATEGY_PATH", "")
    if explicit and Path(explicit, "strategy", "scoring.py").exists():
        return explicit

    wb_dir = Path.home() / "WorkBuddy"
    if not wb_dir.exists():
        return None

    # Find all workspace dirs with strategy/scoring.py, pick newest
    candidates = []
    for ws in wb_dir.iterdir():
        if ws.is_dir() and (ws / "strategy" / "scoring.py").exists():
            candidates.append((ws.stat().st_mtime, str(ws)))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]

    return None


strategy_root = _find_strategy_root()
if not strategy_root:
    print("❌ 找不到策略模块。请设置 STRATEGY_PATH 环境变量或确保 strategy/scoring.py 存在。",
          file=sys.stderr)
    sys.exit(1)
sys.path.insert(0, strategy_root)

from strategy.scoring import score
from strategy.data_fetcher import fetch_ohlcv, fetch_macro_data, clear_cache

WL_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"
MARKET_NAMES = {"A": "A股", "HK": "港股", "US": "美股", "CN_IDX": "指数"}

# ═══════════════════════════════════════════════════════════════════
# BENCHMARKS — daily market overview
# ═══════════════════════════════════════════════════════════════════

BENCHMARKS = [
    ("000300",  "CN_IDX", "沪深300",     "A股大盘"),
    ("0700.HK", "HK",     "腾讯(港股代表)",  "港股风向标"),
    ("IVV",     "US",     "标普500",     "美股大盘"),
    ("QQQ",     "US",     "纳斯达克100", "科技股"),
]


def load_watchlist() -> list[dict]:
    if not WL_FILE.exists():
        return []
    return json.loads(WL_FILE.read_text()).get("stocks", [])


def fetch_benchmark(ticker: str, market: str, start: str, end: str):
    """Fetch benchmark OHLCV. Handles CSI 300 via akshare."""
    if market == "CN_IDX":
        import akshare as ak
        try:
            df = ak.stock_zh_index_daily(symbol="sh000300")
            df.rename(columns={"date": "date", "open": "open", "high": "high",
                               "low": "low", "close": "close", "volume": "volume"}, inplace=True)
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            return df.sort_index().loc[start:end]
        except Exception:
            return None
    return fetch_ohlcv(ticker, market, start, end, freq="daily")


def _macro_section(macro: dict, lines: list):
    """Append macro overview lines."""
    if "vix" in macro and not macro["vix"].empty:
        vix = float(macro["vix"].iloc[-1])
        prev = float(macro["vix"].iloc[-2]) if len(macro["vix"]) > 1 else vix
        arrow = "↑" if vix > prev else "↓"
        status = "低波动" if vix < 20 else ("中等" if vix < 25 else "恐慌")
        lines.append(f"  VIX恐慌指数: {vix:.1f} {arrow}  {status}")

    if "usdcny" in macro and not macro["usdcny"].empty:
        cny = float(macro["usdcny"].iloc[-1])
        lines.append(f"  美元/人民币: {cny:.4f}")

    if "yield10y" in macro and "yield5y" in macro:
        sp = float(macro["yield10y"].iloc[-1]) - float(macro["yield5y"].iloc[-1])
        status = "正常" if sp > 0.5 else ("平坦" if sp > 0 else "倒挂")
        lines.append(f"  美债10Y-5Y利差: {sp:.2f}% ({status})")


def _benchmark_section(start: str, end: str, lines: list, json_data: dict):
    """Append benchmark performance lines."""
    json_data["benchmarks"] = []
    for ticker, market, name, desc in BENCHMARKS:
        try:
            df = fetch_benchmark(ticker, market, start, end)
            if df is not None and len(df) > 5:
                close = df["close"]
                latest = float(close.iloc[-1])
                chg_5d = (latest - float(close.iloc[-6])) / float(close.iloc[-6]) * 100
                chg_20d = (latest - float(close.iloc[-21])) / float(close.iloc[-21]) * 100 if len(close) > 20 else 0
                arrow = "+" if chg_5d > 0 else ""
                lines.append(f"  {desc}: {latest:.1f}  5日: {arrow}{chg_5d:.1f}%  20日: {chg_20d:+.1f}%")
                json_data["benchmarks"].append({
                    "ticker": ticker, "name": name, "latest": latest,
                    "chg_5d": round(chg_5d, 2), "chg_20d": round(chg_20d, 2),
                })
        except Exception:
            pass


def _watchlist_section(start: str, end: str, macro: dict,
                       lines: list, json_data: dict) -> tuple[bool, bool]:
    """Append watchlist analysis. Returns (has_buy, has_exit)."""
    watchlist = load_watchlist()
    json_data["stocks"] = []
    any_buy = False
    any_exit = False

    if not watchlist:
        lines.append("\n观察清单为空。")
        return False, False

    lines.append(f"\n观察清单 ({len(watchlist)}只):")
    lines.append(f"{'代码':<12} {'市场':<5} {'评分':>5} {'建议':<6} {'信号'}")
    lines.append("─" * 55)

    for stock in watchlist:
        ticker = stock["ticker"]
        market = stock["market"]
        stock_entry = {"ticker": ticker, "market": market, "status": "ok"}

        try:
            if market == "CN_IDX":
                df_d = fetch_benchmark(ticker, market, start, end)
                df_w = df_d.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min",
                                                     "close": "last", "volume": "sum"}).dropna() if df_d is not None else None
            else:
                df_d = fetch_ohlcv(ticker, market, start, end, freq="daily")
                df_w = fetch_ohlcv(ticker, market, start, end, freq="weekly")

            if df_d is None or len(df_d) < 50 or df_w is None or len(df_w) < 20:
                lines.append(f"{ticker:<12} {'':<5} {'N/A':>5} {'数据不足':<6}")
                stock_entry["status"] = "no_data"
                json_data["stocks"].append(stock_entry)
                continue

            r = score(df_d, df_w, ticker=ticker, market=market, macro_data=macro)
            comp = r["composite"]
            action = r["action"]
            sig_str = ", ".join(r["active"][:3]) if r["active"] else "—"
            icon = {"BUY": "🟢", "WATCH": "🟡", "HOLD": "⚪", "EXIT": "🔴"}.get(action, "")

            lines.append(f"{ticker:<12} {MARKET_NAMES.get(market,market):<5} {comp:>4.0f}  {icon}{action:<4} {sig_str}")
            stock_entry.update({
                "composite": round(comp, 1), "action": action,
                "signals": r["active"], "bull_regime": r["bull_regime"],
                "tech_score": r["tech_score"], "cap_score": r["cap_score"],
                "macro_score": r["macro_score"],
            })
            if action == "BUY":
                any_buy = True
            if action == "EXIT":
                any_exit = True

        except Exception as e:
            lines.append(f"{ticker:<12} {'':<5} {'ERR':>5} {str(e)[:30]}")
            stock_entry["status"] = "error"
            stock_entry["error"] = str(e)

        json_data["stocks"].append(stock_entry)

    if any_buy:
        lines.append("\n🟢 BUY信号 — 关注入场时机")
    if any_exit:
        lines.append("🔴 EXIT信号 — 关注风险")
    return any_buy, any_exit


def run(format_json: bool = False) -> str:
    """Run the full daily digest. Returns text or JSON."""
    t0 = time_mod.time()
    today = datetime.now()
    start = (today - timedelta(days=365 * 2)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    json_data = {
        "date": today.strftime("%Y-%m-%d"),
        "weekday": today.strftime("%A"),
        "strategy_workspace": strategy_root,
    }

    lines = []
    lines.append(f"股神每日报告 — {today.strftime('%Y年%m月%d日')} {today.strftime('%A')}")
    lines.append("=" * 55)

    # ── Macro ─────────────────────────────────────────────────
    t_macro = time_mod.time()
    try:
        macro = fetch_macro_data(start, end)
        json_data["macro_available"] = bool(macro)
    except Exception as e:
        macro = {}
        json_data["macro_error"] = str(e)

    lines.append("\n市场速览:")
    _macro_section(macro, lines)
    _benchmark_section(start, end, lines, json_data)
    json_data["macro_fetch_sec"] = round(time_mod.time() - t_macro, 1)

    # ── Watchlist ─────────────────────────────────────────────
    t_wl = time_mod.time()
    has_buy, has_exit = _watchlist_section(start, end, macro, lines, json_data)
    json_data["watchlist_scan_sec"] = round(time_mod.time() - t_wl, 1)

    # ── Footer ────────────────────────────────────────────────
    elapsed = time_mod.time() - t0
    lines.append(f"\n{'─' * 55}")
    lines.append(f"策略: {strategy_root} | 耗时: {elapsed:.1f}s")
    lines.append("以上为AI量化策略分析，不构成投资建议。")

    json_data["total_sec"] = round(elapsed, 1)
    json_data["watchlist_count"] = len(load_watchlist())
    json_data["has_buy_signal"] = has_buy
    json_data["has_exit_signal"] = has_exit

    clear_cache()

    if format_json:
        return json.dumps(_sanitize(json_data), indent=2, ensure_ascii=False)
    return "\n".join(lines)


def _sanitize(obj):
    """Convert numpy types to native Python for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    return obj


if __name__ == "__main__":
    args = sys.argv[1:]
    json_mode = "--json" in args

    if "--strategy-path" in args:
        idx = args.index("--strategy-path")
        os.environ["STRATEGY_PATH"] = args[idx + 1]

    output = run(format_json=json_mode)
    print(output)
