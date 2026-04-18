"""
Questrade OAuth2 authentication and order execution.
Uses httpx instead of requests. Shared token file with file-locking.
"""
import fcntl
import json
import os
import re
import time

import httpx
import config

TOKEN_FILE = config.TOKEN_FILE


def _api_base_url(token):
    """Route token's assigned api_server through the SSH tunnel port map.
    See /data/skills/questrade/scripts/questrade.py for the shared pattern."""
    server = token["api_server"]
    port_map_env = os.environ.get("QUESTRADE_API_PORT_MAP")
    if not port_map_env:
        return server
    pm = dict(item.split("=") for item in port_map_env.split(","))
    host_match = re.search(r"(api\d+)\.iq\.questrade\.com", server)
    if not host_match or host_match.group(1) not in pm:
        return server
    port = pm[host_match.group(1)]
    return re.sub(r"(api\d+\.iq\.questrade\.com)(:\d+)?", rf"\1:{port}", server)


class QuestradeClient:

    def __init__(self):
        self.access_token = None
        self.api_server = None
        self.account_id = None
        self._symbol_cache = {}
        self._client = httpx.Client(timeout=15)
        self._authenticate()

    def _authenticate(self):
        refresh_token = config.QUESTRADE_REFRESH_TOKEN

        # Read cached token with file lock
        if os.path.exists(TOKEN_FILE):
            try:
                with open(TOKEN_FILE, "r") as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    saved = json.load(f)
                    fcntl.flock(f, fcntl.LOCK_UN)

                # If cached token is still valid, use it
                if saved.get("expires_at", 0) > time.time() + 60:
                    self.access_token = saved["access_token"]
                    self.api_server = _api_base_url(saved)
                    self.account_id = self._fetch_account_id()
                    return

                refresh_token = saved.get("refresh_token", refresh_token)
            except (json.JSONDecodeError, KeyError):
                pass

        if not refresh_token:
            raise ValueError("No Questrade refresh token available")

        r = self._client.get(
            os.environ.get("QUESTRADE_AUTH_URL") or "https://login.questrade.com/oauth2/token",
            params={"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        r.raise_for_status()
        data = r.json()

        self.access_token = data["access_token"]
        self.api_server = _api_base_url(data)

        # Save with file lock
        token_data = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "api_server": data["api_server"],
            "expires_at": time.time() + data.get("expires_in", 1800) - 60,
        }
        with open(TOKEN_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(token_data, f, indent=2)
            fcntl.flock(f, fcntl.LOCK_UN)

        self.account_id = self._fetch_account_id()

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _get(self, path, params=None):
        r = self._client.get(
            f"{self.api_server}v1/{path}",
            headers=self._headers(),
            params=params or {},
        )
        if r.status_code == 401:
            self._authenticate()
            r = self._client.get(
                f"{self.api_server}v1/{path}",
                headers=self._headers(),
                params=params or {},
            )
        r.raise_for_status()
        return r.json()

    def _post(self, path, body):
        r = self._client.post(
            f"{self.api_server}v1/{path}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code == 401:
            self._authenticate()
            r = self._client.post(
                f"{self.api_server}v1/{path}",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=body,
            )
        r.raise_for_status()
        return r.json()

    def _fetch_account_id(self):
        data = self._get("accounts")
        accounts = data.get("accounts", [])
        for acc in accounts:
            if acc.get("type") in ["Cash", "Individual", "Margin"] and acc.get("status") == "Active":
                return acc["number"]
        return accounts[0]["number"] if accounts else None

    def get_balance(self):
        data = self._get(f"accounts/{self.account_id}/balances")
        result = {}
        for bal in data.get("combinedBalances", []):
            cur = bal.get("currency", "")
            result[cur] = {
                "cash": float(bal.get("cash", 0)),
                "market_value": float(bal.get("marketValue", 0)),
                "total_equity": float(bal.get("totalEquity", 0)),
            }
        return result

    def get_symbol_id(self, symbol):
        if symbol in self._symbol_cache:
            return self._symbol_cache[symbol]

        data = self._get("symbols/search", {"prefix": symbol.replace(".TO", "")})
        for s in data.get("symbols", []):
            if s["symbol"].upper() == symbol.upper():
                self._symbol_cache[symbol] = s["symbolId"]
                return s["symbolId"]

        raise ValueError(f"Symbol not found: {symbol}")

    def get_quote(self, symbol):
        symbol_id = self.get_symbol_id(symbol)
        data = self._get("markets/quotes", {"ids": str(symbol_id)})
        quotes = data.get("quotes", [])
        return quotes[0] if quotes else None

    def get_last_price(self, symbol):
        quote = self.get_quote(symbol)
        if not quote:
            return None
        return float(quote.get("lastTradePrice") or quote.get("bidPrice") or 0)

    def get_positions(self):
        data = self._get(f"accounts/{self.account_id}/positions")
        positions = {}
        for p in data.get("positions", []):
            qty = float(p.get("openQuantity", 0))
            if qty <= 0:
                continue
            positions[p["symbol"]] = {
                "symbol": p["symbol"],
                "symbol_id": p["symbolId"],
                "quantity": qty,
                "entry_price": float(p.get("averageEntryPrice", 0)),
                "current_price": float(p.get("currentPrice", 0)),
                "market_value": float(p.get("currentMarketValue", 0)),
                "open_pnl": float(p.get("openPnl", 0)),
            }
        return positions

    def place_market_order(self, symbol, action, quantity):
        symbol_id = self.get_symbol_id(symbol)
        order = {
            "accountNumber": self.account_id,
            "symbolId": symbol_id,
            "quantity": round(quantity, 6),
            "orderType": "Market",
            "timeInForce": "Day",
            "action": action,
            "primaryRoute": "AUTO",
            "secondaryRoute": "AUTO",
        }
        return self._post(f"accounts/{self.account_id}/orders", order)
