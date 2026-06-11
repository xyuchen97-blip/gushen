"""v15 Layer 2 — Risk Sentinel (VETO-ONLY, SHADOW MODE).

Authority model (ARCHITECTURE v15 design, June 2026):
  - The quant engine (v12) is the ONLY thing that initiates positions.
  - This sentinel may FLAG / suggest SIZE-DOWN / suggest VETO on new entries.
  - It may NEVER suggest adding risk or override the engine toward buying.
  - SHADOW MODE: outputs are logged to data/sentinel_log.jsonl for 6-month
    evaluation (do vetoed entries underperform non-vetoed?) before any authority.

Compute budget: GLM is called ONLY for names with action changes or BUY signals —
typically <10 calls/day, never the full universe.

What it checks per flagged name:
  1. Earnings proximity (Alpha Vantage EARNINGS_CALENDAR, US names; free, cached daily)
  2. GLM-4.7 judgment on event risk given the engine signal + earnings proximity.
     (News headlines can be added later — keep the input grounded, no speculation.)

Usage: python3 agents/risk_sentinel.py            # process latest shadow run
"""
import os, sys, json, csv, io
from datetime import datetime, date
import requests

V15 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V15)
import strategy.gushen_keys  # noqa: F401  (sets env keys)
from agents.glm_client import glm_json

SHADOW = os.path.join(V15, 'data', 'shadow_log.jsonl')
OUT = os.path.join(V15, 'data', 'sentinel_log.jsonl')


def latest_signals():
    rows = [json.loads(l) for l in open(SHADOW)] if os.path.exists(SHADOW) else []
    if not rows:
        return []
    last_run = rows[-1]['run']
    return [r for r in rows if r['run'] == last_run]


def earnings_calendar():
    """US earnings dates within 3 months (AV free endpoint, CSV)."""
    try:
        r = requests.get('https://www.alphavantage.co/query', timeout=20, params={
            'function': 'EARNINGS_CALENDAR', 'horizon': '3month',
            'apikey': os.environ.get('ALPHA_VANTAGE_KEY', '')})
        out = {}
        for row in csv.DictReader(io.StringIO(r.text)):
            out[row.get('symbol', '')] = row.get('reportDate', '')
        return out
    except Exception:
        return {}


def main():
    sigs = latest_signals()
    if not sigs:
        print('no shadow signals found — run scripts/shadow_run.py first')
        return
    # Only flag: BUYs, EXITs, and held names with earnings within 10 days
    cal = earnings_calendar()
    today = date.today()
    # v16 knowledge layer: fresh expert brief adds grounded risk context (caution-only)
    ctx = ''
    CTX_P = os.path.join(V15, 'data', 'market_context.json')
    if os.path.exists(CTX_P):
        c = json.load(open(CTX_P))
        try:
            age = (datetime.now() - datetime.strptime(c.get('as_of', ''), '%Y-%m-%d')).days
        except ValueError:
            age = 99
        if age <= 14 and c.get('risk_flags'):
            ctx = (" Current institutional research flags (context only): "
                   + '; '.join(c['risk_flags'][:4]) + '.')
    flagged = []
    for s in sigs:
        if s.get('action') in ('BUY', 'EXIT'):
            flagged.append(s)
        elif s.get('mkt') == 'US' and s.get('action') == 'HOLD':
            rd = cal.get(s['code'], '')
            if rd:
                try:
                    days = (datetime.strptime(rd, '%Y-%m-%d').date() - today).days
                    if 0 <= days <= 10:
                        s = dict(s); s['earnings_in_days'] = days
                        flagged.append(s)
                except ValueError:
                    pass
    print(f'{len(flagged)} names flagged for sentinel review (of {len(sigs)})')

    results = []
    for s in flagged:
        ed = cal.get(s['code'], 'unknown')
        prompt = (
            "You are a risk sentinel for a quantitative stock engine. Your ONLY job is "
            "to flag event risk on this signal. You may respond with verdict 'CLEAR', "
            "'CAUTION' (suggest half size), or 'VETO' (suggest skip/exit-review). You may "
            "NEVER suggest increasing risk. Respond in strict JSON: "
            '{"verdict": "...", "reason": "<=30 words"}.\n\n'
            f"Signal: {s.get('action')} {s['code']} ({s.get('mkt')}), "
            f"composite={s.get('composite')}, bucket={s.get('bucket')}, "
            f"hold_health={s.get('hold_health')}, next earnings date: {ed}, "
            f"today: {today}. Consider only: earnings proximity, obvious binary-event "
            "exposure for this specific company. Do not speculate beyond given facts."
            + ctx
        )
        try:
            j = glm_json(prompt)
            verdict = str(j.get('verdict', 'CLEAR')).upper()
            if verdict not in ('CLEAR', 'CAUTION', 'VETO'):
                verdict = 'CLEAR'
            results.append({'run': s['run'], 'code': s['code'], 'action': s.get('action'),
                            'verdict': verdict, 'reason': j.get('reason', ''),
                            'earnings': ed, 'ts': str(datetime.now())[:16]})
        except Exception as e:
            results.append({'run': s.get('run'), 'code': s['code'],
                            'verdict': 'ERROR', 'reason': f'{type(e).__name__}: {str(e)[:80]}',
                            'ts': str(datetime.now())[:16]})
    with open(OUT, 'a') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    for r in results:
        print(f"  {r['code']:10s} {r.get('action','')!s:5s} → {r['verdict']:7s} {r['reason']}")


if __name__ == '__main__':
    main()
