"""Squeeze Breaker — Bollinger Squeeze Breakout Strategy (TAY v2).

UPGRADED 2026-04-12 to use horizontal resistance breakouts:
- T (Trend): Bollinger bandwidth squeeze (low volatility — distinct setup)
- A (Area of Value): Horizontal RESISTANCE level (price touching/just-broken)
- Y (Trigger): Breakout candle above resistance + volume surge

Combo 3: Tests horizontal R + breakout (vs Trend Rider's S/R + hammer pullback,
The Reverter's S/R + engulfing reversal). Same S/R indicator, different role.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from shared.indicators import at_resistance, nearest_sr_level
from typing import Dict, List, Optional


class SqueezeBreaker(BaseBot):
    NAME = "Squeeze Breaker"
    BOT_ID = "squeeze-breaker"
    DESCRIPTION = "Bollinger squeeze breakout — catches explosive moves after low volatility"

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.bb_bandwidth is None or d.bb_upper is None:
                continue
            # Squeeze forming: very tight Bollinger bands
            if d.bb_bandwidth < 0.03:
                candidates.append(sym)
        return candidates

    # === TAY Framework v2 — Horizontal R breakout edition ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Volatility squeeze — Bollinger bandwidth compressed (energy building)."""
        if data.bb_bandwidth is None or data.bb_bandwidth >= 0.03:
            return (False, f"BW={data.bb_bandwidth or 0:.4f} no squeeze")
        return (True, f"squeeze BW={data.bb_bandwidth:.4f}")

    def check_value(self, data: AssetData) -> tuple:
        """A: Price at horizontal resistance OR just broke above BB upper."""
        # Strongest signal: at horizontal resistance (about to break)
        resistance_nearby = at_resistance(data.price, data.sr_levels, max_distance_pct=0.012)
        if resistance_nearby:
            level = nearest_sr_level(data.price, data.sr_levels)
            return (True, f"at resistance ${level[0]:.2f} ({level[3]} touches)")
        # Fallback: BB upper breakout
        if data.bb_upper and data.price > data.bb_upper:
            return (True, "broke BB upper")
        return (False, "no resistance zone")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Volume surge + bullish momentum (MACD up) + RSI strong."""
        if (data.rvol or 0) < 1.5:
            return (False, f"vol {(data.rvol or 0):.1f}x low")
        if not data.macd_bullish:
            return (False, "MACD bearish")
        if data.rsi_14 is None or data.rsi_14 < 50:
            return (False, f"RSI={data.rsi_14 or 0:.0f}<50")
        return (True, f"vol {data.rvol:.1f}x breakout")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        if pnl_pct >= 2.5:
            return f"Take profit +{pnl_pct:.1f}%"
        if pnl_pct <= -1.0:
            return f"Stop loss {pnl_pct:.1f}%"
        if data.bb_middle and data.price <= data.bb_middle:
            return f"Returned to BB middle ${data.bb_middle:.2f}"
        if data.rsi_14 and data.rsi_14 > 75:
            return f"Overbought RSI={data.rsi_14:.0f}"
        return None
