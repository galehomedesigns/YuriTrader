#!/usr/bin/env python3
"""Trade Advisor — the "what's the best trade right now?" brain.

Fetches current crypto market data, runs all 10 bots' TAY logic against each
Kraken-tradable symbol, ranks them by how many bots agree, and returns the
top opportunity with a human-readable analysis from GX10 `quick` model.

This is the overseer's real-time research assistant. It doesn't place trades —
it just tells the human (via trading_concierge.py) what to consider.

Usage:
    python3 advisor.py              # Print top opportunity to stdout
    python3 advisor.py --top 3      # Print top 3
    python3 advisor.py --json       # Machine-readable output for concierge
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.market_scanner import (
    fetch_crypto_data, fetch_stock_data, fetch_stock_data_questrade,
    fetch_dynamic_watchlist,
)
from shared.kraken_executor import KRAKEN_PAIR_MAP
from shared.indicators import atr_stop_loss, atr_take_profit

# Import all 10 bots
from bots.momentum_hunter import MomentumHunter
from bots.the_reverter import TheReverter
from bots.nano_sniper import NanoSniper
from bots.trend_rider import TrendRider
from bots.squeeze_breaker import SqueezeBreaker
from bots.flag_rider import FlagRider
from bots.trap_catcher import TrapCatcher
from bots.volume_whisperer import VolumeWhisperer
from bots.correlation_hunter import CorrelationHunter
from bots.news_sniper import NewsSniper

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = "quick"

# The crypto symbols the concierge can recommend (must match KRAKEN_PAIR_MAP)
CRYPTO_SYMBOLS = list(KRAKEN_PAIR_MAP.keys())


def _get_all_bots():
    """Instantiate all 10 bots (each connects to Supabase for its balance)."""
    return [
        MomentumHunter(),
        TheReverter(),
        NanoSniper(),
        TrendRider(),
        SqueezeBreaker(),
        FlagRider(),
        TrapCatcher(),
        VolumeWhisperer(),
        CorrelationHunter(),
        NewsSniper(),
    ]


def score_symbol(symbol, data, bots):
    """Run each bot's TAY check on a symbol's AssetData.

    Returns a dict with:
      - firing_bots: list of bot names where T+A+Y all pass
      - partial_bots: list of bot names where at least T passes
      - tay_breakdown: per-bot T/A/Y results for inspection
    """
    firing = []
    partial = []
    breakdown = []

    for bot in bots:
        # Correlation Hunter has custom logic — it needs its pair context,
        # which isn't meaningful for single-symbol evaluation. Skip it here.
        if bot.BOT_ID == "correlation-hunter":
            continue

        try:
            components = bot.get_tay_components(data)
        except Exception as e:
            continue

        breakdown.append({
            "bot": bot.NAME,
            "t_pass": components["t_pass"],
            "t_reason": components["t_reason"],
            "a_pass": components["a_pass"],
            "a_reason": components["a_reason"],
            "y_pass": components["y_pass"],
            "y_reason": components["y_reason"],
        })

        if components["t_pass"] and components["a_pass"] and components["y_pass"]:
            firing.append(bot.NAME)
        elif components["t_pass"]:
            partial.append(bot.NAME)

    return {
        "symbol": symbol,
        "price": data.price,
        "day_change_pct": data.day_change_pct,
        "rsi_14": data.rsi_14,
        "adx_14": data.adx_14,
        "bb_bandwidth": data.bb_bandwidth,
        "candlestick_pattern": data.candlestick_pattern,
        "atr_14": data.atr_14,
        "firing_bots": firing,
        "partial_bots": partial,
        "firing_count": len(firing),
        "partial_count": len(partial),
        "breakdown": breakdown,
    }


def compute_entry_levels(symbol, data):
    """Compute entry, stop, and target using ATR-based risk management."""
    price = data.price
    atr = data.atr_14

    if atr and atr > 0:
        stop = atr_stop_loss(price, atr, direction="long", multiplier=1.0)
        target = atr_take_profit(price, atr, direction="long", multiplier=2.0)
        stop_pct = (stop - price) / price * 100
        target_pct = (target - price) / price * 100
        rr = abs(target_pct / stop_pct) if stop_pct else 0
    else:
        # Fallback to fixed percentages
        stop = price * 0.985
        target = price * 1.03
        stop_pct = -1.5
        target_pct = 3.0
        rr = 2.0

    return {
        "entry": price,
        "stop": stop,
        "target": target,
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "rr": rr,
    }


def call_gx10(prompt, timeout=120):
    """Send a prompt to the GX10 `quick` model and return the response text."""
    url = f"{OLLAMA_URL}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,  # quick:latest is thinking-capable; skip chain-of-thought for latency
        "keep_alive": "1h",  # keep model in VRAM between arena calls, avoid cold start
        "options": {"num_ctx": 8192, "temperature": 0.3},
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode()).get("response", "").strip()
    except Exception as e:
        return f"(GX10 unavailable: {str(e)[:80]})"


def build_analysis_prompt(opp, levels):
    """Build a concise prompt for GX10 to synthesize an analysis."""
    firing = ", ".join(opp["firing_bots"]) or "none"
    partial = ", ".join(opp["partial_bots"][:3]) or "none"

    # Pick 3 most informative TAY breakdowns for context
    breakdown_lines = []
    for b in opp["breakdown"][:5]:
        t = "✓" if b["t_pass"] else "✗"
        a = "✓" if b["a_pass"] else "✗"
        y = "✓" if b["y_pass"] else "✗"
        breakdown_lines.append(
            f"  {b['bot']}: T{t} A{a} Y{y} — T:{b['t_reason'][:40]} | A:{b['a_reason'][:40]} | Y:{b['y_reason'][:40]}"
        )
    breakdown_text = "\n".join(breakdown_lines)

    # Defensive defaults — stock data may have missing indicators if OHLC fetch failed
    rsi = opp['rsi_14'] or 0
    adx = opp['adx_14'] or 0
    bbw = opp['bb_bandwidth'] or 0
    atr = opp['atr_14'] or 0

    return f"""You are a trading advisor. A human trader is asking for a recommendation. Write a brief, data-driven analysis (3-4 sentences MAX). Be honest about risk. No filler, no hype.

SYMBOL: {opp['symbol']}
PRICE: ${opp['price']:.4f}
24h change: {opp['day_change_pct']:+.2f}%
RSI(14): {rsi:.0f}
ADX(14): {adx:.0f}
BB bandwidth: {bbw:.4f}
Candlestick: {opp['candlestick_pattern'] or 'none'}
ATR(14): ${atr:.4f}

{opp['firing_count']} of 9 bots would enter: {firing}
Partial matches (trend aligns): {partial}

TAY breakdown (T=trend, A=area of value, Y=trigger):
{breakdown_text}

Suggested levels:
  Entry: ${levels['entry']:.4f}
  Stop:  ${levels['stop']:.4f} ({levels['stop_pct']:+.1f}%)
  Target: ${levels['target']:.4f} ({levels['target_pct']:+.1f}%)
  R:R: {levels['rr']:.1f}:1

Write the analysis now. Focus on: why this setup, what could go wrong, and whether it's worth taking. Be direct."""


def _get_stock_watchlist():
    """Read the dynamic stock watchlist from Supabase arena_watchlist.

    fetch_dynamic_watchlist() returns (stock_symbols, crypto_symbols) already
    filtered by asset_type. Falls back to the static STOCK_SYMBOLS config.
    Returns a list of stock ticker strings.
    """
    try:
        stock_syms, _ = fetch_dynamic_watchlist()
        return list(stock_syms)
    except Exception as e:
        print(f"  dynamic watchlist fetch failed: {e}", file=sys.stderr)
        return []


def get_top_opportunity(top_n=1, asset_class="crypto"):
    """Main entry point — returns the top N opportunities with analysis.

    Args:
        top_n: how many opportunities to return
        asset_class: "crypto" (default, fetches from Kraken) or "stock"
                     (fetches from Supabase arena_watchlist + Finnhub/TwelveData)

    Returns:
        list of dicts, each containing:
          - symbol, price, firing_count, firing_bots
          - levels (entry/stop/target)
          - analysis (GX10 text)
          - raw_score_data (full breakdown)
    """
    # Fetch live market data based on asset class
    if asset_class == "crypto":
        market_data = fetch_crypto_data(CRYPTO_SYMBOLS)
    elif asset_class == "stock":
        stock_symbols = _get_stock_watchlist()
        if not stock_symbols:
            return [{"error": "No stocks in dynamic watchlist"}]
        # Questrade is the primary stock data source — it serves quotes AND
        # OHLC candles through one auth path, and supports Canadian symbols.
        market_data = fetch_stock_data_questrade(stock_symbols)
        # Filter to symbols that actually got indicators populated
        market_data = {k: v for k, v in market_data.items() if v.rsi_14 is not None}
        if not market_data:
            return [{"error": "No stock market data — check Questrade auth"}]
    else:
        return [{"error": f"Unknown asset_class: {asset_class}"}]

    if not market_data:
        return [{"error": "No market data available"}]

    # Instantiate bots once
    bots = _get_all_bots()

    # Score each symbol
    opportunities = []
    for symbol, data in market_data.items():
        opportunities.append(score_symbol(symbol, data, bots))

    # Rank by firing count (most bots agreeing), tiebreak by partial count
    opportunities.sort(key=lambda o: (o["firing_count"], o["partial_count"]), reverse=True)

    # Take top N
    top = opportunities[:top_n]

    # Add analysis to each
    results = []
    for opp in top:
        # Find the AssetData for computing levels
        data = market_data.get(opp["symbol"])
        if not data:
            continue
        levels = compute_entry_levels(opp["symbol"], data)

        # Call GX10 for human-readable analysis
        prompt = build_analysis_prompt(opp, levels)
        analysis = call_gx10(prompt)

        results.append({
            "symbol": opp["symbol"],
            "price": opp["price"],
            "day_change_pct": opp["day_change_pct"],
            "firing_count": opp["firing_count"],
            "firing_bots": opp["firing_bots"],
            "partial_bots": opp["partial_bots"][:3],
            "levels": levels,
            "analysis": analysis,
            "indicators": {
                "rsi_14": opp["rsi_14"],
                "adx_14": opp["adx_14"],
                "bb_bandwidth": opp["bb_bandwidth"],
                "candlestick": opp["candlestick_pattern"],
                "atr_14": opp["atr_14"],
            },
        })

    return results


def format_text_output(results):
    """Format results for stdout (not Telegram)."""
    if not results or "error" in results[0]:
        return "No opportunities found."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"=" * 60)
        lines.append(f"#{i}: {r['symbol']} @ ${r['price']:.4f} ({r['day_change_pct']:+.2f}% 24h)")
        lines.append(f"    {r['firing_count']} of 9 bots firing: {', '.join(r['firing_bots']) or 'none'}")
        lines.append(f"    RSI:{r['indicators']['rsi_14']:.0f}  ADX:{r['indicators']['adx_14']:.0f}  "
                     f"Candle:{r['indicators']['candlestick'] or 'none'}")
        lines.append(f"")
        lines.append(f"    Entry:  ${r['levels']['entry']:.4f}")
        lines.append(f"    Stop:   ${r['levels']['stop']:.4f} ({r['levels']['stop_pct']:+.1f}%)")
        lines.append(f"    Target: ${r['levels']['target']:.4f} ({r['levels']['target_pct']:+.1f}%)")
        lines.append(f"    R:R:    {r['levels']['rr']:.1f}:1")
        lines.append(f"")
        lines.append(f"    ANALYSIS:")
        lines.append(f"    {r['analysis']}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=1, help="How many opportunities to return")
    parser.add_argument("--json", action="store_true", help="JSON output for machine consumption")
    args = parser.parse_args()

    results = get_top_opportunity(top_n=args.top)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(format_text_output(results))


if __name__ == "__main__":
    main()
