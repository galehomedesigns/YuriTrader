"""Nano Sniper — EMA Scalping Strategy.

Ultra-short-term scalping using strict 4-EMA hierarchy alignment.
Entry: EMA 8>21>50>200 alignment + VWAP confirmation + volume spike
Exit: +0.3% TP (tiny profit), -0.2% SL (tight stop), EMA hierarchy breaks
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from typing import Dict, List, Optional


class NanoSniper(BaseBot):
    NAME = "Nano Sniper"
    BOT_ID = "nano-sniper"
    DESCRIPTION = "EMA scalping — ultra-tight entries on perfect EMA alignment"

    def _ema_aligned(self, data: AssetData) -> bool:
        """Check strict 4-EMA bullish hierarchy: 8 > 21 > 50 > 200."""
        vals = [data.ema_8, data.ema_21, data.ema_50, data.ema_200]
        if any(v is None for v in vals):
            return False
        return vals[0] > vals[1] > vals[2] > vals[3]

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.ema_8 is None or d.ema_200 is None:
                continue
            # Quick filter: EMA alignment + above VWAP + volume
            if (self._ema_aligned(d) and
                d.vwap_val and d.price > d.vwap_val and
                (d.rvol or 0) > 1.2):
                candidates.append(sym)
        return candidates

    # === TAY Framework ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Perfect EMA hierarchy — 8 > 21 > 50 > 200 (textbook uptrend)."""
        if not self._ema_aligned(data):
            return (False, "EMA not aligned")
        return (True, "EMA 8>21>50>200")

    def check_value(self, data: AssetData) -> tuple:
        """A: Price above VWAP — institutional control of intraday session."""
        if data.vwap_val is None or data.price <= data.vwap_val:
            return (False, "below VWAP")
        return (True, "above VWAP")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Volume spike + RSI in healthy zone + MACD bullish."""
        if (data.rvol or 0) < 1.5:
            return (False, f"vol {(data.rvol or 0):.1f}x low")
        if data.rsi_14 is None or data.rsi_14 < 45 or data.rsi_14 > 75:
            return (False, f"RSI={data.rsi_14 or 0:.0f} not in zone")
        if not data.macd_bullish:
            return (False, "MACD bearish")
        return (True, f"vol {data.rvol:.1f}x scalp")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        if pnl_pct >= 0.3:
            return f"Take profit +{pnl_pct:.2f}%"
        if pnl_pct <= -0.2:
            return f"Stop loss {pnl_pct:.2f}%"
        if not self._ema_aligned(data):
            return "EMA hierarchy broken"
        if data.vwap_val and data.price < data.vwap_val:
            return "Price dropped below VWAP"
        return None
