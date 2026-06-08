#!/usr/bin/env python3
"""Build bot-arena.html from Supabase + bot docstrings.

Contract: see ~/openclaw/docs/DASHBOARDS.md — data-only regeneration,
template is never modified by this script.
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from shared.paper_trader import _supabase_get  # noqa: E402

DASHBOARD_DIR = Path(__file__).resolve().parent
TEMPLATE = DASHBOARD_DIR / "bot-arena.template.html"
OUT = Path("/home/tonygale/openclaw/canvas/bot-arena.html")
BOTS_DIR = SKILL_ROOT / "bots"

SERIES_TRADE_CAP = 50
RULE_PREFIXES = ("Entry:", "Exit:", "Trigger:", "Signal:", "Stop:", "Target:")


def fetch_leaderboard() -> list[dict]:
    return _supabase_get("arena_balances?order=total_pnl.desc") or []


def fetch_closed_trades(bot_id: str, limit: int = SERIES_TRADE_CAP) -> list[dict]:
    return _supabase_get(
        f"arena_trades?bot_id=eq.{bot_id}&status=eq.closed"
        f"&order=closed_at.desc&limit={limit}&select=closed_at,pnl"
    ) or []


def fetch_open_positions() -> int:
    rows = _supabase_get("arena_trades?status=eq.open&select=id") or []
    return len(rows)


def parse_docstring(docstring: str) -> tuple[str, list[dict]]:
    """Split a bot module docstring into (description, rules).

    Shape expected (tolerant to extra blank lines):
        Bot Name — Strategy Label.

        Trades <something description>.
        Entry: <rule>
        Exit: <rule>
    """
    if not docstring:
        return "", []
    lines = [ln.strip() for ln in docstring.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return "", []

    desc_parts: list[str] = []
    rules: list[dict] = []
    # Skip line 0 (the "Bot Name — Strategy Label." header).
    for ln in lines[1:]:
        if not ln:
            continue
        matched = next((p for p in RULE_PREFIXES if ln.startswith(p)), None)
        if matched:
            rules.append({"label": matched.rstrip(":"), "body": ln[len(matched):].strip()})
        else:
            desc_parts.append(ln)
    description = " ".join(desc_parts)
    if not description:
        description = lines[0].rstrip(".")
    return description, rules


def parse_bot_definitions() -> list[dict]:
    defs: list[dict] = []
    for py in sorted(BOTS_DIR.glob("*.py")):
        if py.name.startswith("_"):
            continue
        try:
            module = ast.parse(py.read_text())
        except SyntaxError:
            continue
        description_fallback, rules = parse_docstring(ast.get_docstring(module) or "")
        # Class-level NAME / BOT_ID / DESCRIPTION win over filename and docstring.
        bot_id = py.stem.replace("_", "-")
        bot_name = py.stem.replace("_", " ").title()
        description_override = ""
        for node in ast.walk(module):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, ast.Assign):
                    continue
                for tgt in item.targets:
                    if not isinstance(tgt, ast.Name):
                        continue
                    if isinstance(item.value, ast.Constant):
                        if tgt.id == "BOT_ID":
                            bot_id = item.value.value
                        elif tgt.id == "NAME":
                            bot_name = item.value.value
                        elif tgt.id == "DESCRIPTION":
                            description_override = item.value.value
        defs.append({
            "bot_id": bot_id,
            "bot_name": bot_name,
            "description": description_override or description_fallback,
            "rules": rules,
        })
    return defs


def build_pnl_series(trades_desc: list[dict]) -> list[dict]:
    """Input is DESC by closed_at. Output is ASC cumulative P&L series."""
    trades = list(reversed(trades_desc))
    cum = 0.0
    out: list[dict] = []
    for t in trades:
        pnl = t.get("pnl")
        if pnl is None:
            continue
        cum += float(pnl)
        out.append({"t": t.get("closed_at"), "v": round(cum, 2)})
    return out


def build_data() -> dict:
    board = fetch_leaderboard()
    defs = parse_bot_definitions()
    defs_by_id = {d["bot_id"]: d for d in defs}

    bots: list[dict] = []
    total_trades = 0
    total_pnl_sum = 0.0
    active_bots = 0
    for b in board:
        bot_id = b.get("bot_id")
        if not bot_id:
            continue
        trades = fetch_closed_trades(bot_id)
        series = build_pnl_series(trades)
        trade_count = int(b.get("total_trades") or 0)
        total_trades += trade_count
        total_pnl_sum += float(b.get("total_pnl") or 0)
        if trade_count > 0:
            active_bots += 1
        bots.append({
            "bot_id": bot_id,
            "bot_name": b.get("bot_name") or defs_by_id.get(bot_id, {}).get("bot_name") or bot_id,
            "total_trades": trade_count,
            "total_pnl": round(float(b.get("total_pnl") or 0), 2),
            "win_rate": float(b.get("win_rate") or 0),
            "current_balance": round(float(b.get("current_balance") or 0), 2),
            "starting_balance": round(float(b.get("starting_balance") or 0), 2),
            "updated_at": b.get("updated_at"),
            "pnl_series": series,
        })

    overall_wr = 0.0
    if total_trades > 0:
        overall_wr = sum(b["win_rate"] * b["total_trades"] for b in bots) / total_trades

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_trades": total_trades,
            "total_pnl": round(total_pnl_sum, 2),
            "overall_win_rate": round(overall_wr, 1),
            "best_bot": bots[0]["bot_name"] if bots else None,
            "active_bots": active_bots,
            "total_bots": len(bots),
            "open_positions": fetch_open_positions(),
        },
        "bots": bots,
        "definitions": defs,
    }


def main() -> None:
    template = TEMPLATE.read_text()
    data = build_data()
    html = template.replace("{{DATA}}", json.dumps(data, default=str))
    OUT.write_text(html)
    print(f"Wrote {OUT} ({len(html)} bytes, {len(data['bots'])} bots, {len(data['definitions'])} definitions)")


if __name__ == "__main__":
    main()
