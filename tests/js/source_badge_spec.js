// Behavioural tests for sourceBadge (tasks/io/chat_ui/messages.js).
//
// A delegate reply is persisted with source.type = 'agent_delegate' and its
// identity in from/to. When that durable row reclaims the live token bubble
// (sse_handlers_a.js new_message reconciliation), the bubble content is
// rebuilt from sourceBadge(data.source): an empty badge erased the
// "Agent via service" header the stream had drawn.
//
// Run directly: node tests/js/source_badge_spec.js
// Run via pytest: tests/test_source_badge_js.py

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CHAT_UI = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui');

let passed = 0;
const failures = [];

function test(name, fn) {
  try { fn(); passed++; }
  catch (err) { failures.push(name + ': ' + (err && err.message ? err.message : err)); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }

function env() {
  const ctx = {
    console,
    escapeHtml: s => String(s === null || s === undefined ? '' : s)
      .replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    displayAgentName: n => n,
  };
  vm.createContext(ctx);
  vm.runInContext('globalThis.window = globalThis;', ctx, { filename: 'window.js' });
  vm.runInContext(fs.readFileSync(path.join(CHAT_UI, 'messages.js'), 'utf8'), ctx, { filename: 'messages.js' });
  return ctx;
}

test('a delegate reply keeps the author via service header', () => {
  const ctx = env();
  const html = ctx.sourceBadge({
    type: 'agent_delegate', from: 'GameDev7', to: 'GameDev2', kind: 'reply',
    llm_service: 'codex_interactive_llm_service',
  });
  assert(html.includes('source-badge'), 'a badge is rendered: ' + html);
  assert(html.includes('GameDev7 via codex_interactive_llm_service'),
    'the author and its service are named: ' + html);
  assert(html.includes('\u2192 GameDev2'), 'the addressee is named: ' + html);
});

test('a delegate source is not mutated by rendering it', () => {
  const ctx = env();
  const src = { type: 'agent_delegate', from: 'A', to: 'B', llm_service: 's' };
  ctx.sourceBadge(src);
  assert(src.type === 'agent_delegate' && !src.name, 'the caller keeps its source');
});

test('a plain agent source is unchanged', () => {
  const ctx = env();
  const html = ctx.sourceBadge({ type: 'agent', name: 'claude', llm_service: 'svc' });
  assert(html.includes('claude via svc'), html);
  assert(!html.includes('\u2192'), 'no addressee without reply_to: ' + html);
});

if (failures.length) {
  console.error(failures.join('\n'));
  console.error(passed + ' passed, ' + failures.length + ' failed');
  process.exit(1);
}
console.log(passed + ' passed');
