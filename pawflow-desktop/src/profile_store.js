'use strict';

const fs = require('fs');
const path = require('path');
const { randomUUID } = require('crypto');
const { normalizeServerUrl, required, UUID_RE } = require('./url_policy');

function readJson(file, fallback) {
  if (!fs.existsSync(file)) return fallback;
  const value = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!value || value.version !== 1) throw new Error(`Unsupported local data in ${path.basename(file)}`);
  return value;
}

function atomicWrite(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.${randomUUID()}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n', {
    encoding: 'utf8',
    mode: 0o600,
  });
  fs.renameSync(temporary, file);
}

class ProfileStore {
  constructor({ root, safeStorage, platform = process.platform, now = () => new Date().toISOString() }) {
    this.root = path.resolve(required(root, 'Profile store root'));
    this.safeStorage = safeStorage;
    this.platform = platform;
    this.now = now;
    this.profilesFile = path.join(this.root, 'profiles.json');
    this.secretsFile = path.join(this.root, 'secrets.json');
    this.oauthFile = path.join(this.root, 'pending-oauth.json');
    this.tabsFile = path.join(this.root, 'tabs.json');
  }

  assertSecureStorage() {
    if (!this.safeStorage || !this.safeStorage.isEncryptionAvailable()) {
      throw new Error('The operating-system credential store is unavailable');
    }
    if (this.platform === 'linux'
        && typeof this.safeStorage.getSelectedStorageBackend === 'function'
        && this.safeStorage.getSelectedStorageBackend() === 'basic_text') {
      throw new Error('A Secret Service or KWallet credential store is required');
    }
  }

  _profiles() {
    const value = readJson(this.profilesFile, { version: 1, profiles: [], last_profile_id: '' });
    if (!Array.isArray(value.profiles)) throw new Error('Invalid profiles data');
    return value;
  }

  _secrets() {
    const value = readJson(this.secretsFile, { version: 1, values: {} });
    if (!value.values || typeof value.values !== 'object') throw new Error('Invalid secrets data');
    return value;
  }

  list() {
    return this._profiles().profiles.map(profile => ({ ...profile }));
  }

  get(id) {
    const profile = this.list().find(item => item.id === id);
    if (!profile) throw new Error('Server profile not found');
    return profile;
  }

  save(input) {
    this.assertSecureStorage();
    const state = this._profiles();
    const existing = input.id ? state.profiles.find(item => item.id === input.id) : null;
    const id = existing ? existing.id : (input.id || randomUUID());
    if (!UUID_RE.test(id)) throw new Error('Profile id must be a UUID');
    const gatewayKey = String(input.gatewayKey || '');
    if (!existing && !gatewayKey.trim()) throw new Error('Gateway key is required');
    const timestamp = this.now();
    const profile = {
      id,
      name: required(input.name, 'Server name'),
      base_url: normalizeServerUrl(input.baseUrl),
      secret_ref: `gateway:${id}`,
      created_at: existing ? existing.created_at : timestamp,
      updated_at: timestamp,
    };
    state.profiles = state.profiles.filter(item => item.id !== id);
    state.profiles.push(profile);
    state.last_profile_id = id;
    atomicWrite(this.profilesFile, state);
    if (gatewayKey.trim()) {
      const secrets = this._secrets();
      secrets.values[profile.secret_ref] = this.safeStorage.encryptString(gatewayKey.trim()).toString('base64');
      atomicWrite(this.secretsFile, secrets);
    }
    return { ...profile };
  }

  gatewayKey(id) {
    this.assertSecureStorage();
    const profile = this.get(id);
    const encoded = this._secrets().values[profile.secret_ref];
    if (!encoded) throw new Error('Gateway key is unavailable');
    return this.safeStorage.decryptString(Buffer.from(encoded, 'base64'));
  }

  remove(id) {
    const state = this._profiles();
    const profile = state.profiles.find(item => item.id === id);
    if (!profile) throw new Error('Server profile not found');
    state.profiles = state.profiles.filter(item => item.id !== id);
    if (state.last_profile_id === id) state.last_profile_id = '';
    atomicWrite(this.profilesFile, state);
    const secrets = this._secrets();
    delete secrets.values[profile.secret_ref];
    atomicWrite(this.secretsFile, secrets);
    const tabs = this._tabs();
    delete tabs.profiles[id];
    atomicWrite(this.tabsFile, tabs);
  }

  savePendingOAuth({ profileId, flowId, verifier }) {
    this.assertSecureStorage();
    this.get(profileId);
    atomicWrite(this.oauthFile, {
      version: 1,
      profile_id: profileId,
      flow_id: required(flowId, 'OAuth flow id'),
      verifier: this.safeStorage.encryptString(required(verifier, 'PKCE verifier')).toString('base64'),
      created_at: this.now(),
    });
  }

  loadPendingOAuth() {
    if (!fs.existsSync(this.oauthFile)) return null;
    this.assertSecureStorage();
    const value = readJson(this.oauthFile, null);
    return {
      profileId: value.profile_id,
      flowId: value.flow_id,
      verifier: this.safeStorage.decryptString(Buffer.from(value.verifier, 'base64')),
      createdAt: value.created_at,
    };
  }

  clearPendingOAuth() {
    if (fs.existsSync(this.oauthFile)) fs.unlinkSync(this.oauthFile);
  }

  _tabs() {
    const value = readJson(this.tabsFile, { version: 1, profiles: {} });
    if (!value.profiles || typeof value.profiles !== 'object') throw new Error('Invalid tab data');
    return value;
  }

  tabState(profileId) {
    return this._tabs().profiles[profileId] || { active_tab_id: '', tabs: [] };
  }

  saveTabState(profileId, state) {
    this.get(profileId);
    const tabs = this._tabs();
    tabs.profiles[profileId] = state;
    atomicWrite(this.tabsFile, tabs);
  }
}

module.exports = { ProfileStore, atomicWrite };
