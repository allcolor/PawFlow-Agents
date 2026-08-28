'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { AuthClient } = require('../src/auth');

const profile = { base_url: 'https://pawflow.example.org' };

test('uses the mobile auth contract and gateway header', async () => {
  const requests = [];
  const client = new AuthClient(async (url, options) => {
    requests.push({ url, options });
    return {
      ok: true,
      status: 200,
      url,
      json: async () => ({ providers: [{ type: 'password' }] }),
    };
  });
  const result = await client.providers(profile, 'gateway-key');
  assert.equal(result.providers[0].type, 'password');
  assert.equal(requests[0].url, 'https://pawflow.example.org/auth/mobile/providers');
  assert.equal(requests[0].options.headers['X-PawFlow-Gateway-Key'], 'gateway-key');
  assert.equal(requests[0].options.redirect, 'manual');
});

test('rejects cross-origin authentication responses', async () => {
  const client = new AuthClient(async () => ({
    ok: true,
    status: 200,
    url: 'https://evil.invalid/result',
    json: async () => ({}),
  }));
  await assert.rejects(() => client.providers(profile, 'gateway-key'), /changed origin/);
});
