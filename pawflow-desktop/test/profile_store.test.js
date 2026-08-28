'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { ProfileStore } = require('../src/profile_store');

function fakeSafeStorage(backend = 'gnome_libsecret') {
  return {
    isEncryptionAvailable: () => true,
    getSelectedStorageBackend: () => backend,
    encryptString: value => Buffer.from(`protected:${Buffer.from(value).toString('base64')}`),
    decryptString: value => Buffer.from(value.toString().split(':', 2)[1], 'base64').toString(),
  };
}

test('stores profile metadata separately from encrypted gateway material', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pawflow-desktop-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const store = new ProfileStore({
    root,
    safeStorage: fakeSafeStorage(),
    platform: 'linux',
    now: () => '2026-08-27T20:00:00.000Z',
  });
  const profile = store.save({
    name: 'Production',
    baseUrl: 'https://PawFlow.example.org/',
    gatewayKey: 'never-plaintext',
  });
  assert.equal(profile.base_url, 'https://pawflow.example.org');
  assert.equal(store.gatewayKey(profile.id), 'never-plaintext');
  assert.equal(store.list()[0].gatewayKey, undefined);
  assert.equal(fs.readFileSync(path.join(root, 'profiles.json'), 'utf8').includes('never-plaintext'), false);
  assert.equal(fs.readFileSync(path.join(root, 'secrets.json'), 'utf8').includes('never-plaintext'), false);

  store.savePendingOAuth({ profileId: profile.id, flowId: 'flow', verifier: 'verifier' });
  assert.equal(store.loadPendingOAuth().verifier, 'verifier');
  store.clearPendingOAuth();
  assert.equal(store.loadPendingOAuth(), null);

  store.remove(profile.id);
  assert.deepEqual(store.list(), []);
});

test('fails closed when Linux safeStorage selects plaintext', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pawflow-desktop-test-'));
  const store = new ProfileStore({
    root,
    safeStorage: fakeSafeStorage('basic_text'),
    platform: 'linux',
  });
  assert.throws(() => store.save({
    name: 'Unsafe',
    baseUrl: 'https://pawflow.example.org',
    gatewayKey: 'key',
  }), /Secret Service or KWallet/);
  fs.rmSync(root, { recursive: true, force: true });
});
