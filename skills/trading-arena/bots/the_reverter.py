"""The Reverter — Mean Reversion Strategy (TAY v2).

UPGRADED 2026-04-12 to use horizontal support + engulfing patterns:
- T (Trend): Range-bound market (ADX < 20) — different bias from Trend Rider
- A (Area of Value): Horizontal support level (47 strategies use S/R)
- Y (Trigger): Bullish engulfing OR hammer at the support zone

Combo 2: Tests S/R + engulfing on RANGING markets (vs Trend Rider's S/R + hammer on TRENDING markets)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from shared.indicators import at_support, nearest_sr_level
from typing import Dict, List, Optional


class TheReverter(BaseBot):
    NAME = "The Reverter"
    BOT_ID = "the-reverter"
    DESCRIPTION = "Mean reversion — buys oversold, sells when price reverts to mean"

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.rsi_14 is None or d.bb_lower is None:
                continue
            # Oversold + at lower Bollinger + not trending hard
            if (d.rsi_14 < 35 and d.price <= (d.bb_lower or 0) * 1.02 and
                (d.adx_14 is None or d.adx_14 < 25)):
                candidates.append(sym)
        return candidates

    # === TAY Framework v2 — S/R + engulfing edition ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Range-bound market (ADX < 20) — mean reversion needs choppy markets."""
        if data.adx_14 is None or data.adx_14 >= 20:
            return (False, f"ADX={data.adx_14 or 0:.0f} trending")
        return (True, f"ranging (ADX={data.adx_14:.0f})")

    def check_value(self, data: AssetData) -> tuple:
        """A: Horizontal support level OR Bollinger lower band (S/R confluence)."""
        # Strongest signal: S/R support
        support_nearby = at_support(data.price, data.sr_levels, max_distance_pct=0.012)
        if support_nearby:
            level = nearest_sr_level(data.price, data.sr_levels)
            return (True, f"at support ${level[0]:.2f} ({level[3]} touches)")
        # Fallback: Bollinger lower band
        if data.bb_lower and data.price <= data.bb_lower * 1.01:
            return (True, "at BB lower band")
        return (False, "no support zone")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Bullish engulfing or hammer candle (the dominant reversal trigger)."""
        pattern = data.candlestick_pattern
        if pattern in ("bullish_engulfing", "hammer"):
            return (True, f"{pattern} reversal")
        return (False, f"no reversal pattern (got {pattern or 'none'})")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        if pnl_pct >= 1.5:
            return f"Take profit +{pnl_pct:.1f}%"
        if pnl_pct <= -2.0:
            return f"Stop loss {pnl_pct:.1f}%"
        if data.bb_middle and data.price >= data.bb_middle:
            return f"Reverted to mean (BB middle ${data.bb_middle:.2f})"
        if data.rsi_14 and data.rsi_14 > 60:
            return f"RSI recovered to {data.rsi_14:.0f}"
        return None
