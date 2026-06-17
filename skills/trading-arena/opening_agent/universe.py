"""Universe / funnel — find pre-market DIRECTIONAL movers, then deep-evaluate
only those against the §R1 criteria (TRADING_AGENT.md).

The agent does NOT crunch 2-min bars for all ~16k US stocks. Stage 1 is a cheap
"what's moving with direction pre-market" screen that yields a small candidate
set; Stage 2 fetches 2-min bars for just those and runs the deterministic
classifier. Coverage (candidates in vs evaluated) is logged — never silently
truncated.

Mover sources are pluggable:
  - IBKRMovers  : IBKR server-side scanner (TOP_PERC_GAIN/LOSE) — whole market,
                  no per-symbol calls. PRODUCTION source. Needs the gateway up.
  - QuoteMovers : per-symbol Finnhub /quote over a provided seed list, kept for
                  dev/testing without the gateway (NOT a curated watchlist —
                  pass it whatever universe you want screened).
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
    list should be bounded; IBKRMovers is the real whole-market source."""

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


class IBKRMovers:
    """Production source: IBKR server-side scanner. Returns whole-market movers by
    percent gain/loss without per-symbol calls. Requires the IB Gateway running
    (infra/ib-gateway). Untested until the gateway is up — see note in
    OPENING_AGENT design."""

    def __init__(self, both_directions=True):
        self.both = both_directions

    def movers(self, limit=200):
        try:
            from ib_async import IB, ScannerSubscription
        except Exception as e:               # noqa: BLE001
            print(f"  [IBKRMovers] ib_async unavailable: {e}", file=sys.stderr)
            return []
        host = os.environ.get("IBKR_HOST", "127.0.0.1")
        port = int(os.environ.get("IBKR_PORT", "4002"))
        cid = int(os.environ.get("OPENING_SCANNER_CLIENT_ID", "23"))
        loc = os.environ.get("OPENING_SCAN_LOCATION", "STK.US.MAJOR")
        ib = IB()
        out = []
        try:
            ib.connect(host, port, clientId=cid, timeout=20)
            codes = ["TOP_PERC_GAIN"] + (["TOP_PERC_LOSE"] if self.both else [])
            # stockTypeFilter="CORP" = common stocks only -> excludes ALL ETFs
            # (incl. leveraged SOXL/TQQQ/TSLL), matching the strategy's intent of
            # individual-stock institutional footprints. Set "ALL" to re-include.
            stock_type = os.environ.get("OPENING_STOCK_TYPE_FILTER", "CORP")
            for code in codes:
                sub = ScannerSubscription(
                    instrument="STK", locationCode=loc, scanCode=code,
                    stockTypeFilter=stock_type,
                    abovePrice=float(os.environ.get("OPENING_MIN_PRICE", "5")),
                    aboveVolume=int(os.environ.get("OPENING_MIN_VOLUME", "100000")),
                )
                for row in ib.reqScannerData(sub, [], []):
                    c = row.contractDetails.contract
                    direction = 1 if code == "TOP_PERC_GAIN" else -1
                    out.append(Mover(c.symbol, 0.0, 0.0, 0.0, direction))
        except Exception as e:               # noqa: BLE001
            print(f"  [IBKRMovers] scanner failed: {e}", file=sys.stderr)
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
        # de-dupe preserving order
        seen, uniq = set(), []
        for m in out:
            if m.symbol not in seen:
                seen.add(m.symbol); uniq.append(m)
        return uniq[:limit]


def get_mover_source():
    """Pick the source from OPENING_MOVER_SOURCE (default 'ibkr')."""
    src = os.environ.get("OPENING_MOVER_SOURCE", "ibkr").lower()
    if src == "ibkr":
        return IBKRMovers()
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


def _ibkr_2min_bars(ib, symbol, duration="2 D"):
    """≥200 2-min bars via IBKR reqHistoricalData (~1s/symbol, delayed data ok).
    Returns bar dicts oldest→newest, or [] on failure."""
    from ib_async import Stock
    try:
        c = Stock(symbol.upper(), "SMART", "USD")
        ib.qualifyContracts(c)
        bars = ib.reqHistoricalData(c, endDateTime="", durationStr=duration,
                                    barSizeSetting="2 mins", whatToShow="TRADES",
                                    useRTH=False, formatDate=1)
        return [{"open": b.open, "high": b.high, "low": b.low, "close": b.close,
                 "volume": float(b.volume or 0), "date": b.date} for b in bars]
    except Exception:                          # noqa: BLE001
        return []


def _day_change_pct(bars):
    """Gap / pre-market move = latest price vs the prior session's close, computed
    from the 2-min bars we already have (no extra data call). Returns % or 0.0."""
    if not bars:
        return 0.0
    last = bars[-1]
    today = getattr(last.get("date"), "date", lambda: None)()
    prior_close = None
    for b in reversed(bars[:-1]):
        d = getattr(b.get("date"), "date", lambda: None)()
        if d and today and d < today:
            prior_close = b["close"]
            break
    if not prior_close:
        prior_close = bars[0]["close"]
    return round((last["close"] - prior_close) / prior_close * 100, 2) if prior_close else 0.0


def scan_ibkr(limit_movers=50, cfg=None):
    """Single-connection funnel: IBKR scanner for movers + IBKR historical for
    each mover's 2-min bars. ~20x faster than the Questrade path and one socket."""
    from ib_async import IB, ScannerSubscription, util
    util.patchAsyncio()
    host = os.environ.get("IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_PORT", "4002"))
    cid = int(os.environ.get("OPENING_SCANNER_CLIENT_ID", "23"))
    loc = os.environ.get("OPENING_SCAN_LOCATION", "STK.US.MAJOR")
    stype = os.environ.get("OPENING_STOCK_TYPE_FILTER", "CORP")
    both = os.environ.get("OPENING_ALLOW_SHORTS", "false").lower() == "true"
    ib = IB()
    try:
        ib.connect(host, port, clientId=cid, timeout=20)
        ib.reqMarketDataType(3)                # delayed data (free) fallback
        movers, seen = [], set()
        for code, direction in ([("TOP_PERC_GAIN", 1)]
                                + ([("TOP_PERC_LOSE", -1)] if both else [])):
            sub = ScannerSubscription(
                instrument="STK", locationCode=loc, scanCode=code,
                stockTypeFilter=stype,
                abovePrice=float(os.environ.get("OPENING_MIN_PRICE", "5")),
                aboveVolume=int(os.environ.get("OPENING_MIN_VOLUME", "100000")))
            for row in ib.reqScannerData(sub, [], []):
                s = row.contractDetails.contract.symbol
                if s not in seen:
                    seen.add(s); movers.append(Mover(s, 0.0, 0.0, 0.0, direction))
        movers = movers[:limit_movers]
        candidates = []
        for m in movers:
            bars = _ibkr_2min_bars(ib, m.symbol)
            m.pct_change = _day_change_pct(bars)       # real gap from the bars
            candidates.append(evaluate(m, bars=bars, cfg=cfg))
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
    usable = [c for c in candidates if c.state != "UNKNOWN"]
    print(f"  [opening.scan/ibkr] movers={len(movers)} usable={len(usable)} "
          f"(coverage: {len(usable)}/{len(movers)})", file=sys.stderr)
    return candidates


def scan_tv(limit_movers=50, cfg=None):
    """IBKR-FREE funnel: TradingView public screener for pre-market movers +
    TradingView chart (CDP) for each mover's real-time 2-min bars. No IB Gateway,
    no delayed data, no 2FA. This is the default path (OPENING_DATA_SOURCE=tv)."""
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


def scan(limit_movers=50, cfg=None, source=None):
    """Full funnel: movers → deep-evaluate → list[Candidate]. Logs coverage.
    Default OPENING_DATA_SOURCE=tv (IBKR-free, real-time TradingView). Set it to
    'ibkr' for the legacy gateway path, or pass an explicit `source` (quote)."""
    if source is None:
        ds = os.environ.get("OPENING_DATA_SOURCE", "tv").lower()
        if ds == "tv":
            return scan_tv(limit_movers=limit_movers, cfg=cfg)
        if ds == "ibkr":
            return scan_ibkr(limit_movers=limit_movers, cfg=cfg)
    source = source or get_mover_source()
    movers = source.movers(limit=limit_movers)
    candidates = [evaluate(m, cfg=cfg) for m in movers]
    usable = [c for c in candidates if c.state != "UNKNOWN"]
    print(f"  [opening.scan] movers={len(movers)} evaluated={len(candidates)} "
          f"usable={len(usable)} (coverage: {len(usable)}/{len(movers)})",
          file=sys.stderr)
    return candidates
