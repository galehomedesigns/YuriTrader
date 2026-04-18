#!/usr/bin/env node
// ohlc.mjs — OHLC candle data for a single pair.
// Usage:  node /data/skills/kraken-cli/scripts/ohlc.mjs <pair> [interval-minutes]
// Example: node /data/skills/kraken-cli/scripts/ohlc.mjs XBTUSD 60
// Valid intervals: 1, 5, 15, 30, 60, 240 (4h), 1440 (1d), 10080 (1w), 21600 (15d)
// Output: { pair, interval, count, candles: [{time, open, high, low, close, vwap, volume, trades}, ...] }

import { makeClient, printOk, printErr, parseArgs } from './_common.mjs';

const VALID_INTERVALS = new Set([1, 5, 15, 30, 60, 240, 1440, 10080, 21600]);

const args = parseArgs(process.argv.slice(2));
const pair = args._[0];
const intervalArg = args._[1];
if (!pair) {
  console.error('Usage: ohlc.mjs <pair> [interval-minutes]');
  console.error('Valid intervals: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600');
  process.exit(2);
}
const interval = intervalArg ? parseInt(intervalArg, 10) : 60;
if (!VALID_INTERVALS.has(interval)) {
  console.error(`Invalid interval: ${intervalArg}. Valid: ${[...VALID_INTERVALS].join(', ')}`);
  process.exit(2);
}

try {
  const client = makeClient();
  const raw = await client.public('OHLC', { pair, interval });
  // Extract the pair's candle array (the `last` key is pagination metadata)
  const pairKey = Object.keys(raw).find((k) => k !== 'last');
  const candles = (raw[pairKey] || []).map((c) => ({
    time: c[0],
    open: parseFloat(c[1]),
    high: parseFloat(c[2]),
    low: parseFloat(c[3]),
    close: parseFloat(c[4]),
    vwap: parseFloat(c[5]),
    volume: parseFloat(c[6]),
    trades: c[7],
  }));
  printOk({
    pair: pairKey,
    interval,
    count: candles.length,
    last: raw.last,
    candles,
  });
} catch (e) {
  printErr('ohlc', e);
}
