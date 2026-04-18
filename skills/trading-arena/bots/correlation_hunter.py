"""Correlation Hunter — Pairs/Correlation Trading Strategy.

Trades mean reversion of correlated asset pairs (BTC/ETH, SPY/QQQ).
Entry: Z-score of price ratio deviates > 2 from mean (buy underperformer)
Exit: Z-score reverts to 0, or Z-score > 3.5 (correlation broke — stop loss)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_bot import BaseBot
from shared.market_scanner import AssetData
from shared.indicators import zscore
from typing import Dict, List, Optional


# Correlated pairs to track
PAIRS = [
    ("BTC", "ETH"),
    ("SPY", "QQQ"),
    ("AAPL", "MSFT"),
    ("GLD", "SLV"),
]


class CorrelationHunter(BaseBot):
    NAME = "Correlation Hunter"
    BOT_ID = "correlation-hunter"
    DESCRIPTION = "Pairs/correlation trading — buys underperformer when spread deviates"

    def __init__(self):
        super().__init__()
        self._pair_ratios: Dict[str, List[float]] = {}
        self._pair_zscores: Dict[str, float] = {}

    def _get_pair_key(self, sym_a: str, sym_b: str) -> str:
        return f"{sym_a}/{sym_b}"

    def _find_pair(self, symbol: str) -> Optional[tuple]:
        """Find the correlated pair for a given symbol."""
        for a, b in PAIRS:
            if symbol == a or symbol == b:
                return (a, b)
        return None

    def _compute_pair_zscore(self, data_a: AssetData, data_b: AssetData, pair_key: str) -> Optional[float]:
        """Compute Z-score of price ratio between correlated pair."""
        if data_a.price <= 0 or data_b.price <= 0:
            return None

        ratio = data_a.price / data_b.price

        # Build ratio history from closes if available
        if data_a.closes and data_b.closes:
            min_len = min(len(data_a.closes), len(data_b.closes))
            if min_len >= 20:
                ratios = []
                for i in range(min_len):
                    if data_b.closes[-(min_len - i)] != 0:
                        ratios.append(data_a.closes[-(min_len - i)] / data_b.closes[-(min_len - i)])
                if len(ratios) >= 20:
                    z = zscore(ratios)
                    if z is not None:
                        self._pair_zscores[pair_key] = z
                        return z

        # Fallback: use stored ratio history
        if pair_key not in self._pair_ratios:
            self._pair_ratios[pair_key] = []
        self._pair_ratios[pair_key].append(ratio)
        # Keep last 50 ratios
        self._pair_ratios[pair_key] = self._pair_ratios[pair_key][-50:]

        if len(self._pair_ratios[pair_key]) >= 20:
            z = zscore(self._pair_ratios[pair_key])
            if z is not None:
                self._pair_zscores[pair_key] = z
                return z
        return None

    def scan(self, market_data: Dict[str, AssetData]) -> List[str]:
        candidates = []
        for sym_a, sym_b in PAIRS:
            if sym_a not in market_data or sym_b not in market_data:
                continue
            data_a = market_data[sym_a]
            data_b = market_data[sym_b]
            pair_key = self._get_pair_key(sym_a, sym_b)

            z = self._compute_pair_zscore(data_a, data_b, pair_key)
            if z is None:
                continue

            # Spread deviated significantly
            if abs(z) > 2.0:
                if z > 2.0:
                    # Ratio too high — sym_a overvalued, buy sym_b
                    candidates.append(sym_b)
                else:
                    # Ratio too low — sym_b overvalued, buy sym_a
                    candidates.append(sym_a)
        return candidates

    def should_enter(self, symbol: str, data: AssetData) -> Optional[str]:
        pair = self._find_pair(symbol)
        if not pair:
            return None

        sym_a, sym_b = pair
        pair_key = self._get_pair_key(sym_a, sym_b)
        z = self._pair_zscores.get(pair_key)
        if z is None:
            return None

        signals = []

        if z > 2.0 and symbol == sym_b:
            signals.append(f"Z={z:.2f} buy underperformer {sym_b}")
        elif z < -2.0 and symbol == sym_a:
            signals.append(f"Z={z:.2f} buy underperformer {sym_a}")
        else:
            return None

        if data.rsi_14 and data.rsi_14 < 50:
            signals.append(f"RSI={data.rsi_14:.0f} undervalued")
        if data.day_change_pct is not None and data.day_change_pct < 0:
            signals.append(f"Down {data.day_change_pct:.1f}%")
        if (data.rvol or 0) > 1.0:
            signals.append(f"Vol {data.rvol:.1f}x")

        if len(signals) >= 1:
            return f"Pair divergence: {', '.join(signals)}"
        return None

    def should_exit(self, position: dict, data: AssetData) -> Optional[str]:
        entry = position.get("entry_price", 0)
        if entry <= 0:
            return None
        pnl_pct = (data.price - entry) / entry * 100
        symbol = position.get("symbol", "")

        pair = self._find_pair(symbol)
        if pair:
            pair_key = self._get_pair_key(*pair)
            z = self._pair_zscores.get(pair_key)
            if z is not None:
                # Z-score reverted to mean
                if abs(z) < 0.5:
                    return f"Z-score reverted to {z:.2f} — mean reached"
                # Correlation broke — stop loss
                if abs(z) > 3.5:
                    return f"Z-score={z:.2f} — correlation broke, stopping out"

        if pnl_pct >= 3.0:
            return f"Take profit +{pnl_pct:.1f}%"
        if pnl_pct <= -2.0:
            return f"Stop loss {pnl_pct:.1f}%"
        return None
