'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const { createPkce } = require('../src/pkce');

test('creates a 64-byte S256 PKCE pair', () => {
  const pair = createPkce(size => Buffer.alloc(size, 7));
  assert.equal(Buffer.from(pair.verifier, 'base64url').length, 64);
  const expected = crypto.createHash('sha256').update(pair.verifier, 'ascii').digest('base64url');
  assert.equal(pair.challenge, expected);
  assert.equal(pair.verifier.includes('='), false);
});
