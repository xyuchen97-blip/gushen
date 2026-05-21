#!/usr/bin/env python3
"""
Watchlist Manager — CRUD for data/watchlist.json.

Usage:
    python watchlist.py list
    python watchlist.py add <ticker> <market> [name]
    python watchlist.py remove <ticker>
    python watchlist.py clear
    python watchlist.py json   # output raw JSON (for scripts to consume)
"""
import sys, json, os
from datetime import datetime
from pathlib import Path

WL_FILE = Path(__file__).parent.parent / "data" / "watchlist.json"

def _load():
    if WL_FILE.exists():
        return json.loads(WL_FILE.read_text())
    return {"stocks": [], "last_updated": ""}

def _save(data):
    data["last_updated"] = datetime.now().isoformat()
    WL_FILE.parent.mkdir(parents=True, exist_ok=True)
    WL_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def list_stocks():
    data = _load()
    if not data["stocks"]:
        print("📋 观察清单为空。发送股票代码来添加关注的股票。")
        return data
    print("📋 当前观察清单：\n")
    print(f"{'代码':<12} {'市场':<6} {'名称':<12} {'加入日期':<12}")
    print("─" * 46)
    for s in data["stocks"]:
        print(f"{s['ticker']:<12} {s['market']:<6} {s.get('name',''):<12} {s.get('added','')[:10]:<12}")
    print(f"\n共 {len(data['stocks'])} 只股票")
    return data

def add_stock(ticker, market, name=""):
    data = _load()
    ticker = ticker.strip().upper()
    # Check duplicate
    for s in data["stocks"]:
        if s["ticker"] == ticker:
            print(f"⚠️ {ticker} 已在观察清单中。")
            return data
    data["stocks"].append({
        "ticker": ticker,
        "market": market,
        "name": name or ticker,
        "added": datetime.now().strftime("%Y-%m-%d"),
    })
    _save(data)
    print(f"✅ {ticker} ({name or ticker}) 已加入观察清单 (共{len(data['stocks'])}只)")
    return data

def remove_stock(ticker):
    data = _load()
    ticker = ticker.strip().upper()
    before = len(data["stocks"])
    data["stocks"] = [s for s in data["stocks"] if s["ticker"] != ticker]
    after = len(data["stocks"])
    if before == after:
        print(f"⚠️ {ticker} 不在观察清单中。")
    else:
        _save(data)
        print(f"✅ {ticker} 已从观察清单移除 (剩余{after}只)")
    return data

def clear_all():
    data = _load()
    if not data["stocks"]:
        print("📋 观察清单已经是空的。")
        return data
    count = len(data["stocks"])
    data["stocks"] = []
    _save(data)
    print(f"✅ 已清空观察清单 (原{count}只)")
    return data

def output_json():
    data = _load()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return data

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python watchlist.py <list|add|remove|clear|json> [args]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        list_stocks()
    elif cmd == "add" and len(sys.argv) >= 4:
        add_stock(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "remove" and len(sys.argv) >= 3:
        remove_stock(sys.argv[2])
    elif cmd == "clear":
        clear_all()
    elif cmd == "json":
        output_json()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
