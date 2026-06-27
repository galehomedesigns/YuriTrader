#!/usr/bin/env python3
"""Regenerate canvas/opening-sim-variant.html from logs/opening_sim_variant.json.
Pure data → locked template (docs/DASHBOARDS.md). The sim
(opening_agent/sim_opening_variant.py) writes the multi-day summary this renders.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "opening-sim-variant.template.html"
OUT = HERE.parents[2] / "canvas" / "opening-sim-variant.html"
SUMMARY = HERE.parent / "logs" / "opening_sim_variant.json"


def build_data() -> dict:
    data = json.loads(SUMMARY.read_text())
    data["updated"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return data


def main() -> None:
    html = TEMPLATE.read_text().replace("{{DATA}}", json.dumps(build_data(), default=str))
    OUT.write_text(html)
    print(f"[opening-sim-variant] wrote {OUT}")


if __name__ == "__main__":
    main()
