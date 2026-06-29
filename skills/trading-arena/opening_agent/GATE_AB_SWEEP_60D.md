# Opening-Power — 60-day A/B sweep: as-built vs. "gate-off + gap + breakout"

**Window:** 2026-03-25 → 2026-06-18 (60 trading days, the most recent 60 in the IBKR
2-min cache). **Universe:** the 72 IBKR-cached tech/momentum names. **Data:** IBKR native
2-min RTH bars (the validated backtest source). Methodology identical to
[DAY60_GATE_AB_2026-03-26.md](DAY60_GATE_AB_2026-03-26.md) — live `.env` config (ATR coil
gate), rolling-arm 9:30→9:42, $1,000 in 5 × $200 equal slots, 3% risk cap, +30-min cutoff,
prefix-sum SMAs. **Two scenarios:**
- **A — AS-BUILT:** coil gate ON, rolling-arm, first 5 armed names by arm time.
- **B — SMALL CHANGE:** coil gate OFF, top-5 gap-up names, enter on the 9:30 breakout.

---

## Results (gross, on $1,000)

| Metric | A: as-built | B: gate-off + gap |
|---|---|---|
| **Total P&L over 60 days** | **−$4.61** | **+$140.81** |
| Return on $1,000 | −0.5% | **+14.1%** |
| Avg per trading day | −$0.08 | +$2.35 |
| Days with ≥1 trade | 56 / 60 | 60 / 60 |
| Green days | 25 | 28 |
| Filled trades | 233 | 206 |
| Best / worst day | +$31.96 / −$17.40 | +$51.51 / −$22.23 |

**At the gross level your intuition was right:** the gate-off + gap-ranked + early-entry
variant beat the strategy as configured by a wide margin (+14.1% vs −0.5% on $1,000 over
three months), and it does so on *fewer* trades (206 vs 233) — it dominates A on both
return and trade count.

---

## Execution cost — on Questrade (zero commission), gross ≈ net

The account is **Questrade, commission-free** — so the IBKR commission model below does NOT
apply. At zero commission, **gross = net**: B is **+$140.81 (+14.1%)** and A is **−$4.61**
over the 60 days. The remaining real cost is **spread + slippage** (not commission): the
breakout entry is a stop order that fills at/through the trigger. For liquid names that's a
few bps; for the **small-cap gappers the gap-rank selects, it can be 20–50+ bps** — which is
material and NOT yet modeled. (IBKR-commission table kept for reference only.)

| Net of IBKR commission (reference, N/A on Questrade) | A | B |
|---|---|---|
| gross = **Questrade net** | −$4.61 | **+$140.81** |
| − $1/round-trip | −$237.61 | −$65.19 |
| − $2/round-trip | −$470.61 | −$271.19 |

---

## Honest reading

1. **On Questrade, B is net-positive (+14.1% / 60 days)** and beats the as-built strategy on
   fewer trades. The coil filter was leaving real money on the table. This is a genuine lead.
2. **The remaining cost is spread/slippage, not commission** — and it bites hardest on the
   illiquid small-cap gappers B favours. Must be modeled before trusting the +14%.
3. **The comparison confounds THREE changes** (no gate + gap-ranking + 9:30 entry instead of
   late rolling-arm). Prior work found removing *only* the coil gate (same engine) *hurts*,
   so B's edge most likely comes from **gap-ranking + early entry**, not dropping the gate.
4. **UNIVERSE IS NOT REPRESENTATIVE (the big one).** The live scan pulls the day's actual
   pre-market movers from a TV screener (top ~50, `OPENING_SCAN_LIMIT`) — dynamic, whole
   market. This backtest used a FIXED 72-name hand-picked tech set, biased toward gapping.
   The +14% is on an unrepresentative universe and could move materially either way on the
   real mover set. **This must be redone on a broad universe before the number means much.**

## Next steps (in priority order)
- **Redo on a representative universe** — pull 2-min history for a broad most-active set and
  reconstruct the gapper funnel per day (see limitation: the exact historical screener output
  each day isn't stored, so true small-cap runners may be missed even by a broad set).
- **Model spread/slippage**, especially for low-liquidity gappers.
- **Decompose the 3 changes** (gate-off alone vs +gap-rank vs +early-entry).
- **Split IS/OOS** — this 60-day window is in-period.

_Generated 2026-06-23. Repro: `/tmp/sweep60.py` (reads the IBKR cache). Gross, full-slot
(G9 add assumed filled); halve for the no-add case._
