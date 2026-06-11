"""v12 L3 — behavior bucket classifier (TREND / REVERT / FRAGILE).

Rule v2 (validated M1, June 2026 — see ARCHITECTURE_v12_PROPOSAL.md):
  on a trailing 104-week window, evaluated weekly, with 8-week dwell hysteresis:
    FRAGILE if 2y total return <= -10%
    TREND   if 2y realized Sharpe >= 0.7
    REVERT  otherwise

Causality: uses only closes up to the evaluation bar. Stability (M1): median dwell
53 weeks, 0.70 transitions/stock/year.
"""
import numpy as np
import pandas as pd

WIN, DWELL = 104, 8
TREND_SHARPE = 0.70
FRAGILE_RET = -0.10


def classify_buckets(weekly_close: pd.Series) -> pd.Series:
    """Weekly bucket series ('TREND'/'REVERT'/'FRAGILE'/'NA') aligned to input index."""
    lc = np.log(weekly_close.values)
    n = len(lc)
    raw = np.array(['NA'] * n, dtype=object)
    for i in range(WIN, n):
        w = lc[i - WIN:i + 1]
        r1 = np.diff(w)
        if r1.std() == 0:
            continue
        tr = np.exp(lc[i] - lc[i - WIN]) - 1
        sh2y = (r1.mean() / r1.std()) * np.sqrt(52)
        if tr <= FRAGILE_RET:
            raw[i] = 'FRAGILE'
        elif sh2y >= TREND_SHARPE:
            raw[i] = 'TREND'
        else:
            raw[i] = 'REVERT'
    out = np.array(['NA'] * n, dtype=object)
    cur, pend, cnt = 'NA', None, 0
    for i in range(n):
        if raw[i] == 'NA':
            out[i] = cur
            continue
        if cur == 'NA':
            cur = raw[i]
        elif raw[i] != cur:
            if raw[i] == pend:
                cnt += 1
            else:
                pend, cnt = raw[i], 1
            if cnt >= DWELL:
                cur, pend, cnt = raw[i], None, 0
        else:
            pend, cnt = None, 0
        out[i] = cur
    return pd.Series(out, index=weekly_close.index)
