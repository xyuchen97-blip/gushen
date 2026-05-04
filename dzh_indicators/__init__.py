"""
DZH (DaZhiHui) Technical Indicator Suite — Python Quant Implementation
=======================================================================

Three classic DZH indicators, fully ported to Python:

- **Golden Pit 2.0** (`golden_pit`)   — Oversold reversal detection (no future functions)
- **Nine Turns** (`jiu_zhuan`)        — Tom DeMark TD Sequential indicator
- **Band King** (`band_king`)         — Multi-period ZIG resonance indicator

All indicators accept pandas DataFrames (OHLCV format).
Compatible with akshare / tushare / yfinance data sources.

Usage:
    import akshare as ak
    from dzh_indicators import golden_pit, jiu_zhuan, band_king

    df = ak.stock_zh_a_hist(symbol="000001", period="daily", adjust="qfq")
    df = golden_pit.compute(df)   # adds Golden Pit signal columns
    df = jiu_zhuan.compute(df)    # adds Nine Turns sequence columns
    df = band_king.compute_no_future(df)  # adds Band King signal columns
"""

from . import golden_pit
from . import jiu_zhuan
from . import band_king

__version__ = "1.0.0"
