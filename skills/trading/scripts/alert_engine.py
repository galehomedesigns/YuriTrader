#!/usr/bin/env python3
"""
Alert engine — checks all alert conditions and formats notifications.

Usage:
    python3 alert_engine.py check     # Run all alert checks, return triggered alerts
    python3 alert_engine.py summary   # Show current alert status
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
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


def supabase_patch(table, params, data):
    httpx.patch(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": "return=minimal"},
        params=params,
        json=data,
        timeout=10,
    )


def get_config(key):
    rows = supabase_get("trading_config", {"key": f"eq.{key}", "select": "value"})
    return rows[0]["value"] if rows else None


def cmd_check():
    """Run all alert checks and output triggered alerts."""
    now = datetime.now(timezone.utc)
    alerts = []
    thresholds = get_config("alert_thresholds") or {
        "pct_change": 3.0,
        "volume_multiplier": 2.0,
        "cooldown_minutes": 120,
    }
    cooldown = timedelta(minutes=thresholds.get("cooldown_minutes", 120))

    # 1. Check for significant price movements in recent snapshots
    recent = supabase_get("market_snapshots", {
        "snapshot_at": f"gte.{(now - timedelta(hours=1)).isoformat()}",
        "select": "symbol,price,day_change_pct,volume",
        "order": "snapshot_at.desc",
    })

    seen_symbols = set()
    for snap in recent:
        sym = snap["symbol"]
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)

        pct = float(snap["day_change_pct"]) if snap.get("day_change_pct") else 0
        pct_threshold = thresholds.get("pct_change", 3.0)

        if abs(pct) >= pct_threshold:
            alerts.append({
                "type": "pct_change",
                "symbol": sym,
                "message": f"{sym} {pct:+.1f}% today (${float(snap['price']):.2f})",
                "severity": "HIGH" if abs(pct) >= 5 else "MEDIUM",
            })

    # 2. Check volume anomalies against historical averages
    for sym in seen_symbols:
        history = supabase_get("market_snapshots", {
            "symbol": f"eq.{sym}",
            "select": "volume",
            "order": "snapshot_at.desc",
            "limit": "20",
        })
        if len(history) < 5:
            continue

        volumes = [int(h["volume"]) for h in history if h.get("volume")]
        if not volumes:
            continue

        avg_vol = sum(volumes[1:]) / len(volumes[1:]) if len(volumes) > 1 else volumes[0]
        current_vol = volumes[0]
        vol_multiplier = thresholds.get("volume_multiplier", 2.0)

        if avg_vol > 0 and current_vol > avg_vol * vol_multiplier:
            ratio = current_vol / avg_vol
            alerts.append({
                "type": "volume",
                "symbol": sym,
                "message": f"{sym} volume {ratio:.1f}x average ({current_vol:,} vs avg {int(avg_vol):,})",
                "severity": "MEDIUM",
            })

    # 3. Check trend signal changes
    signals = supabase_get("trend_signals", {
        "signal_changed": "eq.true",
        "computed_at": f"gte.{(now - timedelta(hours=4)).isoformat()}",
        "select": "symbol,signal,previous_signal,computed_at",
        "order": "computed_at.desc",
    })
    for sig in signals:
        alerts.append({
            "type": "signal_change",
            "symbol": sig["symbol"],
            "message": f"{sig['symbol']}: {sig['previous_signal']} -> {sig['signal']}",
            "severity": "HIGH",
        })

    # 4. Check high-severity social signals not yet notified
    social = supabase_get("social_signals", {
        "severity": "eq.HIGH",
        "notified": "eq.false",
        "fetched_at": f"gte.{(now - timedelta(hours=2)).isoformat()}",
        "select": "id,platform,author,content",
        "order": "fetched_at.desc",
        "limit": "5",
    })
    for sig in social:
        alerts.append({
            "type": "social",
            "symbol": "",
            "message": f"[{sig['platform']}] {sig['author']}: {sig['content'][:150]}",
            "severity": "HIGH",
        })
        # Mark as notified
        supabase_patch("social_signals", {"id": f"eq.{sig['id']}"}, {"notified": True})

    # 5. Check high-impact news not yet notified
    news = supabase_get("news_events", {
        "impact_level": "eq.HIGH",
        "notified": "eq.false",
        "fetched_at": f"gte.{(now - timedelta(hours=2)).isoformat()}",
        "select": "id,title,source",
        "order": "fetched_at.desc",
        "limit": "5",
    })
    for n in news:
        alerts.append({
            "type": "news",
            "symbol": "",
            "message": f"[{n['source']}] {n['title'][:150]}",
            "severity": "HIGH",
        })
        supabase_patch("news_events", {"id": f"eq.{n['id']}"}, {"notified": True})

    # 6. Check custom price alerts (already handled by market_data.py, but include results)
    custom = supabase_get("price_alerts", {
        "enabled": "eq.true",
        "triggered": "eq.true",
        "triggered_at": f"gte.{(now - timedelta(hours=1)).isoformat()}",
        "select": "symbol,alert_type,threshold",
    })
    for a in custom:
        alerts.append({
            "type": "price_alert",
            "symbol": a["symbol"],
            "message": f"{a['symbol']} hit {a['alert_type']} ${float(a['threshold']):.2f}",
            "severity": "HIGH",
        })

    # Output
    if not alerts:
        print("No alerts triggered.")
        return

    # Group and format
    high = [a for a in alerts if a["severity"] == "HIGH"]
    medium = [a for a in alerts if a["severity"] == "MEDIUM"]

    if high:
        print("HIGH PRIORITY ALERTS:")
        for a in high:
            print(f"  [{a['type']}] {a['message']}")

    if medium:
        print("\nMEDIUM ALERTS:")
        for a in medium:
            print(f"  [{a['type']}] {a['message']}")

    print(f"\nTotal: {len(alerts)} alerts ({len(high)} high, {len(medium)} medium)")


def cmd_summary():
    """Show current alert configuration and status."""
    thresholds = get_config("alert_thresholds") or {}
    print("Alert Thresholds:")
    print(f"  Price change: +/-{thresholds.get('pct_change', 3.0)}%")
    print(f"  Volume multiplier: {thresholds.get('volume_multiplier', 2.0)}x")
    print(f"  Cooldown: {thresholds.get('cooldown_minutes', 120)} min")

    active = supabase_get("price_alerts", {
        "enabled": "eq.true",
        "select": "symbol,alert_type,threshold,triggered",
    })
    if active:
        print(f"\nCustom Price Alerts ({len(active)}):")
        for a in active:
            status = "TRIGGERED" if a["triggered"] else "active"
            print(f"  {a['symbol']} {a['alert_type']} ${float(a['threshold']):.2f} [{status}]")
    else:
        print("\nNo custom price alerts set.")

    # Recent signal changes
    signals = supabase_get("trend_signals", {
        "signal_changed": "eq.true",
        "select": "symbol,signal,previous_signal,computed_at",
        "order": "computed_at.desc",
        "limit": "5",
    })
    if signals:
        print(f"\nRecent Signal Changes:")
        for s in signals:
            dt = s["computed_at"][:16].replace("T", " ")
            print(f"  {s['symbol']}: {s['previous_signal']} -> {s['signal']} ({dt})")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    if cmd == "check":
        cmd_check()
    elif cmd == "summary":
        cmd_summary()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
