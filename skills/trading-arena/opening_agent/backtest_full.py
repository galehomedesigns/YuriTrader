#!/usr/bin/env python3
"""Opening Power — FULL-STRATEGY 3-month backtest (extends paper_tracker.py).

Unlike paper_tracker.py (which models entry -> initial stop or cutoff only, no
trailing/adds), this drives the REAL OpeningEngine bar-by-bar over historical
2-min sessions, so it scores the COMPLETE exit logic: entry -> breakeven ratchet
-> push-trail -> the G9 add -> and either a hard cutoff flatten OR ride-mode
(keep trailing a breakeven-protected winner past the cutoff to a +RIDE_MAX_MIN
backstop). It reports three variants side by side so the ride-vs-flatten and
trail questions are answered with data, not a worked example:

  • naive    — entry -> initial stop or cutoff close   (the paper_tracker model)
  • flatten  — full engine, hard market-flatten at the cutoff (EOD flatten)
  • ride     — full engine, ride protected winners past the cutoff (EOD ride)

FAITHFULNESS CONTRACT (so results aren't fiction):
  • Uses the REAL OpeningEngine + classifier + indicators — zero reimplementation
    of strategy/indicator logic. The engine decides entry fills, stop moves, adds,
    pushes and stop-outs from the real bars; we only read its state + prices.
  • Sizing MIRRORS the live order path: a $OPENING_BT_POS_USD slot, HALF deployed
    at entry, the G9 add completing it to the full slot (the half-entry->add-to-full
    model now in advisory_monitor._stage_entries / _stage_add). Engine ticket qtys
    (risk-based) are intentionally ignored — we size in dollars like the broker does.
  • Long-only (matches the live default; MATCH_SHORT is logged, never traded).
  • Slippage = OPENING_BT_SLIP_PCT each side; $0 commission (Questrade).

HONEST LIMITS (printed on the dashboard, not buried):
  • The daily PRE-MARKET SCAN is NOT reconstructable offline (it's real-time
    movers/relative-volume/news, ranked top-N). So this runs the rule over a fixed
    broad universe every session — it tests the ENTRY/EXIT RULES + classifier gate,
    NOT the scan's selection/ranking edge or the news nudge.
  • Data = Questrade 2-min RTH candles (same source paper_tracker uses), which is
    not bit-identical to the live TradingView regular-session series used to arm.
  • Fills are modelled at the engine's trigger prices +/- slippage; real one-click
    manual confirms add latency this can't capture.

Usage:
  python3 backtest_full.py                 # fetch (cached) + backtest, write summary
  python3 backtest_full.py --days 90       # window length (default 90)
  python3 backtest_full.py --fetch-only    # just warm the candle cache
  python3 backtest_full.py --symbols A,B   # override the universe
Env knobs (own namespace, NOT the live trading flags):
  OPENING_BT_POS_USD (1000)  OPENING_BT_SLIP_PCT (0.0010)
  OPENING_BT_DAYS (90)       OPENING_BT_RIDE_MAX_MIN (90)
  OPENING_SESSION_CUTOFF_MIN (30, shared with live)
"""
import argparse
import json
import os
import sys
import time as _time
import types
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))            # skills/trading-arena


def _load_env():
    p = "/home/tonygale/openclaw/.env"
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k and v:
                    os.environ.setdefault(k, v)


_load_env()
from opening_agent.engine import OpeningEngine, IN_HALF, IN_FULL, FLAT  # noqa: E402
from opening_agent import classifier as C                              # noqa: E402
from opening_agent import universe as U                                # noqa: E402
from opening_agent import ranker as RANK                               # noqa: E402
from opening_agent import news_sentiment as NS                         # noqa: E402
import shared.indicators as ind                                        # noqa: E402

NEWS_FACTOR = float(os.environ.get("OPENING_NEWS_FACTOR", "5"))
MAX_TRADES = int(os.environ.get("OPENING_MAX_TRADES", "5"))
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")

ET = ZoneInfo("America/New_York")
LOGS = os.path.join(os.path.dirname(_HERE), "logs")
CACHE_DIR = os.path.join(LOGS, "backtest_cache")
SUMMARY = os.path.join(LOGS, "opening_backtest_summary.json")
SIDCACHE = os.path.join(CACHE_DIR, "_symbol_ids.json")

POS_USD = float(os.environ.get("OPENING_BT_POS_USD", "1000"))
SLIP = float(os.environ.get("OPENING_BT_SLIP_PCT", "0.0010"))
CUTOFF_MIN = int(os.environ.get("OPENING_SESSION_CUTOFF_MIN", "30"))
RIDE_MAX_MIN = int(os.environ.get("OPENING_BT_RIDE_MAX_MIN", "90"))
OPEN_T = time(9, 30)
RTH_END = time(16, 0)
THROTTLE_S = float(os.environ.get("OPENING_BT_THROTTLE_S", "0.25"))

# Curated liquid supplement — large/mid-caps + perennial momentum names — unioned
# with the live scan cache to form a broad (200+) universe. Not the live scan
# (which can't be reconstructed); a fixed stand-in so the rule self-selects.
CURATED = [
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "AMZN", "META", "GOOGL", "NFLX", "AVGO",
    "INTC", "MU", "QCOM", "TXN", "AMAT", "LRCX", "KLAC", "ADI", "MRVL", "ON",
    "SMCI", "ARM", "PLTR", "SNOW", "CRWD", "PANW", "DDOG", "NET", "ZS", "MDB",
    "SHOP", "SQ", "PYPL", "COIN", "HOOD", "SOFI", "AFRM", "UPST", "RBLX", "U",
    "UBER", "LYFT", "ABNB", "DASH", "F", "GM", "RIVN", "LCID", "NIO", "XPEV",
    "BABA", "PDD", "JD", "DIS", "WBD", "PARA", "ROKU", "SPOT", "PINS", "SNAP",
    "BAC", "JPM", "WFC", "C", "GS", "MS", "SCHW", "V", "MA", "AXP",
    "XOM", "CVX", "OXY", "SLB", "HAL", "DVN", "MRO", "FANG", "COP", "PXD",
    "BA", "GE", "CAT", "DE", "MMM", "HON", "LMT", "RTX", "UPS", "FDX",
    "PFE", "MRNA", "BNTX", "LLY", "JNJ", "ABBV", "BMY", "GILD", "AMGN", "VRTX",
    "WMT", "TGT", "COST", "HD", "LOW", "NKE", "SBUX", "MCD", "CMG", "DKNG",
    "T", "VZ", "TMUS", "CMCSA", "CVS", "UNH", "CI", "HUM", "WBA", "DAL",
    "AAL", "UAL", "CCL", "NCLH", "RCL", "MGM", "WYNN", "LVS", "PENN", "CHWY",
    "MARA", "RIOT", "CLSK", "BITF", "HUT", "IREN", "WULF", "CIFR", "BTBT", "CAN",
    "AI", "BBAI", "SOUN", "IONQ", "RGTI", "QBTS", "PATH", "GTLB", "S", "FROG",
    "ENPH", "FSLR", "RUN", "SEDG", "PLUG", "FCEL", "BE", "CHPT", "QS", "STEM",
    "DNA", "RKLB", "ASTS", "ACHR", "JOBY", "LUNR", "RDW", "SPCE", "DJT", "GME",
    "AMC", "BBBY", "CVNA", "W", "WOLF", "VRT", "DELL", "HPE", "HPQ", "WDC",
    "STX", "ANET", "CSCO", "ORCL", "IBM", "NOW", "ADBE", "CRM", "INTU", "WDAY",
    "TTD", "ZM", "DOCU", "TWLO", "OKTA", "TEAM", "HUBS", "BILL", "ESTC", "CFLT",
]


# ── Candle fetch: explicit weekly date-range paging + disk cache + throttle ─────
def _executor():
    from shared.questrade_executor import QuestradeExecutor
    return QuestradeExecutor()


def _load_sid_cache():
    try:
        return json.load(open(SIDCACHE))
    except (OSError, ValueError):
        return {}


def _save_sid_cache(c):
    os.makedirs(CACHE_DIR, exist_ok=True)
    json.dump(c, open(SIDCACHE, "w"))


def _get_with_backoff(qe, path, params, tries=5):
    """qe._get with transient-error backoff. The executor wraps HTTP errors in
    QuestradeExecutorError (message carries 'HTTP <code>'), so match on the text
    rather than urllib.error.HTTPError, which it never re-raises."""
    delay = 1.0
    for attempt in range(tries):
        try:
            _time.sleep(THROTTLE_S)
            return qe._get(path, params=params)
        except Exception as e:                              # noqa: BLE001
            msg = str(e)
            transient = any(f"HTTP {c}" in msg for c in (429, 500, 502, 503))
            if transient and attempt < tries - 1:
                _time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise
    return {}


def fetch_series(sym, start_dt, end_dt, qe, sid_cache, cache_only=False):
    """Weekly-paged 2-min RTH candles for one symbol over [start,end]. Cached to
    disk per symbol; returns oldest->newest list of {et,open,high,low,close,vol}.
    A symbol already cached through >= end date is reused without refetching.
    cache_only: on a cache miss, return [] instead of hitting the network — for
    guaranteed-offline re-runs (pin --end to the cached date so misses are rare)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cpath = os.path.join(CACHE_DIR, f"{sym.replace(':','_')}.json")
    want_through = end_dt.date().isoformat()
    want_from = start_dt.date().isoformat()
    if os.path.exists(cpath):
        try:
            cached = json.load(open(cpath))
            # reuse only if BOTH ends of the window match (else a shorter cached
            # window would be silently reused for a longer request).
            if (cached.get("through") == want_through and cached.get("from") == want_from
                    and cached.get("bars")):
                return [_rehydrate(b) for b in cached["bars"]]
        except (OSError, ValueError):
            pass
    if cache_only:
        return []
    # resolve symbol id (cached)
    if sym not in sid_cache:
        try:
            sid_cache[sym] = qe.resolve_symbol_id(sym)
        except Exception:                                  # noqa: BLE001
            sid_cache[sym] = None
        _save_sid_cache(sid_cache)
    sid = sid_cache.get(sym)
    if not sid:
        return []
    # 30-day windows: explicit date ranges return the FULL span (verified up to
    # ~45d/14k bars), unlike the count= path which truncates. Fewer requests/symbol.
    bars, seen = [], set()
    win_start = start_dt
    while win_start < end_dt:
        win_end = min(win_start + timedelta(days=30), end_dt)
        params = {
            "startTime": win_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "endTime": win_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "interval": "TwoMinutes",
        }
        try:
            raw = _get_with_backoff(qe, f"/v1/markets/candles/{sid}", params).get("candles", [])
        except Exception as e:                             # noqa: BLE001
            print(f"  [{sym}] fetch error {win_start.date()}: {e}", file=sys.stderr)
            raw = []
        for c in raw:
            t = c.get("end") or c.get("start")
            if not t:
                continue
            try:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET)
            except ValueError:
                continue
            if not (OPEN_T <= dt.time() < RTH_END):        # RTH only (match live session)
                continue
            key = dt.isoformat()
            if key in seen:
                continue
            seen.add(key)
            bars.append({"et": dt, "open": float(c["open"]), "high": float(c["high"]),
                         "low": float(c["low"]), "close": float(c["close"]),
                         "volume": float(c.get("volume", 0) or 0)})
        win_start = win_end
    bars.sort(key=lambda b: b["et"])
    json.dump({"through": want_through, "from": want_from,
               "bars": [{**b, "et": b["et"].isoformat()} for b in bars]},
              open(cpath, "w"))
    return bars


def _rehydrate(b):
    return {**b, "et": datetime.fromisoformat(b["et"])}


# ── Historical point-in-time news (Finnhub company-news, look-ahead-safe) ───────
NEWS_CACHE_DIR = os.path.join(CACHE_DIR, "news")


def _news_asof(symbol, day):
    """Sentiment for `symbol` as of the morning of `day`, using ONLY headlines
    timestamped before that session's 9:30 ET open (no look-ahead). Scored with
    the SAME news_sentiment keyword logic the live ranker uses; cached per
    (symbol, day). Fail-safe neutral (0.0) on any error / no key."""
    if not FINNHUB_KEY:
        return 0.0
    os.makedirs(NEWS_CACHE_DIR, exist_ok=True)
    cp = os.path.join(NEWS_CACHE_DIR, f"{symbol.replace(':','_')}_{day}.json")
    if os.path.exists(cp):
        try:
            return float(json.load(open(cp)).get("sentiment", 0.0))
        except (OSError, ValueError):
            pass
    open_epoch = datetime.combine(day, OPEN_T, ET).timestamp()
    q = urllib.parse.urlencode({"symbol": symbol.upper(),
                                "from": (day - timedelta(days=3)).isoformat(),
                                "to": day.isoformat(), "token": FINNHUB_KEY})
    try:
        _time.sleep(1.1)                                   # Finnhub free tier ~60/min
        req = urllib.request.Request(f"https://finnhub.io/api/v1/company-news?{q}",
                                     headers={"User-Agent": "opening-backtest/1"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception:                                      # noqa: BLE001 — neutral on any error
        data = []
    items = [{"headline": it.get("headline", "")}
             for it in (data if isinstance(data, list) else [])
             if (it.get("datetime") or 0) < open_epoch]    # strictly pre-open headlines
    sent, meta = NS.headline_sentiment(symbol, items)
    json.dump({"sentiment": sent, "n_pre_open": len(items),
               "pos": meta["pos"], "neg": meta["neg"]}, open(cp, "w"))
    return sent


# ── Session simulation: drive the real engine, size like the live broker path ──
def _sma_pair(prior, bar1):
    closes = [b["close"] for b in prior] + [bar1["close"]]
    return ind.sma(closes, 20), ind.sma(closes, 200)


def _slot(entry_fill):
    """Live sizing: full slot shares, half deployed at entry, add completes it."""
    full = int(POS_USD // entry_fill)
    entry_qty = max(1, full // 2)
    return full, entry_qty


def _pnl(buys, exit_px):
    """buys = [(qty, price)]; long flattened at exit_px. Returns (qty, avg, pnl)."""
    qty = sum(q for q, _ in buys)
    cost = sum(q * p for q, p in buys)
    avg = cost / qty if qty else 0.0
    return qty, avg, (exit_px - avg) * qty


def simulate_session(sym, all_bars, day, pm_gap=None, build_cand=False):
    """Drive the engine over one session; return {naive, flatten, ride} outcomes
    plus the decision. all_bars = full RTH series; day = session date.
    pm_gap=(min,max): if set, apply a PRE-MARKET PROXY filter — keep the session
    only if the opening gap (9:30 open vs prior session close) is in [min,max]%,
    approximating the live scan's moderate-gap band.
    build_cand: also attach a ranker Candidate (`_cand`) + `gap` so a daily top-N
    ranking (technical composite + news nudge) can be applied downstream."""
    day_bars = [b for b in all_bars if b["et"].date() == day]
    oi = next((i for i, b in enumerate(day_bars) if b["et"].time() >= OPEN_T), None)
    if oi is None:
        return None
    bar1 = day_bars[oi]
    prior = [b for b in all_bars if b["et"] < bar1["et"]]
    if len(prior) < 200:
        return {"symbol": sym, "date": str(day), "skip": "insufficient_history"}
    gap = None
    if pm_gap is not None or build_cand:
        prior_day = [b for b in prior if b["et"].date() < day]
        if not prior_day:
            return {"symbol": sym, "date": str(day), "match": False, "decision": "PMSKIP_NOPRIOR"}
        gap = (bar1["open"] - prior_day[-1]["close"]) / prior_day[-1]["close"] * 100
        if pm_gap is not None and not (pm_gap[0] <= gap <= pm_gap[1]):
            return {"symbol": sym, "date": str(day), "match": False,
                    "decision": "PMSKIP_GAP", "gap": round(gap, 2)}
    smf, sms = _sma_pair(prior, bar1)
    v = C.classify_opening(sym, bar1, prior, smf, sms)
    if v.decision != "MATCH_LONG":
        return {"symbol": sym, "date": str(day), "match": False, "decision": v.decision}
    cand = None
    if build_cand:
        try:
            mover = types.SimpleNamespace(symbol=sym, direction=1, pct_change=round(gap or 0, 2))
            cand = U.evaluate(mover, bars=prior + [bar1])
        except Exception:                              # noqa: BLE001 — ranking is best-effort
            cand = None

    cutoff_ts = datetime.combine(day, OPEN_T, ET).timestamp() + CUTOFF_MIN * 60
    ride_ts = cutoff_ts + RIDE_MAX_MIN * 60
    session = day_bars[oi:]                       # bar1 .. end of RTH day
    entry_lvl = C.entry_level_long(bar1)
    stop_lvl = C.stop_level_long(bar1)

    # ---- naive variant: entry takeout -> initial stop or cutoff close ----------
    naive = _sim_naive(sym, day, bar1, entry_lvl, stop_lvl, session, cutoff_ts)
    # ---- full-engine variants: flatten vs ride ---------------------------------
    flat = _sim_engine(sym, day, bar1, prior, smf, sms, session, cutoff_ts, ride=False)
    ride = _sim_engine(sym, day, bar1, prior, smf, sms, session, cutoff_ts, ride=True,
                       ride_ts=ride_ts)
    return {"symbol": sym, "date": str(day), "match": True, "decision": "MATCH_LONG",
            "naive": naive, "flatten": flat, "ride": ride,
            "gap": round(gap, 2) if gap is not None else None, "_cand": cand}


def _sim_naive(sym, day, bar1, entry_lvl, stop_lvl, session, cutoff_ts):
    entered = exit_px = reason = None
    in_window = [b for b in session if b["et"].timestamp() <= cutoff_ts + 1]
    for b in in_window[1:]:
        if entered is None:
            if C.takeout_long(bar1, b):
                entered = entry_lvl * (1 + SLIP)
            continue
        if b["low"] <= stop_lvl:
            exit_px, reason = stop_lvl * (1 - SLIP), "stop"
            break
    if entered is None:
        return {"triggered": False}
    if exit_px is None:
        exit_px, reason = in_window[-1]["close"] * (1 - SLIP), "cutoff"
    full, eq = _slot(entered)
    qty, avg, pnl = _pnl([(full, entered)], exit_px)        # naive = full slot at entry
    return _trade(qty, avg, exit_px, reason, adds=0)


def _sim_engine(sym, day, bar1, prior, smf, sms, session, cutoff_ts, ride, ride_ts=None):
    """Faithful: OpeningEngine decides fills/adds/trailing/stops from real bars.
    Sizing mirrors the live half-entry -> add-to-full $-slot path."""
    eng = OpeningEngine(sym, account_equity=50000.0, cfg={"risk_per_trade": 0.01})
    eng.on_bar1(bar1, prior, smf, sms)
    if eng.side <= 0 or eng.state not in ("ARMED",):
        return {"triggered": False}                        # not a long arm

    buys, exit_px, reason, adds = [], None, None, 0
    full = eq = None
    prev_filled = 0
    for b in session[1:]:
        ts = b["et"].timestamp()
        past_cutoff = ts > cutoff_ts + 1
        # stop feeding at the right horizon for this variant
        if not ride and past_cutoff:
            break
        if ride and ride_ts is not None and ts > ride_ts + 1:
            break

        was_armed = (eng.state == "ARMED")
        tickets = eng.on_bar(b, complete=True)

        # entry just filled this bar (ARMED -> IN_HALF): deploy the half slot
        if was_armed and eng.state in (IN_HALF, IN_FULL) and not buys:
            entry_fill = eng.entry_price * (1 + SLIP)
            full, eq = _slot(entry_fill)
            buys.append((eq, entry_fill))
            prev_filled = eq

        # the G9 add fired (adds incremented): complete to the full slot
        if buys and eng.adds > adds:
            adds = eng.adds
            add_qty = max(0, full - sum(q for q, _ in buys))
            if add_qty > 0:
                add_fill = b["close"] * (1 + SLIP)
                buys.append((add_qty, add_fill))

        # stop hit (engine -> FLAT) this bar
        if buys and eng.state == FLAT:
            exit_px, reason = eng.stop_price * (1 - SLIP), "stop"
            break

        # ride mode past the cutoff: only keep going if breakeven-protected
        if ride and past_cutoff and buys and eng.state in (IN_HALF, IN_FULL):
            protected = (eng.stop_price is not None and eng.entry_price is not None
                         and eng.stop_price >= eng.entry_price)
            if not protected:
                exit_px, reason = b["close"] * (1 - SLIP), "cutoff"
                break

    if not buys:
        return {"triggered": False}
    if exit_px is None:
        # reached the horizon still holding -> flatten at the last seen close
        last = session[-1]
        for b in session[1:]:
            if not ride and b["et"].timestamp() > cutoff_ts + 1:
                break
            last = b
        exit_px = last["close"] * (1 - SLIP)
        reason = "ride-backstop" if ride else "cutoff"
    qty, avg, pnl = _pnl(buys, exit_px)
    return _trade(qty, avg, exit_px, reason, adds)


def _trade(qty, avg, exit_px, reason, adds):
    pct = (exit_px - avg) / avg * 100 if avg else 0.0
    return {"triggered": True, "qty": qty, "avg_entry": round(avg, 4),
            "exit": round(exit_px, 4), "reason": reason, "adds": adds,
            "pct": round(pct, 3), "pnl": round((exit_px - avg) * qty, 2)}


# ── Universe + aggregation ─────────────────────────────────────────────────────
def build_universe(override=None):
    if override:
        return list(dict.fromkeys(s.strip().upper() for s in override if s.strip()))
    syms = list(CURATED)
    try:
        cache = os.path.join(LOGS, "opening_scan_latest.json")
        syms += [r["symbol"] for r in json.load(open(cache)).get("ranked", [])]
    except (OSError, ValueError, KeyError):
        pass
    # normalise (strip EXCHANGE: prefixes) + dedupe, preserve order
    norm = []
    for s in syms:
        s = s.split(":")[-1].strip().upper()
        if s and s not in norm:
            norm.append(s)
    return norm


def _scorecard(trades, key):
    t = [x[key] for x in trades if x.get(key, {}).get("triggered")]
    wins = [x for x in t if x["pnl"] > 0]
    net = sum(x["pnl"] for x in t)
    pct = sum(x["pct"] for x in t)
    return {
        "triggered": len(t),
        "wins": len(wins),
        "win_rate": round(100 * len(wins) / len(t), 1) if t else 0.0,
        "net_pnl": round(net, 2),
        "sum_pct": round(pct, 3),
        "avg_pct": round(pct / len(t), 3) if t else 0.0,
        "avg_pnl": round(net / len(t), 2) if t else 0.0,
        "rides_past_cutoff": sum(1 for x in t if x.get("reason") == "ride-backstop"),
    }


def _apply_topn(matched, use_news, by_gap=False):
    """Reproduce the live funnel's last stage: each day, rank the matched
    candidates and keep the top OPENING_MAX_TRADES. Returns (selected_keys,
    news_stats).

    Default ranking is the REAL technical composite (+ optional ±NEWS_FACTOR news
    nudge). With by_gap=True, rank instead by opening-gap size — the one robust
    predictor — to test whether the composite ranker is diluting the gap edge.
    News is only fetched on days where matches exceed the cap (the only days
    ranking can change the trade set) — cheap + look-ahead-safe."""
    from collections import defaultdict
    by_day = defaultdict(list)
    for r in matched:
        if r.get("_cand") is not None:
            by_day[r["date"]].append(r)
    selected, stats = set(), {"days_capped": 0, "news_fetched": 0, "news_nonzero": 0}
    for day_str, rows in by_day.items():
        if len(rows) <= MAX_TRADES:                        # cap doesn't bind → take all
            for r in rows:
                selected.add((r["symbol"], r["date"]))
            continue
        stats["days_capped"] += 1
        if by_gap:                                         # rank by the robust predictor
            ranked = sorted(rows, key=lambda r: (r.get("gap") or 0.0), reverse=True)
            for r in ranked[:MAX_TRADES]:
                selected.add((r["symbol"], day_str))
            continue
        d = datetime.fromisoformat(day_str).date()
        news = {}
        if use_news:
            for r in rows:
                s = _news_asof(r["symbol"], d)
                news[r["symbol"]] = {"sentiment": s}
                stats["news_fetched"] += 1
                stats["news_nonzero"] += 1 if s else 0
        ranked = RANK.rank([r["_cand"] for r in rows], top_n=MAX_TRADES,
                           news=news, news_factor=(NEWS_FACTOR if use_news else 0.0))
        for x in ranked[:MAX_TRADES]:
            selected.add((x["symbol"], day_str))
    return selected, stats


def main():
    ap = argparse.ArgumentParser()
    # Questrade serves 2-min candles only ~60-65 days back; 90d (true 3 months)
    # isn't available at this granularity from this source. Default to the
    # faithful 60-day window and label it honestly rather than fabricate older data.
    ap.add_argument("--days", type=int, default=int(os.environ.get("OPENING_BT_DAYS", "60")))
    ap.add_argument("--symbols", default="")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--premarket-gap", action="store_true",
                    help="apply the pre-market proxy: keep only sessions whose opening "
                         "gap is in the scan's [min,max]%% band")
    ap.add_argument("--rank-topn", action="store_true",
                    help="apply the daily top-N ranking (OPENING_MAX_TRADES) via the real "
                         "ranker's technical composite — the live funnel's last stage")
    ap.add_argument("--rank-by-gap", action="store_true",
                    help="when capping to top-N, rank the day's candidates by opening-gap "
                         "size (the one robust predictor) instead of the technical composite; "
                         "implies --rank-topn. Mutually exclusive with --news.")
    ap.add_argument("--news", action="store_true",
                    help="include the ±OPENING_NEWS_FACTOR point-in-time news nudge in the "
                         "ranking (implies --rank-topn); reports the news-vs-no-news delta")
    ap.add_argument("--end", default="",
                    help="window end date YYYY-MM-DD (default today). Pin to the cached "
                         "date to run fully offline (no Questrade fetch).")
    ap.add_argument("--cache-only", action="store_true",
                    help="never hit the network: on a cache miss, skip the symbol instead "
                         "of fetching. Use with --end <cache date> for a guaranteed-offline run.")
    ap.add_argument("--limit", type=int, default=0, help="cap universe size (debug)")
    args = ap.parse_args()
    if args.news:
        args.rank_topn = True
    if args.rank_by_gap:
        args.rank_topn = True
        if args.news:
            ap.error("--rank-by-gap and --news are mutually exclusive (gap ranking ignores news)")
    pm_gap = None
    if args.premarket_gap:
        pm_gap = (float(os.environ.get("OPENING_SCAN_MIN_GAP_PCT", "1")),
                  float(os.environ.get("OPENING_SCAN_MAX_GAP_PCT", "6")))
    pm_gap = None
    if args.premarket_gap:
        pm_gap = (float(os.environ.get("OPENING_SCAN_MIN_GAP_PCT", "1")),
                  float(os.environ.get("OPENING_SCAN_MAX_GAP_PCT", "6")))

    end_dt = datetime.now(ET).replace(hour=20, minute=0, second=0, microsecond=0)
    if args.end:
        y, m, d = (int(x) for x in args.end.split("-"))
        end_dt = end_dt.replace(year=y, month=m, day=d)
    start_dt = end_dt - timedelta(days=args.days)
    universe = build_universe(args.symbols.split(",") if args.symbols else None)
    if args.limit:
        universe = universe[:args.limit]
    print(f"[backtest] universe={len(universe)} symbols  window={start_dt.date()}..{end_dt.date()} "
          f"  pos=${POS_USD:.0f}  slip={SLIP*100:.2f}%  cutoff={CUTOFF_MIN}m  ride_max={RIDE_MAX_MIN}m",
          file=sys.stderr)

    qe = _executor()
    sid_cache = _load_sid_cache()
    all_rows, fetched = [], 0
    for n, sym in enumerate(universe, 1):
        try:
            series = fetch_series(sym, start_dt, end_dt, qe, sid_cache,
                                  cache_only=args.cache_only)
        except Exception as e:                             # noqa: BLE001
            print(f"  [{sym}] SKIP ({e})", file=sys.stderr)
            continue
        fetched += 1
        if n % 10 == 0 or n == len(universe):
            print(f"[backtest] {n}/{len(universe)} fetched ({sym}: {len(series)} bars)", file=sys.stderr)
        if args.fetch_only or len(series) < 201:
            continue
        days = sorted({b["et"].date() for b in series if b["et"].time() >= OPEN_T})
        for d in days:
            row = simulate_session(sym, series, d, pm_gap=pm_gap, build_cand=args.rank_topn)
            if row and (row.get("match") or row.get("skip")):
                all_rows.append(row)

    if args.fetch_only:
        print(f"[backtest] fetch-only done: {fetched}/{len(universe)} symbols cached", file=sys.stderr)
        return

    matched = [r for r in all_rows if r.get("match")]
    # ── Daily top-N ranking (live funnel's last stage). Optionally news-nudged. ──
    select_label, news_effect, news_stats = None, None, None
    if args.rank_topn:
        pre_n = len(matched)
        keys, news_stats = _apply_topn(matched, use_news=args.news, by_gap=args.rank_by_gap)
        if args.news:                                  # isolate the news effect
            keys_nonews, _ = _apply_topn(matched, use_news=False)
            changed = len(keys.symmetric_difference(keys_nonews)) // 2
            news_effect = {"trades_changed_by_news": changed,
                           "days_cap_binds": news_stats["days_capped"],
                           "news_fetched": news_stats["news_fetched"],
                           "news_nonzero": news_stats["news_nonzero"]}
        matched = [r for r in matched if (r["symbol"], r["date"]) in keys]
        if args.rank_by_gap:
            select_label = f"top-{MAX_TRADES}/day, ranked by opening-gap size"
        else:
            select_label = (f"top-{MAX_TRADES}/day, ranked by composite"
                            + (f" + news ±{NEWS_FACTOR:g}" if args.news else " (no news)"))
        print(f"[backtest] top-N ranking: {pre_n} matches -> {len(matched)} selected "
              f"({news_stats['days_capped']} days the cap bound)", file=sys.stderr)
    summary = {
        "updated": datetime.now(ET).strftime("%Y-%m-%d %H:%M %Z"),
        "window": {"start": start_dt.date().isoformat(), "end": end_dt.date().isoformat(),
                   "days": args.days},
        "config": {"pos_usd": POS_USD, "slippage_pct": SLIP, "cutoff_min": CUTOFF_MIN,
                   "ride_max_min": RIDE_MAX_MIN, "universe_size": len(universe),
                   "premarket_filter": (f"opening gap {pm_gap[0]:g}-{pm_gap[1]:g}%"
                                        if pm_gap else None),
                   "selection": select_label, "max_trades": MAX_TRADES,
                   "news_factor": NEWS_FACTOR if args.news else None},
        "coverage": {"symbols_fetched": fetched, "sessions_scored": len(all_rows),
                     "long_matches": len(matched)},
        "news_effect": news_effect,
        "variants": {k: _scorecard(matched, k) for k in ("naive", "flatten", "ride")},
        "trades": [
            {"symbol": r["symbol"], "date": r["date"],
             **{f"{k}_pnl": r[k].get("pnl") for k in ("naive", "flatten", "ride") if r[k].get("triggered")},
             **{f"{k}_pct": r[k].get("pct") for k in ("naive", "flatten", "ride") if r[k].get("triggered")},
             "ride_reason": r["ride"].get("reason"), "adds": r["ride"].get("adds", 0)}
            for r in matched if r["ride"].get("triggered") or r["flatten"].get("triggered")
        ],
    }
    os.makedirs(LOGS, exist_ok=True)
    json.dump(summary, open(SUMMARY, "w"), indent=2, default=str)
    print(f"[backtest] wrote {SUMMARY}", file=sys.stderr)
    for k in ("naive", "flatten", "ride"):
        s = summary["variants"][k]
        print(f"  {k:8} trig={s['triggered']:3}  win%={s['win_rate']:5}  "
              f"net=${s['net_pnl']:+10.2f}  avg%={s['avg_pct']:+.3f}", file=sys.stderr)


if __name__ == "__main__":
    main()
