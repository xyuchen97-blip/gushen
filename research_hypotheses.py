#!/usr/bin/env python3
"""
Research: Two trading hypotheses for Gushen optimization
========================================================
H1: HK/China daytime gains → US growth stocks rise that night
H2: US Friday / options expiry dates tend to drop

Uses gushen.db OHLCV cache (2021-2026).
"""
import sqlite3, pandas as pd, numpy as np, json, os, sys
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "data" / "gushen.db"
if not DB_PATH.exists():
    print(f"ERROR: {DB_PATH} not found. Run tune.py first to build cache.")
    sys.exit(1)

conn = sqlite3.connect(str(DB_PATH))

# ─── Load OHLCV data ───────────────────────────────────────────────

def load_market(market, tickers=None):
    q = "SELECT ticker, date, open, high, low, close, volume FROM ohlcv WHERE market=?"
    df = pd.read_sql(q, conn, params=(market,))
    df['date'] = pd.to_datetime(df['date'])
    if tickers:
        df = df[df['ticker'].isin(tickers)]
    return df

# HK / A-share tickers from the backtest universe
HK_TICKERS = ['0700.HK', '9988.HK', '3690.HK', '1810.HK', '1211.HK', '0388.HK']
A_TICKERS = ['600519.SH', '000858.SZ', '300750.SZ', '002594.SZ', '601318.SH', '600036.SH', '002230.SZ', '300015.SZ']
US_GROWTH = ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'META']
US_ALL = US_GROWTH + ['JPM']

print("Loading data from gushen.db...")
hk_df = load_market('HK', HK_TICKERS)
a_df  = load_market('A', A_TICKERS)
us_df = load_market('US', US_ALL)

print(f"  HK: {len(hk_df):,} rows, {hk_df['ticker'].nunique()} tickers")
print(f"  A:  {len(a_df):,} rows, {a_df['ticker'].nunique()} tickers")
print(f"  US: {len(us_df):,} rows, {us_df['ticker'].nunique()} tickers")

# ═══════════════════════════════════════════════════════════════════
# HYPOTHESIS 1: HK/China daytime → US overnight momentum
# ═══════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("HYPOTHESIS 1: HK/China gains → US growth stocks rise that night")
print("="*70)

# Build daily return indices for each market
def market_daily_return(df):
    """Equal-weighted daily return across all tickers in a market."""
    pivoted = df.pivot_table(index='date', columns='ticker', values='close')
    daily_ret = pivoted.pct_change()
    return daily_ret.mean(axis=1).dropna()

hk_ret = market_daily_return(hk_df)
a_ret  = market_daily_return(a_df)
us_growth_ret = market_daily_return(us_df[us_df['ticker'].isin(US_GROWTH)])
us_all_ret = market_daily_return(us_df)

# HK trades ~9:30-16:00 HKT, US trades ~9:30-16:00 ET (same calendar day for HK morning → US evening)
# Key insight: HK day N close → US day N close (same date)
# We align on calendar date: if HK is up on date D, is US up on date D?

# Merge on date
h1 = pd.DataFrame({
    'hk_ret': hk_ret,
    'a_ret': a_ret,
    'asia_avg': (hk_ret.reindex(hk_ret.index.union(a_ret.index)).fillna(0) +
                 a_ret.reindex(hk_ret.index.union(a_ret.index)).fillna(0)) / 2,
    'us_growth': us_growth_ret,
    'us_all': us_all_ret,
}).dropna()

# Also test: Asia day D → US day D (same-day effect, since Asia trades first)
# AND: Asia day D → US day D+1 (next-day spillover)
h1['us_growth_next'] = h1['us_growth'].shift(-1)
h1['us_all_next'] = h1['us_all'].shift(-1)

print(f"\nOverlapping trading days: {len(h1)}")

# --- Same-day correlation ---
print("\n--- Same-day correlation (Asia morning → US evening) ---")
for asia_col, asia_label in [('hk_ret', 'HK'), ('a_ret', 'A-shares'), ('asia_avg', 'Asia avg')]:
    for us_col, us_label in [('us_growth', 'US Growth'), ('us_all', 'US All')]:
        valid = h1[[asia_col, us_col]].dropna()
        corr = valid[asia_col].corr(valid[us_col])

        # Conditional returns: when Asia is up vs down
        asia_up = valid[valid[asia_col] > 0]
        asia_down = valid[valid[asia_col] <= 0]
        us_when_asia_up = asia_up[us_col].mean() * 100
        us_when_asia_down = asia_down[us_col].mean() * 100
        win_rate = (asia_up[us_col] > 0).mean() * 100

        print(f"  {asia_label:12s} → {us_label:10s}: corr={corr:+.3f} | "
              f"Asia↑: US avg={us_when_asia_up:+.3f}% win={win_rate:.1f}% (n={len(asia_up)}) | "
              f"Asia↓: US avg={us_when_asia_down:+.3f}%")

# --- Next-day spillover ---
print("\n--- Next-day spillover (Asia day D → US day D+1) ---")
for asia_col, asia_label in [('hk_ret', 'HK'), ('a_ret', 'A-shares'), ('asia_avg', 'Asia avg')]:
    for us_col, us_label in [('us_growth_next', 'US Growth+1'), ('us_all_next', 'US All+1')]:
        valid = h1[[asia_col, us_col]].dropna()
        corr = valid[asia_col].corr(valid[us_col])

        asia_up = valid[valid[asia_col] > 0]
        asia_down = valid[valid[asia_col] <= 0]
        us_when_asia_up = asia_up[us_col].mean() * 100
        us_when_asia_down = asia_down[us_col].mean() * 100
        win_rate = (asia_up[us_col] > 0).mean() * 100

        print(f"  {asia_label:12s} → {us_label:14s}: corr={corr:+.3f} | "
              f"Asia↑: US avg={us_when_asia_up:+.3f}% win={win_rate:.1f}% (n={len(asia_up)}) | "
              f"Asia↓: US avg={us_when_asia_down:+.3f}%")

# --- Magnitude buckets ---
print("\n--- HK return magnitude → US Growth same-day response ---")
valid = h1[['hk_ret', 'us_growth']].dropna()
bins = [(-np.inf, -0.02), (-0.02, -0.005), (-0.005, 0.005), (0.005, 0.02), (0.02, np.inf)]
labels = ['HK<<-2%', 'HK -2~-0.5%', 'HK flat', 'HK +0.5~2%', 'HK>>+2%']
for (lo, hi), label in zip(bins, labels):
    mask = (valid['hk_ret'] > lo) & (valid['hk_ret'] <= hi)
    subset = valid[mask]
    if len(subset) > 5:
        avg = subset['us_growth'].mean() * 100
        wr = (subset['us_growth'] > 0).mean() * 100
        print(f"  {label:16s}: US avg={avg:+.4f}%  win={wr:.1f}%  n={len(subset)}")

# --- Statistical significance ---
from scipy import stats
print("\n--- Statistical significance ---")
valid = h1[['hk_ret', 'us_growth']].dropna()
asia_up_us = valid[valid['hk_ret'] > 0]['us_growth']
asia_dn_us = valid[valid['hk_ret'] <= 0]['us_growth']
t_stat, p_val = stats.ttest_ind(asia_up_us, asia_dn_us)
print(f"  t-test (US growth | HK up vs HK down): t={t_stat:.3f}, p={p_val:.4f}")

corr, p_corr = stats.pearsonr(valid['hk_ret'], valid['us_growth'])
print(f"  Pearson correlation: r={corr:.4f}, p={p_corr:.4f}")


# ═══════════════════════════════════════════════════════════════════
# HYPOTHESIS 2: US Friday / Options Expiry Effect
# ═══════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("HYPOTHESIS 2: US Friday / Options Expiry Drop")
print("="*70)

# Build per-ticker daily returns for US stocks
us_pivot = us_df[us_df['ticker'].isin(US_GROWTH)].pivot_table(
    index='date', columns='ticker', values='close')
us_daily = us_pivot.pct_change().dropna(how='all')
us_daily['avg'] = us_daily.mean(axis=1)
us_daily['dow'] = us_daily.index.dayofweek  # 0=Mon, 4=Fri

# --- Day-of-week returns ---
print("\n--- Day-of-week average returns (US Growth, 2021-2026) ---")
dow_names = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday', 4: 'Friday'}
dow_stats = []
for d in range(5):
    subset = us_daily[us_daily['dow'] == d]['avg']
    avg_ret = subset.mean() * 100
    win = (subset > 0).mean() * 100
    vol = subset.std() * 100
    t, p = stats.ttest_1samp(subset, 0)
    dow_stats.append({'day': dow_names[d], 'avg_ret': avg_ret, 'win': win, 'vol': vol, 'n': len(subset), 't': t, 'p': p})
    print(f"  {dow_names[d]:10s}: avg={avg_ret:+.4f}%  win={win:.1f}%  vol={vol:.3f}%  n={len(subset)}  p={p:.4f}")

# --- Friday vs non-Friday ---
fri = us_daily[us_daily['dow'] == 4]['avg']
non_fri = us_daily[us_daily['dow'] != 4]['avg']
t_fri, p_fri = stats.ttest_ind(fri, non_fri)
print(f"\n  Friday vs Non-Friday t-test: t={t_fri:.3f}, p={p_fri:.4f}")
print(f"  Friday avg: {fri.mean()*100:+.4f}%  Non-Friday avg: {non_fri.mean()*100:+.4f}%")

# --- Options Expiry (3rd Friday of each month) ---
print("\n--- Monthly Options Expiry (3rd Friday) Effect ---")
def is_opex(date):
    """Third Friday of the month."""
    if date.weekday() != 4: return False
    day = date.day
    # Third Friday: day is between 15 and 21
    return 15 <= day <= 21

us_daily['is_opex'] = us_daily.index.map(is_opex)
opex = us_daily[us_daily['is_opex']]['avg']
non_opex_fri = us_daily[(us_daily['dow'] == 4) & (~us_daily['is_opex'])]['avg']
non_opex_all = us_daily[~us_daily['is_opex']]['avg']

print(f"  OpEx Fridays:     avg={opex.mean()*100:+.4f}%  win={( opex > 0).mean()*100:.1f}%  n={len(opex)}")
print(f"  Non-OpEx Fridays: avg={non_opex_fri.mean()*100:+.4f}%  win={(non_opex_fri > 0).mean()*100:.1f}%  n={len(non_opex_fri)}")
print(f"  All non-OpEx:     avg={non_opex_all.mean()*100:+.4f}%  win={(non_opex_all > 0).mean()*100:.1f}%  n={len(non_opex_all)}")

t_opex, p_opex = stats.ttest_ind(opex, non_opex_all)
print(f"  OpEx vs All Others: t={t_opex:.3f}, p={p_opex:.4f}")

# --- Week of OpEx (OpEx week Mon-Thu vs OpEx Friday) ---
print("\n--- OpEx Week Pattern ---")
opex_dates = us_daily[us_daily['is_opex']].index
opex_weeks = set()
for d in opex_dates:
    # Get Mon-Fri of that week
    monday = d - pd.Timedelta(days=d.weekday())
    for i in range(5):
        opex_weeks.add(monday + pd.Timedelta(days=i))

us_daily['in_opex_week'] = us_daily.index.isin(opex_weeks)
opex_week = us_daily[us_daily['in_opex_week']]
for d in range(5):
    subset = opex_week[opex_week['dow'] == d]['avg']
    if len(subset) > 3:
        print(f"  OpEx-week {dow_names[d]:10s}: avg={subset.mean()*100:+.4f}%  win={(subset > 0).mean()*100:.1f}%  n={len(subset)}")

# --- Per-ticker Friday effect ---
print("\n--- Per-ticker Friday returns ---")
for ticker in US_GROWTH:
    if ticker in us_daily.columns:
        fri_t = us_daily[us_daily['dow'] == 4][ticker].dropna()
        non_t = us_daily[us_daily['dow'] != 4][ticker].dropna()
        print(f"  {ticker:6s}: Fri avg={fri_t.mean()*100:+.4f}%  Non-Fri avg={non_t.mean()*100:+.4f}%  "
              f"Δ={( fri_t.mean()-non_t.mean())*100:+.4f}%")

# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("SUMMARY")
print("="*70)

# Save results
results = {
    'date': str(datetime.now())[:10],
    'h1_cross_market': {
        'hk_us_growth_sameday_corr': float(h1[['hk_ret','us_growth']].dropna().corr().iloc[0,1]),
        'hk_us_growth_sameday_pvalue': float(p_corr),
        'us_avg_when_hk_up': float(h1[h1['hk_ret']>0]['us_growth'].mean()),
        'us_avg_when_hk_down': float(h1[h1['hk_ret']<=0]['us_growth'].mean()),
    },
    'h2_friday_effect': {
        'friday_avg_ret': float(fri.mean()),
        'non_friday_avg_ret': float(non_fri.mean()),
        'friday_vs_nonfriday_pvalue': float(p_fri),
        'opex_avg_ret': float(opex.mean()),
        'opex_vs_all_pvalue': float(p_opex),
    }
}

out_path = Path(__file__).parent / "data" / "hypothesis_research.json"
with open(out_path, 'w') as f:
    json.dump(results, f, indent=2)
print(f"\nResults saved to {out_path}")

conn.close()
