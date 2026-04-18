"""Momentum Hunter — Momentum Breakout Strategy.

Scans for assets with volume surges + bullish momentum signals.
Entry: RSI > 50 + MACD bullish + volume > 2x avg + price > EMA-50
Exit: +2% TP, -1% SL, RSI > 80 overbought, MACD bearish cross
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from typing import Dict, List, Optional


class MomentumHunter(BaseBot):
    NAME = "Momentum Hunter"
    BOT_ID = "momentum-hunter"
    DESCRIPTION = "Momentum breakout — buys assets breaking out on high volume"

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.rsi_14 is None or d.ema_50 is None:
                continue
            # Quick filter: bullish momentum + volume
            if (d.rsi_14 > 50 and d.macd_bullish and
                (d.rvol or 0) > 1.5 and d.price > (d.ema_50 or 0)):
                candidates.append(sym)
        return candidates

    # === TAY Framework ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Strong uptrend — price above 50 EMA, ADX confirms trend strength."""
        if data.ema_50 is None or data.price <= data.ema_50:
            return (False, "no trend")
        if data.adx_14 is not None and data.adx_14 < 20:
            return (False, f"ADX={data.adx_14:.0f} weak")
        return (True, f"uptrend (>EMA50, ADX={data.adx_14 or 0:.0f})")

    def check_value(self, data: AssetData) -> tuple:
        """A: Momentum just kicked off — price gapping up with intraday strength."""
        if data.day_change_pct < 1.0:
            return (False, f"flat ({data.day_change_pct:.1f}%)")
        return (True, f"+{data.day_change_pct:.1f}% intraday")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Volume surge + RSI not overbought + MACD bullish (3-of-3 trigger)."""
        if (data.rvol or 0) < 2.0:
            return (False, f"vol {(data.rvol or 0):.1f}x low")
        if data.rsi_14 is None or data.rsi_14 < 50 or data.rsi_14 > 75:
            return (False, f"RSI={data.rsi_14 or 0:.0f} not in zone")
        if not data.macd_bullish:
            return (False, "MACD bearish")
        return (True, f"vol {data.rvol:.1f}x + MACD↑")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        if pnl_pct >= 2.0:
            return f"Take profit +{pnl_pct:.1f}%"
        if pnl_pct <= -1.0:
            return f"Stop loss {pnl_pct:.1f}%"
        if data.rsi_14 and data.rsi_14 > 80:
            return f"Overbought RSI={data.rsi_14:.0f}"
        if not data.macd_bullish and data.macd_histogram is not None:
            return "MACD bearish cross"
        return None
