"""Volume Whisperer — VWAP + OBV Volume Trading Strategy.

Follows institutional volume flows using VWAP and OBV.
Entry: Price above VWAP + OBV positive + rvol > 1.5 (institutional activity)
Exit: +2% TP, -1% SL, price drops below VWAP, volume dries up (rvol < 0.5)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from typing import Dict, List, Optional


class VolumeWhisperer(BaseBot):
    NAME = "Volume Whisperer"
    BOT_ID = "volume-whisperer"
    DESCRIPTION = "VWAP + OBV volume trading — follows institutional money flow"

    def _obv_trending_up(self, data: AssetData) -> bool:
        """Check if OBV is positive and trending upward."""
        if data.obv_val is None:
            return False
        return data.obv_val > 0

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.vwap_val is None:
                continue
            # Price above VWAP + OBV positive + volume spike
            if (d.price > d.vwap_val and
                self._obv_trending_up(d) and
                (d.rvol or 0) > 1.5):
                candidates.append(sym)
        return candidates

    # === TAY Framework ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: OBV trending up — institutional accumulation in progress."""
        if not self._obv_trending_up(data):
            return (False, "OBV not up")
        if data.ema_21 is None or data.price <= data.ema_21:
            return (False, "below EMA21")
        return (True, "OBV↑ + above EMA21")

    def check_value(self, data: AssetData) -> tuple:
        """A: Price above VWAP — institutional control of session."""
        if data.vwap_val is None or data.price <= data.vwap_val:
            return (False, "below VWAP")
        return (True, "above VWAP")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Volume spike + RSI healthy + MACD bullish."""
        if (data.rvol or 0) < 1.5:
            return (False, f"vol {(data.rvol or 0):.1f}x low")
        if data.rsi_14 is None or data.rsi_14 < 40 or data.rsi_14 > 70:
            return (False, f"RSI={data.rsi_14 or 0:.0f} not healthy")
        if not data.macd_bullish:
            return (False, "MACD bearish")
        return (True, f"vol {data.rvol:.1f}x institutional")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        if pnl_pct >= 2.0:
            return f"Take profit +{pnl_pct:.1f}%"
        if pnl_pct <= -1.0:
            return f"Stop loss {pnl_pct:.1f}%"
        if data.vwap_val and data.price < data.vwap_val:
            return "Price dropped below VWAP"
        if (data.rvol or 0) < 0.5:
            return f"Volume dried up rvol={data.rvol:.2f}"
        if not self._obv_trending_up(data):
            return "OBV turned negative"
        return None
