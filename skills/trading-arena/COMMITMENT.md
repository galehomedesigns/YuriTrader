# Trend-Rider — Commitment Contract

A pre-commitment between me (Tony) and future-me, signed **before** any
capital is funded, so future-me cannot negotiate with it during a drawdown.
Locked to the strategy in [trend.py](trend.py) **exactly as it stands on
2026-05-18**. Editing any strategy parameter voids this contract and
requires full re-validation. This is the anti-self-deception mechanism.

## 1. The locked strategy (no knobs may turn)

Daily `FAST=20 / SLOW=100`, or weekly calendar-equivalent `FAST=4 / SLOW=20`.
`BUFFER=3%` exit dead-band. Fee `0.40%/side` (measured Kraken maker, 2026-05-18).
Long only. **BTC and ETH only** — not as tuning, but because the cross-pair +
multi-regime tests *proved twice* the method structurally underperforms
parabolic alts (XRP). ~1 trade per asset per year.

## 2. What the real data actually showed (not a forecast)

| | Strategy | Buy & hold | Worst measured DD* |
|---|---|---|---|
| BTC daily ~2yr | +44.0% | +32.2% | −7.0% |
| ETH daily ~2yr | +56.8% | −13.6% | −7.1% |
| BTC weekly ~12.6yr | +25,511% | +12,131% | **−30.7%** |
| ETH weekly ~10.8yr | +618,185% | +245,373% | **−42.6%** |

\*Closed-trade equity drawdown — a **FLOOR**. Real account drawdown will be
**deeper** (it ignores adverse move inside open trades).

**The five/six-figure weekly percentages are fantasy as a forecast.** They are
real arithmetic on a once-in-history super-cycle measured from sub-$10 prices.
They will not repeat. I acknowledge this in writing.

## 3. What I am actually committing money to

A **downside-protection vehicle**, not a wealth machine. Its honest job over a
full cycle: roughly match or modestly beat just holding BTC/ETH, with
materially smaller crashes. It will sometimes sit in cash while a parabolic run
happens and I will feel I'm "missing it." That is the deal I am accepting.

## 4. Capital terms

- Amount funded: **$____________**  (recommended floor $10k–$25k; below ~$10k
  this loses to just indexing on a time-adjusted basis — don't.)
- This is money I can **lose in full** and do **not** need within 5 years.
- Not rent, not emergency, not borrowed capital.

## 5. Time commitment

- Minimum horizon: **3–5 years** (one full ~4-year crypto cycle). Shorter is
  noise, not a test.

## 6. The no-override pledge

I will not place a single discretionary buy or sell against the rule. No "this
time it's different," no early exit in fear, no early entry in greed. The
instant I override it, I am back in the discretionary game this entire
investigation *proved* is negative-EV. The rule's whole purpose is to remove
me. If I cannot honour this, I will not fund it.

## 7. Pre-defined kill criterion (decided now, while calm)

The worst measured drawdown FLOOR is −42.6% (ETH weekly), true drawdown deeper.
Therefore a drawdown to ~−43% is **expected and not a failure** — selling there
would be the single worst mistake. I stop **only** if, with the rule followed
exactly:

- account peak-to-trough drawdown exceeds **−60%** (clearly beyond
  backtested-worst + margin for unmeasured adverse excursion), **OR**
- **8+ consecutive** rule-followed round-trips are net losers (the backtest
  never produced this — it would signal the structural edge has broken).

On a kill trigger I flatten, stop, and re-validate from scratch — I do **not**
tweak parameters and continue.

## 8. Acknowledged risk of the path I chose

I chose to commit capital instead of months of free forward paper-testing.
I accept that I am therefore **paying real money to do the out-of-sample
validation paper-testing would have done for free**, and that year one is that
paid validation, not expected profit.

---

Signed: ______________________  Date: ____________

(Strategy hash anchor: trend.py as of 2026-05-18. Any param edit → void.)
