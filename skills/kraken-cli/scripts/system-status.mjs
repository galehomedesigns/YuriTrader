#!/usr/bin/env node
// system-status.mjs — Kraken operational status.
// Usage:  node /home/tonygale/openclaw/skills/kraken-cli/scripts/system-status.mjs
// Output: {"status": "online", "timestamp": "..."}  (or maintenance/cancel_only/post_only)

import { makeClient, printOk, printErr } from './_common.mjs';

try {
  const client = makeClient();
  const result = await client.public('SystemStatus');
  printOk(result);
} catch (e) {
  printErr('system-status', e);
}
