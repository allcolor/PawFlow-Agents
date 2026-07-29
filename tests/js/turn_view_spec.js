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

// ── Boundaries are positional: no turn_id required anywhere ─────────────
//
// The block holds everything between a user message and the terminal answer,
// or between two user messages. Correlation metadata is a refinement, never a
// precondition -- a turn nobody stamped must group exactly the same.

test('a turn with no turn_id anywhere still groups', () => {
  const e = env('simplified');
  const user = e.row('u1');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, user);        // no turn_id

  const narration = e.row('a1');
  eq(e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, narration), true);
  const call = e.row('c1');
  eq(e.ctx.turnViewIngest('tool_call', { msg_id: 'c1', tc_id: 'tc-1' }, call), true);

  const block = e.block();
  assert(block, 'block created from the user boundary alone');
  assert(user.nextSibling === block, 'block sits right after the user message');
  assert(narration.parentNode !== e.messages, 'narration moved into a tab');
  assert(call.parentNode !== e.messages, 'tool call moved into a tab');

  const answer = e.row('a2');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a2' }, answer);
  e.ctx.turnViewFinalize({ msg_id: 'a2', final_msg_id: 'a2' });
  assert(block.nextSibling === answer, 'terminal answer lifted out after the block');
});

test('a user message with no id at all still opens its own turn', () => {
  const e = env('simplified');
  const user = e.row('');
  e.ctx.turnViewRegisterUser({}, user);
  eq(e.ctx.turnViewIngest('assistant', {}, e.row('a1')), true);
  assert(e.block(), 'block created without a single identifier');
});

test('a second user message closes the previous turn and opens a new block', () => {
  const e = env('simplified');
  const user1 = e.row('u1');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, user1);
  const first = e.row('a1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, first);
  const block1 = e.block();

  const user2 = e.row('u2');
  e.ctx.turnViewRegisterUser({ msg_id: 'u2' }, user2);
  const second = e.row('a2');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a2' }, second);

  const blocks = e.messages.querySelectorAll('.simple-turn-block');
  eq(blocks.length, 2, 'one block per turn');
  assert(blocks[0] === block1, 'the first block is untouched');
  assert(user2.nextSibling === blocks[1], 'the second block follows the second user message');
  assert(second.parentNode !== e.messages, 'the later row joined the later turn');
  assert(first.parentNode !== e.messages, 'the earlier row stayed in the earlier turn');
});

test('a user message before the answer gives user / block / user / block / answer', () => {
  const e = env('simplified');
  const user1 = e.row('u1');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, user1);
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a1' }, e.row('a1'));

  // The user speaks again before the agent is done.
  const user2 = e.row('u2');
  e.ctx.turnViewRegisterUser({ msg_id: 'u2' }, user2);
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a2' }, e.row('a2'));

  // The done event still carries the FIRST turn's id -- position must win.
  const answer = e.row('a3');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a3' }, answer);
  e.ctx.turnViewFinalize({ turn_id: 'u1', final_msg_id: 'a3' });

  eq(topLevelIds(e).join(','), 'u1,BLOCK,u2,BLOCK,a3');
});

test('a user message nothing followed gets no empty block', () => {
  const e = env('simplified');
  const user = e.row('u1');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, user);
  e.ctx.turnViewReconcile();
  eq(e.block(), null, 'a pending prompt is not given a block of its own');
  eq(e.ctx.turnViewFail('u1', 'cancelled'), false, 'a failure builds no block either');
  eq(e.block(), null);
});

test('rows before any user message are left top level', () => {
  const e = env('simplified');
  const orphan = e.row('old-1');
  eq(e.ctx.turnViewIngest('assistant', { msg_id: 'old-1' }, orphan), false);
  eq(e.block(), null, 'no block without a user boundary');
  assert(orphan.parentNode === e.messages, 'row stays top level');
});

// ── Reload: the whole turn must rebuild from classifier rows ────────────
//
// A restart plus a hard reload replays history through _renderHistoryRow, not
// through live SSE. That path was never exercised here, and it is the one the
// user hits first after a restart.

// The rows tasks/ai/agent_serialization.py emits for one completed turn.
const HISTORY_ROWS = [
  { type: 'user', role: 'user', msg_id: 'u1', turn_id: 'u1' },
  { type: 'assistant', role: 'assistant', msg_id: 'a1', turn_id: 'u1', turn_final: false, content: 'Je regarde.' },
  { type: 'thinking', role: 'thinking', msg_id: 't1', turn_id: 'u1', turn_final: false, content: 'hmm' },
  { type: 'tool_call', role: 'tool_call', msg_id: 'c1', tc_id: 'tc-1', turn_id: 'u1', turn_final: false },
  { type: 'tool_result', role: 'tool', msg_id: 'r1', tc_id: 'tc-1', turn_id: 'u1', turn_final: false },
  { type: 'assistant', role: 'assistant', msg_id: 'a2', turn_id: 'u1', turn_final: true, content: 'Le CHANGELOG est pret.' },
];

// Same branch _renderHistoryRow takes, minus addMsg.
function replayHistory(e, rows) {
  for (const m of rows) {
    const el = e.row(m.msg_id);
    const role = m.type || m.role;
    const data = Object.assign({}, m, { _history: true });
    if (role === 'user') e.ctx.turnViewRegisterUser(data, el);
    else e.ctx.turnViewIngest(role, data, el);
  }
  e.ctx.turnViewReconcile();
}

function topLevelIds(e) {
  return Array.from(e.messages.children)
    .map(el => el.dataset.msgid || (el.className.indexOf('simple-turn-block') >= 0 ? 'BLOCK' : '?'))
    .filter(Boolean);
}

test('a reloaded turn shows only the user message, the block and the answer', () => {
  const e = env('simplified');
  replayHistory(e, HISTORY_ROWS);
  eq(topLevelIds(e).join(','), 'u1,BLOCK,a2');
});

test('a reloaded turn files every intermediate row into its tab', () => {
  const e = env('simplified');
  replayHistory(e, HISTORY_ROWS);
  const rows = key => {
    const panel = e.block().querySelector('#turn-panel-u1-' + key + ' .simple-turn-panel-scroll');
    return panel ? panel.children.length : -1;
  };
  eq(rows('messages'), 1, 'narration in Messages');
  eq(rows('thinking'), 1, 'thinking in Thinking');
  eq(rows('tools'), 2, 'call and result in Tool calls');
});

test('a reloaded block is expandable and exposes its four tabs', () => {
  const e = env('simplified');
  replayHistory(e, HISTORY_ROWS);
  const block = e.block();
  eq(block.querySelectorAll('.simple-turn-tab').length, 4);
  const header = block.querySelector('.simple-turn-header');
  assert(header, 'header exists');
  header.click();
  eq(header.getAttribute('aria-expanded'), 'true', 'clicking the header expands');
  assert(block.className.indexOf('expanded') >= 0, 'block carries the expanded class');
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
