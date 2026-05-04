#!/usr/bin/env python3
"""
Stock Name Normalizer — maps free-text names to standard ticker:market pairs.

Supports: Chinese names, English names, abbreviations, common aliases.
Unknown names fall back to LLM knowledge (agent handles these).

Usage:
    python normalize.py "茅台"           # → 600519 A 贵州茅台
    python normalize.py "AMD 超微电子"    # → AMD US AMD
    python normalize.py "腾讯"           # → 0700.HK HK 腾讯控股
    python normalize.py --json "茅台"    # → JSON output
"""
import sys, json, os
from pathlib import Path
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════
# BUILT-IN MAPPING: free-text → (ticker, market, canonical_name)
# Keys are lowercased. Values are (ticker, market, display_name).
# Order: put more specific matches FIRST.
# ═══════════════════════════════════════════════════════════════════

STOCK_MAP = {}

def _add(*keys, ticker, market, name):
    for k in keys:
        STOCK_MAP[k.lower().strip()] = (ticker.upper(), market, name)

# ── A-Shares (600/000/300 series) ─────────────────────────────────
_add("茅台", "贵州茅台", "maotai", "kweichow moutai", "600519",
     ticker="600519", market="A", name="贵州茅台")

_add("五粮液", "wuliangye", "000858",
     ticker="000858", market="A", name="五粮液")

_add("宁德时代", "宁德", "catl", "当代安普", "300750",
     ticker="300750", market="A", name="宁德时代")

_add("比亚迪", "byd", "build your dreams", "002594",
     ticker="002594", market="A", name="比亚迪")

_add("招商银行", "cmb", "招行", "600036",
     ticker="600036", market="A", name="招商银行")

_add("中国平安", "平安保险", "ping an", "601318",
     ticker="601318", market="A", name="中国平安")

_add("美的", "美的集团", "midea", "000333",
     ticker="000333", market="A", name="美的集团")

_add("迈瑞医疗", "迈瑞", "mindray", "300760",
     ticker="300760", market="A", name="迈瑞医疗")

_add("恒瑞医药", "恒瑞", "600276",
     ticker="600276", market="A", name="恒瑞医药")

_add("药明康德", "药明", "wuxi", "603259",
     ticker="603259", market="A", name="药明康德")

_add("隆基绿能", "隆基", "longi", "601012",
     ticker="601012", market="A", name="隆基绿能")

_add("兴业银行", "兴业", "601166",
     ticker="601166", market="A", name="兴业银行")

_add("工商银行", "icbc", "工行", "601398",
     ticker="601398", market="A", name="工商银行")

_add("中国中免", "中免", "601888",
     ticker="601888", market="A", name="中国中免")

_add("格力电器", "格力", "gree", "000651",
     ticker="000651", market="A", name="格力电器")

_add("海康威视", "海康", "hikvision", "002415",
     ticker="002415", market="A", name="海康威视")

_add("中信证券", "中信", "600030",
     ticker="600030", market="A", name="中信证券")

_add("京东方", "boe", "000725",
     ticker="000725", market="A", name="京东方")

_add("长江电力", "600900",
     ticker="600900", market="A", name="长江电力")

_add("平安银行", "000001",
     ticker="000001", market="A", name="平安银行")

_add("山西汾酒", "汾酒", "600809",
     ticker="600809", market="A", name="山西汾酒")

# ── HK Shares ────────────────────────────────────────────────────
_add("腾讯", "tencent", "腾讯控股", "00700", "0700",
     ticker="0700.HK", market="HK", name="腾讯控股")

_add("阿里巴巴", "alibaba", "阿里", "baba", "9988", "09988",
     ticker="9988.HK", market="HK", name="阿里巴巴")

_add("美团", "meituan", "3690", "03690",
     ticker="3690.HK", market="HK", name="美团")

_add("友邦保险", "aia", "友邦", "1299", "01299",
     ticker="1299.HK", market="HK", name="友邦保险")

_add("中国平安hk", "平安hk", "2318",
     ticker="2318.HK", market="HK", name="中国平安(港)")

_add("小米", "xiaomi", "mi", "1810", "01810",
     ticker="1810.HK", market="HK", name="小米集团")

_add("京东", "jd", "jingdong", "9618", "09618",
     ticker="9618.HK", market="HK", name="京东集团")

_add("网易", "netease", "9999", "09999",
     ticker="9999.HK", market="HK", name="网易")

_add("快手", "kuaishou", "1024", "01024",
     ticker="1024.HK", market="HK", name="快手")

_add("比亚迪hk", "byd hk", "1211",
     ticker="1211.HK", market="HK", name="比亚迪股份")

_add("港交所", "hkex", "0388", "00388",
     ticker="0388.HK", market="HK", name="港交所")

_add("中国移动", "chinamobile", "0941", "00941",
     ticker="0941.HK", market="HK", name="中国移动")

# ── US Stocks ────────────────────────────────────────────────────
_add("苹果", "apple", "aapl",
     ticker="AAPL", market="US", name="Apple")

_add("微软", "microsoft", "msft",
     ticker="MSFT", market="US", name="Microsoft")

_add("英伟达", "nvidia", "nvda", "恩伟达",
     ticker="NVDA", market="US", name="Nvidia")

_add("谷歌", "google", "alphabet", "goog", "googl", "alphabat",
     ticker="GOOGL", market="US", name="Alphabet")

_add("亚马逊", "amazon", "amzn",
     ticker="AMZN", market="US", name="Amazon")

_add("meta", "facebook", "fb", "元", "meta platforms",
     ticker="META", market="US", name="Meta")

_add("特斯拉", "tesla", "tsla",
     ticker="TSLA", market="US", name="Tesla")

_add("摩根大通", "jpmorgan", "jpm", "jp morgan", "摩根",
     ticker="JPM", market="US", name="JP Morgan")

_add("amd", "超微", "超微电子", "超微半导体", "advanced micro", "amd半导体",
     ticker="AMD", market="US", name="AMD")

_add("英特尔", "intel", "intc", "intell",
     ticker="INTC", market="US", name="Intel")

_add("台积电", "tsm", "tsmc", "台湾积体电路", "台积",
     ticker="TSM", market="US", name="TSMC")

_add("博通", "broadcom", "avgo",
     ticker="AVGO", market="US", name="Broadcom")

_add("高通", "qualcomm", "qcom",
     ticker="QCOM", market="US", name="Qualcomm")

_add("甲骨文", "oracle", "orcl",
     ticker="ORCL", market="US", name="Oracle")

_add("阿斯麦", "asml",
     ticker="ASML", market="US", name="ASML")

_add("奈飞", "netflix", "nflx",
     ticker="NFLX", market="US", name="Netflix")

_add("可口可乐", "cocacola", "coke", "ko",
     ticker="KO", market="US", name="Coca-Cola")

_add("伯克希尔", "berkshire", "brk", "brk.b", "brkb", "巴菲特", "buffett",
     ticker="BRK-B", market="US", name="Berkshire Hathaway")

_add("标普500", "sp500", "spx", "标普",
     ticker="IVV", market="US", name="S&P 500 ETF")

_add("纳斯达克", "nasdaq", "纳指", "qqq",
     ticker="QQQ", market="US", name="Nasdaq ETF")

_add("道琼斯", "dow", "dia", "道指",
     ticker="DIA", market="US", name="Dow ETF")

# ── Indices ──────────────────────────────────────────────────────
_add("沪深300", "csi300", "csi 300", "000300",
     ticker="000300", market="CN_IDX", name="沪深300")

_add("上证50", "上证", "000016",
     ticker="000016", market="CN_IDX", name="上证50")

_add("恒生", "恒生指数", "hsi", "hang seng",
     ticker="HSI", market="HK", name="恒生指数")


# ═══════════════════════════════════════════════════════════════════
# NORMALIZATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def normalize_one(name: str) -> dict | None:
    """Map a single free-text name to (ticker, market, display_name)."""
    name = name.strip()
    key = name.lower().strip()
    
    # Direct lookup
    if key in STOCK_MAP:
        t, m, n = STOCK_MAP[key]
        return {"ticker": t, "market": m, "name": n, "input": name}

    # Try without special chars
    import re
    clean = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff]', '', key)
    if clean and clean != key and clean in STOCK_MAP:
        t, m, n = STOCK_MAP[clean]
        return {"ticker": t, "market": m, "name": n, "input": name}
    
    return None  # Unknown — agent should use LLM knowledge


def normalize_all(names: list[str]) -> list[dict]:
    """Normalize a list of free-text names. Returns [{"ticker","market","name","input"}]."""
    results = []
    for name in names:
        r = normalize_one(name)
        if r:
            results.append(r)
        else:
            # Mark as unknown — caller (LLM agent) should resolve
            results.append({
                "ticker": None, "market": None, "name": name,
                "input": name, "unknown": True,
                "hint": f"Unknown: '{name}'. Try providing the stock code directly (e.g., '600519' or 'NVDA')."
            })
    return results


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]
    json_mode = "--json" in args
    names = [a for a in args if a != "--json"]
    
    if not names:
        print("Usage: python normalize.py [--json] <name1> [name2 ...]")
        print("Example: python normalize.py 茅台 AMD 超微电子 腾讯")
        sys.exit(1)
    
    results = normalize_all(names)
    
    if json_mode:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for r in results:
            if r.get("unknown"):
                print(f"❓ {r['input']:20s} → {r['hint']}")
            else:
                print(f"✅ {r['input']:20s} → {r['ticker']:10s} {r['market']:4s} {r['name']}")
