#!/usr/bin/env python3
"""Bridge between the Opening-Power arm logic and the isolated IBKR executor.

Places the long bracket (entry stop + protective stop) on IBKR for armed names,
sized to the SAME $-slot as the Questrade/TV path (OPENING_TRADE_BUDGET_USD /
OPENING_MAX_TRADES), with the SAME risk cap. Imports only the isolated executor +
stdlib — nothing from opening_agent / tv_* / CDP.

Two-stage go-live gating:
  OPENING_IBKR_EXEC=1            -> bridge is active
  OPENING_IBKR_ALLOW_TRADING=0  -> SHADOW: log the bracket it WOULD place (no connect,
                                   no transmit). =1 -> LIVE: actually place via API.
Both default OFF.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from executor import IBKRExecutor


def _flag(name, default="0"):
    return os.environ.get(name, default).lower() in ("1", "true", "yes")


def enabled():
    return _flag("OPENING_IBKR_EXEC")


def live():
    return _flag("OPENING_IBKR_ALLOW_TRADING")


def _client_id():
    return int(os.environ.get("OPENING_IBKR_CLIENT_ID", "93"))


def _slot_qty(entry):
    budget = float(os.environ.get("OPENING_TRADE_BUDGET_USD", "1000") or 1000)
    mx = int(os.environ.get("OPENING_MAX_TRADES", "5"))
    per = budget / mx
    return (int(per // entry) if entry and entry > 0 else 0), per


def _plan(symbol, entry, stop):
    """(qty, skip_msg). Applies the same risk cap as the TV path; full $-slot size
    (= the position the TV half-entry+add completes to)."""
    qty, per = _slot_qty(entry)
    if qty < 1:
        return 0, f"⚪ IBKR {symbol}: ${per:.0f}/slot < 1 share @ ${entry:.2f} — skip"
    max_risk = float(os.environ.get("OPENING_MAX_RISK_PCT", "3.0"))
    risk = (entry - stop) / entry * 100 if entry > 0 else 0
    if risk > max_risk:
        return 0, f"⚪ IBKR {symbol}: risk {risk:.1f}% > {max_risk}% cap — skip"
    return qty, None


def execute_batch(orders):
    """orders: [{'symbol','entry','stop'}]. Place (or shadow) one bracket each.
    Returns a list of human-readable status strings (for Telegram/log). Never raises."""
    if not enabled() or not orders:
        return []
    msgs, planned = [], []
    for o in orders:
        qty, skip = _plan(o["symbol"], float(o["entry"]), float(o["stop"]))
        if skip:
            msgs.append(skip)
        else:
            planned.append((o, qty))
    if not planned:
        return msgs
    if not live():
        for o, qty in planned:
            msgs.append(f"🧪 IBKR SHADOW — would place BUY {qty} {o['symbol']} "
                        f"STOP {o['entry']} / SL {o['stop']} (not sent)")
        return msgs
    ex = IBKRExecutor(client_id=_client_id())
    try:
        ex.connect()
        for o, qty in planned:
            try:
                r = ex.place_bracket(o["symbol"], qty, float(o["entry"]), float(o["stop"]))
                msgs.append(f"🟢 IBKR placed BUY {qty} {o['symbol']} STOP {o['entry']} / "
                            f"SL {o['stop']} (entry {r['parent_status']}, stop {r['child_status']})")
            except Exception as e:                          # noqa: BLE001
                msgs.append(f"🔴 IBKR FAILED {o['symbol']}: {e} — place by hand")
    except Exception as e:                                  # noqa: BLE001
        msgs.append(f"🔴 IBKR connect failed: {e} — orders NOT placed, do them by hand")
    finally:
        ex.disconnect()
    return msgs


def flatten_all():
    """Cutoff: cancel resting brackets + market-close any long positions on the
    IBKR account. (reqGlobalCancel cancels ALL open orders on the account — this is
    the dedicated opening account.) Returns status strings. Shadow = no-op note."""
    if not enabled():
        return []
    if not live():
        return ["🧪 IBKR SHADOW — would flatten all IBKR positions at cutoff (not sent)"]
    ex = IBKRExecutor(client_id=_client_id())
    msgs = []
    try:
        ex.connect()
        ex.ib.reqGlobalCancel()
        ex.ib.sleep(1.0)
        pos = ex.positions()
        for sym, qty in pos:
            if qty and qty > 0:
                st = ex.flatten(sym, qty)
                msgs.append(f"🏁 IBKR flatten {sym} x{qty}: {st}")
        if not msgs:
            msgs.append("🏁 IBKR: cancelled resting orders, no positions to close")
    except Exception as e:                                  # noqa: BLE001
        msgs.append(f"🔴 IBKR flatten failed: {e} — check the account by hand")
    finally:
        ex.disconnect()
    return msgs
