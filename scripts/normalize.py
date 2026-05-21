#!/usr/bin/env python3
"""
Stock Name Normalizer v2 — Zhipu GLM-4-Flash powered.

Replaces hardcoded 271-line STOCK_MAP with LLM resolution.
Cache persists to ~/.workbuddy/stock_cache.json for zero repeat API calls.

Usage:
    python normalize.py "茅台"            # → 600519 A 贵州茅台
    python normalize.py "Quantum Computing Inc"  # → QUBT US Quantum Computing
    python normalize.py --json "腾讯"     # → JSON output
"""
import sys, json, os, requests
from pathlib import Path
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════
# ZHIPU LLM CONFIG
# ═══════════════════════════════════════════════════════════════════
ZHIPU_API_KEY = "82e5ed0f0960410c9ee93849295a5467.kv6mLp0DtG4RWPdG"
ZHIPU_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_MODEL = "glm-4-flash"

# ═══════════════════════════════════════════════════════════════════
# CACHE: ~/.workbuddy/stock_cache.json
# ═══════════════════════════════════════════════════════════════════
CACHE_PATH = Path.home() / ".workbuddy" / "stock_cache.json"

def _load_cache():
    if CACHE_PATH.exists():
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}

def _save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

# ═══════════════════════════════════════════════════════════════════
# LLM RESOLUTION
# ═══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are a stock ticker resolver. Given a stock name in Chinese or English, return the correct ticker and market.

Rules:
- A-shares: return 6-digit code (e.g., 600519) and market "A"
- HK stocks: return 4-digit code with .HK suffix (e.g., 0700.HK) and market "HK"  
- US stocks: return uppercase ticker (e.g., AAPL) and market "US"
- Indexes: return the index code and market "CN_IDX" (e.g., 000300 for 沪深300)
- Return ONLY a JSON object: {"ticker": "CODE", "market": "A|HK|US|CN_IDX", "name": "Canonical Name"}
- No other text. No explanations. Just the JSON."""

def _llm_resolve(name: str) -> dict | None:
    """Ask Zhipu GLM-4-Flash to resolve a stock name."""
    try:
        resp = requests.post(
            ZHIPU_ENDPOINT,
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": ZHIPU_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Resolve: {name}"}
                ],
                "max_tokens": 80,
                "temperature": 0
            },
            timeout=10
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        
        # Parse JSON from response (handle markdown code blocks)
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        
        result = json.loads(content)
        return {
            "ticker": str(result.get("ticker", "")).upper(),
            "market": str(result.get("market", "")),
            "name": str(result.get("name", name))
        }
    except Exception as e:
        return None

# ═══════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════

def normalize_one(name: str) -> dict | None:
    """Map a single free-text name to (ticker, market, display_name)."""
    name = name.strip()
    key = name.lower().strip()
    
    # 1. Cache lookup
    cache = _load_cache()
    if key in cache:
        entry = cache[key]
        return {"ticker": entry["ticker"], "market": entry["market"], "name": entry["name"], "input": name}
    
    # 2. LLM resolution
    result = _llm_resolve(name)
    if result and result.get("ticker"):
        cache[key] = result
        _save_cache(cache)
        return {**result, "input": name}
    
    # 3. Give up
    return {"ticker": None, "market": None, "name": name, "input": name, "unknown": True,
            "hint": f"Unknown: '{name}'. Try providing the stock code directly."}


def normalize_all(names: list[str]) -> list[dict]:
    return [normalize_one(n) for n in names]

# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]
    json_mode = "--json" in args
    names = [a for a in args if a != "--json"]
    
    if not names:
        print("Usage: python normalize.py [--json] <name1> [name2 ...]")
        print("Example: python normalize.py 茅台 AMD 腾讯 寒武纪")
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
