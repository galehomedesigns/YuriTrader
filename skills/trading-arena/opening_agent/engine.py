"""Execution engine (R2-R7) — opening-range state machine. signal_only.

Spec: TRADING_AGENT.md §3 (roles R2-R7), §4 (rules), §6 (state machine).

SCAFFOLD STATUS: this implements the decision logic as a deterministic state
machine that consumes 2-min bars (with intra-bar high/low updates) and EMITS
order tickets. It is signal_only (G15): it NEVER transmits an order — it returns
OrderTicket objects for a human/host to act on. Going live (auto_execute) requires
(a) a real-time bar feed (IBKR reqRealTimeBars) the cron architecture lacks, and
(b) explicit arming. Not armed. No broker import here.

The host drives it:
    eng = OpeningEngine(symbol, narrow_band, account_equity, cfg)
    tickets = eng.on_bar1(bar1, prior_bars, sma_fast, sma_slow)   # R2 -> arm
    for bar in stream:           # each (possibly forming) 2-min bar
        tickets += eng.on_bar(bar, complete=is_complete)
    tickets += eng.on_cutoff()   # R7
"""
import math
from dataclasses import dataclass, field

from opening_agent import classifier as C


@dataclass
class OrderTicket:
    """A fully-specified, UNTRANSMITTED order (G15). The host decides whether to
    send it. 'rule' records which governing rule produced it (G14)."""
    symbol: str
    side: str                 # BUY / SELL
    order_type: str           # STP / STP_LMT / LMT / MKT
    qty: int
    price: float
    reason: str
    rule: str


# Engine states (TRADING_AGENT.md §6)
PRE = "PRE_MARKET"; ARMED = "ARMED"; WAITING = "WAITING"; STAND_DOWN = "STAND_DOWN"
IN_HALF = "IN_TRADE_HALF"; IN_FULL = "IN_TRADE_FULL"; FLAT = "FLAT"


@dataclass
class OpeningEngine:
    symbol: str
    account_equity: float = 50000.0
    cfg: dict = field(default_factory=dict)

    # runtime
    state: str = PRE
    side: int = 0                 # +1 long / -1 short
    bar1: dict = None
    entry_price: float = None
    stop_price: float = None
    shares: int = 0               # full target size
    filled: int = 0               # shares currently on
    adds: int = 0
    push: "C.PushState" = None
    journal: list = field(default_factory=list)
    _pending_red: dict = None     # first counter-color bar awaiting removal (R5)

    def _c(self, k):
        return {**C.DEFAULTS,
                "risk_per_trade": 0.01, "initial_fraction": 0.5, "max_adds": 2,
                **self.cfg}[k]

    def _log(self, msg, rule):
        self.journal.append({"state": self.state, "rule": rule, "msg": msg})

    # ── R2: classify bar 1 → arm ─────────────────────────────────────────────
    def on_bar1(self, bar1, prior_bars, sma_fast, sma_slow):
        v = C.classify_opening(self.symbol, bar1, prior_bars, sma_fast, sma_slow, self.cfg)
        self.bar1 = bar1
        if v.decision == "MATCH_LONG":
            self.side, self.state = 1, ARMED
        elif v.decision == "MATCH_SHORT":
            self.side, self.state = -1, ARMED
        elif v.decision == "MISMATCH":
            self.state = WAITING
            self._pending_red = bar1
            self._log(v.reason, "G11")
            return []
        else:
            self.state = STAND_DOWN
            self._log(v.reason, "G3/G4")
            return []

        # Size from the one-bar stop distance (R4/G8).
        entry = C.entry_level_long(bar1, self.cfg) if self.side > 0 \
            else C.entry_level_short(bar1, self.cfg)
        stop = C.stop_level_long(bar1, self.cfg) if self.side > 0 \
            else C.stop_level_short(bar1, self.cfg)
        self.entry_price, self.stop_price = entry, stop
        self.shares = self._size(entry, stop)
        self._log(f"armed {v.decision} entry={entry} stop={stop} shares={self.shares}",
                  "R3")
        # The resting stop-entry order (half the position fires on trigger).
        half = max(1, int(self.shares * self._c("initial_fraction")))
        return [OrderTicket(self.symbol, "BUY" if self.side > 0 else "SELL",
                            "STP", half, entry, "armed opening breakout", "G5")]

    def _size(self, entry, stop):
        risk_dollars = self.account_equity * self._c("risk_per_trade")
        dist = abs(entry - stop)
        return max(0, math.floor(risk_dollars / dist)) if dist > 0 else 0

    # ── R3-R7: per-bar drive ─────────────────────────────────────────────────
    def on_bar(self, bar, complete=True):
        if self.state == ARMED:
            return self._try_entry(bar)
        if self.state in (IN_HALF, IN_FULL):
            out = self._manage_stop(bar)
            if self.state == FLAT:
                return out
            if complete:
                out += self._manage_adds_and_pushes(bar)
            return out
        if self.state == WAITING and complete:
            return self._try_removal(bar)
        return []

    def _try_entry(self, bar):
        hit = (C.takeout_long(self.bar1, bar, self.cfg) if self.side > 0
               else C.takeout_short(self.bar1, bar, self.cfg))
        if not hit:
            return []
        half = max(1, int(self.shares * self._c("initial_fraction")))
        self.filled = half
        self.state = IN_HALF
        self.push = C.PushState(direction=self.side,
                                trade_extreme=bar["high"] if self.side > 0 else bar["low"])
        self._log(f"entry filled {half}@~{self.entry_price}; stop {self.stop_price}", "R3")
        # protective stop placed immediately on fill (R4/G7)
        return [OrderTicket(self.symbol, "SELL" if self.side > 0 else "BUY",
                            "STP", half, self.stop_price, "protective one-bar stop", "G7")]

    def _manage_stop(self, bar):
        stopped = (bar["low"] <= self.stop_price if self.side > 0
                   else bar["high"] >= self.stop_price)
        if stopped:
            self.state = FLAT
            self._log(f"stop hit @{self.stop_price} — flat, done for day", "G13")
            return [OrderTicket(self.symbol, "SELL" if self.side > 0 else "BUY",
                                "MKT", self.filled, self.stop_price,
                                "stop hit — flatten, no re-entry", "G7")]
        return []

    def _manage_adds_and_pushes(self, bar):
        out = []
        # Push/pause tracking → ratchet stop (R6/R4/G16).
        prev_push = self.push.pushes
        self.push.update(bar)
        if self.push.pushes > prev_push:
            new_stop = (bar["low"] - self._c("trade_offset") if self.side > 0
                        else bar["high"] + self._c("trade_offset"))
            # ratchet only in favor (G16); floor at breakeven after push 1
            if (self.side > 0 and new_stop > self.stop_price) or \
               (self.side < 0 and new_stop < self.stop_price):
                self.stop_price = new_stop
                out.append(OrderTicket(self.symbol, "SELL" if self.side > 0 else "BUY",
                                       "STP", self.filled, new_stop,
                                       f"ratchet stop after push {self.push.pushes}", "G16"))
            if self.push.pushes == 2:
                # rest profit orders ahead at the projected push-3 area (R6/G10)
                target = (bar["high"] + (bar["high"] - bar["low"]) if self.side > 0
                          else bar["low"] - (bar["high"] - bar["low"]))
                out.append(OrderTicket(self.symbol, "SELL" if self.side > 0 else "BUY",
                                       "LMT", self.filled, round(target, 2),
                                       "push-2: rest profit orders at push-3 area", "G10"))

        # The add: first counter-color bar, removed by a same-direction takeout (R5/G9).
        if self.state == IN_HALF and self.adds < self._c("max_adds"):
            counter = (C.is_red(bar) if self.side > 0 else C.is_green(bar))
            if self._pending_red is None and counter:
                self._pending_red = bar           # mark the little counter bar
            elif self._pending_red is not None:
                removed = (C.takeout_long(self._pending_red, bar, self.cfg) if self.side > 0
                           else C.takeout_short(self._pending_red, bar, self.cfg))
                same_dir = (C.is_green(bar) if self.side > 0 else C.is_red(bar))
                if removed and same_dir:
                    add_qty = self.shares - self.filled
                    if add_qty > 0:
                        self.filled += add_qty; self.adds += 1; self.state = IN_FULL
                        self._pending_red = None
                        out.append(OrderTicket(self.symbol, "BUY" if self.side > 0 else "SELL",
                                               "MKT", add_qty, bar["close"],
                                               "add: counter-bar removed by takeout", "G9"))
                else:
                    # a second consecutive counter-color bar cancels the add (G9)
                    if counter:
                        self._pending_red = None
        return out

    def _try_removal(self, bar):
        """WAITING (mismatch §5): the problem bar is removed → delayed entry."""
        if self._pending_red is None:
            self.state = STAND_DOWN
            return []
        loc_long = bar["close"] > bar["open"]
        removed = (C.takeout_long(self._pending_red, bar, self.cfg) if loc_long
                   else C.takeout_short(self._pending_red, bar, self.cfg))
        if removed:
            self.side = 1 if loc_long else -1
            self.bar1 = self._pending_red       # stop under the problem-bar/pair
            self.entry_price = bar["close"]
            self.stop_price = (self._pending_red["low"] - self._c("trade_offset") if loc_long
                               else self._pending_red["high"] + self._c("trade_offset"))
            self.shares = self._size(self.entry_price, self.stop_price)
            half = max(1, int(self.shares * self._c("initial_fraction")))
            self.filled, self.state = half, IN_HALF
            self.push = C.PushState(direction=self.side, trade_extreme=bar["high"] if loc_long else bar["low"])
            self._log("mismatch removed — delayed entry", "G11")
            return [OrderTicket(self.symbol, "BUY" if loc_long else "SELL",
                                "MKT", half, bar["close"], "mismatch removal entry", "G11"),
                    OrderTicket(self.symbol, "SELL" if loc_long else "BUY",
                                "STP", half, self.stop_price, "protective stop", "G7")]
        return []

    # ── R7: session cutoff ───────────────────────────────────────────────────
    def on_cutoff(self):
        if self.state in (IN_HALF, IN_FULL) and self.filled > 0:
            self.state = FLAT
            self._log("session cutoff — flatten", "G1/R7")
            return [OrderTicket(self.symbol, "SELL" if self.side > 0 else "BUY",
                                "MKT", self.filled, 0.0, "session cutoff flatten", "G1")]
        self.state = FLAT
        return []
