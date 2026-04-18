#!/usr/bin/env node
// closed-orders.mjs — List historical closed orders (filled, cancelled, expired).
// Usage:  node /data/skills/kraken-cli/scripts/closed-orders.mjs [--ofs=N] [--start=UNIX] [--end=UNIX]
// Kraken returns up to 50 at a time. Use --ofs=50 to page.
// Output: { count, total, orders: { txid: {...}, ... } }

import { makeClient, printOk, printErr, parseArgs } from './_common.mjs';

const args = parseArgs(process.argv.slice(2));
const params = { trades: false };
if (args.ofs !== undefined) params.ofs = parseInt(args.ofs, 10);
if (args.start !== undefined) params.start = args.start;
if (args.end !== undefined) params.end = args.end;

try {
  const client = makeClient({ requireAuth: true });
  const result = await client.private('ClosedOrders', params);
  const orders = result.closed || {};
  const simplified = {};
  for (const [txid, o] of Object.entries(orders)) {
    simplified[txid] = {
      status: o.status,
      pair: o.descr?.pair,
      side: o.descr?.type,
      ordertype: o.descr?.ordertype,
      price: o.descr?.price,
      volume: o.vol,
      volumeExecuted: o.vol_exec,
      cost: o.cost,
      fee: o.fee,
      avgPrice: o.price,
      opentm: o.opentm,
      closetm: o.closetm,
      reason: o.reason,
    };
  }
  printOk({
    count: Object.keys(simplified).length,
    total: result.count,
    orders: simplified,
  });
} catch (e) {
  printErr('closed-orders', e);
}
