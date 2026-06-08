#!/usr/bin/env python3
"""Build kraken.html from Kraken API + Supabase arena_trades (live only).

See ~/openclaw/docs/DASHBOARDS.md for the contract.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from shared.paper_trader import _supabase_get  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent
TEMPLATE = DASHBOARD_DIR / "kraken.template.html"
OUT = Path("/home/tonygale/openclaw/canvas/kraken.html")
CONCIERGE_DB = SKILL_ROOT / "concierge_state.db"


def bool_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def fetch_usd_balance() -> tuple[float | None, str | None]:
    """Return (usd_balance, error). Kraken API call."""
    try:
        from shared.kraken_executor import KrakenExecutor
        kx = KrakenExecutor()
        return float(kx.get_usd_balance()), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:180]


def fetch_live_trades_open() -> list[dict]:
    return _supabase_get(
        "arena_trades?status=eq.open&paper=eq.false&order=opened_at.desc&select=*"
    ) or []


def fetch_live_trades_closed_today() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _supabase_get(
        "arena_trades?status=eq.closed&paper=eq.false"
        f"&closed_at=gte.{today}T00:00:00Z"
        "&order=closed_at.desc&select=*"
    ) or []


def count_pending_actions() -> int:
    if not CONCIERGE_DB.exists():
        return 0
    try:
        conn = sqlite3.connect(str(CONCIERGE_DB))
        row = conn.execute(
            "SELECT COUNT(*) FROM pending_actions WHERE consumed_at IS NULL"
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0  # table may not exist on fresh db


def build_data() -> dict:
    usd_balance, err = fetch_usd_balance()
    open_trades = fetch_live_trades_open()
    closed_trades = fetch_live_trades_closed_today()

    today_pnl = sum(float(t.get("pnl") or 0) for t in closed_trades)

    gates = {
        "kraken_allow_trading": bool_env("KRAKEN_ALLOW_TRADING"),
        "live_trading_enabled": bool_env("LIVE_TRADING_ENABLED"),
        "live_trading_bots": [b.strip() for b in os.environ.get("LIVE_TRADING_BOTS", "").split(",") if b.strip()],
        "manual_max_exposure_usd": float_env("MANUAL_MAX_EXPOSURE_USD", 50.0),
        "manual_daily_loss_limit": float_env("MANUAL_DAILY_LOSS_LIMIT", -10.0),
    }

    summary = {
        "usd_balance": usd_balance,
        "open_positions": len(open_trades),
        "today_pnl": round(today_pnl, 2),
        "daily_loss_limit": gates["manual_daily_loss_limit"],
        "pending_actions": count_pending_actions(),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kraken_balance_error": err,
        "summary": summary,
        "gates": gates,
        "open_trades": open_trades,
        "closed_trades_today": closed_trades,
    }


def main() -> None:
    template = TEMPLATE.read_text()
    data = build_data()
    html = template.replace("{{DATA}}", json.dumps(data, default=str))
    OUT.write_text(html)
    print(
        f"Wrote {OUT} ({len(html)} bytes, "
        f"{len(data['open_trades'])} open, "
        f"{len(data['closed_trades_today'])} closed today, "
        f"balance={'ok' if data['summary']['usd_balance'] is not None else 'error'})"
    )


if __name__ == "__main__":
    main()
