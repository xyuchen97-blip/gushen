# guts/utils/normalizer.py
"""
Shared stock name resolver — alias table + fuzzy matching + LLM fallback.
Product-agnostic: Gushen and BitBrave use the same engine.

Usage:
    norm = create_normalizer()
    result = norm.resolve("茅台")       # → 600519.SH
    result = norm.resolve("nvidia")      # → NVDA
    result = norm.resolve("s&p 500")     # → SPY (BitBrave)
    result = norm.resolve("money market") # → SHV (MMF)
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Tuple, Callable


class AssetType(Enum):
    STOCK = "stock"
    ETF = "etf"
    BOND_ETF = "bond_etf"
    MMF = "mmf"
    INDEX = "index"
    UNKNOWN = "unknown"


@dataclass
class AssetEntry:
    """A single asset in the resolution universe."""
    ticker: str
    market: str                      # US, HK, A
    name: str                        # Display name
    asset_type: AssetType = AssetType.STOCK
    aliases: List[str] = field(default_factory=list)
    sector: Optional[str] = None     # For A-stocks (消费, 医药, etc.)
    style: Optional[str] = None      # growth, value, cyclical, defensive, blend
    cap_size: Optional[str] = None   # large, mid, small (v9.8: dual-dimension classification)

    def all_search_terms(self) -> List[str]:
        return [self.ticker.lower(), self.name.lower()] + [a.lower() for a in self.aliases]


@dataclass
class ResolveResult:
    ticker: str
    market: str
    name: str
    asset_type: AssetType = AssetType.STOCK
    confidence: float = 1.0
    matched_alias: str = ""
    candidates: Optional[List[Tuple[str, str, float]]] = None


class TickerNormalizer:
    """Multi-stage stock name resolver."""
    
    def __init__(self):
        self._entries: Dict[str, AssetEntry] = {}
        self._alias_index: Dict[str, List[str]] = {}
        self._llm_resolver: Optional[Callable] = None
    
    def register(self, entry: AssetEntry):
        self._entries[entry.ticker] = entry
        for term in entry.all_search_terms():
            key = term.strip()
            if key not in self._alias_index:
                self._alias_index[key] = []
            if entry.ticker not in self._alias_index[key]:
                self._alias_index[key].append(entry.ticker)
    
    def register_many(self, entries: List[AssetEntry]):
        for e in entries:
            self.register(e)
    
    def set_llm_resolver(self, resolver: Callable):
        self._llm_resolver = resolver
    
    def resolve(self, query: str, market: Optional[str] = None,
                use_llm: bool = True) -> Optional[ResolveResult]:
        if not query or not query.strip():
            return None
        cleaned = query.strip()
        
        result = self._exact_match(cleaned, market)
        if result: return result
        
        result = self._normalized_match(cleaned, market)
        if result: return result
        
        result = self._fuzzy_match(cleaned, market)
        if result: return result
        
        if use_llm and self._llm_resolver:
            return self._llm_resolver(cleaned, market)
        return None
    
    def resolve_or_passthrough(self, query: str, market: Optional[str] = None) -> str:
        result = self.resolve(query, market, use_llm=False)
        return result.ticker if result else query.strip().upper()
    
    def reverse_lookup(self, ticker: str) -> Optional[str]:
        entry = self._entries.get(ticker) or self._entries.get(ticker.upper())
        return entry.name if entry else None
    
    def get_asset_type(self, ticker: str) -> AssetType:
        entry = self._entries.get(ticker) or self._entries.get(ticker.upper())
        return entry.asset_type if entry else AssetType.UNKNOWN
    
    def get_entry(self, ticker: str) -> Optional[AssetEntry]:
        return self._entries.get(ticker) or self._entries.get(ticker.upper())
    
    def _exact_match(self, query: str, market: Optional[str]) -> Optional[ResolveResult]:
        key = query.lower().strip()
        tickers = self._alias_index.get(key, [])
        if not tickers: return None
        if market and len(tickers) > 1:
            filtered = [t for t in tickers if self._entries[t].market == market]
            if filtered: tickers = filtered
        if len(tickers) == 1:
            e = self._entries[tickers[0]]
            return ResolveResult(e.ticker, e.market, e.name, e.asset_type, 1.0, key)
        e = self._entries[tickers[0]]
        return ResolveResult(e.ticker, e.market, e.name, e.asset_type, 0.8, key)
    
    def _normalized_match(self, query: str, market: Optional[str]) -> Optional[ResolveResult]:
        normalized = re.sub(r'[\s\.\-\_\(\)（）]', '', query.lower())
        normalized = re.sub(r'(股份|有限公司|集团|控股|科技|股票|shares?|corp|inc|ltd)', '', normalized)
        if not normalized: return None
        tickers = self._alias_index.get(normalized, [])
        if tickers:
            if market:
                filtered = [t for t in tickers if self._entries[t].market == market]
                if filtered: tickers = filtered
            e = self._entries[tickers[0]]
            return ResolveResult(e.ticker, e.market, e.name, e.asset_type, 0.95, normalized)
        return None
    
    def _fuzzy_match(self, query: str, market: Optional[str]) -> Optional[ResolveResult]:
        q = query.lower()
        best, best_score = None, 0
        for alias, tickers in self._alias_index.items():
            if len(alias) < 2: continue
            score = 0
            if q in alias: score = len(q) / len(alias)
            elif alias in q: score = len(alias) / len(q) * 0.9
            if score > best_score and score > 0.4:
                if market:
                    mt = [t for t in tickers if self._entries[t].market == market]
                    if mt: best, best_score = mt[0], score
                elif tickers:
                    best, best_score = tickers[0], score
        if best:
            e = self._entries[best]
            return ResolveResult(e.ticker, e.market, e.name, e.asset_type, round(best_score*0.9, 2), q)
        return None


# ─── Pre-built Universes ───────────────────────────────────────

GUSHEN_UNIVERSE: List[AssetEntry] = [
    AssetEntry('GOOGL','US','Alphabet Inc', aliases=['google','alphabet','谷歌','goog'], style='growth', cap_size='large'),
    AssetEntry('NVDA','US','NVIDIA Corp', aliases=['nvidia','英伟达','nv'], style='growth', cap_size='large'),
    AssetEntry('MSFT','US','Microsoft Corp', aliases=['microsoft','微软'], style='growth', cap_size='large'),
    AssetEntry('AAPL','US','Apple Inc', aliases=['apple','苹果'], style='blend', cap_size='large'),
    AssetEntry('AMZN','US','Amazon.com Inc', aliases=['amazon','亚马逊'], style='growth', cap_size='large'),
    AssetEntry('META','US','Meta Platforms Inc', aliases=['meta','facebook','fb'], style='growth', cap_size='large'),
    AssetEntry('JPM','US','JPMorgan Chase', aliases=['jpmorgan','摩根大通'], style='value', cap_size='large'),
    AssetEntry('0700.HK','HK','腾讯控股', aliases=['腾讯','tencent','700'], style='growth', cap_size='large'),
    AssetEntry('9988.HK','HK','阿里巴巴', aliases=['阿里','alibaba','baba','9988'], style='growth', cap_size='large'),
    AssetEntry('3690.HK','HK','美团', aliases=['美团','meituan','3690'], style='growth', cap_size='mid'),
    AssetEntry('1810.HK','HK','小米集团', aliases=['小米','xiaomi','1810'], style='cyclical', cap_size='large'),
    AssetEntry('1211.HK','HK','比亚迪股份', aliases=['比亚迪hk','byd hk','1211'], style='cyclical', cap_size='large'),
    AssetEntry('0388.HK','HK','香港交易所', aliases=['港交所','hkex','388'], style='value', cap_size='large'),
    AssetEntry('600519.SH','A','贵州茅台', aliases=['茅台','moutai','600519'], sector='消费', style='defensive', cap_size='large'),
    AssetEntry('300750.SZ','A','宁德时代', aliases=['宁德','catl','300750'], sector='新能源', style='growth', cap_size='large'),
    AssetEntry('002594.SZ','A','比亚迪', aliases=['比亚迪','byd a','002594'], sector='新能源车', style='cyclical', cap_size='large'),
    AssetEntry('601318.SH','A','中国平安', aliases=['平安','ping an','601318'], sector='金融', style='value', cap_size='large'),
    AssetEntry('000858.SZ','A','五粮液', aliases=['五粮液','000858'], sector='消费', style='defensive', cap_size='large'),
    AssetEntry('600036.SH','A','招商银行', aliases=['招行','cmb','600036'], sector='金融', style='value', cap_size='large'),
    AssetEntry('002230.SZ','A','科大讯飞', aliases=['讯飞','002230'], sector='科技', style='growth', cap_size='mid'),
    AssetEntry('300015.SZ','A','爱尔眼科', aliases=['爱尔','300015'], sector='医药', style='growth', cap_size='mid'),
]


def create_normalizer(include_gushen: bool = True):
    norm = TickerNormalizer()
    if include_gushen:
        norm.register_many(GUSHEN_UNIVERSE)
    return norm
