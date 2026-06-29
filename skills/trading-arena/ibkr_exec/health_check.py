#!/usr/bin/env python3
"""Pre-open IBKR canary — is the gateway connected to the brokerage AND holding the
trading session? Prints JSON {"ok":bool,"detail":str}. Read-only; places nothing.

Run it with a TIMEOUT from the caller: if the gateway is stuck (e.g. a competing
login left it at the 'Existing session detected' dialog) the API calls hang, and the
caller's timeout converts that into an unhealthy verdict instead of a freeze.

accountSummary returning a real NetLiquidation proves the gateway holds the *trading*
session — a gateway kicked to a delayed/secondary state returns no account data.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def emit(ok, detail):
    print(json.dumps({"ok": ok, "detail": detail}))
    sys.exit(0)


try:
    from executor import IBKRExecutor
    ex = IBKRExecutor(client_id=int(os.environ.get("OPENING_IBKR_HEALTH_CLIENT_ID", "99")))
    ex.connect(timeout=15)
    summ = {v.tag: v.value for v in ex.ib.accountSummary()
            if v.tag in ("NetLiquidation", "BuyingPower")}
    oo = len(ex.ib.reqAllOpenOrders())
    pos = len(ex.positions())
    ex.disconnect()
    nl = float(summ.get("NetLiquidation", "0") or 0)
    if nl > 0:
        emit(True, f"gateway connected to brokerage — NetLiq ${nl:.0f}, "
                   f"{oo} open orders, {pos} positions")
    emit(False, "connected to gateway but NO account data — competing session / "
                "delayed-secondary state (close other IBKR logins)")
except Exception as e:                                  # noqa: BLE001
    emit(False, str(e)[:100])
