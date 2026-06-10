"""Deterministic portfolio caps + circuit breaker for the LLM advisor path.

Design: ../LLM_ADVISOR_DESIGN.md §4. These are the *caller-side* limits that the
LLM never sees and cannot influence. The LLM advisor (llm_advisor.advise) can only
SUBTRACT from the bots' signals; this module is the second, independent wall that
bounds whatever survives — in code, not by model judgement.

Pure function: callers supply the live facts (open positions, realised daily P&L,
trades so far today) so this stays testable and decoupled from Supabase/broker.
Everything is fail-closed: missing/garbage inputs reject the trade.

Caps (env-overridable, conservative defaults — kept SEPARATE from the bot configs
so the LLM-gated path has its own independent kill limits):
    ADVISOR_PER_TRADE_MAX_USD      default 50
    ADVISOR_MAX_OPEN_POSITIONS     default 3
    ADVISOR_MAX_DAILY_TRADES       default 5
    ADVISOR_DAILY_LOSS_LIMIT_USD   default 25   (breaker trips at <= -this)
    ADVISOR_SYMBOL_ALLOWLIST       default "" = allow any (set "BTC,ETH,AAPL" to lock)
"""
import os


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _allowlist():
    raw = os.environ.get("ADVISOR_SYMBOL_ALLOWLIST", "").strip()
    if not raw:
        return None  # None = no allowlist restriction
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def breaker_tripped(daily_realized_pnl):
    """True if the daily-loss circuit breaker should be tripped. On a trip the
    CALLER must flip the trading gates off and call executor.cancel_all()."""
    try:
        return float(daily_realized_pnl) <= -_f("ADVISOR_DAILY_LOSS_LIMIT_USD", 25)
    except (TypeError, ValueError):
        return True  # fail closed: unknown P&L → treat as tripped


def apply_caps(approved, *, open_positions, daily_realized_pnl, trades_today):
    """Filter the LLM-approved signals through deterministic portfolio caps.

    Args:
        approved: list of approved signals from llm_advisor.advise() — each
            {id, symbol, side, qty, rank, ...}; here `qty` is USD notional ceiling.
        open_positions: int, current count of open positions in the account.
        daily_realized_pnl: float, today's realised P&L (USD).
        trades_today: int, trades already executed today on this path.

    Returns:
        {"allowed": [...], "rejected": [{"id","symbol","reason"}],
         "breaker_tripped": bool}
    Fail-closed: any bad input → that signal rejected (or, for the breaker, all).
    """
    out = {"allowed": [], "rejected": [], "breaker_tripped": False}

    # 1) Circuit breaker — independent of anything the model said.
    if breaker_tripped(daily_realized_pnl):
        out["breaker_tripped"] = True
        out["rejected"] = [
            {"id": s.get("id"), "symbol": s.get("symbol"),
             "reason": "daily_loss_circuit_breaker"} for s in (approved or [])
        ]
        return out  # caller must flip gates off + cancel_all()

    per_trade_max = _f("ADVISOR_PER_TRADE_MAX_USD", 50)
    max_open = _i("ADVISOR_MAX_OPEN_POSITIONS", 3)
    max_daily = _i("ADVISOR_MAX_DAILY_TRADES", 5)
    allow = _allowlist()

    try:
        slots = max(0, max_open - int(open_positions))
        room_today = max(0, max_daily - int(trades_today))
    except (TypeError, ValueError):
        # Unknown account state → reject everything (fail closed).
        out["rejected"] = [
            {"id": s.get("id"), "symbol": s.get("symbol"),
             "reason": "unknown_account_state"} for s in (approved or [])
        ]
        return out

    budget = min(slots, room_today)

    # Honour the LLM's ranking (already sorted), then apply hard caps.
    for s in approved or []:
        sym = str(s.get("symbol", "")).upper()
        try:
            qty = float(s.get("qty", 0))
        except (TypeError, ValueError):
            out["rejected"].append({"id": s.get("id"), "symbol": sym,
                                    "reason": "bad_qty"})
            continue
        if allow is not None and sym not in allow:
            out["rejected"].append({"id": s.get("id"), "symbol": sym,
                                    "reason": "not_on_allowlist"})
            continue
        if qty <= 0:
            out["rejected"].append({"id": s.get("id"), "symbol": sym,
                                    "reason": "nonpositive_qty"})
            continue
        if budget <= 0:
            out["rejected"].append({"id": s.get("id"), "symbol": sym,
                                    "reason": "no_capacity (positions/daily cap)"})
            continue
        # Clamp notional to the per-trade ceiling (subtract-only stays intact).
        clamped = min(qty, per_trade_max)
        allowed = dict(s)
        allowed["qty"] = clamped
        if clamped < qty:
            allowed["capped_from"] = qty
        out["allowed"].append(allowed)
        budget -= 1

    return out
