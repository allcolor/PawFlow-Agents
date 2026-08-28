'use strict';

const path = require('path');
const { randomUUID } = require('crypto');

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function required(value, label) {
  const text = String(value || '').trim();
  if (!text) throw new Error(`${label} is required`);
  return text;
}

function normalizeServerUrl(value) {
  let parsed;
  try {
    parsed = new URL(required(value, 'Server URL'));
  } catch (_error) {
    throw new Error('A valid HTTPS server URL is required');
  }
  if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username
      || parsed.password || parsed.search || parsed.hash
      || (parsed.pathname && parsed.pathname !== '/')) {
    throw new Error('A valid HTTPS server URL without a path is required');
  }
  return `https://${parsed.hostname.toLowerCase()}${parsed.port ? `:${parsed.port}` : ''}`;
}

function sameOrigin(baseUrl, candidate) {
  try {
    return new URL(normalizeServerUrl(baseUrl)).origin === new URL(candidate).origin
      && new URL(candidate).protocol === 'https:';
  } catch (_error) {
    return false;
  }
}

function safeExternalUrl(value) {
  const parsed = new URL(required(value, 'External URL'));
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password) {
    throw new Error('Only credential-free HTTPS links may open externally');
  }
  return parsed.toString();
}

function requireUuid(value, label) {
  const text = required(value, label);
  if (!UUID_RE.test(text)) throw new Error(`${label} must be a UUID`);
  return text;
}

function parseDeepLink(value) {
  const parsed = new URL(required(value, 'Deep link'));
  if (parsed.protocol !== 'pawflow:') throw new Error('Unsupported deep-link scheme');
  if (parsed.hostname === 'oauth') {
    const flowId = required(parsed.searchParams.get('flow_id'), 'flow_id');
    const code = parsed.searchParams.get('code') || '';
    const error = parsed.searchParams.get('error') || '';
    if (!code && !error) throw new Error('OAuth callback requires code or error');
    return { action: 'oauth', flowId, code, error };
  }
  if (parsed.hostname === 'open') {
    const serverId = requireUuid(parsed.searchParams.get('server'), 'server');
    const conversationId = parsed.searchParams.get('conversation_id');
    let targetPath = parsed.searchParams.get('path') || '/chat';
    if (conversationId) {
      targetPath = `/chat?conversation_id=${encodeURIComponent(requireUuid(conversationId, 'conversation_id'))}`;
    }
    if (!targetPath.startsWith('/chat')) throw new Error('Deep-link path is not allowed');
    const resolved = new URL(targetPath, 'https://pawflow.invalid');
    if (resolved.origin !== 'https://pawflow.invalid') throw new Error('Deep-link path is not allowed');
    return { action: 'open', serverId, path: resolved.pathname + resolved.search };
  }
  throw new Error('Unsupported PawFlow deep link');
}

function sanitizeDownloadName(value) {
  const original = required(value, 'Download filename');
  const cleaned = path.basename(original)
    .replace(/[\x00-\x1f<>:"/\\|?*]/g, '_')
    .replace(/[. ]+$/g, '')
    .slice(0, 180);
  if (!cleaned || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(cleaned)) {
    throw new Error('Download filename is not safe');
  }
  return cleaned;
}

function newId() {
  return randomUUID();
}

module.exports = {
  UUID_RE,
  newId,
  normalizeServerUrl,
  parseDeepLink,
  required,
  safeExternalUrl,
  sameOrigin,
  sanitizeDownloadName,
};
