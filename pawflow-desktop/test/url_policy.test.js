'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  normalizeServerUrl,
  parseDeepLink,
  sameOrigin,
  safeExternalUrl,
  sanitizeDownloadName,
} = require('../src/url_policy');

test('normalizes an HTTPS server root', () => {
  assert.equal(normalizeServerUrl('https://Example.ORG:9443/'), 'https://example.org:9443');
  assert.throws(() => normalizeServerUrl('http://example.org'), /HTTPS/);
  assert.throws(() => normalizeServerUrl('https://user@example.org'), /HTTPS/);
  assert.throws(() => normalizeServerUrl('https://example.org/chat'), /without a path/);
});

test('pins navigation to scheme host and effective port', () => {
  assert.equal(sameOrigin('https://example.org', 'https://example.org/chat'), true);
  assert.equal(sameOrigin('https://example.org', 'https://example.org:444/chat'), false);
  assert.equal(sameOrigin('https://example.org', 'http://example.org/chat'), false);
});

test('parses only reviewed PawFlow deep links', () => {
  assert.deepEqual(
    parseDeepLink('pawflow://oauth?flow_id=flow-1&code=code-1'),
    { action: 'oauth', flowId: 'flow-1', code: 'code-1', error: '' },
  );
  const opened = parseDeepLink(
    'pawflow://open?server=123e4567-e89b-42d3-a456-426614174000'
    + '&conversation_id=123e4567-e89b-42d3-a456-426614174001',
  );
  assert.equal(opened.path, '/chat?conversation_id=123e4567-e89b-42d3-a456-426614174001');
  assert.throws(() => parseDeepLink(
    'pawflow://open?server=123e4567-e89b-42d3-a456-426614174000&path=https://evil.invalid',
  ), /not allowed/);
});

test('allows only safe external links and download names', () => {
  assert.equal(safeExternalUrl('https://example.org/docs'), 'https://example.org/docs');
  assert.throws(() => safeExternalUrl('file:///etc/passwd'), /HTTPS/);
  assert.equal(sanitizeDownloadName('../report?.pdf'), 'report_.pdf');
  assert.throws(() => sanitizeDownloadName('CON'), /not safe/);
});
