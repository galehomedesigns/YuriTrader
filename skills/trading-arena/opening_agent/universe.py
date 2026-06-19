"""Universe / funnel — find pre-market DIRECTIONAL movers, then deep-evaluate
only those against the §R1 criteria (TRADING_AGENT.md).

The agent does NOT crunch 2-min bars for all ~16k US stocks. Stage 1 is a cheap
"what's moving with direction pre-market" screen that yields a small candidate
set; Stage 2 fetches 2-min bars for just those and runs the deterministic
classifier. Coverage (candidates in vs evaluated) is logged — never silently
truncated.

Production path is scan_tv (OPENING_DATA_SOURCE=tv): TradingView's public
screener for pre-market movers + TradingView (CDP) for each mover's real-time
2-min bars. No external gateway, no delayed data, no 2FA.

QuoteMovers (per-symbol Finnhub /quote over a provided seed list) is kept only
for dev/testing without the live feed, via OPENING_MOVER_SOURCE=quote.
"""
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import indicators
from opening_agent import classifier as C


@dataclass
class Mover:
    symbol: str
    last: float
    prev_close: float
    pct_change: float
    direction: int                 # +1 up, -1 down


@dataclass
class Candidate:
    """A mover after 2-min deep evaluation — carries everything the ranker needs."""
    symbol: str
    direction: int
    pct_change: float
    price: float = 0.0
    sma_fast: float = None
    sma_slow: float = None
    state: str = "UNKNOWN"
    tightness: float = None
    location: str = "unknown"
    bar_tags: set = field(default_factory=set)
    bar_signal: int = 0
    bars_seen: int = 0
    note: str = ""
    # Existing Yuri KPIs (computed on the same 2-min series) for the ranker
    rsi_14: float = None
    adx_14: float = None
    rvol: float = None
    atr_14: float = None
    candlestick: str = None


# ── Stage 1: mover sources ───────────────────────────────────────────────────
class QuoteMovers:
    """Dev/test source: screen a provided symbol list via Finnhub /quote and keep
    those moving in a clear direction. Rate-limited (~60/min free) so the seed
    list should be bounded; scan_tv is the production whole-market source."""

    def __init__(self, seed_symbols, min_abs_pct=1.0):
        self.seed = seed_symbols
        self.min_abs_pct = float(min_abs_pct)
        self.key = os.environ.get("FINNHUB_KEY", "")

    def movers(self, limit=200):
        import json, time, urllib.request
        out = []
        for sym in self.seed:
            if not self.key:
                break
            try:
                url = f"https://finnhub.io/api/v1/quote?symbol={sym}&token={self.key}"
                with urllib.request.urlopen(url, timeout=8) as r:
                    q = json.loads(r.read())
            except Exception:
                continue
            last, pc, dp = q.get("c") or 0, q.get("pc") or 0, q.get("dp") or 0
            if last and abs(dp) >= self.min_abs_pct:
                out.append(Mover(sym, last, pc, dp, 1 if dp > 0 else -1))
            time.sleep(1.05)        # stay under 60/min
        out.sort(key=lambda m: abs(m.pct_change), reverse=True)
        return out[:limit]


def get_mover_source():
    """Dev/testing mover source. Production uses scan_tv (OPENING_DATA_SOURCE=tv);
    this only serves OPENING_MOVER_SOURCE=quote over a seed list."""
    src = os.environ.get("OPENING_MOVER_SOURCE", "quote").lower()
    if src == "quote":
        seed = [s.strip().upper() for s in
                os.environ.get("OPENING_SEED_SYMBOLS", "").split(",") if s.strip()]
        return QuoteMovers(seed)
    raise ValueError(f"Unknown OPENING_MOVER_SOURCE={src!r}")


# ── Stage 2: deep-evaluate a mover on 2-min bars ─────────────────────────────
def _fetch_2min_bars(symbol, count=210):
    """≥200 2-min bars via Questrade (native TwoMinutes). Returns list of bar
    dicts oldest→newest, or [] on failure."""
    try:
        from shared.questrade_executor import QuestradeExecutor
        ex = QuestradeExecutor()
        candles = ex.get_candles(symbol, interval="TwoMinutes", count=count)
    except Exception as e:                   # noqa: BLE001
        print(f"  [{symbol}] 2-min fetch failed: {e}", file=sys.stderr)
        return []
    bars = []
    for c in candles or []:
        try:
            bars.append({"open": float(c["open"]), "high": float(c["high"]),
                         "low": float(c["low"]), "close": float(c["close"]),
                         "volume": float(c.get("volume", 0) or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    return bars


def evaluate(mover, bars=None, cfg=None):
    """Run the §R1 criteria on a mover's 2-min bars → Candidate (or None if no
    usable data). Pass `bars` to evaluate without a live fetch (testing)."""
    bars = bars if bars is not None else _fetch_2min_bars(mover.symbol)
    if len(bars) < 200:
        return Candidate(mover.symbol, mover.direction, mover.pct_change,
                         bars_seen=len(bars), note="insufficient 2-min history (<200)")
    closes = [b["close"] for b in bars]
    price = closes[-1]
    sma_fast = indicators.sma(closes, 20)
    sma_slow = indicators.sma(closes, 200)
    # Volatility yardstick for the ATR-normalized TIGHT gate (no-op in flat-% mode).
    atr_val = C.atr(bars, {**C.DEFAULTS, **(cfg or {})}["atr_len"])
    state, _ = C.market_state(sma_fast, sma_slow, price, cfg, atr_val=atr_val)
    last_bar, prior = bars[-1], bars[:-1]

    # Existing Yuri KPIs on the same 2-min series (reuse the arena's AssetData so
    # RSI/ADX/rvol/candlestick are literally the same metrics used elsewhere).
    rsi = adx = rvol = atr = None
    candle = None
    try:
        from shared.market_scanner import AssetData
        ad = AssetData(symbol=mover.symbol, asset_type="stock", price=price,
                       closes=closes, highs=[b["high"] for b in bars],
                       lows=[b["low"] for b in bars], opens=[b["open"] for b in bars],
                       volumes=[b["volume"] for b in bars])
        ad.compute_indicators()
        rsi, adx, rvol, atr, candle = (ad.rsi_14, ad.adx_14, ad.rvol,
                                       ad.atr_14, ad.candlestick_pattern)
    except Exception:                        # noqa: BLE001 — KPIs are best-effort
        pass

    return Candidate(
        symbol=mover.symbol, direction=mover.direction, pct_change=mover.pct_change,
        price=price, sma_fast=sma_fast, sma_slow=sma_slow, state=state,
        tightness=C.tightness(sma_fast, sma_slow, price, cfg, atr_val=atr_val),
        location=C.location(bars[-1]["open"], sma_fast, sma_slow),
        bar_tags=C.classify_bar(last_bar, prior, cfg),
        bar_signal=C.bar_signal(last_bar, prior, cfg),
        bars_seen=len(bars),
        rsi_14=rsi, adx_14=adx, rvol=rvol, atr_14=atr, candlestick=candle,
    )


def scan_tv(limit_movers=None, cfg=None):
    """Production funnel: TradingView public screener for pre-market movers +
    TradingView chart (CDP) for each mover's real-time 2-min bars. No external
    gateway, no delayed data, no 2FA. The default path (OPENING_DATA_SOURCE=tv).
    How many movers get deep-evaluated = OPENING_SCAN_LIMIT (default 50)."""
    if limit_movers is None:
        limit_movers = int(os.environ.get("OPENING_SCAN_LIMIT", "50"))
    from opening_agent import tv_screener, tv_bars
    both = os.environ.get("OPENING_ALLOW_SHORTS", "false").lower() == "true"
    min_price = float(os.environ.get("OPENING_MIN_PRICE", "5"))
    min_pmvol = int(os.environ.get("OPENING_MIN_PREMARKET_VOLUME", "50000"))
    min_gap = float(os.environ.get("OPENING_SCAN_MIN_GAP_PCT", "1"))
    max_gap = float(os.environ.get("OPENING_SCAN_MAX_GAP_PCT", "6"))
    raw = tv_screener.movers(limit=limit_movers, min_price=min_price,
                             min_premarket_vol=min_pmvol, min_gap=min_gap, max_gap=max_gap)
    if both:
        raw += tv_screener.movers(limit=limit_movers, min_price=min_price,
                                  min_premarket_vol=min_pmvol, min_gap=min_gap,
                                  max_gap=max_gap, losers=True)
    raw = raw[:limit_movers]
    full_syms = [f'{m["exchange"]}:{m["symbol"]}' for m in raw]
    bars_map = tv_bars.fetch_bars(full_syms, min_bars=200)
    candidates = []
    for m in raw:
        full = f'{m["exchange"]}:{m["symbol"]}'
        mv = Mover(m["symbol"], m["close"], 0.0, m["premarket_change"], m["direction"])
        candidates.append(evaluate(mv, bars=bars_map.get(full, []), cfg=cfg))
    usable = [c for c in candidates if c.state != "UNKNOWN"]
    print(f"  [opening.scan/tv] movers={len(raw)} usable={len(usable)} "
          f"(coverage: {len(usable)}/{len(raw)})", file=sys.stderr)
    return candidates


def scan(limit_movers=None, cfg=None, source=None):
    """Full funnel: movers → deep-evaluate → list[Candidate]. Logs coverage.
    Production path is real-time TradingView (OPENING_DATA_SOURCE=tv). Pass an
    explicit `source` (e.g. QuoteMovers) for dev/testing. limit_movers defaults to
    OPENING_SCAN_LIMIT (50)."""
    if limit_movers is None:
        limit_movers = int(os.environ.get("OPENING_SCAN_LIMIT", "50"))
    if source is None and os.environ.get("OPENING_DATA_SOURCE", "tv").lower() == "tv":
        return scan_tv(limit_movers=limit_movers, cfg=cfg)
    source = source or get_mover_source()
    movers = source.movers(limit=limit_movers)
    candidates = [evaluate(m, cfg=cfg) for m in movers]
    usable = [c for c in candidates if c.state != "UNKNOWN"]
    print(f"  [opening.scan] movers={len(movers)} evaluated={len(candidates)} "
          f"usable={len(usable)} (coverage: {len(usable)}/{len(movers)})",
          file=sys.stderr)
    return candidates
