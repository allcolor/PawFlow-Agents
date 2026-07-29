// Behavioural tests for the simplified turn controller, run under Node with
// the local DOM stub. These exercise real state transitions -- placement of
// the final answer, coalescing of streamed text, and the eviction guard --
// rather than asserting that source strings exist.
//
// Run directly: node tests/js/turn_view_spec.js
// Run via pytest: tests/test_turn_view_js.py

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

// Fresh document + fresh module state for every test.
function env(mode) {
  delete require.cache[require.resolve(STUB)];
  const dom = require(STUB);
  const ctx = {
    document: dom.document,
    setTimeout: dom.setTimeout,
    clearTimeout: dom.clearTimeout,
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
  };
  vm.createContext(ctx);
  for (const file of ['turn_view.js', 'messages_render.js']) {
    vm.runInContext(fs.readFileSync(path.join(CHAT_UI, file), 'utf8'), ctx, { filename: file });
  }
  const messages = dom.document.createElement('div');
  messages.id = 'messages';
  dom.documentElement.appendChild(messages);

  const api = {
    ctx, dom, messages, clock: dom.clock,
    row(msgId, extraClass) {
      const el = dom.document.createElement('div');
      el.className = 'msg' + (extraClass ? ' ' + extraClass : '');
      if (msgId) el.dataset.msgid = msgId;
      messages.appendChild(el);
      return el;
    },
    block() { return messages.querySelector('.simple-turn-block'); },
    ephemeralText() {
      const el = messages.querySelector('.simple-turn-ephemeral-text');
      return el ? el.textContent : null;
    },
  };
  ctx.turnViewSetMode(mode);
  return api;
}

function startTurn(e, turnId) {
  const user = e.row(turnId);
  e.ctx.turnViewRegisterUser({ msg_id: turnId, turn_id: turnId }, user);
  return user;
}

// ── Classic mode must be completely inert ───────────────────────────────

test('classic mode ingests nothing and builds no block', () => {
  const e = env('classic');
  const user = startTurn(e, 'u1');
  const answer = e.row('a1');
  eq(e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a1' }, answer), false);
  eq(e.ctx.turnViewFinalize({ turn_id: 'u1', msg_id: 'a1', turn_final: true }), false);
  eq(e.block(), null, 'no activity block in classic mode');
  assert(answer.parentNode === e.messages, 'row must stay top level');
  assert(user.parentNode === e.messages, 'user row must stay top level');
});

test('classic mode eviction groups stay single-node', () => {
  const e = env('classic');
  const user = startTurn(e, 'u1');
  const group = e.ctx.turnViewEvictionGroup(user);
  eq(group.length, 1);
  assert(group[0] === user);
});

// ── Turn layout: user / block / final answer ────────────────────────────

test('final answer is placed after the block and intermediates inside it', () => {
  const e = env('simplified');
  const user = startTurn(e, 'u1');
  const narration = e.row('a1');
  eq(e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a1' }, narration), true);

  const block = e.block();
  assert(block, 'block created');
  assert(user.nextSibling === block, 'block sits right after the user message');
  assert(narration.parentNode !== e.messages, 'narration moved into a tab');

  const answer = e.row('a2');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a2', turn_final: true }, answer);
  assert(answer.parentNode === e.messages, 'final answer is top level');
  assert(block.nextSibling === answer, 'final answer sits right after the block');
});

test('a turn with no final answer leaves nothing after the block', () => {
  const e = env('simplified');
  const user = startTurn(e, 'u1');
  const narration = e.row('a1');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a1' }, narration);
  const block = e.block();
  eq(block.nextSibling, null, 'nothing is promoted out of the block');
});

// ── Derived vs authoritative final (pagination reconstruction) ──────────

test('a derived final never displaces an established final', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const real = e.row('a2');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a2', turn_final: true }, real);
  const block = e.block();
  assert(block.nextSibling === real);

  const guess = e.row('a1');
  e.ctx.turnViewIngest('assistant',
    { turn_id: 'u1', msg_id: 'a1', turn_final: true, turn_final_derived: true }, guess);

  assert(block.nextSibling === real, 'established final stays in place');
  assert(guess.parentNode !== e.messages, 'the rejected guess is filed into a tab');
});

test('an authoritative final displaces a derived one and reclaims it', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const guess = e.row('a1');
  e.ctx.turnViewIngest('assistant',
    { turn_id: 'u1', msg_id: 'a1', turn_final: true, turn_final_derived: true }, guess);
  const block = e.block();
  assert(block.nextSibling === guess, 'derived final is placed while it is all we have');

  const real = e.row('a2');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a2', turn_final: true }, real);

  assert(block.nextSibling === real, 'authoritative final wins');
  assert(guess.parentNode !== e.messages, 'the superseded row returns into the block');
});

// ── Streaming: one cue per coalescing window, not one per token ─────────

test('streamed tokens coalesce into a single cue', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const stream = e.row('a1');
  for (let i = 0; i < 50; i++) {
    e.ctx.turnViewIngest('token', { turn_id: 'u1', msg_id: 'a1', content: 'tok' + i }, stream);
  }
  eq(e.ephemeralText(), '', 'no cue is rendered before the coalescing window closes');
  e.clock.tick(300);
  eq(e.ephemeralText(), 'tok49', 'exactly one cue, carrying the newest excerpt');
});

test('discrete tool cues are not delayed by coalescing', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const call = e.row('c1');
  e.ctx.turnViewIngest('tool_call', { turn_id: 'u1', msg_id: 'c1', tc_id: 'tc-1' }, call);
  eq(e.ephemeralText(), 'Calling tool...', 'tool cue renders immediately');
});

test('identity is not re-rendered on every token', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const stream = e.row('a1');
  e.ctx.turnViewIngest('token',
    { turn_id: 'u1', msg_id: 'a1', content: 'x', agent_name: 'assistant', llm_service: 'svc' }, stream);

  const header = e.block().querySelector('.simple-turn-header');
  const original = header.querySelector.bind(header);
  let lookups = 0;
  header.querySelector = sel => { lookups++; return original(sel); };

  for (let i = 0; i < 50; i++) {
    e.ctx.turnViewIngest('token',
      { turn_id: 'u1', msg_id: 'a1', content: 'tok' + i, agent_name: 'assistant', llm_service: 'svc' },
      stream);
  }
  assert(lookups <= 4, 'header was re-queried ' + lookups + ' times for 50 tokens');
});

test('a changed agent identity is still rendered', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const stream = e.row('a1');
  e.ctx.turnViewIngest('token',
    { turn_id: 'u1', msg_id: 'a1', content: 'x', agent_name: 'alpha', llm_service: 'svc' }, stream);
  eq(e.block().querySelector('.simple-turn-title').textContent, 'alpha');
  e.ctx.turnViewIngest('token',
    { turn_id: 'u1', msg_id: 'a1', content: 'y', agent_name: 'beta', llm_service: 'svc' }, stream);
  eq(e.block().querySelector('.simple-turn-title').textContent, 'beta');
});

// ── Live-window eviction ────────────────────────────────────────────────

function fillRows(e, count) {
  const made = [];
  for (let i = 0; i < count; i++) made.push(e.row('filler-' + i));
  return made;
}

test('eviction never destroys a turn whose block is still live', () => {
  const e = env('simplified');
  const user = startTurn(e, 'u1');
  const call = e.row('c1');
  call.dataset.live = '1';
  e.ctx.turnViewIngest('tool_call', { turn_id: 'u1', msg_id: 'c1', tc_id: 'tc-1' }, call);
  const block = e.block();

  fillRows(e, 205);
  e.ctx.trimLiveDisplayWindowIfAutoscrolling(true);

  assert(user.isConnected, 'the running turn user row survives');
  assert(block.isConnected, 'the running turn block survives');
});

test('eviction still trims plain rows in classic mode', () => {
  const e = env('classic');
  fillRows(e, 206);
  eq(e.messages.children.length, 206);
  e.ctx.trimLiveDisplayWindowIfAutoscrolling(true);
  eq(e.messages.children.length, 200, 'classic trimming is unchanged');
});

test('eviction is a no-op when not autoscrolling', () => {
  const e = env('classic');
  fillRows(e, 206);
  e.ctx.trimLiveDisplayWindowIfAutoscrolling(false);
  eq(e.messages.children.length, 206);
});

// ── Terminal status ─────────────────────────────────────────────────────

test('failure finalizes the block without inventing an answer', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const narration = e.row('a1');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a1' }, narration);
  const block = e.block();
  eq(e.ctx.turnViewFail('u1', 'cancelled'), true);
  assert(block.querySelector('.simple-turn-status').classList.contains('cancelled'));
  eq(block.nextSibling, null, 'no answer is manufactured on cancellation');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const f of failures) console.error('  - ' + f);
  process.exit(1);
}
console.log(passed + ' passing');
