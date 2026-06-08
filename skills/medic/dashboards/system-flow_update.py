#!/usr/bin/env python3
"""Build system-flow.html — agent status + systemd status + timestamps.

The mermaid diagram itself lives in the template and is static; this
script only feeds the per-agent / per-service metadata.

See ~/openclaw/docs/DASHBOARDS.md for the contract.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

OPENCLAW = Path("/home/tonygale/openclaw")
TEMPLATE = Path(__file__).resolve().parent / "system-flow.template.html"
OUT = OPENCLAW / "canvas/system-flow.html"


def log_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts.isoformat()


def systemd_active(unit: str) -> tuple[bool, str | None]:
    """Check if a --user unit is active. Return (active, last_active_iso)."""
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True, text=True, timeout=5,
        )
        active = out.stdout.strip() == "active"
    except Exception:
        return False, None
    # ActiveEnterTimestamp tells us when it most recently became active.
    last = None
    try:
        show = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5,
        )
        for line in show.stdout.splitlines():
            if line.startswith("ActiveEnterTimestamp="):
                raw = line.split("=", 1)[1].strip()
                if raw and raw != "0" and raw != "n/a":
                    # systemd format: "Mon 2026-04-18 17:51:53 UTC"
                    from datetime import datetime as _dt
                    try:
                        last = _dt.strptime(raw, "%a %Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc).isoformat()
                    except ValueError:
                        last = None
    except Exception:
        pass
    return active, last


def build_agents() -> list[dict]:
    """The three headline agents the user called out: orchestrator, overseer, medic."""
    medic_log = OPENCLAW / "skills/medic/logs/cron.log"
    overseer_log = OPENCLAW / "skills/trading-arena/logs/overseer.log"
    arena_log = OPENCLAW / "skills/trading-arena/logs/arena_scan.log"

    return [
        {
            "name": "Orchestrator",
            "role": "User crontab on GX10",
            "description": (
                "The crontab is the orchestrator. It fires arena scans "
                "every 5 minutes during market hours, drives the overseer "
                "at 9 AM / 4:30 PM / Fri 6 PM ET, runs medic twice a day, "
                "and refreshes the dashboards every 30 minutes."
            ),
            "accent": "",
            "meta": [
                {"k": "Location", "v": "crontab -l (tonygale)"},
                {"k": "Last arena scan", "v": fmt_mtime(arena_log)},
                {"k": "Persona", "v": "projects/trading-arena/orchestrator.md"},
            ],
        },
        {
            "name": "Overseer",
            "role": "Meta-agent — operational discipline",
            "description": (
                "Enforces 5 practices across the 10 bots: custom alerts, "
                "pre-market game plan, daily autopsies, weekly super-prompt, "
                "and periodic restriction checks. Uses Ollama (quick:latest) "
                "for narrative generation; writes to Telegram and Supabase."
            ),
            "accent": "accent-2",
            "meta": [
                {"k": "Location", "v": "skills/trading-arena/overseer/"},
                {"k": "Last run", "v": fmt_mtime(overseer_log)},
                {"k": "Persona", "v": "projects/trading-arena/sub-agents/trading-overseer.md"},
            ],
        },
        {
            "name": "Medic",
            "role": "System health monitor",
            "description": (
                "Runs 20+ checks across systemd services, cron health, "
                "Supabase tables, Questrade auth, and dashboard hosting. "
                "Posts a formatted report to Telegram twice daily "
                "(11:00 UTC weekdays, 03:00 UTC nightly) and regenerates "
                "canvas/health.html."
            ),
            "accent": "accent-ok",
            "meta": [
                {"k": "Location", "v": "skills/medic/scripts/medic.py"},
                {"k": "Last run", "v": fmt_mtime(medic_log)},
                {"k": "Output dashboard", "v": "canvas/health.html"},
            ],
        },
    ]


def fmt_mtime(p: Path) -> str:
    t = log_mtime(p)
    if not t:
        return "never"
    # How long ago?
    ts = datetime.fromisoformat(t.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def build_services() -> list[dict]:
    units = [
        ("tv-webhook", "TradingView Webhook :8089",
         "Receives TradingView alerts, logs to Supabase trade_audit, forwards to Telegram. Exposed publicly via Tailscale Funnel :443."),
        ("stock-concierge", "Questrade Telegram bot",
         "Long-polls @YuriTradingViewBot for Tony's commands. Executes Questrade trades on command (paper or live based on config)."),
        ("trading-concierge", "Kraken Telegram bot",
         "Long-polls @YuriTrade24Bot. /best shows advisor output, inline buttons execute Kraken trades via KrakenExecutor. Gated by KRAKEN_ALLOW_TRADING + LIVE_TRADING_ENABLED."),
        ("dashboards", "Static dashboard server :8090",
         "Plain Python http.server serving ~/openclaw/canvas/*.html on localhost. No auth at this layer."),
        ("dashboard-proxy", "Caddy basic-auth proxy :8091",
         "Caddy adds basic_auth (tony/decades2026) in front of dashboards.service. Tailscale Funnel :8443 terminates TLS and forwards here."),
    ]
    out: list[dict] = []
    for unit, name, desc in units:
        active, last = systemd_active(unit)
        out.append({
            "name": name,
            "unit": f"{unit}.service",
            "description": desc,
            "active": active,
            "last_active": last,
        })
    return out


def build_data() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agents": build_agents(),
        "services": build_services(),
    }


def main() -> None:
    template = TEMPLATE.read_text()
    data = build_data()
    html = template.replace("{{DATA}}", json.dumps(data, default=str))
    OUT.write_text(html)
    print(
        f"Wrote {OUT} ({len(html)} bytes, "
        f"{len(data['agents'])} agents, "
        f"{len(data['services'])} services, "
        f"{sum(1 for s in data['services'] if s['active'])} active)"
    )


if __name__ == "__main__":
    main()
