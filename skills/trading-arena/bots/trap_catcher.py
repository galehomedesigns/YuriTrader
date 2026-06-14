"""Trap Catcher — False Breakout Reversal (Contrarian) Strategy.

Catches exhaustion moves by fading extreme RSI readings.

LONG-ONLY: the arena trades crypto spot on Kraken — there is no short. So the
only actionable edge is the *oversold* reversal (price overshot down, snaps
back up = BUY). An *overbought* reversal is a bearish signal and is explicitly
NOT taken as a long (doing so was buying assets the model itself flagged as
topping — the dominant loss source before 2026-05-17).

Entry: RSI in the oversold-reversal band (proxy for "was <25, rising back")
       + weakening-but-present trend + declining volume + fading MACD
Exit:  +3% TP, -1.5% SL, or RSI back to 45-55 mid-range — but the RSI exit is
       suppressed in the 0..fee-hurdle profit zone so it can't scratch a tiny
       "win" that the round-trip Kraken fee turns into a real loss, and can't
       front-run the +3% TP.

NOTE: AssetData carries a single rsi_14 scalar (no history), so the
"reverting from an extreme" tests are proxies on the *current* RSI band, not a
verified prior extreme. This is a known limitation, not a guarantee.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from config import roundtrip_fee_pct
from typing import Dict, List, Optional

# A win must clear the round-trip fee before the RSI mid-range exit is allowed to
# bank it; below this, hold for TP/SL instead of scratching into a fee loss.
# Computed per-asset in should_exit (Kraken fee for crypto — unchanged; spread
# proxy for stocks) so crypto behavior is identical and stocks aren't over-held.

# check_trend gate: ADX must be below "strong" (exhaustion plausible) but above
# this floor — pure dead-flat chop (ADX < floor) has no trend to revert and is
# where these entries got whipsawed.
WEAK_ADX_FLOOR = 12.0
WEAK_ADX_CEIL = 30.0

# MACD histogram is price-scaled; a raw 0.5 threshold is ~always true for
# sub-dollar coins and meaningless across BTC vs XRP. Fading momentum = the
# histogram is small *relative to price* (0.1% of price).
MACD_FADE_FRAC = 0.001


class TrapCatcher(BaseBot):
    NAME = "Trap Catcher"
    BOT_ID = "trap-catcher"
    DESCRIPTION = "False breakout reversal — fades oversold exhaustion (long-only)"

    def _rsi_reverting_from_oversold(self, data: AssetData) -> bool:
        """Proxy for "RSI was < 25 and is rising back": current RSI in the
        28-40 band. No RSI history is available, so this is a band check on the
        current value, not a verified prior extreme (see module docstring)."""
        if data.rsi_14 is None or not data.closes or len(data.closes) < 3:
            return False
        return 28 <= data.rsi_14 <= 40

    def _volume_declining(self, data: AssetData) -> bool:
        """Volume fading = exhaustion of the move that overshot."""
        if not data.volumes or len(data.volumes) < 5:
            return False
        recent = data.volumes[-3:]
        earlier = data.volumes[-5:-3]
        if not earlier or sum(earlier) == 0:
            return False
        return sum(recent) / len(recent) < sum(earlier) / len(earlier)

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        # Long-only: only surface the oversold side. Overbought names can never
        # pass check_value, so scanning them is wasted churn.
        candidates = []
        for sym, d in market_data.items():
            if d.rsi_14 is None:
                continue
            if d.rsi_14 < 25 or self._rsi_reverting_from_oversold(d):
                candidates.append(sym)
        return candidates

    # === TAY Framework ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: trend present but weakening — ADX below strong, above dead chop.

        Scalar ADX has no direction/history, so this is a regime filter, not a
        directional one: reject strong trends (don't fade a freight train) and
        reject flat chop (nothing overshot, nothing to revert)."""
        if data.adx_14 is None:
            return (False, "ADX=n/a")
        if data.adx_14 >= WEAK_ADX_CEIL:
            return (False, f"ADX={data.adx_14:.0f} too strong to fade")
        if data.adx_14 < WEAK_ADX_FLOOR:
            return (False, f"ADX={data.adx_14:.0f} flat chop")
        return (True, f"weakening (ADX={data.adx_14:.0f})")

    def check_value(self, data: AssetData) -> tuple:
        """A: oversold overshoot snapping back up (long-only — overbought
        reversal is bearish and is NOT a long entry here)."""
        if self._rsi_reverting_from_oversold(data):
            return (True, f"OS reversal RSI={data.rsi_14:.0f}")
        return (False, f"RSI={data.rsi_14 or 0:.0f} not an oversold reversal")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: volume declining AND MACD momentum fading (price-normalized)."""
        if not self._volume_declining(data):
            return (False, "vol still high")
        if data.macd_histogram is None or data.price is None or data.price <= 0:
            return (False, "MACD n/a")
        if abs(data.macd_histogram) >= MACD_FADE_FRAC * data.price:
            return (False, "MACD still strong")
        return (True, "vol↓ + MACD fade")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100
        fee_hurdle_pct = roundtrip_fee_pct(data.asset_type) * 100.0

        if pnl_pct >= 3.0:
            return f"Take profit +{pnl_pct:.1f}%"
        if pnl_pct <= -1.5:
            return f"Stop loss {pnl_pct:.1f}%"
        if data.rsi_14 and 45 <= data.rsi_14 <= 55:
            # Cut a failed reversal fast (it didn't revert) ...
            if pnl_pct <= 0:
                return f"RSI={data.rsi_14:.0f} reverted, no follow-through ({pnl_pct:.1f}%)"
            # ... or bank a win that has cleared the round-trip fee ...
            if pnl_pct >= fee_hurdle_pct:
                return f"RSI={data.rsi_14:.0f} mid-range, +{pnl_pct:.1f}% banked"
            # ... but in the 0..fee-hurdle dead zone, hold for TP/SL: exiting
            # here books a real-money loss after fees and front-runs the +3% TP.
        return None
