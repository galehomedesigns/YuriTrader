#!/usr/bin/env node
// add-order.mjs — Place an order on Kraken.
//
// ═══════════════════════════════════════════════════════════════════════════
//   DOUBLE-GATE SAFETY INTERLOCK — READ BEFORE MODIFYING
// ═══════════════════════════════════════════════════════════════════════════
//
// A real order is submitted to Kraken ONLY when BOTH of these are true:
//
//   Gate 1 (env):   KRAKEN_ALLOW_TRADING=true   (server environment)
//   Gate 2 (call):  --validate=false            (explicit caller opt-in)
//
// If either gate is closed, this script forces `validate=true` on the
// upstream Kraken call. Kraken's validator runs the same checks it would for
// a real order but does not submit it. The response JSON always includes the
// gate state so the caller (agent, script, human) can tell what happened.
//
// Never "simplify" by collapsing the gates. They are deliberately
// independent so that a bug or misconfiguration in one cannot accidentally
// enable live trading. The behavior here must match exactly what's
// documented in ../../../kraken-mcp/src/tools/trading.ts
//
// Usage:
//   node add-order.mjs --pair=XBTUSD --type=buy --ordertype=market --volume=0.0001
//   node add-order.mjs --pair=XBTUSD --type=sell --ordertype=limit --volume=0.0001 --price=80000
//
// Required args: --pair, --type (buy|sell), --ordertype, --volume
// Optional args: --price, --price2, --leverage, --oflags, --timeinforce, --userref
// Dry-run gate: --validate=true (default) or --validate=false (opt in to real)
//
// Output: {
//   dryRun, willSubmitReal, gates: {...},
//   request: { ... the params we sent to Kraken ... },
//   krakenResult: { ... Kraken's response ... }
// }

import { makeClient, printOk, printErr, parseArgs, resolveTradingGate } from './_common.mjs';

const args = parseArgs(process.argv.slice(2));

// Required args
const required = ['pair', 'type', 'ordertype', 'volume'];
const missing = required.filter((k) => args[k] === undefined);
if (missing.length > 0) {
  console.error(`Missing required args: ${missing.join(', ')}`);
  console.error('Usage: add-order.mjs --pair=XBTUSD --type=buy --ordertype=market --volume=0.0001');
  process.exit(2);
}
if (args.type !== 'buy' && args.type !== 'sell') {
  console.error('--type must be "buy" or "sell"');
  process.exit(2);
}

// Resolve gate state. This is the ONLY place in the script where we decide
// whether the order is real or a dry-run. Every code path below this line
// routes through `effectiveValidate`.
const gate = resolveTradingGate(args);
const effectiveValidate = !gate.willSubmitReal;

// Build the request params. Kraken takes string/number/boolean values.
const params = {
  pair: args.pair,
  type: args.type,
  ordertype: args.ordertype,
  volume: args.volume,
  validate: effectiveValidate, // ← the gate is enforced here, never trust the raw arg
};
if (args.price !== undefined) params.price = args.price;
if (args.price2 !== undefined) params.price2 = args.price2;
if (args.leverage !== undefined) params.leverage = args.leverage;
if (args.oflags !== undefined) params.oflags = args.oflags;
if (args.timeinforce !== undefined) params.timeinforce = args.timeinforce;
if (args.userref !== undefined) params.userref = parseInt(args.userref, 10);

try {
  const client = makeClient({ requireAuth: true });
  const krakenResult = await client.private('AddOrder', params);
  printOk({
    dryRun: gate.dryRun,
    willSubmitReal: gate.willSubmitReal,
    gates: {
      envGateOpen: gate.envGateOpen,
      callerOptedIn: gate.callerOptedIn,
      effectiveValidate,
    },
    request: params,
    krakenResult,
  });
} catch (e) {
  printErr('add-order', e);
}
