"""
Main trading agent — runs continuously.
  - Checks quotes every 10 seconds during market hours
  - Evaluates sell flags on every cycle (instant exits)
  - Evaluates buy flags every 15th cycle (~2.5 min)
  - News check every 60th cycle (~10 min)
  - Force sell at 3:55 PM ET
  - Daily reset at 9:25 AM ET

$0/mo — uses Questrade quotes + local indicator math.

Start: python3 trading_agent.py
"""
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from data_fetcher import QuoteFetcher, NewsFetcher
from flag_scorer import score_buy_flags, score_sell_flags, passes_risk_controls
from questrade_client import QuestradeClient
from web_trader import WebTrader
from notify import (
    send_telegram, log_trade_to_supabase, update_trade_in_supabase,
    audit_log,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("TradingAgent")

HEARTBEAT_FILE = Path("/tmp/trading_agent_heartbeat")
POLL_INTERVAL = 10  # seconds between quote checks
BUY_EVERY_N = 15    # check buy flags every 15th cycle (~2.5 min)
NEWS_EVERY_N = 60   # check news every 60th cycle (~10 min)


class TradingAgent:

    def __init__(self):
        log.info("Initialising trading agent...")
        self.qt = None
        self.fetcher = None
        self.news = NewsFetcher()

        self.positions = {}
        self.daily_pnl = 0.0
        self.paused = False
        self.cycle_count = 0
        self._news_cache = {}  # symbol -> (timestamp, impact)
        self._running = True

        self.web_trader = WebTrader()

        try:
            self.qt = QuestradeClient()
            self.fetcher = QuoteFetcher(self.qt)
            self._recover_open_positions()
        except Exception as e:
            log.warning(f"Questrade API init failed (will retry): {e}")
            send_telegram("Trading Agent: Questrade API auth failed. Quotes may be delayed until reconnected.")

        # Attempt web login (for order execution)
        try:
            if self.web_trader.login():
                log.info("Questrade web login successful — order execution ready")
            else:
                log.warning("Web login failed — will retry before first trade")
        except Exception as e:
            log.warning(f"Web login deferred: {e}")

        log.info(f"Agent ready — watchlist: {config.WATCHLIST}")
        log.info(f"Poll: {POLL_INTERVAL}s | Buy check: ~{POLL_INTERVAL * BUY_EVERY_N}s | "
                 f"News: ~{POLL_INTERVAL * NEWS_EVERY_N}s | Min flags: {config.MIN_FLAGS_TO_BUY}")

    def _recover_open_positions(self):
        import httpx
        try:
            resp = httpx.get(
                f"{config.SUPABASE_URL}/rest/v1/auto_trades",
                headers={
                    "apikey": config.SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
                    "Prefer": "return=representation",
                },
                params={"status": "eq.OPEN", "select": "*"},
                timeout=10,
            )
            if resp.status_code == 200:
                for trade in resp.json():
                    sym = trade["symbol"]
                    self.positions[sym] = {
                        "symbol": sym,
                        "entry_price": float(trade["entry_price"]),
                        "quantity": float(trade["qty"]),
                        "market_value": float(trade["entry_total"]),
                        "bars_held": trade.get("trading_days_held", 0) * 78,
                        "trade_id": trade["id"],
                    }
                    log.info(f"Recovered open position: {sym} @ ${trade['entry_price']}")
        except Exception as e:
            log.error(f"Position recovery failed: {e}")

    def _now_et(self):
        """Current time in ET (UTC-4 EDT)."""
        from datetime import timedelta
        return datetime.now(timezone.utc) - timedelta(hours=4)

    def _market_open(self):
        now = self._now_et()
        if now.weekday() >= 5:
            return False
        h, m = now.hour, now.minute
        return (h == 9 and m >= 30) or (10 <= h <= 15) or (h == 15 and m <= 55)

    def _heartbeat(self):
        HEARTBEAT_FILE.touch()

    def _ensure_questrade(self):
        if not self.qt:
            try:
                self.qt = QuestradeClient()
                self.fetcher = QuoteFetcher(self.qt)
                log.info("Questrade reconnected.")
                return True
            except Exception:
                return False
        return True

    def _get_news_cached(self, symbol, direction_filter):
        """Cache news lookups to avoid API rate limits."""
        cache_key = (symbol, direction_filter)
        now = time.time()
        if cache_key in self._news_cache:
            cached_time, cached_impact = self._news_cache[cache_key]
            if now - cached_time < 600:  # 10 min cache
                return cached_impact
        impact = self.news.get_impact(symbol, direction_filter=direction_filter)
        self._news_cache[cache_key] = (now, impact)
        return impact

    # ── SELL CHECK (every cycle) ──

    def sell_check(self):
        if not self.positions or not self._ensure_questrade():
            return

        for symbol, position in list(self.positions.items()):
            indicators = self.fetcher.get_all_indicators(symbol)
            if not indicators:
                continue

            position["bars_held"] = position.get("bars_held", 0) + 1

            # Only fetch news on news cycles to save API calls
            news_impact = "LOW"
            if self.cycle_count % NEWS_EVERY_N == 0:
                news_impact = self._get_news_cached(symbol, "bearish")

            sell_signal = score_sell_flags(position, indicators, news_impact)

            if not sell_signal:
                gain = (indicators["price"] - position["entry_price"]) / position["entry_price"]
                if self.cycle_count % BUY_EVERY_N == 0:
                    log.info(f"  HOLD {symbol} | P&L: {gain:+.2%} | price: ${indicators['price']:.2f}")
                continue

            flag_num, flag_name = sell_signal
            self._execute_sell(symbol, position, indicators["price"], flag_name)

    # ── BUY CHECK (every Nth cycle) ──

    def buy_check(self):
        if self.paused or not self._ensure_questrade():
            return

        self.positions = self._sync_positions()
        total_exposure = sum(p["market_value"] for p in self.positions.values())

        for symbol in config.WATCHLIST:
            if symbol in self.positions:
                continue

            indicators = self.fetcher.get_all_indicators(symbol)
            if not indicators:
                continue

            # Inverse ETFs benefit from bearish news
            news_dir = "bearish" if symbol in config.INVERSE_SYMBOLS else "bullish"
            news_impact = self._get_news_cached(symbol, news_dir)
            flags = score_buy_flags(indicators, news_impact, symbol=symbol)

            if len(flags) < config.MIN_FLAGS_TO_BUY:
                continue

            flag_names = [f[1] for f in flags]
            log.info(f"  {symbol}: {len(flags)} flag(s) — {flag_names}")

            ok, reason = passes_risk_controls(
                symbol, self.positions, self.daily_pnl, total_exposure
            )
            if not ok:
                log.info(f"  {symbol}: blocked — {reason}")
                audit_log("BUY_BLOCKED", symbol, details={"reason": reason, "flags": flag_names})
                continue

            price = indicators["price"]
            quantity = config.MAX_TRADE_VALUE / price

            try:
                self.web_trader.place_order(symbol, "Buy", quantity)

                trade_id = log_trade_to_supabase({
                    "symbol": symbol,
                    "side": "BUY",
                    "qty": round(quantity, 6),
                    "entry_price": price,
                    "entry_total": round(price * quantity, 4),
                    "status": "OPEN",
                    "buy_flags_met": flag_names,
                    "buy_flags_count": len(flags),
                })

                audit_log("BUY_EXECUTED", symbol, trade_id=trade_id, details={
                    "price": price, "qty": round(quantity, 6),
                    "total": round(price * quantity, 2), "flags": flag_names,
                })

                self.positions[symbol] = {
                    "symbol": symbol,
                    "entry_price": price,
                    "quantity": quantity,
                    "market_value": price * quantity,
                    "bars_held": 0,
                    "trade_id": trade_id,
                }
                total_exposure += price * quantity

                msg = (
                    f"<b>AUTO-TRADE: BUY</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{symbol} — {quantity:.4f} shares @ ${price:.2f}\n"
                    f"Total: ${price * quantity:.2f} | Slot {len(self.positions)}/{config.MAX_POSITIONS}\n"
                    f"Flags ({len(flags)}/{config.MIN_FLAGS_TO_BUY}+): {', '.join(flag_names)}"
                )
                send_telegram(msg)
                log.info(f"  BUY {quantity:.5f} × {symbol} @ ~${price:.2f}")

            except Exception as e:
                log.error(f"  Buy order failed for {symbol}: {e}")
                audit_log("BUY_FAILED", symbol, details={"error": str(e)[:200]})

    def _execute_sell(self, symbol, position, exit_price, reason):
        try:
            self.web_trader.place_order(symbol, "Sell", position["quantity"])

            pnl = (exit_price - position["entry_price"]) * position["quantity"]
            pnl_pct = ((exit_price - position["entry_price"]) / position["entry_price"]) * 100
            self.daily_pnl += pnl

            update_trade_in_supabase(position.get("trade_id"), {
                "status": "CLOSED",
                "exit_price": exit_price,
                "exit_total": round(exit_price * position["quantity"], 4),
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 2),
                "sell_reason": reason,
                "closed_at": datetime.now(timezone.utc).isoformat(),
            })

            audit_log("SELL_EXECUTED", symbol, trade_id=position.get("trade_id"), details={
                "reason": reason, "entry": position["entry_price"],
                "exit": exit_price, "pnl": round(pnl, 4),
            })

            sign = "+" if pnl >= 0 else ""
            msg = (
                f"<b>AUTO-TRADE: SELL</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{symbol} — {position['quantity']:.4f} shares @ ${exit_price:.2f}\n"
                f"P&L: {sign}${pnl:.2f} ({sign}{pnl_pct:.1f}%)\n"
                f"Reason: {reason}\n"
                f"Open: {len(self.positions)-1}/{config.MAX_POSITIONS} | Day P&L: ${self.daily_pnl:.2f}"
            )
            send_telegram(msg)
            log.info(f"  SELL {symbol} @ ${exit_price:.2f} | P&L: {sign}${pnl:.4f} | {reason}")

            del self.positions[symbol]

            if self.daily_pnl <= config.DAILY_LOSS_PAUSE:
                log.warning(f"Daily loss limit hit (${self.daily_pnl:.2f}) — pausing")
                self.paused = True
                send_telegram(
                    f"<b>AUTO-TRADE PAUSED</b>\n"
                    f"Daily loss limit reached: ${self.daily_pnl:.2f}\n"
                    f"Selling remaining positions."
                )
                self._sell_all("Daily loss pause")

        except Exception as e:
            log.error(f"  Sell order failed for {symbol}: {e}")
            audit_log("SELL_FAILED", symbol, trade_id=position.get("trade_id"),
                      details={"error": str(e)[:200]})

    def _sell_all(self, reason="Force sell"):
        for symbol, pos in list(self.positions.items()):
            try:
                quote = self.qt.get_quote(symbol)
                price = float(quote["lastTradePrice"]) if quote else pos["entry_price"]
                self._execute_sell(symbol, pos, price, reason)
            except Exception as e:
                log.error(f"Force-sell failed for {symbol}: {e}")

    def _sync_positions(self):
        try:
            live = self.qt.get_positions()
            for symbol, pos in live.items():
                pos["bars_held"] = self.positions.get(symbol, {}).get("bars_held", 0)
                pos["trade_id"] = self.positions.get(symbol, {}).get("trade_id")
            return live
        except Exception as e:
            log.error(f"Position sync failed: {e}")
            return self.positions

    def _daily_reset(self):
        self.daily_pnl = 0.0
        self.paused = False
        self._news_cache.clear()
        log.info("Daily reset — P&L cleared, agent active")
        send_telegram("Trading agent: daily reset. Agent active.")

    def _startup_check(self):
        errors = []

        if self.qt:
            try:
                bal = self.qt.get_balance()
                log.info(f"Questrade: OK — {bal}")
            except Exception as e:
                errors.append(f"Questrade: {e}")
        else:
            errors.append("Questrade: not connected")

        try:
            impact = self.news.get_impact("AAPL")
            log.info(f"News APIs: OK (impact={impact})")
        except Exception as e:
            errors.append(f"News APIs: {e}")

        if errors:
            msg = "Trading Agent startup errors:\n" + "\n".join(errors)
            log.error(msg)
            send_telegram(f"<b>TRADING AGENT WARNING</b>\n{msg}")
        else:
            send_telegram(
                f"<b>Trading Agent Started</b>\n"
                f"Watchlist: {', '.join(config.WATCHLIST)}\n"
                f"Poll: {POLL_INTERVAL}s | Buy: ~{POLL_INTERVAL * BUY_EVERY_N}s\n"
                f"Min flags: {config.MIN_FLAGS_TO_BUY} | Max: ${config.MAX_TRADE_VALUE}\n"
                f"$0/mo — Questrade quotes + local math"
            )

    def run(self):
        self._startup_check()

        def shutdown(signum, frame):
            log.info("Shutting down...")
            self._running = False
            self.web_trader.close()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)

        log.info(f"Trading agent running — continuous {POLL_INTERVAL}s loop")

        while self._running:
            try:
                self._heartbeat()
                now_et = self._now_et()

                # Daily reset at 9:25 AM ET
                if now_et.hour == 9 and now_et.minute == 25 and now_et.weekday() < 5:
                    if self.cycle_count > 0:  # avoid reset on first boot
                        self._daily_reset()

                # Force sell at 3:55 PM ET
                if now_et.hour == 15 and now_et.minute == 55 and now_et.weekday() < 5:
                    if self.positions:
                        log.info("── END OF DAY — force selling ──")
                        self._sell_all("End of day")

                # Only trade during market hours
                if self._market_open() and not self.paused:
                    # Sell check EVERY cycle (instant exits)
                    self.sell_check()

                    # Buy check every Nth cycle
                    if self.cycle_count % BUY_EVERY_N == 0:
                        log.info(f"── BUY CHECK (cycle {self.cycle_count}) ──")
                        self.buy_check()

                self.cycle_count += 1
                time.sleep(POLL_INTERVAL)

            except Exception as e:
                log.error(f"Main loop error: {e}")
                time.sleep(30)  # back off on error

        log.info("Agent stopped.")


if __name__ == "__main__":
    agent = TradingAgent()
    agent.run()
