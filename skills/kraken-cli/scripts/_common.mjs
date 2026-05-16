// _common.mjs — Shared helpers for kraken-cli skill scripts.
// This file is not a CLI itself; other scripts in scripts/ import from it.
//
// KrakenClient is inlined here. The previous version imported from
// ~/openclaw/kraken-mcp/build/kraken/client.js, but that source no longer
// exists on this host. Reimplementing the small surface the kraken-cli
// scripts actually use (public, private) avoids the broken dependency.
//
// Nonce scale must match the other clients sharing this API key
// (trading-arena's kraken_executor.py and yuri/krakenAuth.js both use
// microseconds, ~1e15). If we land at a higher scale, every later call
// from those clients fails with EAPI:Invalid nonce.

import crypto from 'node:crypto';

const BASE_URL = (process.env.KRAKEN_API_URL || 'https://api.kraken.com').trim();

let _lastNonce = 0n;

class KrakenClient {
  constructor({ apiUrl = BASE_URL, apiKey = null, apiSecret = null } = {}) {
    this.apiUrl = apiUrl;
    this.apiKey = apiKey;
    this.apiSecret = apiSecret;
  }

  nextNonce() {
    // Microsecond-scale wall-clock nonce, matching the Python/Node clients
    // that share this API key. Bump by 1 if the clock didn't advance since
    // the last call so in-process bursts stay monotonic.
    let n = BigInt(Date.now()) * 1000n;
    if (n <= _lastNonce) n = _lastNonce + 1n;
    _lastNonce = n;
    return n.toString();
  }

  async public(endpoint, params = null) {
    const qs = params ? '?' + new URLSearchParams(
      Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))
    ).toString() : '';
    const url = `${this.apiUrl}/0/public/${endpoint}${qs}`;
    const resp = await fetch(url);
    const body = await resp.json();
    if (body.error && body.error.length) {
      throw new Error(body.error.join('; '));
    }
    return body.result;
  }

  async private(endpoint, params = {}) {
    if (!this.apiKey || !this.apiSecret) {
      throw new Error(`KRAKEN_API_KEY / KRAKEN_API_SECRET required for ${endpoint}`);
    }
    const path = `/0/private/${endpoint}`;
    const nonce = this.nextNonce();
    const bodyObj = { nonce, ...params };
    // Booleans/numbers need string-form in the body for the signature to match
    // (Kraken signs the URL-encoded form).
    const body = new URLSearchParams(
      Object.fromEntries(Object.entries(bodyObj).map(([k, v]) => [k, String(v)]))
    ).toString();

    const sha256 = crypto.createHash('sha256').update(nonce + body).digest();
    const signature = crypto
      .createHmac('sha512', Buffer.from(this.apiSecret, 'base64'))
      .update(Buffer.concat([Buffer.from(path, 'utf8'), sha256]))
      .digest('base64');

    const resp = await fetch(this.apiUrl + path, {
      method: 'POST',
      headers: {
        'API-Key': this.apiKey,
        'API-Sign': signature,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body,
    });
    const data = await resp.json();
    if (data.error && data.error.length) {
      throw new Error(data.error.join('; '));
    }
    return data.result;
  }
}

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
    console.error('Source /home/tonygale/openclaw/.env before invoking the script.');
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
