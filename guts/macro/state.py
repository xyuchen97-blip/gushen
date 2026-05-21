# guts/macro/state.py
"""Macro state data model. Product-agnostic."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class MacroRegime(Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class Region(Enum):
    US = "us"
    CN = "cn"
    HK = "hk"


@dataclass
class MacroState:
    """Output of centralized macro layer. Computed once per region per bar."""
    region: Region
    regime: MacroRegime
    score: float                       # -1.0 (extreme risk-off) to +1.0 (extreme risk-on)
    sub_factors: Dict[str, float]      # individual factor scores, all -1 to +1
    coverage: float                    # 0-1, fraction of factors with valid data
    timestamp: str                     # bar date (ISO format)

    def is_valid(self) -> bool:
        return self.coverage >= 0.5

    def to_dict(self) -> dict:
        return {
            'region': self.region.value,
            'regime': self.regime.value,
            'score': round(self.score, 4),
            'sub_factors': {k: round(v, 4) for k, v in self.sub_factors.items()},
            'coverage': round(self.coverage, 3),
            'timestamp': self.timestamp,
        }
