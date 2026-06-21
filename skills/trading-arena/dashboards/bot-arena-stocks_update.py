#!/usr/bin/env python3
"""Regenerate canvas/bot-arena-stocks.html by merging the three analysis summaries
(bot tournament + indicator edge + filter combinations). Pure data; no LLM in the
render path (per docs/DASHBOARDS.md). Read-only on the summaries."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "bot-arena-stocks.template.html"
OUT = HERE.parents[2] / "canvas" / "bot-arena-stocks.html"
LOGS = HERE.parent / "logs"
BOTS = LOGS / "bot_arena_stocks_summary.json"
IND = LOGS / "indicator_edge_summary.json"
COMBO = LOGS / "combo_edge_summary.json"


def _load(p):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def build_data() -> dict:
    bots = _load(BOTS)
    ind = _load(IND)
    combo = _load(COMBO)
    cov = bots.get("coverage", {})
    cfg = bots.get("config", {})
    results = bots.get("results", [])
    base = ind.get("baseline", {})
    return {
        "updated": bots.get("updated") or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "coverage": {
            "sessions": cfg.get("window", {}).get("sessions"),
            "gap_symbol_days": cov.get("gap_symbol_days"),
            "gap_band": cfg.get("gap_band"),
            "best_bot": (f"{results[0]['bot']}-{results[0]['variant']} "
                         f"(${results[0]['end_balance']:.0f})") if results else "—",
        },
        "bots": results,
        "indicators": ind.get("findings", []),
        "ind_baseline": base,
        "combos": combo.get("combos", []),
        "regime_note": (
            "<b>One regime, ~2 months.</b> Baseline gap setups returned "
            f"<b>{base.get('is', 0):+.3f}%</b> in-sample vs <b>{base.get('oos', 0):+.3f}%</b> "
            "out-of-sample — the period, not the indicators, drives most of the result. "
            "Only filters positive in BOTH halves (robust=YES) are credible, and even those "
            "are suggestive, not proven. Pre-market volume + ranking are proxied."),
    }


def main() -> None:
    html = TEMPLATE.read_text().replace("{{DATA}}", json.dumps(build_data(), default=str))
    OUT.write_text(html)
    print(f"[bot-arena-stocks] wrote {OUT}")


if __name__ == "__main__":
    main()
