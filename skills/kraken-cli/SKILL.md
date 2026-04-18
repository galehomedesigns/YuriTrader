---
summary: "Kraken spot trading via CLI — chat-callable balance, price, orders, and trades"
read_when:
  - When Tony asks about Kraken balance, price, orders, system status, or trades
  - When Tony wants to place, cancel, or dry-run a Kraken order from chat
---

# kraken-cli — Kraken via chat

When Tony asks about Kraken (balance, price, orders, system status, trades), use
these scripts. They all print JSON to stdout. Parse and summarise the JSON in a
human-readable reply rather than dumping it raw.

All scripts live in `/home/tonygale/openclaw/skills/kraken-cli/scripts/`. They share a common
`KrakenClient` imported from `/data/kraken-mcp/build/kraken/client.js`.

**Yuri is separate.** The `yuri-crypto` container runs Tony's autonomous Kraken
trading bot on the 50-SMA + first-candle-breakout strategy. Do NOT touch yuri's
scripts in `/home/tonygale/openclaw/skills/crypto-trading/scripts/yuri/`. If Tony asks about yuri's
status, check its logs via `docker compose logs yuri-crypto`, not these scripts.

## Public endpoints (no auth)

### System status
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/system-status.mjs
```
Check this first if another command returns a Kraken-side error. Status values:
`online`, `maintenance`, `cancel_only`, `post_only`.

### Ticker (current price, bid/ask, 24h stats)
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/ticker.mjs XBTUSD
node /home/tonygale/openclaw/skills/kraken-cli/scripts/ticker.mjs XBTUSD,ETHUSD,SOLUSD
```
Comma-separate multiple pairs. Kraken internal codes: `XBTUSD` = BTC/USD,
`ETHUSD` = ETH/USD, etc.

### OHLC candles
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/ohlc.mjs XBTUSD          # default 60m
node /home/tonygale/openclaw/skills/kraken-cli/scripts/ohlc.mjs XBTUSD 240      # 4h candles
```
Valid intervals (minutes): 1, 5, 15, 30, 60, 240, 1440, 10080, 21600. Max 720
candles per call.

### Order book
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/orderbook.mjs XBTUSD 10
```
Second arg is depth per side (1-500, default 10). Output includes the current
spread and mid-price.

## Account (reads Tony's account via KRAKEN_API_KEY/SECRET)

### Balance
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/balance.mjs
```
Returns non-zero balances only. Asset codes: `ZUSD`, `ZEUR`, `ZCAD` for fiat;
`XXBT`, `XETH`, etc. for crypto.

### Open orders
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/open-orders.mjs
```
Lists currently unfilled or partially filled orders with their txid, pair, side,
price, and volume.

### Closed orders (paginated)
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/closed-orders.mjs
node /home/tonygale/openclaw/skills/kraken-cli/scripts/closed-orders.mjs --ofs=50
```
Kraken returns up to 50 at a time. Use `--ofs=N` to page. Use
`--start=UNIX_TS` / `--end=UNIX_TS` to time-window.

### Trade fills (paginated)
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/trades-history.mjs
```
Individual fills (executions), not parent orders. Same pagination options as
closed-orders.

## Trading — DOUBLE-GATED

### Place an order (default: DRY-RUN)
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/add-order.mjs --pair=XBTUSD --type=buy --ordertype=market --volume=0.0001
```

By default this is a **dry-run** — Kraken validates the order but does NOT
place it. The response includes a `dryRun: true` field so you can tell.

**To place a REAL order**, BOTH must be true:
1. The container env has `KRAKEN_ALLOW_TRADING=true`
2. The command includes `--validate=false`

```bash
# Real order (requires env gate also open)
node /home/tonygale/openclaw/skills/kraken-cli/scripts/add-order.mjs --pair=XBTUSD --type=buy --ordertype=limit --volume=0.0001 --price=80000 --validate=false
```

If only one gate is open (e.g. env is false but caller passed --validate=false,
or vice versa), the script forces dry-run mode. Both gates required. Never
"simplify" the gate logic.

**When a user asks for a trade in chat, default to dry-run** unless they
explicitly say "for real", "live", or "actually place it". Show them the
`krakenResult.descr.order` string from the dry-run response so they can
confirm the order looks right before they opt in to a real submission.

Required args: `--pair`, `--type` (buy|sell), `--ordertype`, `--volume`.
Optional: `--price`, `--price2`, `--leverage`, `--oflags`, `--timeinforce`,
`--userref`.

Order types: `market`, `limit`, `stop-loss`, `take-profit`, `stop-loss-limit`,
`take-profit-limit`, `settle-position`.

### Cancel an order
```bash
node /home/tonygale/openclaw/skills/kraken-cli/scripts/cancel-order.mjs <txid>
```
Get txids from `open-orders.mjs`. Cancellation is NOT gated — it only removes
orders, it doesn't create positions.

## Pair-code cheatsheet

Kraken uses two-tier asset codes. The public API accepts either form in
requests, but responses use the canonical form:

| User form | Canonical (response key) |
|---|---|
| XBTUSD | XXBTZUSD |
| ETHUSD | XETHZUSD |
| SOLUSD | SOLUSD |
| XBTEUR | XXBTZEUR |

When Tony says "BTC" use `XBT` — Kraken's historical code for Bitcoin.

## Troubleshooting

- `EAPI:Invalid signature` → check the API secret for trailing whitespace or truncation in `.env`
- `EGeneral:Invalid arguments:pair` → wrong pair code; use `XBTUSD` not `BTCUSD` or `BTC-USD`
- `EAPI:Invalid nonce` → system clock jumped backwards or multiple clients sharing a key
- `EService:Unavailable` on add-order → run `system-status.mjs` first, Kraken may be in maintenance
- `EGeneral:Permission denied` → the API key lacks the permission the endpoint needs
