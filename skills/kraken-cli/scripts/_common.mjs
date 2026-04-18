// _common.mjs — Shared helpers for kraken-cli skill scripts.
// This file is not a CLI itself; other scripts in scripts/ import from it.
//
// Deployment target: /home/tonygale/openclaw/skills/kraken-cli/scripts/_common.mjs
// KrakenClient source: /data/kraken-mcp/build/kraken/client.js
//
// The relative import (`../../../kraken-mcp/build/kraken/client.js`) resolves
// correctly from inside the OpenClaw container because /home/tonygale/openclaw/skills/kraken-cli/
// and /data/kraken-mcp/ are siblings under /data/.

import { KrakenClient } from '../../../kraken-mcp/build/kraken/client.js';

/**
 * Construct a KrakenClient from env vars.
 * Exits with a helpful error if required env is missing and the caller
 * declared that auth is required.
 */
export function makeClient({ requireAuth = false } = {}) {
  const apiKey = (process.env.KRAKEN_API_KEY || '').trim() || null;
  const apiSecret = (process.env.KRAKEN_API_SECRET || '').trim() || null;
  const apiUrl = (process.env.KRAKEN_API_URL || 'https://api.kraken.com').trim();

  if (requireAuth && !(apiKey && apiSecret)) {
    console.error('Error: this command requires KRAKEN_API_KEY and KRAKEN_API_SECRET in the environment.');
    console.error('Add them to /docker/openclaw-xrt9/.env and restart the OpenClaw container.');
    process.exit(2);
  }

  return new KrakenClient({ apiUrl, apiKey, apiSecret });
}

/**
 * Print a JS value as pretty JSON and exit 0.
 * Convert numbers/BigInts safely.
 */
export function printOk(value) {
  console.log(JSON.stringify(value, (_, v) => (typeof v === 'bigint' ? v.toString() : v), 2));
  process.exit(0);
}

/**
 * Print an error to stderr with a prefix and exit with code 1.
 * Never leaks API keys or secrets because we never log env values.
 */
export function printErr(prefix, err) {
  const msg = err instanceof Error ? err.message : String(err);
  console.error(`${prefix}: ${msg}`);
  process.exit(1);
}

/**
 * Tiny arg parser: turns `--key=value` and `--flag` into an object.
 * Positional args (no leading --) go into `_` array.
 */
export function parseArgs(argv) {
  const out = { _: [] };
  for (const a of argv) {
    if (a.startsWith('--')) {
      const eq = a.indexOf('=');
      if (eq === -1) {
        out[a.slice(2)] = true;
      } else {
        out[a.slice(2, eq)] = a.slice(eq + 1);
      }
    } else {
      out._.push(a);
    }
  }
  return out;
}

/**
 * Read the trading double-gate state from env + caller args.
 * Returns { dryRun, envGateOpen, callerOptedIn, willSubmitReal }.
 *
 * ⚠️  This is safety-critical. Do not modify without re-reading the interlock
 *    documentation in ../../../kraken-mcp/src/tools/trading.ts
 *
 * Real order requires BOTH:
 *   - KRAKEN_ALLOW_TRADING=true in environment
 *   - --validate=false passed on the command line
 * Any other combination (unset, true, typo, default) forces dry-run.
 */
export function resolveTradingGate(args) {
  const envGateOpen = process.env.KRAKEN_ALLOW_TRADING === 'true';
  const callerOptedIn = args.validate === 'false';
  const willSubmitReal = envGateOpen && callerOptedIn;
  return {
    dryRun: !willSubmitReal,
    envGateOpen,
    callerOptedIn,
    willSubmitReal,
  };
}
