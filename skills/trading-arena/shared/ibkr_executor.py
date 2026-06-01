"""IBKR Live Trading Executor.

ib_async-backed client for placing real (or paper) orders on Interactive
Brokers from the stock_concierge daemon. Mirrors the QuestradeExecutor
public interface so callers can swap by changing STOCK_BROKER in .env.

Auth model: NO API key. The IB Gateway container in infra/ib-gateway/
holds the user's IBKR login and 2FA session. This module connects to it
over localhost (port 4002 paper / 4001 live).

DOUBLE-GATE SAFETY:
  Gate 1 (env): IBKR_ALLOW_TRADING=true
  Gate 2 (env): MANUAL_STOCK_TRADING_ENABLED=true
Both must be true for execute_manual_trade() to place a real order.
Either false = validate-only (no order transmitted to IBKR).

Note: when IBKR_TRADING_MODE=paper the orders are real in IBKR's paper
system but use fake money. The double-gate still applies — paper orders
also require both gates open, so an accidental config flip doesn't fire
trades you didn't expect.
"""
import os
import sys
import time

from ib_async import IB, Stock, MarketOrder, util

util.patchAsyncio()


class IBKRExecutorError(Exception):
    """Raised when an IBKR API call or safety check fails."""


class IBKRExecutor:
    """ib_async-backed IBKR client for the trading-arena stock concierge."""

    def __init__(self):
        self._ib = None
        self._account_id = None
        self._delayed_md = False

    # ===== Connection =====

    def _connect(self):
        if self._ib is not None and self._ib.isConnected():
            return self._ib
        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        port = int(os.environ.get("IBKR_PORT", "4002"))
        client_id = int(os.environ.get("IBKR_CLIENT_ID", "17"))
        ib = IB()
        try:
            ib.connect(host, port, clientId=client_id, timeout=20)
        except Exception as e:
            raise IBKRExecutorError(
                f"IB Gateway not reachable at {host}:{port} (clientId={client_id}): {e}. "
                f"Check: docker ps | grep ib-gateway"
            )
        self._ib = ib
        return ib

    def _disconnect(self):
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()
        self._ib = None

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._disconnect()

    # ===== Account =====

    def get_account_id(self):
        if self._account_id:
            return self._account_id
        configured = os.environ.get("IBKR_ACCOUNT_ID", "").strip()
        if configured:
            self._account_id = configured
            return configured
        ib = self._connect()
        accounts = ib.managedAccounts()
        if not accounts:
            raise IBKRExecutorError(
                "Gateway returned no managed accounts (still logging in?)"
            )
        self._account_id = accounts[0]
        return self._account_id

    def get_balance(self):
        """Per-currency balance dict, mirroring QuestradeExecutor.get_balance():
        {"CAD": {"cash", "market_value", "total_equity", "buying_power",
                 "available_funds"}}. IBKR reports a single account base currency.
        """
        ib = self._connect()
        account = self.get_account_id()
        summary = {v.tag: v for v in ib.accountSummary(account)}
        def f(tag, default=0.0):
            v = summary.get(tag)
            return float(v.value) if v else default
        nlv = summary.get("NetLiquidation")
        currency = nlv.currency if nlv else "USD"
        total_equity = f("NetLiquidation")
        cash = f("TotalCashValue")
        return {
            currency: {
                "cash": cash,
                "market_value": round(total_equity - cash, 2),
                "total_equity": total_equity,
                "buying_power": f("BuyingPower"),
                "available_funds": f("AvailableFunds"),
            }
        }

    def _available_funds(self):
        """(available_funds, currency) read straight off the account summary —
        decoupled from get_balance()'s per-currency public shape."""
        ib = self._connect()
        account = self.get_account_id()
        for v in ib.accountSummary(account):
            if v.tag == "AvailableFunds":
                return float(v.value), v.currency
        return None, "USD"

    def get_positions(self):
        ib = self._connect()
        account = self.get_account_id()
        out = []
        for p in ib.positions(account):
            out.append({
                "symbol": p.contract.symbol,
                "qty": p.position,
                "avg_cost": p.avgCost,
                "currency": p.contract.currency,
                "exchange": p.contract.primaryExchange or p.contract.exchange,
            })
        return out

    def get_open_orders(self):
        ib = self._connect()
        out = []
        for t in ib.openTrades():
            out.append({
                "order_id": t.order.orderId,
                "symbol": t.contract.symbol,
                "side": t.order.action,
                "qty": t.order.totalQuantity,
                "order_type": t.order.orderType,
                "status": t.orderStatus.status,
            })
        return out

    def cancel_order(self, order_id):
        ib = self._connect()
        for t in ib.openTrades():
            if t.order.orderId == int(order_id):
                ib.cancelOrder(t.order)
                return True
        return False

    def cancel_all(self):
        """Cancel every open order. Returns {'cancelled': [...], 'count': N}
        to mirror QuestradeExecutor.cancel_all() (used by the /kill switch)."""
        ib = self._connect()
        open_ids = [t.order.orderId for t in ib.openTrades()]
        ib.reqGlobalCancel()
        return {"cancelled": open_ids, "count": len(open_ids)}

    # ===== Market data =====

    def _stock(self, symbol, currency="USD"):
        return Stock(symbol.upper(), "SMART", currency)

    @staticmethod
    def _clean_px(v):
        """Drop NaN and non-positive sentinels — IBKR returns -1 for fields
        with no value (e.g. bid/ask when the market is closed)."""
        if v is None:
            return None
        if isinstance(v, float) and v != v:   # NaN
            return None
        return v if v > 0 else None

    def _read_quote(self, ib, contract):
        ticker = ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
        ib.sleep(2.5)
        out = (self._clean_px(ticker.bid), self._clean_px(ticker.ask),
               self._clean_px(ticker.last), self._clean_px(ticker.close))
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass
        return out

    def get_quote(self, symbol, currency="USD"):
        ib = self._connect()
        contract = self._stock(symbol, currency)
        try:
            ib.qualifyContracts(contract)
        except Exception as e:
            raise IBKRExecutorError(f"Symbol {symbol!r} not recognized: {e}")
        bid, ask, last, close = self._read_quote(ib, contract)
        # No live data (no market-data subscription)? Fall back to delayed data
        # (free). Good enough for a pre-trade price reference; market orders
        # still fill at the real exchange price.
        if bid is None and ask is None and last is None and close is None:
            ib.reqMarketDataType(3)  # 3 = delayed
            self._delayed_md = True
            bid, ask, last, close = self._read_quote(ib, contract)
        return {
            "symbol": symbol.upper(),
            "bid": bid,
            "ask": ask,
            "last": last,
            "previous_close": close,
            "price": last or ask or bid or close,
            "currency": currency,
            "delayed": self._delayed_md,
        }

    # ===== Orders =====

    def place_market_order(self, symbol, side, qty, validate=True):
        """Place a market order. If validate=True, returns the contract+order
        without transmitting. If validate=False, transmits and waits for fill
        confirmation (or rejection) up to ~30s.
        """
        if side.lower() not in ("buy", "sell"):
            raise IBKRExecutorError(f"side must be 'buy' or 'sell', got {side!r}")
        if qty <= 0:
            raise IBKRExecutorError(f"qty must be positive, got {qty}")
        ib = self._connect()
        contract = self._stock(symbol)
        try:
            ib.qualifyContracts(contract)
        except Exception as e:
            raise IBKRExecutorError(f"Symbol {symbol!r} not recognized: {e}")
        action = "BUY" if side.lower() == "buy" else "SELL"
        order = MarketOrder(action, qty)
        order.transmit = not validate
        order.outsideRth = False
        if validate:
            return {
                "validated": True,
                "symbol": symbol.upper(),
                "side": action,
                "qty": qty,
                "order_type": "MKT",
            }
        trade = ib.placeOrder(contract, order)
        deadline = time.time() + 30
        while time.time() < deadline:
            ib.sleep(0.5)
            status = trade.orderStatus.status
            if status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
                break
        return {
            "validated": False,
            "order_id": trade.order.orderId,
            "symbol": symbol.upper(),
            "side": action,
            "qty": qty,
            "status": trade.orderStatus.status,
            "filled_qty": trade.orderStatus.filled,
            "avg_fill_price": trade.orderStatus.avgFillPrice,
            "remaining": trade.orderStatus.remaining,
        }

    def execute_manual_trade(self, symbol, side, qty):
        """Main entry point for the Telegram concierge. Mirrors
        QuestradeExecutor.execute_manual_trade(): runs pre-trade checks, then
        places a market order ONLY when BOTH gates are open; otherwise returns
        a validate-only (dry-run) result without transmitting anything.

        DOUBLE-GATE: IBKR_ALLOW_TRADING=true AND MANUAL_STOCK_TRADING_ENABLED=true.
        Either false → dry_run=True (no order sent to IBKR).

        Return shape matches Questrade so the concierge is broker-agnostic:
          {dry_run, symbol, side, qty, price, total, currency[, order_id, status]}.
        """
        if side.lower() not in ("buy", "sell"):
            raise IBKRExecutorError(f"side must be 'buy' or 'sell', got {side!r}")
        if qty <= 0:
            raise IBKRExecutorError(f"qty must be positive, got {qty}")

        gate1 = os.environ.get("IBKR_ALLOW_TRADING", "false").lower() == "true"
        gate2 = os.environ.get("MANUAL_STOCK_TRADING_ENABLED", "false").lower() == "true"
        live = gate1 and gate2

        quote = self.get_quote(symbol)
        ref_price = quote["ask"] if side.lower() == "buy" else quote["bid"]
        if ref_price is None:
            ref_price = quote["price"]
        if ref_price is None:
            raise IBKRExecutorError(
                f"No quote available for {symbol} — refusing to place order "
                f"without a price reference (live + delayed market data both empty)"
            )
        currency = quote.get("currency", "USD")

        if side.lower() == "buy":
            avail, _cur = self._available_funds()
            need = ref_price * qty
            if avail is not None and need > avail:
                raise IBKRExecutorError(
                    f"Insufficient buying power: need ${need:.2f}, have ${avail:.2f}"
                )

        if not live:
            # One or both gates closed → validate-only, transmit nothing.
            return {
                "dry_run": True,
                "symbol": symbol.upper(),
                "side": side.upper(),
                "qty": qty,
                "price": ref_price,
                "total": round(ref_price * qty, 2),
                "currency": currency,
            }

        placed = self.place_market_order(symbol, side, qty, validate=False)
        fill_price = placed.get("avg_fill_price") or ref_price
        return {
            "dry_run": False,
            "symbol": symbol.upper(),
            "side": side.upper(),
            "qty": qty,
            "price": fill_price,
            "total": round((fill_price or 0) * qty, 2),
            "currency": currency,
            "order_id": placed.get("order_id"),
            "status": placed.get("status"),
        }


# Module-level helper to mirror questrade_executor.is_market_open()
def is_market_open():
    """US equities regular trading hours: 9:30-16:00 ET Mon-Fri."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60
