# 股神 (Gushen) — OpenClaw Stock Analysis Skill

A capital-preservation focused multi-factor quantitative stock analysis skill for A-shares, HK, and US markets.

## Quick Install

Add this URL to your OpenClaw skills:

```
https://github.com/xyuchen97-blip/gushen
```

## What It Does

- 📊 **Single-stock analysis** — BUY/WATCH/HOLD/EXIT with detailed reasoning
- 📋 **Watchlist management** — maintain a curated list across A/HK/US markets
- 🌅 **Daily 8:30 AM digest** — market overview (CSI 300, S&P 500, Nasdaq, VIX, USDCNY) + watchlist scoring
- 🧹 **Auto-cleanup** — old generated files removed after 7 days

## Strategy

**14 technical signals** (Golden Pit, Nine Turns, Band King, MACD, KDJ, Bollinger, ADX, Fibonacci divergence) + **10 macro/capital signals** (northbound flow, LPR, CPI, PMI, M2, VIX, yield curve) → **0-105 composite score**.

- Score ≥ 45 → BUY
- Score 38-44 → WATCH
- Score < 20 → EXIT
- Otherwise → HOLD

**Best for**: A-shares & HK stocks in bear/sideways markets. Capital preservation (max drawdown -0.2%). Not a replacement for buy-and-hold in strong bull markets.

## Usage

### Watchlist
```bash
python3 scripts/watchlist.py add 600519 A 茅台
python3 scripts/watchlist.py add 0700.HK HK 腾讯
python3 scripts/watchlist.py list
python3 scripts/watchlist.py remove 600519
```

### Single Stock Analysis
```bash
python3 scripts/analyze.py 600519 A
python3 scripts/analyze.py NVDA US
```

### Daily Digest
```bash
python3 scripts/daily_digest.py           # text report
python3 scripts/daily_digest.py --json    # JSON for agent consumption
```

### Cleanup
```bash
python3 scripts/cleanup.py
```

## Requirements

- Python 3.10+
- `pip install akshare yfinance pandas numpy`
- A workspace with `strategy/scoring.py` (auto-discovered from `~/WorkBuddy/*/strategy/scoring.py`)

## Files

```
gushen/
├── SKILL.md              # Skill definition (persona, triggers, behavior)
├── README.md             # This file
├── .gitignore
├── scripts/
│   ├── watchlist.py      # JSON watchlist CRUD
│   ├── analyze.py        # Single-stock scoring
│   ├── daily_digest.py   # Daily market report + watchlist analysis
│   └── cleanup.py        # Remove files older than 7 days
└── data/
    └── .gitkeep          # Watchlist directory (user data not tracked)
```
