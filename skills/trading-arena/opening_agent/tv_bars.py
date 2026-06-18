"""Python bridge to tv_bars_fetch.js — real-time 2-min bars from TradingView via
CDP, in the dict format universe.py/engine consume.

    from opening_agent import tv_bars
    bars_map = tv_bars.fetch_bars(["NASDAQ:AAPL", "NYSE:F"])   # {sym: [bar,...]}
    bars     = tv_bars.fetch_one("AAPL")                        # [bar,...]

bar = {"open","high","low","close","volume","date"} oldest->newest; date = unix
epoch seconds (int) of the bar (used only for de-dup in the advisory loop).
Failed/missing symbols come back as []. The node side runs DEDICATED background
TradingView tabs (parallel), isolated from the trading/order chart.
"""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
_FETCH = os.path.join(HERE, "tv_bars_fetch.js")


def _port():
    return os.environ.get("OPENING_TV_CDP_PORT", "9225")


def _parallel():
    return os.environ.get("OPENING_CDP_PARALLEL_TABS", "3")


def fetch_bars(symbols, min_bars=200, res="2", timeout=240):
    """symbols: list of 'EXCHANGE:TICKER' or bare 'TICKER'. Returns
    {requested_symbol: [bar dicts]}; failed/missing -> []."""
    symbols = [s for s in symbols if s]
    if not symbols:
        return {}
    try:
        p = subprocess.run(
            ["node", _FETCH, "--symbols", ",".join(symbols),
             "--min", str(min_bars), "--res", str(res), "--port", _port(),
             "--parallel", _parallel()],
            capture_output=True, text=True, timeout=timeout)
        lines = [l for l in p.stdout.splitlines() if l.strip().startswith("{")]
        data = json.loads(lines[-1]) if lines else {"results": []}
    except Exception:                                  # noqa: BLE001
        return {s: [] for s in symbols}
    out = {s: [] for s in symbols}
    for r in data.get("results", []):
        sym = r.get("symbol")
        out[sym] = [{"open": b["open"], "high": b["high"], "low": b["low"],
                     "close": b["close"], "volume": b.get("volume", 0) or 0,
                     "date": b["time"]} for b in (r.get("bars") or [])]
    return out


def fetch_one(symbol, min_bars=200, res="2"):
    """Bars for a single symbol (bare ticker or EXCHANGE:TICKER), or []."""
    return fetch_bars([symbol], min_bars=min_bars, res=res).get(symbol, [])
