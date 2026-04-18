#!/usr/bin/env python3
"""
Market data management — snapshots, alerts, watchlist, history.

Usage:
    python3 market_data.py snapshot                     # Store portfolio snapshot
    python3 market_data.py check-alerts                 # Check price alert thresholds
    python3 market_data.py watchlist                    # Show watchlist
    python3 market_data.py add-watch <symbol>           # Add to watchlist
    python3 market_data.py remove-watch <symbol>        # Remove from watchlist
    python3 market_data.py set-alert <symbol> <above|below> <price>
    python3 market_data.py history <symbol> [days]      # Historical snapshots
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
QUESTRADE_SCRIPT = "/home/tonygale/openclaw/skills/questrade/scripts/questrade.py"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def supabase_get(table, params=None):
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": "return=representation"},
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def supabase_post(table, data):
    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": "return=representation"},
        json=data,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def supabase_upsert(table, data, on_conflict="key"):
    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            **HEADERS,
            "Prefer": "return=representation,resolution=merge-duplicates",
        },
        json=data,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def supabase_patch(table, params, data):
    resp = httpx.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": "return=representation"},
        params=params,
        json=data,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_config(key):
    rows = supabase_get("trading_config", {"key": f"eq.{key}", "select": "value"})
    if rows:
        return rows[0]["value"]
    return None


def set_config(key, value):
    supabase_upsert("trading_config", {"key": key, "value": json.dumps(value) if isinstance(value, (list, dict)) else value, "updated_at": datetime.utcnow().isoformat()})


def run_questrade(*args):
    result = subprocess.run(
        ["python3", QUESTRADE_SCRIPT] + list(args),
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout, result.stderr, result.returncode


def cmd_snapshot():
    """Fetch quotes for watchlist + positions and store snapshots."""
    watchlist = get_config("watchlist") or []

    # Get position symbols from Questrade
    stdout, stderr, rc = run_questrade("portfolio")
    if rc != 0:
        print(f"ERROR: Questrade portfolio failed: {stderr}")
        return

    print(stdout)

    # Combine watchlist symbols for quoting
    all_symbols = list(set(watchlist))
    if not all_symbols:
        print("No symbols to snapshot.")
        return

    # Fetch quotes
    stdout, stderr, rc = run_questrade("quote", *all_symbols)
    if rc != 0:
        print(f"ERROR: Questrade quote failed: {stderr}")
        return

    print(stdout)

    # Parse quote output and store snapshots
    # The questrade.py script outputs formatted text — we'll also call the API directly
    # for structured data. For now, store what we can parse.
    lines = stdout.strip().split("\n")
    snapshots = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 7 and parts[0] not in ("Symbol", "---", ""):
            try:
                symbol = parts[0]
                last = float(parts[1].replace(",", ""))
                chg = float(parts[2].replace("+", "").replace(",", ""))
                bid = float(parts[4].replace(",", ""))
                ask = float(parts[5].replace(",", ""))
                vol = int(parts[6].replace(",", ""))
                open_price = last - chg if chg else None
                pct = float(parts[3].replace("+", "").replace("%", "")) if "%" in parts[3] else None

                snapshots.append({
                    "symbol": symbol,
                    "price": last,
                    "open_price": open_price,
                    "volume": vol,
                    "day_change_pct": pct,
                    "bid": bid,
                    "ask": ask,
                    "snapshot_at": datetime.utcnow().isoformat(),
                })
            except (ValueError, IndexError):
                continue

    if snapshots:
        supabase_post("market_snapshots", snapshots)
        print(f"\nStored {len(snapshots)} snapshots in Supabase.")
    else:
        print("\nNo snapshots parsed from quote output.")


def cmd_check_alerts():
    """Check price alerts against current data."""
    alerts = supabase_get("price_alerts", {
        "enabled": "eq.true",
        "select": "*",
    })

    if not alerts:
        print("No active price alerts.")
        return

    # Get unique symbols
    symbols = list(set(a["symbol"] for a in alerts))
    stdout, stderr, rc = run_questrade("quote", *symbols)
    if rc != 0:
        print(f"ERROR: Quote failed: {stderr}")
        return

    # Parse prices from output
    prices = {}
    for line in stdout.strip().split("\n"):
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in ("Symbol", "---", ""):
            try:
                prices[parts[0]] = float(parts[1].replace(",", ""))
            except (ValueError, IndexError):
                continue

    triggered = []
    now = datetime.utcnow()

    for alert in alerts:
        symbol = alert["symbol"]
        if symbol not in prices:
            continue

        price = prices[symbol]
        threshold = float(alert["threshold"])
        alert_type = alert["alert_type"]

        # Check cooldown
        if alert.get("last_alerted_at"):
            last = datetime.fromisoformat(alert["last_alerted_at"].replace("Z", "+00:00")).replace(tzinfo=None)
            cooldown = timedelta(minutes=alert.get("cooldown_minutes", 120))
            if now - last < cooldown:
                continue

        fire = False
        if alert_type == "above" and price >= threshold:
            fire = True
        elif alert_type == "below" and price <= threshold:
            fire = True

        if fire:
            triggered.append({
                "id": alert["id"],
                "symbol": symbol,
                "type": alert_type,
                "threshold": threshold,
                "current": price,
            })
            # Update alert
            supabase_patch("price_alerts",
                {"id": f"eq.{alert['id']}"},
                {"triggered": True, "triggered_at": now.isoformat(), "last_alerted_at": now.isoformat()},
            )

    if triggered:
        print("TRIGGERED ALERTS:")
        for t in triggered:
            direction = "above" if t["type"] == "above" else "below"
            print(f"  {t['symbol']}: ${t['current']:.2f} hit {direction} ${t['threshold']:.2f}")
    else:
        print("No alerts triggered.")


def cmd_watchlist():
    watchlist = get_config("watchlist") or []
    print("Watchlist:", ", ".join(watchlist) if watchlist else "(empty)")


def cmd_add_watch(symbol):
    watchlist = get_config("watchlist") or []
    symbol = symbol.upper()
    if symbol not in watchlist:
        watchlist.append(symbol)
        set_config("watchlist", watchlist)
        print(f"Added {symbol} to watchlist.")
    else:
        print(f"{symbol} already on watchlist.")


def cmd_remove_watch(symbol):
    watchlist = get_config("watchlist") or []
    symbol = symbol.upper()
    if symbol in watchlist:
        watchlist.remove(symbol)
        set_config("watchlist", watchlist)
        print(f"Removed {symbol} from watchlist.")
    else:
        print(f"{symbol} not on watchlist.")


def cmd_set_alert(symbol, direction, price):
    symbol = symbol.upper()
    if direction not in ("above", "below"):
        print("Direction must be 'above' or 'below'.")
        return

    supabase_post("price_alerts", {
        "symbol": symbol,
        "alert_type": direction,
        "threshold": float(price),
    })
    print(f"Alert set: {symbol} {direction} ${float(price):.2f}")


def cmd_history(symbol, days=7):
    symbol = symbol.upper()
    since = (datetime.utcnow() - timedelta(days=int(days))).isoformat()
    rows = supabase_get("market_snapshots", {
        "symbol": f"eq.{symbol}",
        "snapshot_at": f"gte.{since}",
        "select": "symbol,price,volume,day_change_pct,snapshot_at",
        "order": "snapshot_at.desc",
        "limit": "100",
    })

    if not rows:
        print(f"No snapshots for {symbol} in the last {days} days.")
        return

    print(f"History for {symbol} (last {days} days, {len(rows)} snapshots):")
    print(f"  {'Date':<20} {'Price':>10} {'Change%':>10} {'Volume':>12}")
    for r in rows:
        dt = r["snapshot_at"][:16].replace("T", " ")
        price = float(r["price"])
        pct = float(r["day_change_pct"]) if r["day_change_pct"] else 0
        vol = int(r["volume"]) if r["volume"] else 0
        print(f"  {dt:<20} {price:>10.2f} {pct:>+9.1f}% {vol:>12,}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == "snapshot":
        cmd_snapshot()
    elif cmd == "check-alerts":
        cmd_check_alerts()
    elif cmd == "watchlist":
        cmd_watchlist()
    elif cmd == "add-watch":
        cmd_add_watch(sys.argv[2])
    elif cmd == "remove-watch":
        cmd_remove_watch(sys.argv[2])
    elif cmd == "set-alert":
        if len(sys.argv) < 5:
            print("Usage: market_data.py set-alert <symbol> <above|below> <price>")
            return
        cmd_set_alert(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "history":
        if len(sys.argv) < 3:
            print("Usage: market_data.py history <symbol> [days]")
            return
        cmd_history(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 7)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
