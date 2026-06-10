"""Daily-resetting ledger for the LLM-advisor execution path.

The caps in llm_advisor_caps.apply_caps() need the LIVE state of *this path* —
open positions, today's realised P&L, today's trade count. That state belongs to
the advisor path specifically, NOT the bots' broker positions (those are a
separate path), so it is tracked here in its own ledger.

Until the advisor path actually executes (shadow / dry-run phases), the ledger is
0/0/0 — which is correct, not a placeholder. When the path goes live, the
executing caller calls record_fill()/record_close() to keep it current.

Backed by a JSON file (ADVISOR_PATH_STATE, default logs/advisor_path_state.json),
keyed by UTC date so it resets each day. Fail-safe: unreadable/corrupt state
returns a conservative zeroed view (which, combined with the breaker's
fail-closed default, never loosens a cap).
"""
import json
import os
from datetime import datetime, timezone

STATE_FILE = os.environ.get(
    "ADVISOR_PATH_STATE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "advisor_path_state.json"),
)


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _fresh():
    return {"date": _today(), "trades_today": 0, "realized_pnl": 0.0,
            "open_positions": 0}


def _read():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        if not isinstance(s, dict) or s.get("date") != _today():
            return _fresh()                      # new day → reset daily counters
        # carry open_positions across the day boundary if present
        return {
            "date": s.get("date", _today()),
            "trades_today": int(s.get("trades_today", 0)),
            "realized_pnl": float(s.get("realized_pnl", 0.0)),
            "open_positions": int(s.get("open_positions", 0)),
        }
    except (OSError, ValueError, TypeError):
        return _fresh()


def _write(s):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(s, f)
    except OSError:
        pass


def get_state():
    """Return the cap inputs: (open_positions, realized_pnl, trades_today).
    Pass these straight into llm_advisor_caps.apply_caps()."""
    s = _read()
    return s["open_positions"], s["realized_pnl"], s["trades_today"]


def record_fill(delta_positions=1):
    """Live path calls this when the advisor path OPENS a position."""
    s = _read()
    s["trades_today"] += 1
    s["open_positions"] = max(0, s["open_positions"] + int(delta_positions))
    _write(s)


def record_close(realized_pnl_delta, delta_positions=-1):
    """Live path calls this when the advisor path CLOSES a position, booking P&L
    (feeds the daily-loss circuit breaker)."""
    s = _read()
    s["realized_pnl"] += float(realized_pnl_delta)
    s["open_positions"] = max(0, s["open_positions"] + int(delta_positions))
    _write(s)
