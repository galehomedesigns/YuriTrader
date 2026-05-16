#!/usr/bin/env python3
"""Practice 5: AI Trade Autopsy — post-session analysis of every trade.

Usage:
    python3 autopsy.py              # Analyze today's closed trades
    python3 autopsy.py --bot momentum-hunter  # Specific bot only
    python3 autopsy.py --all        # Analyze all trades with no autopsy yet
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUPABASE_URL, SUPABASE_KEY, OLLAMA_URL

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

BOT_RULES = {
    "momentum-hunter": "Entry: RSI>50 + MACD bullish + Volume>2x + Price>EMA50. Exit: +3% TP, -1% SL, RSI>80, MACD bearish.",
    "the-reverter": "Entry: RSI<30 + price at BB lower + ADX<25. Exit: Price at BB middle, +1.5% TP, -2% SL.",
    "nano-sniper": "Entry: EMA 8>21>55>200 alignment + above VWAP. Exit: +0.3% TP, -0.2% SL, EMA breaks.",
    "trend-rider": "Entry: Uptrend EMA21>EMA50 + pullback to 21 EMA + low volume. Exit: +3% TP, -1.5% SL, below EMA21.",
    "squeeze-breaker": "Entry: BB squeeze (bandwidth<0.03) + breakout above upper BB + volume. Exit: +2.5% TP, -1% SL.",
    "flag-rider": "Entry: Strong impulse (>2%) + tight consolidation + breakout on volume. Exit: +2% TP, -1% SL.",
    "trap-catcher": "Entry: RSI reverting from extreme (>75 or <25) + declining volume. Exit: +3% TP, -1.5% SL, RSI at 50.",
    "volume-whisperer": "Entry: Above VWAP + OBV positive + RVOL>1.5. Exit: +2% TP, -1% SL, below VWAP.",
    "correlation-hunter": "Entry: Z-score>2 on pair spread. Exit: Z-score reverts to 0, Z>3.5 stop.",
    "news-sniper": "Entry: Big move (>3%) + high volume + RSI not extreme. Exit: +1% TP, -0.5% SL, 30min time decay.",
}


def _supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def _supabase_patch(table, match, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{match}"
    headers = {**HEADERS, "Prefer": "return=representation"}
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def call_llm(prompt):
    """Call Ollama for autopsy analysis."""
    import httpx
    opts = {"temperature": 0.2, "num_predict": 1024, "num_ctx": 8192}
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": "quick36:latest", "prompt": prompt, "stream": False,
                   "options": opts},
            timeout=120,
        )
        if r.status_code == 200:
            return r.json().get("response", "")
    except Exception as e:
        print(f"  LLM error: {e}", file=sys.stderr)
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": "quality:latest", "prompt": prompt, "stream": False,
                   "options": opts},
            timeout=120,
        )
        if r.status_code == 200:
            return r.json().get("response", "")
    except Exception:
        pass
    return "LLM unavailable for autopsy."


def autopsy_trade(trade):
    """Run autopsy on a single trade."""
    bot_id = trade.get("bot_id", "unknown")
    rules = BOT_RULES.get(bot_id, "No rules defined")

    prompt = f"""Analyze this paper trade for the {trade.get('bot_name', bot_id)} bot.

SETUP:
- Symbol: {trade.get('symbol')}
- Side: {trade.get('side')}
- Entry: ${trade.get('entry_price', 0):.4f}
- Exit: ${trade.get('exit_price', 0):.4f}
- P&L: ${trade.get('pnl', 0):.4f} ({trade.get('pnl_pct', 0):.2f}%)
- Entry reason: {trade.get('reason', 'Not recorded')}
- Exit reason: {trade.get('exit_reason', 'Not recorded')}
- Duration: opened {trade.get('opened_at', '?')} → closed {trade.get('closed_at', '?')}

BOT RULES: {rules}

QUESTIONS:
1. Did entry meet the bot's rules? Clean or forced?
2. Did the bot follow its exit plan?
3. What behavioral pattern does this suggest?
4. ONE thing to improve for this bot?

Keep response under 150 words. Be specific and data-driven."""

    return call_llm(prompt)


def main():
    parser = argparse.ArgumentParser(description="AI Trade Autopsy")
    parser.add_argument("--bot", type=str, help="Specific bot ID")
    parser.add_argument("--all", action="store_true", help="All un-autopsied trades")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params = "arena_trades?status=eq.closed&select=*&order=closed_at.desc&limit=50"
    if args.bot:
        params += f"&bot_id=eq.{args.bot}"
    if not args.all:
        params += f"&closed_at=gte.{today}T00:00:00Z"

    trades = _supabase_get(params)
    if not trades:
        print("No trades to autopsy.")
        return

    print(f"\n{'='*60}")
    print(f"  TRADE AUTOPSIES — {len(trades)} trades")
    print(f"{'='*60}\n")

    for i, trade in enumerate(trades, 1):
        pnl = trade.get("pnl", 0)
        emoji = "+" if pnl >= 0 else ""
        print(f"--- Trade {i}: {trade.get('bot_name')} | {trade.get('symbol')} | "
              f"{emoji}${pnl:.4f} ({trade.get('pnl_pct', 0):.2f}%) ---")

        analysis = autopsy_trade(trade)
        print(f"{analysis}\n")

    print(f"{'='*60}")
    print(f"Autopsied {len(trades)} trades.")


if __name__ == "__main__":
    main()
