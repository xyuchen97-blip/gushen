#!/usr/bin/env python3
"""Backtest runner with macro snapshot cache.

Fixes two problems:
1. Reproducibility: FRED/akshare historical revisions cause baseline drift
   (same code: S=1.476 in May vs 1.324 in June). Pinning macro to a snapshot
   makes baseline-vs-experiment comparisons trustworthy.
2. Speed: skips ~40s of network fetches on every run.

Usage:
    python3 scripts/run_bt_cached.py --snapshot-only   # fetch + pin macro once
    python3 scripts/run_bt_cached.py                   # run backtest using pinned macro
    rm data/macro_snapshot.pkl                         # to re-pin fresh macro
"""
import os, sys, pickle, runpy
from pathlib import Path

GUSHEN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GUSHEN))
os.environ['GUSHEN_TUNE'] = '1'
os.environ.setdefault('GUSHEN_DB_PATH', str(GUSHEN / 'data' / 'gushen.db'))

SNAP = GUSHEN / 'data' / 'macro_snapshot.pkl'

import strategy.data_fetcher as _dfm
_orig_fetch = _dfm.fetch_macro_data

def _cached_fetch(start, end, *a, **kw):
    if SNAP.exists():
        with open(SNAP, 'rb') as f:
            return pickle.load(f)
    m = _orig_fetch(start, end, *a, **kw)
    with open(SNAP, 'wb') as f:
        pickle.dump(m, f)
    print(f'  [macro snapshot pinned -> {SNAP}]')
    return m

_dfm.fetch_macro_data = _cached_fetch

if '--snapshot-only' in sys.argv:
    _cached_fetch('2021-01-01', '2026-05-06')
    print('  Snapshot ready.')
    sys.exit(0)

runpy.run_path(str(GUSHEN / 'strategy' / 'fast_backtest.py'), run_name='__main__')
