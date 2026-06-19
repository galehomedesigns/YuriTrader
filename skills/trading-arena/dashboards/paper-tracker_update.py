#!/usr/bin/env python3
"""Regenerate canvas/paper-tracker.html from logs/paper_track.jsonl.

Pure data: read the paper-tracker log -> aggregate -> dict -> json -> substitute
into the locked template. No LLM in the render path (per docs/DASHBOARDS.md).
Read-only on the paper log; independent of the live trading system.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "paper-tracker.template.html"
OUT = HERE.parents[2] / "canvas" / "paper-tracker.html"        # ~/openclaw/canvas/
LOG = HERE.parent / "logs" / "paper_track.jsonl"               # trading-arena/logs/


def _rows() -> list[dict]:
    if not LOG.exists():
        return []
    out = []
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def build_data() -> dict:
    rows = _rows()
    trades = [r for r in rows if r.get("triggered")]
    signals = [r for r in rows if r.get("match")]
    wins = [r for r in trades if (r.get("pnl") or 0) > 0]
    net_pnl = round(sum(float(r.get("pnl") or 0) for r in trades), 2)
    sum_pct = round(sum(float(r.get("pct") or 0) for r in trades), 2)
    win_rate = round(100 * len(wins) / len(trades)) if trades else 0
    # newest first for display
    decisions = sorted(rows, key=lambda r: (r.get("date", ""),), reverse=True)[:60]
    return {
        "updated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "signals": len(signals),
        "triggered": len(trades),
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "sum_pct": sum_pct,
        "trades": sorted(trades, key=lambda r: r.get("date", ""), reverse=True),
        "decisions": [{"symbol": r.get("symbol"), "date": r.get("date"),
                       "decision": r.get("decision") or ("TRADE" if r.get("triggered")
                                   else ("MATCH/no-trigger" if r.get("match") else "?")),
                       "mode": r.get("mode")} for r in decisions],
        "config": {
            "slippage_pct": float(os.environ.get("PAPER_SLIPPAGE_PCT", "0.0010")),
            "position_usd": float(os.environ.get("PAPER_POSITION_USD", "500")),
            "cutoff_min": int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30")),
        },
    }


def main() -> None:
    html = TEMPLATE.read_text().replace("{{DATA}}", json.dumps(build_data(), default=str))
    OUT.write_text(html)
    print(f"[paper-tracker] wrote {OUT}")


if __name__ == "__main__":
    main()
