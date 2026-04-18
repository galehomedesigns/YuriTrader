#!/usr/bin/env python3
"""
Questrade API client for OpenClaw/Yuri.
Handles auth token management, portfolio queries, market data, and order placement.

Usage:
    python3 questrade.py portfolio          # Account balances + positions
    python3 questrade.py quote AAPL MSFT    # Live quotes
    python3 questrade.py search <keyword>   # Search symbols
    python3 questrade.py orders             # Open orders
    python3 questrade.py history [days]     # Recent executions (default: 7 days)
    python3 questrade.py buy <symbol> <qty> [limit_price]   # Place buy order
    python3 questrade.py sell <symbol> <qty> [limit_price]  # Place sell order
    python3 questrade.py cancel <order_id>  # Cancel an order
"""

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

# Token storage — persists across runs.
# Default is the in-container path (/data is the openclaw bind mount which
# maps to /docker/openclaw-xrt9/data on the host, so CLI and host daemons
# share the same physical file).
TOKEN_FILE = Path(os.environ.get(
    "QUESTRADE_TOKEN_FILE",
    "/home/tonygale/openclaw/state/questrade_token.json",
))
TOKEN_LOCK = Path(str(TOKEN_FILE) + ".lock")
AUTH_URL = os.environ.get("QUESTRADE_AUTH_URL") or "https://login.questrade.com/oauth2/token"


def _api_base_url(token):
    """Return api_server to use, honoring a tunnel port map for IP-blocked hosts.
    Questrade blocks the Hostinger VPS IP AND assigns api_server per session
    (api01..api05). Tunnel maps each subdomain to local port 1150N via
    questrade-tunnel.service. We rewrite token.api_server URL's port only."""
    server = token["api_server"]  # e.g. https://api03.iq.questrade.com/
    port_map_env = os.environ.get("QUESTRADE_API_PORT_MAP")
    if not port_map_env:
        return server
    # port_map_env form: "api01=11501,api02=11502,..."
    import re
    pm = dict(item.split("=") for item in port_map_env.split(","))
    host_match = re.search(r"(api\d+)\.iq\.questrade\.com", server)
    if not host_match or host_match.group(1) not in pm:
        return server
    port = pm[host_match.group(1)]
    # Rewrite only the port: https://api0N.iq.questrade.com:PORT/
    return re.sub(r"(api\d+\.iq\.questrade\.com)(:\d+)?", rf"\1:{port}", server)


def _read_cache_raw():
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except Exception:
        return None


def load_token():
    """Load cached access token if still valid (60s safety margin)."""
    data = _read_cache_raw()
    if data and data.get("expires_at", 0) > time.time() + 60:
        return data
    return None


def save_token(data):
    """Atomically write token cache with expiry timestamp."""
    data["expires_at"] = time.time() + data.get("expires_in", 1800) - 60
    tmp = Path(str(TOKEN_FILE) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(TOKEN_FILE)
    return data


def _post_refresh(refresh_token):
    resp = httpx.get(
        AUTH_URL,
        params={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={"User-Agent": "Mozilla/5.0 (compatible; YuriStockTrader/1.0)"},
        timeout=15.0,
    )
    return resp


def refresh_auth(stale_access_token=None):
    """Rotate to a fresh access token, serialised via flock.

    `stale_access_token`: if the caller just saw this token rejected with 401,
    the cache must not short-circuit us back to the same dead value.
    """
    lock_fd = os.open(str(TOKEN_LOCK), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Another process may have refreshed while we were blocked on the lock.
        fresh = load_token()
        if fresh and fresh.get("access_token") != stale_access_token:
            return fresh

        cache = _read_cache_raw() or {}
        cached_rt = (cache.get("refresh_token") or "").strip()
        env_rt = (os.environ.get("QUESTRADE_REFRESH_TOKEN") or "").strip()

        candidates = []
        if cached_rt:
            candidates.append(("cache", cached_rt))
        if env_rt and env_rt != cached_rt:
            candidates.append(("env", env_rt))
        if not candidates:
            print("ERROR: No refresh token available. Set QUESTRADE_REFRESH_TOKEN.")
            sys.exit(1)

        last_err = None
        for source, rt in candidates:
            resp = _post_refresh(rt)
            if resp.status_code == 200:
                data = resp.json()
                save_token(data)
                return data
            last_err = (source, resp.status_code, resp.text[:200])
            print(
                f"  QT refresh via {source} failed HTTP {resp.status_code}: "
                f"{resp.text[:200]}",
                file=sys.stderr,
            )

        print(
            f"ERROR: Questrade auth failed (last: {last_err}). "
            "Generate a new token at questrade.com > Settings > API centre."
        )
        sys.exit(1)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def get_client():
    """Return an authenticated httpx client with the correct API server."""
    token = load_token()
    if not token:
        token = refresh_auth()

    return httpx.Client(
        base_url=_api_base_url(token),
        headers={"Authorization": f"Bearer {token['access_token']}"},
        timeout=15.0,
    )


def _request_with_retry(method, path, params=None, data=None):
    """GET/POST/DELETE with automatic token refresh on 401."""
    token = load_token() or refresh_auth()
    client = httpx.Client(
        base_url=_api_base_url(token),
        headers={"Authorization": f"Bearer {token['access_token']}"},
        timeout=15.0,
    )
    try:
        if method == "GET":
            resp = client.get(path, params=params)
        elif method == "POST":
            resp = client.post(path, json=data)
        elif method == "DELETE":
            resp = client.delete(path)
        else:
            raise ValueError(f"unsupported method: {method}")

        if resp.status_code == 401:
            refresh_auth(stale_access_token=token["access_token"])
            token = load_token()
            client = httpx.Client(
                base_url=_api_base_url(token),
                headers={"Authorization": f"Bearer {token['access_token']}"},
                timeout=15.0,
            )
            if method == "GET":
                resp = client.get(path, params=params)
            elif method == "POST":
                resp = client.post(path, json=data)
            elif method == "DELETE":
                resp = client.delete(path)

        resp.raise_for_status()
        return resp.json()
    finally:
        client.close()


def api_get(path, params=None):
    return _request_with_retry("GET", path, params=params)


def api_post(path, data=None):
    return _request_with_retry("POST", path, data=data)


def api_delete(path):
    return _request_with_retry("DELETE", path)


# ── Account helpers ──

def get_accounts():
    return api_get("/v1/accounts")["accounts"]


def get_primary_account_id():
    accounts = get_accounts()
    # Prefer margin, then TFSA, then first available
    for pref in ["Margin", "TFSA", "RRSP", "Cash"]:
        for a in accounts:
            if a["type"] == pref and a["status"] == "Active":
                return a["number"]
    return accounts[0]["number"]


# ── Commands ──

def cmd_portfolio():
    acct = get_primary_account_id()
    balances = api_get(f"/v1/accounts/{acct}/balances")
    positions = api_get(f"/v1/accounts/{acct}/positions")

    # Combined balances (CAD + USD)
    print("== Account Balances ==")
    for b in balances.get("combinedBalances", []):
        currency = b["currency"]
        print(f"  {currency}: Cash ${b['cash']:,.2f} | Market Value ${b['marketValue']:,.2f} | Total Equity ${b['totalEquity']:,.2f}")

    # Positions
    pos_list = positions.get("positions", [])
    if not pos_list:
        print("\n== Positions ==\n  No open positions.")
        return

    print(f"\n== Positions ({len(pos_list)}) ==")
    print(f"  {'Symbol':<10} {'Qty':>8} {'Avg Cost':>10} {'Current':>10} {'P&L':>12} {'P&L %':>8}")
    print(f"  {'-'*60}")
    for p in pos_list:
        symbol = p["symbol"]
        qty = p["openQuantity"]
        avg_cost = p["averageEntryPrice"]
        current = p["currentPrice"]
        pnl = p.get("openPnl", 0)
        pnl_pct = ((current / avg_cost) - 1) * 100 if avg_cost > 0 else 0
        print(f"  {symbol:<10} {qty:>8.0f} {avg_cost:>10.2f} {current:>10.2f} {pnl:>12.2f} {pnl_pct:>7.1f}%")


def cmd_quote(symbols):
    if not symbols:
        print("Usage: questrade.py quote SYMBOL [SYMBOL ...]")
        return

    # Resolve symbol IDs
    ids = []
    for sym in symbols:
        results = api_get("/v1/symbols/search", params={"prefix": sym})
        matches = results.get("symbols", [])
        if matches:
            ids.append(str(matches[0]["symbolId"]))
        else:
            print(f"  Symbol not found: {sym}")

    if not ids:
        return

    quotes = api_get("/v1/markets/quotes", params={"ids": ",".join(ids)})
    print(f"  {'Symbol':<10} {'Last':>10} {'Change':>8} {'Chg%':>8} {'Bid':>10} {'Ask':>10} {'Volume':>12}")
    print(f"  {'-'*70}")
    for q in quotes.get("quotes", []):
        symbol = q["symbol"]
        last = q.get("lastTradePrice") or 0
        chg = q.get("lastTradePrice", 0) - q.get("openPrice", 0) if q.get("openPrice") else 0
        chg_pct = (chg / q["openPrice"] * 100) if q.get("openPrice") else 0
        bid = q.get("bidPrice") or 0
        ask = q.get("askPrice") or 0
        vol = q.get("volume") or 0
        print(f"  {symbol:<10} {last:>10.2f} {chg:>+8.2f} {chg_pct:>+7.1f}% {bid:>10.2f} {ask:>10.2f} {vol:>12,}")


def cmd_search(keyword):
    results = api_get("/v1/symbols/search", params={"prefix": keyword})
    matches = results.get("symbols", [])
    if not matches:
        print(f"  No results for '{keyword}'")
        return

    print(f"  {'Symbol':<10} {'ID':>10} {'Exchange':<10} {'Description'}")
    print(f"  {'-'*60}")
    for s in matches[:15]:
        print(f"  {s['symbol']:<10} {s['symbolId']:>10} {s.get('listingExchange',''):<10} {s.get('description','')[:40]}")


def cmd_orders():
    acct = get_primary_account_id()
    orders = api_get(f"/v1/accounts/{acct}/orders", params={"stateFilter": "Open"})
    order_list = orders.get("orders", [])

    if not order_list:
        print("  No open orders.")
        return

    print(f"  {'ID':<12} {'Symbol':<10} {'Side':<6} {'Qty':>6} {'Type':<8} {'Price':>10} {'Status':<12}")
    print(f"  {'-'*66}")
    for o in order_list:
        print(f"  {o['id']:<12} {o['symbol']:<10} {o['side']:<6} {o['totalQuantity']:>6.0f} {o['orderType']:<8} {o.get('limitPrice', 0):>10.2f} {o['state']:<12}")


def cmd_history(days=7):
    acct = get_primary_account_id()
    end = datetime.now()
    start = end - timedelta(days=int(days))
    executions = api_get(
        f"/v1/accounts/{acct}/executions",
        params={
            "startTime": start.strftime("%Y-%m-%dT00:00:00-05:00"),
            "endTime": end.strftime("%Y-%m-%dT23:59:59-05:00"),
        },
    )
    exec_list = executions.get("executions", [])

    if not exec_list:
        print(f"  No executions in the last {days} days.")
        return

    print(f"  {'Date':<12} {'Symbol':<10} {'Side':<6} {'Qty':>6} {'Price':>10} {'Commission':>12}")
    print(f"  {'-'*58}")
    for e in exec_list:
        dt = e.get("timestamp", "")[:10]
        print(f"  {dt:<12} {e['symbol']:<10} {e['side']:<6} {e['quantity']:>6.0f} {e['price']:>10.2f} {e.get('commission', 0):>12.2f}")


def cmd_buy(symbol, qty, limit_price=None):
    _place_order(symbol, int(qty), "Buy", limit_price)


def cmd_sell(symbol, qty, limit_price=None):
    _place_order(symbol, int(qty), "Sell", limit_price)


def _place_order(symbol, qty, side, limit_price=None):
    # Resolve symbol ID
    results = api_get("/v1/symbols/search", params={"prefix": symbol})
    matches = results.get("symbols", [])
    if not matches:
        print(f"ERROR: Symbol '{symbol}' not found.")
        return

    sym = matches[0]
    symbol_id = sym["symbolId"]

    acct = get_primary_account_id()

    order = {
        "accountNumber": acct,
        "symbolId": symbol_id,
        "quantity": qty,
        "orderType": "Market" if limit_price is None else "Limit",
        "timeInForce": "Day",
        "action": side,
        "primaryRoute": "AUTO",
        "secondaryRoute": "AUTO",
    }

    if limit_price is not None:
        order["limitPrice"] = float(limit_price)

    # Get a quote first so the user sees what they're getting
    quotes = api_get("/v1/markets/quotes", params={"ids": str(symbol_id)})
    q = quotes["quotes"][0] if quotes.get("quotes") else {}
    last = q.get("lastTradePrice", "N/A")

    order_type = order["orderType"]
    price_str = f"@ ${float(limit_price):.2f}" if limit_price else f"@ Market (~${last})"

    print(f"  ORDER: {side} {qty} x {sym['symbol']} ({sym.get('description','')}) {price_str}")
    print(f"  Type: {order_type} | Exchange: {sym.get('listingExchange','')}")

    result = api_post(f"/v1/accounts/{acct}/orders", data=order)
    order_resp = result.get("orders", [{}])[0] if result.get("orders") else result
    order_id = order_resp.get("id", "unknown")
    state = order_resp.get("state", "unknown")
    print(f"  Result: Order #{order_id} — {state}")


def cmd_cancel(order_id):
    acct = get_primary_account_id()
    result = api_delete(f"/v1/accounts/{acct}/orders/{order_id}")
    print(f"  Order #{order_id} cancelled.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "portfolio":
        cmd_portfolio()
    elif cmd == "quote":
        cmd_quote(sys.argv[2:])
    elif cmd == "search":
        cmd_search(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "orders":
        cmd_orders()
    elif cmd == "history":
        cmd_history(sys.argv[2] if len(sys.argv) > 2 else 7)
    elif cmd == "buy":
        if len(sys.argv) < 4:
            print("Usage: questrade.py buy <symbol> <qty> [limit_price]")
            sys.exit(1)
        cmd_buy(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    elif cmd == "sell":
        if len(sys.argv) < 4:
            print("Usage: questrade.py sell <symbol> <qty> [limit_price]")
            sys.exit(1)
        cmd_sell(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    elif cmd == "cancel":
        if len(sys.argv) < 3:
            print("Usage: questrade.py cancel <order_id>")
            sys.exit(1)
        cmd_cancel(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
