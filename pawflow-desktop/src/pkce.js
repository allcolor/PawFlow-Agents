'use strict';

const crypto = require('crypto');

function base64url(value) {
  return Buffer.from(value).toString('base64url');
}

function createPkce(randomBytes = crypto.randomBytes) {
  const verifier = base64url(randomBytes(64));
  const challenge = base64url(crypto.createHash('sha256').update(verifier, 'ascii').digest());
  return { verifier, challenge };
}

module.exports = { createPkce };
