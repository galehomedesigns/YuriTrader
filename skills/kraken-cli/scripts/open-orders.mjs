#!/usr/bin/env node
// open-orders.mjs — List currently open (unfilled or partially filled) orders.
// Usage:  node /data/skills/kraken-cli/scripts/open-orders.mjs
// Output: { count, orders: { txid: {status, pair, side, ordertype, volume, price, ...}, ... } }

import { makeClient, printOk, printErr } from './_common.mjs';

try {
  const client = makeClient({ requireAuth: true });
  const result = await client.private('OpenOrders', { trades: false });
  const orders = result.open || {};
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
      opentm: o.opentm,
    };
  }
  printOk({
    count: Object.keys(simplified).length,
    orders: simplified,
  });
} catch (e) {
  printErr('open-orders', e);
}
