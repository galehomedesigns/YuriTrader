"""Deterministic bar & state classification — TRADING_AGENT.md §7.1.

Every pattern in the Opening Power strategy is fully determined by OHLCV numbers
(rule G17: never from chart images). This module is pure: no I/O, no network,
no globals. Feed it bars (dicts with open/high/low/close[/volume]) and SMA values;
it returns classifications. Everything here is unit-tested and back-testable.

A "bar" is a dict: {"open","high","low","close","volume"} (volume optional).
SMAs are computed by the caller (market_scanner already has sma()).
"""
import os
from dataclasses import dataclass, field

# ── Config defaults (every value is a TRADING_AGENT.md §7 config key) ──────────
DEFAULTS = {
    "tight_threshold": 0.0025,    # max (|SMA20-SMA200| / price) to call TIGHT (0.25%)
    "elephant_body_mult": 2.0,    # body >= mult * avgBody(20)
    "elephant_body_frac": 0.70,   # body >= frac * range
    "tail_ratio": 2.0,            # tail >= ratio * body
    "tail_body_zone": 0.25,       # opposite tail <= zone * range
    "small_bar_mult": 0.5,        # body <= mult * avgBody(5)
    "elephant_lookback": 20,      # avgBody window for elephants
    "small_lookback": 5,          # avgBody window for "little" bars
    "trade_offset": 0.01,         # $ offset for all trigger levels (G5)
}

# Tunable without code edits. The opening-movers re-aim (2026-06-15) loosens TIGHT
# so coiled-but-moving names actually qualify — 0.25% was too strict given the
# funnel. Set OPENING_TIGHT_THRESHOLD in .env (e.g. 0.005 = 0.5%).
DEFAULTS["tight_threshold"] = float(
    os.environ.get("OPENING_TIGHT_THRESHOLD", DEFAULTS["tight_threshold"]))

# TIGHT can be judged two ways. Legacy: a flat percentage of price (over-selects
# sleepy large-caps, rejects coiled-but-volatile movers). OPENING_TIGHT_MODE=atr
# normalizes to the stock's OWN volatility — tight iff |SMA20-SMA200| <=
# tight_atr_mult * ATR(atr_len) — so "coiled" means coiled for THIS name.
DEFAULTS["tight_mode"] = os.environ.get("OPENING_TIGHT_MODE", "pct").lower()
DEFAULTS["tight_atr_mult"] = float(os.environ.get("OPENING_TIGHT_ATR_MULT", "1.0"))
DEFAULTS["atr_len"] = int(os.environ.get("OPENING_ATR_LEN", "14"))

# Whether MATCH requires the TIGHT/coil state (G3). Default TRUE = live behaviour
# (live is unaffected). Set OPENING_REQUIRE_TIGHT=false ONLY to run the backtest's
# gate on/off A/B — it keeps the power-bar + location logic but drops the coil
# requirement, isolating exactly what the coil gate contributes.
DEFAULTS["require_tight"] = os.environ.get("OPENING_REQUIRE_TIGHT", "true").lower() == "true"

# ── Strategy-profile knobs (set by profiles.py / .env; defaults = current live) ──
# loc_mode: "open_band" (current — first bar's OPEN vs the SMA20/200 band) or
#   "close_slow" (sweet-spot — the bar's CLOSE vs the slow/200 SMA only).
# exit_mode / rr_target / entry_fraction are consumed by engine.py; they live here
# so engine._c() picks them up through C.DEFAULTS without extra plumbing.
DEFAULTS["require_trend_align"] = os.environ.get("OPENING_REQUIRE_TREND_ALIGN", "false").lower() == "true"
DEFAULTS["loc_mode"] = os.environ.get("OPENING_LOC_MODE", "open_band").lower()
DEFAULTS["exit_mode"] = os.environ.get("OPENING_EXIT_MODE", "push_trail").lower()
DEFAULTS["rr_target"] = float(os.environ.get("OPENING_TARGET_RR", "3.0"))
DEFAULTS["entry_fraction"] = float(os.environ.get("OPENING_ENTRY_FRACTION", "0.5"))


# ── Primitive bar geometry ────────────────────────────────────────────────────
def body(bar):
    return abs(bar["close"] - bar["open"])

def bar_range(bar):
    return bar["high"] - bar["low"]

def upper_tail(bar):
    return bar["high"] - max(bar["open"], bar["close"])

def lower_tail(bar):
    return min(bar["open"], bar["close"]) - bar["low"]

def is_green(bar):
    return bar["close"] > bar["open"]

def is_red(bar):
    return bar["close"] < bar["open"]

def avg_body(bars, n):
    """Mean body over the last n COMPLETED bars (the n bars prior to 'now')."""
    window = bars[-n:] if n else bars
    if not window:
        return 0.0
    return sum(body(b) for b in window) / len(window)


def true_range(bar, prev_close):
    return max(bar["high"] - bar["low"], abs(bar["high"] - prev_close),
               abs(bar["low"] - prev_close))


def atr(bars, n=14):
    """Average True Range over the last n bars (simple mean of TR) — the volatility
    yardstick for the ATR-normalized TIGHT gate. Returns None if <2 bars."""
    if not bars or len(bars) < 2:
        return None
    trs = [true_range(bars[i], bars[i - 1]["close"]) for i in range(1, len(bars))]
    if not trs:
        return None
    w = trs[-n:] if n else trs
    return sum(w) / len(w)


# ── Market state (TIGHT / WIDE) and location ─────────────────────────────────
def market_state(sma_fast, sma_slow, price, cfg=None, atr_val=None):
    """Return ('TIGHT'|'WIDE', direction) where direction is +1/-1 (sign of
    SMA20-SMA200) and 0 when exactly equal. TIGHT iff the SMA separation is small:
    flat-% mode -> sep/price <= tight_threshold; ATR mode (tight_mode=='atr' with
    an atr_val supplied) -> sep/ATR <= tight_atr_mult."""
    cfg = {**DEFAULTS, **(cfg or {})}
    if not price or sma_fast is None or sma_slow is None:
        return ("UNKNOWN", 0)
    sep = abs(sma_fast - sma_slow)
    direction = (sma_fast > sma_slow) - (sma_fast < sma_slow)  # +1 / -1 / 0
    if cfg.get("tight_mode") == "atr" and atr_val:
        tight = (sep / atr_val) <= cfg["tight_atr_mult"]
    else:                                      # flat-% (legacy) or no ATR available
        tight = (sep / price) <= cfg["tight_threshold"]
    return ("TIGHT" if tight else "WIDE", direction)


def tightness(sma_fast, sma_slow, price, cfg=None, atr_val=None):
    """0..1 where 1 = SMAs identical (tightest), 0 = at/over the TIGHT cutoff. Used
    by the ranker. Honors the same mode as market_state (flat-% or ATR-normalized).
    None if inputs missing."""
    if not price or sma_fast is None or sma_slow is None:
        return None
    cfg = {**DEFAULTS, **(cfg or {})}
    if cfg.get("tight_mode") == "atr" and atr_val:
        ratio = (abs(sma_fast - sma_slow) / atr_val) / cfg["tight_atr_mult"]
    else:
        ratio = (abs(sma_fast - sma_slow) / price) / cfg["tight_threshold"]
    return max(0.0, 1.0 - ratio) if ratio <= 1.0 else 0.0


def location(open_price, sma_fast, sma_slow):
    """Where the (first) bar opens relative to a TIGHT SMA band.
    'above' (bullish), 'below' (bearish), or 'inside'."""
    if open_price is None or sma_fast is None or sma_slow is None:
        return "unknown"
    hi, lo = max(sma_fast, sma_slow), min(sma_fast, sma_slow)
    if open_price > hi:
        return "above"
    if open_price < lo:
        return "below"
    return "inside"


def location_close_slow(close_price, sma_slow):
    """Sweet-spot location: the first bar's CLOSE vs the slow (200) SMA only.
    'above' = close > slow (bullish), 'below' = close < slow, else 'inside'.
    (The 20-SMA and the open are ignored — this is the looser 'coiled-but-moving'
    gate found by the parameter sweep.)"""
    if close_price is None or sma_slow is None:
        return "unknown"
    if close_price > sma_slow:
        return "above"
    if close_price < sma_slow:
        return "below"
    return "inside"


# ── Power bars ────────────────────────────────────────────────────────────────
def classify_bar(bar, prior_bars, cfg=None):
    """Classify a single completed bar against its prior context.
    Returns a set of tags from: bull_elephant, bear_elephant, bottoming_tail,
    topping_tail, small. (A bar can be e.g. both a tail and small.)"""
    cfg = {**DEFAULTS, **(cfg or {})}
    tags = set()
    b, r = body(bar), bar_range(bar)
    if r <= 0:
        return tags
    avg20 = avg_body(prior_bars, cfg["elephant_lookback"])
    avg5 = avg_body(prior_bars, cfg["small_lookback"])

    # Elephants: large body, both vs recent bodies and vs own range.
    if avg20 > 0 and b >= cfg["elephant_body_mult"] * avg20 and \
            b >= cfg["elephant_body_frac"] * r:
        tags.add("bull_elephant" if is_green(bar) else
                 "bear_elephant" if is_red(bar) else None)

    # Tails: long rejection wick, small body parked in the opposite zone. Body may
    # be 0 (doji) — then `tail >= ratio*body` is trivially true and the opposite-
    # zone test does the discriminating (a symmetric doji fails both zone tests).
    if lower_tail(bar) >= cfg["tail_ratio"] * b and \
            upper_tail(bar) <= cfg["tail_body_zone"] * r:
        tags.add("bottoming_tail")              # bullish (color irrelevant)
    if upper_tail(bar) >= cfg["tail_ratio"] * b and \
            lower_tail(bar) <= cfg["tail_body_zone"] * r:
        tags.add("topping_tail")                # bearish

    # "Little" bar (for the add / color game).
    if avg5 > 0 and b <= cfg["small_bar_mult"] * avg5:
        tags.add("small")

    tags.discard(None)
    return tags


def bar_signal(bar, prior_bars, cfg=None):
    """Net directional signal of a bar's power-bar tags: +1 bullish, -1 bearish,
    0 none. Elephant dominates; otherwise tail decides."""
    tags = classify_bar(bar, prior_bars, cfg)
    if "bull_elephant" in tags:
        return 1
    if "bear_elephant" in tags:
        return -1
    if "bottoming_tail" in tags and "topping_tail" not in tags:
        return 1
    if "topping_tail" in tags and "bottoming_tail" not in tags:
        return -1
    return 0


# ── Triggers: takeout / pause / push (intra-bar aware) ───────────────────────
def takeout_long(level_bar, probe_bar, cfg=None):
    """True if probe_bar trades >= level_bar.high + offset (a long takeout)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    return probe_bar["high"] >= level_bar["high"] + cfg["trade_offset"]

def takeout_short(level_bar, probe_bar, cfg=None):
    cfg = {**DEFAULTS, **(cfg or {})}
    return probe_bar["low"] <= level_bar["low"] - cfg["trade_offset"]

def entry_level_long(bar1, cfg=None):
    cfg = {**DEFAULTS, **(cfg or {})}
    return bar1["high"] + cfg["trade_offset"]

def entry_level_short(bar1, cfg=None):
    cfg = {**DEFAULTS, **(cfg or {})}
    return bar1["low"] - cfg["trade_offset"]

def stop_level_long(bar1, cfg=None):
    cfg = {**DEFAULTS, **(cfg or {})}
    return bar1["low"] - cfg["trade_offset"]      # one-bar loss (G7)

def stop_level_short(bar1, cfg=None):
    cfg = {**DEFAULTS, **(cfg or {})}
    return bar1["high"] + cfg["trade_offset"]


@dataclass
class PushState:
    """Tracks push/pause progression for the profit-taker + ratchet stop (R6/R4)."""
    direction: int                 # +1 long, -1 short
    trade_extreme: float           # best trade-high (long) / trade-low (short) so far
    pauses: int = 0
    pushes: int = 0
    paused_since_push: bool = False

    def update(self, completed_bar):
        """Feed each COMPLETED bar. Long: a bar failing to make a new trade-high is
        a pause; a new trade-high after >=1 pause is a push. Short mirrors."""
        extreme = completed_bar["high"] if self.direction > 0 else completed_bar["low"]
        made_new = (extreme > self.trade_extreme) if self.direction > 0 \
            else (extreme < self.trade_extreme)
        if made_new:
            if self.paused_since_push:
                self.pushes += 1
                self.paused_since_push = False
            self.trade_extreme = extreme
        else:
            self.pauses += 1
            self.paused_since_push = True
        return self


# ── Opening-bar verdict (R2) ─────────────────────────────────────────────────
@dataclass
class Verdict:
    symbol: str
    decision: str                  # MATCH_LONG | MATCH_SHORT | MISMATCH | NO_PLAY
    bar_tags: set = field(default_factory=set)
    location: str = "unknown"
    state: str = "UNKNOWN"
    reason: str = ""


def classify_opening(symbol, bar1, prior_bars, sma_fast, sma_slow, cfg=None):
    """R2 verdict for the first bar. MATCH requires TIGHT state + a power bar +
    matching location (G3/G4). Direction must agree (positive bar in positive
    location → long). Anything else is MISMATCH (patience play, §5) or NO_PLAY."""
    cfg = {**DEFAULTS, **(cfg or {})}
    atr_val = atr((prior_bars or []) + [bar1], cfg["atr_len"])
    state, _dir = market_state(sma_fast, sma_slow, bar1["open"], cfg, atr_val=atr_val)
    if cfg.get("loc_mode") == "close_slow":
        loc = location_close_slow(bar1["close"], sma_slow)
    else:
        loc = location(bar1["open"], sma_fast, sma_slow)
    tags = classify_bar(bar1, prior_bars, cfg)
    sig = bar_signal(bar1, prior_bars, cfg)

    if cfg.get("require_tight", True) and state != "TIGHT":
        return Verdict(symbol, "NO_PLAY", tags, loc, state,
                       "state not TIGHT (G3)")
    if loc == "inside" or loc == "unknown":
        return Verdict(symbol, "NO_PLAY", tags, loc, state,
                       f"location {loc}")
    # Trend-align gate (subtractive, default off): require the 20-SMA on the correct side
    # of the 200-SMA for the direction — consumes the SMA20-vs-200 direction `_dir` the
    # classifier otherwise discards. Removes counter-trend opening pops (best in chop).
    if cfg.get("require_trend_align", False):
        if sig > 0 and not (_dir > 0):
            return Verdict(symbol, "NO_PLAY", tags, loc, state, "trend not aligned (SMA20<SMA200) (G3b)")
        if sig < 0 and not (_dir < 0):
            return Verdict(symbol, "NO_PLAY", tags, loc, state, "trend not aligned (SMA20>SMA200) (G3b)")
    if sig > 0 and loc == "above":
        return Verdict(symbol, "MATCH_LONG", tags, loc, state,
                       "bullish bar in bullish location")
    if sig < 0 and loc == "below":
        return Verdict(symbol, "MATCH_SHORT", tags, loc, state,
                       "bearish bar in bearish location")
    if sig == 0:
        return Verdict(symbol, "NO_PLAY", tags, loc, state, "no power bar")
    # power bar present but fights the location → patience/removal play (§5)
    return Verdict(symbol, "MISMATCH", tags, loc, state,
                   "bar direction opposes location (wait for removal)")
