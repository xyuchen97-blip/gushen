# guts/macro/sensitivity.py
"""
Stock Style × CapSize Sensitivity to Macro Regime.

Growth stocks amplify in risk-on, dampen in risk-off.
Value/defensive stocks are more stable — mild amplification in risk-off.
CapSize modulates the intensity: small caps are more sensitive.

v9.8: Dual-dimension classification (StockStyle × CapSize)
  - L1 (capital amplifier): 0.7 ~ 1.3
  - L2 (fundamental gate):  0.6 ~ 1.0, fund_bonus 0 ~ +5
  - L3 (macro filter):      0.5 ~ 1.0

Multiplier range: 0.85 to 1.15 (±15% maximum swing) — legacy, replaced by L3 table
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, Tuple
from .state import MacroRegime


class StockStyle(Enum):
    GROWTH = "growth"        # NVDA, 宁德, 腾讯 — high macro sensitivity
    VALUE = "value"          # JPM, 招行 — moderate sensitivity
    CYCLICAL = "cyclical"    # 比亚迪, 小米 — depends on economic cycle
    DEFENSIVE = "defensive"  # 茅台, 五粮液 — low sensitivity
    BLEND = "blend"          # AAPL — neutral


class CapSize(Enum):
    """Market-cap classification — modulates layer sensitivity."""
    LARGE = "large"   # A:>2000亿RMB, HK:>2000亿HKD, US:>1000亿USD
    MID = "mid"       # A:500-2000亿, HK:500-2000亿HKD, US:200-1000亿USD
    SMALL = "small"   # A:<500亿, HK:<500亿HKD, US:<200亿USD


@dataclass
class StockClassification:
    """Complete stock classification: style × cap_size."""
    style: StockStyle
    cap_size: CapSize
    
    @property
    def key(self) -> Tuple[StockStyle, CapSize]:
        return (self.style, self.cap_size)


class MacroSensitivity:
    """Per-stock sensitivity to macro regime (legacy — uses style only)."""
    
    def __init__(self, style: StockStyle, cap_size: CapSize = CapSize.LARGE):
        self.style = style
        self.cap_size = cap_size
        self.classification = StockClassification(style, cap_size)
    
    def compute_multiplier(self, regime: MacroRegime) -> float:
        """Get the L3 macro multiplier for this stock in the given regime."""
        return L3_MACRO_TABLE.get(self.classification.key, {}).get(regime, 1.0)
    
    def get_capital_signal_weight(self, signal_name: str) -> float:
        """Get L1 capital signal weight multiplier for this stock's style×cap_size."""
        weights = L1_CAPITAL_SIGNAL_WEIGHTS.get(self.classification.key, {})
        return weights.get(signal_name, 1.0)
    
    def get_fund_gate(self) -> float:
        """Get L2 fundamental gate base for this stock's style×cap_size."""
        return L2_FUND_TABLE.get(self.classification.key, {}).get("gate", 0.80)
    
    def get_fund_bonus_max(self) -> float:
        """Get L2 fund_bonus max for this stock's style×cap_size."""
        return L2_FUND_TABLE.get(self.classification.key, {}).get("bonus_max", 3.0)


# ═══ Legacy Multiplier Table (style-only, kept for backward compat) ═══
# Replaced by L3_MACRO_TABLE in v9.8.

MULTIPLIER_TABLE = {
    StockStyle.GROWTH: {
        MacroRegime.RISK_ON: 1.12,
        MacroRegime.NEUTRAL: 1.00,
        MacroRegime.RISK_OFF: 0.88,
    },
    StockStyle.VALUE: {
        MacroRegime.RISK_ON: 1.05,
        MacroRegime.NEUTRAL: 1.00,
        MacroRegime.RISK_OFF: 0.95,
    },
    StockStyle.CYCLICAL: {
        MacroRegime.RISK_ON: 1.10,
        MacroRegime.NEUTRAL: 1.00,
        MacroRegime.RISK_OFF: 0.92,
    },
    StockStyle.DEFENSIVE: {
        MacroRegime.RISK_ON: 0.90,
        MacroRegime.NEUTRAL: 1.00,
        MacroRegime.RISK_OFF: 1.10,
    },
    StockStyle.BLEND: {
        MacroRegime.RISK_ON: 1.05,
        MacroRegime.NEUTRAL: 1.00,
        MacroRegime.RISK_OFF: 0.95,
    },
}


# ═══ v9.8: L3 Macro Filter Table (style × cap_size → regime → multiplier) ═══
# Key: (StockStyle, CapSize) → {MacroRegime: multiplier}
# macro_mult is always ≤ 1.0 — macro only suppresses, never amplifies.
# Base = 0.90 (neutral), risk_off suppresses, risk_on = up to 1.0.

L3_MACRO_TABLE = {
    # Growth: most sensitive to macro — small growth gets crushed in risk_off
    (StockStyle.GROWTH, CapSize.LARGE): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.90, MacroRegime.RISK_OFF: 0.70,
    },
    (StockStyle.GROWTH, CapSize.MID): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.85, MacroRegime.RISK_OFF: 0.60,
    },
    (StockStyle.GROWTH, CapSize.SMALL): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.80, MacroRegime.RISK_OFF: 0.50,
    },
    # Value: moderately counter-cyclical — strictest gate in risk_off but still positive
    (StockStyle.VALUE, CapSize.LARGE): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.95, MacroRegime.RISK_OFF: 0.85,
    },
    (StockStyle.VALUE, CapSize.MID): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.90, MacroRegime.RISK_OFF: 0.80,
    },
    (StockStyle.VALUE, CapSize.SMALL): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.85, MacroRegime.RISK_OFF: 0.75,
    },
    # Cyclical: pro-cyclical — suffers in risk_off
    (StockStyle.CYCLICAL, CapSize.LARGE): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.90, MacroRegime.RISK_OFF: 0.75,
    },
    (StockStyle.CYCLICAL, CapSize.MID): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.85, MacroRegime.RISK_OFF: 0.65,
    },
    (StockStyle.CYCLICAL, CapSize.SMALL): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.80, MacroRegime.RISK_OFF: 0.55,
    },
    # Defensive: near-immune to macro — safe haven in risk_off
    (StockStyle.DEFENSIVE, CapSize.LARGE): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 1.00, MacroRegime.RISK_OFF: 0.95,
    },
    (StockStyle.DEFENSIVE, CapSize.MID): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 1.00, MacroRegime.RISK_OFF: 0.90,
    },
    (StockStyle.DEFENSIVE, CapSize.SMALL): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.95, MacroRegime.RISK_OFF: 0.85,
    },
    # Blend: neutral — slight macro sensitivity
    (StockStyle.BLEND, CapSize.LARGE): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.95, MacroRegime.RISK_OFF: 0.80,
    },
    (StockStyle.BLEND, CapSize.MID): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.90, MacroRegime.RISK_OFF: 0.75,
    },
    (StockStyle.BLEND, CapSize.SMALL): {
        MacroRegime.RISK_ON: 1.00, MacroRegime.NEUTRAL: 0.85, MacroRegime.RISK_OFF: 0.70,
    },
}


# ═══ v9.8: L1 Capital Signal Weight Table (style × cap_size → signal → weight) ═══
# These weights multiply each capital signal's contribution to capital_mult.
# Base contribution per signal stays the same; the weight scales it by style×cap_size.
# Default weight = 1.0 (no scaling). Growth+mid gets strongest capital signals.

L1_CAPITAL_SIGNAL_WEIGHTS = {
    # Growth: capital flow matters most — especially for mid-cap (主力拉升中小盘成长股)
    (StockStyle.GROWTH, CapSize.LARGE): {"volume_anomaly": 1.2, "northbound_inflow": 1.1, "mff_strong": 1.2},
    (StockStyle.GROWTH, CapSize.MID):   {"volume_anomaly": 1.3, "northbound_inflow": 1.1, "mff_strong": 1.3},
    (StockStyle.GROWTH, CapSize.SMALL): {"volume_anomaly": 1.3, "northbound_inflow": 1.0, "mff_strong": 1.2},
    # Value: capital confirms but not as critical
    (StockStyle.VALUE, CapSize.LARGE):  {"volume_anomaly": 1.1, "northbound_inflow": 1.1, "mff_strong": 1.0},
    (StockStyle.VALUE, CapSize.MID):    {"volume_anomaly": 1.2, "northbound_inflow": 1.0, "mff_strong": 1.0},
    (StockStyle.VALUE, CapSize.SMALL):  {"volume_anomaly": 1.2, "northbound_inflow": 1.0, "mff_strong": 1.0},
    # Cyclical: volume is important for cycle confirmation
    (StockStyle.CYCLICAL, CapSize.LARGE): {"volume_anomaly": 1.2, "northbound_inflow": 1.1, "mff_strong": 1.0},
    (StockStyle.CYCLICAL, CapSize.MID):   {"volume_anomaly": 1.2, "northbound_inflow": 1.0, "mff_strong": 1.0},
    (StockStyle.CYCLICAL, CapSize.SMALL): {"volume_anomaly": 1.3, "northbound_inflow": 1.0, "mff_strong": 1.0},
    # Defensive: capital signals nearly useless — 茅台不需要放量确认
    (StockStyle.DEFENSIVE, CapSize.LARGE): {"volume_anomaly": 1.0, "northbound_inflow": 1.0, "mff_strong": 1.0},
    (StockStyle.DEFENSIVE, CapSize.MID):   {"volume_anomaly": 1.1, "northbound_inflow": 1.0, "mff_strong": 1.0},
    (StockStyle.DEFENSIVE, CapSize.SMALL): {"volume_anomaly": 1.1, "northbound_inflow": 1.0, "mff_strong": 1.0},
    # Blend: moderate
    (StockStyle.BLEND, CapSize.LARGE): {"volume_anomaly": 1.1, "northbound_inflow": 1.0, "mff_strong": 1.0},
    (StockStyle.BLEND, CapSize.MID):   {"volume_anomaly": 1.2, "northbound_inflow": 1.0, "mff_strong": 1.0},
    (StockStyle.BLEND, CapSize.SMALL): {"volume_anomaly": 1.2, "northbound_inflow": 1.0, "mff_strong": 1.0},
}


# ═══ v9.8: L2 Fundamental Gate Table (style × cap_size → gate, bonus_max) ═══
# gate: base multiplier (0.6~1.0), no fundamentals info → composite × gate
# bonus_max: max fund_bonus (0~+5), small cap good fundamentals = more meaningful
# Value gets strictest gate — 价值股本质是"基本面好但股价没反映"

L2_FUND_TABLE = {
    # Growth: loose gate — growth stocks buy expectations, ROE can be low
    (StockStyle.GROWTH, CapSize.LARGE): {"gate": 0.85, "bonus_max": 3.0},
    (StockStyle.GROWTH, CapSize.MID):   {"gate": 0.80, "bonus_max": 4.0},
    (StockStyle.GROWTH, CapSize.SMALL): {"gate": 0.75, "bonus_max": 5.0},
    # Value: strictest gate — no good fundamentals = don't buy
    (StockStyle.VALUE, CapSize.LARGE):  {"gate": 0.70, "bonus_max": 5.0},
    (StockStyle.VALUE, CapSize.MID):    {"gate": 0.65, "bonus_max": 5.0},
    (StockStyle.VALUE, CapSize.SMALL):  {"gate": 0.60, "bonus_max": 5.0},
    # Cyclical: moderate — cycle matters more than current fundamentals
    (StockStyle.CYCLICAL, CapSize.LARGE): {"gate": 0.80, "bonus_max": 3.0},
    (StockStyle.CYCLICAL, CapSize.MID):   {"gate": 0.75, "bonus_max": 4.0},
    (StockStyle.CYCLICAL, CapSize.SMALL): {"gate": 0.70, "bonus_max": 5.0},
    # Defensive: strict — defensive stocks need solid fundamentals
    (StockStyle.DEFENSIVE, CapSize.LARGE): {"gate": 0.75, "bonus_max": 4.0},
    (StockStyle.DEFENSIVE, CapSize.MID):   {"gate": 0.70, "bonus_max": 5.0},
    (StockStyle.DEFENSIVE, CapSize.SMALL): {"gate": 0.65, "bonus_max": 5.0},
    # Blend: moderate
    (StockStyle.BLEND, CapSize.LARGE): {"gate": 0.80, "bonus_max": 3.0},
    (StockStyle.BLEND, CapSize.MID):   {"gate": 0.75, "bonus_max": 4.0},
    (StockStyle.BLEND, CapSize.SMALL): {"gate": 0.70, "bonus_max": 5.0},
}


# ═══ Stock Classification Registry (style × cap_size) ═══
# Source of truth for stock classifications.
# v9.8: expanded from style-only to (style, cap_size) dual-dimension tuple.
# Uses common stock aliases and tickers.

STOCK_CLASSIFICATIONS = {
    # US
    'NVDA': (StockStyle.GROWTH, CapSize.LARGE),
    'MSFT': (StockStyle.GROWTH, CapSize.LARGE),
    'GOOGL': (StockStyle.GROWTH, CapSize.LARGE),
    'AMZN': (StockStyle.GROWTH, CapSize.LARGE),
    'META': (StockStyle.GROWTH, CapSize.LARGE),
    'AAPL': (StockStyle.BLEND, CapSize.LARGE),
    'JPM': (StockStyle.VALUE, CapSize.LARGE),
    # HK
    '腾讯': (StockStyle.GROWTH, CapSize.LARGE), '腾讯控股': (StockStyle.GROWTH, CapSize.LARGE),
    '阿里': (StockStyle.GROWTH, CapSize.LARGE), '阿里巴巴': (StockStyle.GROWTH, CapSize.LARGE),
    '美团': (StockStyle.GROWTH, CapSize.MID),
    '小米': (StockStyle.CYCLICAL, CapSize.LARGE), '小米集团': (StockStyle.CYCLICAL, CapSize.LARGE),
    '比亚迪': (StockStyle.CYCLICAL, CapSize.LARGE),
    '港交所': (StockStyle.VALUE, CapSize.LARGE),
    '0700.HK': (StockStyle.GROWTH, CapSize.LARGE), '9988.HK': (StockStyle.GROWTH, CapSize.LARGE),
    '3690.HK': (StockStyle.GROWTH, CapSize.MID), '1810.HK': (StockStyle.CYCLICAL, CapSize.LARGE),
    '1211.HK': (StockStyle.CYCLICAL, CapSize.LARGE), '0388.HK': (StockStyle.VALUE, CapSize.LARGE),
    # A
    '宁德': (StockStyle.GROWTH, CapSize.LARGE), '宁德时代': (StockStyle.GROWTH, CapSize.LARGE),
    '比亚迪': (StockStyle.CYCLICAL, CapSize.LARGE),  # A-share BYD
    '讯飞': (StockStyle.GROWTH, CapSize.MID), '科大讯飞': (StockStyle.GROWTH, CapSize.MID),
    '爱尔': (StockStyle.GROWTH, CapSize.MID), '爱尔眼科': (StockStyle.GROWTH, CapSize.MID),
    '茅台': (StockStyle.DEFENSIVE, CapSize.LARGE), '贵州茅台': (StockStyle.DEFENSIVE, CapSize.LARGE),
    '五粮液': (StockStyle.DEFENSIVE, CapSize.LARGE),
    '平安': (StockStyle.VALUE, CapSize.LARGE), '中国平安': (StockStyle.VALUE, CapSize.LARGE),
    '招行': (StockStyle.VALUE, CapSize.LARGE), '招商银行': (StockStyle.VALUE, CapSize.LARGE),
    '600519.SH': (StockStyle.DEFENSIVE, CapSize.LARGE), '000858.SZ': (StockStyle.DEFENSIVE, CapSize.LARGE),
    '300750.SZ': (StockStyle.GROWTH, CapSize.LARGE), '002594.SZ': (StockStyle.CYCLICAL, CapSize.LARGE),
    '601318.SH': (StockStyle.VALUE, CapSize.LARGE), '600036.SH': (StockStyle.VALUE, CapSize.LARGE),
    '002230.SZ': (StockStyle.GROWTH, CapSize.MID), '300015.SZ': (StockStyle.GROWTH, CapSize.MID),
}

# Legacy compatibility: STOCK_STYLES maps ticker → StockStyle (style-only)
STOCK_STYLES = {ticker: cls[0] for ticker, cls in STOCK_CLASSIFICATIONS.items()}


def get_sensitivity(ticker: str) -> MacroSensitivity:
    """Get the macro sensitivity for a stock by ticker or name (dual-dimension)."""
    cls = STOCK_CLASSIFICATIONS.get(ticker)
    if cls is None:
        # Try matching by common name patterns
        ticker_upper = ticker.upper().replace('.HK', '').replace('.SH', '').replace('.SZ', '')
        cls = STOCK_CLASSIFICATIONS.get(ticker_upper)
    if cls is None:
        cls = (StockStyle.BLEND, CapSize.LARGE)  # default: neutral, large cap
    return MacroSensitivity(cls[0], cls[1])
