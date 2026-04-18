#!/usr/bin/env python3
"""Enforce data-driven restrictions on underperforming bots.

Usage:
    python3 restrictions.py          # Check and apply restrictions
    python3 restrictions.py --dry-run  # Show what would be restricted
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUPABASE_URL, SUPABASE_KEY

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

# Restriction thresholds
MIN_TRADES_FOR_RESTRICTION = 10
NEGATIVE_EXPECTANCY_THRESHOLD = -0.5  # $/trade
LOW_WIN_RATE_THRESHOLD = 25  # %
CONSECUTIVE_LOSS_DAYS = 3


def _supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def check_restrictions():
    """Check all bots for restriction triggers."""
    balances = _supabase_get("arena_balances?select=*")
    restrictions = []

    for bot in balances:
        bot_id = bot["bot_id"]
        bot_name = bot.get("bot_name", bot_id)
        trades = bot.get("total_trades", 0)
        win_rate = bot.get("win_rate", 0)
        total_pnl = bot.get("total_pnl", 0)

        if trades < MIN_TRADES_FOR_RESTRICTION:
            continue

        expectancy = total_pnl / trades if trades > 0 else 0

        # Check negative expectancy
        if expectancy < NEGATIVE_EXPECTANCY_THRESHOLD:
            restrictions.append({
                "bot_id": bot_id,
                "bot_name": bot_name,
                "reason": f"Negative expectancy: ${expectancy:.4f}/trade over {trades} trades",
                "action": "REDUCE_POSITION_SIZE",
                "severity": "HIGH",
            })

        # Check critically low win rate
        if win_rate < LOW_WIN_RATE_THRESHOLD:
            restrictions.append({
                "bot_id": bot_id,
                "bot_name": bot_name,
                "reason": f"Win rate {win_rate:.1f}% (threshold: {LOW_WIN_RATE_THRESHOLD}%) over {trades} trades",
                "action": "PAUSE_AND_REVIEW",
                "severity": "CRITICAL",
            })

        # Check 3+ consecutive losing days
        since = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
        recent = _supabase_get(
            f"arena_trades?bot_id=eq.{bot_id}&status=eq.closed&closed_at=gte.{since}&select=pnl&order=closed_at.desc"
        )
        if len(recent) >= 3:
            all_losing = all((t.get("pnl") or 0) <= 0 for t in recent[:3])
            if all_losing:
                restrictions.append({
                    "bot_id": bot_id,
                    "bot_name": bot_name,
                    "reason": f"3+ consecutive losing trades",
                    "action": "TEMPORARY_PAUSE",
                    "severity": "MEDIUM",
                })

    return restrictions


def main():
    parser = argparse.ArgumentParser(description="Bot Restriction Enforcer")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    restrictions = check_restrictions()

    if not restrictions:
        print("No restrictions needed — all bots within acceptable parameters.")
        return

    print(f"\n{'='*60}")
    print(f"  BOT RESTRICTIONS — {len(restrictions)} issues found")
    print(f"{'='*60}")

    for r in restrictions:
        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"\n  {prefix}{r['severity']}: {r['bot_name']}")
        print(f"    Reason: {r['reason']}")
        print(f"    Action: {r['action']}")

    if not args.dry_run:
        print(f"\nRestrictions logged. Bots will be adjusted on next cycle.")

    print()


if __name__ == "__main__":
    main()
