#!/usr/bin/env node
// orderbook.mjs — Order book (bids and asks) for a single pair.
// Usage:  node /data/skills/kraken-cli/scripts/orderbook.mjs <pair> [count]
// Example: node /data/skills/kraken-cli/scripts/orderbook.mjs XBTUSD 5
// count defaults to 10, max 500.
// Output: { pair, asks: [[price, volume, ts]], bids: [[price, volume, ts]] }

import { makeClient, printOk, printErr, parseArgs } from './_common.mjs';

const args = parseArgs(process.argv.slice(2));
const pair = args._[0];
const countArg = args._[1];
if (!pair) {
  console.error('Usage: orderbook.mjs <pair> [count]');
  process.exit(2);
}
const count = countArg ? parseInt(countArg, 10) : 10;
if (!Number.isFinite(count) || count < 1 || count > 500) {
  console.error('count must be between 1 and 500');
  process.exit(2);
}

try {
  const client = makeClient();
  const raw = await client.public('Depth', { pair, count });
  const pairKey = Object.keys(raw)[0];
  const book = raw[pairKey];
  const simplified = {
    pair: pairKey,
    asks: book.asks.map(([price, volume, ts]) => ({
      price: parseFloat(price),
      volume: parseFloat(volume),
      timestamp: ts,
    })),
    bids: book.bids.map(([price, volume, ts]) => ({
      price: parseFloat(price),
      volume: parseFloat(volume),
      timestamp: ts,
    })),
  };
  // Spread snapshot
  if (simplified.asks[0] && simplified.bids[0]) {
    simplified.spread = simplified.asks[0].price - simplified.bids[0].price;
    simplified.mid = (simplified.asks[0].price + simplified.bids[0].price) / 2;
  }
  printOk(simplified);
} catch (e) {
  printErr('orderbook', e);
}
