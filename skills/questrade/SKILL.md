---
name: questrade
description: Questrade brokerage integration — portfolio, quotes, trading, and order management via the Questrade API. Triggers on requests about stocks, trading, portfolio, positions, balances, buying, selling, market quotes, or Questrade.
---

# Questrade Trading Skill

Full Questrade brokerage integration for Tony's account. Supports portfolio queries, live quotes, symbol search, order placement, and trade history.

## Commands

```bash
# Portfolio — balances and positions across all currencies
python3 {baseDir}/scripts/questrade.py portfolio

# Live quotes for one or more symbols
python3 {baseDir}/scripts/questrade.py quote AAPL MSFT ENB.TO

# Search for symbols by keyword
python3 {baseDir}/scripts/questrade.py search "enbridge"

# View open orders
python3 {baseDir}/scripts/questrade.py orders

# Recent trade history (default: 7 days)
python3 {baseDir}/scripts/questrade.py history
python3 {baseDir}/scripts/questrade.py history 30

# Place a market buy order
python3 {baseDir}/scripts/questrade.py buy ENB 100

# Place a limit buy order
python3 {baseDir}/scripts/questrade.py buy ENB 100 55.50

# Place a sell order
python3 {baseDir}/scripts/questrade.py sell MSFT 50

# Cancel an order by ID
python3 {baseDir}/scripts/questrade.py cancel 12345678
```

## Symbol Format

- **US stocks**: Use ticker directly — `AAPL`, `MSFT`, `NVDA`
- **Canadian stocks**: Use `.TO` suffix — `ENB.TO`, `TD.TO`, `SHOP.TO`
- **ETFs**: Same rules — `SPY` (US), `XIU.TO` (Canadian)
- When unsure, use the `search` command first to find the correct symbol

## Trading Safety

- **ALWAYS confirm with Tony before placing any order.** Show him the symbol, quantity, price, and order type before executing.
- Market orders execute immediately at current price — use limit orders when possible for better control.
- Orders are placed as Day orders (expire at market close if not filled).
- Commission: $4.95–$9.95 per stock trade, ETF purchases are commission-free.

## Authentication

- Uses OAuth2 refresh token flow. Tokens are cached at `/home/tonygale/openclaw/state/questrade_token.json`.
- Refresh tokens are single-use — each refresh generates a new one.
- If auth fails, Tony needs to generate a new token at questrade.com > Settings > API centre.

## Environment Variables

- `QUESTRADE_REFRESH_TOKEN` — Initial refresh token (used only on first auth)
- `QUESTRADE_CONSUMER_KEY` — App consumer key

## Account Selection

The script auto-selects the primary account, preferring: Margin > TFSA > RRSP > Cash.
