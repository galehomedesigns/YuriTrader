#!/usr/bin/env python3
"""Practice 5: Weekly Super-Prompt — identifies ONE focus improvement per bot.

Usage:
    python3 super_prompt.py           # Run weekly analysis
    python3 super_prompt.py --output json  # Output as JSON
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUPABASE_URL, SUPABASE_KEY, OLLAMA_URL

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


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


def call_llm(prompt):
    """Call LLM for super-prompt analysis."""
    import httpx
    opts = {"temperature": 0.2, "num_predict": 4096, "num_ctx": 16384}
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": "quick:latest", "prompt": prompt, "stream": False,
                   "options": opts},
            timeout=300,
        )
        if r.status_code == 200:
            return r.json().get("response", "")
    except Exception:
        pass
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": "gemma:latest", "prompt": prompt, "stream": False,
                   "options": opts},
            timeout=300,
        )
        if r.status_code == 200:
            return r.json().get("response", "")
    except Exception:
        pass
    return "LLM unavailable."


def run_super_prompt():
    """Run the weekly super-prompt analysis."""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")

    # Get all trades from past week
    trades = _supabase_get(
        f"arena_trades?status=eq.closed&closed_at=gte.{since}&select=*&order=closed_at.desc&limit=500"
    )
    balances = _supabase_get("arena_balances?order=total_pnl.desc")

    if not trades:
        print("No trades this week to analyze.")
        return

    # Build trade summary per bot
    from collections import defaultdict
    by_bot = defaultdict(list)
    for t in trades:
        by_bot[t.get("bot_id", "unknown")].append(t)

    bot_summaries = []
    for bot_id, bot_trades in by_bot.items():
        wins = sum(1 for t in bot_trades if (t.get("pnl") or 0) > 0)
        total = len(bot_trades)
        total_pnl = sum(t.get("pnl", 0) for t in bot_trades)
        symbols = list(set(t.get("symbol") for t in bot_trades))
        reasons = [t.get("reason", "")[:50] for t in bot_trades[:5]]
        exit_reasons = [t.get("exit_reason", "")[:50] for t in bot_trades[:5]]

        bot_summaries.append(
            f"BOT: {bot_trades[0].get('bot_name', bot_id)}\n"
            f"  Trades: {total} | Wins: {wins} | Win Rate: {wins/total*100:.0f}% | P&L: ${total_pnl:.2f}\n"
            f"  Symbols traded: {', '.join(symbols[:5])}\n"
            f"  Sample entries: {'; '.join(reasons)}\n"
            f"  Sample exits: {'; '.join(exit_reasons)}"
        )

    # Build the super-prompt
    prompt = f"""WEEKLY PORTFOLIO AUTOPSY — SUPER PROMPT

Period: Last 7 days
Total trades across all bots: {len(trades)}
Active bots: {len(by_bot)}

BOT PERFORMANCE SUMMARIES:
{chr(10).join(bot_summaries)}

LEADERBOARD:
{json.dumps([dict(bot_id=b['bot_id'], bot_name=b['bot_name'], total_pnl=b.get('total_pnl',0), win_rate=b.get('win_rate',0), trades=b.get('total_trades',0)) for b in balances], indent=2)}

ANALYSIS REQUIRED:

1. PER-BOT PATTERNS: What is the recurring pattern for each bot this week?

2. CROSS-BOT PATTERNS: Are multiple bots making the same mistake?

3. RULE VIOLATIONS: List any bot consistently hitting stop losses or taking exits that don't match its strategy.

4. THE ONE THING: For each bot, define the SINGLE most important focus for next week. Do NOT list multiple — force-rank to ONE.

5. PROPOSED RULE CHANGES: Based on data only, propose specific parameter adjustments (e.g., "Momentum Hunter should increase volume threshold from 2x to 2.5x").

6. CAPITAL ALLOCATION: Should any bot get more or less virtual capital based on performance?

Be specific, concise, and data-driven. No vague advice."""

    print(f"\n{'='*70}")
    print(f"  WEEKLY SUPER-PROMPT ANALYSIS")
    print(f"  Period: Last 7 days | Trades: {len(trades)} | Bots: {len(by_bot)}")
    print(f"{'='*70}\n")

    analysis = call_llm(prompt)
    print(analysis)

    # Store the analysis
    _supabase_post("arena_signals", {
        "bot_id": "overseer",
        "symbol": "WEEKLY",
        "action": "SUPER_PROMPT",
        "indicators": json.dumps({
            "total_trades": len(trades),
            "active_bots": len(by_bot),
            "period": "7d",
        }),
        "executed": False,
    })

    print(f"\n{'='*70}")
    return analysis


def main():
    parser = argparse.ArgumentParser(description="Weekly Super-Prompt Analysis")
    parser.add_argument("--output", choices=["text", "json"], default="text")
    args = parser.parse_args()
    run_super_prompt()


if __name__ == "__main__":
    main()
