/**
 * File: models/supabaseClient.js
 * Supabase REST client for crypto trade/signal logging
 * Uses the same Supabase instance as OpenClaw
 */
const https = require('https');
const config = require('../config');

class SupabaseClient {
  constructor() {
    this.url = config.supabase.url;
    this.key = config.supabase.key;
  }

  async insert(table, data) {
    return this._request('POST', `/${table}`, data);
  }

  async update(table, id, data) {
    return this._request('PATCH', `/${table}?id=eq.${id}`, data);
  }

  async select(table, params = '') {
    return this._request('GET', `/${table}?${params}`);
  }

  async _request(method, path, body = null) {
    return new Promise((resolve, reject) => {
      const url = new URL(`${this.url}/rest/v1${path}`);

      const options = {
        hostname: url.hostname,
        path: url.pathname + url.search,
        method,
        headers: {
          'apikey': this.key,
          'Authorization': `Bearer ${this.key}`,
          'Content-Type': 'application/json',
          'Prefer': method === 'POST' ? 'return=representation' : 'return=minimal',
        },
      };

      const req = https.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => data += chunk);
        res.on('end', () => {
          try {
            if (res.statusCode >= 200 && res.statusCode < 300) {
              const parsed = data ? JSON.parse(data) : null;
              if (method === 'POST' && Array.isArray(parsed) && parsed.length > 0) {
                resolve(parsed[0].id);
              } else {
                resolve(parsed);
              }
            } else {
              console.error(`[Supabase] ${method} ${path} — ${res.statusCode}: ${data.substring(0, 200)}`);
              resolve(null);
            }
          } catch (e) {
            resolve(null);
          }
        });
      });

      req.on('error', (e) => {
        console.error(`[Supabase] Request error: ${e.message}`);
        resolve(null);
      });

      if (body) {
        req.write(JSON.stringify(body));
      }
      req.end();
    });
  }
}

module.exports = SupabaseClient;
