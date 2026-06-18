"""Live execution adapter for the Opening Power agent.

Turns a MATCH + its bar-1 levels into IBKR bracket orders (stop-entry + attached
protective stop), sized by an EVEN split of the user-confirmed budget. The broker
(IBKR) handles the intra-bar stop-entry fill and the protective stop natively, so
no tick feed is needed for entry/stop (G5/G7).

SAFETY MODEL (real money):
  - Own arming gate OPENING_ALLOW_TRADING (separate from the manual concierge's
    gates) — must be "true" or every placement is a validate-only dry run.
  - Budget cap: total notional deployed never exceeds the budget you confirm at
    9:25. OPENING_MAX_BUDGET_USD is an OPTIONAL standing ceiling (fat-finger
    guard); UNSET = no extra cap (defaults to infinity, NOT 80) — your confirmed
    amount and account buying power are the real limits.
  - Whole shares only; a symbol whose even slice can't buy >=1 share is skipped
    (the scan digest flags these up front so a pricey lone qualifier is visible).
  - Every entry carries its protective one-bar stop as a child order.
  - validate=True (or gate off) builds the full order plan and returns it WITHOUT
    transmitting anything.

v1 scope: entry + protective stop + flatten-at-cutoff. Adds/push-pyramiding
(G9/G10/G16) are intentionally OFF for the first live version (they'd exceed a
fixed budget).
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class LiveExecError(Exception):
    pass


def _armed():
    """Real orders transmit only when armed. (The one-time smoke-test pre-gate was
    retired 2026-06-15 — the live order path has been exercised repeatedly in real
    configuration, so OPENING_ALLOW_TRADING is now the sole arming switch.)"""
    return os.environ.get("OPENING_ALLOW_TRADING", "false").lower() == "true"


def max_budget():
    """OPTIONAL standing ceiling (fat-finger guard only). Unset = no ceiling —
    the amount you confirm at 9:25 and the account's buying power are the real
    limits. Set OPENING_MAX_BUDGET_USD only if you want a hard daily lid."""
    v = os.environ.get("OPENING_MAX_BUDGET_USD", "").strip()
    try:
        return float(v) if v else float("inf")
    except ValueError:
        return float("inf")


def plan_allocations(matches, budget):
    """Even split of `budget` across matches; whole shares; skip unaffordable.
    `budget` = the amount YOU confirmed at 9:25 (already capped to available
    buying power by the orchestrator). `matches` = list of dicts
    {symbol, side, entry, stop, price}. Never exceeds `budget`."""
    budget = min(float(budget), max_budget())          # your amount is authoritative
    if not matches or budget <= 0:
        return []
    slice_usd = budget / len(matches)
    allocs, spent = [], 0.0
    for m in matches:
        px = m["entry"] or m["price"]
        if not px or px <= 0:
            continue
        shares = int(math.floor(slice_usd / px))
        if shares < 1:
            allocs.append({**m, "shares": 0, "notional": 0.0,
                           "skipped": f"slice ${slice_usd:.2f} < 1 share @ ${px:.2f}"})
            continue
        notional = shares * px
        if spent + notional > budget + 1e-6:           # never breach the cap
            shares = int(math.floor((budget - spent) / px))
            if shares < 1:
                allocs.append({**m, "shares": 0, "notional": 0.0,
                               "skipped": "budget exhausted"})
                continue
            notional = shares * px
        spent += notional
        allocs.append({**m, "shares": shares, "notional": round(notional, 2),
                       "skipped": None})
    return allocs


class LiveExecutor:
    """Places opening-range bracket orders on IBKR. Connects to the gateway port
    in IBKR_PORT (4001 live / 4002 paper) — i.e. it trades whatever account the
    gateway is logged into."""

    def __init__(self):
        self._ib = None

    def _connect(self):
        from ib_async import IB, util
        util.patchAsyncio()
        if self._ib and self._ib.isConnected():
            return self._ib
        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        port = int(os.environ.get("IBKR_PORT", "4002"))
        cid = int(os.environ.get("OPENING_EXEC_CLIENT_ID", "24"))
        ib = IB()
        try:
            ib.connect(host, port, clientId=cid, timeout=20)
        except Exception as e:
            raise LiveExecError(f"IB gateway not reachable at {host}:{port}: {e}")
        self._ib = ib
        return ib

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
        self._ib = None

    def place_bracket(self, alloc, validate=None):
        """Place a stop-entry + protective-stop bracket for one allocation.
        Returns a plan dict. Transmits ONLY when armed AND not validate."""
        validate = (not _armed()) if validate is None else validate
        sym, side = alloc["symbol"], alloc["side"]
        shares, entry, stop = alloc["shares"], alloc["entry"], alloc["stop"]
        if shares < 1:
            return {"symbol": sym, "placed": False, "reason": alloc.get("skipped") or "0 shares"}
        plan = {
            "symbol": sym, "side": side, "shares": shares,
            "entry_stop": round(entry, 2), "protective_stop": round(stop, 2),
            "notional": alloc["notional"], "validated": validate, "placed": False,
        }
        if validate:
            return plan                                  # built, not transmitted

        from ib_async import Stock, StopOrder
        ib = self._connect()
        contract = Stock(sym.upper(), "SMART", "USD")
        ib.qualifyContracts(contract)
        entry_action = "BUY" if side == "long" else "SELL"
        stop_action = "SELL" if side == "long" else "BUY"
        parent = StopOrder(entry_action, shares, round(entry, 2))
        parent.orderId = ib.client.getReqId()
        parent.transmit = False
        child = StopOrder(stop_action, shares, round(stop, 2))
        child.parentId = parent.orderId
        child.transmit = True                            # transmit the pair
        t_parent = ib.placeOrder(contract, parent)
        t_child = ib.placeOrder(contract, child)
        ib.sleep(1)
        plan.update(placed=True, order_id=parent.orderId,
                    status=t_parent.orderStatus.status)
        return plan

    def flatten_all(self, validate=None):
        """Cutoff/kill: cancel all open orders and market-close any positions.
        Returns a summary. Transmits only when armed and not validate."""
        validate = (not _armed()) if validate is None else validate
        if validate:
            return {"action": "flatten_all", "validated": True, "transmitted": False}
        from ib_async import MarketOrder
        ib = self._connect()
        ib.reqGlobalCancel()
        closed = []
        acct = os.environ.get("IBKR_ACCOUNT_ID", "").strip() or None
        for p in ib.positions(acct) if acct else ib.positions():
            if p.position == 0:
                continue
            act = "SELL" if p.position > 0 else "BUY"
            o = MarketOrder(act, abs(p.position))
            ib.placeOrder(p.contract, o)
            closed.append({"symbol": p.contract.symbol, "qty": abs(p.position)})
        ib.sleep(1)
        return {"action": "flatten_all", "validated": False, "transmitted": True,
                "cancelled_orders": True, "closed_positions": closed}
