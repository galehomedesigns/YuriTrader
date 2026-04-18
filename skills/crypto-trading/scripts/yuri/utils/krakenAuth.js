/**
 * File: utils/krakenAuth.js
 * Kraken private API HMAC-SHA512 signature generator
 */
const crypto = require('crypto');
const qs = require('querystring');

function generateKrakenSignature(path, request, secret) {
  const message = qs.stringify(request);
  const secretBuffer = Buffer.from(secret, 'base64');
  const hash = crypto.createHash('sha256');
  const hmac = crypto.createHmac('sha512', secretBuffer);
  const hashDigest = hash.update(request.nonce + message).digest('binary');
  const hmacDigest = hmac.update(path + hashDigest, 'binary').digest('base64');
  return hmacDigest;
}

function getNonce() {
  return Date.now() * 1000;
}

module.exports = { generateKrakenSignature, getNonce };
