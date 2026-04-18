"""
Evaluates all buy flags, sell flags, and risk controls.
Buy rules: need 3+ flags to trigger a buy
Sell rules: ANY single flag triggers a sell immediately
"""
import config


def score_buy_flags(indicators, news_impact, symbol=""):
    """Returns list of (flag_number, flag_name) for each triggered rule.
    For inverse ETFs, bearish market signals = buy signals."""
    flags = []
    d = indicators
    is_inverse = symbol in config.INVERSE_SYMBOLS

    def have(*keys):
        return all(d.get(k) is not None for k in keys)

    if is_inverse:
        # ── INVERSE ETF BUY FLAGS (bearish market = bullish for inverse) ──

        # 1 — Bearish SMA crossover on the inverse ETF itself (its price rising)
        if have("sma5", "sma5_prev", "sma20", "sma20_prev"):
            if d["sma5_prev"] < d["sma20_prev"] and d["sma5"] >= d["sma20"]:
                flags.append((1, "SMA crossover (inverse rising)"))

        # 2 — Price above SMA-50 (inverse ETF trending up = market trending down)
        if have("sma50_daily") and d["price"] > d["sma50_daily"]:
            flags.append((2, "Above SMA-50 (inverse)"))

        # 3 — Volume surge (high volume on inverse = fear/selling in market)
        if d["volume_avg_20"] > 0 and d["volume"] > 1.5 * d["volume_avg_20"]:
            flags.append((3, "Volume surge (inverse)"))

        # 4 — Positive momentum on inverse ETF (market dropping)
        if have("open") and d["open"] > 0:
            if (d["price"] - d["open"]) / d["open"] > 0.01:
                flags.append((4, "Inverse momentum +1%"))

        # 5 — RSI oversold bounce on inverse (was beaten down, now recovering)
        if have("rsi", "rsi_prev"):
            if d["rsi_prev"] < 30 and d["rsi"] > 30:
                flags.append((5, "RSI bounce (inverse)"))

        # 6 — Negative news catalyst (bad for market = good for inverse)
        if news_impact == "HIGH":
            flags.append((6, "Negative catalyst (bearish market)"))

        # 7 — MACD bullish on inverse ETF
        if have("macd", "macd_signal", "macd_prev", "macd_signal_prev"):
            if d["macd_prev"] < d["macd_signal_prev"] and d["macd"] >= d["macd_signal"]:
                flags.append((7, "MACD bullish (inverse)"))

        # 8 — Bollinger bounce on inverse ETF
        if have("bband_lower", "bband_lower_prev", "price_prev"):
            if d["price_prev"] <= d["bband_lower_prev"] and d["price"] > d["bband_lower"]:
                flags.append((8, "Bollinger bounce (inverse)"))

        # 9 — Above VWAP on inverse ETF
        if have("vwap") and d["price"] > d["vwap"]:
            flags.append((9, "Above VWAP (inverse)"))

    else:
        # ── REGULAR STOCK BUY FLAGS (bullish) ──

        # 1 — SMA-5 crosses above SMA-20
        if have("sma5", "sma5_prev", "sma20", "sma20_prev"):
            if d["sma5_prev"] < d["sma20_prev"] and d["sma5"] >= d["sma20"]:
                flags.append((1, "SMA crossover"))

        # 2 — Price above SMA-50 (daily)
        if have("sma50_daily") and d["price"] > d["sma50_daily"]:
            flags.append((2, "Above SMA-50"))

        # 3 — Volume surge > 1.5x 20-bar average
        if d["volume_avg_20"] > 0 and d["volume"] > 1.5 * d["volume_avg_20"]:
            flags.append((3, "Volume surge"))

        # 4 — Positive session momentum > +1%
        if have("open") and d["open"] > 0:
            if (d["price"] - d["open"]) / d["open"] > 0.01:
                flags.append((4, "Positive momentum"))

        # 5 — RSI oversold bounce (drops below 30, then rises above 30)
        if have("rsi", "rsi_prev"):
            if d["rsi_prev"] < 30 and d["rsi"] > 30:
                flags.append((5, "RSI oversold bounce"))

        # 6 — Bullish news/social catalyst
        if news_impact == "HIGH":
            flags.append((6, "News catalyst"))

        # 7 — MACD line crosses above signal line
        if have("macd", "macd_signal", "macd_prev", "macd_signal_prev"):
            if d["macd_prev"] < d["macd_signal_prev"] and d["macd"] >= d["macd_signal"]:
                flags.append((7, "MACD bullish cross"))

        # 8 — Price touches lower Bollinger Band then reverses up
        if have("bband_lower", "bband_lower_prev", "price_prev"):
            if d["price_prev"] <= d["bband_lower_prev"] and d["price"] > d["bband_lower"]:
                flags.append((8, "Bollinger bounce"))

        # 9 — Price crosses above VWAP
        if have("vwap") and d["price"] > d["vwap"]:
            flags.append((9, "Above VWAP"))

    return flags


def score_sell_flags(position, indicators, news_impact):
    """Returns (flag_number, flag_name) for the first triggered rule, or None.
    Uses trailing stop: tracks peak price, sells when price drops 1.5% from peak."""
    d = indicators
    entry = position["entry_price"]
    price = d["price"]
    gain = (price - entry) / entry

    # Update peak price tracking
    peak = position.get("peak_price", entry)
    if price > peak:
        position["peak_price"] = price
        peak = price

    def have(*keys):
        return all(d.get(k) is not None for k in keys)

    # 1 — Trailing stop: price dropped 1.5% from peak
    if peak > entry:  # only active once we're in profit
        drop_from_peak = (peak - price) / peak
        if drop_from_peak >= config.TRAILING_STOP_PCT:
            locked_gain = (price - entry) / entry
            return (1, f"Trailing stop ({locked_gain:+.2%}, peak was {((peak-entry)/entry):+.2%})")

    # 2 — Hard stop loss from entry
    if gain <= config.STOP_LOSS_PCT:
        return (2, f"Stop loss ({gain:+.2%})")

    if have("sma5", "sma5_prev", "sma20", "sma20_prev"):
        if d["sma5_prev"] > d["sma20_prev"] and d["sma5"] < d["sma20"]:
            return (3, "Bearish SMA cross")

    if have("price_prev") and d["price"] < d["price_prev"]:
        if d["volume_avg_20"] > 0 and d["volume"] > 2 * d["volume_avg_20"]:
            return (4, "Volume dump")

    if news_impact == "HIGH":
        return (5, "Negative catalyst")

    if position.get("bars_held", 0) >= config.MAX_HOLD_BARS and gain <= 0:
        return (6, f"Time decay ({position['bars_held']} bars)")

    if have("macd", "macd_signal", "macd_prev", "macd_signal_prev"):
        if d["macd_prev"] > d["macd_signal_prev"] and d["macd"] < d["macd_signal"]:
            return (7, "MACD bearish cross")

    if have("bband_upper") and d["price"] >= d["bband_upper"]:
        return (8, "Bollinger upper touch")

    if have("vwap") and d["price"] < d["vwap"]:
        return (9, "Below VWAP")

    return None


def passes_risk_controls(symbol, current_positions, daily_pnl, total_exposure):
    """Returns (can_buy: bool, reason: str)."""
    if daily_pnl <= config.DAILY_LOSS_PAUSE:
        return False, f"Daily loss limit hit (${daily_pnl:.2f})"

    if len(current_positions) >= config.MAX_POSITIONS:
        return False, f"Max positions reached ({len(current_positions)}/{config.MAX_POSITIONS})"

    if symbol in current_positions:
        return False, f"Already holding {symbol}"

    if total_exposure + config.MAX_TRADE_VALUE > config.MAX_TOTAL_EXPOSURE:
        return False, f"Would exceed ${config.MAX_TOTAL_EXPOSURE} exposure"

    return True, "OK"
