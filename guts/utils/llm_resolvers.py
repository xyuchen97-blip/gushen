# guts/utils/llm_resolvers.py
"""LLM-based fallback resolvers for TickerNormalizer. Optional — normalizer works without them."""

import os, json
from typing import Optional
from .normalizer import ResolveResult, AssetType


def make_zhipu_resolver(api_key: Optional[str] = None):
    api_key = api_key or os.environ.get('ZHIPU_API_KEY')
    if not api_key: return None
    
    def resolve(query: str, market: Optional[str] = None) -> Optional[ResolveResult]:
        try:
            from zhipuai import ZhipuAI
            client = ZhipuAI(api_key=api_key)
            hint = f" in the {market} market" if market else ""
            prompt = f'''Resolve this stock name to a ticker{hint}.
Input: "{query}"
Reply in JSON: {{"ticker": "...", "market": "US|HK|A", "name": "...", "asset_type": "stock"}}
If unknown: {{"ticker": null}}'''
            resp = client.chat.completions.create(model="glm-4", messages=[{"role":"user","content":prompt}], temperature=0.1)
            text = resp.choices[0].message.content.strip()
            if '{' in text:
                d = json.loads(text[text.index('{'):text.rindex('}')+1])
                if d.get('ticker'):
                    return ResolveResult(d['ticker'], d.get('market','US'), d.get('name',query),
                                        AssetType(d.get('asset_type','stock')), 0.7, f"llm:{query}")
        except: pass
        return None
    return resolve


def make_openai_resolver(api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
    api_key = api_key or os.environ.get('OPENAI_API_KEY')
    if not api_key: return None
    
    def resolve(query: str, market: Optional[str] = None) -> Optional[ResolveResult]:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            hint = f" in the {market} market" if market else ""
            prompt = f'''Resolve to ticker{hint}. Input: "{query}"
Reply JSON only: {{"ticker":"...","market":"US|HK|A","name":"...","asset_type":"stock"}}
Unknown: {{"ticker":null}}'''
            resp = client.chat.completions.create(model=model, messages=[{"role":"user","content":prompt}], temperature=0.0)
            text = resp.choices[0].message.content.strip()
            if '{' in text:
                d = json.loads(text[text.index('{'):text.rindex('}')+1])
                if d.get('ticker'):
                    return ResolveResult(d['ticker'], d.get('market','US'), d.get('name',query),
                                        AssetType(d.get('asset_type','stock')), 0.7, f"llm:{query}")
        except: pass
        return None
    return resolve
