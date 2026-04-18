"""Flag Rider — Bull/Bear Flag Pattern Strategy.

Detects strong impulse moves (pole) followed by consolidation (flag),
then enters on the breakout from the flag pattern.
Entry: Strong impulse (>2% move) + consolidation + breakout with volume
Exit: +2% TP (pole projection), -1% SL, VWAP breakdown
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from typing import Dict, List, Optional


class FlagRider(BaseBot):
    NAME = "Flag Rider"
    BOT_ID = "flag-rider"
    DESCRIPTION = "Bull/bear flag pattern — enters on breakout from consolidation after impulse"

    def _detect_consolidation(self, data: AssetData) -> bool:
        """Check if recent price action shows consolidation (low range)."""
        if not data.closes or len(data.closes) < 10:
            return False
        recent = data.closes[-5:]
        if not recent:
            return False
        high = max(recent)
        low = min(recent)
        if low <= 0:
            return False
        # Tight range = consolidation (less than 1% range in last 5 bars)
        return (high - low) / low < 0.01

    def _breakout_detected(self, data: AssetData) -> bool:
        """Check if price is breaking out of consolidation range."""
        if not data.closes or len(data.closes) < 10:
            return False
        consolidation = data.closes[-6:-1]  # Previous 5 bars
        if not consolidation:
            return False
        consolidation_high = max(consolidation)
        # Current price above consolidation high
        return data.price > consolidation_high

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.day_change_pct is None:
                continue
            # Strong impulse move (pole) + volume
            if abs(d.day_change_pct) > 2.0 and (d.rvol or 0) > 1.5:
                candidates.append(sym)
        return candidates

    # === TAY Framework ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Strong impulse pole — >2% intraday move with volume."""
        if not data.day_change_pct or data.day_change_pct <= 2.0:
            return (False, f"no impulse ({(data.day_change_pct or 0):.1f}%)")
        if (data.rvol or 0) <= 1.5:
            return (False, "no impulse volume")
        return (True, f"+{data.day_change_pct:.1f}% pole")

    def check_value(self, data: AssetData) -> tuple:
        """A: Flag consolidation — last 5 bars in tight range (<1%)."""
        if not self._detect_consolidation(data):
            return (False, "no consolidation")
        return (True, "flag formed")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Breakout above flag high + above VWAP + MACD bullish."""
        if not self._breakout_detected(data):
            return (False, "no breakout")
        if data.vwap_val is None or data.price <= data.vwap_val:
            return (False, "below VWAP")
        if not data.macd_bullish:
            return (False, "MACD bearish")
        return (True, "breakout + VWAP + MACD↑")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        if pnl_pct >= 2.0:
            return f"Take profit +{pnl_pct:.1f}% (pole projection)"
        if pnl_pct <= -1.0:
            return f"Stop loss {pnl_pct:.1f}%"
        if data.vwap_val and data.price < data.vwap_val:
            return "VWAP breakdown"
        if data.ema_21 and data.price < data.ema_21:
            return "Price broke below EMA21"
        return None
