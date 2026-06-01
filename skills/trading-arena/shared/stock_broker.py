"""Stock broker selector.

`stock_concierge` (and any other manual-stock caller) should obtain its
executor from here instead of importing a concrete executor directly. Which
broker is returned is controlled by `STOCK_BROKER` in .env:

    STOCK_BROKER=ibkr       -> IBKRExecutor   (IB Gateway, real order placement)
    STOCK_BROKER=questrade  -> QuestradeExecutor (read-only API; orders 403 — see
                               QUESTRADE_NEXT_STEPS.md) [default for back-compat]

Both executors expose the same manual-path contract:
    get_quote(symbol)        -> {"last", "bid", "ask", "price", "currency", ...}
    get_balance()            -> {CURRENCY: {"cash","total_equity","buying_power", ...}}
    execute_manual_trade(symbol, side, qty)
                             -> {"dry_run", "symbol", "side", "qty", "price",
                                 "total", "currency"[, "order_id"]}
    cancel_all()             -> {"cancelled": [...], "count": N}

Use `StockExecutorError` in `except` clauses to catch errors from whichever
broker is active.
"""
import os

from shared.questrade_executor import QuestradeExecutor, QuestradeExecutorError
from shared.ibkr_executor import IBKRExecutor, IBKRExecutorError

# Catch-all for callers: `except StockExecutorError as e:` works regardless of
# which broker is active.
StockExecutorError = (QuestradeExecutorError, IBKRExecutorError)


def get_broker_name():
    return os.environ.get("STOCK_BROKER", "questrade").strip().lower()


def get_executor():
    """Return an executor instance for the broker selected by STOCK_BROKER."""
    broker = get_broker_name()
    if broker == "ibkr":
        return IBKRExecutor()
    if broker in ("questrade", ""):
        return QuestradeExecutor()
    raise ValueError(
        f"Unknown STOCK_BROKER={broker!r} (expected 'ibkr' or 'questrade')"
    )
