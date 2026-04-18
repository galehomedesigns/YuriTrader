#!/usr/bin/env python3
"""TAY Component Analytics — analyzes which T/A/Y components are winning.

Reads closed trades from arena_trades, groups by TAY component, and reports
which trends, areas of value, and entry triggers are most profitable.

This is the data-driven feedback loop: the digester told us what components
to test, the arena tests them, and this script tells us which combinations
actually make money.

Usage:
    python3 tay_analytics.py            # Print analysis to stdout + save markdown
    python3 tay_analytics.py --telegram # Also send summary to Telegram
"""
import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "TAY_ANALYTICS.md"
)


def supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def send_telegram(message):
    if not TELEGRAM_TOKEN:
        return
    try:
        data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def categorize_reason(reason: str) -> str:
    """Map a TAY reason string into a coarse category for grouping."""
    if not reason:
        return "unknown"
    r = reason.lower()
    # Trend categories
    if "ema200" in r or "200ma" in r:
        return "200_ma_filter"
    if "ema21>" in r or "ema21 >" in r:
        return "ema_hierarchy"
    if "ranging" in r or "adx" in r and "<" in r:
        return "ranging"
    if "trending" in r or "uptrend" in r:
        return "trending"
    if "squeeze" in r or "bw=" in r:
        return "bb_squeeze"
    if "weakening" in r:
        return "weakening_trend"
    if "impulse" in r or "pole" in r:
        return "impulse_pole"
    if "obv" in r:
        return "obv_accumulation"
    # Area of value
    if "support" in r and "$" in r:
        return "horizontal_support"
    if "resistance" in r and "$" in r:
        return "horizontal_resistance"
    if "50ema" in r or "50 ema" in r or "ema50" in r:
        return "ma_pullback"
    if "vwap" in r:
        return "vwap_zone"
    if "bb lower" in r or "bb upper" in r:
        return "bb_band"
    if "consolidation" in r or "flag" in r:
        return "consolidation"
    # Triggers
    if "hammer" in r:
        return "hammer_candle"
    if "engulfing" in r:
        return "engulfing_candle"
    if "doji" in r:
        return "doji_candle"
    if "vol" in r and "x" in r:
        return "volume_spike"
    if "macd" in r:
        return "macd_cross"
    if "rsi" in r:
        return "rsi_signal"
    return "other"


def analyze():
    """Pull all closed trades with tay_components and group by component."""
    trades = supabase_get(
        "arena_trades?status=eq.closed&tay_components=not.is.null"
        "&select=bot_name,symbol,pnl,pnl_pct,tay_components,closed_at"
        "&order=closed_at.desc&limit=500"
    )

    if not trades:
        return None

    # Group by component category
    t_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    a_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    y_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})

    for trade in trades:
        tay = trade.get("tay_components") or {}
        if isinstance(tay, str):
            try:
                tay = json.loads(tay)
            except json.JSONDecodeError:
                continue
        pnl = trade.get("pnl") or 0
        win = pnl > 0

        t_cat = categorize_reason(tay.get("t_reason", ""))
        a_cat = categorize_reason(tay.get("a_reason", ""))
        y_cat = categorize_reason(tay.get("y_reason", ""))

        for stats, cat in [(t_stats, t_cat), (a_stats, a_cat), (y_stats, y_cat)]:
            stats[cat]["trades"] += 1
            stats[cat]["pnl"] += pnl
            if win:
                stats[cat]["wins"] += 1

    return {
        "total_trades": len(trades),
        "trend": dict(t_stats),
        "value": dict(a_stats),
        "trigger": dict(y_stats),
    }


def format_section(title, stats):
    """Format one section (T, A, or Y) as a table."""
    lines = [f"\n## {title}", ""]
    lines.append("| Component | Trades | Wins | Win % | Total P&L | Avg P&L |")
    lines.append("|-----------|--------|------|-------|-----------|---------|")
    rows = sorted(stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
    for cat, s in rows:
        if s["trades"] == 0:
            continue
        win_pct = (s["wins"] / s["trades"] * 100)
        avg = s["pnl"] / s["trades"]
        lines.append(
            f"| {cat} | {s['trades']} | {s['wins']} | {win_pct:.1f}% | "
            f"${s['pnl']:+.2f} | ${avg:+.2f} |"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--telegram", action="store_true", help="Send summary to Telegram")
    args = parser.parse_args()

    print("Loading closed trades with TAY components...", file=sys.stderr)
    result = analyze()
    if result is None:
        print("No TAY-tagged trades yet — need to wait for new trades after this upgrade.")
        return

    md = []
    md.append(f"<!-- Generated {datetime.now(timezone.utc).isoformat()} -->")
    md.append(f"# TAY Component Analytics")
    md.append(f"\nAnalyzed {result['total_trades']} closed trades with TAY tagging.")
    md.append("\nThis report tells us which Trend / Area of Value / Trigger components")
    md.append("are actually making money in the arena. Compare to STRATEGY_DIGEST.md to")
    md.append("see how the YouTube research holds up against real performance.\n")

    md.append(format_section("Trend (T) Performance", result["trend"]))
    md.append(format_section("Area of Value (A) Performance", result["value"]))
    md.append(format_section("Trigger (Y) Performance", result["trigger"]))

    md.append("\n## Recommendations")
    # Best performers
    for label, stats in [("trend", result["trend"]),
                         ("value", result["value"]),
                         ("trigger", result["trigger"])]:
        ranked = sorted(stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
        if ranked and ranked[0][1]["trades"] >= 3:
            best = ranked[0]
            md.append(f"- **Best {label}**: `{best[0]}` "
                      f"({best[1]['trades']} trades, "
                      f"{best[1]['wins']/best[1]['trades']*100:.0f}% win, "
                      f"${best[1]['pnl']:+.2f})")

    output = "\n".join(md)
    with open(OUTPUT_FILE, "w") as f:
        f.write(output)
    print(output)
    print(f"\nSaved to {OUTPUT_FILE}", file=sys.stderr)

    if args.telegram:
        # Compact summary for Telegram
        lines = [f"📊 <b>TAY Component Analytics</b>"]
        lines.append(f"\n{result['total_trades']} closed trades analyzed.\n")
        for label, stats in [("Trend", result["trend"]),
                             ("Value", result["value"]),
                             ("Trigger", result["trigger"])]:
            ranked = sorted(stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
            if ranked and ranked[0][1]["trades"] >= 3:
                best = ranked[0]
                wp = best[1]["wins"] / best[1]["trades"] * 100
                lines.append(f"<b>{label}</b>: <code>{best[0]}</code> "
                             f"{wp:.0f}% win, ${best[1]['pnl']:+.2f}")
        send_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
