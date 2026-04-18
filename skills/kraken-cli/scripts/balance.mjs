#!/usr/bin/env node
// balance.mjs — Current account balances (non-zero only).
// Usage:  node /data/skills/kraken-cli/scripts/balance.mjs
// Output: { "XXBT": "0.12345", "ZUSD": "1234.56", ... }
//
// Requires KRAKEN_API_KEY / KRAKEN_API_SECRET. Kraken asset codes:
//   ZUSD = USD, ZEUR = EUR, ZCAD = CAD
//   XXBT = BTC, XETH = ETH, etc.

import { makeClient, printOk, printErr } from './_common.mjs';

try {
  const client = makeClient({ requireAuth: true });
  const result = await client.private('Balance');
  // Filter out zero balances so the output is easier to read.
  const nonzero = {};
  for (const [asset, amount] of Object.entries(result)) {
    if (parseFloat(amount) > 0) nonzero[asset] = amount;
  }
  printOk({
    nonzero,
    assetCount: Object.keys(nonzero).length,
  });
} catch (e) {
  printErr('balance', e);
}
