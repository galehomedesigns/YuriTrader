#!/usr/bin/env python3
"""Trading Arena Runner — launches all 10 bots and orchestrates paper trading.

Usage:
    python3 arena_runner.py              # Run all bots in continuous loop
    python3 arena_runner.py --once       # Run one scan cycle and exit
    python3 arena_runner.py --status     # Show all bot statuses
    python3 arena_runner.py --leaderboard # Show P&L leaderboard
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import SCAN_INTERVAL, GLOBAL_DAILY_LOSS_LIMIT, GLOBAL_MAX_POSITIONS, SUPABASE_URL, SUPABASE_KEY
from shared.market_scanner import fetch_all
from shared.paper_trader import _supabase_get

# Import all bots
from bots.momentum_hunter import MomentumHunter
from bots.the_reverter import TheReverter
from bots.trend_rider import TrendRider

# These will be available after background agent creates them
try:
    from bots.nano_sniper import NanoSniper
    from bots.squeeze_breaker import SqueezeBreaker
    from bots.flag_rider import FlagRider
    from bots.trap_catcher import TrapCatcher
    from bots.volume_whisperer import VolumeWhisperer
    from bots.correlation_hunter import CorrelationHunter
    from bots.news_sniper import NewsSniper
    ALL_BOT_CLASSES = [
        MomentumHunter, TheReverter, NanoSniper, TrendRider, SqueezeBreaker,
        FlagRider, TrapCatcher, VolumeWhisperer, CorrelationHunter, NewsSniper,
    ]
except ImportError as e:
    print(f"Warning: Some bots not yet available: {e}", file=sys.stderr)
    ALL_BOT_CLASSES = [MomentumHunter, TheReverter, TrendRider]


def get_global_open_positions():
    """Count total open positions across all bots."""
    positions = _supabase_get("arena_trades?status=eq.open&select=id")
    return len(positions) if positions else 0


def get_global_daily_pnl():
    """Sum daily P&L across all bots."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    closed = _supabase_get(
        f"arena_trades?status=eq.closed&closed_at=gte.{today}T00:00:00Z&select=pnl"
    ) or []
    return sum(t.get("pnl", 0) for t in closed)


def get_leaderboard():
    """Get bot leaderboard from Supabase."""
    return _supabase_get("arena_balances?order=total_pnl.desc") or []


def run_once(bots, crypto_only: bool = False):
    """Run a single scan cycle across all bots.

    crypto_only=True skips the stock-data fetch (for 24/7 crypto cron).
    """
    print(f"\n{'='*60}")
    print(f"Arena Scan{' (crypto-only)' if crypto_only else ''} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # Global risk check
    global_pnl = get_global_daily_pnl()
    global_positions = get_global_open_positions()
    print(f"Global: Daily P&L=${global_pnl:.2f} | Open positions={global_positions}/{GLOBAL_MAX_POSITIONS}")

    if global_pnl <= GLOBAL_DAILY_LOSS_LIMIT:
        print(f"GLOBAL STOP — Daily loss limit hit: ${global_pnl:.2f}")
        return

    if global_positions >= GLOBAL_MAX_POSITIONS:
        print(f"GLOBAL MAX — {global_positions} positions open, skipping new entries")

    # Fetch market data
    print("\nFetching market data...")
    market_data = fetch_all(crypto_only=crypto_only)
    if not market_data:
        print("No market data available. Skipping cycle.")
        return

    print(f"Loaded {len(market_data)} assets\n")

    # Run each bot
    for bot in bots:
        try:
            bot.evaluate(market_data)
        except Exception as e:
            print(f"  [{bot.NAME}] ERROR: {e}", file=sys.stderr)

    # Print summary
    print(f"\n--- Cycle Summary ---")
    for bot in bots:
        status = bot.status()
        pos = status["open_positions"]
        pnl = status["daily_pnl"]
        state = "PAUSED" if status["paused"] else "ACTIVE"
        print(f"  {bot.NAME:20s} | {state:7s} | Positions: {pos} | Day P&L: ${pnl:.2f}")


def print_leaderboard():
    """Print the current leaderboard."""
    board = get_leaderboard()
    if not board:
        print("No data yet. Run the arena first.")
        return

    print(f"\n{'='*70}")
    print(f"  TRADING ARENA LEADERBOARD")
    print(f"{'='*70}")
    print(f"  {'Rank':<5} {'Bot':<22} {'Balance':>10} {'P&L':>10} {'Trades':>7} {'Win%':>6}")
    print(f"  {'-'*60}")

    for i, b in enumerate(board, 1):
        pnl = b.get("total_pnl", 0)
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        print(f"  {i:<5} {b.get('bot_name', '?'):<22} "
              f"${b.get('current_balance', 1000):.2f}  {pnl_str:>10} "
              f"{b.get('total_trades', 0):>7} {b.get('win_rate', 0):>5.1f}%")

    print(f"  {'-'*60}")
    total_pnl = sum(b.get("total_pnl", 0) for b in board)
    total_trades = sum(b.get("total_trades", 0) for b in board)
    print(f"  {'TOTAL':<27} {'':>10} ${total_pnl:>+9.2f} {total_trades:>7}")
    print()


def print_status(bots):
    """Print status of all bots."""
    print(f"\n{'='*60}")
    print(f"  BOT STATUS")
    print(f"{'='*60}")
    for bot in bots:
        s = bot.status()
        state = "PAUSED" if s["paused"] else "ACTIVE"
        print(f"  {bot.NAME:20s} | {state:7s} | "
              f"Balance: ${s['balance']:.2f} | "
              f"Positions: {s['open_positions']} | "
              f"Day P&L: ${s['daily_pnl']:.2f}")
        if s["paused"]:
            print(f"    Reason: {s['pause_reason']}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Trading Arena Runner")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--status", action="store_true", help="Show bot statuses")
    parser.add_argument("--leaderboard", action="store_true", help="Show P&L leaderboard")
    parser.add_argument("--crypto-only", action="store_true", help="Skip stock fetch (for 24/7 crypto cron)")
    args = parser.parse_args()

    if args.leaderboard:
        print_leaderboard()
        return

    # Initialize all bots
    bots = [cls() for cls in ALL_BOT_CLASSES]
    print(f"Trading Arena initialized with {len(bots)} bots:")
    for bot in bots:
        print(f"  - {bot.NAME} ({bot.BOT_ID})")

    if args.status:
        print_status(bots)
        return

    if args.once:
        run_once(bots, crypto_only=args.crypto_only)
        print_leaderboard()
        return

    # Continuous loop
    print(f"\nStarting continuous scanning (interval: {SCAN_INTERVAL}s)")
    print("Press Ctrl+C to stop\n")

    while True:
        try:
            run_once(bots, crypto_only=args.crypto_only)
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\nArena stopped.")
            break
        except Exception as e:
            print(f"\nArena error: {e}", file=sys.stderr)
            time.sleep(30)


if __name__ == "__main__":
    main()
