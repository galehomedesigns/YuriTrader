#!/usr/bin/env python3
"""Practice 3: Performance Analytics Engine — quantitative analysis across all bots.

Usage:
    python3 analytics.py                # Last 24 hours
    python3 analytics.py --period 7d    # Last 7 days
    python3 analytics.py --period 30d   # Last 30 days
    python3 analytics.py --bot momentum-hunter  # Specific bot only
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUPABASE_URL, SUPABASE_KEY

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


def get_trades(since, bot_id=None):
    """Fetch closed trades from Supabase."""
    params = f"arena_trades?status=eq.closed&closed_at=gte.{since}&select=*&order=closed_at.desc&limit=1000"
    if bot_id:
        params += f"&bot_id=eq.{bot_id}"
    return _supabase_get(params)


def analyze_trades(trades):
    """Run full performance analysis on a set of trades."""
    if not trades:
        return None

    # Overall stats
    total = len(trades)
    winners = [t for t in trades if (t.get("pnl") or 0) > 0]
    losers = [t for t in trades if (t.get("pnl") or 0) <= 0]
    pnls = [t.get("pnl", 0) for t in trades]
    win_pnls = [t.get("pnl", 0) for t in winners]
    lose_pnls = [t.get("pnl", 0) for t in losers]

    overall = {
        "total_trades": total,
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / total * 100, 1) if total else 0,
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / total, 4) if total else 0,
        "avg_win": round(sum(win_pnls) / len(winners), 4) if winners else 0,
        "avg_loss": round(sum(lose_pnls) / len(losers), 4) if losers else 0,
        "largest_win": round(max(pnls), 4) if pnls else 0,
        "largest_loss": round(min(pnls), 4) if pnls else 0,
        "expectancy": round(sum(pnls) / total, 4) if total else 0,
    }

    # Per-bot breakdown
    by_bot = defaultdict(list)
    for t in trades:
        by_bot[t.get("bot_id", "unknown")].append(t)

    bot_stats = {}
    for bot_id, bot_trades in by_bot.items():
        bt = len(bot_trades)
        bw = sum(1 for t in bot_trades if (t.get("pnl") or 0) > 0)
        bp = [t.get("pnl", 0) for t in bot_trades]
        bot_stats[bot_id] = {
            "bot_name": bot_trades[0].get("bot_name", bot_id),
            "trades": bt,
            "win_rate": round(bw / bt * 100, 1) if bt else 0,
            "total_pnl": round(sum(bp), 2),
            "expectancy": round(sum(bp) / bt, 4) if bt else 0,
            "avg_win": round(sum(p for p in bp if p > 0) / max(bw, 1), 4),
            "avg_loss": round(sum(p for p in bp if p <= 0) / max(bt - bw, 1), 4),
        }

    # Time-of-day analysis (30-min blocks)
    time_blocks = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0})
    for t in trades:
        closed = t.get("closed_at", "")
        if closed:
            try:
                dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
                block = f"{dt.hour:02d}:{(dt.minute // 30) * 30:02d}"
                pnl = t.get("pnl", 0)
                time_blocks[block]["trades"] += 1
                time_blocks[block]["pnl"] += pnl
                if pnl > 0:
                    time_blocks[block]["wins"] += 1
            except Exception:
                pass

    # Escalation checks
    escalations = []
    for bot_id, stats in bot_stats.items():
        if stats["expectancy"] < 0 and stats["trades"] >= 20:
            escalations.append(f"ALERT: {stats['bot_name']} has negative expectancy ({stats['expectancy']:.4f}) over {stats['trades']} trades")
        if stats["trades"] >= 10 and stats["win_rate"] < 30:
            escalations.append(f"ALERT: {stats['bot_name']} win rate critically low ({stats['win_rate']}%) over {stats['trades']} trades")

    for block, bdata in time_blocks.items():
        if bdata["trades"] >= 5 and bdata["pnl"] < 0:
            wr = bdata["wins"] / bdata["trades"] * 100
            if wr < 30:
                escalations.append(f"WARN: Time block {block} has {wr:.0f}% win rate across {bdata['trades']} trades — consider restricting")

    return {
        "overall": overall,
        "by_bot": bot_stats,
        "time_blocks": dict(time_blocks),
        "escalations": escalations,
    }


def print_report(analysis, period_label):
    """Print formatted analytics report."""
    if not analysis:
        print("No trades to analyze.")
        return

    o = analysis["overall"]
    print(f"\n{'='*70}")
    print(f"  PERFORMANCE ANALYTICS — {period_label}")
    print(f"{'='*70}")
    print(f"  Total Trades: {o['total_trades']} | Win Rate: {o['win_rate']}% | Expectancy: ${o['expectancy']:.4f}")
    print(f"  Total P&L: ${o['total_pnl']:.2f} | Avg Win: ${o['avg_win']:.4f} | Avg Loss: ${o['avg_loss']:.4f}")
    print(f"  Largest Win: ${o['largest_win']:.4f} | Largest Loss: ${o['largest_loss']:.4f}")

    print(f"\n  BOT RANKINGS (by expectancy):")
    print(f"  {'Bot':<22} {'Trades':>7} {'Win%':>6} {'Expect':>10} {'P&L':>10}")
    print(f"  {'-'*55}")
    for bot_id, s in sorted(analysis["by_bot"].items(), key=lambda x: -x[1]["expectancy"]):
        pnl_str = f"${s['total_pnl']:+.2f}"
        print(f"  {s['bot_name']:<22} {s['trades']:>7} {s['win_rate']:>5.1f}% ${s['expectancy']:>9.4f} {pnl_str:>10}")

    if analysis["time_blocks"]:
        print(f"\n  TIME-OF-DAY ANALYSIS:")
        print(f"  {'Block':<8} {'Trades':>7} {'Win%':>6} {'P&L':>10}")
        print(f"  {'-'*31}")
        for block in sorted(analysis["time_blocks"].keys()):
            b = analysis["time_blocks"][block]
            wr = b["wins"] / b["trades"] * 100 if b["trades"] else 0
            print(f"  {block:<8} {b['trades']:>7} {wr:>5.1f}% ${b['pnl']:>9.2f}")

    if analysis["escalations"]:
        print(f"\n  ESCALATIONS:")
        for e in analysis["escalations"]:
            print(f"  ⚠ {e}")

    # Store report in Supabase
    _supabase_post("arena_signals", {
        "bot_id": "overseer",
        "symbol": "REPORT",
        "action": "ANALYTICS",
        "indicators": json.dumps(analysis["overall"]),
        "confidence": o["win_rate"] / 100 if o["win_rate"] else 0,
        "executed": False,
    })

    print()


def main():
    parser = argparse.ArgumentParser(description="Trading Arena Performance Analytics")
    parser.add_argument("--period", default="1d", help="Period: 1d, 7d, 30d")
    parser.add_argument("--bot", type=str, help="Specific bot ID")
    args = parser.parse_args()

    # Parse period
    days = int(args.period.replace("d", ""))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")

    trades = get_trades(since, bot_id=args.bot)
    analysis = analyze_trades(trades)
    print_report(analysis, f"Last {days} day(s)" + (f" — {args.bot}" if args.bot else ""))


if __name__ == "__main__":
    main()
