"""Trend Breakout — low-frequency Donchian breakout trend-follower.

Design rationale (2026-05-17): the backtest proved every existing bot has
~zero gross edge and Kraken's 0.5-0.8% round-trip fee dominates. The ONLY
TA-shaped structure that can survive that fee is one whose AVERAGE WINNER is
many multiples of the fee — i.e. rare entries, winners left to run, losers
cut fast. This is classic CTA/Turtle time-series-momentum, the single most
out-of-sample-robust anomaly across decades and asset classes (incl. crypto).

  T (trend) : regime filter — price above its 50-period EMA (only buy uptrends;
              breakouts in downtrends/chop are the false-breakout trap).
  A (value) : Donchian breakout — close makes a new ENTRY_N-bar high
              (price escaping its range = the momentum trigger).
  Y (trigger): trend has real strength — ADX >= ADX_MIN (filters limp
              breakouts that immediately fail).

  Exit      : NO fixed take-profit (capping winners is exactly what killed
              Trap Catcher). Exit only on (a) a hard initial ATR stop, or
              (b) a Donchian EXIT_M-bar low = the trend structurally broke.
              Long-only (arena trades Kraken spot — no short).

PARAMETERS ARE TEXTBOOK AND CHOSEN A PRIORI — NOT fitted to this data.
ENTRY_N=20 / EXIT_M=10 / ADX_MIN=20 / ATR_K=2.0 are the canonical Turtle-ish
values. They are intentionally NOT optimised here: a strategy whose edge only
appears after parameter search on the same data it's tested on has no edge.
Meant to be judged by backtest.py on 4h/daily, then the promotion gate.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from typing import Dict, List, Optional

ENTRY_N = 20   # breakout lookback (new N-bar high)
EXIT_M = 10    # trailing exit (new M-bar low = trend break)
ADX_MIN = 20.0 # minimum trend strength to take the breakout
ATR_K = 2.0    # hard initial stop = entry - ATR_K * ATR


class TrendBreakout(BaseBot):
    NAME = "Trend Breakout"
    BOT_ID = "trend-breakout"
    DESCRIPTION = "Low-frequency Donchian breakout trend-follower (winners run, losers cut)"
    MARKET_TYPE = "both"

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        # Cheap pre-filter; TAY does the real work. Need enough history for the
        # ENTRY_N lookback and the EMA regime filter.
        out = []
        for sym, d in market_data.items():
            if d.price and d.highs and len(d.highs) > ENTRY_N + 1:
                out.append(sym)
        return out

    def check_trend(self, data: AssetData) -> tuple:
        """T: only buy when price is above its 50-EMA (uptrend regime)."""
        if data.ema_50 is None or data.price <= 0:
            return (False, "EMA50 n/a")
        if data.price <= data.ema_50:
            return (False, f"below EMA50 ({data.price:.4g}<{data.ema_50:.4g})")
        return (True, f"uptrend (px>EMA50)")

    def check_value(self, data: AssetData) -> tuple:
        """A: Donchian breakout — close exceeds the prior ENTRY_N-bar high."""
        if not data.highs or len(data.highs) < ENTRY_N + 1:
            return (False, "insufficient bars")
        prior_high = max(data.highs[-(ENTRY_N + 1):-1])
        if data.price > prior_high:
            return (True, f"{ENTRY_N}-bar breakout >{prior_high:.4g}")
        return (False, f"no breakout (px {data.price:.4g}<= {prior_high:.4g})")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: the trend has real strength (filters limp false breakouts)."""
        if data.adx_14 is None:
            return (False, "ADX n/a")
        if data.adx_14 < ADX_MIN:
            return (False, f"weak trend ADX={data.adx_14:.0f}<{ADX_MIN:.0f}")
        return (True, f"strong trend ADX={data.adx_14:.0f}")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0 or data.price <= 0:
            return None
        # (a) hard initial ATR stop — cut a failed breakout fast.
        if data.atr_14:
            stop = entry - ATR_K * data.atr_14
            if data.price <= stop:
                pnl_pct = (data.price - entry) / entry * 100
                return f"ATR stop {pnl_pct:.1f}% (<= {stop:.4g})"
        # (b) trailing trend-break — Donchian EXIT_M-bar low. NO take-profit:
        #     winners are left to run; this is the whole point of the design.
        if data.lows and len(data.lows) >= EXIT_M + 1:
            exit_low = min(data.lows[-(EXIT_M + 1):-1])
            if data.price < exit_low:
                pnl_pct = (data.price - entry) / entry * 100
                return f"{EXIT_M}-bar trend break {pnl_pct:.1f}% (< {exit_low:.4g})"
        return None
