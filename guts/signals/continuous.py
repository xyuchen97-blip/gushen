# guts/signals/continuous.py
"""
CONTINUOUS SIGNALS — v10 Architecture (updated May 20, 2026, originally v9.8)
======================================================
Replaces hard-coded integer bonuses with smooth [-1, +1] signal strengths.

Each signal is scored continuously based on raw indicator values,
producing wider score variance and better separation between "mild" and "strong" buys.

v9.8 changes:
  - Accepts precomputed dict directly (from scoring.precompute)
  - Added bb_sell_strength, chain_resonance, trend_hold signals
  - DZH binary signals integrated as 0/+1 with lower weight
  - Macro signals integrated from guts.macro.compute
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class SignalStrength:
    """Single continuous signal with confidence."""
    name: str
    value: float       # -1 to +1, signed (positive = bullish)
    category: str      # trend, contrarian, momentum, volume, capital, macro
    weight: float      # within-category weight (default 1.0)
    raw_value: Optional[float] = None  # original indicator value


class ContinuousSignals:
    """
    Vessel for continuous signal computation.
    
    v9.8: Receives precomputed dict from scoring.precompute() and bar index.
    Extracts all signals in continuous [-1, +1] format.
    """
    
    results: Dict[str, SignalStrength]
    
    def __init__(self):
        self.results = {}
    
    def compute(self, precomputed: dict, i: int,
                macro_data: dict = None, bar_date=None,
                market: str = "US") -> Dict[str, SignalStrength]:
        """
        Extract all continuous signals for bar i.
        
        Parameters
        ----------
        precomputed : dict from scoring.precompute()
        i : int — bar index
        macro_data : dict from data_fetcher.fetch_macro_data()
        bar_date : datetime-like — current bar date
        market : str — "A", "HK", "US"
        """
        self.results = {}
        
        # ── Contrarian (DZH) — binary, weight=0.5 (reduced from 1.0) ──
        self._score_dzh(precomputed, i)
        
        # ── Momentum signals ──
        self._score_rsi(precomputed, i)
        self._score_kdj(precomputed, i)
        self._score_macd(precomputed, i)
        
        # ── Trend signals ──
        self._score_adx(precomputed, i)
        self._score_ma_alignment(precomputed, i)
        self._score_bull_regime(precomputed, i)
        
        # ── Contrarian / BB signals ──
        self._score_bb_buy(precomputed, i)
        self._score_bb_sell(precomputed, i)       # NEW: replaces post-hoc BB sell patch
        self._score_divergence(precomputed, i)
        
        # ── Volume signals ──
        self._score_volume(precomputed, i)
        
        # ── Resonance signals ──
        self._score_chain(precomputed, i)          # NEW: replaces post-hoc chain patch
        self._score_fib_combo(precomputed, i)
        
        # ── Capital flow signals ──
        self._score_capital(precomputed, i, macro_data, bar_date, market)
        
        # ── Macro signals ──
        if macro_data and bar_date is not None:
            self._score_macro(macro_data, bar_date, market, precomputed, i)
        
        return self.results
    
    def _add(self, name: str, value: float, category: str,
             weight: float = 1.0, raw: Optional[float] = None):
        value = max(-1.0, min(1.0, value))
        self.results[name] = SignalStrength(name, value, category, weight, raw)
    
    # ═══════════════════════════════════════════════════════════════
    # DZH Indicators (binary, reduced weight)
    # ═══════════════════════════════════════════════════════════════
    
    def _score_dzh(self, pre: dict, i: int):
        """DZH contrarian signals — binary (0 or +1), high-conviction weight=1.5."""
        # High-conviction DZH signals (rare but strong) — weight=1.5
        # Medium-conviction DZH signals — weight=1.0
        if pre["golden_pit"].iloc[i] != 0:
            self._add("golden_pit", 1.0, "contrarian", 1.5)  # high conviction
        if pre["band_low"].iloc[i] != 0:
            self._add("band_low", 0.5, "contrarian", 0.8)
        if pre["buy_signal"].iloc[i]:
            self._add("nine_turns_buy", 1.0, "contrarian", 1.5)  # high conviction
        if pre["buy_setup_done"].iloc[i]:
            self._add("nine_turns_setup9", 0.5, "contrarian", 0.8)
        if pre["buy2"].iloc[i]:
            self._add("band_king_buy2", 1.0, "contrarian", 1.5)  # high conviction
        # DZH sell signals — negative (high conviction, same weight as buys)
        if pre["sell_signal"].iloc[i]:
            self._add("nine_turns_sell", -1.0, "contrarian", 1.5)
        if pre["sell1"].iloc[i]:
            self._add("band_king_sell1", -1.0, "contrarian", 1.5)
    
    # ═══════════════════════════════════════════════════════════════
    # Momentum signals (continuous)
    # ═══════════════════════════════════════════════════════════════
    
    def _score_rsi(self, pre: dict, i: int):
        """RSI-based contrarian signal. Oversold (<30) = +1, overbought (>70) = -1."""
        if "kdj_j" not in pre: return
        # Approximate RSI from KDJ context — use K value as RSI proxy
        # Full RSI would need separate calculation; for now use KDJ K
        k = float(pre["kdj_k"].iloc[i]) if pd.notna(pre["kdj_k"].iloc[i]) else None
        if k is None: return
        # KDJ K is similar range to RSI
        val = 1.0 - 2.0 * (k / 100.0)  # K=30→+0.4, K=50→0, K=70→-0.4
        if k < 20: val = min(1.0, val * 1.5)   # deep oversold boost
        if k > 80: val = max(-1.0, val * 1.5)   # deep overbought penalty
        self._add("rsi_proxy", val, "momentum", 1.0, k)
    
    def _score_kdj(self, pre: dict, i: int):
        """KDJ golden cross / oversold as continuous signal."""
        j = float(pre["kdj_j"].iloc[i]) if pd.notna(pre["kdj_j"].iloc[i]) else None
        if j is None: return
        # J value → continuous: J<0 oversold (+1), J>100 overbought (-1)
        val = 1.0 - 2.0 * (j / 100.0)
        # Golden cross: K crosses above D
        if pre["kdj_golden"].iloc[i]:
            val = max(val, 0.5)  # boost on golden cross
        # Oversold zone: J<20
        if pre["kdj_oversold"].iloc[i]:
            val = max(val, 0.8)  # strong contrarian buy
        self._add("kdj", val, "momentum", 1.0, j)
    
    def _score_macd(self, pre: dict, i: int):
        """MACD histogram as continuous trend signal."""
        hist = float(pre["macd_hist"].iloc[i]) if pd.notna(pre["macd_hist"].iloc[i]) else None
        if hist is None: return
        # Normalize MACD histogram by recent price range
        val = np.tanh(hist * 3)  # sigmoid: mild histogram → small signal
        # Golden cross boost
        if pre["macd_golden"].iloc[i]:
            val = max(val, 0.6)
        # Death cross penalty
        if pre["macd_death"].iloc[i]:
            val = min(val, -0.6)
        self._add("macd", val, "momentum", 1.0, hist)
    
    # ═══════════════════════════════════════════════════════════════
    # Trend signals (continuous)
    # ═══════════════════════════════════════════════════════════════
    
    def _score_adx(self, pre: dict, i: int):
        """ADX trend strength — always positive (0 to +1)."""
        if "adx_strong" not in pre: return
        # ADX is a boolean in precomputed; extract the raw DX if available
        # For now, use boolean: adx_strong = +0.8, else +0.3 (ADX always has some value)
        if pre["adx_strong"].iloc[i]:
            self._add("adx_trend", 0.8, "trend", 1.0)
        else:
            self._add("adx_trend", 0.3, "trend", 0.6)  # weak trend, lower weight
    
    def _score_ma_alignment(self, pre: dict, i: int):
        """MA alignment as trend signal."""
        bull = bool(pre["bull_regime"].iloc[i])
        # MA20>MA60>MA120 = strong alignment
        if pre["ma_aligned"].iloc[i]:
            val = 0.8
        elif bull:
            val = 0.3
        else:
            val = -0.4
        # Golden/death cross
        if pre["ma_golden"].iloc[i]:
            val = max(val, 0.6)
        if pre["ma_death"].iloc[i]:
            val = min(val, -0.5)
        self._add("ma_align", val, "trend", 1.0)
    
    def _score_bull_regime(self, pre: dict, i: int):
        """
        Trend-hold signal — replaces v9.7 post-hoc trend-override.
        In strong bull (price>MA200 AND ADX strong), signal +1 (hold don't exit).
        This signal flows THROUGH the pipeline instead of patching the decision.
        """
        bull = bool(pre["bull_regime"].iloc[i])
        adx_strong = bool(pre["adx_strong"].iloc[i]) if "adx_strong" in pre else False
        
        if bull and adx_strong:
            self._add("trend_hold", 1.0, "trend", 0.8)   # strong bull, moderate weight
        elif bull:
            self._add("trend_hold", 0.4, "trend", 0.4)   # mild bull, lower weight
        else:
            self._add("trend_hold", -0.2, "trend", 0.3)  # bear, low weight
    
    # ═══════════════════════════════════════════════════════════════
    # Bollinger Band signals (continuous)
    # ═══════════════════════════════════════════════════════════════
    
    def _score_bb_buy(self, pre: dict, i: int):
        """BB weekly buy signal — contrarian bullish."""
        if pre["bb_buy"].iloc[i]:
            self._add("bb_buy", 0.8, "contrarian", 1.0)
    
    def _score_bb_sell(self, pre: dict, i: int):
        """
        BB sell strength — replaces v9.0 post-hoc BB graded sell.
        Graded by context: strong trend + vol burst = mild sell, weak = strong sell.
        """
        if not pre["bb_sell"].iloc[i]:
            return
        # Graded: in strong trend, BB sell is weaker (price may keep rising)
        adx_strong = bool(pre["adx_strong"].iloc[i]) if "adx_strong" in pre else False
        above_ma50 = bool(pre["price_above_ma50"].iloc[i])
        vol_burst = bool(pre["vol_anomaly"].iloc[i])
        
        if adx_strong and above_ma50 and vol_burst:
            self._add("bb_sell", -0.3, "contrarian", 0.8)   # mild: strong trend absorbing
        elif adx_strong or above_ma50:
            self._add("bb_sell", -0.5, "contrarian", 0.9)   # moderate
        else:
            self._add("bb_sell", -0.8, "contrarian", 1.0)   # strong: no trend support
    
    def _score_divergence(self, pre: dict, i: int):
        """Bullish divergence signal."""
        if pre["bullish_divergence"].iloc[i]:
            self._add("divergence", 0.9, "contrarian", 1.2)  # high conviction
    
    # ═══════════════════════════════════════════════════════════════
    # Volume signals (continuous)
    # ═══════════════════════════════════════════════════════════════
    
    def _score_volume(self, pre: dict, i: int):
        """Volume anomaly as confirming signal."""
        if pre["vol_anomaly"].iloc[i]:
            # High volume confirms the direction of other signals
            # Value depends on whether other signals are bullish or bearish
            # For now: positive (confirms the move)
            self._add("volume_confirm", 0.6, "volume", 0.8)
        # National team
        if pre.get("national_team") is not None and pre["national_team"].iloc[i]:
            self._add("national_team", 0.7, "volume", 0.6)
    
    # ═══════════════════════════════════════════════════════════════
    # Resonance signals (continuous) — replaces post-hoc chain logic
    # ═══════════════════════════════════════════════════════════════
    
    def _score_chain(self, pre: dict, i: int):
        """
        Chain resonance (BOLL→KDJ→MACD) — continuous.
        C2 = BB buy then KDJ fires = +0.7
        C3 = BB buy then KDJ then MACD = +1.0
        Window selection based on ADX (same as v9.2).
        """
        chain_window = 5
        if pre["adx_strong"].iloc[i]:
            chain_window = 3
        elif i >= 30 and not pre["adx_strong"].iloc[i-30:i].any() if hasattr(pre["adx_strong"], 'iloc') else False:
            chain_window = 8
        
        c2_key = f"chain_c2_w{chain_window}"
        c3_key = f"chain_c3_w{chain_window}"
        
        if c3_key in pre and pre[c3_key][i]:
            self._add("chain_c3", 1.0, "momentum", 1.3)  # highest conviction
        elif c2_key in pre and pre[c2_key][i]:
            self._add("chain_c2", 0.7, "momentum", 1.0)
    
    def _score_fib_combo(self, pre: dict, i: int):
        """Fibonacci support + divergence/KDJ combo."""
        fib = bool(pre["weekly_fib_support"].iloc[i]) if "weekly_fib_support" in pre else False
        div = bool(pre["bullish_divergence"].iloc[i])
        oversold = bool(pre["kdj_oversold"].iloc[i])
        
        if fib and div:
            self._add("fib_div_combo", 1.0, "contrarian", 1.2)
        elif fib and oversold:
            self._add("fib_kdj_combo", 0.8, "contrarian", 1.0)
        elif fib:
            self._add("fib_support", 0.4, "contrarian", 0.5)
    
    # ═══════════════════════════════════════════════════════════════
    # Capital flow signals
    # ═══════════════════════════════════════════════════════════════
    
    def _score_capital(self, pre: dict, i: int,
                       macro_data: dict, bar_date, market: str):
        """Capital flow signals — volume, margin, chip, holder."""
        # Triple confirm (contrarian ∩ volume ∩ momentum)
        from strategy.elliot_wave import triple_confirm
        tc = triple_confirm(pre, i)
        if tc["triple_confirm"]:
            self._add("triple_confirm", 0.3, "capital", 0.5)
        
        # A-stock specific capital signals
        if market == "A" and macro_data:
            # Margin financing
            if "margin" in macro_data and bar_date in macro_data.get("margin", {}):
                mr = macro_data["margin"][bar_date]
                pct5 = mr.get("pct_5d", 0)
                if pct5 > 5:  # v9.8 B2 fix: >5% = extreme (most overheated)
                    self._add("margin_extreme", -0.8, "capital", 1.0)
                elif pct5 > 2:  # >2% = overheat (moderate)
                    self._add("margin_overheat", -0.5, "capital", 0.8)
                elif pct5 < -5:
                    self._add("margin_panic", 0.4, "capital", 0.6)
            
            # Chip concentration
            if "chip_conc" in macro_data:
                cc = macro_data["chip_conc"]
                if cc > 22:
                    self._add("chip_tight", 0.6, "capital", 0.8)
                elif cc < 12:
                    self._add("chip_loose", -0.4, "capital", 0.6)
            
            # Holder change
            if "holder_chg" in macro_data:
                hc = macro_data["holder_chg"]
                if hc < -0.03:
                    self._add("holder_consolidate", 0.4, "capital", 0.6)
                elif hc > 0.05:
                    self._add("holder_dilute", -0.4, "capital", 0.6)
            
            # Main force flow
            if "mff" in macro_data and macro_data.get("a_sector") == "growth":
                if bar_date in macro_data.get("mff", {}):
                    mf = macro_data["mff"][bar_date]
                    avg_super = mf.get("super_ratio", 0)
                    avg_mf = mf.get("mf_ratio", 0)
                    if avg_super > 3:
                        self._add("mff_strong", 0.9, "capital", 0.8)
                    elif avg_mf > 2:
                        self._add("mff_moderate", 0.5, "capital", 0.6)
                    elif avg_mf < -8:
                        self._add("mff_sell", -0.6, "capital", 0.7)
            
            # Northbound flow
            if "northbound_flow" in macro_data:
                nb = macro_data["northbound_flow"]
                if hasattr(nb, 'index'):
                    nb_vals = nb[nb.index <= bar_date]
                    if len(nb_vals) > 0 and float(nb_vals.iloc[-1]) > 0:
                        self._add("northbound", 0.5, "capital", 0.6)
    
    # ═══════════════════════════════════════════════════════════════
    # Macro signals (delegated to guts.macro.compute)
    # ═══════════════════════════════════════════════════════════════
    
    def _score_macro(self, macro_data: dict, bar_date, market: str,
                     pre: dict, i: int):
        """Macro signals — all continuous [-1, +1] from guts.macro."""
        from guts.macro.compute import (score_vix, score_yield_curve, score_cpi_direction,
                                         score_unemployment, score_lpr, score_pmi,
                                         score_m2_growth, score_qvix, score_usdcny)
        
        def _safe_val(key):
            if macro_data is None or key not in macro_data: return None
            series = macro_data.get(key)
            if series is None: return None
            if isinstance(series, (int, float)): return float(series) if pd.notna(series) else None
            if isinstance(series, pd.Series):
                vals = series[series.index <= bar_date]
                if len(vals) > 0 and pd.notna(vals.iloc[-1]): return float(vals.iloc[-1])
            return None
        
        def _prev_val(key):
            """Get previous value for direction calculations."""
            series = macro_data.get(key)
            if series is None or not isinstance(series, pd.Series): return None
            vals = series[series.index <= bar_date]
            if len(vals) >= 2 and pd.notna(vals.iloc[-2]): return float(vals.iloc[-2])
            return None
        
        # VIX (all markets)
        vix = _safe_val('vix')
        if vix is not None:
            s = score_vix(vix)
            if s is not None: self._add("macro_vix", s, "macro", 1.0, vix)
        
        # Yield curve spread (all markets)
        spread = _safe_val('us_spread_10y2y')
        if spread is not None:
            s = score_yield_curve(spread)
            if s is not None: self._add("macro_yield", s, "macro", 0.8, spread)
        
        # US-specific
        if market == "US":
            cpi = _safe_val('us_cpi_yoy')
            if cpi is not None:
                cpi_prev = _prev_val('us_cpi_yoy') or cpi
                s = score_cpi_direction(cpi, cpi_prev)
                if s is not None: self._add("macro_cpi", s, "macro", 0.6, cpi)
            
            unemp = _safe_val('us_unemployment')
            if unemp is not None:
                s = score_unemployment(unemp)
                if s is not None: self._add("macro_unemp", s, "macro", 0.4, unemp)
        
        # CN-specific (A + HK)
        if market in ("A", "CN_IDX", "HK"):
            lpr = _safe_val('china_lpr1y')
            if lpr is not None:
                lpr_prev = _prev_val('china_lpr1y') or lpr
                s = score_lpr(lpr, lpr_prev)
                if s is not None: self._add("macro_lpr", s, "macro", 0.8, lpr)
            
            pmi = _safe_val('china_pmi')
            if pmi is not None:
                s = score_pmi(pmi)
                if s is not None: self._add("macro_pmi", s, "macro", 0.6, pmi)
            
            m2 = _safe_val('china_m2_yoy')
            if m2 is not None:
                s = score_m2_growth(m2)
                if s is not None: self._add("macro_m2", s, "macro", 0.6, m2)
            
            qvix = _safe_val('china_qvix')
            if qvix is not None:
                s = score_qvix(qvix)
                if s is not None: self._add("macro_qvix", s, "macro", 0.8, qvix)
            
            usdcny = _safe_val('usdcny')
            if usdcny is not None:
                usdcny_series = macro_data.get('usdcny')
                usdcny_ma = usdcny
                if isinstance(usdcny_series, pd.Series):
                    vals = usdcny_series[usdcny_series.index <= bar_date]
                    if len(vals) >= 20:
                        usdcny_ma = float(vals.tail(20).mean())
                s = score_usdcny(usdcny, usdcny_ma)
                if s is not None: self._add("macro_usdcny", s, "macro", 0.6, usdcny)
    
    # ═══════════════════════════════════════════════════════════════
    # Aggregation helpers
    # ═══════════════════════════════════════════════════════════════
    
    def category_summary(self) -> Dict[str, float]:
        """Weighted average signal strength by category."""
        cats = {}
        for r in self.results.values():
            if r.category not in cats:
                cats[r.category] = []
            cats[r.category].append((r.value, r.weight))
        return {k: float(np.average([v for v, _ in vals], weights=[w for _, w in vals]))
                if sum(w for _, w in vals) > 0 else 0.0
                for k, vals in cats.items()}
    
    def composite(self, market_weights: dict = None) -> float:
        """
        Weighted composite score using market weights.
        Returns value in [-1, +1] range (approximately).
        """
        if not self.results:
            return 0.0
        
        cats = self.category_summary()
        w = market_weights or {"technical": 36, "capital": 26, "fundamental": 14, "macro": 19, "fibonacci": 5}
        
        total = 0.0
        wsum = 0.0
        cat_map = {
            "contrarian": "technical", "momentum": "technical", "trend": "technical",
            "volume": "capital", "capital": "capital",
            "macro": "macro",
        }
        for r in self.results.values():
            factor = cat_map.get(r.category, r.category)
            factor_w = w.get(factor, 5)
            total += r.value * r.weight * factor_w
            wsum += r.weight * factor_w
        
        return total / wsum if wsum > 0 else 0.0
