"""Base bot class — all 10 strategy bots extend this.

TAY Framework:
    Every bot follows the unified TAY (Trend, Area of value, Y/trigger) rule:
    - check_trend(): Is the market in the right direction for this strategy?
    - check_value(): Has price reached a meaningful pullback or setup zone?
    - check_trigger(): Is there a confirmation signal to enter NOW?

    The default should_enter() requires ALL THREE to return True.
    Each bot defines its own variables for T, A, Y — that's how we get
    10 different "one rule" strategies that compete in the arena.
"""
import sys
import os
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import BOT_DAILY_LOSS_LIMIT, STARTING_BALANCE
from shared.market_scanner import AssetData
from shared.paper_trader import PaperTrader


class BaseBot(ABC):
    """Abstract base class for all trading bots — implements the TAY framework."""

    NAME: str = "Unnamed Bot"
    BOT_ID: str = "unnamed"
    DESCRIPTION: str = ""
    MARKET_TYPE: str = "both"  # "stock", "crypto", or "both"

    def __init__(self):
        self.trader = PaperTrader(self.BOT_ID, self.NAME, STARTING_BALANCE)
        self.paused = False
        self.pause_reason = ""

    @abstractmethod
    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        """Scan market data and return list of candidate symbols to trade.
        This is the initial filter — fast check across all assets."""
        pass

    # === TAY Framework — bots override these three ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Is the market trending in the right direction for this strategy?
        Returns (passed: bool, reason: str)"""
        return (True, "trend ok")

    def check_value(self, data: AssetData) -> tuple:
        """A: Is price at a meaningful pullback / setup zone (area of value)?
        Returns (passed: bool, reason: str)"""
        return (True, "value ok")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Is there a confirmation signal to enter NOW?
        Returns (passed: bool, reason: str)"""
        return (True, "trigger ok")

    def get_tay_components(self, data: AssetData) -> dict:
        """Returns the TAY component breakdown for a given asset.
        Used by the overseer to analyze which components win."""
        t_pass, t_reason = self.check_trend(data)
        a_pass, a_reason = self.check_value(data)
        y_pass, y_reason = self.check_trigger(data)
        return {
            "t_pass": t_pass, "t_reason": t_reason,
            "a_pass": a_pass, "a_reason": a_reason,
            "y_pass": y_pass, "y_reason": y_reason,
        }

    def should_enter(self, symbol: str, data: AssetData) -> Optional[str]:
        """Default TAY rule: ALL three components must pass.
        Bots can override this for custom logic, but the default enforces discipline."""
        components = self.get_tay_components(data)
        if not components["t_pass"]:
            return None
        if not components["a_pass"]:
            return None
        if not components["y_pass"]:
            return None
        # Stash for paper_trader to log alongside the trade
        self._last_tay = components
        return f"TAY: {components['t_reason']} | {components['a_reason']} | {components['y_reason']}"

    @abstractmethod
    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        """Check if an existing position should be closed.
        Returns exit reason string if should exit, None otherwise."""
        pass

    def evaluate(self, market_data: Dict[str, AssetData]):
        """Main evaluation loop — called every scan cycle."""
        if self.paused:
            return

        # Check daily risk limit
        daily_pnl = self.trader.get_daily_pnl()
        if daily_pnl <= BOT_DAILY_LOSS_LIMIT:
            self.paused = True
            self.pause_reason = f"Daily loss limit hit: ${daily_pnl:.2f}"
            print(f"  [{self.NAME}] PAUSED — {self.pause_reason}", file=sys.stderr)
            return

        # Step 1: Check exits on open positions
        positions = self.trader.get_open_positions()
        for pos in positions:
            symbol = pos.get("symbol", "")
            if symbol in market_data:
                exit_reason = self.should_exit(pos, market_data[symbol])
                if exit_reason:
                    self.trader.close_position(pos, market_data[symbol].price, exit_reason)

        # Step 2: Scan for new entries
        if self.trader.can_open_position():
            candidates = self.scan(market_data)
            for symbol in candidates:
                if not self.trader.can_open_position():
                    break
                if self.trader.has_position(symbol):
                    continue
                data = market_data.get(symbol)
                if not data:
                    continue
                entry_reason = self.should_enter(symbol, data)
                if entry_reason:
                    self.trader.log_signal(symbol, "BUY", indicators={
                        "rsi": data.rsi_14, "macd": data.macd_bullish,
                        "rvol": data.rvol, "price": data.price,
                    }, executed=True)
                    tay = getattr(self, "_last_tay", None)
                    self.trader.open_position(symbol, data.price, "BUY", entry_reason, tay_components=tay)

    def status(self) -> dict:
        """Return bot status summary."""
        positions = self.trader.get_open_positions()
        return {
            "bot_id": self.BOT_ID,
            "name": self.NAME,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "balance": self.trader.balance,
            "open_positions": len(positions),
            "daily_pnl": self.trader.get_daily_pnl(),
        }
