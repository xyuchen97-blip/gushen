# guts/macro/compute.py
"""
Continuous macro scoring functions. Each returns Optional[float] in [-1.0, +1.0].
None = data not available. These replace the old binary macro thresholds in scoring.py.

Product-agnostic — Gushen, BitBrave, and GUTS cloud all use the same scorers.
"""

import numpy as np
import pandas as pd
from typing import Optional, Dict, Callable
from .state import MacroState, MacroRegime, Region


def safe_last(series) -> Optional[float]:
    """NaN-safe extraction of last value from a Series, scalar, or dict."""
    if series is None:
        return None
    if isinstance(series, dict):
        # From data_fetcher macro_data dict
        return None
    if isinstance(series, (int, float)):
        return float(series) if pd.notna(series) else None
    if isinstance(series, pd.Series):
        if series.empty:
            return None
        val = series.dropna()
        return float(val.iloc[-1]) if len(val) > 0 else None
    return None


def safe_last_n(series, n: int = 20) -> Optional[float]:
    """Get the last value from a Series, checking at least n valid values exist."""
    if series is None:
        return None
    if isinstance(series, pd.Series):
        valid = series.dropna()
        return float(valid.iloc[-1]) if len(valid) >= n else None
    return safe_last(series)


# ═══ Individual Factor Scorers (-1 to +1) ═══

def score_vix(vix: Optional[float]) -> Optional[float]:
    """VIX level → -1 (panic) to +1 (calm). Continuous piecewise linear."""
    if vix is None: return None
    if vix < 12: return 1.0
    elif vix < 18: return 0.5 + 0.5 * (18 - vix) / 6
    elif vix < 25: return -0.5 + 1.0 * (25 - vix) / 7
    elif vix < 35: return -0.8 + 0.3 * (35 - vix) / 10
    else: return -1.0


def score_qvix(qvix: Optional[float]) -> Optional[float]:
    """China QVIX → -1 (panic) to +1 (calm)."""
    if qvix is None: return None
    if qvix < 15: return 1.0
    elif qvix < 22: return 0.5 + 0.5 * (22 - qvix) / 7
    elif qvix < 30: return -0.5 + 1.0 * (30 - qvix) / 8
    else: return -1.0


def score_yield_curve(spread: Optional[float]) -> Optional[float]:
    """Yield curve (10y-2y %) → -1 (inverted) to +1 (steep)."""
    if spread is None: return None
    return float(np.clip(spread / 1.0, -1.0, 1.0))


def score_fed_policy(rate: Optional[float], rate_3m_ago: Optional[float]) -> Optional[float]:
    """Fed rate delta → -1 (hiking) to +1 (cutting)."""
    if rate is None or rate_3m_ago is None: return None
    delta = rate - rate_3m_ago
    if delta > 0.25: return -1.0
    elif delta > 0: return -0.5
    elif delta == 0: return 0.2
    elif delta > -0.25: return 0.5
    else: return 1.0


def score_cpi_direction(cpi: Optional[float], cpi_prev: Optional[float]) -> Optional[float]:
    """CPI trend → -1 (accelerating) to +1 (disinflation)."""
    if cpi is None or cpi_prev is None: return None
    return float(np.clip(-(cpi - cpi_prev) / 1.0, -1.0, 1.0))


def score_unemployment(unemp: Optional[float]) -> Optional[float]:
    """Unemployment → -1 (high) to +1 (low)."""
    if unemp is None: return None
    return float(np.clip((5.0 - unemp) / 2.0, -1.0, 1.0))


def score_usdcny(usdcny: Optional[float], usdcny_ma20: Optional[float]) -> Optional[float]:
    """USDCNY trend → -1 (CNY weakening) to +1 (CNY strengthening)."""
    if usdcny is None or usdcny_ma20 is None: return None
    pct = (usdcny - usdcny_ma20) / usdcny_ma20
    return float(np.clip(-pct / 0.02, -1.0, 1.0))


def score_pmi(pmi: Optional[float]) -> Optional[float]:
    """PMI → -1 (contraction) to +1 (expansion). 50 = neutral."""
    if pmi is None: return None
    return float(np.clip((pmi - 50) / 5, -1.0, 1.0))


def score_m2_growth(m2_yoy: Optional[float]) -> Optional[float]:
    """M2 YoY → -1 (tight) to +1 (loose). ~10% = neutral for China."""
    if m2_yoy is None: return None
    return float(np.clip((m2_yoy - 10) / 4, -1.0, 1.0))


def score_lpr(lpr: Optional[float], lpr_prev: Optional[float]) -> Optional[float]:
    """LPR direction → +1 (cut), 0 (hold), -0.5 (hike)."""
    if lpr is None or lpr_prev is None: return None
    if lpr < lpr_prev: return 1.0
    elif lpr > lpr_prev: return -0.5
    return 0.0


def score_index_trend(above_200d: Optional[bool]) -> Optional[float]:
    """Index above 200d MA → +0.5 / -0.5."""
    if above_200d is None: return None
    return 0.5 if above_200d else -0.5


# ═══ Region Factor Weights ═══

REGION_FACTORS: Dict[Region, Dict[str, float]] = {
    Region.US: {
        'vix': 0.25, 'yield_curve': 0.20, 'fed_policy': 0.20,
        'cpi': 0.15, 'unemployment': 0.10, 'spy_trend': 0.10,
    },
    Region.CN: {
        'qvix': 0.20, 'pmi': 0.15, 'm2': 0.15, 'lpr': 0.15,
        'usdcny': 0.15, 'cpi': 0.10, 'yield_curve': 0.10,
    },
    Region.HK: {
        'qvix': 0.20, 'pmi': 0.15, 'm2': 0.10, 'lpr': 0.10,
        'usdcny': 0.15, 'yield_curve': 0.15, 'spy_trend': 0.10,
        'cpi': 0.05,
    },
}

REGIME_THRESHOLDS = {
    MacroRegime.RISK_ON: 0.25,
    MacroRegime.RISK_OFF: -0.25,
    # Between -0.25 and +0.25 = NEUTRAL
}


# ═══ Main Computation ═══

def compute_macro_state(macro_data: dict, region: Region,
                        timestamp: str = "") -> MacroState:
    """
    Compute macro state from raw data dict (matching data_fetcher.py format).
    
    Args:
        macro_data: Dict from data_fetcher.fetch_macro_data()
        region: Target region (US, CN, HK)
        timestamp: Current bar date string
    
    Returns:
        MacroState with score, regime, sub_factors, and coverage
    """
    factors = REGION_FACTORS.get(region, {})
    sub_scores = {}
    weighted_sum = 0.0
    total_weight = 0.0
    n_available = 0
    
    for factor, weight in factors.items():
        score = None
        
        if factor == 'vix':
            vix_val = safe_last(macro_data.get('vix'))
            if vix_val is not None:
                score = score_vix(vix_val)
        elif factor == 'qvix':
            qvix_val = safe_last(macro_data.get('china_qvix'))
            if qvix_val is not None:
                score = score_qvix(qvix_val)
        elif factor == 'yield_curve':
            spread = safe_last(macro_data.get('us_spread_10y2y'))
            if spread is not None:
                score = score_yield_curve(spread)
        elif factor == 'fed_policy':
            rate = safe_last(macro_data.get('fed_rate'))
            # Fed rate 3m ago — approximate from same series
            rate_series = macro_data.get('fed_rate')
            if isinstance(rate_series, pd.Series) and len(rate_series) > 60:
                rate_3m = safe_last(rate_series.iloc[:-60]) if len(rate_series) > 60 else None
            else:
                rate_3m = rate  # fallback
            if rate is not None and rate_3m is not None:
                score = score_fed_policy(rate, rate_3m)
        elif factor == 'cpi':
            cpi = safe_last(macro_data.get('us_cpi_yoy'))
            cpi_series = macro_data.get('us_cpi_yoy')
            if isinstance(cpi_series, pd.Series) and len(cpi_series) > 1:
                cpi_prev = safe_last(cpi_series.iloc[:-1])
            else:
                cpi_prev = cpi
            if cpi is not None and cpi_prev is not None:
                score = score_cpi_direction(cpi, cpi_prev)
        elif factor == 'unemployment':
            unemp = safe_last(macro_data.get('us_unemployment'))
            if unemp is not None:
                score = score_unemployment(unemp)
        elif factor == 'spy_trend':
            # Index trend: use bull_regime from precomputed if available
            # Otherwise neutral
            score = 0.0
            n_available += 1
            total_weight += weight
            sub_scores[factor] = score
            continue
        elif factor == 'pmi':
            pmi = safe_last(macro_data.get('china_pmi'))
            if pmi is not None:
                score = score_pmi(pmi)
        elif factor == 'm2':
            m2 = safe_last(macro_data.get('china_m2_yoy'))
            if m2 is not None:
                score = score_m2_growth(m2)
        elif factor == 'lpr':
            lpr = safe_last(macro_data.get('china_lpr1y'))
            lpr_series = macro_data.get('china_lpr1y')
            if isinstance(lpr_series, pd.Series) and len(lpr_series) > 1:
                lpr_prev = safe_last(lpr_series.iloc[:-1])
            else:
                lpr_prev = lpr
            if lpr is not None and lpr_prev is not None:
                score = score_lpr(lpr, lpr_prev)
        elif factor == 'usdcny':
            usdcny = safe_last(macro_data.get('usdcny'))
            usdcny_series = macro_data.get('usdcny')
            if isinstance(usdcny_series, pd.Series) and len(usdcny_series) >= 20:
                usdcny_ma20 = usdcny_series.tail(20).mean()
            else:
                usdcny_ma20 = usdcny
            if usdcny is not None and usdcny_ma20 is not None:
                score = score_usdcny(usdcny, usdcny_ma20)
        
        if score is not None:
            sub_scores[factor] = round(score, 4)
            weighted_sum += score * weight
            total_weight += weight
            n_available += 1
    
    total_factors = len(factors)
    coverage = n_available / total_factors if total_factors > 0 else 0
    macro_score = (weighted_sum / total_weight) if total_weight > 0 else 0.0
    
    # Determine regime
    if macro_score > REGIME_THRESHOLDS[MacroRegime.RISK_ON]:
        regime = MacroRegime.RISK_ON
    elif macro_score < REGIME_THRESHOLDS[MacroRegime.RISK_OFF]:
        regime = MacroRegime.RISK_OFF
    else:
        regime = MacroRegime.NEUTRAL
    
    return MacroState(
        region=region,
        regime=regime,
        score=round(macro_score, 4),
        sub_factors=sub_scores,
        coverage=round(coverage, 3),
        timestamp=timestamp,
    )
