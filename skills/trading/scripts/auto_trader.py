#!/usr/bin/env python3
"""
Autonomous day-trader — evaluates buy/sell/short/cover flags and executes trades.

Usage:
    python3 auto_trader.py evaluate       # Full buy + sell evaluation cycle
    python3 auto_trader.py sell-check     # Quick sell/cover check only (runs every 5 min)
    python3 auto_trader.py positions      # Show open auto-trade positions
    python3 auto_trader.py history [days] # Show closed trades
    python3 auto_trader.py pause [reason] # Pause auto-trading
    python3 auto_trader.py resume         # Resume auto-trading
    python3 auto_trader.py status         # System status
"""

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
QUESTRADE_SCRIPT = "/home/tonygale/openclaw/skills/questrade/scripts/questrade.py"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

NOW = datetime.now(timezone.utc)


# ── Supabase helpers ──

def supabase_get(table, params=None):
    resp = httpx.get(f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": "return=representation"},
        params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()

def supabase_post(table, data):
    resp = httpx.post(f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": "return=representation"},
        json=data, timeout=15)
    resp.raise_for_status()
    return resp.json()

def supabase_patch(table, params, data):
    resp = httpx.patch(f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**HEADERS, "Prefer": "return=representation"},
        params=params, json=data, timeout=15)
    resp.raise_for_status()
    return resp.json()

def get_rule(key):
    rows = supabase_get("trading_rules", {"key": f"eq.{key}", "select": "value"})
    return rows[0]["value"] if rows else {}

def set_rule(key, value):
    httpx.post(f"{SUPABASE_URL}/rest/v1/trading_rules",
        headers={**HEADERS, "Prefer": "return=minimal,resolution=merge-duplicates"},
        json={"key": key, "value": value, "updated_at": NOW.isoformat()}, timeout=10)

def run_questrade(*args):
    result = subprocess.run(["python3", QUESTRADE_SCRIPT] + list(args),
        capture_output=True, text=True, timeout=30)
    return result.stdout, result.stderr, result.returncode

def audit_log(action, symbol, trade_id=None, details=None):
    supabase_post("trade_audit", {
        "action": action, "symbol": symbol,
        "trade_id": trade_id,
        "details": details or {},
    })


# ── Technical Indicators ──

def get_snapshots(symbol, limit=100):
    """Get recent price snapshots for a symbol."""
    return supabase_get("market_snapshots", {
        "symbol": f"eq.{symbol}",
        "select": "price,volume,day_change_pct,snapshot_at",
        "order": "snapshot_at.desc",
        "limit": str(limit),
    })

def get_daily_closes(snapshots):
    """Extract one close per day from 15-min snapshots."""
    daily = {}
    for s in snapshots:
        date = s["snapshot_at"][:10]
        if date not in daily:
            daily[date] = float(s["price"])
    return [daily[d] for d in sorted(daily.keys())]  # oldest to newest

def compute_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period

def compute_ema(prices, period):
    """Exponential Moving Average."""
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period  # seed with SMA
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def compute_macd(prices):
    """MACD (12/26/9). Returns (macd_line, signal_line, histogram)."""
    if len(prices) < 26:
        return None, None, None
    ema12 = compute_ema(prices, 12)
    ema26 = compute_ema(prices, 26)
    if ema12 is None or ema26 is None:
        return None, None, None

    # Compute MACD line series for signal line
    macd_series = []
    m12 = 2 / 13
    m26 = 2 / 27
    e12 = sum(prices[:12]) / 12
    e26 = sum(prices[:26]) / 26
    for i, p in enumerate(prices):
        if i < 12:
            continue
        e12 = (p - e12) * m12 + e12
        if i < 26:
            continue
        e26 = (p - e26) * m26 + e26
        macd_series.append(e12 - e26)

    macd_line = macd_series[-1] if macd_series else None
    signal_line = compute_ema(macd_series, 9) if len(macd_series) >= 9 else None
    histogram = (macd_line - signal_line) if macd_line and signal_line else None
    return macd_line, signal_line, histogram

def compute_rsi(prices, period=14):
    """RSI-14 from daily closes."""
    if len(prices) < period + 1:
        return None
    changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    recent = changes[-period:]
    gains = [c for c in recent if c > 0]
    losses = [-c for c in recent if c < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.001
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def compute_bollinger(prices, period=20, std_dev=2):
    """Bollinger Bands. Returns (upper, middle, lower)."""
    if len(prices) < period:
        return None, None, None
    window = prices[-period:]
    middle = sum(window) / period
    variance = sum((p - middle) ** 2 for p in window) / period
    std = variance ** 0.5
    return middle + std_dev * std, middle, middle - std_dev * std

def compute_vwap(snapshots):
    """VWAP from intraday snapshots (today only)."""
    today = NOW.strftime("%Y-%m-%d")
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


# ── Flag Checks ──

def check_buy_flags(symbol, snapshots, trend_signals, current_price):
    """Evaluate 9 buy flags. Returns list of met flag names."""
    flags = []
    closes = get_daily_closes(snapshots)
    rules = get_rule("buy_flags")

    # 1. Bullish SMA Crossover: SMA-5 > SMA-20 (and previously wasn't)
    if rules.get("sma_crossover_enabled", True):
        if len(trend_signals) >= 2:
            curr = trend_signals[0]
            prev = trend_signals[1]
            if (curr.get("sma_5") and curr.get("sma_20") and prev.get("sma_5") and prev.get("sma_20")):
                if float(curr["sma_5"]) > float(curr["sma_20"]) and float(prev["sma_5"]) <= float(prev["sma_20"]):
                    flags.append("SMA Crossover")

    # 2. Price Above SMA-50
    if rules.get("price_above_sma50_enabled", True):
        sma50 = compute_sma(closes, 50)
        if sma50 and current_price > sma50:
            flags.append("Price > SMA-50")

    # 3. Volume Surge
    if rules.get("volume_surge_enabled", True):
        threshold = rules.get("volume_surge_threshold", 1.5)
        volumes = [int(s["volume"]) for s in snapshots if s.get("volume")]
        if len(volumes) >= 5:
            current_vol = volumes[0]
            avg_vol = sum(volumes[1:21]) / min(len(volumes[1:21]), 20) if len(volumes) > 1 else 1
            if avg_vol > 0 and current_vol > avg_vol * threshold:
                flags.append(f"Volume Surge ({current_vol/avg_vol:.1f}x)")

    # 4. Positive Momentum
    if rules.get("momentum_enabled", True):
        threshold = rules.get("momentum_threshold_pct", 1.0)
        if snapshots and snapshots[0].get("day_change_pct"):
            pct = float(snapshots[0]["day_change_pct"])
            if pct >= threshold:
                flags.append(f"+{pct:.1f}% Momentum")

    # 5. RSI Oversold Bounce
    if rules.get("rsi_oversold_enabled", True):
        rsi = compute_rsi(closes)
        rsi_threshold = rules.get("rsi_oversold_threshold", 30)
        if rsi is not None:
            # Check if RSI was below threshold recently and now above
            prev_closes = closes[:-1] if len(closes) > 1 else closes
            prev_rsi = compute_rsi(prev_closes)
            if prev_rsi is not None and prev_rsi < rsi_threshold and rsi >= rsi_threshold:
                flags.append(f"RSI Bounce ({rsi:.0f})")

    # 6. Positive News/Social Catalyst
    if rules.get("news_catalyst_enabled", True):
        two_hours_ago = (NOW - timedelta(hours=2)).isoformat()
        news = supabase_get("news_events", {
            "impact_level": "eq.HIGH", "fetched_at": f"gte.{two_hours_ago}",
            "select": "id", "limit": "1"})
        social = supabase_get("social_signals", {
            "severity": "eq.HIGH", "market_relevant": "eq.true",
            "fetched_at": f"gte.{two_hours_ago}", "select": "id", "limit": "1"})
        if news or social:
            flags.append("News/Social Catalyst")

    # 7. MACD Bullish Crossover
    if rules.get("macd_enabled", True):
        macd, signal, hist = compute_macd(closes)
        if macd is not None and signal is not None:
            # Check if MACD just crossed above signal
            prev_closes = closes[:-1]
            prev_macd, prev_signal, _ = compute_macd(prev_closes)
            if prev_macd is not None and prev_signal is not None:
                if macd > signal and prev_macd <= prev_signal:
                    flags.append("MACD Bullish")

    # 8. Bollinger Band Bounce
    if rules.get("bollinger_enabled", True):
        upper, middle, lower = compute_bollinger(closes)
        if lower is not None:
            # Price near or below lower band and bouncing up
            prev_price = closes[-2] if len(closes) >= 2 else current_price
            if prev_price <= lower and current_price > lower:
                flags.append("Bollinger Bounce")

    # 9. Price Above VWAP
    if rules.get("vwap_enabled", True):
        vwap = compute_vwap(snapshots)
        if vwap is not None and current_price > vwap:
            flags.append("Above VWAP")

    return flags


def check_sell_flags(position, current_price, snapshots, trend_signals):
    """Evaluate 9 sell flags for a LONG position. Returns first triggered flag or None."""
    rules = get_rule("sell_flags")
    entry = float(position["entry_price"])
    ret_pct = ((current_price - entry) / entry) * 100

    # 1. Take Profit
    tp = rules.get("take_profit_pct", 2.0)
    if ret_pct >= tp:
        return f"Take Profit (+{ret_pct:.1f}%)"

    # 2. Stop Loss
    sl = rules.get("stop_loss_pct", -2.0)
    if ret_pct <= sl:
        return f"Stop Loss ({ret_pct:.1f}%)"

    closes = get_daily_closes(snapshots)

    # 3. Bearish SMA Crossover
    if rules.get("bearish_crossover_enabled", True) and len(trend_signals) >= 2:
        curr, prev = trend_signals[0], trend_signals[1]
        if (curr.get("sma_5") and curr.get("sma_20") and prev.get("sma_5") and prev.get("sma_20")):
            if float(curr["sma_5"]) < float(curr["sma_20"]) and float(prev["sma_5"]) >= float(prev["sma_20"]):
                return "Bearish SMA Crossover"

    # 4. Volume Dump
    if rules.get("volume_dump_enabled", True):
        threshold = rules.get("volume_dump_threshold", 2.0)
        volumes = [int(s["volume"]) for s in snapshots if s.get("volume")]
        if len(volumes) >= 5 and snapshots[0].get("day_change_pct"):
            pct = float(snapshots[0]["day_change_pct"])
            current_vol = volumes[0]
            avg_vol = sum(volumes[1:21]) / min(len(volumes[1:21]), 20)
            if pct < 0 and avg_vol > 0 and current_vol > avg_vol * threshold:
                return f"Volume Dump ({current_vol/avg_vol:.1f}x, {pct:.1f}%)"

    # 5. Negative News/Social Catalyst
    if rules.get("negative_catalyst_enabled", True):
        two_hours_ago = (NOW - timedelta(hours=2)).isoformat()
        social = supabase_get("social_signals", {
            "severity": "eq.HIGH", "market_relevant": "eq.true",
            "fetched_at": f"gte.{two_hours_ago}", "select": "content", "limit": "3"})
        # Check if any negative content mentions the symbol
        sym_lower = position["symbol"].lower().replace(".to", "")
        for s in social:
            content = s.get("content", "").lower()
            if sym_lower in content:
                return "Negative Catalyst"

    # 6. Time Decay
    if rules.get("time_decay_enabled", True):
        max_days = rules.get("time_decay_days", 1)
        opened = datetime.fromisoformat(position["opened_at"].replace("Z", "+00:00"))
        days_held = (NOW - opened).days
        if days_held >= max_days and ret_pct <= 0:
            return f"Time Decay ({days_held}d, {ret_pct:.1f}%)"

    # 7. MACD Bearish Crossover
    if rules.get("macd_bearish_enabled", True):
        macd, signal, _ = compute_macd(closes)
        prev_macd, prev_signal, _ = compute_macd(closes[:-1]) if len(closes) > 1 else (None, None, None)
        if macd is not None and signal is not None and prev_macd is not None and prev_signal is not None:
            if macd < signal and prev_macd >= prev_signal:
                return "MACD Bearish"

    # 8. Bollinger Upper Band Touch
    if rules.get("bollinger_upper_enabled", True):
        upper, _, _ = compute_bollinger(closes)
        if upper is not None and current_price >= upper:
            return "Bollinger Upper Touch"

    # 9. Price Drops Below VWAP
    if rules.get("vwap_drop_enabled", True):
        vwap = compute_vwap(snapshots)
        if vwap is not None and current_price < vwap and ret_pct > 0:
            return "Below VWAP"

    return None


def check_short_flags(symbol, snapshots, trend_signals, current_price):
    """Evaluate 6 short sell flags. Returns list of met flag names."""
    flags = []
    closes = get_daily_closes(snapshots)

    # 1. Bearish SMA Crossover
    if len(trend_signals) >= 2:
        curr, prev = trend_signals[0], trend_signals[1]
        if (curr.get("sma_5") and curr.get("sma_20") and prev.get("sma_5") and prev.get("sma_20")):
            if float(curr["sma_5"]) < float(curr["sma_20"]) and float(prev["sma_5"]) >= float(prev["sma_20"]):
                flags.append("Bearish SMA Crossover")

    # 2. Price Below SMA-50
    sma50 = compute_sma(closes, 50)
    if sma50 and current_price < sma50:
        flags.append("Price < SMA-50")

    # 3. MACD Bearish Crossover
    macd, signal, _ = compute_macd(closes)
    prev_macd, prev_signal, _ = compute_macd(closes[:-1]) if len(closes) > 1 else (None, None, None)
    if macd is not None and signal is not None and prev_macd is not None and prev_signal is not None:
        if macd < signal and prev_macd >= prev_signal:
            flags.append("MACD Bearish")

    # 4. RSI Overbought Drop
    rsi = compute_rsi(closes)
    prev_rsi = compute_rsi(closes[:-1]) if len(closes) > 1 else None
    if rsi is not None and prev_rsi is not None:
        if prev_rsi > 70 and rsi <= 70:
            flags.append(f"RSI Overbought Drop ({rsi:.0f})")

    # 5. Bollinger Upper Band Rejection
    upper, _, _ = compute_bollinger(closes)
    if upper is not None and len(closes) >= 2:
        if closes[-2] >= upper and current_price < upper:
            flags.append("Bollinger Rejection")

    # 6. Negative News/Social Catalyst
    two_hours_ago = (NOW - timedelta(hours=2)).isoformat()
    social = supabase_get("social_signals", {
        "severity": "eq.HIGH", "market_relevant": "eq.true",
        "fetched_at": f"gte.{two_hours_ago}", "select": "id", "limit": "1"})
    if social:
        flags.append("Negative Catalyst")

    return flags


def check_cover_flags(position, current_price, snapshots, trend_signals):
    """Evaluate 6 cover flags for a SHORT position. Returns first triggered or None."""
    entry = float(position["entry_price"])
    # For shorts, profit = entry - current (price went down)
    ret_pct = ((entry - current_price) / entry) * 100

    # 1. Take Profit (stock dropped 2% from short entry)
    if ret_pct >= 2.0:
        return f"Take Profit (+{ret_pct:.1f}%)"

    # 2. Stop Loss (stock rose 2% against the short)
    if ret_pct <= -2.0:
        return f"Stop Loss ({ret_pct:.1f}%)"

    closes = get_daily_closes(snapshots)

    # 3. Bullish SMA Crossover
    if len(trend_signals) >= 2:
        curr, prev = trend_signals[0], trend_signals[1]
        if (curr.get("sma_5") and curr.get("sma_20") and prev.get("sma_5") and prev.get("sma_20")):
            if float(curr["sma_5"]) > float(curr["sma_20"]) and float(prev["sma_5"]) <= float(prev["sma_20"]):
                return "Bullish SMA Crossover"

    # 4. MACD Bullish Crossover
    macd, signal, _ = compute_macd(closes)
    prev_macd, prev_signal, _ = compute_macd(closes[:-1]) if len(closes) > 1 else (None, None, None)
    if macd is not None and signal is not None and prev_macd is not None and prev_signal is not None:
        if macd > signal and prev_macd <= prev_signal:
            return "MACD Bullish"

    # 5. Price Above VWAP
    vwap = compute_vwap(snapshots)
    if vwap is not None and current_price > vwap:
        return "Above VWAP"

    # 6. Time Decay
    opened = datetime.fromisoformat(position["opened_at"].replace("Z", "+00:00"))
    if (NOW - opened).days >= 1:
        return f"Time Decay ({(NOW - opened).days}d)"

    return None


# ── Trade Execution ──

def execute_buy(symbol, current_price, flags, side="BUY"):
    """Buy $9 worth of a stock (fractional shares)."""
    risk = get_rule("risk_limits")
    target = risk.get("max_per_trade", 9.0)
    qty = round(target / current_price, 6)

    if qty <= 0:
        audit_log("SKIP_ZERO_QTY", symbol, details={"price": current_price})
        return None

    # Execute via Questrade
    action = "buy" if side == "BUY" else "sell"  # short sell = sell shares you don't own
    stdout, stderr, rc = run_questrade(action, symbol, str(qty))

    if rc != 0:
        audit_log("ORDER_FAILED", symbol, details={"error": stderr[:200], "side": side})
        print(f"  ORDER FAILED: {symbol} {side} — {stderr[:100]}")
        return None

    # Extract order ID from output
    order_id = "unknown"
    for line in stdout.split("\n"):
        if "Order #" in line:
            order_id = line.split("#")[1].split()[0] if "#" in line else "unknown"

    # Record in auto_trades
    trade = supabase_post("auto_trades", {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_price": current_price,
        "entry_total": round(qty * current_price, 4),
        "status": "OPEN",
        "buy_flags_met": flags,
        "buy_flags_count": len(flags),
        "questrade_buy_order_id": order_id,
    })

    trade_id = trade[0]["id"] if trade else None
    audit_log(f"{side}_EXECUTED", symbol, trade_id=trade_id, details={
        "qty": qty, "price": current_price, "total": round(qty * current_price, 4),
        "flags": flags, "order_id": order_id,
    })

    total = round(qty * current_price, 2)
    print(f"  {side}: {symbol} — {qty:.6f} shares @ ${current_price:.2f} = ${total:.2f}")
    print(f"  Flags ({len(flags)}/9): {', '.join(flags)}")
    return trade_id


def execute_sell(position, reason, current_price):
    """Sell/cover an open position."""
    symbol = position["symbol"]
    qty = float(position["qty"])
    side = position["side"]

    # For longs: sell. For shorts: buy to cover.
    action = "sell" if side == "BUY" else "buy"
    stdout, stderr, rc = run_questrade(action, symbol, str(qty))

    if rc != 0:
        audit_log("SELL_FAILED", symbol, trade_id=position["id"],
            details={"error": stderr[:200]})
        print(f"  SELL FAILED: {symbol} — {stderr[:100]}")
        return

    entry = float(position["entry_price"])
    if side == "BUY":
        pnl = round((current_price - entry) * qty, 4)
        pnl_pct = round(((current_price - entry) / entry) * 100, 2)
    else:  # SHORT
        pnl = round((entry - current_price) * qty, 4)
        pnl_pct = round(((entry - current_price) / entry) * 100, 2)

    supabase_patch("auto_trades", {"id": f"eq.{position['id']}"}, {
        "status": "CLOSED",
        "exit_price": current_price,
        "exit_total": round(qty * current_price, 4),
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "sell_reason": reason,
        "closed_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    })

    audit_log("SELL_EXECUTED", symbol, trade_id=position["id"], details={
        "reason": reason, "entry": entry, "exit": current_price,
        "pnl": pnl, "pnl_pct": pnl_pct,
    })

    sign = "+" if pnl >= 0 else ""
    print(f"  SELL: {symbol} @ ${current_price:.2f} | P&L: {sign}${pnl:.2f} ({sign}{pnl_pct:.1f}%) | Reason: {reason}")


# ── Commands ──

def cmd_evaluate():
    """Full buy + sell evaluation cycle."""
    risk = get_rule("risk_limits")

    # Check if paused
    if risk.get("auto_trading_paused"):
        print(f"Auto-trading is PAUSED: {risk.get('pause_reason', 'manual pause')}")
        return

    if not risk.get("auto_trading_enabled", True):
        print("Auto-trading is DISABLED.")
        return

    # Check daily loss limit
    today_start = NOW.replace(hour=0, minute=0, second=0).isoformat()
    closed_today = supabase_get("auto_trades", {
        "status": "eq.CLOSED", "closed_at": f"gte.{today_start}",
        "select": "pnl",
    })
    daily_pnl = sum(float(t["pnl"]) for t in closed_today if t.get("pnl"))
    pause_threshold = risk.get("daily_loss_pause_threshold", -5.0)
    if daily_pnl <= pause_threshold:
        risk["auto_trading_paused"] = True
        risk["pause_reason"] = f"Daily loss limit: ${daily_pnl:.2f}"
        set_rule("risk_limits", risk)
        print(f"AUTO-TRADE PAUSED — Daily loss limit reached: ${daily_pnl:.2f}")
        return

    # Get open positions
    open_positions = supabase_get("auto_trades", {
        "status": "eq.OPEN", "select": "*", "order": "opened_at.asc",
    })

    # Get watchlist
    wl_config = supabase_get("trading_config", {"key": "eq.watchlist", "select": "value"})
    watchlist = wl_config[0]["value"] if wl_config else []
    if isinstance(watchlist, str):
        watchlist = json.loads(watchlist)

    held_symbols = {p["symbol"] for p in open_positions}
    max_positions = risk.get("max_positions", 5)
    slots_available = max_positions - len(open_positions)

    print(f"Auto-Trade Evaluation — {NOW.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Open: {len(open_positions)}/{max_positions} | Day P&L: ${daily_pnl:.2f}")
    print(f"{'='*50}")

    trades_executed = []

    # ── SELL/COVER CHECK ──
    for pos in open_positions:
        symbol = pos["symbol"]
        snapshots = get_snapshots(symbol, 100)
        if not snapshots:
            continue

        current_price = float(snapshots[0]["price"])
        trend_sigs = supabase_get("trend_signals", {
            "symbol": f"eq.{symbol}", "select": "*",
            "order": "computed_at.desc", "limit": "2",
        })

        if pos["side"] == "BUY":
            reason = check_sell_flags(pos, current_price, snapshots, trend_sigs)
        else:  # SHORT
            reason = check_cover_flags(pos, current_price, snapshots, trend_sigs)

        if reason:
            execute_sell(pos, reason, current_price)
            trades_executed.append(f"SELL {symbol}: {reason}")
            slots_available += 1

    # ── BUY CHECK ──
    if slots_available > 0:
        for symbol in watchlist:
            if symbol in held_symbols:
                continue
            if slots_available <= 0:
                break

            snapshots = get_snapshots(symbol, 100)
            if not snapshots:
                continue

            current_price = float(snapshots[0]["price"])
            trend_sigs = supabase_get("trend_signals", {
                "symbol": f"eq.{symbol}", "select": "*",
                "order": "computed_at.desc", "limit": "2",
            })

            # Check LONG buy flags
            buy_flags = check_buy_flags(symbol, snapshots, trend_sigs, current_price)
            min_flags = get_rule("buy_flags").get("min_flags_to_buy", 3)

            if len(buy_flags) >= min_flags:
                trade_id = execute_buy(symbol, current_price, buy_flags, "BUY")
                if trade_id:
                    trades_executed.append(f"BUY {symbol}: {', '.join(buy_flags)}")
                    held_symbols.add(symbol)
                    slots_available -= 1
                continue

            # Check SHORT flags if enabled
            if risk.get("short_selling_enabled"):
                short_flags = check_short_flags(symbol, snapshots, trend_sigs, current_price)
                if len(short_flags) >= 3:
                    trade_id = execute_buy(symbol, current_price, short_flags, "SHORT")
                    if trade_id:
                        trades_executed.append(f"SHORT {symbol}: {', '.join(short_flags)}")
                        held_symbols.add(symbol)
                        slots_available -= 1

    # Summary
    print(f"{'='*50}")
    if trades_executed:
        print(f"Trades executed: {len(trades_executed)}")
        for t in trades_executed:
            print(f"  {t}")
    else:
        print("No trades triggered.")


def cmd_sell_check():
    """Quick sell/cover check only — runs every 5 min."""
    risk = get_rule("risk_limits")
    if risk.get("auto_trading_paused") or not risk.get("auto_trading_enabled", True):
        return

    open_positions = supabase_get("auto_trades", {
        "status": "eq.OPEN", "select": "*",
    })

    if not open_positions:
        return

    for pos in open_positions:
        symbol = pos["symbol"]
        snapshots = get_snapshots(symbol, 50)
        if not snapshots:
            continue

        current_price = float(snapshots[0]["price"])
        trend_sigs = supabase_get("trend_signals", {
            "symbol": f"eq.{symbol}", "select": "*",
            "order": "computed_at.desc", "limit": "2",
        })

        if pos["side"] == "BUY":
            reason = check_sell_flags(pos, current_price, snapshots, trend_sigs)
        else:
            reason = check_cover_flags(pos, current_price, snapshots, trend_sigs)

        if reason:
            print(f"AUTO-TRADE: SELL")
            execute_sell(pos, reason, current_price)


def cmd_positions():
    positions = supabase_get("auto_trades", {
        "status": "eq.OPEN", "select": "*", "order": "opened_at.desc",
    })
    if not positions:
        print("No open auto-trade positions.")
        return

    print(f"Open Positions ({len(positions)}/5):")
    print(f"  {'Symbol':<10} {'Side':<6} {'Qty':>8} {'Entry':>8} {'Total':>8} {'Flags':>6} {'Opened'}")
    for p in positions:
        dt = p["opened_at"][:16].replace("T", " ")
        print(f"  {p['symbol']:<10} {p['side']:<6} {float(p['qty']):>8.4f} ${float(p['entry_price']):>7.2f} ${float(p['entry_total']):>7.2f} {p['buy_flags_count']:>5}/9 {dt}")


def cmd_history(days=7):
    since = (NOW - timedelta(days=int(days))).isoformat()
    trades = supabase_get("auto_trades", {
        "status": "eq.CLOSED", "closed_at": f"gte.{since}",
        "select": "*", "order": "closed_at.desc",
    })
    if not trades:
        print(f"No closed auto-trades in the last {days} days.")
        return

    total_pnl = sum(float(t["pnl"]) for t in trades if t.get("pnl"))
    wins = len([t for t in trades if t.get("pnl") and float(t["pnl"]) > 0])
    losses = len(trades) - wins

    print(f"Auto-Trade History (last {days} days): {len(trades)} trades")
    print(f"Total P&L: ${total_pnl:.2f} | Wins: {wins} | Losses: {losses}")
    print(f"  {'Symbol':<10} {'Side':<6} {'Entry':>8} {'Exit':>8} {'P&L':>8} {'Reason'}")
    for t in trades:
        pnl = float(t["pnl"]) if t.get("pnl") else 0
        sign = "+" if pnl >= 0 else ""
        print(f"  {t['symbol']:<10} {t['side']:<6} ${float(t['entry_price']):>7.2f} ${float(t.get('exit_price',0)):>7.2f} {sign}${pnl:>6.2f} {t.get('sell_reason','')}")


def cmd_pause(reason=None):
    risk = get_rule("risk_limits")
    risk["auto_trading_paused"] = True
    risk["pause_reason"] = reason or "Manual pause"
    set_rule("risk_limits", risk)
    print(f"Auto-trading PAUSED: {risk['pause_reason']}")


def cmd_resume():
    risk = get_rule("risk_limits")
    risk["auto_trading_paused"] = False
    risk["pause_reason"] = None
    set_rule("risk_limits", risk)
    print("Auto-trading RESUMED.")


def cmd_status():
    risk = get_rule("risk_limits")
    open_pos = supabase_get("auto_trades", {"status": "eq.OPEN", "select": "id"})

    today_start = NOW.replace(hour=0, minute=0, second=0).isoformat()
    closed_today = supabase_get("auto_trades", {
        "status": "eq.CLOSED", "closed_at": f"gte.{today_start}", "select": "pnl"})
    daily_pnl = sum(float(t["pnl"]) for t in closed_today if t.get("pnl"))

    status = "PAUSED" if risk.get("auto_trading_paused") else "ACTIVE" if risk.get("auto_trading_enabled") else "DISABLED"

    print(f"Auto-Trade Status: {status}")
    if risk.get("auto_trading_paused"):
        print(f"  Pause reason: {risk.get('pause_reason')}")
    print(f"  Positions: {len(open_pos)}/{risk.get('max_positions', 5)}")
    print(f"  Max per trade: ${risk.get('max_per_trade', 9.0):.2f}")
    print(f"  Day P&L: ${daily_pnl:.2f} (pause at ${risk.get('daily_loss_pause_threshold', -5.0):.2f})")
    print(f"  Short selling: {'Enabled' if risk.get('short_selling_enabled') else 'Disabled'}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    if cmd == "evaluate":
        cmd_evaluate()
    elif cmd == "sell-check":
        cmd_sell_check()
    elif cmd == "positions":
        cmd_positions()
    elif cmd == "history":
        cmd_history(sys.argv[2] if len(sys.argv) > 2 else 7)
    elif cmd == "pause":
        cmd_pause(" ".join(sys.argv[2:]) if len(sys.argv) > 2 else None)
    elif cmd == "resume":
        cmd_resume()
    elif cmd == "status":
        cmd_status()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
