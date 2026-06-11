"""v17 Council — grounded bull/bear/judge deliberation for ONE stock, on demand.

Design (June 2026, refining the owner's multi-agent reference):
  - NOT a decider: the quant engine owns systematic decisions. The council is a
    structured PRE-MORTEM for the moment a human is about to act on one name.
  - Roles are grounded, not cosplay: bull and bear each receive the SAME factual
    dossier (engine verdict, calibration base rates, cross-sectional rank context,
    fundamentals if cached, flow context, earnings proximity, expert brief) and must
    argue ONLY from it. The judge weighs both against the engine's evidence.
  - GRADED: every verdict is logged to data/council_log.jsonl with the price at
    verdict time. Outcome attribution (did council conviction correlate with
    forward returns?) becomes computable after ~3 months — same trust gradient
    as every other component. No authority until earned.
  - Budget: 3 GLM calls (bull/bear on glm-4.7-flash, judge on glm-4.7), on demand only.

Usage: python3 agents/council.py NVDA
"""
import os, sys, json, pickle, warnings
from datetime import datetime, date
import pandas as pd
warnings.filterwarnings('ignore')
V15 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V15)
os.environ['GUSHEN_TUNE'] = '1'
os.environ.setdefault('GUSHEN_DB_PATH', os.path.join(V15, 'data', 'gushen.db'))
import strategy.gushen_keys  # noqa: F401
from strategy.scoring import score
from strategy.gushen_cache import get_ohlcv
from agents.glm_client import glm_chat, normalize_ticker

if len(sys.argv) < 2:
    print('usage: python3 agents/council.py <TICKER|中文名>'); sys.exit(1)
raw = sys.argv[1]
if raw.replace('.', '').replace('-', '').isalnum() and not any('一' <= ch <= '鿿' for ch in raw):
    code = raw
    mkt = 'US' if code.isalpha() or '-' in code else ('HK' if code.endswith('.HK') else 'A')
else:
    code, mkt = normalize_ticker(raw)
    print(f'normalized: {raw} → {code} ({mkt})')

# ── assemble the grounded dossier (facts only — both sides argue from THIS) ──
macro = pickle.load(open(os.path.join(V15, 'data', 'macro_snapshot.pkl'), 'rb'))
df = get_ohlcv(code, mkt)
if df is None or len(df) < 300:
    print(f'{code}: no data in cache'); sys.exit(1)
df = df.sort_index()
dfw = df.resample('W-FRI').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
r = score(df, dfw, ticker=code, market=mkt, macro_data=macro)
px = df['close']
chg = {f'{n}d': f"{(px.iloc[-1]/px.iloc[-min(n,len(px))]-1)*100:+.1f}%" for n in (5, 20, 60, 250)}
vol_ann = f"{px.pct_change().tail(63).std()*15.87*100:.0f}%"
CAL = json.load(open(os.path.join(V15, 'data', 'calibration.json'))) if os.path.exists(os.path.join(V15, 'data', 'calibration.json')) else {}
ent = CAL.get('entry_events', {})
tiers = CAL.get('rank_tiers', {})
ctx = {}
CTX_P = os.path.join(V15, 'data', 'market_context.json')
if os.path.exists(CTX_P):
    ctx = json.load(open(CTX_P))
# recent news (the only truly high-frequency information layer; council-only, never
# the quant model). Headlines as FACTS for both sides to interpret — numeric sentiment
# labels included where AV provides them but treated as one input, not a verdict.
news_line = '近期新闻: 不可用'
try:
    items = []
    if mkt == 'US':
        import requests as _rq
        rj = _rq.get('https://www.alphavantage.co/query', timeout=12, params={
            'function': 'NEWS_SENTIMENT', 'tickers': code, 'limit': 6,
            'apikey': os.environ.get('ALPHA_VANTAGE_KEY', '')}).json()
        for f_ in rj.get('feed', [])[:4]:
            items.append(f"[{f_['time_published'][:8]}|{f_.get('overall_sentiment_label','')}] {f_['title'][:80]}")
    if not items:  # HK/A primary, US fallback
        import yfinance as _yf
        ysym = code.replace('.SH', '.SS') if mkt == 'A' else code
        for it in (_yf.Ticker(ysym).news or [])[:4]:
            c_ = it.get('content', it)
            t_ = c_.get('title') or it.get('title') or ''
            if t_:
                items.append(f"- {t_[:90]}")
    if items:
        news_line = '近期新闻(标题为事实, 双方自行解读):\n' + '\n'.join(items)
except Exception:
    pass

flow = ''
if mkt == 'HK' and macro.get('south_flow') is not None:
    s20 = macro['south_flow'].rolling(20).sum()
    z = float(((s20 - s20.rolling(250).mean()) / s20.rolling(250).std()).iloc[-1])
    flow = f'南向资金20日流z分数: {z:+.1f} (z<-1为重度流出风险区)'

# live fundamentals (TradingView screener, ~0.7s, all 3 markets, no auth):
# statement data as reported + P/E computed from live price. Deliberately NOT in the
# quant model (4 tests, all negative at this horizon) — the council/human layer is
# where fundamentals belong.
fund_line = '基本面: 数据不可用'
try:
    from strategy.data_fetcher import _fetch_fundamental_tv
    fd = _fetch_fundamental_tv(code, mkt) or {}
    if fd:
        pe = (float(px.iloc[-1]) / fd['eps']) if fd.get('eps') else None
        parts = []
        if pe and 0 < pe < 1000: parts.append(f"P/E(TTM) {pe:.1f}")
        if fd.get('roe') is not None: parts.append(f"ROE {fd['roe']:.1f}%")
        if fd.get('profit_margin') is not None: parts.append(f"净利率 {fd['profit_margin']*100:.1f}%")
        if fd.get('revenue_growth') is not None: parts.append(f"营收增速 {fd['revenue_growth']*100:+.1f}%")
        if fd.get('profit_growth') is not None: parts.append(f"利润增速 {fd['profit_growth']*100:+.1f}%")
        fund_line = '基本面(TV实时): ' + ' | '.join(parts)
except Exception:
    pass

dossier = f"""【事实档案 — 仅可依据以下事实论证】
标的: {code} ({mkt}市场) | 现价涨跌: {json.dumps(chg, ensure_ascii=False)} | 年化波动: {vol_ann}
量化引擎判定: {r['action']} | composite {r['composite']} | 区制 {r['regime']} | 行为桶 {r['bucket']}
趋势健康度 {r['hold_health']} | 建议仓位倍数 ×{r['suggested_position_mult']}
引擎活跃信号: {', '.join(str(a) for a in r['active'][:12])}
{fund_line}
{news_line}
校准基率(历史131只股票2016-2026): 引擎入场信号后4周: {ent.get('p_pos_4w','?')}概率为正, 平均{ent.get('mean_4w',0)*100:+.1f}%;
排名前30组合周均收益 {tiers.get('top30',{}).get('mean_1w',0)*100:+.2f}% vs 其余 {tiers.get('rest',{}).get('mean_1w',0)*100:+.2f}%
{flow}
机构研究背景(如有): 宏观: {ctx.get('macro_view','无')} | 资金流: {ctx.get('flows_liquidity','无')} | 风险: {'; '.join(ctx.get('risk_flags',[])[:3]) or '无'}
重要约束: 校准显示引擎信号的绝对优势温和(入场后4周约54%为正)——不要夸大任何一方的确定性。"""

print(f'\n{code} — 引擎: {r["action"]} comp {r["composite"]} bucket {r["bucket"]}\n')
print('── 召集议事会 (bull → bear → judge) ──')

def ask(role_prompt, model):
    return glm_chat([{"role": "user", "content": role_prompt + '\n\n' + dossier}],
                    model=model, max_tokens=600, temperature=0.4, timeout=60, thinking=False)

bull = ask("你是多方分析师。仅依据事实档案, 从技术/基本面背景/资金流/事件四个角度, "
           "给出此刻做多该标的的最强论证(<=180字, 中文)。必须诚实承认论证中最薄弱的一环。",
           'glm-4.7-flash')
print('\n🐂 BULL:\n' + bull)
bear = ask("你是空方分析师(pre-mortem角色)。仅依据事实档案, 假设6个月后这笔多头交易亏损了, "
           "最可能的原因是什么? 从技术/基本面/资金流/事件四个角度给出最强反方论证(<=180字, 中文)。"
           "必须诚实承认反方论证中最薄弱的一环。", 'glm-4.7-flash')
print('\n🐻 BEAR:\n' + bear)
judge = glm_chat([{"role": "user", "content":
    f"你是裁判。引擎判定与事实档案如下, 多空双方论证如下。\n\n{dossier}\n\n"
    f"【多方】{bull}\n\n【空方】{bear}\n\n"
    "裁决(中文): (1)哪一方论证更扎实, 为什么(<=60字); (2)引擎判定是否应被人为推翻? "
    "规则: 你只能建议比引擎更谨慎, 不能更激进; (3)最终立场: 同意引擎/减半执行/暂缓; "
    "(4)信心1-5; (5)未来什么具体证据会改变此裁决(<=30字)。"
    '最后一行输出严格JSON: {"stance":"同意引擎|减半执行|暂缓","conviction":N}'}],
    model=os.environ.get('GLM_JUDGE_MODEL', 'glm-4.7'),
    max_tokens=700, temperature=0.2, timeout=90, thinking=False)
print('\n⚖️ JUDGE:\n' + judge)

# parse stance for the log
stance, conv = '?', 0
try:
    tail = judge[judge.rfind('{'):judge.rfind('}')+1]
    j = json.loads(tail)
    stance, conv = j.get('stance', '?'), int(j.get('conviction', 0))
except Exception:
    pass
with open(os.path.join(V15, 'data', 'council_log.jsonl'), 'a') as f:
    f.write(json.dumps({'date': str(datetime.now())[:16], 'code': code, 'mkt': mkt,
        'engine_action': r['action'], 'composite': r['composite'], 'bucket': r['bucket'],
        'price': float(px.iloc[-1]), 'stance': stance, 'conviction': conv},
        ensure_ascii=False) + '\n')
print(f'\n[logged → council_log.jsonl: stance={stance} conviction={conv} @ {float(px.iloc[-1]):.2f} — outcome grading after ~3 months]')
