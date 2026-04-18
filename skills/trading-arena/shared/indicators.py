"""Technical indicator calculations for the Trading Arena."""
import math


def sma(prices, period):
    """Simple Moving Average."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def ema(prices, period):
    """Exponential Moving Average."""
    if len(prices) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(prices[:period]) / period
    for price in prices[period:]:
        result = (price - result) * multiplier + result
    return result


def rsi(prices, period=14):
    """Relative Strength Index (0-100)."""
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(prices, fast=12, slow=26, signal=9):
    """MACD line, signal line, histogram."""
    if len(prices) < slow + signal:
        return None, None, None
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    if fast_ema is None or slow_ema is None:
        return None, None, None
    macd_line = fast_ema - slow_ema
    # Calculate signal as EMA of MACD values
    macd_values = []
    for i in range(slow, len(prices)):
        f = ema(prices[:i + 1], fast)
        s = ema(prices[:i + 1], slow)
        if f and s:
            macd_values.append(f - s)
    signal_line = ema(macd_values, signal) if len(macd_values) >= signal else None
    histogram = (macd_line - signal_line) if signal_line else None
    return macd_line, signal_line, histogram


def bollinger_bands(prices, period=20, std_dev=2):
    """Bollinger Bands: (upper, middle, lower, bandwidth)."""
    if len(prices) < period:
        return None, None, None, None
    middle = sma(prices, period)
    variance = sum((p - middle) ** 2 for p in prices[-period:]) / period
    std = math.sqrt(variance)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = (upper - lower) / middle if middle else 0
    return upper, middle, lower, bandwidth


def vwap(prices, volumes):
    """Volume Weighted Average Price."""
    if not prices or not volumes or len(prices) != len(volumes):
        return None
    total_pv = sum(p * v for p, v in zip(prices, volumes))
    total_v = sum(volumes)
    return total_pv / total_v if total_v > 0 else None


def obv(prices, volumes):
    """On-Balance Volume."""
    if len(prices) < 2 or len(volumes) < 2:
        return None
    result = 0
    for i in range(1, len(prices)):
        if prices[i] > prices[i - 1]:
            result += volumes[i]
        elif prices[i] < prices[i - 1]:
            result -= volumes[i]
    return result


def atr(highs, lows, closes, period=14):
    """Average True Range."""
    if len(closes) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def adx(highs, lows, closes, period=14):
    """Average Directional Index (trend strength 0-100)."""
    if len(closes) < period * 2:
        return None
    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, len(closes)):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]
        plus_dm.append(high_diff if high_diff > low_diff and high_diff > 0 else 0)
        minus_dm.append(low_diff if low_diff > high_diff and low_diff > 0 else 0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)
    if len(tr_list) < period:
        return None
    atr_val = sum(tr_list[-period:]) / period
    if atr_val == 0:
        return 0
    plus_di = (sum(plus_dm[-period:]) / period) / atr_val * 100
    minus_di = (sum(minus_dm[-period:]) / period) / atr_val * 100
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0
    dx = abs(plus_di - minus_di) / di_sum * 100
    return dx


def zscore(values, lookback=20):
    """Z-score of the latest value relative to recent history."""
    if len(values) < lookback:
        return None
    window = values[-lookback:]
    mean = sum(window) / len(window)
    variance = sum((v - mean) ** 2 for v in window) / len(window)
    std = math.sqrt(variance) if variance > 0 else 0.0001
    return (values[-1] - mean) / std


def relative_volume(volumes, period=20):
    """Current volume relative to average."""
    if len(volumes) < period + 1:
        return None
    avg = sum(volumes[-period - 1:-1]) / period
    return volumes[-1] / avg if avg > 0 else 0


# === Support/Resistance and Candlestick Pattern Detection ===
# Added per 2026-04-12 analysis: 47 of 96 YouTube strategies use horizontal S/R
# as their primary "area of value" component, and 31 use candlestick patterns
# (hammer, engulfing) as their entry trigger. See STRATEGY_DIGEST.md.


def find_swing_points(highs, lows, lookback=3):
    """Identify swing highs and swing lows in recent price action.

    A swing high is a bar whose high is greater than the highs of N bars
    on either side. A swing low is the inverse.

    Returns: (swing_highs, swing_lows) — lists of (index, price) tuples
    """
    swing_highs = []
    swing_lows = []
    n = len(highs)
    if n < 2 * lookback + 1:
        return swing_highs, swing_lows
    for i in range(lookback, n - lookback):
        if all(highs[i] > highs[i - j] and highs[i] > highs[i + j] for j in range(1, lookback + 1)):
            swing_highs.append((i, highs[i]))
        if all(lows[i] < lows[i - j] and lows[i] < lows[i + j] for j in range(1, lookback + 1)):
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def find_sr_levels(highs, lows, lookback=3, cluster_pct=0.005):
    """Find horizontal support and resistance levels by clustering swing points.

    Levels within `cluster_pct` (default 0.5%) of each other get merged into
    one level. Returns the most-touched (strongest) levels first.

    Returns: list of (level_price, touch_count, type) where type is 'support' or 'resistance'
    """
    swing_highs, swing_lows = find_swing_points(highs, lows, lookback=lookback)

    def cluster(points, type_name):
        if not points:
            return []
        prices = sorted([p for _, p in points])
        clusters = []
        current = [prices[0]]
        for p in prices[1:]:
            if abs(p - current[0]) / current[0] <= cluster_pct:
                current.append(p)
            else:
                clusters.append((sum(current) / len(current), len(current), type_name))
                current = [p]
        clusters.append((sum(current) / len(current), len(current), type_name))
        return clusters

    levels = cluster(swing_highs, "resistance") + cluster(swing_lows, "support")
    # Sort by touch count desc (most touched = strongest level)
    levels.sort(key=lambda x: x[1], reverse=True)
    return levels


def nearest_sr_level(price, levels, max_distance_pct=0.015):
    """Find the closest S/R level to the current price (within max_distance).

    Returns (level_price, distance_pct, type) or None if nothing nearby.
    """
    closest = None
    min_dist = max_distance_pct
    for lvl_price, count, lvl_type in levels:
        if lvl_price <= 0:
            continue
        dist = abs(price - lvl_price) / lvl_price
        if dist < min_dist:
            min_dist = dist
            closest = (lvl_price, dist, lvl_type, count)
    return closest


def at_support(price, levels, max_distance_pct=0.01):
    """Check if price is at or just above a support level."""
    near = nearest_sr_level(price, levels, max_distance_pct)
    if near is None:
        return False
    lvl, dist, lvl_type, count = near
    return lvl_type == "support" and price >= lvl * 0.99


def at_resistance(price, levels, max_distance_pct=0.01):
    """Check if price is at or just below a resistance level."""
    near = nearest_sr_level(price, levels, max_distance_pct)
    if near is None:
        return False
    lvl, dist, lvl_type, count = near
    return lvl_type == "resistance" and price <= lvl * 1.01


def is_hammer(open_price, high, low, close):
    """Hammer candlestick pattern: small body near top, long lower wick.

    Bullish reversal signal — appears at bottoms after downtrends.
    Criteria: lower wick >= 2x body, small upper wick, body in upper third.
    """
    body = abs(close - open_price)
    full_range = high - low
    if full_range <= 0 or body <= 0:
        return False
    lower_wick = min(open_price, close) - low
    upper_wick = high - max(open_price, close)
    # Lower wick must be at least 2x the body
    if lower_wick < body * 2:
        return False
    # Upper wick must be small (less than body)
    if upper_wick > body:
        return False
    # Body should be in upper third of range
    body_position = (min(open_price, close) - low) / full_range
    return body_position >= 0.5


def is_bullish_engulfing(prev_open, prev_close, curr_open, curr_close):
    """Bullish engulfing: green candle that fully wraps prior red candle."""
    prev_red = prev_close < prev_open
    curr_green = curr_close > curr_open
    if not (prev_red and curr_green):
        return False
    # Current body must engulf previous body
    return curr_open <= prev_close and curr_close >= prev_open


def is_bearish_engulfing(prev_open, prev_close, curr_open, curr_close):
    """Bearish engulfing: red candle that fully wraps prior green candle."""
    prev_green = prev_close > prev_open
    curr_red = curr_close < curr_open
    if not (prev_green and curr_red):
        return False
    return curr_open >= prev_close and curr_close <= prev_open


def is_doji(open_price, high, low, close, body_threshold=0.1):
    """Doji: open and close nearly equal — indecision/reversal signal."""
    full_range = high - low
    if full_range <= 0:
        return False
    body = abs(close - open_price)
    return (body / full_range) <= body_threshold


def detect_candlestick_pattern(opens, highs, lows, closes):
    """Detect the most recent significant candlestick pattern.

    Returns the pattern name as a string, or None if nothing significant.
    Checks the most recent bar, with engulfing patterns needing 2 bars.
    """
    if not closes or len(closes) < 2:
        return None

    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]

    # Check current bar patterns
    if is_hammer(o, h, l, c):
        return "hammer"
    if is_doji(o, h, l, c):
        return "doji"

    # Check 2-bar patterns
    if len(closes) >= 2:
        po, pc = opens[-2], closes[-2]
        if is_bullish_engulfing(po, pc, o, c):
            return "bullish_engulfing"
        if is_bearish_engulfing(po, pc, o, c):
            return "bearish_engulfing"

    return None


def atr_stop_loss(price, atr_value, direction="long", multiplier=1.0):
    """Calculate ATR-based stop loss price.

    23 of 96 strategies use ATR for stop placement (more robust than fixed %).
    """
    if atr_value is None or atr_value <= 0:
        return None
    if direction == "long":
        return price - (atr_value * multiplier)
    else:
        return price + (atr_value * multiplier)


def atr_take_profit(price, atr_value, direction="long", multiplier=2.0):
    """Calculate ATR-based take profit price (default 2:1 R:R)."""
    if atr_value is None or atr_value <= 0:
        return None
    if direction == "long":
        return price + (atr_value * multiplier)
    else:
        return price - (atr_value * multiplier)
