#!/usr/bin/env node
// ticker.mjs — Current price, bid/ask, 24h volume for one or more pairs.
// Usage:  node /home/tonygale/openclaw/skills/kraken-cli/scripts/ticker.mjs XBTUSD
//         node /home/tonygale/openclaw/skills/kraken-cli/scripts/ticker.mjs XBTUSD,ETHUSD,SOLUSD
// Output: { "XXBTZUSD": { last, bid, ask, volume24h, low24h, high24h }, ... }

import { makeClient, printOk, printErr, parseArgs } from './_common.mjs';

const args = parseArgs(process.argv.slice(2));
const pair = args._[0];
if (!pair) {
  console.error('Usage: ticker.mjs <pair[,pair,...]>');
  console.error('Example: ticker.mjs XBTUSD,ETHUSD');
  process.exit(2);
}

try {
  const client = makeClient();
  const raw = await client.public('Ticker', { pair });
  // Kraken returns a verbose response; simplify to just the fields humans care about.
  const simplified = {};
  for (const [code, t] of Object.entries(raw)) {
    simplified[code] = {
      last: parseFloat(t.c[0]),
      bid: parseFloat(t.b[0]),
      ask: parseFloat(t.a[0]),
      volume24h: parseFloat(t.v[1]),
      vwap24h: parseFloat(t.p[1]),
      low24h: parseFloat(t.l[1]),
      high24h: parseFloat(t.h[1]),
      trades24h: t.t[1],
    };
  }
  printOk(simplified);
} catch (e) {
  printErr('ticker', e);
}
