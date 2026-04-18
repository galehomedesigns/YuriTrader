"""Questrade Live Trading Executor.

Stdlib-only Python client for placing real orders on Questrade from the
stock_concierge daemon. Mirrors the kraken_executor.py interface so the
Telegram concierge code can be shared / parallelized.

DOUBLE-GATE SAFETY:
  Gate 1 (env): QUESTRADE_ALLOW_TRADING=true
  Gate 2 (env): MANUAL_STOCK_TRADING_ENABLED=true
Both must be true for execute_manual_trade() to place a real order.
Either false = validate-only (no POST to Questrade).

Pre-trade checks (ALL must pass for execute_manual_trade):
  1. Both gates open
  2. Symbol resolvable via /v1/symbols/search
  3. Quote fetchable
  4. Quantity > 0 (whole shares)
  5. Total cost (qty * ask) <= available buying power in relevant currency
"""
import fcntl
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

AUTH_URL = "https://login.questrade.com/oauth2/token"
TOKEN_FILE = os.environ.get(
    "QUESTRADE_TOKEN_FILE",
    "/docker/openclaw-xrt9/data/.openclaw/questrade_token.json",
)
TOKEN_LOCK = TOKEN_FILE + ".lock"
# Questrade sits behind Cloudflare, which blocks urllib's default User-Agent
# (HTTP 403, Cloudflare error 1010). Identify as a real browser-ish client.
USER_AGENT = "Mozilla/5.0 (compatible; YuriStockTrader/1.0)"


class QuestradeExecutorError(Exception):
    """Raised when a Questrade API call or safety check fails."""


class QuestradeExecutor:
    """Stdlib Questrade client for the trading-arena stock concierge."""

    def __init__(self):
        self._token = None
        self._account_id = None
        self._symbol_id_cache = {}

    # ===== Token / auth =====
    #
    # Concurrency model: Questrade rotates the refresh_token on every successful
    # /oauth2/token call (single-use). With 4 processes sharing the cache file
    # (stock-concierge daemon, stock_position_watcher, stock_buy_watcher, CLI),
    # any concurrent refresh breaks all-but-one caller with HTTP 400 and leaves
    # the cache in a stale state. We serialise through an exclusive flock on
    # `<TOKEN_FILE>.lock` and, inside the lock, re-read the cache to see if
    # someone else already did the work.

    def _read_cache_raw(self):
        """Return the raw cache dict (even if expired), or None."""
        if not os.path.exists(TOKEN_FILE):
            return None
        try:
            with open(TOKEN_FILE) as f:
                return json.load(f)
        except Exception:
            return None

    def _load_cached_token(self):
        data = self._read_cache_raw()
        if data and data.get("expires_at", 0) > time.time() + 60:
            return data
        return None

    def _save_token(self, data):
        data["expires_at"] = time.time() + data.get("expires_in", 1800) - 60
        tmp = TOKEN_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, TOKEN_FILE)
        except Exception as e:
            print(f"  QT token save warning: {e}", file=sys.stderr)
        return data

    def _post_refresh(self, refresh_token):
        url = f"{AUTH_URL}?" + urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            raise QuestradeExecutorError(
                f"Questrade auth failed HTTP {e.code}: {body}"
            )

    def _refresh(self, stale_access_token=None):
        """Rotate to a new access token, serialised across processes.

        `stale_access_token` — if provided, this is an access_token the caller
        just saw rejected by Questrade (HTTP 401). In that case, if the cache
        file still holds the same access_token, we know the cache is bad and
        must force a real refresh. If the cache holds a DIFFERENT token, some
        other process has already refreshed for us and we can short-circuit.
        """
        # Serialise refreshes across all processes sharing this token cache.
        lock_fd = os.open(TOKEN_LOCK, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            # Another process may have refreshed while we were blocked on the
            # lock — if so, use their result instead of burning another rotation.
            fresh = self._load_cached_token()
            if fresh and fresh.get("access_token") != stale_access_token:
                self._token = fresh
                return fresh

            cache = self._read_cache_raw() or {}
            cached_rt = (cache.get("refresh_token") or "").strip()
            env_rt = (os.environ.get("QUESTRADE_REFRESH_TOKEN") or "").strip()

            # Try cached first (usually the most recently rotated value), then
            # env as fallback. The env var is what Tony most recently pasted —
            # useful when the cache has gone permanently bad (e.g., race loss).
            candidates = []
            if cached_rt:
                candidates.append(("cache", cached_rt))
            if env_rt and env_rt != cached_rt:
                candidates.append(("env", env_rt))
            if not candidates:
                raise QuestradeExecutorError(
                    "no Questrade refresh_token available (cache empty and "
                    "QUESTRADE_REFRESH_TOKEN unset)"
                )

            last_err = None
            for source, rt in candidates:
                try:
                    data = self._post_refresh(rt)
                    self._save_token(data)
                    self._token = data
                    return data
                except QuestradeExecutorError as e:
                    last_err = e
                    print(
                        f"  QT refresh via {source} token failed: {e}",
                        file=sys.stderr,
                    )
                    continue
            raise last_err
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)

    def _get_token(self):
        if self._token and self._token.get("expires_at", 0) > time.time() + 60:
            return self._token
        cached = self._load_cached_token()
        if cached:
            self._token = cached
            return cached
        return self._refresh()

    # ===== HTTP =====

    def _request(self, method, path, params=None, body=None, _retry=True):
        token = self._get_token()
        base = token["api_server"].rstrip("/")
        url = f"{base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", f"Bearer {token['access_token']}")
        req.add_header("User-Agent", USER_AGENT)
        if body is not None:
            req.add_header("Content-Type", "application/json")
            req.data = json.dumps(body).encode()

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 401 and _retry:
                self._refresh(stale_access_token=token["access_token"])
                return self._request(method, path, params=params, body=body, _retry=False)
            body_text = e.read().decode()[:300] if e.fp else ""
            raise QuestradeExecutorError(
                f"Questrade {method} {path} HTTP {e.code}: {body_text}"
            )
        except Exception as e:
            raise QuestradeExecutorError(f"Questrade {method} {path} error: {e}")

    def _get(self, path, params=None):
        return self._request("GET", path, params=params)

    def _post(self, path, body):
        return self._request("POST", path, body=body)

    def _delete(self, path):
        return self._request("DELETE", path)

    # ===== Account helpers =====

    def get_account_id(self):
        """Returns the primary account number. Prefers Margin > TFSA > RRSP > Cash."""
        if self._account_id:
            return self._account_id
        accounts = self._get("/v1/accounts").get("accounts", [])
        if not accounts:
            raise QuestradeExecutorError("no Questrade accounts available")
        for pref in ["Margin", "TFSA", "RRSP", "Cash"]:
            for a in accounts:
                if a.get("type") == pref and a.get("status") == "Active":
                    self._account_id = a["number"]
                    return self._account_id
        self._account_id = accounts[0]["number"]
        return self._account_id

    def get_balance(self):
        """Returns per-currency balance dict:
        {"USD": {"cash", "market_value", "total_equity", "buying_power"},
         "CAD": {...}}
        """
        acct = self.get_account_id()
        raw = self._get(f"/v1/accounts/{acct}/balances")
        out = {}
        for b in raw.get("perCurrencyBalances", []):
            out[b["currency"]] = {
                "cash": float(b.get("cash", 0) or 0),
                "market_value": float(b.get("marketValue", 0) or 0),
                "total_equity": float(b.get("totalEquity", 0) or 0),
                "buying_power": float(b.get("buyingPower", 0) or 0),
            }
        return out

    def get_positions(self):
        """Returns list of open positions with live P&L fields."""
        acct = self.get_account_id()
        raw = self._get(f"/v1/accounts/{acct}/positions")
        return raw.get("positions", [])

    def get_open_orders(self):
        acct = self.get_account_id()
        raw = self._get(
            f"/v1/accounts/{acct}/orders",
            params={"stateFilter": "Open"},
        )
        return raw.get("orders", [])

    def cancel_order(self, order_id):
        acct = self.get_account_id()
        return self._delete(f"/v1/accounts/{acct}/orders/{order_id}")

    def cancel_all(self):
        """Cancel every open order. Used by the /kill switch."""
        open_orders = self.get_open_orders()
        cancelled = []
        for o in open_orders:
            try:
                self.cancel_order(o["id"])
                cancelled.append(o["id"])
            except Exception as e:
                print(f"  cancel {o['id']} failed: {e}", file=sys.stderr)
        return {"cancelled": cancelled, "count": len(cancelled)}

    # ===== Market data =====

    def resolve_symbol_id(self, symbol):
        """Returns Questrade symbolId (int) and caches it."""
        if symbol in self._symbol_id_cache:
            return self._symbol_id_cache[symbol]
        raw = self._get("/v1/symbols/search", params={"prefix": symbol})
        matches = raw.get("symbols", [])
        exact = [m for m in matches if m.get("symbol") == symbol]
        if exact:
            sid = exact[0]["symbolId"]
        elif matches:
            sid = matches[0]["symbolId"]
        else:
            raise QuestradeExecutorError(f"symbol not found: {symbol}")
        self._symbol_id_cache[symbol] = sid
        return sid

    def get_candles(self, symbol, interval="FifteenMinutes", count=50):
        """Fetch OHLCV bars for `symbol` from Questrade.

        Args:
            symbol: "AAPL", "SHOP.TO", etc.
            interval: one of OneMinute, TwoMinutes, ThreeMinutes, FourMinutes,
                      FiveMinutes, TenMinutes, FifteenMinutes, TwentyMinutes,
                      HalfHour, OneHour, TwoHours, FourHours, OneDay, etc.
            count: number of bars to return (most recent N)

        Returns a list of dicts with keys: start, end, open, high, low, close, volume.
        """
        from datetime import datetime, timezone, timedelta
        sid = self.resolve_symbol_id(symbol)
        # Ask for a generous window; we'll trim to `count` bars client-side
        per_bar_minutes = {
            "OneMinute": 1, "TwoMinutes": 2, "ThreeMinutes": 3, "FourMinutes": 4,
            "FiveMinutes": 5, "TenMinutes": 10, "FifteenMinutes": 15,
            "TwentyMinutes": 20, "HalfHour": 30, "OneHour": 60,
            "TwoHours": 120, "FourHours": 240, "OneDay": 1440,
        }.get(interval, 15)
        # Pad window by 5x to account for weekends/off-hours with no bars
        window_minutes = count * per_bar_minutes * 5
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        # Questrade requires ISO-8601 with timezone offset
        params = {
            "startTime": start.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "endTime": end.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "interval": interval,
        }
        raw = self._get(f"/v1/markets/candles/{sid}", params=params)
        candles = raw.get("candles", [])
        # Return the most recent `count` bars
        return candles[-count:] if len(candles) > count else candles

    def get_quote(self, symbol):
        """Returns a compact quote dict for the given symbol."""
        sid = self.resolve_symbol_id(symbol)
        raw = self._get("/v1/markets/quotes", params={"ids": str(sid)})
        quotes = raw.get("quotes", [])
        if not quotes:
            raise QuestradeExecutorError(f"no quote for {symbol}")
        q = quotes[0]
        return {
            "symbol": q.get("symbol", symbol),
            "last": float(q.get("lastTradePrice") or 0),
            "bid": float(q.get("bidPrice") or 0),
            "ask": float(q.get("askPrice") or 0),
            "open": float(q.get("openPrice") or 0),
            "high": float(q.get("highPrice") or 0),
            "low": float(q.get("lowPrice") or 0),
            "volume": int(q.get("volume") or 0),
            "currency": "CAD" if symbol.upper().endswith(".TO") else "USD",
        }

    # ===== Order placement =====

    def place_market_order(self, symbol, side, qty, validate=True):
        """Place a market order for `qty` shares of `symbol` on the primary account.

        When `validate=True` (DEFAULT): does NOT post to Questrade. Returns
        {dry_run: True, qty, price, total, would_execute, currency}.

        When `validate=False`: POSTs a real Market/Day order. Returns
        {dry_run: False, order_id, state, filled_qty, fill_price, ...}.

        `side` is "Buy" or "Sell" (case-insensitive; normalized internally).
        """
        if qty <= 0:
            raise QuestradeExecutorError(f"qty must be > 0 (got {qty})")
        side_norm = side.capitalize()
        if side_norm not in ("Buy", "Sell"):
            raise QuestradeExecutorError(f"side must be Buy or Sell (got {side})")

        quote = self.get_quote(symbol)
        price = quote["ask"] if side_norm == "Buy" else quote["bid"]
        if price <= 0:
            price = quote["last"]
        if price <= 0:
            raise QuestradeExecutorError(f"no valid price for {symbol}")
        total = qty * price
        currency = quote["currency"]

        if validate:
            balance = self.get_balance().get(currency, {})
            buying_power = balance.get("buying_power") or balance.get("cash", 0)
            return {
                "dry_run": True,
                "symbol": symbol,
                "side": side_norm,
                "qty": qty,
                "price": price,
                "total": total,
                "currency": currency,
                "buying_power": buying_power,
                "would_execute": total <= buying_power if buying_power else False,
            }

        # Live path
        sid = self.resolve_symbol_id(symbol)
        acct = self.get_account_id()
        order = {
            "accountNumber": acct,
            "symbolId": sid,
            "quantity": int(qty),
            "orderType": "Market",
            "timeInForce": "Day",
            "action": side_norm,
            "primaryRoute": "AUTO",
            "secondaryRoute": "AUTO",
        }
        raw = self._post(f"/v1/accounts/{acct}/orders", body=order)
        orders = raw.get("orders", [])
        order_resp = orders[0] if orders else raw
        return {
            "dry_run": False,
            "symbol": symbol,
            "side": side_norm,
            "qty": qty,
            "price": price,
            "total": total,
            "currency": currency,
            "order_id": order_resp.get("id"),
            "state": order_resp.get("state"),
            "filled_qty": float(order_resp.get("totalQuantity", 0) or 0),
            "fill_price": float(order_resp.get("avgExecPrice") or price),
            "raw": order_resp,
        }

    # ===== High-level helper matching KrakenExecutor interface =====

    def execute_manual_trade(self, symbol, side, qty):
        """Execute a manual buy/sell from the stock concierge.

        Enforces the double-gate interlock:
          - MANUAL_STOCK_TRADING_ENABLED=true (gate 2)
          - QUESTRADE_ALLOW_TRADING=true (gate 1)

        If either gate is false, runs in validate-only mode (no real POST)
        and returns a dry-run dict explaining what would have happened.
        """
        gate2 = os.environ.get("MANUAL_STOCK_TRADING_ENABLED", "false").lower() == "true"
        gate1 = os.environ.get("QUESTRADE_ALLOW_TRADING", "false").lower() == "true"

        if not gate2:
            raise QuestradeExecutorError("MANUAL_STOCK_TRADING_ENABLED=false")

        # Validate-only unless both gates are open
        validate = not gate1
        return self.place_market_order(symbol, side, qty, validate=validate)
