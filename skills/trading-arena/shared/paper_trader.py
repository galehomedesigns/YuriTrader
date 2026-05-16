"""Paper trading engine — simulates trades and tracks P&L in Supabase."""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    SUPABASE_URL, SUPABASE_KEY, MAX_POSITION_PCT, MAX_POSITION_USD, MAX_CONCURRENT_POS,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    LIVE_TRADING_ENABLED, LIVE_TRADING_BOTS, LIVE_MAX_POSITION_USD,
    LIVE_MAX_EXPOSURE_USD, LIVE_DAILY_LOSS_LIMIT,
    RE_ENTRY_COOLDOWN_MINUTES,
)
# Live trading executor (lazy-loaded to keep paper-only setups working)
try:
    from shared.kraken_executor import KrakenExecutor, KrakenExecutorError, is_trade_eligible_for_live, KRAKEN_PAIR_MAP
    _LIVE_AVAILABLE = True
except ImportError:
    _LIVE_AVAILABLE = False

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _send_telegram(message):
    """Send a notification to Telegram."""
    # Silenced 2026-04-12 at Tony's request — arena alert firehose was too noisy.
    # To re-enable: remove the early return below.
    return
    if not TELEGRAM_TOKEN:
        return
    try:
        data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _supabase_post(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {**HEADERS, "Prefer": "return=representation"}
    body = json.dumps(data if isinstance(data, list) else [data]).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Supabase POST error: {e}", file=sys.stderr)
        return None


def _supabase_get(path):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return []


def _supabase_patch(table, match, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{match}"
    headers = {**HEADERS, "Prefer": "return=representation"}
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Supabase PATCH error: {e}", file=sys.stderr)
        return None


class PaperTrader:
    """Manages paper trading positions and P&L for a single bot."""

    def __init__(self, bot_id, bot_name, starting_balance=1000.0):
        self.bot_id = bot_id
        self.bot_name = bot_name
        self.starting_balance = starting_balance
        self._init_balance()

    def _init_balance(self):
        """Initialize or load bot balance from Supabase."""
        existing = _supabase_get(
            f"arena_balances?bot_id=eq.{self.bot_id}&limit=1"
        )
        if not existing:
            _supabase_post("arena_balances", {
                "bot_id": self.bot_id,
                "bot_name": self.bot_name,
                "starting_balance": self.starting_balance,
                "current_balance": self.starting_balance,
                "total_pnl": 0,
                "total_trades": 0,
                "win_rate": 0,
            })
            self.balance = self.starting_balance
        else:
            self.balance = existing[0].get("current_balance", self.starting_balance)

    def get_open_positions(self):
        """Get all open positions for this bot."""
        return _supabase_get(
            f"arena_trades?bot_id=eq.{self.bot_id}&status=eq.open&select=*"
        ) or []

    def get_position_count(self):
        return len(self.get_open_positions())

    def has_position(self, symbol):
        """Check if bot already has an open position in this symbol."""
        positions = _supabase_get(
            f"arena_trades?bot_id=eq.{self.bot_id}&status=eq.open&symbol=eq.{symbol}&limit=1"
        )
        return bool(positions)

    def _minutes_since_last_close(self, symbol):
        """Minutes since this bot's most recent closed trade on this symbol.
        Returns None if no prior closed trade exists. Used to enforce the
        re-entry cooldown — without it, paper P&L looks good but live P&L
        gets eaten by Kraken's 0.40% taker fee on every round trip."""
        rows = _supabase_get(
            f"arena_trades?bot_id=eq.{self.bot_id}&symbol=eq.{symbol}"
            f"&status=eq.closed&order=closed_at.desc&select=closed_at&limit=1"
        ) or []
        if not rows or not rows[0].get("closed_at"):
            return None
        closed_str = rows[0]["closed_at"].replace("Z", "+00:00")
        try:
            closed = datetime.fromisoformat(closed_str)
        except ValueError:
            return None
        return (datetime.now(timezone.utc) - closed).total_seconds() / 60.0

    def can_open_position(self):
        """Check if bot can open a new position."""
        return self.get_position_count() < MAX_CONCURRENT_POS

    def _live_exposure_usd(self):
        """Sum of all currently-open LIVE position USD values across all bots."""
        live_open = _supabase_get(
            "arena_trades?paper=eq.false&status=eq.open&select=qty,entry_price"
        ) or []
        return sum((p.get("qty") or 0) * (p.get("entry_price") or 0) for p in live_open)

    def _live_daily_pnl(self):
        """Sum of all LIVE closed trades' P&L today across all bots."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        closed = _supabase_get(
            f"arena_trades?paper=eq.false&status=eq.closed"
            f"&closed_at=gte.{today}T00:00:00Z&select=pnl"
        ) or []
        return sum(t.get("pnl", 0) for t in closed)

    def _try_live_trade(self, symbol, price, side, position_size):
        """Attempt to execute a live trade on Kraken.

        Returns (success, kraken_data, reason). On any failure, returns
        (False, None, reason_string) and the caller falls back to paper.
        """
        # Gate 1: live trading available
        if not _LIVE_AVAILABLE:
            return False, None, "kraken_executor module not loaded"
        if not LIVE_TRADING_ENABLED:
            return False, None, "LIVE_TRADING_ENABLED=false"

        # Gate 2: bot eligibility
        eligible, reason = is_trade_eligible_for_live(self.bot_id, symbol, position_size)
        if not eligible:
            return False, None, reason

        # Gate 3: portfolio-level safety
        exposure = self._live_exposure_usd()
        if exposure + position_size > LIVE_MAX_EXPOSURE_USD:
            return False, None, f"exposure ${exposure:.2f}+${position_size:.2f} > LIVE_MAX_EXPOSURE_USD ${LIVE_MAX_EXPOSURE_USD}"

        daily_pnl = self._live_daily_pnl()
        if daily_pnl <= LIVE_DAILY_LOSS_LIMIT:
            return False, None, f"daily live P&L ${daily_pnl:.2f} hit limit ${LIVE_DAILY_LOSS_LIMIT}"

        # All gates passed — call Kraken
        try:
            executor = KrakenExecutor()
            result = executor.execute_arena_trade(
                symbol=symbol, side=side.lower(),
                position_size_usd=position_size, current_price=price
            )
            return True, result, "ok"
        except KrakenExecutorError as e:
            return False, None, f"Kraken error: {str(e)[:120]}"
        except Exception as e:
            return False, None, f"unexpected error: {type(e).__name__}: {str(e)[:120]}"

    def open_position(self, symbol, price, side="BUY", reason="", tay_components=None):
        """Open a position. Routes to live Kraken trade if eligible, else paper."""
        if self.has_position(symbol):
            return None
        if not self.can_open_position():
            return None

        # Re-entry cooldown — block fast same-symbol re-entries that would
        # be eaten by fees in live mode.
        minutes_since = self._minutes_since_last_close(symbol)
        if minutes_since is not None and minutes_since < RE_ENTRY_COOLDOWN_MINUTES:
            return None

        position_size = min(self.balance * MAX_POSITION_PCT, MAX_POSITION_USD)
        # If this trade is eligible for live execution, cap at live size
        if (LIVE_TRADING_ENABLED and self.bot_id in LIVE_TRADING_BOTS
                and symbol in (KRAKEN_PAIR_MAP if _LIVE_AVAILABLE else {})):
            position_size = min(position_size, LIVE_MAX_POSITION_USD)
        if position_size <= 0 or price <= 0:
            return None

        # Try live execution first (only fires if all gates pass)
        live_ok, live_data, live_reason = self._try_live_trade(symbol, price, side, position_size)

        if live_ok:
            # === LIVE TRADE PATH ===
            actual_volume = live_data["volume"]
            actual_price = price  # Market order — fill price will be reflected by Kraken later
            kraken_order_id = live_data.get("order_id") or ""
            is_dry_run = live_data.get("dry_run", True)

            trade = {
                "bot_id": self.bot_id,
                "bot_name": self.bot_name,
                "symbol": symbol,
                "side": side,
                "entry_price": actual_price,
                "qty": actual_volume,
                "status": "open",
                "reason": reason,
                "paper": False,  # LIVE
                "kraken_order_id": kraken_order_id,
                "fill_price": actual_price,
            }
            if tay_components:
                trade["tay_components"] = tay_components
            result = _supabase_post("arena_trades", trade)
            if result:
                # NOTE: balance tracking uses paper $ for the leaderboard.
                # Real USD comes from Kraken account directly.
                self.balance -= position_size
                self._update_balance()
                mode_tag = "DRY-RUN" if is_dry_run else "LIVE"
                print(f"  [{self.bot_name}] {mode_tag} {side} {symbol} @ ${actual_price:.2f} "
                      f"(vol={actual_volume:.8f}, ${position_size:.2f}) — order={kraken_order_id} — {reason}")
                _send_telegram(
                    f"\U0001f4b0 <b>{mode_tag}</b>: {self.bot_name} {side} {symbol} @ ${actual_price:.2f}\n"
                    f"Volume: {actual_volume:.8f} | Paid: ${position_size:.2f}\n"
                    f"Order: <code>{kraken_order_id or 'validate-only'}</code>\n"
                    f"Reason: {reason}"
                )
            return result

        # === PAPER TRADE PATH ===
        # If eligibility was checked and FAILED (not just "not eligible"), surface why
        was_attempted_live = (LIVE_TRADING_ENABLED and self.bot_id in LIVE_TRADING_BOTS
                              and symbol in (KRAKEN_PAIR_MAP if _LIVE_AVAILABLE else {}))
        if was_attempted_live and live_reason and live_reason != "ok":
            print(f"  [{self.bot_name}] Live blocked: {live_reason} — falling back to paper", file=sys.stderr)
            _send_telegram(
                f"\u26a0 <b>{self.bot_name}</b> wanted LIVE {side} {symbol} but blocked\n"
                f"Reason: {live_reason}\nFalling back to paper."
            )

        qty = position_size / price
        trade = {
            "bot_id": self.bot_id,
            "bot_name": self.bot_name,
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "qty": qty,
            "status": "open",
            "reason": reason,
            "paper": True,
        }
        if tay_components:
            trade["tay_components"] = tay_components
        result = _supabase_post("arena_trades", trade)
        if result:
            self.balance -= position_size
            self._update_balance()
            print(f"  [{self.bot_name}] OPEN {side} {symbol} @ ${price:.2f} "
                  f"(qty={qty:.6f}, ${position_size:.2f}) — {reason}")
            _send_telegram(
                f"\U0001f7e2 <b>{self.bot_name}</b> {side} {symbol} @ ${price:.2f}\n"
                f"Size: ${position_size:.2f} | Reason: {reason}"
            )
        return result

    def close_position(self, position, current_price, exit_reason=""):
        """Close a paper trade position. If position is live, send sell order to Kraken."""
        entry = position.get("entry_price", 0)
        qty = position.get("qty", 0)
        side = position.get("side", "BUY")
        symbol = position.get("symbol", "")
        is_live = position.get("paper") is False

        # If this is a LIVE position, place the closing order on Kraken first
        live_close_order_id = None
        live_close_failed = False
        if is_live and _LIVE_AVAILABLE:
            close_side = "sell" if side == "BUY" else "buy"
            try:
                executor = KrakenExecutor()
                kraken_pair = KRAKEN_PAIR_MAP.get(symbol)
                if kraken_pair:
                    # Use validate=true if env gate is closed (safety mirror)
                    env_allow = os.environ.get("KRAKEN_ALLOW_TRADING", "false").lower() == "true"
                    result = executor.place_market_order(
                        kraken_pair=kraken_pair, side=close_side,
                        volume=qty, validate=not env_allow
                    )
                    live_close_order_id = result.get("order_id")
                    print(f"  [{self.bot_name}] LIVE CLOSE order placed: {live_close_order_id}",
                          file=sys.stderr)
            except Exception as e:
                live_close_failed = True
                print(f"  [{self.bot_name}] LIVE CLOSE FAILED: {e}", file=sys.stderr)
                _send_telegram(
                    f"\u26a0 <b>{self.bot_name}</b> LIVE CLOSE FAILED for {symbol}\n"
                    f"Error: {str(e)[:200]}\nMANUAL INTERVENTION REQUIRED"
                )

        if side == "BUY":
            pnl = (current_price - entry) * qty
            pnl_pct = ((current_price - entry) / entry * 100) if entry else 0
        else:  # SHORT
            pnl = (entry - current_price) * qty
            pnl_pct = ((entry - current_price) / entry * 100) if entry else 0

        trade_id = position.get("id")
        update_data = {
            "exit_price": current_price,
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "status": "closed",
            "exit_reason": exit_reason,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        }
        if live_close_order_id:
            update_data["fill_price"] = current_price  # market order, approximate
        _supabase_patch("arena_trades", f"id=eq.{trade_id}", update_data)

        self.balance += (qty * current_price) if side == "BUY" else (qty * entry + pnl)
        self._update_balance(pnl)

        emoji = "+" if pnl >= 0 else ""
        mode_tag = "LIVE" if is_live else ""
        print(f"  [{self.bot_name}] CLOSE {mode_tag} {symbol} @ ${current_price:.2f} "
              f"— {emoji}${pnl:.2f} ({emoji}{pnl_pct:.1f}%) — {exit_reason}")
        tg_emoji = "\U0001f534" if pnl < 0 else "\U0001f7e2"
        if is_live:
            tg_emoji = "\U0001f4b0"  # money bag for live
        _send_telegram(
            f"{tg_emoji} <b>{self.bot_name}</b> {('LIVE ' if is_live else '')}CLOSED {symbol} @ ${current_price:.2f}\n"
            f"P&L: {emoji}${pnl:.2f} ({emoji}{pnl_pct:.1f}%) | {exit_reason}"
        )
        return pnl

    def _update_balance(self, pnl=0):
        """Update bot balance in Supabase."""
        # Get all closed trades for stats
        closed = _supabase_get(
            f"arena_trades?bot_id=eq.{self.bot_id}&status=eq.closed&select=pnl"
        ) or []
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        total_trades = len(closed)
        wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        _supabase_patch("arena_balances", f"bot_id=eq.{self.bot_id}", {
            "current_balance": round(self.balance, 2),
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 1),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    def get_daily_pnl(self):
        """Get today's P&L for risk management."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        closed_today = _supabase_get(
            f"arena_trades?bot_id=eq.{self.bot_id}&status=eq.closed"
            f"&closed_at=gte.{today}T00:00:00Z&select=pnl"
        ) or []
        return sum(t.get("pnl", 0) for t in closed_today)

    def log_signal(self, symbol, action, confidence=None, indicators=None, executed=False):
        """Log a trading signal."""
        _supabase_post("arena_signals", {
            "bot_id": self.bot_id,
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "indicators": json.dumps(indicators) if indicators else None,
            "executed": executed,
        })
