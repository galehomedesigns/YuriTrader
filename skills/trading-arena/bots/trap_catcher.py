"""Trap Catcher — False Breakout Reversal (Contrarian) Strategy.

Catches exhaustion moves by fading extreme RSI readings.
Entry: RSI reverting from extreme (>75 dropping below 70, or <25 rising above 30) + declining volume
Exit: +3% TP, -1.5% SL, RSI returns to 50 mid-range
       (+2% raised to +3% on 2026-05-13 to clear ~0.8% round-trip Kraken taker fees with margin)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from typing import Dict, List, Optional


class TrapCatcher(BaseBot):
    NAME = "Trap Catcher"
    BOT_ID = "trap-catcher"
    DESCRIPTION = "False breakout reversal — fades exhaustion moves at RSI extremes"

    def _rsi_reverting_from_overbought(self, data: AssetData) -> bool:
        """RSI was > 75 and is now dropping below 70 (bearish reversal)."""
        if data.rsi_14 is None or not data.closes or len(data.closes) < 3:
            return False
        # Current RSI between 60-70 suggests it was recently higher
        return 60 <= data.rsi_14 <= 72

    def _rsi_reverting_from_oversold(self, data: AssetData) -> bool:
        """RSI was < 25 and is now rising above 30 (bullish reversal)."""
        if data.rsi_14 is None or not data.closes or len(data.closes) < 3:
            return False
        # Current RSI between 28-40 suggests it was recently lower
        return 28 <= data.rsi_14 <= 40

    def _volume_declining(self, data: AssetData) -> bool:
        """Check if volume is declining (exhaustion signal)."""
        if not data.volumes or len(data.volumes) < 5:
            return False
        recent = data.volumes[-3:]
        earlier = data.volumes[-5:-3]
        if not earlier or sum(earlier) == 0:
            return False
        return sum(recent) / len(recent) < sum(earlier) / len(earlier)

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.rsi_14 is None:
                continue
            # Extreme RSI — look for exhaustion
            if d.rsi_14 > 75 or d.rsi_14 < 25:
                candidates.append(sym)
            # Also catch assets already reverting from extremes
            elif self._rsi_reverting_from_overbought(d) or self._rsi_reverting_from_oversold(d):
                candidates.append(sym)
        return candidates

    # === TAY Framework ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Weakening trend — ADX dropping (exhaustion incoming)."""
        if data.adx_14 is None or data.adx_14 >= 30:
            return (False, f"ADX={data.adx_14 or 0:.0f} strong")
        return (True, f"weakening (ADX={data.adx_14:.0f})")

    def check_value(self, data: AssetData) -> tuple:
        """A: RSI was extreme and is now reverting (price overshot, snap-back)."""
        if self._rsi_reverting_from_oversold(data):
            return (True, f"OS reversal RSI={data.rsi_14:.0f}")
        if self._rsi_reverting_from_overbought(data):
            return (True, f"OB reversal RSI={data.rsi_14:.0f}")
        return (False, f"RSI={data.rsi_14 or 0:.0f} not reverting")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Volume declining (exhaustion) AND MACD momentum fading."""
        if not self._volume_declining(data):
            return (False, "vol still high")
        if data.macd_histogram is None or abs(data.macd_histogram) >= 0.5:
            return (False, "MACD still strong")
        return (True, "vol↓ + MACD fade")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        if pnl_pct >= 3.0:
            return f"Take profit +{pnl_pct:.1f}%"
        if pnl_pct <= -1.5:
            return f"Stop loss {pnl_pct:.1f}%"
        if data.rsi_14 and 45 <= data.rsi_14 <= 55:
            return f"RSI={data.rsi_14:.0f} returned to mid-range"
        return None
