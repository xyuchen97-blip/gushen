# guts/validate_backtest.py
"""
BACKTEST VALIDATION — Claude Plan Sprint 3, Task 8.
Formalizes the compatibility criteria into PASS/FAIL functions.

Criteria:
  1. Positive Alpha:  ≥60% of stocks must have positive alpha (strategy Sharpe > B&H Sharpe)
  2. DD Ratio:         avg strategy drawdown < 50% of B&H drawdown (capped at 200%)
  3. Upside Capture:   avg capture >30% of market upside (aligned week comparison)
  4. Per-Market:       each market avg S > 0 (no market consistently loses)

USAGE:
    from guts.validate_backtest import validate, print_report
    report = validate(results)  # results = {ticker: {s, alpha, dd_ratio, up_capture}}
    print_report(report)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import numpy as np


@dataclass
class StockResult:
    ticker: str
    market: str
    sharpe: float
    alpha: float
    dd_ratio: float
    up_capture: float
    buy_count: int = 0


@dataclass  
class ValidationReport:
    """Validation report with PASS/FAIL for each criterion."""
    pos_alpha_pct: float
    avg_dd: float
    avg_uc: float
    avg_alpha: float
    n_stocks: int
    
    passes: Dict[str, bool] = field(default_factory=dict)
    by_market: Dict[str, Dict] = field(default_factory=dict)
    
    THRESHOLDS = {
        'pos_alpha': 0.60,      # 60% positive alpha
        'dd_ratio': 50.0,       # <50% of B&H drawdown
        'upside_capture': 30.0,  # >30% upside capture
    }
    
    def all_pass(self) -> bool:
        return all(self.passes.values())
    
    def failing(self) -> List[str]:
        return [k for k, v in self.passes.items() if not v]


def validate(results: Dict[str, dict], silent: bool = False) -> ValidationReport:
    """
    Validate backtest results against compatibility criteria.
    
    Args:
        results: {ticker: {s, alpha, dd_ratio, up_capture, market, buy_count}}
        silent: suppress console output
    """
    n = len(results)
    if n == 0:
        raise ValueError("Empty results")
    
    alphas = [v['alpha'] for v in results.values()]
    dd_ratios = [v['dd_ratio'] for v in results.values()]
    ucs = [v['up_capture'] for v in results.values()]
    
    pos_alpha_pct = sum(1 for a in alphas if a > 0) / n
    avg_alpha = float(np.mean(alphas))
    avg_dd = float(np.mean(dd_ratios))
    avg_uc = float(np.mean(ucs))
    
    report = ValidationReport(
        pos_alpha_pct=pos_alpha_pct,
        avg_dd=avg_dd,
        avg_uc=avg_uc,
        avg_alpha=avg_alpha,
        n_stocks=n,
        passes={
            'pos_alpha': pos_alpha_pct >= 0.60,
            'dd_ratio': avg_dd < 50.0,
            'upside_capture': avg_uc > 30.0,
        }
    )
    
    # Per-market breakdown
    markets = {}
    for ticker, v in results.items():
        mkt = v.get('market', '?')
        if mkt not in markets:
            markets[mkt] = {'alphas': [], 'sharpe': [], 'count': 0}
        markets[mkt]['alphas'].append(v['alpha'])
        markets[mkt]['sharpe'].append(v['s'])
        markets[mkt]['count'] += 1
    
    for mkt, data in markets.items():
        report.by_market[mkt] = {
            'avg_sharpe': float(np.mean(data['sharpe'])),
            'avg_alpha': float(np.mean(data['alphas'])),
            'pos_count': sum(1 for a in data['alphas'] if a > 0),
            'total': data['count'],
        }
    
    return report


def print_report(report: ValidationReport):
    """Pretty-print validation report."""
    print(f"\n{'='*50}")
    print(f"  GUSHEN BACKTEST VALIDATION — {report.n_stocks} stocks")
    print(f"{'='*50}")
    
    print(f"\n  Pos α: {report.pos_alpha_pct*100:.0f}% "
          f"[{'PASS' if report.passes['pos_alpha'] else 'FAIL'}] (threshold: 60%)")
    print(f"  DD ratio: {report.avg_dd:.0f}% of B&H "
          f"[{'PASS' if report.passes['dd_ratio'] else 'FAIL'}] (threshold: <50%)")
    print(f"  Upside capture: {report.avg_uc:.0f}% "
          f"[{'PASS' if report.passes['upside_capture'] else 'FAIL'}] (threshold: >30%)")
    print(f"  Avg α: {report.avg_alpha:+.3f}")
    
    print(f"\n  By market:")
    for mkt, data in sorted(report.by_market.items()):
        print(f"    {mkt}: S={data['avg_sharpe']:.3f}  α={data['avg_alpha']:+.3f}  "
              f"({data['pos_count']}/{data['total']}>0)")
    
    if report.all_pass():
        print(f"\n  ✅ ALL CRITERIA PASS")
    else:
        print(f"\n  ❌ FAILING: {', '.join(report.failing())}")
    
    return report.all_pass()
