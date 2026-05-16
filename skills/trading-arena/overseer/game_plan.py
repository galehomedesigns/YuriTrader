#!/usr/bin/env python3
"""Practice 2: Pre-Market Game Plan — generates daily prioritized watchlist with bot assignments.

Usage:
    python3 game_plan.py                # Generate today's game plan
    python3 game_plan.py --output html  # Save as HTML to canvas
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (SUPABASE_URL, SUPABASE_KEY, FINNHUB_KEY,
                    STOCK_SYMBOLS, CRYPTO_SYMBOLS, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

BOT_STRATEGIES = {
    "momentum-hunter": {"name": "Momentum Hunter", "best_for": "volume surges, breakouts, strong momentum"},
    "the-reverter": {"name": "The Reverter", "best_for": "oversold bounces, ranging markets, mean reversion"},
    "nano-sniper": {"name": "Nano Sniper", "best_for": "high-liquidity scalps, EMA alignment, tight spreads"},
    "trend-rider": {"name": "Trend Rider", "best_for": "established uptrends, pullbacks to EMA, swing setups"},
    "squeeze-breaker": {"name": "Squeeze Breaker", "best_for": "low volatility squeeze, Bollinger contraction, breakouts"},
    "flag-rider": {"name": "Flag Rider", "best_for": "impulse + consolidation patterns, flag/pennant breakouts"},
    "trap-catcher": {"name": "Trap Catcher", "best_for": "false breakouts, exhaustion moves, contrarian reversals"},
    "volume-whisperer": {"name": "Volume Whisperer", "best_for": "unusual volume activity, institutional flow, VWAP plays"},
    "correlation-hunter": {"name": "Correlation Hunter", "best_for": "pair divergences, spread trading, market neutral"},
    "news-sniper": {"name": "News Sniper", "best_for": "breaking news moves, sentiment shifts, gap plays"},
}


def _http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _supabase_get(path):
    return _http_get(f"{SUPABASE_URL}/rest/v1/{path}", HEADERS) or []


def _supabase_post(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {**HEADERS, "Prefer": "return=representation"}
    body = json.dumps(data if isinstance(data, list) else [data]).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def get_pre_market_data():
    """Fetch pre-market data for all symbols."""
    data = {}
    for sym in STOCK_SYMBOLS:
        if FINNHUB_KEY:
            quote = _http_get(f"https://finnhub.io/api/v1/quote?symbol={sym}&token={FINNHUB_KEY}")
            if quote and quote.get("c"):
                data[sym] = {
                    "price": quote["c"],
                    "change_pct": quote.get("dp", 0),
                    "high": quote.get("h", 0),
                    "low": quote.get("l", 0),
                    "prev_close": quote.get("pc", 0),
                }
            time.sleep(0.2)
    # Crypto from Kraken
    for sym in CRYPTO_SYMBOLS:
        from config import KRAKEN_PAIRS
        pair = KRAKEN_PAIRS.get(sym)
        if pair:
            ticker = _http_get(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
            if ticker and ticker.get("result"):
                info = list(ticker["result"].values())[0]
                data[sym] = {
                    "price": float(info["c"][0]),
                    "change_pct": 0,
                    "high": float(info["h"][1]),
                    "low": float(info["l"][1]),
                    "prev_close": float(info["o"]),
                }
            time.sleep(0.2)
    return data


def get_recent_bot_performance():
    """Get each bot's recent win rate and best setup type."""
    perf = {}
    balances = _supabase_get("arena_balances?select=*")
    for b in balances:
        perf[b["bot_id"]] = {
            "name": b["bot_name"],
            "win_rate": b.get("win_rate", 0),
            "total_pnl": b.get("total_pnl", 0),
            "total_trades": b.get("total_trades", 0),
        }
    return perf


def assign_bot(symbol, data, bot_perf):
    """Assign the best-fit bot for a given ticker based on its characteristics."""
    change = abs(data.get("change_pct", 0))
    price = data.get("price", 0)

    # Simple heuristic assignment
    if change > 3:
        return "news-sniper"
    elif change > 1.5:
        return "momentum-hunter"
    elif change < 0.3:
        return "squeeze-breaker"
    elif data.get("change_pct", 0) < -1:
        return "the-reverter"
    elif "/" in symbol:  # crypto pairs
        return "correlation-hunter"
    else:
        return "trend-rider"


def generate_llm_narrative(plan, bot_perf):
    """Use quick36 to synthesize a tactical pre-market narrative from the plan
    data. Returns a 4-6 sentence strategic brief, or None on failure."""
    import urllib.request, urllib.error

    top_entries = plan.get("entries", [])[:8]
    lines = []
    for e in top_entries:
        lines.append(
            f"  {e['priority']:<6} {e['symbol']:<10} ${e['price']:>8.2f} "
            f"{e['change_pct']:+.2f}% — assigned to {e['bot_name']}"
        )
    perf_lines = []
    for bot_id, perf in list(bot_perf.items())[:5]:
        wins = perf.get("wins", 0)
        losses = perf.get("losses", 0)
        pnl = perf.get("pnl", 0)
        perf_lines.append(f"  {bot_id}: {wins}W/{losses}L, ${pnl:+.2f} 7d P&L")

    prompt = f"""You are the overseer of Tony's autonomous trading arena. Today is {plan['date']}.
Write a tight pre-market briefing (4-6 sentences, plain text, no markdown).

Lead with the highest-priority symbols and what the assigned bots are positioned to do.
Mention any recent bot performance worth flagging (hot streaks, recent losses).
Skip the table — just the strategic read.

Top symbols ({plan['high_priority']} HIGH priority of {plan['total_symbols']} total):
{chr(10).join(lines) if lines else '  (none)'}

Recent bot performance (last 7 days):
{chr(10).join(perf_lines) if perf_lines else '  (no recent trades)'}"""

    payload = json.dumps({
        "model": "quick36:latest",
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.4, "num_ctx": 8192, "num_predict": 500},
    }).encode()

    url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434") + "/api/generate"
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read()).get("response", "").strip() or None
    except Exception as e:
        print(f"(narrative unavailable: {type(e).__name__}: {str(e)[:100]})", file=sys.stderr)
        return None


def generate_game_plan():
    """Generate the full pre-market game plan."""
    now = datetime.now()
    print(f"Generating pre-market game plan — {now.strftime('%Y-%m-%d %H:%M')}")

    market_data = get_pre_market_data()
    bot_perf = get_recent_bot_performance()

    if not market_data:
        print("No market data available.")
        return None

    # Build plan entries
    entries = []
    for sym, data in market_data.items():
        assigned_bot = assign_bot(sym, data, bot_perf)
        change = data.get("change_pct", 0)
        price = data.get("price", 0)

        # Priority based on movement + volume
        if abs(change) > 3:
            priority = "HIGH"
        elif abs(change) > 1:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        entries.append({
            "symbol": sym,
            "price": price,
            "change_pct": change,
            "high": data.get("high", 0),
            "low": data.get("low", 0),
            "priority": priority,
            "assigned_bot": assigned_bot,
            "bot_name": BOT_STRATEGIES.get(assigned_bot, {}).get("name", assigned_bot),
        })

    # Sort by priority
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    entries.sort(key=lambda e: (priority_order.get(e["priority"], 3), -abs(e["change_pct"])))

    # Check for conflicts (same ticker, different bots with opposing bias — N/A in paper trading)
    conflicts = []

    # Build report
    plan = {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
        "entries": entries,
        "conflicts": conflicts,
        "total_symbols": len(entries),
        "high_priority": sum(1 for e in entries if e["priority"] == "HIGH"),
        "active_bots": len(bot_perf),
    }

    # LLM narrative on top, then the table for verification
    narrative = generate_llm_narrative(plan, bot_perf)
    if narrative:
        print()
        print(narrative)
        plan["narrative"] = narrative

    # Print
    print(f"\n{'='*70}")
    print(f"  PRE-MARKET GAME PLAN — {now.strftime('%A, %B %d, %Y')}")
    print(f"{'='*70}")
    print(f"  Symbols: {plan['total_symbols']} | High Priority: {plan['high_priority']} | Bots: {plan['active_bots']}")
    print(f"{'='*70}")
    print(f"  {'Priority':<10} {'Symbol':<12} {'Price':>10} {'Change':>8} {'High':>10} {'Low':>10} {'Assigned Bot':<20}")
    print(f"  {'-'*80}")
    for e in entries:
        chg = f"{e['change_pct']:+.1f}%"
        print(f"  {e['priority']:<10} {e['symbol']:<12} ${e['price']:>9.2f} {chg:>8} "
              f"${e['high']:>9.2f} ${e['low']:>9.2f} {e['bot_name']:<20}")

    if conflicts:
        print(f"\n  ⚠ CONFLICTS:")
        for c in conflicts:
            print(f"    {c}")

    print()
    return plan


def main():
    parser = argparse.ArgumentParser(description="Pre-Market Game Plan Generator")
    parser.add_argument("--output", choices=["text", "html", "json"], default="text")
    args = parser.parse_args()

    plan = generate_game_plan()
    if plan and args.output == "json":
        print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
