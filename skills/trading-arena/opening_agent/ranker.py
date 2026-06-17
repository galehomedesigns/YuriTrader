"""Match-ranker — score each candidate 0..100 (best→worst match) and take top 10.

Blends the TRADING_AGENT.md §R1 criteria with the existing Yuri KPIs, per the
user's spec ("rank using the references in the file AND the existing KPIs").

Score = weighted sum, each component normalised 0..1:

  Spec criteria (the strategy's own logic — dominant weight):
    setup       0.30  MATCH(long/short)=1, MISMATCH=0.5, NO_PLAY=0
    tightness   0.20  how narrow the 20/200 SMA band is (1=tightest)
    power_bar   0.15  elephant=1.0, tail=0.7, none=0
    direction   0.10  pre-market move magnitude, capped (gap too far is penalised)

  Existing Yuri KPIs (confirmation — supporting weight):
    adx         0.10  trend strength (ADX/50, capped)
    rvol        0.08  relative volume (institutional participation)
    rsi_align   0.07  RSI agrees with the trade direction and isn't extreme

Weights are config (RANK_WEIGHTS env JSON) so you can retune without code edits.
Only TIGHT-state candidates can score the setup/tightness components — a WIDE or
UNKNOWN candidate is not a valid opening-power setup and floors near 0.
"""
import json
import os

DEFAULT_WEIGHTS = {
    "setup": 0.30, "tightness": 0.20, "power_bar": 0.15, "direction": 0.10,
    "adx": 0.10, "rvol": 0.08, "rsi_align": 0.07,
}
MAX_GAP_PCT = float(os.environ.get("OPENING_MAX_GAP_PCT", "8.0"))  # beyond = too wide


def _weights():
    raw = os.environ.get("RANK_WEIGHTS", "")
    if not raw:
        return DEFAULT_WEIGHTS
    try:
        w = json.loads(raw)
        return {**DEFAULT_WEIGHTS, **w}
    except (ValueError, TypeError):
        return DEFAULT_WEIGHTS


def _clamp01(x):
    return 0.0 if x is None else max(0.0, min(1.0, x))


def _setup_score(c):
    """MATCH if state TIGHT, location matches the power-bar direction (G4)."""
    if c.state != "TIGHT" or c.bar_signal == 0:
        return 0.0
    if c.bar_signal > 0 and c.location == "above":
        return 1.0
    if c.bar_signal < 0 and c.location == "below":
        return 1.0
    # power bar fights location → mismatch / patience play (still has potential)
    if c.location in ("above", "below"):
        return 0.5
    return 0.0


def _power_score(c):
    if {"bull_elephant", "bear_elephant"} & c.bar_tags:
        return 1.0
    if {"bottoming_tail", "topping_tail"} & c.bar_tags:
        return 0.7
    return 0.0


def _direction_score(c):
    """Pre-market move magnitude, rewarded up to MAX_GAP_PCT then penalised — a
    gap too far means it opens already WIDE (G6), which is not the play."""
    g = abs(c.pct_change or 0.0)
    if g <= 0:
        return 0.0
    if g <= MAX_GAP_PCT:
        return g / MAX_GAP_PCT
    # overshoot: decay back down past the threshold
    return max(0.0, 1.0 - (g - MAX_GAP_PCT) / MAX_GAP_PCT)


def _rsi_align(c):
    """RSI agrees with the intended direction and isn't already exhausted."""
    if c.rsi_14 is None:
        return 0.0
    r = c.rsi_14
    if c.bar_signal > 0:                 # long: want momentum, not overbought
        if r >= 80:
            return 0.2
        return _clamp01((r - 50) / 30)   # 50→0, 80→1
    if c.bar_signal < 0:                 # short: want weakness, not oversold
        if r <= 20:
            return 0.2
        return _clamp01((50 - r) / 30)
    return 0.0


def score(c):
    """Composite 0..100 with a per-component breakdown (for transparency/logging)."""
    w = _weights()
    comps = {
        "setup": _setup_score(c),
        "tightness": _clamp01(c.tightness),
        "power_bar": _power_score(c),
        "direction": _direction_score(c),
        "adx": _clamp01((c.adx_14 or 0) / 50.0),
        "rvol": _clamp01((c.rvol or 0) / 3.0),
        "rsi_align": _rsi_align(c),
    }
    total = sum(w[k] * comps[k] for k in w) * 100.0
    return round(total, 1), {k: round(v, 3) for k, v in comps.items()}


def rank(candidates, top_n=10, news=None, news_factor=0.0):
    """Score and sort candidates best→worst. Returns list of dicts ready for
    formatting: {rank, symbol, score, direction, verdict, components, kpis}.

    `news` (optional) is {symbol -> {'sentiment': -1..+1, ...}} from
    news_sentiment.batch(); `news_factor` is the MAX point budget it may move a
    score (e.g. 5 → at most ±5 on the 0..100 scale). News is a tie-breaker/nudge,
    never governing — the technical composite still decides the setup.
    """
    news = news or {}
    scored = []
    for c in candidates:
        s, comps = score(c)
        sent = float((news.get(c.symbol) or {}).get("sentiment", 0.0))
        news_adj = round(sent * news_factor, 1)          # bounded: sent∈[-1,1]
        final = round(s + news_adj, 1)
        verdict = ("LONG" if (_setup_score(c) == 1.0 and c.bar_signal > 0)
                   else "SHORT" if (_setup_score(c) == 1.0 and c.bar_signal < 0)
                   else "WATCH" if _setup_score(c) > 0 else "NO-PLAY")
        scored.append({
            "symbol": c.symbol, "score": final, "base_score": s,
            "news_adj": news_adj, "news_sentiment": round(sent, 2),
            "direction": verdict,
            "state": c.state, "location": c.location,
            "pct_change": round(c.pct_change or 0, 2),
            "tightness": round(c.tightness or 0, 3),
            "power": sorted(c.bar_tags) or ["none"],
            "components": comps,
            "kpis": {"rsi": c.rsi_14, "adx": c.adx_14, "rvol": c.rvol,
                     "candle": c.candlestick},
            "bars_seen": c.bars_seen, "note": c.note,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(scored[:top_n], 1):
        row["rank"] = i
    return scored[:top_n]
