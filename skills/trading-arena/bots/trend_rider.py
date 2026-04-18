"""Trend Rider — Pullback Trend Following Strategy (TAY v2).

UPGRADED 2026-04-12 to use S/R + candlestick patterns from the YouTube digest:
- T (Trend): 200 MA filter + EMA21 > EMA50 (combines 14 + 8 strategies)
- A (Area of Value): Pullback to S/R level OR 50 EMA confluence
- Y (Trigger): Hammer or Bullish Engulfing candlestick at the value zone

Exit: ATR-based stop loss + take profit (used by 23 strategies in the digest)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from shared.indicators import at_support, nearest_sr_level
from typing import Dict, List, Optional


class TrendRider(BaseBot):
    NAME = "Trend Rider"
    BOT_ID = "trend-rider"
    DESCRIPTION = "Pullback trend following — enters on dips in strong uptrends"

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.ema_21 is None or d.ema_50 is None:
                continue
            # Strong uptrend: EMA alignment
            if (d.ema_21 > d.ema_50 and d.price > d.ema_50 and
                (d.adx_14 is None or d.adx_14 > 20)):
                # Pulling back to 21 EMA (within 1%)
                if d.ema_21 and abs(d.price - d.ema_21) / d.ema_21 < 0.01:
                    candidates.append(sym)
        return candidates

    # === TAY Framework v2 — S/R + candlestick edition ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: 200 MA filter + EMA21 > EMA50 (price above both = uptrend)."""
        if data.ema_200 is None or data.ema_50 is None or data.ema_21 is None:
            return (False, "no ema data")
        # 200 MA filter: price must be above 200 EMA
        if data.price <= data.ema_200:
            return (False, "below EMA200")
        # EMA hierarchy: 21 > 50
        if data.ema_21 <= data.ema_50:
            return (False, "EMA21 < EMA50")
        return (True, "200MA up + EMA21>50")

    def check_value(self, data: AssetData) -> tuple:
        """A: Pullback to 50 EMA OR horizontal support level."""
        # Check for pullback to 50 EMA (within 1.5%)
        ema_pullback = False
        if data.ema_50:
            dist_pct = abs(data.price - data.ema_50) / data.ema_50
            if dist_pct < 0.015:
                ema_pullback = True

        # Check for horizontal support level nearby
        support_nearby = at_support(data.price, data.sr_levels, max_distance_pct=0.012)

        if ema_pullback and support_nearby:
            return (True, "S/R + 50EMA confluence")
        if support_nearby:
            level = nearest_sr_level(data.price, data.sr_levels)
            return (True, f"at support ${level[0]:.2f} ({level[3]} touches)")
        if ema_pullback:
            return (True, f"pullback to 50EMA")
        return (False, "no value zone")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Hammer or Bullish Engulfing candle at the value zone."""
        pattern = data.candlestick_pattern
        if pattern in ("hammer", "bullish_engulfing"):
            return (True, f"{pattern} signal")
        return (False, f"no reversal pattern (got {pattern or 'none'})")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        # ATR-based stop loss (1x ATR below entry) and take profit (2x ATR above)
        if data.atr_14 and data.atr_14 > 0:
            atr_pct = (data.atr_14 / entry) * 100
            atr_stop_pct = -atr_pct  # 1x ATR
            atr_target_pct = atr_pct * 2  # 2x ATR (2:1 R:R)
            if pnl_pct >= atr_target_pct:
                return f"ATR target +{pnl_pct:.1f}% (2x ATR)"
            if pnl_pct <= atr_stop_pct:
                return f"ATR stop {pnl_pct:.1f}% (1x ATR)"
        else:
            # Fallback to fixed % if no ATR
            if pnl_pct >= 3.0:
                return f"Take profit +{pnl_pct:.1f}%"
            if pnl_pct <= -1.5:
                return f"Stop loss {pnl_pct:.1f}%"

        # Trend invalidation exits
        if data.ema_21 and data.price < data.ema_21 * 0.99:
            return "Price broke below 21 EMA"
        if data.ema_21 and data.ema_50 and data.ema_21 < data.ema_50:
            return "Trend reversal: EMA21 < EMA50"
        return None
