"""v15 daily driver — Josh's two daily uses, compute-light.

1. Daily assessment of YOUR list (data/my_list.json):
     python3 agents/daily_driver.py
   Scores every name (milliseconds each, cached indicators), prints actions +
   sizing + what CHANGED vs yesterday. No LLM calls unless --commentary.

2. Deep dive on one stock (engine + GLM-4.7 analysis):
     python3 agents/daily_driver.py --deep NVDA

LLM budget rule: GLM-4.7 is called only for changed/flagged names or deep dives —
never the whole list.
"""
import os, sys, json, pickle, argparse, warnings
from datetime import datetime
import pandas as pd
warnings.filterwarnings('ignore')
V15 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V15)
os.environ['GUSHEN_TUNE'] = '1'
os.environ.setdefault('GUSHEN_DB_PATH', os.path.join(V15, 'data', 'gushen.db'))
import strategy.gushen_keys  # noqa: F401
from strategy.scoring import score
from strategy.gushen_cache import get_ohlcv

ap = argparse.ArgumentParser()
ap.add_argument('--deep', default='', help='ticker for deep dive')
ap.add_argument('--commentary', action='store_true', help='GLM commentary on changes')
ap.add_argument('--scan', action='store_true',
                help='discovery: scan the WIDE universe for new BUYs and top-30 entrants')
ap.add_argument('--review', action='store_true',
                help='v16: review your discretion ledger (you vs engine, over time)')
args = ap.parse_args()

macro = pickle.load(open(os.path.join(V15, 'data', 'macro_snapshot.pkl'), 'rb'))

# ── v16: user lists are YOURS — created empty on first run, you fill them in ──
LIST_TEMPLATE = {
    "_help": "Your monitor list. Add stocks as \"TICKER\": \"MARKET\" — markets: A | HK | US. "
             "Formats: A-share 600519.SH / 000858.SZ, HK 0700.HK, US NVDA. "
             "Example: \"0700.HK\": \"HK\". Save this file and rerun the daily driver."
}
POS_TEMPLATE = {
    "_help": "Your actual positions (optional but recommended). Format per position: "
             "\"TICKER\": {\"market\": \"US\", \"entry_price\": 123.4, \"entry_date\": \"2026-01-15\", "
             "\"exit\": {\"stop\": 110.0, \"hh_below\": -2, \"max_weeks\": 26}}. "
             "The \"exit\" contract is optional — set the rules you commit to at entry; "
             "the daily report will enforce them."
}

def load_user_json(path, template, required):
    if not os.path.exists(path):
        json.dump(template, open(path, 'w'), ensure_ascii=False, indent=1)
        print(f"\n📝 FIRST RUN: created {os.path.basename(path)} — please edit it with your "
              f"own stocks (instructions inside the file), then rerun.")
        if required:
            sys.exit(0)
        return {}
    data = {k: v for k, v in json.load(open(path)).items() if not k.startswith('_')}
    if required and not data:
        print(f"\n📝 {os.path.basename(path)} is empty — add your stocks "
              f"(instructions in the file's _help field), then rerun.")
        sys.exit(0)
    return data

CAL_P = os.path.join(V15, 'data', 'calibration.json')
CAL = json.load(open(CAL_P)) if os.path.exists(CAL_P) else None

def entry_stats():
    """Honest historical record of engine entries (v16 calibration layer)."""
    if not CAL: return ''
    e = CAL['entry_events']
    return (f"[hist: {e['n']} such entries → {e['p_pos_4w']:.0%} positive 4w, "
            f"avg {e['mean_4w']:+.1%}]")

def tier_note(rank, n_cand):
    if not CAL or rank is None: return ''
    t = CAL['rank_tiers']
    tier = 'top30' if rank <= 30 else ('rank31_60' if rank <= 60 else 'rest')
    s = t.get(tier)
    return (f"rank #{rank}/{n_cand} ({tier}: hist {s['mean_1w']:+.2%}/wk)" if s else f"rank #{rank}/{n_cand}")
MYLIST = os.path.join(V15, 'data', 'my_list.json')
# monitor list required for the default view; not required for --scan/--review/--deep
_list_required = not (args.scan or args.review or args.deep)
mylist = load_user_json(MYLIST, LIST_TEMPLATE, required=_list_required)
LOG = os.path.join(V15, 'data', 'daily_log.jsonl')


def score_one(code, mkt):
    df = get_ohlcv(code, mkt)
    if df is None or len(df) < 300:
        return None
    df = df.sort_index()
    dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min',
                                    'close':'last','volume':'sum'}).dropna()
    return score(df, dfw, ticker=code, market=mkt, macro_data=macro)


if args.review:
    DISC_ = os.path.join(V15, 'data', 'discretion_ledger.jsonl')
    if not os.path.exists(DISC_):
        print('no ledger yet — it builds automatically as you run the daily driver'); sys.exit(0)
    L = [json.loads(l) for l in open(DISC_)]
    days = sorted({l['date'] for l in L})
    held_vs_exit = [l for l in L if l.get('held') and l.get('engine_action') == 'EXIT']
    missed_buys = [l for l in L if not l.get('held') and l.get('engine_action') == 'BUY']
    print(f"═══ DISCRETION LEDGER — {len(days)} days, {len(L)} entries ═══")
    print(f"days you held something the engine said EXIT: {len(held_vs_exit)} "
          f"({len(set(l['code'] for l in held_vs_exit))} names)")
    print(f"engine BUYs you did not hold: {len(missed_buys)} "
          f"({len(set(l['code'] for l in missed_buys))} names)")
    from collections import Counter
    for label, rows_ in (('most-held-against-engine', held_vs_exit), ('most-missed BUYs', missed_buys)):
        c = Counter(l['code'] for l in rows_).most_common(5)
        if c:
            print(f"  {label}: " + ', '.join(f'{k}×{v}' for k, v in c))
    print("\n(Outcome attribution — whether your overrides made money — needs ~3 months "
          "of ledger history; this report will grow that section automatically.)")
    sys.exit(0)

if args.deep:
    code = args.deep
    mkt = mylist.get(code) or ('US' if code.isalpha() else 'HK' if code.endswith('.HK') else 'A')
    r = score_one(code, mkt)
    if r is None:
        print(f'{code}: no data in cache — refresh OHLCV first'); sys.exit(1)
    print(f"\n{code} — engine verdict: {r['action']} | composite {r['composite']} | "
          f"bucket {r['bucket']} | hold_health {r['hold_health']} | "
          f"suggested size ×{r['suggested_position_mult']}")
    print('engine reasoning:', r['reasoning'][:400])
    # v16 knowledge layer: expert context brief (if fresh) grounds the GLM analysis
    ctx = ''
    CTX_P = os.path.join(V15, 'data', 'market_context.json')
    if os.path.exists(CTX_P):
        c = json.load(open(CTX_P))
        try:
            age = (datetime.now() - datetime.strptime(c.get('as_of',''), '%Y-%m-%d')).days
        except ValueError:
            age = 99
        if age <= 14:
            ctx = ("\n\n当前机构研究背景(Citadel/Man/SemiAnalysis等摘要, 仅供风险参考, "
                   "不可作为加仓理由): "
                   f"宏观: {c.get('macro_view','')} | 资金流: {c.get('flows_liquidity','')} | "
                   f"半导体/AI: {c.get('semis_ai','')} | 中国/香港: {c.get('china_hk','')} | "
                   f"风险提示: {'; '.join(c.get('risk_flags', [])[:4])}")
    try:
        from agents.glm_client import glm_chat
        df = get_ohlcv(code, mkt).sort_index()
        recent = df['close'].tail(60)
        chg = {f'{n}d': f"{(recent.iloc[-1]/recent.iloc[-min(n,len(recent))]-1)*100:+.1f}%"
               for n in (5, 20, 60)}
        txt = glm_chat([{"role": "user", "content":
            f"You are a senior equity analyst. A quantitative engine rates {code} ({mkt} market) "
            f"as {r['action']} (composite {r['composite']}, regime {r['regime']}, behavior bucket "
            f"{r['bucket']}, trend health {r['hold_health']}, active signals: "
            f"{', '.join(str(a) for a in r['active'][:12])}). Recent price change: {chg}. "
            "Give a concise second-opinion analysis (<=250 words, Chinese): what the quant view "
            "captures, what risks it might miss (events, fundamentals, sector), and what you would "
            "verify before acting. You may advise MORE caution than the engine but never more "
            "aggression. End with one line: 同意引擎/建议谨慎/建议否决 + 理由." + ctx}])
        print('\n— GLM-4.7 deep dive —\n' + txt)
    except Exception as e:
        print(f'\n[GLM unavailable: {e}] — engine verdict above stands alone.')
    sys.exit(0)

if args.scan:
    # ── discovery scan: whole wide universe, surface what EARNED attention ──
    LEGACY = {'600519.SH':'A','000858.SZ':'A','300750.SZ':'A','002594.SZ':'A','601318.SH':'A',
              '600036.SH':'A','002230.SZ':'A','300015.SZ':'A','0700.HK':'HK','9988.HK':'HK',
              '3690.HK':'HK','1810.HK':'HK','1211.HK':'HK','0388.HK':'HK','AAPL':'US','NVDA':'US',
              'MSFT':'US','GOOGL':'US','AMZN':'US','META':'US','JPM':'US'}
    universe = dict(LEGACY)
    for fn in ('universe_v13_new.json', 'universe_v14_breadth.json'):
        p = os.path.join(V15, 'data', fn)
        if os.path.exists(p):
            u = json.load(open(p))
            for mkt_ in ('A', 'HK', 'US'):
                for c in u[mkt_]:
                    universe.setdefault(c, mkt_)
    SCAN_LOG = os.path.join(V15, 'data', 'scan_log.jsonl')
    prev_top = set()
    if os.path.exists(SCAN_LOG):
        last = json.loads(open(SCAN_LOG).readlines()[-1])
        prev_top = set(last.get('top30', []))
    res = []
    for code, mkt in universe.items():
        r = score_one(code, mkt)
        if r:
            res.append({'code': code, 'mkt': mkt, 'action': r['action'],
                        'composite': r['composite'], 'bucket': r['bucket'],
                        'size': r['suggested_position_mult']})
    candidates = [r for r in res if r['action'] in ('BUY', 'HOLD')]
    top30 = sorted(candidates, key=lambda x: -x['composite'])[:30]
    top_codes = [r['code'] for r in top30]
    with open(SCAN_LOG, 'a') as f:
        f.write(json.dumps({'date': str(datetime.now())[:10], 'top30': top_codes},
                           ensure_ascii=False) + '\n')
    inlist = set(mylist)
    ranks = {r['code']: i+1 for i, r in
             enumerate(sorted(candidates, key=lambda x: -x['composite']))}
    print(f"═══ DISCOVERY SCAN — {len(res)} names, {len(candidates)} candidates ═══")
    buys = [r for r in res if r['action'] == 'BUY']
    if buys:
        print(f"\nBUY signals across the universe (★ = not in your list) {entry_stats()}")
        for r in sorted(buys, key=lambda x: -x['composite']):
            star = ' ' if r['code'] in inlist else '★'
            print(f" {star} {r['code']:10s} {r['mkt']:2s} comp {r['composite']:5.1f} "
                  f"{r['bucket']:7s} size ×{r['size']} | {tier_note(ranks.get(r['code']), len(candidates))}")
    new_in = [c for c in top_codes if c not in prev_top]
    dropped = [c for c in prev_top if c not in top_codes]
    if prev_top:
        print(f"\ntop-30 changes: entered {new_in or '—'} | dropped {dropped or '—'}")
    print('\ntop-30 selection (★ = not in your list):')
    for r in top30:
        star = ' ' if r['code'] in inlist else '★'
        print(f" {star} {r['code']:10s} {r['mkt']:2s} comp {r['composite']:5.1f} {r['bucket']:7s}")
    sys.exit(0)

# ── daily assessment (position-aware if data/my_positions.json exists) ──
# my_positions.json format: {"NVDA": {"market":"US","entry_price":950.0,"entry_date":"2026-03-01"}, ...}
POS = os.path.join(V15, 'data', 'my_positions.json')
positions = load_user_json(POS, POS_TEMPLATE, required=False)
DISC = os.path.join(V15, 'data', 'discretion_ledger.jsonl')

prev = {}
if os.path.exists(LOG):
    for l in open(LOG):
        j = json.loads(l)
        prev[j['code']] = j
rows, changes = [], []
for code, mkt in mylist.items():
    r = score_one(code, mkt)
    if r is None:
        continue
    row = {'date': str(datetime.now())[:10], 'code': code, 'mkt': mkt,
           'action': r['action'], 'composite': r['composite'], 'bucket': r['bucket'],
           'hold_health': r['hold_health'], 'size': r['suggested_position_mult']}
    rows.append(row)
    p = prev.get(code)
    if p and p['action'] != row['action']:
        changes.append((code, p['action'], row['action']))
with open(LOG, 'a') as f:
    for row in rows:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')

# position-aware view: portfolio header + P&L + EXIT CONTRACTS + discretion ledger
if positions:
    # v16 Man-style portfolio vol-target (validated §6i: S 1.45→1.56, dd -21%→-15%):
    # trailing 21d realized vol of YOUR portfolio → scale ALL position sizes.
    # De-risk only (scale ≤ 1.0) — leverage is an owner decision, not the tool's.
    port_scale = 1.0
    if len(positions) >= 3:
        try:
            import numpy as _np
            rets = []
            for code, p in positions.items():
                d_ = get_ohlcv(code, p.get('market', 'US'))
                if d_ is not None and len(d_) > 30:
                    rets.append(d_.sort_index()['close'].pct_change().tail(21).reset_index(drop=True))
            if len(rets) >= 3:
                pr = pd.concat(rets, axis=1).mean(axis=1)
                rvol = float(pr.std() * _np.sqrt(252))
                if rvol > 0:
                    port_scale = round(min(1.0, 0.10 / rvol), 2)
        except Exception:
            pass
    # portfolio-first header (v16): exposure shape before any signal
    mkts = {}
    pnls = []
    for code, p in positions.items():
        mkts[p.get('market', '?')] = mkts.get(p.get('market', '?'), 0) + 1
    print(f"═══ PORTFOLIO — {len(positions)} positions | by market: "
          + ', '.join(f'{k}:{v}' for k, v in mkts.items())
          + (f" | ⚠ concentration: {max(mkts.values())}/{len(positions)} in one market"
             if max(mkts.values()) > len(positions) * 0.6 and len(positions) >= 3 else '')
          + (f" | vol-target scale ×{port_scale}" + (" ⚠ DE-RISK" if port_scale < 0.9 else "")
             if len(positions) >= 3 else '') + ' ═══')
    ledger = []
    for code, p in positions.items():
        mkt = p.get('market', mylist.get(code, 'US'))
        df_ = get_ohlcv(code, mkt)
        row = next((r for r in rows if r['code'] == code), None)
        if df_ is None or row is None:
            print(f"  {code:10s} — no data/score"); continue
        px = float(df_.sort_index()['close'].iloc[-1])
        pnl = px / float(p['entry_price']) - 1
        pnls.append(pnl)
        eng = row['action']
        flags = []
        if eng == 'EXIT':
            flags.append('⚠ ENGINE SAYS EXIT')
        elif row['hold_health'] < 0:
            flags.append(f"⚠ trend health {row['hold_health']:+.0f}")
        # v16 EXIT CONTRACTS: rules you committed to at entry, enforced here
        x = p.get('exit', {})
        if x.get('stop') and px <= float(x['stop']):
            flags.append(f"🔴 STOP HIT ({x['stop']})")
        if x.get('hh_below') is not None and row['hold_health'] <= float(x['hh_below']):
            flags.append(f"🔴 CONTRACT: hh ≤ {x['hh_below']}")
        if x.get('max_weeks') and p.get('entry_date'):
            held_w = (datetime.now() - datetime.strptime(p['entry_date'], '%Y-%m-%d')).days // 7
            if held_w >= int(x['max_weeks']):
                flags.append(f"🔴 CONTRACT: held {held_w}w ≥ {x['max_weeks']}w")
        print(f"  {code:10s} P&L {pnl:+7.1%} (entry {p['entry_price']}, since {p.get('entry_date','?')}) "
              f"| engine: {eng} hh {row['hold_health']:+.0f} {' '.join(flags)}")
        ledger.append({'date': str(datetime.now())[:10], 'code': code, 'held': True,
                       'pnl': round(pnl, 4), 'engine_action': eng,
                       'hold_health': row['hold_health']})
    # discretion ledger: you holding what the engine would exit (or not holding its BUYs)
    eng_buys = {r['code'] for r in rows if r['action'] == 'BUY'}
    for c in eng_buys - set(positions):
        ledger.append({'date': str(datetime.now())[:10], 'code': c, 'held': False,
                       'engine_action': 'BUY', 'note': 'engine BUY not held'})
    with open(DISC, 'a') as f:
        for l in ledger:
            f.write(json.dumps(l, ensure_ascii=False) + '\n')
    print()

print(f"═══ Gushen daily — {str(datetime.now())[:10]} — {len(rows)} names ═══")
if changes:
    print('CHANGES since last run:')
    for c, a, b in changes:
        print(f'  {c}: {a} → {b}')
else:
    print('no action changes since last run')
for act in ('BUY', 'EXIT', 'WATCH'):
    sel = sorted([r for r in rows if r['action'] == act], key=lambda x: -x['composite'])
    if sel:
        print(f'\n{act}:')
        _ps = port_scale if positions and len(positions) >= 3 else 1.0
        for r in sel:
            eff = round(r['size'] * _ps, 2)
            tag = f"size ×{r['size']}" + (f" → ×{eff} (vol-target)" if _ps < 1.0 else '')
            print(f"  {r['code']:10s} comp {r['composite']:5.1f}  {r['bucket']:7s}  {tag}")
print(f"\nHOLD: {sum(1 for r in rows if r['action']=='HOLD')} names")
if args.commentary and changes:
    try:
        from agents.glm_client import glm_chat
        txt = glm_chat([{"role": "user", "content":
            "用中文简评以下量化引擎信号变化(每条<=25字,只可提示风险不可鼓励加仓): " +
            "; ".join(f"{c}:{a}→{b}" for c, a, b in changes)}])
        print('\n— GLM commentary —\n' + txt)
    except Exception as e:
        print(f'[GLM unavailable: {e}]')
