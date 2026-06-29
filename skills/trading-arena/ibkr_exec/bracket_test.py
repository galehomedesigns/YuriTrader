#!/usr/bin/env python3
"""SAFE bracket-shape test: places the Opening-Power long bracket with levels that
CANNOT fill at current market (entry stop ABOVE market, protective stop BELOW market),
asserts zero fill, then cancels both legs. Proves the bracket places + attaches +
cancels on the live account without any real trade."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from executor import IBKRExecutor

ex = IBKRExecutor(client_id=92)
ex.connect()
print("connected", ex.ib.isConnected(), ex.ib.managedAccounts())

# F ~ $14.4. Entry BUY STOP 16 (above -> won't trigger). Protective SELL STOP 13
# (below -> won't trigger even if it went live independently). Neither can fill.
r = ex.place_bracket("F", 1, entry_stop=16.00, protective_stop=13.00)
print("bracket placed:", {k: v for k, v in r.items() if not k.startswith("_")})
filled = (r["parent_filled"] or 0) + (r["child_filled"] or 0)
if filled:
    print("!! UNEXPECTED FILL — flattening + cancelling immediately")
    ex.cancel_trades(r["_pt"], r["_ct"])
    ex.flatten("F", 1)
    ex.disconnect(); sys.exit(1)
print("CANCELLING both legs...")
ex.cancel_trades(r["_pt"], r["_ct"])
print("after cancel:", r["_pt"].orderStatus.status, "/", r["_ct"].orderStatus.status)
print("open orders now:", len(ex.open_orders()), "| positions:", ex.positions() or "none")
ex.disconnect()
ok = (filled == 0 and r["_pt"].orderStatus.status in ("Cancelled", "ApiCancelled", "PendingCancel")
      and r["_ct"].orderStatus.status in ("Cancelled", "ApiCancelled", "PendingCancel"))
print("RESULT:", "PASS - bracket placed (both legs) + cancelled, ZERO fill" if ok
      else f"REVIEW: parent={r['_pt'].orderStatus.status} child={r['_ct'].orderStatus.status} filled={filled}")
