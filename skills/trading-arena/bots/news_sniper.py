"""News Sniper — News/Sentiment Scalping Strategy.

Catches momentum from news-driven moves by entering after big price spikes.
Entry: Large day_change_pct (>3%) + high volume (rvol > 2) + RSI not extreme
Exit: +1% TP (quick scalp), -0.5% SL (tight), 30-minute time decay
"""
import sys, os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from typing import Dict, List, Optional

# 30 minutes in seconds
TIME_DECAY_SECONDS = 30 * 60


class NewsSniper(BaseBot):
    NAME = "News Sniper"
    BOT_ID = "news-sniper"
    DESCRIPTION = "News/sentiment scalping — quick entries on big news-driven moves"

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.day_change_pct is None:
                continue
            # Big move (news event) + high volume
            if abs(d.day_change_pct) > 3.0 and (d.rvol or 0) > 2.0:
                candidates.append(sym)
        return candidates

    # === TAY Framework ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Big intraday move — news event in progress (>3% in either direction)."""
        if data.day_change_pct is None or abs(data.day_change_pct) <= 3.0:
            return (False, f"flat ({(data.day_change_pct or 0):.1f}%)")
        return (True, f"news move {data.day_change_pct:+.1f}%")

    def check_value(self, data: AssetData) -> tuple:
        """A: Volume confirms institutional participation (not retail noise)."""
        if (data.rvol or 0) <= 2.0:
            return (False, f"vol {(data.rvol or 0):.1f}x")
        return (True, f"vol {data.rvol:.1f}x institutional")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: RSI not extreme + VWAP + MACD agree with the news direction."""
        if data.rsi_14 is None or data.rsi_14 <= 30 or data.rsi_14 >= 70:
            return (False, f"RSI={data.rsi_14 or 0:.0f} extreme")
        if data.day_change_pct and data.day_change_pct > 0:
            if data.vwap_val is None or data.price <= data.vwap_val:
                return (False, "below VWAP on up move")
            if not data.macd_bullish:
                return (False, "MACD bearish on up move")
            return (True, "VWAP + MACD↑")
        else:
            if data.vwap_val is None or data.price >= data.vwap_val:
                return (False, "above VWAP on down move")
            if data.macd_bullish:
                return (False, "MACD bullish on down move")
            return (True, "VWAP + MACD↓")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        if pnl_pct >= 1.0:
            return f"Take profit +{pnl_pct:.2f}%"
        if pnl_pct <= -0.5:
            return f"Stop loss {pnl_pct:.2f}%"

        # Time decay: exit after 30 minutes regardless
        opened_at = position.get("opened_at")
        if opened_at:
            if isinstance(opened_at, (int, float)):
                elapsed = time.time() - opened_at
            else:
                # Try parsing ISO string
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                    elapsed = time.time() - dt.timestamp()
                except (ValueError, TypeError):
                    elapsed = 0
            if elapsed > TIME_DECAY_SECONDS:
                return f"Time decay — {elapsed / 60:.0f}min elapsed, closing at {pnl_pct:+.2f}%"

        # Volume drying up = move is over
        if (data.rvol or 0) < 0.8:
            return f"Volume fading rvol={data.rvol:.2f}"
        return None
