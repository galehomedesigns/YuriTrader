# Scope: run the arena bots on STOCKS (paper-first)

Goal: forward paper-test the 12 arena strategies on stocks (commission-free via
Questrade), since crypto proved unprofitable (fee floor). **Independent of the
Opening Power / CDP order-staging setup — that is never touched.**

## Isolation guarantee
The arena (`bots/`, `shared/market_scanner.py`, `shared/paper_trader.py`,
`arena_runner.py`) is a separate subsystem from `opening_agent/` (Opening Power)
and the CDP staging (`tv_order*.js`, `tv_order_queue.js`, `advisory_monitor.py`).
This work touches ONLY arena files. Stock live stays OFF (`LIVE_STOCK_*` gates),
so it's paper-only. No CDP / Opening Power file or flag is modified.

## Ground truth (2026-06-14)
- Bots are asset-agnostic (extend `BaseBot`, decide from generic `AssetData`).
- Data layer abstracted; stock fetchers exist (`fetch_stock_data`,
  `fetch_stock_data_questrade`); `dynamic_watchlist.scan_stocks` exists.
- `arena_scan.sh` (every 5 min, Mon–Fri US hours) already calls `fetch_all()`
  which already fetches stocks. `paper_trader` routes stock symbols to a stock
  path; trades land in Supabase `arena_trades` (paper boolean).
- BUT: arena_trades shows **500 paper trades, all crypto, 0 stock** → stocks
  aren't actually reaching the bots (likely thin Finnhub/TwelveData free tier).
- Stock LIVE is built but blocked at Questrade (read-only OAuth, 403/1016).

## Phase 1 — stock paper-trading (small)
1. Route `fetch_all` stock data to `fetch_stock_data_questrade` (read-only
   quotes/candles work despite the order-scope 403). Verify it returns data.
2. Confirm stock symbols flow into `market_data` during market hours.
3. De-Kraken the 2 fee-aware bots (`trap_catcher`, `momentum_burst`): make the
   profit hurdle asset-aware — Kraken RT fee for crypto (unchanged), a small
   stock fee (~spread, commission-free) for stocks — so they stop over-filtering
   stock entries. Crypto behavior must stay identical.
4. Verify stock paper trades start landing in `arena_trades`.

## Phase 2 — observe & evaluate (weeks, passive)
Let stock paper bots run during market hours; track per-bot stock P&L on the
bot-arena dashboard. After a real sample, see which bots (if any) have stock edge
net of commission-free + spread. That is the answer we want.

## Phase 3 — stock LIVE (gated, separate, only if a bot earns it)
Blocked at the broker (Questrade read-only OAuth 403/1016). Options: Questrade
API trade scope, IBKR, or manual. Do NOT invest until Phase 2 proves edge.

## Risks / notes
- Stock data tier: use Questrade read-only candles (reliable) over free Finnhub/TwelveData.
- Universe: `dynamic_watchlist.SCAN_UNIVERSE` ~50 mega-caps; widen for more setups.
- Honest expectation: crypto failed largely on the 1.6% fee floor; commission-free
  stocks remove it (better shot) but edge is not guaranteed — Phase 2 decides.
- Live crypto bots still bleed (−$9.98); separate decision to kill them.

## Progress
- [~] Phase 1.1 stock data routing — DIAGNOSED 2026-06-14: 0 stock paper trades because the
      Questrade stock fetch is the gap. Both fetchers + raw get_candles/get_quote TIMED OUT
      in testing, though paper_tracker pulled candles fine earlier → transient Questrade API
      hang (token fresh+valid, lock free; likely rate-limit from concierge daemons + test
      calls). Can't verify a fix while it hangs AND markets are closed (weekend). DEFER the
      apply+verify to a market-open weekday: confirm live fetch → route fetch_all to the
      Questrade candle path → confirm stock paper trades land.
- [ ] 1.2 stocks in loop (Monday, with 1.1)
- [x] 1.3 de-Kraken bots — DONE 2026-06-14: added config.STOCK_ROUNDTRIP_FEE_PCT (0.10% spread proxy)
      + roundtrip_fee_pct(asset_type); trap_catcher + momentum_burst now compute the fee per-asset
      (crypto = Kraken fee, VERIFIED identical: trap 1.6%, momentum (1.6,3.0,3.7); stock = (0.1,3.0,3.5)).
      Arena-only; no Opening Power / CDP file touched.
- [ ] 1.4 verify stock paper trades land (Monday)
