// Behavioural tests for the rendering half of in-flight hydration.
//
// The server stamps `live` on the tool_call rows the relay still has in
// flight (ToolRelayService.inflight_snapshot -> load_history). That is worth
// nothing unless the renderer treats a replayed row exactly like a streamed
// one: pending bullet, BG and Kill. Nothing in the row says where it came
// from, and that is the point -- these tests hold the renderer to it.
//
// Run directly: node tests/js/inflight_hydration_spec.js
// Run via pytest: tests/test_inflight_hydration_js.py

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CHAT_UI = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui');
const STUB = path.join(__dirname, 'dom_stub.js');

let passed = 0;
const failures = [];

function test(name, fn) {
  try { fn(); passed++; }
  catch (err) { failures.push(name + ': ' + (err && err.message ? err.message : err)); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }
function eq(actual, expected, msg) {
  if (actual !== expected) {
    throw new Error((msg ? msg + ': ' : '') + 'expected ' + JSON.stringify(expected)
      + ' but got ' + JSON.stringify(actual));
  }
}

function env() {
  delete require.cache[require.resolve(STUB)];
  const dom = require(STUB);
  const ctx = {
    document: dom.document,
    setTimeout: dom.setTimeout,
    clearTimeout: dom.clearTimeout,
    setInterval: dom.setInterval,
    clearInterval: dom.clearInterval,
    // The stub's Date is a clock, not a constructor, and the row renderer
    // both stamps Date.now() and formats a timestamp.
    Date: class extends Date { static now() { return dom.Date.now(); } },
    console,
    CSS: { escape: s => String(s).replace(/["\\\]]/g, '\\$&') },
    t: k => k,
    escapeHtml: s => String(s === null || s === undefined ? '' : s)
      .replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    displayAgentName: n => n,
    displayWindow: 50,
    hasMoreMessages: false,
    _selectedMsgIds: new Set(),
    _seenMsgIds: new Set(),
    _updateLoadMoreBanner: () => {},
    pawflowDebugLog: () => {},
    isNearBottom: () => true,
    scrollBottom: () => {},
    applyTechnicalMessageGrouping: () => {},
  };
  vm.createContext(ctx);
  // The sources read feature flags off `window`; in a page that is the global
  // object, and here it has to be said out loud.
  vm.runInContext('globalThis.window = globalThis;', ctx, { filename: 'window.js' });
  for (const file of ['messages.js', 'messages_tools.js', 'turn_view.js',
                      'messages_render.js']) {
    vm.runInContext(fs.readFileSync(path.join(CHAT_UI, file), 'utf8'), ctx, { filename: file });
  }
  const messages = dom.document.createElement('div');
  messages.id = 'messages';
  dom.documentElement.appendChild(messages);
  ctx.turnViewSetMode('classic');
  return { ctx, dom, messages };
}

// The row a reload rebuilds for a call the relay is still running.
function historyToolCall(extra) {
  return Object.assign({
    tool_name: 'bash',
    agent_name: 'claude',
    arguments: { command: 'sleep 300' },
    tc_id: 'toolu_1',
    tool_call_id: 'toolu_1',
    msg_id: 'm1',
    _history: true,
  }, extra || {});
}

test('a replayed row the server calls live is rendered as running', () => {
  const e = env();

  const el = e.ctx.addMsg('tool_call', '', historyToolCall({ live: true }));

  assert(el, 'the row was rendered');
  eq(el.dataset.live, '1', 'the row carries the live marker');
  assert(el.querySelector('.tc-bullet.pending'), 'its bullet is pending');
  assert(el.querySelector('.tc-bg-btn'), 'BG is offered');
  assert(el.querySelector('.tc-kl-btn'), 'Kill is offered');
  eq(el.dataset.tcId, 'toolu_1',
    'the row is addressable by its call id, so BG/Kill reach the running request');
});

test('a replayed row with no live flag stays finished', () => {
  const e = env();

  const el = e.ctx.addMsg('tool_call', '', historyToolCall());

  assert(el, 'the row was rendered');
  eq(el.dataset.live, undefined, 'nothing marks it live');
  assert(el.querySelector('.tc-bullet.done'), 'its bullet is done');
  assert(!el.querySelector('.tc-bg-btn'), 'no BG on a finished call');
  assert(!el.querySelector('.tc-kl-btn'), 'no Kill on a finished call');
});

test('a delegate row hydrates live the same way', () => {
  // The delegate branch renders its own inner row; it reads the same flag and
  // had the same blind spot.
  const e = env();

  const el = e.ctx.addMsg('tool_call', '', historyToolCall({
    live: true,
    source: { type: 'agent_delegate', from: 'claude', to: 'worker' },
  }));

  assert(el, 'the row was rendered');
  const host = el.parentNode || el;
  const root = host.querySelector ? host : el;
  assert(root.querySelector('.tc-bullet.pending'),
    'the delegate inner row is pending too');
  assert(root.querySelector('.tc-kl-btn'), 'and it can be killed');
});

test('the result that finally arrives closes the hydrated row', () => {
  const e = env();
  const el = e.ctx.addMsg('tool_call', '', historyToolCall({ live: true }));

  e.ctx.addMsg('tool_result', 'done', { tc_id: 'toolu_1', msg_id: 'm2' });

  assert(el.querySelector('.tc-result'), 'the result attached to the live row');
  assert(!el.querySelector('.tc-bullet.pending'),
    'a call that answered is no longer pending');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const f of failures) console.error('  - ' + f);
  process.exit(1);
}
console.log(passed + ' passing');
