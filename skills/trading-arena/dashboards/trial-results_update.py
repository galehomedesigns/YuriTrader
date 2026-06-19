#!/usr/bin/env python3
"""Render trial-results dashboard from trial_backtest.py output.

Usage:
  # Generate fresh data + render:
  python3 trial_backtest.py > /tmp/trial_data.json
  python3 trial-results_update.py              # reads /tmp/trial_data.json
  python3 trial-results_update.py data.json    # or a specific file
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "trial-results.template.html"
OUT = HERE.parents[2] / "canvas" / "trial-results.html"
DEFAULT_DATA = Path("/tmp/trial_data.json")


def main():
    data_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATA
    if not data_path.exists():
        print(f"[trial-results] data file not found: {data_path}")
        print(f"  Run: python3 {HERE / 'trial_backtest.py'} > {DEFAULT_DATA}")
        sys.exit(1)

    data = json.loads(data_path.read_text())
    html = TEMPLATE.read_text().replace("{{DATA}}", json.dumps(data, default=str))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html)
    print(f"[trial-results] wrote {OUT}")
    print(f"  Stats: {data['stats']['total_trades']} trades, "
          f"net ${data['stats']['net_pnl']:+.2f}, "
          f"win rate {data['stats']['win_rate']}%, "
          f"PF {data['stats']['profit_factor']}")


if __name__ == "__main__":
    main()
