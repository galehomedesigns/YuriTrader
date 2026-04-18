#!/usr/bin/env node
// trades-history.mjs — Historical trade fills (individual executions).
// Usage:  node /home/tonygale/openclaw/skills/kraken-cli/scripts/trades-history.mjs [--ofs=N] [--start=UNIX] [--end=UNIX]
// Note: these are FILLS, not orders. One order can produce multiple fills.
// Output: { count, total, trades: { tradeid: {pair, side, ordertype, price, cost, fee, volume, ...}, ... } }

import { makeClient, printOk, printErr, parseArgs } from './_common.mjs';

const args = parseArgs(process.argv.slice(2));
const params = { type: 'all', trades: false };
if (args.ofs !== undefined) params.ofs = parseInt(args.ofs, 10);
if (args.start !== undefined) params.start = args.start;
if (args.end !== undefined) params.end = args.end;

try {
  const client = makeClient({ requireAuth: true });
  const result = await client.private('TradesHistory', params);
  const trades = result.trades || {};
  const simplified = {};
  for (const [tradeid, t] of Object.entries(trades)) {
    simplified[tradeid] = {
      ordertxid: t.ordertxid,
      pair: t.pair,
      time: t.time,
      side: t.type,
      ordertype: t.ordertype,
      price: t.price,
      cost: t.cost,
      fee: t.fee,
      volume: t.vol,
      margin: t.margin,
    };
  }
  printOk({
    count: Object.keys(simplified).length,
    total: result.count,
    trades: simplified,
  });
} catch (e) {
  printErr('trades-history', e);
}
