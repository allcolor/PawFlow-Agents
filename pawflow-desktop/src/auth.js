'use strict';

const { normalizeServerUrl, sameOrigin, required } = require('./url_policy');

async function responseJson(response) {
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error(`PawFlow returned HTTP ${response.status}`);
  }
  if (!response.ok) {
    throw new Error(String(payload.error || payload.message || `PawFlow returned HTTP ${response.status}`));
  }
  return payload;
}

class AuthClient {
  constructor(fetchFn) {
    if (typeof fetchFn !== 'function') throw new Error('fetch is required');
    this.fetch = fetchFn;
  }

  async request(profile, gatewayKey, method, route, body = null) {
    const baseUrl = normalizeServerUrl(profile.base_url);
    const url = new URL(required(route, 'Auth route'), baseUrl);
    if (!sameOrigin(baseUrl, url)) throw new Error('Authentication route changed origin');
    const headers = {
      Accept: 'application/json',
      'X-PawFlow-Gateway-Key': required(gatewayKey, 'Gateway key'),
    };
    const options = { method, headers, redirect: 'manual' };
    if (body !== null) {
      headers['Content-Type'] = 'application/json; charset=utf-8';
      options.body = JSON.stringify(body);
    }
    const response = await this.fetch(url.toString(), options);
    if (response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400)) {
      throw new Error('Authentication endpoint redirected unexpectedly');
    }
    if (response.url && !sameOrigin(baseUrl, response.url)) {
      throw new Error('Authentication response changed origin');
    }
    return responseJson(response);
  }

  providers(profile, gatewayKey) {
    return this.request(profile, gatewayKey, 'GET', '/auth/mobile/providers');
  }

  builtin(profile, gatewayKey, username, password, challenge) {
    return this.request(profile, gatewayKey, 'POST', '/auth/mobile/builtin', {
      username: required(username, 'Username'),
      password: required(password, 'Password'),
      code_challenge: required(challenge, 'PKCE challenge'),
    });
  }

  startOAuth(profile, gatewayKey, provider, challenge) {
    return this.request(profile, gatewayKey, 'POST', '/auth/mobile/start', {
      provider: required(provider, 'OAuth provider'),
      code_challenge: required(challenge, 'PKCE challenge'),
    });
  }
}

module.exports = { AuthClient };
