#!/usr/bin/env python3
"""Render canvas/opening-sim-5min.html from logs/opening_sim_multi_5min.json via the shared
opening-sim-multi template (3-way: baseline / new-sim / live-engine)."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "opening-sim-multi.template.html"
OUT = HERE.parents[2] / "canvas" / "opening-sim-5min.html"
SUMMARY = HERE.parent / "logs" / "opening_sim_multi_5min.json"
def main() -> None:
    data = json.loads(SUMMARY.read_text())
    data["updated"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    html = TEMPLATE.read_text().replace("{{DATA}}", json.dumps(data, default=str))
    OUT.write_text(html)
    print(f"[opening-sim-5min] wrote {OUT}")
if __name__ == "__main__":
    main()
