#!/usr/bin/env python3
"""Regenerate canvas/opening-sim-2026-06-26.html from logs/opening_sim_2026-06-26.json.

Pure data: read the simulation summary -> substitute into the locked template
(per docs/DASHBOARDS.md). No LLM in the render path. The simulation itself
(opening_agent/sim_opening_2026-06-26.py) writes the summary this renders. This is
a ONE-OFF historical snapshot (a single session), not a recurring dashboard.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "opening-sim-2026-06-26.template.html"
OUT = HERE.parents[2] / "canvas" / "opening-sim-2026-06-26.html"          # ~/openclaw/canvas/
SUMMARY = HERE.parent / "logs" / "opening_sim_2026-06-26.json"            # trading-arena/logs/


def build_data() -> dict:
    data = json.loads(SUMMARY.read_text())
    data["updated"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return data


def main() -> None:
    html = TEMPLATE.read_text().replace("{{DATA}}", json.dumps(build_data(), default=str))
    OUT.write_text(html)
    print(f"[opening-sim-2026-06-26] wrote {OUT}")


if __name__ == "__main__":
    main()
