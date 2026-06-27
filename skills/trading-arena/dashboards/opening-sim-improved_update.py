#!/usr/bin/env python3
"""Render canvas/opening-sim-improved.html from logs/opening_sim_improved.json via the
shared opening-sim-multi template (improved config ± RVOL filter)."""
import json
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "opening-sim-multi.template.html"
OUT = HERE.parents[2] / "canvas" / "opening-sim-improved.html"
SUMMARY = HERE.parent / "logs" / "opening_sim_improved.json"
def main():
    data = json.loads(SUMMARY.read_text())
    data["updated"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    OUT.write_text(TEMPLATE.read_text().replace("{{DATA}}", json.dumps(data, default=str)))
    print(f"[opening-sim-improved] wrote {OUT}")
if __name__ == "__main__": main()
