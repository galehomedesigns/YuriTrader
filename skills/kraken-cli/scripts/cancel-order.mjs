#!/usr/bin/env node
// cancel-order.mjs — Cancel an open order by txid or userref.
// Usage:   node /data/skills/kraken-cli/scripts/cancel-order.mjs <txid>
// Example: node /data/skills/kraken-cli/scripts/cancel-order.mjs OQCLML-BW3P3-BUCMWZ
//
// Cancellation is NOT gated by the --validate flag. It only removes orders,
// never creates positions, so the default-deny posture that protects
// add-order is unnecessary here. Cancellation still requires a Kraken API
// key with "Cancel & Close Orders" permission.

import { makeClient, printOk, printErr, parseArgs } from './_common.mjs';

const args = parseArgs(process.argv.slice(2));
const txid = args._[0];
if (!txid) {
  console.error('Usage: cancel-order.mjs <txid>');
  console.error('Get txids from open-orders.mjs output.');
  process.exit(2);
}

try {
  const client = makeClient({ requireAuth: true });
  const result = await client.private('CancelOrder', { txid });
  printOk({
    txid,
    ...result,
  });
} catch (e) {
  printErr('cancel-order', e);
}
