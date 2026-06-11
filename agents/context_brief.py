"""v16 knowledge layer — distill expert sources into a structured market-context brief.

Sources (owner-provided, June 2026): Citadel Securities Market Insights, Man Group
Insights, SemiAnalysis, Bridgewater. These are ESSAYS, not data: they NEVER touch the
quant model (no backtestable history → would violate causality discipline). Their home
is the GLM layer: a fortnightly brief that grounds deep-dive and sentinel prompts in
current expert thinking instead of the model's training-data priors.

Authority: context only. The brief may inform caution; it may never justify adding risk.

Usage: python3 agents/context_brief.py      # fetch → distill → data/market_context.json
Run weekly (the Saturday scheduled task) or whenever you want a fresh brief.
"""
import os, sys, re, json
from datetime import datetime
import requests

V15 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, V15)
import strategy.gushen_keys  # noqa: F401
from agents.glm_client import glm_chat

OUT = os.path.join(V15, 'data', 'market_context.json')
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}

# RSS preferred (reliable); page-scrape fallback (bot defenses make it flaky from
# servers — usually works from a residential IP, i.e., the owner's machine);
# data/context_inbox/ manual drops ALWAYS work and are the only path for paywalled
# content (SemiAnalysis premium, Bridgewater Daily Observations).
SOURCES = {
    'semianalysis': {'rss': 'https://semianalysis.com/feed/', 'take': 2},
    'citadel_securities': {
        'list': 'https://www.citadelsecurities.com/news-and-insights/category/market-insights/',
        'link_re': r'href="(https://www\.citadelsecurities\.com/news-and-insights/[a-z0-9\-]+/)"',
        'take': 2},
    'man_group': {
        'list': 'https://www.man.com/insights',
        'link_re': r'href="([^"]*/insights/[a-z0-9][^"]*)"',
        'base': 'https://www.man.com', 'take': 2},
    'bridgewater': {
        'list': 'https://www.bridgewater.com/research-and-insights',
        'link_re': r'href="([^"]*research[^"]*/[a-z0-9\-]{8,})"',
        'base': 'https://www.bridgewater.com', 'take': 1},
}
INBOX = os.path.join(V15, 'data', 'context_inbox')


def fetch_rss(name, url, take):
    out = []
    try:
        xml = requests.get(url, headers=UA, timeout=25).text
        items = re.findall(r'<item>([\s\S]*?)</item>', xml)[:take]
        for it in items:
            t = re.search(r'<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</title>', it)
            c = re.search(r'<content:encoded>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</content:encoded>', it) \
                or re.search(r'<description>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</description>', it)
            l = re.search(r'<link>([\s\S]*?)</link>', it)
            if c:
                txt = strip_html(c.group(1))
                if len(txt) > 500:
                    title = strip_html(t.group(1)) if t else ''
                    out.append((l.group(1).strip() if l else url, f'{title}\n{txt[:4000]}'))
    except Exception as e:
        print(f'  {name} rss: {type(e).__name__}')
    return out


def read_inbox():
    """Manual drops: any .txt/.md file in data/context_inbox/ (paste articles here)."""
    out = []
    if os.path.isdir(INBOX):
        for fn in sorted(os.listdir(INBOX)):
            if fn.endswith(('.txt', '.md')):
                try:
                    txt = open(os.path.join(INBOX, fn), encoding='utf-8').read()
                    if len(txt) > 300:
                        out.append((f'inbox/{fn}', txt[:5000]))
                except Exception:
                    pass
    print(f'  inbox: {len(out)} documents')
    return out


def strip_html(html):
    html = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', html)
    txt = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', txt).strip()


def fetch_source(name, cfg):
    """Return list of (url, text_excerpt) for the newest articles of one source."""
    out = []
    try:
        lst = requests.get(cfg['list'], headers=UA, timeout=25).text
        links = []
        for m in re.findall(cfg['link_re'], lst):
            url = cfg.get('base', '') + m
            if url not in links and 'category' not in url and 'series' not in url:
                links.append(url)
        for url in links[:cfg.get('take', 2)]:
            try:
                art = requests.get(url, headers=UA, timeout=25).text
                txt = strip_html(art)
                if len(txt) > 800:
                    out.append((url, txt[:4000]))
            except Exception:
                continue
    except Exception as e:
        print(f'  {name}: fetch failed ({type(e).__name__})')
    print(f'  {name}: {len(out)} articles')
    return out


def main():
    os.makedirs(INBOX, exist_ok=True)
    docs = []
    for name, cfg in SOURCES.items():
        arts = fetch_rss(name, cfg['rss'], cfg.get('take', 2)) if 'rss' in cfg \
               else fetch_source(name, cfg)
        if 'rss' in cfg:
            print(f'  {name}: {len(arts)} articles (rss)')
        for url, txt in arts:
            docs.append(f'[SOURCE: {name} | {url}]\n{txt}')
    for url, txt in read_inbox():
        docs.append(f'[SOURCE: {url}]\n{txt}')
    if not docs:
        print('no sources fetched — brief not updated')
        return
    corpus = '\n\n────\n\n'.join(docs)[:24000]
    prompt = (
        "You are distilling recent institutional research into a structured market-context "
        "brief for a quantitative equity engine covering A-shares, Hong Kong, and US large "
        "caps. From the source excerpts below, extract ONLY what is actually stated — no "
        "speculation, no investment advice. Respond in strict JSON:\n"
        '{"macro_view": "<=60 words: rates/inflation/growth views expressed>",\n'
        ' "flows_liquidity": "<=40 words: positioning/flow/liquidity observations>",\n'
        ' "semis_ai": "<=50 words: semiconductor/AI supply-demand specifics if present>",\n'
        ' "china_hk": "<=40 words: China/HK-specific views if present>",\n'
        ' "risk_flags": ["<=15 words each, max 5 — concrete risks the sources warn about"],\n'
        ' "notable": "<=40 words: anything else decision-relevant"}\n\n'
        f"SOURCES:\n{corpus}")
    # default glm-4.7 (best extraction); CONTEXT_BRIEF_MODEL=glm-4-flash for speed
    model = os.environ.get('CONTEXT_BRIEF_MODEL', 'glm-4.7')
    txt = glm_chat([{"role": "user", "content": prompt}], model=model,
                   max_tokens=900, temperature=0.1, timeout=90, thinking=False)
    t = txt.strip()
    if t.startswith('```'):
        t = t.split('```')[1]
        if t.startswith('json'):
            t = t[4:]
    brief = json.loads(t)
    brief['as_of'] = str(datetime.now())[:10]
    brief['n_sources'] = len(docs)
    brief['urls'] = [d.split('|')[1].split(']')[0].strip() for d in docs]
    json.dump(brief, open(OUT, 'w'), ensure_ascii=False, indent=1)
    print(f"\nbrief written: {OUT} ({len(docs)} articles)")
    for k in ('macro_view', 'flows_liquidity', 'semis_ai', 'china_hk'):
        if brief.get(k):
            print(f'  {k}: {brief[k]}')
    for f in brief.get('risk_flags', []):
        print(f'  ⚠ {f}')


if __name__ == '__main__':
    main()
