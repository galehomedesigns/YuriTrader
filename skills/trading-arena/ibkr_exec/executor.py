#!/usr/bin/env python3
"""Isolated IBKR order executor for Opening-Power — browser-free, API-direct.

The robust parallel path to the fragile TradingView-CDP DOM staging. Imports ONLY
ib_async + stdlib — NOTHING from opening_agent / tv_* / questrade / CDP. Talks solely
to the IB Gateway API (127.0.0.1:4001 live) on a DEDICATED clientId.

Order shape = the Opening-Power long bracket: a parent BUY STOP (go long when price
trades up through the entry) with an attached child SELL STOP (protective stop-loss,
inactive until the entry fills). transmit chain: parent=False, child=True so both
arrive atomically and the child is held until fill.
"""
import os
from ib_async import IB, Stock, StopOrder, StopLimitOrder, MarketOrder


class IBKRExecutor:
    def __init__(self, client_id=92):
        self.host = os.environ.get("IBKR_HOST", "127.0.0.1")
        self.port = int(os.environ.get("IBKR_PORT", "4001"))   # 4001 = live gateway API
        self.acct = os.environ.get("IBKR_ACCOUNT_ID", "")
        self.client_id = client_id
        self.ib = IB()

    def connect(self, timeout=25):
        if not self.ib.isConnected():
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=timeout)
        return self.ib.isConnected()

    def disconnect(self):
        try:
            self.ib.disconnect()
        except Exception:
            pass

    def _stock(self, sym):
        c = Stock(sym, "SMART", "USD")
        self.ib.qualifyContracts(c)
        return c

    def place_bracket(self, sym, qty, entry_stop, protective_stop):
        """Long bracket: parent BUY STOP @ entry_stop + attached child SELL STOP @
        protective_stop. Child is parentId-attached so it stays inactive until the
        entry fills. Returns leg statuses + the Trade objects (keys _pt/_ct)."""
        c = self._stock(sym)
        parent = StopOrder("BUY", qty, round(entry_stop, 2), transmit=False, tif="DAY")
        parent.orderId = self.ib.client.getReqId()
        if self.acct:
            parent.account = self.acct
        child = StopOrder("SELL", qty, round(protective_stop, 2), transmit=True,
                          parentId=parent.orderId, tif="DAY")
        if self.acct:
            child.account = self.acct
        pt = self.ib.placeOrder(c, parent)
        ct = self.ib.placeOrder(c, child)
        self.ib.sleep(2.5)
        return {"symbol": sym, "qty": qty, "entry": round(entry_stop, 2),
                "stop": round(protective_stop, 2), "parent_id": parent.orderId,
                "parent_status": pt.orderStatus.status, "parent_filled": pt.orderStatus.filled,
                "child_status": ct.orderStatus.status, "child_filled": ct.orderStatus.filled,
                "_pt": pt, "_ct": ct}

    def place_short_bracket(self, sym, qty, entry_stop, entry_limit, protective_stop):
        """SHORT bracket: parent SELL STOP-LIMIT (trigger=entry_stop at the bar-1-low
        breakdown, limit=entry_limit to cap slippage / capture the spread) + attached
        child BUY STOP (protective, above bar-1 high). Child held until the entry fills.
        Returns leg statuses + Trade objects (_pt/_ct). The stop-LIMIT may NOT fill if
        price gaps through the limit (adverse selection) — that is the live test."""
        c = self._stock(sym)
        parent = StopLimitOrder("SELL", qty, round(entry_limit, 2), round(entry_stop, 2),
                                transmit=False, tif="DAY")
        parent.orderId = self.ib.client.getReqId()
        if self.acct:
            parent.account = self.acct
        child = StopOrder("BUY", qty, round(protective_stop, 2), transmit=True,
                          parentId=parent.orderId, tif="DAY")
        if self.acct:
            child.account = self.acct
        pt = self.ib.placeOrder(c, parent)
        ct = self.ib.placeOrder(c, child)
        self.ib.sleep(2.5)
        return {"symbol": sym, "qty": qty, "entry_stop": round(entry_stop, 2),
                "entry_limit": round(entry_limit, 2), "stop": round(protective_stop, 2),
                "parent_id": parent.orderId, "parent_status": pt.orderStatus.status,
                "parent_filled": pt.orderStatus.filled, "child_status": ct.orderStatus.status,
                "child_filled": ct.orderStatus.filled, "_pt": pt, "_ct": ct}

    def cover(self, sym, qty):
        """Buy-to-cover a short at market (cutoff flatten for the short strategy)."""
        c = self._stock(sym)
        o = MarketOrder("BUY", abs(qty))
        if self.acct:
            o.account = self.acct
        t = self.ib.placeOrder(c, o)
        self.ib.sleep(2)
        return t.orderStatus.status

    def cancel_trades(self, *trades):
        for t in trades:
            if t is not None:
                try:
                    self.ib.cancelOrder(t.order)
                except Exception:
                    pass
        self.ib.sleep(1.5)

    def open_orders(self):
        return self.ib.reqAllOpenOrders()

    def positions(self):
        return [(p.contract.symbol, p.position) for p in self.ib.positions()]

    def flatten(self, sym, qty):
        """Market-sell to close a long (cutoff flatten)."""
        c = self._stock(sym)
        o = MarketOrder("SELL", abs(qty))
        if self.acct:
            o.account = self.acct
        t = self.ib.placeOrder(c, o)
        self.ib.sleep(2)
        return t.orderStatus.status
