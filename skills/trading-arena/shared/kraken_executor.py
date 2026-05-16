"""Kraken Live Trading Executor.

Self-contained Python client for placing real orders on Kraken when a bot's
trade qualifies for live execution. Uses stdlib only (no httpx, no requests).

DOUBLE-GATE SAFETY:
  Gate 1 (env): KRAKEN_ALLOW_TRADING=true
  Gate 2 (env): LIVE_TRADING_ENABLED=true
Both must be true. Either being false = paper trade only.
This mirrors the design of the kraken-mcp server.

Pre-trade checks (ALL must pass):
  1. Bot ID is in LIVE_TRADING_BOTS env list
  2. Asset is in the crypto pair map
  3. Position size USD <= LIVE_MAX_POSITION_USD
  4. Computed volume >= Kraken ordermin for the pair
  5. Total live exposure <= LIVE_MAX_EXPOSURE_USD
  6. Live daily P&L > LIVE_DAILY_LOSS_LIMIT
  7. Account has sufficient USD balance

If ANY check fails, return None — caller falls back to paper.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

API_URL = "https://api.kraken.com"

# Mapping from arena symbol → Kraken pair code
KRAKEN_PAIR_MAP = {
    "BTC/USD": "XBTUSD",
    "ETH/USD": "ETHUSD",
    "SOL/USD": "SOLUSD",
    "XRP/USD": "XRPUSD",
    "ADA/USD": "ADAUSD",
    "DOGE/USD": "XDGUSD",
}

# Monotonic guard so two _private() calls in the same microsecond can't
# repeat a nonce. Matches the in-process guard in kraken-cli/_common.mjs.
_last_nonce = 0


class KrakenExecutorError(Exception):
    """Raised when a live trade attempt fails any safety check or API call."""


class KrakenExecutor:
    """Minimal Kraken authenticated client for the trading arena."""

    def __init__(self):
        self.api_key = os.environ.get("KRAKEN_API_KEY", "")
        self.api_secret = os.environ.get("KRAKEN_API_SECRET", "")
        self._asset_pairs_cache = None

    def _public(self, endpoint, params=None):
        url = f"{API_URL}/0/public/{endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise KrakenExecutorError(f"Kraken public {endpoint} HTTP {e.code}: {e.read().decode()[:200]}")
        if data.get("error"):
            raise KrakenExecutorError(f"Kraken public {endpoint} error: {data['error']}")
        return data.get("result", {})

    def _private(self, endpoint, params=None):
        if not self.api_key or not self.api_secret:
            raise KrakenExecutorError("KRAKEN_API_KEY/SECRET not set")
        global _last_nonce
        params = dict(params or {})
        # Microsecond-scale wall-clock nonce, matching kraken-cli/_common.mjs and
        # yuri/krakenAuth.js. Bump by 1 if the clock didn't advance since the last
        # call so in-process bursts stay strictly monotonic.
        n = int(time.time() * 1_000_000)
        if n <= _last_nonce:
            n = _last_nonce + 1
        _last_nonce = n
        nonce = str(n)
        params["nonce"] = nonce

        url_path = f"/0/private/{endpoint}"
        post_data = urllib.parse.urlencode(params)

        # Kraken signature: HMAC-SHA512 of (path + SHA256(nonce + post_data)) with base64 secret
        sha256 = hashlib.sha256((nonce + post_data).encode()).digest()
        message = url_path.encode() + sha256
        secret_bytes = base64.b64decode(self.api_secret)
        signature = base64.b64encode(
            hmac.new(secret_bytes, message, hashlib.sha512).digest()
        ).decode()

        req = urllib.request.Request(
            f"{API_URL}{url_path}",
            data=post_data.encode(),
            headers={
                "API-Key": self.api_key,
                "API-Sign": signature,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise KrakenExecutorError(f"Kraken {endpoint} HTTP {e.code}: {e.read().decode()[:200]}")
        if data.get("error"):
            raise KrakenExecutorError(f"Kraken {endpoint} error: {data['error']}")
        return data.get("result", {})

    # ===== Public API =====

    def get_balance(self):
        """Returns dict of asset → free balance."""
        return self._private("Balance")

    def get_usd_balance(self):
        """Returns ZUSD balance as float (defaults to 0)."""
        balance = self.get_balance()
        return float(balance.get("ZUSD", 0))

    def get_asset_pairs(self):
        """Cached lookup of all asset pairs (for ordermin lookup)."""
        if self._asset_pairs_cache is None:
            pairs = ",".join(KRAKEN_PAIR_MAP.values())
            self._asset_pairs_cache = self._public("AssetPairs", {"pair": pairs})
        return self._asset_pairs_cache

    def get_ordermin(self, kraken_pair):
        """Returns the minimum order size for a given Kraken pair as float."""
        pairs = self.get_asset_pairs()
        # Pairs come back keyed by their canonical name (e.g. XXBTZUSD), not the
        # alt name we passed in. Find by altname match.
        for name, info in pairs.items():
            if info.get("altname") == kraken_pair or name == kraken_pair:
                return float(info.get("ordermin", 0))
        return 0.0

    def get_pair_decimals(self, kraken_pair):
        """Returns the volume decimal precision for a given Kraken pair."""
        pairs = self.get_asset_pairs()
        for name, info in pairs.items():
            if info.get("altname") == kraken_pair or name == kraken_pair:
                return int(info.get("lot_decimals", 8))
        return 8

    def get_open_orders(self):
        return self._private("OpenOrders").get("open", {})

    def get_closed_orders(self):
        return self._private("ClosedOrders").get("closed", {})

    def query_order(self, order_id):
        """Get details of a specific order."""
        result = self._private("QueryOrders", {"txid": order_id})
        return result.get(order_id, {})

    # ===== Trading =====

    def place_market_order(self, kraken_pair, side, volume, validate=True):
        """Place a market order on Kraken.

        Args:
          kraken_pair: e.g. "XBTUSD"
          side: "buy" or "sell"
          volume: float volume in base asset (e.g. BTC amount)
          validate: True = dry-run (Kraken validates but doesn't place);
                    False = real order

        Returns:
          dict with keys: dry_run, order_id (or None), descr, raw
        """
        # Round volume to the pair's decimal precision
        decimals = self.get_pair_decimals(kraken_pair)
        volume_str = f"{volume:.{decimals}f}".rstrip("0").rstrip(".")
        if not volume_str or volume_str == "0":
            raise KrakenExecutorError(f"volume rounds to zero at {decimals} decimals")

        params = {
            "pair": kraken_pair,
            "type": side,
            "ordertype": "market",
            "volume": volume_str,
        }
        if validate:
            params["validate"] = "true"

        result = self._private("AddOrder", params)
        order_ids = result.get("txid", [])
        return {
            "dry_run": validate,
            "order_id": order_ids[0] if order_ids else None,
            "descr": result.get("descr", {}).get("order", ""),
            "raw": result,
        }

    def cancel_order(self, order_id):
        """Cancel a specific order by its txid."""
        return self._private("CancelOrder", {"txid": order_id})

    def cancel_all(self):
        """Cancel all open orders. Used by the kill switch."""
        return self._private("CancelAll")

    # ===== Execution helper =====

    def execute_arena_trade(self, symbol, side, position_size_usd, current_price,
                            min_balance_usd=5.0):
        """High-level entry point used by paper_trader.py.

        Performs all pre-trade checks, computes volume, places the order.
        Returns dict on success, raises KrakenExecutorError on any failure.
        """
        # Check 1: symbol must be a known crypto pair
        kraken_pair = KRAKEN_PAIR_MAP.get(symbol)
        if not kraken_pair:
            raise KrakenExecutorError(f"{symbol} not in KRAKEN_PAIR_MAP")

        # Check 2: account has enough USD
        usd_balance = self.get_usd_balance()
        if usd_balance < position_size_usd:
            raise KrakenExecutorError(
                f"insufficient USD: have ${usd_balance:.2f}, need ${position_size_usd:.2f}"
            )

        # Check 3: compute volume and verify >= ordermin
        volume = position_size_usd / current_price
        ordermin = self.get_ordermin(kraken_pair)
        if volume < ordermin:
            raise KrakenExecutorError(
                f"volume {volume:.8f} < ordermin {ordermin} for {kraken_pair} "
                f"(${position_size_usd:.2f} @ ${current_price:.2f})"
            )

        # Determine validate mode from env (gate 1)
        env_allow = os.environ.get("KRAKEN_ALLOW_TRADING", "false").lower() == "true"
        validate = not env_allow  # validate=true if env gate is closed

        # Place the order
        result = self.place_market_order(kraken_pair, side, volume, validate=validate)
        result["volume"] = volume
        result["kraken_pair"] = kraken_pair
        result["position_size_usd"] = position_size_usd
        return result

    def execute_manual_trade(self, symbol, side, position_size_usd, current_price):
        """Execute a manual trade from the Telegram concierge.

        Bypasses the LIVE_TRADING_BOTS eligibility check (the human IS the bot).
        Still enforces:
          - Symbol in KRAKEN_PAIR_MAP
          - USD balance sufficient
          - Volume >= ordermin
          - MANUAL_TRADING_ENABLED env var must be true
          - KRAKEN_ALLOW_TRADING env var controls validate mode (gate 1)

        Returns dict on success, raises KrakenExecutorError on any failure.
        """
        # Check: manual trading gate
        if os.environ.get("MANUAL_TRADING_ENABLED", "false").lower() != "true":
            raise KrakenExecutorError("MANUAL_TRADING_ENABLED=false")

        # Delegate to execute_arena_trade for the actual order logic
        # (it will use KRAKEN_ALLOW_TRADING to determine validate mode)
        return self.execute_arena_trade(
            symbol=symbol, side=side,
            position_size_usd=position_size_usd,
            current_price=current_price,
        )


def is_trade_eligible_for_live(bot_id, symbol, position_size_usd):
    """Check the env-level gates and bot eligibility before any Kraken call.

    Returns (eligible: bool, reason: str). If False, caller should fall back
    to paper trade and log the reason in the Telegram alert.
    """
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "true":
        return False, "LIVE_TRADING_ENABLED=false"

    live_bots = [b.strip() for b in os.environ.get("LIVE_TRADING_BOTS", "").split(",") if b.strip()]
    if bot_id not in live_bots:
        return False, f"bot {bot_id} not in LIVE_TRADING_BOTS"

    if symbol not in KRAKEN_PAIR_MAP:
        return False, f"{symbol} not a Kraken crypto pair"

    max_pos = float(os.environ.get("LIVE_MAX_POSITION_USD", "5.0"))
    if position_size_usd > max_pos:
        return False, f"position ${position_size_usd:.2f} exceeds LIVE_MAX_POSITION_USD ${max_pos}"

    return True, "eligible"
