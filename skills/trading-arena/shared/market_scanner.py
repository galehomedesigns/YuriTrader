"""Market data scanner — fetches prices for stocks and crypto."""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (TWELVEDATA_KEY, FINNHUB_KEY, KRAKEN_KEY,
                    STOCK_SYMBOLS, CRYPTO_SYMBOLS, KRAKEN_PAIRS)
from shared.indicators import *


@dataclass
class AssetData:
    """Market data + indicators for a single asset."""
    symbol: str
    price: float = 0
    open: float = 0
    high: float = 0
    low: float = 0
    volume: float = 0
    day_change_pct: float = 0
    closes: List[float] = field(default_factory=list)
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    opens: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)
    # Computed indicators (filled by compute_indicators)
    rsi_14: Optional[float] = None
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    macd_bullish: bool = False
    ema_8: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_bandwidth: Optional[float] = None
    vwap_val: Optional[float] = None
    obv_val: Optional[float] = None
    atr_14: Optional[float] = None
    adx_14: Optional[float] = None
    rvol: Optional[float] = None
    asset_type: str = "stock"  # "stock" or "crypto"
    # New per 2026-04-12 (TAY framework upgrade): S/R + candlestick + ATR stops
    sr_levels: list = field(default_factory=list)  # [(price, count, type), ...]
    candlestick_pattern: Optional[str] = None  # hammer/bullish_engulfing/etc
    atr_stop_long: Optional[float] = None
    atr_target_long: Optional[float] = None

    def compute_indicators(self):
        """Calculate all indicators from price history."""
        c = self.closes
        if len(c) < 2:
            return
        self.rsi_14 = rsi(c, 14)
        self.macd_line, self.macd_signal, self.macd_histogram = macd(c)
        self.macd_bullish = (self.macd_histogram or 0) > 0
        self.ema_8 = ema(c, 8)
        self.ema_21 = ema(c, 21)
        self.ema_50 = ema(c, 50)
        self.ema_200 = ema(c, 200)
        self.sma_20 = sma(c, 20)
        self.sma_50 = sma(c, 50)
        self.bb_upper, self.bb_middle, self.bb_lower, self.bb_bandwidth = bollinger_bands(c)
        if self.volumes:
            self.vwap_val = vwap(c, self.volumes)
            self.obv_val = obv(c, self.volumes)
            self.rvol = relative_volume(self.volumes)
        if self.highs and self.lows:
            self.atr_14 = atr(self.highs, self.lows, c)
            self.adx_14 = adx(self.highs, self.lows, c)
            # S/R levels from swing points
            self.sr_levels = find_sr_levels(self.highs, self.lows, lookback=3, cluster_pct=0.005)
        # Candlestick pattern (requires opens)
        if self.opens and self.highs and self.lows and len(self.closes) >= 2:
            self.candlestick_pattern = detect_candlestick_pattern(
                self.opens, self.highs, self.lows, self.closes
            )
        # ATR-based stop/target prices for the bots that use them
        if self.atr_14 and self.price > 0:
            self.atr_stop_long = atr_stop_loss(self.price, self.atr_14, "long", multiplier=1.0)
            self.atr_target_long = atr_take_profit(self.price, self.atr_14, "long", multiplier=2.0)


def _http_get(url, headers=None, timeout=10):
    """Simple HTTP GET with a browser-ish default User-Agent.

    TwelveData (and others behind Cloudflare) return HTTP 403 / error 1010
    to urllib's default "Python-urllib/x.y" UA. Set a generic UA by default;
    callers can still override via the headers arg.
    """
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; YuriTrader/1.0)"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  HTTP error ({url[:60]}): {e}", file=sys.stderr)
        return None


def fetch_stock_data(symbols: List[str]) -> Dict[str, AssetData]:
    """Fetch stock data from Finnhub (quotes) + TwelveData (history)."""
    result = {}

    for sym in symbols:
        data = AssetData(symbol=sym, asset_type="stock")

        # Finnhub quote
        if FINNHUB_KEY:
            quote = _http_get(f"https://finnhub.io/api/v1/quote?symbol={sym}&token={FINNHUB_KEY}")
            if quote and quote.get("c"):
                data.price = quote["c"]
                data.open = quote.get("o", 0)
                data.high = quote.get("h", 0)
                data.low = quote.get("l", 0)
                data.day_change_pct = quote.get("dp", 0)

        # TwelveData time series (50 bars for indicators)
        if TWELVEDATA_KEY and data.price:
            ts = _http_get(
                f"https://api.twelvedata.com/time_series?symbol={sym}&interval=15min"
                f"&outputsize=50&apikey={TWELVEDATA_KEY}"
            )
            if ts and ts.get("values"):
                bars = list(reversed(ts["values"]))  # oldest first
                data.closes = [float(b["close"]) for b in bars]
                data.highs = [float(b["high"]) for b in bars]
                data.lows = [float(b["low"]) for b in bars]
                data.opens = [float(b["open"]) for b in bars]
                data.volumes = [float(b.get("volume", 0)) for b in bars]
                data.compute_indicators()

        if data.price > 0:
            result[sym] = data

        time.sleep(0.2)  # Rate limit

    return result


def fetch_stock_data_questrade(symbols: List[str], interval="FifteenMinutes") -> Dict[str, AssetData]:
    """Fetch stock data from Questrade (both quotes and candles).

    This is the preferred stock fetcher when TwelveData isn't available and
    Finnhub free tier doesn't include /stock/candle. Requires a valid
    Questrade refresh token in .env (rotates automatically).

    Supports both US and Canadian (.TO) symbols natively.
    """
    # Lazy import to avoid circular imports
    from shared.questrade_executor import QuestradeExecutor, QuestradeExecutorError

    result = {}
    try:
        executor = QuestradeExecutor()
    except Exception as e:
        print(f"  Questrade executor init failed: {e}", file=sys.stderr)
        return result

    for sym in symbols:
        data = AssetData(symbol=sym, asset_type="stock")
        try:
            quote = executor.get_quote(sym)
            data.price = quote["last"] or quote["bid"] or 0
            data.open = quote.get("open", 0)
            data.high = quote.get("high", 0)
            data.low = quote.get("low", 0)
            data.volume = quote.get("volume", 0)
            if data.open:
                data.day_change_pct = (data.price - data.open) / data.open * 100
        except QuestradeExecutorError as e:
            print(f"  [{sym}] quote failed: {e}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"  [{sym}] quote error: {e}", file=sys.stderr)
            continue

        if data.price <= 0:
            continue

        # Fetch OHLC history
        try:
            candles = executor.get_candles(sym, interval=interval, count=50)
        except Exception as e:
            print(f"  [{sym}] candles failed: {e}", file=sys.stderr)
            candles = []

        if candles:
            data.opens = [float(c.get("open", 0) or 0) for c in candles]
            data.highs = [float(c.get("high", 0) or 0) for c in candles]
            data.lows = [float(c.get("low", 0) or 0) for c in candles]
            data.closes = [float(c.get("close", 0) or 0) for c in candles]
            data.volumes = [float(c.get("volume", 0) or 0) for c in candles]
            data.compute_indicators()

        result[sym] = data
        time.sleep(0.15)  # gentle rate-limit

    return result


def fetch_crypto_data(symbols: List[str],
                      pair_map: Optional[Dict[str, str]] = None) -> Dict[str, AssetData]:
    """Fetch crypto data from Kraken public API.

    pair_map: optional {symbol -> kraken_pair} override sourced from the dynamic
    watchlist, so momentum movers OUTSIDE the hardcoded config.KRAKEN_PAIRS 6 are
    actually fetched instead of silently dropped. Falls back to config.KRAKEN_PAIRS
    for the legacy symbols, so existing callers (passing no map) are unaffected.
    """
    pair_map = pair_map or {}
    result = {}

    for sym in symbols:
        pair = pair_map.get(sym) or KRAKEN_PAIRS.get(sym)
        if not pair:
            continue
        data = AssetData(symbol=sym, asset_type="crypto")

        # Kraken ticker
        ticker = _http_get(f"https://api.kraken.com/0/public/Ticker?pair={pair}")
        if ticker and ticker.get("result"):
            info = list(ticker["result"].values())[0]
            data.price = float(info["c"][0])  # Last trade price
            data.open = float(info["o"])
            data.high = float(info["h"][1])   # 24h high
            data.low = float(info["l"][1])    # 24h low
            data.volume = float(info["v"][1]) # 24h volume

        # Kraken OHLC (15-min bars)
        ohlc = _http_get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=15")
        if ohlc and ohlc.get("result"):
            bars = list(ohlc["result"].values())[0]
            if isinstance(bars, list) and len(bars) > 10:
                bars = bars[-50:]  # Last 50 bars
                data.closes = [float(b[4]) for b in bars]
                data.highs = [float(b[2]) for b in bars]
                data.lows = [float(b[3]) for b in bars]
                data.opens = [float(b[1]) for b in bars]
                data.volumes = [float(b[6]) for b in bars]
                if data.closes:
                    data.price = data.closes[-1]
                    data.day_change_pct = ((data.price - data.open) / data.open * 100) if data.open else 0
                data.compute_indicators()

        if data.price > 0:
            result[sym] = data

        time.sleep(0.3)

    return result


def fetch_dynamic_watchlist() -> tuple:
    """Read the most recent dynamic watchlist from Supabase.
    Returns (stock_symbols, crypto_symbols). Falls back to config if empty.
    """
    from config import SUPABASE_URL as SB_URL, SUPABASE_KEY as SB_KEY
    if not SB_URL or not SB_KEY:
        return (STOCK_SYMBOLS, CRYPTO_SYMBOLS)

    try:
        url = f"{SB_URL}/rest/v1/arena_watchlist?order=created_at.desc&limit=1"
        req = urllib.request.Request(url, headers={
            "apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode())
        if not rows:
            return (STOCK_SYMBOLS, CRYPTO_SYMBOLS)

        details = rows[0].get("details")
        if isinstance(details, str):
            details = json.loads(details)
        if not details:
            return (STOCK_SYMBOLS, CRYPTO_SYMBOLS)

        stocks = [item["symbol"] for item in details if item.get("asset_type") == "stock"]
        crypto = [item["symbol"] for item in details if item.get("asset_type") == "crypto"]
        return (stocks or STOCK_SYMBOLS, crypto or CRYPTO_SYMBOLS)
    except Exception as e:
        print(f"  Watchlist fetch error: {e}", file=sys.stderr)
        return (STOCK_SYMBOLS, CRYPTO_SYMBOLS)


def _latest_watchlist_crypto_pairs() -> Dict[str, str]:
    """{symbol -> kraken_pair} for crypto items in the latest arena_watchlist
    row that carry a `kraken_pair` (written by the widened dynamic_watchlist
    scanner). Returns {} on any failure — fully backward compatible: legacy
    rows without `kraken_pair` resolve via config.KRAKEN_PAIRS downstream."""
    from config import SUPABASE_URL as SB_URL, SUPABASE_KEY as SB_KEY
    if not SB_URL or not SB_KEY:
        return {}
    try:
        url = f"{SB_URL}/rest/v1/arena_watchlist?order=created_at.desc&limit=1"
        req = urllib.request.Request(url, headers={
            "apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode())
        if not rows:
            return {}
        details = rows[0].get("details")
        if isinstance(details, str):
            details = json.loads(details)
        return {
            it["symbol"]: it["kraken_pair"]
            for it in (details or [])
            if it.get("asset_type") == "crypto" and it.get("kraken_pair")
        }
    except Exception as e:
        print(f"  watchlist pair-map fetch error: {e}", file=sys.stderr)
        return {}


def fetch_all(crypto_only: bool = False) -> Dict[str, AssetData]:
    """Fetch all stock + crypto data using the dynamic watchlist.

    crypto_only=True skips the stock fetch entirely (saves finnhub/twelvedata
    quota when scanning 24/7 outside US market hours).
    """
    stock_syms, crypto_syms = fetch_dynamic_watchlist()
    crypto_pairs = _latest_watchlist_crypto_pairs()
    data = {}
    if not crypto_only:
        print(f"  Scanning {len(stock_syms)} stocks...", file=sys.stderr, end=" ")
        stocks = fetch_stock_data(stock_syms)
        data.update(stocks)
        print(f"{len(stocks)} found", file=sys.stderr)

    print(f"  Scanning {len(crypto_syms)} crypto...", file=sys.stderr, end=" ")
    crypto = fetch_crypto_data(crypto_syms, crypto_pairs)
    data.update(crypto)
    print(f"{len(crypto)} found", file=sys.stderr)

    return data
