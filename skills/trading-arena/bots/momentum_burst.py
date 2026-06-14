"""Momentum Burst — fee-aware momentum-notice chaser.

Trades the same kind of fast, high-volume breakout that Kraken's own
"X has momentum" notices fire on — but only when the move is large enough to
clear the round-trip fee floor with margin. It rides the widened, fee-aware
dynamic watchlist (overseer/dynamic_watchlist.py now feeds EVERY Kraken USD
pair, not the hardcoded 6, and market_scanner now actually fetches them).

PAPER-GATED BY DESIGN: this bot is intentionally NOT in LIVE_TRADING_BOTS. The
arena's fee-aware promotion gate (paper_trader._promotion_ok) keeps it on paper
until it proves a net-of-fee edge at 95% confidence over >= MIN_PROMOTION_TRADES
trades. The structural finding is that nothing has cleared that bar yet; this is
the forward-test mechanism, not a license to churn real money — same contract as
TrendBreakout.

Entry: fresh burst >= fee-aware floor, high RVOL, MACD bullish, RSI in the
       momentum zone, price extended above EMAs but not a blow-off top.
Exit:  fee-aware TP (>= ~2x round-trip fee), tight SL, momentum-fade cut.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from config import roundtrip_fee_pct
from typing import Dict, List, Optional

# Asset-independent knobs (same for crypto + stock).
_FEE_MULT = float(os.environ.get("MOMENTUM_FEE_MULT", "1.5"))
_MIN_MOVE_FLOOR = float(os.environ.get("MOMENTUM_MIN_MOVE_PCT", "3.0"))
# Don't chase a blow-off / illiquid pump we'd be buying at the very top of.
_MAX_BURST_PCT = float(os.environ.get("MOMENTUM_MAX_MOVE_PCT", "40.0"))
_SL_PCT = -1.5


def _fee_params(asset_type):
    """Per-asset (fee_floor%, min_burst%, take_profit%). Crypto uses the Kraken
    round-trip fee (unchanged); stock uses the spread proxy so the 1.6% floor
    isn't applied to commission-free stocks."""
    fee_floor = roundtrip_fee_pct(asset_type) * 100.0
    min_burst = max(fee_floor * _FEE_MULT, _MIN_MOVE_FLOOR)
    tp = max(3.5, fee_floor * 2.0 + 0.5)
    return fee_floor, min_burst, tp


class MomentumBurst(BaseBot):
    NAME = "Momentum Burst"
    BOT_ID = "momentum-burst"
    DESCRIPTION = ("Fee-aware momentum-notice chaser — buys high-volume bursts "
                   "only when the move can pay for its round-trip fee")
    MARKET_TYPE = "both"

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym, d in market_data.items():
            if d.rsi_14 is None or d.ema_21 is None:
                continue
            _, min_burst, _ = _fee_params(d.asset_type)
            if (d.day_change_pct >= min_burst
                    and d.macd_bullish
                    and (d.rvol or 0) > 2.0
                    and d.price > (d.ema_21 or 0)):
                candidates.append(sym)
        return candidates

    # === TAY Framework ===

    def check_trend(self, data: AssetData) -> tuple:
        """T: Price riding above its short/mid EMAs with a real trend (ADX)."""
        if data.ema_21 is None or data.price <= data.ema_21:
            return (False, "not above EMA21")
        if data.ema_50 is not None and data.price <= data.ema_50:
            return (False, "below EMA50")
        if data.adx_14 is not None and data.adx_14 < 20:
            return (False, f"ADX={data.adx_14:.0f} weak")
        return (True, f"trend (>EMA21/50, ADX={data.adx_14 or 0:.0f})")

    def check_value(self, data: AssetData) -> tuple:
        """A: A genuine burst that clears the fee floor — but not a blow-off
        top we'd be the last buyer of."""
        move = data.day_change_pct
        fee_floor, min_burst, _ = _fee_params(data.asset_type)
        if move < min_burst:
            return (False, f"+{move:.1f}% < {min_burst:.1f}% fee floor")
        if move > _MAX_BURST_PCT:
            return (False, f"+{move:.1f}% blow-off (> {_MAX_BURST_PCT:.0f}%)")
        return (True, f"burst +{move:.1f}% (clears {fee_floor:.1f}% fee)")

    def check_trigger(self, data: AssetData) -> tuple:
        """Y: Volume surge + MACD up + RSI in the momentum (not exhausted) zone,
        price in the upper half of the Bollinger band (breakout, not fade)."""
        if (data.rvol or 0) < 2.0:
            return (False, f"vol {(data.rvol or 0):.1f}x low")
        if not data.macd_bullish or (data.macd_histogram or 0) <= 0:
            return (False, "MACD not bullish")
        if data.rsi_14 is None or data.rsi_14 < 55 or data.rsi_14 > 82:
            return (False, f"RSI={data.rsi_14 or 0:.0f} out of zone")
        if data.bb_middle is not None and data.price < data.bb_middle:
            return (False, "below BB mid (fading)")
        return (True, f"vol {data.rvol:.1f}x + MACD↑ + RSI {data.rsi_14:.0f}")

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100

        _, _, tp_pct = _fee_params(data.asset_type)
        if pnl_pct >= tp_pct:
            return f"Take profit +{pnl_pct:.1f}% (fee-aware TP {tp_pct:.1f}%)"
        if pnl_pct <= _SL_PCT:
            return f"Stop loss {pnl_pct:.1f}%"
        if data.rsi_14 and data.rsi_14 > 88:
            return f"Exhaustion RSI={data.rsi_14:.0f}"
        if not data.macd_bullish and data.macd_histogram is not None:
            return "Momentum fade (MACD bearish cross)"
        if data.ema_21 is not None and data.price < data.ema_21:
            return "Trend broke (lost EMA21)"
        return None
