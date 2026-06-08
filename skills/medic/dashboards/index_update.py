#!/usr/bin/env python3
"""Build index.html — the dashboard landing page.

The catalog below is the single source of truth for what shows up. Add
a new dashboard there when you migrate or create one. The script
computes the `last_updated` field from the canvas file's mtime.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

CANVAS = Path("/home/tonygale/openclaw/canvas")
TEMPLATE = Path(__file__).resolve().parent / "index.template.html"
OUT = CANVAS / "index.html"

# Order matters within each category — first one in list renders first.
CATALOG: list[dict] = [
    # LIVE — follow the DASHBOARDS.md standard, regen on schedule
    {
        "filename": "bot-arena.html",
        "title": "Trading Arena",
        "purpose": "10-bot paper-trading leaderboard with per-bot cumulative-P&L line charts and strategy definition cards.",
        "owner_skill": "trading-arena",
        "category": "live",
    },
    {
        "filename": "kraken.html",
        "title": "Kraken — Live Trading Status",
        "purpose": "Live USD balance, open positions, today's closed trades, and the state of the live-trading gates.",
        "owner_skill": "trading-arena",
        "category": "live",
    },
    {
        "filename": "trading.html",
        "title": "Trading Intelligence",
        "purpose": "Questrade-side market snapshots, price alerts, signal changes, auto-trade positions, news and sentiment.",
        "owner_skill": "trading",
        "category": "live",
    },
    {
        "filename": "health.html",
        "title": "System Health (Yuri)",
        "purpose": "Medic's health-check report — cron jobs, systemd services, Supabase, Questrade auth, alert history.",
        "owner_skill": "medic",
        "category": "live",
    },
    {
        "filename": "system-flow.html",
        "title": "System Flow",
        "purpose": "Mermaid map of how orchestrator, overseer, medic, arena bots, and the concierges fit together on GX10.",
        "owner_skill": "medic",
        "category": "live",
    },
    {
        "filename": "dashboard.html",
        "title": "Decades Developments — Personal",
        "purpose": "Spending by category, recent transactions, and the active to-do list.",
        "owner_skill": "dashboard",
        "category": "live",
    },

    # REFERENCE — static or rarely-changing content; no scheduled regen
    {
        "filename": "agent-overview.html",
        "title": "Yuri Agent Architecture",
        "purpose": "Deeper visual on the agent hierarchy (Local/GX10 vs Cloud fallback, data flow). Static reference.",
        "owner_skill": "medic",
        "category": "reference",
    },
    {
        "filename": "agents.html",
        "title": "Yuri Agent Ecosystem",
        "purpose": "Agent-by-agent cards (model, role, tools). Static reference — some overlap with system-flow.",
        "owner_skill": "medic",
        "category": "reference",
    },
    {
        "filename": "yt-strategies-rayner-teo.html",
        "title": "Rayner Teo — Strategy Report",
        "purpose": "Static analytical report from the YouTube-strategy skill: indicators, strategy-type breakdown, top-N.",
        "owner_skill": "youtube-strategy",
        "category": "reference",
    },

    # ARCHIVED — kept for history, no scheduled regen; banner at top of page
    {
        "filename": "procurement.html",
        "title": "BC Procurement Intelligence",
        "purpose": "Public-tender crawler dashboard. Crawler paused 2026-04-23; data frozen at last refresh.",
        "owner_skill": "procurement",
        "category": "archived",
    },
    {
        "filename": "newsletter-draft.html",
        "title": "Weekly Procurement Digest",
        "purpose": "Newsletter draft fed by the procurement crawler. Orphaned when procurement was archived.",
        "owner_skill": "procurement",
        "category": "archived",
    },
]


def build_data() -> dict:
    dashboards: list[dict] = []
    for entry in CATALOG:
        path = CANVAS / entry["filename"]
        last = None
        if path.exists():
            last = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        item = dict(entry)
        item["last_updated"] = last
        item["exists"] = path.exists()
        dashboards.append(item)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dashboards": dashboards,
    }


def main() -> None:
    template = TEMPLATE.read_text()
    data = build_data()
    html = template.replace("{{DATA}}", json.dumps(data, default=str))
    OUT.write_text(html)
    by_cat: dict[str, int] = {}
    for d in data["dashboards"]:
        by_cat[d["category"]] = by_cat.get(d["category"], 0) + 1
    print(f"Wrote {OUT} ({len(html)} bytes, {by_cat})")


if __name__ == "__main__":
    main()
