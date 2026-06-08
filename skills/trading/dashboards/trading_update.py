#!/usr/bin/env python3
"""Build trading.html from Supabase trading tables.

Replaces skills/trading/scripts/dashboard_gen.py (which string-concatenated
HTML on the VPS). Contract: ~/openclaw/docs/DASHBOARDS.md.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "trading.template.html"
OUT = Path("/home/tonygale/openclaw/canvas/trading.html")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_KEY", "")
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}


def sb_get(table: str, params: dict | None = None) -> list:
    if not SUPABASE_URL:
        return []
    try:
        resp = httpx.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=HEADERS, params=params or {}, timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def build_positions(snapshots: list, signals_by_sym: dict) -> list[dict]:
    latest: dict[str, dict] = {}
    for s in snapshots:
        if s["symbol"] not in latest:
            latest[s["symbol"]] = s
    out = []
    for sym, s in sorted(latest.items()):
        sig = signals_by_sym.get(sym, {})
        out.append({
            "symbol": sym,
            "price": float(s.get("price") or 0),
            "day_change_pct": float(s.get("day_change_pct") or 0) if s.get("day_change_pct") is not None else None,
            "volume": int(s.get("volume") or 0) if s.get("volume") is not None else None,
            "signal": sig.get("signal"),
        })
    return out


def build_signal_changes(signals_by_sym: dict) -> list[dict]:
    return [s for s in signals_by_sym.values() if s.get("signal_changed")]


def build_auto_open(auto_open_rows: list, latest_snapshots: dict) -> list[dict]:
    out = []
    for p in auto_open_rows:
        entry = float(p.get("entry_price") or 0)
        snap = latest_snapshots.get(p.get("symbol"))
        cur = float(snap["price"]) if snap and snap.get("price") else entry
        if p.get("side") == "BUY":
            ret = ((cur - entry) / entry) * 100 if entry else 0
        else:
            ret = ((entry - cur) / entry) * 100 if entry else 0
        out.append({
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "entry_price": entry,
            "current_price": cur,
            "unrealized_pct": round(ret, 2),
            "opened_at": p.get("opened_at"),
            "buy_flags_met": p.get("buy_flags_met") or [],
        })
    return out


def build_data() -> dict:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

    snapshots = sb_get("market_snapshots", {
        "select": "symbol,price,day_change_pct,volume,bid,ask,snapshot_at",
        "order": "snapshot_at.desc", "limit": "50",
    })
    alerts = sb_get("price_alerts", {
        "enabled": "eq.true", "select": "*", "order": "created_at.desc",
    })
    signals_raw = sb_get("trend_signals", {
        "select": "symbol,signal,previous_signal,signal_changed,sma_5,sma_20,volume_ratio,computed_at",
        "order": "computed_at.desc", "limit": "30",
    })
    news = sb_get("news_events", {
        "select": "title,source,impact_level,published_at,url",
        "order": "fetched_at.desc", "limit": "15",
    })
    social = sb_get("social_signals", {
        "market_relevant": "eq.true",
        "select": "platform,author,content,severity,fetched_at",
        "order": "fetched_at.desc", "limit": "15",
    })
    watchlist: list = []
    cfg = sb_get("trading_config", {"key": "eq.watchlist", "select": "value"})
    if cfg:
        v = cfg[0].get("value")
        watchlist = v if isinstance(v, list) else (json.loads(v) if isinstance(v, str) else [])
    auto_open_rows = sb_get("auto_trades", {
        "status": "eq.OPEN", "select": "*", "order": "opened_at.desc",
    })
    auto_closed_rows = sb_get("auto_trades", {
        "status": "eq.CLOSED", "select": "*",
        "order": "closed_at.desc", "limit": "15",
    })
    risk_cfg = sb_get("trading_rules", {"key": "eq.risk_limits", "select": "value"})
    risk = risk_cfg[0].get("value", {}) if risk_cfg else {}
    if isinstance(risk, str):
        try:
            risk = json.loads(risk)
        except Exception:
            risk = {}

    # Deduplicate signals to latest per symbol
    signals_by_sym: dict[str, dict] = {}
    for s in signals_raw:
        signals_by_sym.setdefault(s["symbol"], s)

    # Deduplicate snapshots (for auto-open current-price lookup)
    latest_snapshots: dict[str, dict] = {}
    for s in snapshots:
        latest_snapshots.setdefault(s["symbol"], s)

    positions = build_positions(snapshots, signals_by_sym)
    signal_changes = build_signal_changes(signals_by_sym)
    auto_open = build_auto_open(auto_open_rows, latest_snapshots)
    auto_today = [t for t in auto_closed_rows if (t.get("closed_at") or "") >= today_start]
    auto_today_pnl = sum(float(t.get("pnl") or 0) for t in auto_today)

    summary = {
        "watchlist_count": len(watchlist),
        "signal_count": len(signals_by_sym),
        "active_alerts": sum(1 for a in alerts if not a.get("triggered")),
        "auto_open": len(auto_open),
        "auto_today_pnl": round(auto_today_pnl, 2),
        "auto_paused": bool(risk.get("auto_trading_paused", False)),
    }

    return {
        "generated_at": now.isoformat(),
        "summary": summary,
        "positions": positions,
        "alerts": alerts,
        "signal_changes": signal_changes,
        "watchlist": watchlist,
        "auto_open": auto_open,
        "auto_closed": auto_closed_rows,
        "news": news,
        "social": social,
    }


def main() -> None:
    template = TEMPLATE.read_text()
    data = build_data()
    html = template.replace("{{DATA}}", json.dumps(data, default=str))
    OUT.write_text(html)
    print(
        f"Wrote {OUT} ({len(html)} bytes; "
        f"{len(data['positions'])} positions, "
        f"{len(data['alerts'])} alerts, "
        f"{len(data['auto_open'])} open auto-trades, "
        f"{len(data['news'])} news, {len(data['social'])} social)"
    )


if __name__ == "__main__":
    main()
