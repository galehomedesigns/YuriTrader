#!/usr/bin/env python3
"""Regenerate canvas/opening-backtest.html from logs/opening_backtest_summary.json.

Pure data: read the backtest summary -> substitute into the locked template.
No LLM in the render path (per docs/DASHBOARDS.md). Read-only on the summary;
independent of the live trading system. The heavy lifting (fetch + simulate)
lives in opening_agent/backtest_full.py, which writes the summary this renders.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "opening-backtest.template.html"
OUT = HERE.parents[2] / "canvas" / "opening-backtest.html"        # ~/openclaw/canvas/
SUMMARY = HERE.parent / "logs" / "opening_backtest_summary.json"  # trading-arena/logs/


def build_data() -> dict:
    if SUMMARY.exists():
        try:
            data = json.loads(SUMMARY.read_text())
            data.setdefault("updated",
                            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"))
            return data
        except ValueError:
            pass
    # no run yet — render an honest empty shell
    return {
        "updated": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "window": {}, "config": {}, "coverage": {}, "variants": {}, "trades": [],
    }


def main() -> None:
    html = TEMPLATE.read_text().replace("{{DATA}}", json.dumps(build_data(), default=str))
    OUT.write_text(html)
    print(f"[opening-backtest] wrote {OUT}")


if __name__ == "__main__":
    main()
