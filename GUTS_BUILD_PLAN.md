# GUTS Engine — Cloud Deployment Build Plan

> **Purpose**: Step-by-step execution plan for building the independent GUTS (Gushen Unified Trading System) engine on an Ubuntu cloud server.
> **Date**: 2026-05-20
> **Source**: Extracted from Gushen v10 handoff package (`~/Desktop/gushen_handoff/`)
> **Target**: Ubuntu 22.04+ cloud server (any provider: AWS, GCP, Vultr, etc.)
> **Audience**: Execution AI — follow each phase sequentially, verify before proceeding

---

## Phase 0: Prerequisites & Environment

### 0.1 Server Requirements

- **OS**: Ubuntu 22.04 LTS or newer
- **CPU**: 2+ cores (scoring is CPU-bound, not GPU)
- **RAM**: 4GB minimum (21 stocks × ~500KB precomputed data per stock = ~10MB peak)
- **Disk**: 20GB minimum (DB + logs + data cache)
- **Network**: Outbound HTTPS to APIs (akshare, FRED, Tushare, Tiingo)
- **Python**: 3.10+

### 0.2 API Keys Required

Store these in `/etc/guts/.env` (never in code):

```env
# Tushare Pro — primary A-share data source (258 APIs)
TUSHARE_TOKEN=<your-tushare-token>

# FRED — US macro data (VIX, yields, unemployment)
FRED_API_KEY=<your-fred-api-key>

# Tiingo — US stock backup
TIINGO_KEY=5eb4fd3ed2a24d3a85dc823e93f18d3fbfc32639

# Alpha Vantage — US stock last-resort (free tier, 25/day)
ALPHA_VANTAGE_KEY=

# Zhipu GLM-4-Flash — stock name normalization (optional)
ZHIPU_API_KEY=

# Notification (choose one or both)
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=tomj2019@foxmail.com
SMTP_PASS=
NOTIFY_EMAIL=tomj2019@foxmail.com

# Optional: Telegram/WeChat bot
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### 0.3 System Setup

```bash
# System packages
sudo apt update && sudo apt install -y python3.10 python3.10-venv python3-pip \
    postgresql postgresql-contrib redis-server nginx certbot git

# Create guts user
sudo useradd -m -s /bin/bash guts
sudo mkdir -p /opt/guts /var/log/guts /etc/guts
sudo chown guts:guts /opt/guts /var/log/guts /etc/guts

# Switch to guts user for everything below
sudo su - guts
```

### 0.4 Python Environment

```bash
cd /opt/guts
python3.10 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install \
    pandas>=2.0 numpy>=1.24 \
    akshare>=1.12 tushare>=1.4 yfinance>=0.2 \
    fastapi>=0.110 uvicorn[standard]>=0.29 \
    sqlalchemy>=2.0 psycopg2-binary>=2.9 alembic>=1.13 \
    apscheduler>=3.10 \
    python-dotenv>=1.0 \
    httpx>=0.27 \
    jinja2>=3.1 \
    loguru>=0.7 \
    pydantic>=2.6 \
    redis>=5.0 \
    pytest>=8.0
```

### 0.5 PostgreSQL Setup

```bash
sudo -u postgres psql << 'SQL'
CREATE USER guts WITH PASSWORD 'guts_secure_password_change_me';
CREATE DATABASE guts_db OWNER guts;
GRANT ALL PRIVILEGES ON DATABASE guts_db TO guts;
SQL
```

---

## Phase 1: Project Structure & Code Extraction

### 1.1 Directory Layout

Create this exact structure under `/opt/guts/`:

```
/opt/guts/
├── venv/                       # Python virtual environment
├── .env                        # Symlink to /etc/guts/.env
├── guts/                       # Main package
│   ├── __init__.py
│   ├── config.py               # All constants, thresholds, env loading
│   ├── models.py               # SQLAlchemy ORM models
│   ├── api/                    # FastAPI application
│   │   ├── __init__.py
│   │   ├── app.py              # FastAPI app factory
│   │   ├── routes.py           # API endpoints
│   │   └── schemas.py          # Pydantic request/response models
│   ├── scoring/                # Core scoring engine
│   │   ├── __init__.py
│   │   ├── precompute.py       # precompute() — extracted from scoring.py line 334-527
│   │   ├── engine.py           # score_bar_v5() — extracted from scoring.py line 2533-2987
│   │   ├── legacy.py           # score_bar() v9.7 — for regression testing
│   │   └── entry.py            # score() — high-level entry point
│   ├── signals/                # Signal computation
│   │   ├── __init__.py
│   │   ├── bollinger.py        # Copy from strategy/bollinger.py
│   │   ├── fibonacci.py        # Copy from strategy/fibonacci.py
│   │   ├── elliot_wave.py      # Copy from strategy/elliot_wave.py (triple_confirm only)
│   │   └── continuous.py       # Copy from guts/signals/continuous.py
│   ├── indicators/             # DZH proprietary indicators
│   │   ├── __init__.py
│   │   ├── golden_pit.py       # Copy from dzh_indicators/golden_pit.py
│   │   ├── jiu_zhuan.py        # Copy from dzh_indicators/jiu_zhuan.py
│   │   └── band_king.py        # Copy from dzh_indicators/band_king.py
│   ├── macro/                  # Macro layer
│   │   ├── __init__.py
│   │   ├── compute.py          # Copy from guts/macro/compute.py
│   │   ├── state.py            # Copy from guts/macro/state.py
│   │   └── sensitivity.py      # Copy from guts/macro/sensitivity.py
│   ├── data/                   # Data pipeline
│   │   ├── __init__.py
│   │   ├── fetcher.py          # Unified data fetcher (refactored from data_fetcher.py)
│   │   ├── cache.py            # Database cache layer (replaces gushen_cache.py)
│   │   └── migrations/         # Alembic migrations
│   ├── portfolio/              # Portfolio management (NEW)
│   │   ├── __init__.py
│   │   ├── backtest.py         # Backtest harness (refactored from tune.py)
│   │   ├── position.py         # Position tracking
│   │   └── sizing.py           # Macro_mult application
│   ├── notify/                 # Notification layer (NEW)
│   │   ├── __init__.py
│   │   ├── email.py            # SMTP email sender
│   │   ├── telegram.py         # Telegram bot (optional)
│   │   └── formatter.py        # Signal → human-readable message
│   ├── scheduler/              # Job scheduling (NEW)
│   │   ├── __init__.py
│   │   └── jobs.py             # APScheduler job definitions
│   └── utils/                  # Utilities
│       ├── __init__.py
│       ├── normalizer.py       # Copy from guts/utils/normalizer.py
│       └── llm_resolvers.py    # Copy from guts/utils/llm_resolvers.py
├── tests/                      # Test suite
│   ├── __init__.py
│   ├── test_precompute.py
│   ├── test_engine.py
│   ├── test_fetcher.py
│   ├── test_api.py
│   └── conftest.py             # Shared fixtures
├── alembic/                    # Database migrations
│   ├── alembic.ini
│   └── versions/
├── scripts/                    # Operational scripts
│   ├── seed_db.py              # Import SQLite → PostgreSQL
│   ├── daily_score.py          # Standalone daily scoring script
│   └── backtest.py             # Standalone backtest runner
├── deploy/                     # Deployment configs
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── nginx.conf
│   ├── guts-api.service        # systemd unit for API
│   ├── guts-scheduler.service  # systemd unit for scheduler
│   └── logrotate.conf
└── data/                       # Local data (gitignored)
    ├── gushen.db               # Seed SQLite (copied from handoff)
    └── logs/
```

### 1.2 Extraction Map: scoring.py → GUTS Modules

The extraction is line-by-line from the handoff's `strategy/scoring.py` (3010 lines). Here is the exact mapping:

#### `guts/config.py` — Extract constants

Source lines in `scoring.py`:

```
Lines 47-56:   BEAR_TREND_DISCOUNT, MA20_PENALTY_A_HK, MA20_PENALTY_US
Lines 2522-2531: V10_THRESHOLDS dict
```

Create `guts/config.py`:

```python
"""GUTS Configuration — all tunable parameters in one place."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv("/etc/guts/.env")

# ── Engine version ──
ENGINE_VERSION = "v10"

# ── Market thresholds (grid-searched, OOS-validated May 2026) ──
V10_THRESHOLDS = {
    "US": {"bear_buy": 32, "bear_watch": 24, "bear_exit": 10,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 10},
    "HK": {"bear_buy": 28, "bear_watch": 20, "bear_exit": 10,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 10},
    "A":  {"bear_buy": 25, "bear_watch": 17, "bear_exit": 0,
           "bull_buy": 28, "bull_watch": 20, "bull_exit": 0},
}

# ── Scoring constants ──
BEAR_TREND_DISCOUNT = 0.40    # P4 trend weight in bear mode
MA20_PENALTY_A_HK = 0.65      # Counter-trend penalty for A/HK
MA20_PENALTY_US = 0.75         # Counter-trend penalty for US

# ── Stock universe ──
STOCK_UNIVERSE = [
    ("600519.SH", "茅台", "A"), ("000858.SZ", "五粮液", "A"),
    ("300750.SZ", "宁德时代", "A"), ("002594.SZ", "比亚迪", "A"),
    ("601318.SH", "平安", "A"), ("600036.SH", "招行", "A"),
    ("002230.SZ", "科大讯飞", "A"), ("300015.SZ", "爱尔眼科", "A"),
    ("0700.HK", "腾讯", "HK"), ("9988.HK", "阿里", "HK"),
    ("3690.HK", "美团", "HK"), ("1810.HK", "小米", "HK"),
    ("1211.HK", "比亚迪", "HK"), ("0388.HK", "港交所", "HK"),
    ("AAPL", "苹果", "US"), ("NVDA", "英伟达", "US"),
    ("MSFT", "微软", "US"), ("GOOGL", "谷歌", "US"),
    ("AMZN", "亚马逊", "US"), ("META", "Meta", "US"),
    ("JPM", "摩根大通", "US"),
]

# ── API keys (from env) ──
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
TIINGO_KEY = os.getenv("TIINGO_KEY", "")
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

# ── Database ──
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://guts:guts_secure_password_change_me@localhost:5432/guts_db")

# ── Scheduling ──
# Daily scoring schedule (Hong Kong timezone, UTC+8)
SCORING_SCHEDULE_CRON = {"hour": 8, "minute": 30, "timezone": "Asia/Hong_Kong"}
# Data refresh: weekday evenings after all markets close
DATA_REFRESH_CRON = {"hour": 22, "minute": 0, "day_of_week": "mon-fri", "timezone": "Asia/Hong_Kong"}

# ── Notifications ──
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")
```

#### `guts/scoring/precompute.py` — Extract precompute()

Source: `scoring.py` lines 334-527 (`def precompute(df_daily, df_weekly) -> dict`)

This function is self-contained. Copy it verbatim. Fix imports to point to the new package paths:

```python
# Old imports in scoring.py (lines 1-35):
from dzh_indicators.golden_pit import golden_pit_v2
from dzh_indicators.jiu_zhuan import compute_jiu_zhuan
from dzh_indicators.band_king import compute_band_king
from .bollinger import compute_weekly_bb, bb_weekly_sell_signal, bb_weekly_buy_signal
from .fibonacci import score_fibonacci
from .elliot_wave import detect_wave5_target, detect_right_shoulder, triple_confirm

# New imports in guts/scoring/precompute.py:
from guts.indicators.golden_pit import golden_pit_v2
from guts.indicators.jiu_zhuan import compute_jiu_zhuan
from guts.indicators.band_king import compute_band_king
from guts.signals.bollinger import compute_weekly_bb, bb_weekly_sell_signal, bb_weekly_buy_signal
from guts.signals.fibonacci import score_fibonacci
from guts.signals.elliot_wave import detect_wave5_target, detect_right_shoulder, triple_confirm
```

The function body is unchanged. It takes `(df_daily: pd.DataFrame, df_weekly: pd.DataFrame) -> dict` and returns a dict of `pd.Series`, all indexed to df_daily.

#### `guts/scoring/engine.py` — Extract score_bar_v5()

Source: `scoring.py` lines 2533-2987 (`def score_bar_v5(...)`)

Fix imports:
```python
from guts.config import V10_THRESHOLDS, BEAR_TREND_DISCOUNT, MA20_PENALTY_A_HK, MA20_PENALTY_US
```

The function signature stays:
```python
def score_bar_v5(i: int, df_daily: pd.DataFrame, precomputed: dict,
                 macro_data: dict = None, weights: dict = None,
                 market: str = "US", ticker: str = "", score_history=None) -> dict:
```

Return contract (the dict the engine produces):
```python
{
    "composite":    float,    # final composite score
    "action":       str,      # "BUY" | "WATCH" | "HOLD" | "EXIT"
    "active":       list,     # active signal names (for debugging/logging)
    "mode":         str,      # "contrarian_entry" | "bull_entry"
    "regime":       str,      # "bull" | "bear"
    "strong_bull":  bool,     # bull + ADX>25 + plus_di > minus_di
    "entry_score":  float,    # raw entry signal score
    "hold_score":   float,    # trend hold score (for exit logic)
    "cap_bonus":    int,      # capital/volume bonus
    "vol_confirm":  float,    # volume confirmation multiplier
    "bb_sell":      bool,     # Bollinger weekly sell active
    "bull_regime":  bool,     # alias for strong_bull
    "mgmt_hints":   dict,     # position management hints (trail stop etc)
    "macro_mult":   float,    # 0.5-1.3, portfolio-level position sizing multiplier
    "tech_score":   float,    # = entry_score (backward compat)
    "fib_bonus":    int,      # fibonacci bonus (bull mode only)
}
```

#### `guts/scoring/entry.py` — High-level entry point

```python
"""GUTS scoring entry point — single call to score a stock."""
from guts.scoring.precompute import precompute
from guts.scoring.engine import score_bar_v5

def score(df_daily, df_weekly, ticker="", market="US", macro_data=None):
    """Score the most recent bar for a ticker. Main entry point for all callers."""
    if len(df_daily) < 50:
        return {"error": "Need at least 50 bars"}
    precomputed = precompute(df_daily, df_weekly)
    return score_bar_v5(len(df_daily) - 1, df_daily, precomputed,
                        macro_data, None, market, ticker)
```

### 1.3 Direct Copies (No Modifications Needed)

These files can be copied verbatim from the handoff — they have no internal cross-imports that need changing:

| Source (handoff path) | Destination (GUTS path) | Notes |
|---|---|---|
| `dzh_indicators/golden_pit.py` | `guts/indicators/golden_pit.py` | Pure numpy/pandas |
| `dzh_indicators/jiu_zhuan.py` | `guts/indicators/jiu_zhuan.py` | Pure numpy/pandas |
| `dzh_indicators/band_king.py` | `guts/indicators/band_king.py` | Pure numpy/pandas |
| `strategy/bollinger.py` | `guts/signals/bollinger.py` | Change: `from .config import` → `from guts.config import` |
| `strategy/fibonacci.py` | `guts/signals/fibonacci.py` | Pure numpy/pandas |
| `strategy/elliot_wave.py` | `guts/signals/elliot_wave.py` | Pure numpy/pandas |
| `guts/signals/continuous.py` | `guts/signals/continuous.py` | Already in guts namespace |
| `guts/macro/compute.py` | `guts/macro/compute.py` | Already in guts namespace |
| `guts/macro/state.py` | `guts/macro/state.py` | Already in guts namespace |
| `guts/macro/sensitivity.py` | `guts/macro/sensitivity.py` | Already in guts namespace |
| `guts/utils/normalizer.py` | `guts/utils/normalizer.py` | Already in guts namespace |
| `guts/utils/llm_resolvers.py` | `guts/utils/llm_resolvers.py` | Already in guts namespace |

### 1.4 Verification: Phase 1

After extraction, run:
```bash
cd /opt/guts
source venv/bin/activate
python -c "
from guts.scoring.entry import score
from guts.scoring.precompute import precompute
from guts.scoring.engine import score_bar_v5
from guts.config import V10_THRESHOLDS, STOCK_UNIVERSE
print('V10_THRESHOLDS:', list(V10_THRESHOLDS.keys()))
print('Universe:', len(STOCK_UNIVERSE), 'stocks')
print('✓ All imports OK')
"
```

---

## Phase 2: Database Layer

### 2.1 SQLAlchemy Models (`guts/models.py`)

```python
"""GUTS database models."""
from sqlalchemy import Column, String, Float, Integer, DateTime, Boolean, Index, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

Base = declarative_base()

class OHLCV(Base):
    __tablename__ = "ohlcv"
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False, index=True)
    market = Column(String(5), nullable=False)
    date = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    __table_args__ = (Index("ix_ohlcv_ticker_date", "ticker", "date", unique=True),)

class MacroData(Base):
    __tablename__ = "macro"
    id = Column(Integer, primary_key=True)
    series = Column(String(50), nullable=False, index=True)
    date = Column(DateTime, nullable=False)
    value = Column(Float)
    __table_args__ = (Index("ix_macro_series_date", "series", "date", unique=True),)

class Fundamental(Base):
    __tablename__ = "fundamentals"
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False, index=True)
    market = Column(String(5))
    disc_date = Column(DateTime, nullable=False)  # disclosure date (no look-ahead)
    roe = Column(Float)
    profit_growth = Column(Float)
    revenue_growth = Column(Float)
    profit_margin = Column(Float)
    eps = Column(Float)
    __table_args__ = (Index("ix_fund_ticker_date", "ticker", "disc_date"),)

class ScoreHistory(Base):
    """Stores every scoring result for audit trail and analysis."""
    __tablename__ = "score_history"
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False, index=True)
    market = Column(String(5))
    scored_at = Column(DateTime, default=datetime.utcnow, index=True)
    bar_date = Column(DateTime, nullable=False)
    action = Column(String(10))         # BUY/WATCH/HOLD/EXIT
    composite = Column(Float)
    mode = Column(String(20))           # contrarian_entry / bull_entry
    regime = Column(String(10))         # bull / bear
    entry_score = Column(Float)
    hold_score = Column(Float)
    macro_mult = Column(Float)
    active_signals = Column(String(500))  # JSON-encoded list
    __table_args__ = (Index("ix_score_ticker_date", "ticker", "bar_date"),)

class Position(Base):
    """Tracks current portfolio positions."""
    __tablename__ = "positions"
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False, unique=True)
    market = Column(String(5))
    entry_date = Column(DateTime)
    entry_price = Column(Float)
    entry_score = Column(Float)
    current_action = Column(String(10))  # latest action
    last_scored = Column(DateTime)
    is_active = Column(Boolean, default=False)

class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True)
    ticker = Column(String(20), nullable=False, unique=True)
    market = Column(String(5))
    name = Column(String(50))
    added_at = Column(DateTime, default=datetime.utcnow)
```

### 2.2 Database Migration

```bash
cd /opt/guts
alembic init alembic
# Edit alembic.ini: sqlalchemy.url = postgresql://guts:password@localhost/guts_db
# Edit alembic/env.py: target_metadata = Base.metadata (import from guts.models)
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

### 2.3 Seed from SQLite (`scripts/seed_db.py`)

```python
"""Import data from handoff SQLite into PostgreSQL."""
import sqlite3
import pandas as pd
from sqlalchemy import create_engine
from guts.config import DATABASE_URL
from guts.models import Base

def seed():
    sqlite_path = "/opt/guts/data/gushen.db"  # copied from handoff
    src = sqlite3.connect(sqlite_path)
    dst = create_engine(DATABASE_URL)
    Base.metadata.create_all(dst)

    for table in ["ohlcv", "macro", "fundamentals", "holders", "cyq_chips"]:
        df = pd.read_sql(f"SELECT * FROM {table}", src)
        if not df.empty:
            df.to_sql(table, dst, if_exists="append", index=False)
            print(f"  ✓ {table}: {len(df)} rows")

    src.close()
    print("Seed complete.")

if __name__ == "__main__":
    seed()
```

### 2.4 Verification: Phase 2

```bash
python scripts/seed_db.py
python -c "
from sqlalchemy import create_engine, text
from guts.config import DATABASE_URL
e = create_engine(DATABASE_URL)
with e.connect() as c:
    n = c.execute(text('SELECT count(*) FROM ohlcv')).scalar()
    print(f'OHLCV rows: {n}')
    n = c.execute(text('SELECT count(*) FROM macro')).scalar()
    print(f'Macro rows: {n}')
print('✓ Database OK')
"
```

---

## Phase 3: Data Pipeline (`guts/data/`)

### 3.1 Data Fetcher (`guts/data/fetcher.py`)

Refactor from `strategy/data_fetcher.py` (the handoff's 1000-line file). Key changes:

1. **Remove hardcoded API keys** → read from `guts.config`
2. **Add PostgreSQL sink** → write fetched data to DB, not just return
3. **Keep the rate limiter** — the `TokenBucket` class at lines 49-72 of `data_fetcher.py` is critical
4. **Keep the fallback chain**: Tushare → akshare → yfinance → Tiingo → Alpha Vantage

Core methods to preserve:
```python
class DataFetcher:
    def fetch_ohlcv(self, ticker, market, start, end, freq="daily") -> pd.DataFrame
    def fetch_macro_data(self, start, end) -> dict
    def fetch_fundamental(self, ticker, market) -> dict
    def refresh_all(self)  # NEW: fetch + store for entire universe
```

The `refresh_all()` method is new — it iterates `STOCK_UNIVERSE`, fetches latest data, and upserts into PostgreSQL. This is what the scheduler calls.

### 3.2 Cache Layer (`guts/data/cache.py`)

```python
"""Database-backed cache layer replacing SQLite gushen_cache."""
import pandas as pd
from sqlalchemy import create_engine, text
from guts.config import DATABASE_URL

_engine = create_engine(DATABASE_URL)

def get_ohlcv(ticker: str, market: str = None) -> pd.DataFrame:
    """Load OHLCV from PostgreSQL. Returns DataFrame indexed by date."""
    query = "SELECT date, open, high, low, close, volume FROM ohlcv WHERE ticker = :t"
    params = {"t": ticker}
    if market:
        query += " AND market = :m"
        params["m"] = market
    query += " ORDER BY date"
    df = pd.read_sql(text(query), _engine, params=params, parse_dates=["date"])
    if df.empty:
        return None
    df = df.set_index("date").sort_index()
    return df

def get_macro_data(start: str = "2021-01-01", end: str = "2026-12-31") -> dict:
    """Load macro series from PostgreSQL. Returns dict of {series_name: pd.Series}."""
    query = "SELECT series, date, value FROM macro WHERE date BETWEEN :s AND :e ORDER BY date"
    df = pd.read_sql(text(query), _engine, params={"s": start, "e": end}, parse_dates=["date"])
    result = {}
    for name, group in df.groupby("series"):
        s = group.set_index("date")["value"].sort_index()
        result[name] = s
    return result
```

### 3.3 Verification: Phase 3

```bash
python -c "
from guts.data.cache import get_ohlcv, get_macro_data
df = get_ohlcv('AAPL', 'US')
print(f'AAPL: {len(df)} bars, {df.index[0]} to {df.index[-1]}')
macro = get_macro_data()
print(f'Macro series: {list(macro.keys())}')
print('✓ Data pipeline OK')
"
```

---

## Phase 4: API Layer (FastAPI)

### 4.1 Pydantic Schemas (`guts/api/schemas.py`)

```python
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class ScoreRequest(BaseModel):
    ticker: str
    market: str  # "A" | "HK" | "US"

class ScoreResponse(BaseModel):
    ticker: str
    market: str
    bar_date: datetime
    action: str
    composite: float
    mode: str
    regime: str
    macro_mult: float
    entry_score: float
    hold_score: float
    active_signals: List[str]
    reasoning: str

class UniverseScoreResponse(BaseModel):
    scored_at: datetime
    results: List[ScoreResponse]
    summary: dict  # by-market averages

class HealthResponse(BaseModel):
    status: str
    version: str
    last_scored: Optional[datetime]
    db_ohlcv_count: int
```

### 4.2 API Routes (`guts/api/routes.py`)

```python
from fastapi import APIRouter, HTTPException
from guts.api.schemas import ScoreRequest, ScoreResponse, UniverseScoreResponse, HealthResponse
from guts.scoring.entry import score
from guts.data.cache import get_ohlcv, get_macro_data
from guts.config import STOCK_UNIVERSE, ENGINE_VERSION
import pandas as pd
from datetime import datetime

router = APIRouter()

@router.get("/health", response_model=HealthResponse)
def health():
    """Health check endpoint."""
    ...

@router.post("/score", response_model=ScoreResponse)
def score_stock(req: ScoreRequest):
    """Score a single stock. Returns current action and signal details."""
    df = get_ohlcv(req.ticker, req.market)
    if df is None or len(df) < 50:
        raise HTTPException(404, f"Insufficient data for {req.ticker}")
    dfw = df.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    macro = get_macro_data()
    result = score(df, dfw, ticker=req.ticker, market=req.market, macro_data=macro)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return ScoreResponse(
        ticker=req.ticker, market=req.market,
        bar_date=df.index[-1], action=result["action"],
        composite=result["composite"], mode=result["mode"],
        regime=result["regime"], macro_mult=result["macro_mult"],
        entry_score=result["entry_score"], hold_score=result["hold_score"],
        active_signals=result["active"],
        reasoning=f'{result["mode"]} | composite={result["composite"]} | {result["action"]}'
    )

@router.get("/score/universe", response_model=UniverseScoreResponse)
def score_universe():
    """Score all 21 stocks in the universe. Used by daily digest."""
    results = []
    macro = get_macro_data()
    for ticker, name, market in STOCK_UNIVERSE:
        df = get_ohlcv(ticker, market)
        if df is None or len(df) < 50:
            continue
        dfw = df.resample("W-FRI").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        r = score(df, dfw, ticker=ticker, market=market, macro_data=macro)
        if "error" not in r:
            results.append(ScoreResponse(
                ticker=ticker, market=market, bar_date=df.index[-1],
                action=r["action"], composite=r["composite"], mode=r["mode"],
                regime=r["regime"], macro_mult=r["macro_mult"],
                entry_score=r["entry_score"], hold_score=r["hold_score"],
                active_signals=r["active"],
                reasoning=f'{name} | {r["mode"]} | {r["action"]}'
            ))
    # Summary by market
    summary = {}
    for mkt in ["A", "HK", "US"]:
        mkt_results = [r for r in results if r.market == mkt]
        buys = [r for r in mkt_results if r.action == "BUY"]
        summary[mkt] = {"total": len(mkt_results), "buys": len(buys),
                        "buy_tickers": [r.ticker for r in buys]}
    return UniverseScoreResponse(scored_at=datetime.utcnow(), results=results, summary=summary)

@router.get("/positions")
def get_positions():
    """Get current portfolio positions (stocks in BUY/HOLD state)."""
    ...

@router.get("/history/{ticker}")
def get_score_history(ticker: str, days: int = 30):
    """Get scoring history for a ticker."""
    ...
```

### 4.3 FastAPI App (`guts/api/app.py`)

```python
from fastapi import FastAPI
from guts.api.routes import router

def create_app():
    app = FastAPI(
        title="GUTS — Gushen Unified Trading System",
        version="1.0.0",
        description="Regime-adaptive multi-market stock scoring engine"
    )
    app.include_router(router, prefix="/api/v1")
    return app

app = create_app()
```

### 4.4 Run API

```bash
# Development
uvicorn guts.api.app:app --host 0.0.0.0 --port 8000 --reload

# Production (behind nginx)
uvicorn guts.api.app:app --host 127.0.0.1 --port 8000 --workers 2
```

### 4.5 Verification: Phase 4

```bash
# Start API
uvicorn guts.api.app:app --port 8000 &

# Test endpoints
curl http://localhost:8000/api/v1/health
curl -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d '{"ticker":"AAPL","market":"US"}'
curl http://localhost:8000/api/v1/score/universe
```

---

## Phase 5: Scheduler & Automation

### 5.1 Job Definitions (`guts/scheduler/jobs.py`)

```python
"""Scheduled jobs for GUTS."""
from apscheduler.schedulers.background import BackgroundScheduler
from guts.config import SCORING_SCHEDULE_CRON, DATA_REFRESH_CRON, STOCK_UNIVERSE
from guts.scoring.entry import score
from guts.data.cache import get_ohlcv, get_macro_data
from guts.data.fetcher import DataFetcher
from guts.notify.formatter import format_daily_digest
from guts.notify.email import send_email
from guts.models import ScoreHistory, Position
from loguru import logger
import pandas as pd
from datetime import datetime

scheduler = BackgroundScheduler()

def job_refresh_data():
    """Fetch latest OHLCV + macro data for all stocks. Runs weekday evenings."""
    logger.info("Starting data refresh...")
    fetcher = DataFetcher()
    fetcher.refresh_all()
    logger.info("Data refresh complete.")

def job_daily_scoring():
    """Score all 21 stocks, update positions, send notifications. Runs daily 8:30 HKT."""
    logger.info("Starting daily scoring...")
    macro = get_macro_data()
    results = []

    for ticker, name, market in STOCK_UNIVERSE:
        df = get_ohlcv(ticker, market)
        if df is None or len(df) < 50:
            logger.warning(f"  {ticker}: insufficient data, skipping")
            continue
        dfw = df.resample("W-FRI").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
        r = score(df, dfw, ticker=ticker, market=market, macro_data=macro)
        if "error" not in r:
            results.append({"ticker": ticker, "name": name, "market": market, **r})
            logger.info(f"  {ticker} ({name}): {r['action']} | composite={r['composite']}")
            # TODO: Store to ScoreHistory table
            # TODO: Update Position table

    # Format and send notification
    digest = format_daily_digest(results)
    send_email(subject=f"GUTS Daily Digest — {datetime.now().strftime('%Y-%m-%d')}", body=digest)
    logger.info(f"Daily scoring complete. {len(results)} stocks scored.")

def start_scheduler():
    """Register all jobs and start the scheduler."""
    scheduler.add_job(job_refresh_data, "cron", **DATA_REFRESH_CRON, id="refresh_data")
    scheduler.add_job(job_daily_scoring, "cron", **SCORING_SCHEDULE_CRON, id="daily_scoring")
    scheduler.start()
    logger.info("Scheduler started.")
```

### 5.2 Notification Formatter (`guts/notify/formatter.py`)

```python
"""Format scoring results into human-readable messages."""
from datetime import datetime

def format_daily_digest(results: list) -> str:
    """Format list of score dicts into a daily digest email body."""
    lines = [f"🐉 GUTS Daily Digest — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]

    # BUY signals first
    buys = [r for r in results if r["action"] == "BUY"]
    if buys:
        lines.append("🟢 BUY SIGNALS:")
        for r in buys:
            lines.append(f"  {r['ticker']} ({r['name']}) | {r['regime']} | score={r['composite']:.1f} | macro={r['macro_mult']:.2f}")
    else:
        lines.append("No BUY signals today.")

    # EXIT signals
    exits = [r for r in results if r["action"] == "EXIT"]
    if exits:
        lines.append("\n🔴 EXIT SIGNALS:")
        for r in exits:
            lines.append(f"  {r['ticker']} ({r['name']}) | reason: {', '.join(r['active'][:3])}")

    # Summary by market
    lines.append("\n📊 Market Summary:")
    for mkt in ["A", "HK", "US"]:
        mkt_r = [r for r in results if r["market"] == mkt]
        buys_n = sum(1 for r in mkt_r if r["action"] == "BUY")
        bulls = sum(1 for r in mkt_r if r["regime"] == "bull")
        lines.append(f"  {mkt}: {len(mkt_r)} stocks | {buys_n} BUY | {bulls} bull regime")

    return "\n".join(lines)
```

### 5.3 Email Sender (`guts/notify/email.py`)

```python
"""SMTP email notification."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from guts.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL
from loguru import logger

def send_email(subject: str, body: str, to: str = None):
    """Send email via SMTP (QQ/foxmail SSL)."""
    to = to or NOTIFY_EMAIL
    if not all([SMTP_USER, SMTP_PASS, to]):
        logger.warning("Email not configured, skipping notification")
        return

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        logger.info(f"Email sent to {to}: {subject}")
    except Exception as e:
        logger.error(f"Email failed: {e}")
```

---

## Phase 6: Deployment

### 6.1 Docker (`deploy/Dockerfile`)

```dockerfile
FROM python:3.10-slim
WORKDIR /opt/guts

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY guts/ guts/
COPY scripts/ scripts/
COPY alembic/ alembic/
COPY alembic.ini .

ENV PYTHONPATH=/opt/guts
CMD ["uvicorn", "guts.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 6.2 Docker Compose (`deploy/docker-compose.yml`)

```yaml
version: "3.8"
services:
  db:
    image: postgres:15
    environment:
      POSTGRES_USER: guts
      POSTGRES_PASSWORD: guts_secure_password_change_me
      POSTGRES_DB: guts_db
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7
    ports:
      - "6379:6379"

  api:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    ports:
      - "8000:8000"
    env_file: /etc/guts/.env
    depends_on:
      - db
      - redis
    environment:
      DATABASE_URL: postgresql://guts:guts_secure_password_change_me@db:5432/guts_db

  scheduler:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    command: python -c "from guts.scheduler.jobs import start_scheduler; start_scheduler(); import time; time.sleep(999999999)"
    env_file: /etc/guts/.env
    depends_on:
      - db
    environment:
      DATABASE_URL: postgresql://guts:guts_secure_password_change_me@db:5432/guts_db

volumes:
  pgdata:
```

### 6.3 Systemd Units (Alternative to Docker)

`deploy/guts-api.service`:
```ini
[Unit]
Description=GUTS API Server
After=network.target postgresql.service

[Service]
User=guts
WorkingDirectory=/opt/guts
Environment=PYTHONPATH=/opt/guts
EnvironmentFile=/etc/guts/.env
ExecStart=/opt/guts/venv/bin/uvicorn guts.api.app:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`deploy/guts-scheduler.service`:
```ini
[Unit]
Description=GUTS Scheduler
After=network.target postgresql.service

[Service]
User=guts
WorkingDirectory=/opt/guts
Environment=PYTHONPATH=/opt/guts
EnvironmentFile=/etc/guts/.env
ExecStart=/opt/guts/venv/bin/python -c "from guts.scheduler.jobs import start_scheduler; start_scheduler(); import time; time.sleep(999999999)"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 6.4 Nginx Reverse Proxy (`deploy/nginx.conf`)

```nginx
server {
    listen 80;
    server_name guts.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name guts.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/guts.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/guts.yourdomain.com/privkey.pem;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 6.5 Deploy Commands

```bash
# Option A: Docker
cd /opt/guts
docker compose -f deploy/docker-compose.yml up -d
docker compose exec api python scripts/seed_db.py

# Option B: Systemd
sudo cp deploy/guts-api.service /etc/systemd/system/
sudo cp deploy/guts-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now guts-api guts-scheduler
```

---

## Phase 7: Testing & Monitoring

### 7.1 Test Suite

```bash
# Run all tests
cd /opt/guts
pytest tests/ -v

# Key test scenarios:
# test_precompute.py — verify precompute() produces correct keys
# test_engine.py — verify score_bar_v5() matches handoff results for 3 stocks
# test_fetcher.py — verify data fetch + DB storage
# test_api.py — verify API endpoints return correct schemas
```

### 7.2 Regression Test (`tests/test_engine.py`)

The critical test: v10 on GUTS must produce identical results to v10 on the handoff. Use these known-good reference values:

```python
# Known OOS test Sharpe values (from v10_oos_validation.json)
REFERENCE_SHARPE = {
    "AAPL": 1.751,
    "0700.HK": 0.000,  # no trades in test period
    "600519.SH": 0.463,
}
```

Load the same data, run the same backtest, assert Sharpe matches within 0.01.

### 7.3 Monitoring Checklist

| What | How | Alert |
|------|-----|-------|
| API health | `GET /api/v1/health` every 5 min | If non-200 |
| Scheduler alive | Check systemd/docker status | If process dies |
| Data freshness | Query `MAX(date) FROM ohlcv` | If >2 business days stale |
| Scoring runs | Check `score_history` table | If no new rows in 25 hours |
| API latency | Uvicorn access log p99 | If >10s for `/score/universe` |
| Disk usage | `df -h` | If >80% |
| Error rate | Loguru error count in `/var/log/guts/` | If >5 errors/day |

### 7.4 Log Rotation (`deploy/logrotate.conf`)

```
/var/log/guts/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
}
```

---

## Phase 8: Execution Order Summary

The execution AI should follow this exact order:

| Step | Phase | What | Verify |
|------|-------|------|--------|
| 1 | 0 | Server setup, Python env, PostgreSQL | `python --version`, `psql -c '\l'` |
| 2 | 1 | Create directory structure | `ls -R /opt/guts/guts/` |
| 3 | 1 | Extract precompute.py from scoring.py:334-527 | Import test |
| 4 | 1 | Extract engine.py from scoring.py:2533-2987 | Import test |
| 5 | 1 | Copy all signal/indicator modules | Import test |
| 6 | 1 | Create config.py with constants | Import test |
| 7 | 1 | Create entry.py | `from guts.scoring.entry import score` |
| 8 | 2 | Create models.py, run migrations | `alembic upgrade head` |
| 9 | 2 | Seed DB from SQLite | `SELECT count(*) FROM ohlcv` |
| 10 | 3 | Create fetcher.py and cache.py | `get_ohlcv('AAPL', 'US')` returns data |
| 11 | 3 | Test scoring end-to-end | `score(df, dfw, 'AAPL', 'US')` returns BUY/HOLD/EXIT |
| 12 | 4 | Create FastAPI app and routes | `curl /api/v1/health` |
| 13 | 4 | Test all API endpoints | `curl /api/v1/score/universe` |
| 14 | 5 | Create scheduler jobs | Scheduler starts without errors |
| 15 | 5 | Create notification layer | Test email sends |
| 16 | 6 | Deploy with Docker or systemd | Services running |
| 17 | 6 | Configure nginx + SSL | HTTPS works |
| 18 | 7 | Run regression tests | Sharpe matches reference |
| 19 | 7 | Set up monitoring | Health check passes |
| 20 | 7 | Run for 3 days, verify daily digest arrives | Email received |

---

## Appendix A: Complete Import Dependency Graph

```
guts/scoring/engine.py
  ← guts.config (V10_THRESHOLDS, BEAR_TREND_DISCOUNT, MA20_PENALTY_*)
  ← pandas, numpy (stdlib)

guts/scoring/precompute.py
  ← guts.indicators.golden_pit (golden_pit_v2)
  ← guts.indicators.jiu_zhuan (compute_jiu_zhuan)
  ← guts.indicators.band_king (compute_band_king)
  ← guts.signals.bollinger (compute_weekly_bb, bb_weekly_sell_signal, bb_weekly_buy_signal)
  ← guts.signals.fibonacci (score_fibonacci)
  ← guts.signals.elliot_wave (triple_confirm)
  ← pandas, numpy

guts/scoring/entry.py
  ← guts.scoring.precompute (precompute)
  ← guts.scoring.engine (score_bar_v5)

guts/data/cache.py
  ← guts.config (DATABASE_URL)
  ← sqlalchemy, pandas

guts/data/fetcher.py
  ← guts.config (API keys)
  ← akshare, tushare, yfinance, httpx

guts/api/routes.py
  ← guts.scoring.entry (score)
  ← guts.data.cache (get_ohlcv, get_macro_data)
  ← guts.config (STOCK_UNIVERSE)

guts/scheduler/jobs.py
  ← guts.scoring.entry (score)
  ← guts.data.cache (get_ohlcv, get_macro_data)
  ← guts.data.fetcher (DataFetcher)
  ← guts.notify.* (email, formatter)
```

## Appendix B: Macro Series Reference

These 13 series are in the `macro` PostgreSQL table and used by Stage 5 (macro_mult):

| Series | Source | Used For | In macro_mult? |
|--------|--------|----------|----------------|
| `vix` | FRED VIXCLS | Fear gauge (all markets) | Yes: >30 → -1.5, >25 → -0.5, <15 → +0.5 |
| `us_spread_10y2y` | FRED T10Y2Y | Recession indicator (US/HK) | Yes: <0 → -1.0, <0.3 → -0.3, >1.5 → +0.3 |
| `china_pmi` | akshare | Manufacturing cycle (A) | Yes: <49 → -1.0, <50 → -0.3, >51 → +0.5 |
| `yield10y` | akshare | Bond market | No (informational) |
| `yield5y` | akshare | Bond market | No (informational) |
| `china_cpi` | akshare | Inflation | No (removed from v10, was net drag) |
| `china_lpr1y` | akshare | Monetary policy | No |
| `china_m2` | akshare | Liquidity | No |
| `china_m2_yoy` | akshare | Liquidity growth | No |
| `us_cpi_yoy` | akshare | US inflation | No |
| `us_unemployment` | akshare | US labor | No |
| `usdcny` | akshare | FX | No |
| `northbound_flow` | akshare | A-stock flow | No |

Only VIX, us_spread_10y2y, and china_pmi are used in the v10 macro_mult calculation. The others are fetched for future use or informational display.

## Appendix C: Files to Copy from Handoff

```bash
# From ~/Desktop/gushen_handoff/ to /opt/guts/
# These are the authoritative source files:

# Core engine (EXTRACT, don't copy verbatim — fix imports per Phase 1.2)
strategy/scoring.py         → guts/scoring/precompute.py + engine.py + entry.py

# Direct copies (fix imports as noted in Phase 1.3)
strategy/bollinger.py       → guts/signals/bollinger.py
strategy/fibonacci.py       → guts/signals/fibonacci.py
strategy/elliot_wave.py     → guts/signals/elliot_wave.py
dzh_indicators/golden_pit.py → guts/indicators/golden_pit.py
dzh_indicators/jiu_zhuan.py  → guts/indicators/jiu_zhuan.py
dzh_indicators/band_king.py  → guts/indicators/band_king.py
guts/signals/continuous.py   → guts/signals/continuous.py
guts/macro/compute.py        → guts/macro/compute.py
guts/macro/state.py          → guts/macro/state.py
guts/macro/sensitivity.py    → guts/macro/sensitivity.py
guts/utils/normalizer.py     → guts/utils/normalizer.py
guts/utils/llm_resolvers.py  → guts/utils/llm_resolvers.py

# Data (seed)
data/gushen.db              → data/gushen.db

# Reference (refactor, don't copy verbatim)
strategy/data_fetcher.py    → guts/data/fetcher.py (refactor per Phase 3.1)
strategy/gushen_cache.py    → guts/data/cache.py (rewrite per Phase 3.2)
strategy/tune.py            → guts/portfolio/backtest.py (refactor per Phase 1.1)
```
