"""
Data fetcher — uses Questrade for real-time quotes and computes
all technical indicators locally from Supabase snapshots.
News impact via Alpha Vantage + Finnhub (both free).

$0/mo total — no paid data feeds.
"""
import math
import time
from datetime import datetime, timedelta, timezone

import httpx
import config


class QuoteFetcher:
    """Fetches real-time quotes from Questrade and computes all indicators
    from stored snapshots in Supabase."""

    def __init__(self, questrade_client):
        self.qt = questrade_client
        self._client = httpx.Client(timeout=15)
        self._snapshot_cache = {}  # symbol -> (timestamp, snapshots)
        self._CACHE_TTL = 60  # cache snapshots for 60 seconds

    def _supabase_get(self, path):
        try:
            resp = self._client.get(
                f"{config.SUPABASE_URL}/rest/v1/{path}",
                headers={
                    "apikey": config.SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
                    "Prefer": "return=representation",
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return []

    def _get_snapshots(self, symbol, limit=100):
        """Get historical snapshots from Supabase with caching."""
        now = time.time()
        if symbol in self._snapshot_cache:
            cached_time, cached_data = self._snapshot_cache[symbol]
            if now - cached_time < self._CACHE_TTL:
                return cached_data

        snapshots = self._supabase_get(
            f"market_snapshots?symbol=eq.{symbol}&select=price,volume,open_price,day_change_pct,snapshot_at"
            f"&order=snapshot_at.desc&limit={limit}"
        )
        self._snapshot_cache[symbol] = (now, snapshots)
        return snapshots

    def _store_snapshot(self, symbol, quote):
        """Store a quote as a snapshot in Supabase."""
        try:
            self._client.post(
                f"{config.SUPABASE_URL}/rest/v1/market_snapshots",
                headers={
                    "apikey": config.SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json={
                    "symbol": symbol,
                    "price": quote.get("lastTradePrice") or 0,
                    "open_price": quote.get("openPrice"),
                    "volume": quote.get("volume") or 0,
                    "day_change_pct": round(
                        ((quote["lastTradePrice"] - quote["openPrice"]) / quote["openPrice"] * 100), 2
                    ) if quote.get("openPrice") and quote["openPrice"] > 0 else None,
                    "bid": quote.get("bidPrice"),
                    "ask": quote.get("askPrice"),
                },
            )
        except Exception:
            pass

    # ── Indicator Calculations ──

    @staticmethod
    def _compute_sma(prices, period):
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    @staticmethod
    def _compute_ema(prices, period):
        if len(prices) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def _compute_rsi(prices, period=14):
        if len(prices) < period + 1:
            return None
        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = changes[-period:]
        gains = [c for c in recent if c > 0]
        losses = [-c for c in recent if c < 0]
        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0.001
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    @staticmethod
    def _compute_macd(prices):
        if len(prices) < 26:
            return None, None
        m12 = 2 / 13
        m26 = 2 / 27
        e12 = sum(prices[:12]) / 12
        e26 = sum(prices[:26]) / 26
        macd_series = []
        for i, p in enumerate(prices):
            if i < 12:
                continue
            e12 = (p - e12) * m12 + e12
            if i < 26:
                continue
            e26 = (p - e26) * m26 + e26
            macd_series.append(e12 - e26)
        macd_line = macd_series[-1] if macd_series else None
        signal = None
        if len(macd_series) >= 9:
            m9 = 2 / 10
            signal = sum(macd_series[:9]) / 9
            for v in macd_series[9:]:
                signal = (v - signal) * m9 + signal
        return macd_line, signal

    @staticmethod
    def _compute_bollinger(prices, period=20, std_dev=2):
        if len(prices) < period:
            return None, None, None
        window = prices[-period:]
        middle = sum(window) / period
        variance = sum((p - middle) ** 2 for p in window) / period
        std = variance ** 0.5
        return middle + std_dev * std, middle, middle - std_dev * std

    def _compute_vwap(self, snapshots):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_snaps = [s for s in snapshots if s["snapshot_at"][:10] == today]
        if not today_snaps:
            return None
        total_pv = 0
        total_vol = 0
        for s in today_snaps:
            price = float(s["price"])
            vol = int(s["volume"]) if s.get("volume") else 0
            if vol > 0:
                total_pv += price * vol
                total_vol += vol
        return total_pv / total_vol if total_vol > 0 else None

    def _get_daily_closes(self, snapshots):
        daily = {}
        for s in snapshots:
            date = s["snapshot_at"][:10]
            if date not in daily:
                daily[date] = float(s["price"])
        return [daily[d] for d in sorted(daily.keys())]

    # ── Main Fetch ──

    def get_all_indicators(self, symbol):
        """Fetch live quote from Questrade + compute all indicators from snapshots.
        Returns indicator dict compatible with flag_scorer, or None on failure."""
        try:
            # Get live quote from Questrade
            quote = self.qt.get_quote(symbol)
            if not quote or not quote.get("lastTradePrice"):
                return None

            price = float(quote["lastTradePrice"])
            open_price = float(quote.get("openPrice") or price)
            volume = float(quote.get("volume") or 0)

            # Store snapshot for future indicator calculations
            self._store_snapshot(symbol, quote)

            # Get historical snapshots for indicator computation
            snapshots = self._get_snapshots(symbol, 200)
            closes = self._get_daily_closes(snapshots)

            # Volume average from snapshots
            volumes = [int(s["volume"]) for s in snapshots if s.get("volume")]
            volume_avg_20 = sum(volumes[1:21]) / min(len(volumes[1:21]), 20) if len(volumes) > 1 else 0

            # Compute all indicators
            sma5 = self._compute_sma(closes, 5)
            sma20 = self._compute_sma(closes, 20)
            sma50 = self._compute_sma(closes, 50)

            # Previous values for crossover detection
            prev_closes = closes[:-1] if len(closes) > 1 else closes
            sma5_prev = self._compute_sma(prev_closes, 5)
            sma20_prev = self._compute_sma(prev_closes, 20)

            rsi = self._compute_rsi(closes)
            rsi_prev = self._compute_rsi(prev_closes)

            macd, macd_signal = self._compute_macd(closes)
            macd_prev, macd_signal_prev = self._compute_macd(prev_closes)

            bb_upper, bb_middle, bb_lower = self._compute_bollinger(closes)
            bb_upper_prev, _, bb_lower_prev = self._compute_bollinger(prev_closes)

            vwap = self._compute_vwap(snapshots)

            return {
                "price": price,
                "price_prev": float(snapshots[1]["price"]) if len(snapshots) > 1 else price,
                "open": open_price,
                "volume": volume,
                "volume_avg_20": volume_avg_20,
                "sma5": sma5,
                "sma5_prev": sma5_prev,
                "sma20": sma20,
                "sma20_prev": sma20_prev,
                "sma50_daily": sma50,
                "rsi": rsi,
                "rsi_prev": rsi_prev,
                "macd": macd,
                "macd_signal": macd_signal,
                "macd_prev": macd_prev,
                "macd_signal_prev": macd_signal_prev,
                "bband_upper": bb_upper,
                "bband_lower": bb_lower,
                "bband_upper_prev": bb_upper_prev,
                "bband_lower_prev": bb_lower_prev,
                "vwap": vwap,
            }
        except Exception as e:
            print(f"[QuoteFetcher] Error fetching {symbol}: {e}")
            return None


class NewsFetcher:
    """Alpha Vantage + Finnhub news scoring (both free)."""
    AV_BASE = "https://www.alphavantage.co/query"
    FH_BASE = "https://finnhub.io/api/v1"

    def __init__(self):
        self.av_key = config.ALPHA_VANTAGE_KEY
        self.fh_key = config.FINNHUB_KEY
        self._client = httpx.Client(timeout=10)

    def _av_news(self, symbol, hours_back):
        time_from = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y%m%dT%H%M")
        try:
            r = self._client.get(self.AV_BASE, params={
                "function": "NEWS_SENTIMENT",
                "tickers": symbol,
                "time_from": time_from,
                "sort": "RELEVANCE",
                "limit": 10,
                "apikey": self.av_key,
            })
            articles = r.json().get("feed", [])
            if not articles:
                return None, 0.0

            max_score = 0.0
            direction = None
            for article in articles:
                for ts in article.get("ticker_sentiment", []):
                    if ts["ticker"].upper() == symbol.upper().replace(".TO", ""):
                        score = abs(float(ts.get("ticker_sentiment_score", 0)))
                        if score > max_score:
                            max_score = score
                            direction = ts.get("ticker_sentiment_label")
            return direction, max_score
        except Exception as e:
            print(f"[AV News] {symbol}: {e}")
            return None, 0.0

    def _fh_news_count(self, symbol):
        try:
            clean_sym = symbol.replace(".TO", "")
            today = datetime.utcnow().strftime("%Y-%m-%d")
            r = self._client.get(f"{self.FH_BASE}/company-news", params={
                "symbol": clean_sym,
                "from": today,
                "to": today,
                "token": self.fh_key,
            })
            result = r.json()
            return len(result) if isinstance(result, list) else 0
        except Exception as e:
            print(f"[Finnhub] {symbol}: {e}")
            return 0

    def get_impact(self, symbol, direction_filter=None):
        direction, av_score = self._av_news(symbol, config.NEWS_LOOKBACK_HOURS)
        fh_count = self._fh_news_count(symbol)

        if direction_filter and direction:
            if direction_filter == "bullish" and "Bearish" in direction:
                av_score = 0.0
            elif direction_filter == "bearish" and "Bullish" in direction:
                av_score = 0.0

        if av_score >= config.NEWS_HIGH_THRESHOLD:
            return "HIGH"
        if fh_count >= 3 and av_score >= 0.15:
            return "HIGH"
        if av_score >= 0.15 or fh_count >= 5:
            return "MEDIUM"
        return "LOW"
