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
    setInterval: dom.setInterval,
    clearInterval: dom.clearInterval,
    Date: dom.Date,
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
      const els = messages.querySelectorAll('.simple-turn-ephemeral-text');
      const last = els[els.length - 1];
      return last ? last.textContent : null;
    },
    cues() { return messages.querySelectorAll('.simple-turn-cue').length; },
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
  assert(block.nextSibling === narration, 'the last message so far is readable outside');
  assert(call.parentNode !== e.messages, 'tool call moved into a tab');

  const answer = e.row('a2');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a2' }, answer);
  e.ctx.turnViewFinalize({ msg_id: 'a2', final_msg_id: 'a2' });
  assert(block.nextSibling === answer, 'terminal answer lifted out after the block');
  assert(narration.parentNode !== e.messages, 'the message it replaced went back in');
});

// The compact notice is born of a compact_progress event, not of a message
// event, so it was the one row-creating path left uninstrumented: it sat at
// top level, outside every block, for the rest of the conversation. Ingesting
// it is only half the fix -- it must not then displace the answer, which is
// what happens to anything that lands in the messages tab live.
test('a system notice is filed in the block, never in the outside spot', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const answer = e.row('a1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, answer);
  const block = e.block();
  assert(block.nextSibling === answer, 'the answer holds the outside spot');

  const notice = e.row('s1');
  eq(e.ctx.turnViewIngest('system', {}, notice), true, 'the notice is ingested');
  assert(notice.parentNode !== e.messages, 'it does not stay at top level');
  assert(block.nextSibling === answer, 'and it does not displace the answer');
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
  assert(block1.nextSibling === first, 'the first turn keeps its last message readable');
  assert(first.nextSibling === user2, 'and it sits before the message that follows it');
  assert(user2.nextSibling === blocks[1], 'the second block follows the second user message');
  assert(blocks[1].nextSibling === second, 'the second turn shows its own last message');
});

// A turn the server never closed is closed by the next thing the user says:
// leaving it on "working", with its clock still running, above a message the
// reader has already moved past, states something false.
test('a new user message closes the block above it', () => {
  const e = env('simplified');
  const user1 = e.row('u1');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, user1);
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, e.row('a1'));
  const block1 = e.block();
  assert(block1.classList.contains('turn-working'), 'it is working while it runs');

  e.ctx.turnViewRegisterUser({ msg_id: 'u2' }, e.row('u2'));

  assert(!block1.classList.contains('turn-working'), 'and not once the user has moved on');
  eq(block1.querySelector('.simple-turn-status').textContent, 'Completed');
});

test('a block that ended badly keeps saying so', () => {
  const e = env('simplified');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, e.row('u1'));
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, e.row('a1'));
  const block1 = e.block();
  eq(e.ctx.turnViewFail('u1', 'error', 'boom'), true);

  e.ctx.turnViewRegisterUser({ msg_id: 'u2' }, e.row('u2'));

  assert(block1.querySelector('.simple-turn-status').classList.contains('error'),
         'closing the turn must not overwrite how it ended');
});

test('a correlated late answer stays with its own turn', () => {
  const e = env('simplified');
  const user1 = e.row('u1');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, user1);
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a1' }, e.row('a1'));

  // The user speaks again before the agent is done.
  const user2 = e.row('u2');
  e.ctx.turnViewRegisterUser({ msg_id: 'u2' }, user2);
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a2' }, e.row('a2'));

  // The done event still carries the FIRST turn's id. Durable correlation wins
  // even though another user row has since opened a different turn.
  const answer = e.row('a3');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a3' }, answer);
  e.ctx.turnViewFinalize({ turn_id: 'u1', final_msg_id: 'a3' });

  eq(topLevelIds(e).join(','), 'u1,BLOCK,a3,u2');

  const answer2 = e.row('b1');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u2', msg_id: 'b1' }, answer2);
  eq(topLevelIds(e).join(','), 'u1,BLOCK,a3,u2,BLOCK,b1');
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

// Activity with no user row above it is still a turn. Leaving those rows at
// top level is what made the view collapse to a flat transcript on a long
// conversation: the history window opens in the middle of a turn, the user
// message that started it is hundreds of rows above, and every tool call,
// thought and message rendered inline with no block anywhere.
test('activity with no user row above it opens a turn of its own', () => {
  const e = env('simplified');
  const orphan = e.row('old-1');
  eq(e.ctx.turnViewIngest('assistant', { msg_id: 'old-1' }, orphan), true);
  const block = e.block();
  assert(block, 'a block is built for the agent-opened turn');
  // USER > BLOCK > last message, minus the user row nobody has: the block
  // takes the row's place and the row becomes the message below it.
  eq(block.nextSibling, orphan, 'the row is the block\'s last message');
  eq(e.messages.children[0], block, 'the block sits where the row was');
});

// The rule, whatever happened before: top level holds a user row, its block,
// and that block's last message -- nothing else. This is the pass that makes
// it true after a reload, where rows are replayed through a path that may
// never have reached the view at all.
test('reconcile files stray rows into the turn they fall in', () => {
  const e = env('simplified');
  const user = e.row('u1');
  user.dataset.messageRole = 'user';
  const think = e.row('t1'); think.dataset.messageRole = 'thinking';
  const call = e.row('c1'); call.dataset.messageRole = 'tool_call';
  const answer = e.row('a1'); answer.dataset.messageRole = 'assistant';
  answer.dataset.rawText = 'the answer';
  e.ctx.turnViewReconcile();
  const block = e.block();
  assert(block, 'the turn got its block');
  const top = Array.from(e.messages.children);
  eq(top.length, 3, 'top level is user + block + last message');
  eq(top[0], user, 'user row first');
  eq(top[1], block, 'block second');
  eq(top[2], answer, 'the last message below the block');
  assert(think.parentNode !== e.messages, 'thinking went into the block');
  assert(call.parentNode !== e.messages, 'the tool call went into the block');
});

// A turn the server closed does not orphan what comes after it. The provider
// ends a turn, the agent goes back to work without a new user message, and
// every following row must still land in a block.
test('activity after a finished turn keeps a block', () => {
  const e = env('simplified');
  const user = startTurn(e, 'u1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, e.row('a1'));
  e.ctx.turnViewFinalize({ final_msg_id: 'a1' });
  const late = e.row('c9');
  eq(e.ctx.turnViewIngest('tool_call', { msg_id: 'c9' }, late), true);
  assert(late.parentNode !== e.messages, 'the late tool call is in a block');
  assert(user.isConnected, 'the user row is untouched');
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
  assert(block.nextSibling === narration, 'until something newer arrives, it is the answer');

  const answer = e.row('a2');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a2', turn_final: true }, answer);
  assert(answer.parentNode === e.messages, 'final answer is top level');
  assert(block.nextSibling === answer, 'final answer sits right after the block');
  assert(narration.parentNode !== e.messages, 'the intermediate one is back in the block');
});

// The failure on screen: a turn whose done named no final message left its only
// answer inside a collapsed tab, under a header that still said "working".
test('a turn the server never closed still shows its last message', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const only = e.row('a1');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a1' }, only);
  const block = e.block();
  assert(block.nextSibling === only, 'the last message is outside the block');

  // A done that names nothing at all still ends the turn.
  eq(e.ctx.turnViewFinalize({ turn_id: 'u1' }), true);
  assert(block.nextSibling === only, 'and it stays there');
  eq(block.querySelector('.simple-turn-status').textContent, 'Completed');
});

// ── Derived vs authoritative final (pagination reconstruction) ──────────

test('a derived final never displaces an established final', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const real = e.row('a2');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a2', turn_final: true }, real);
  const block = e.block();
  assert(block.nextSibling === real);

  // A derived final comes from replaying an older page: it arrives last but it
  // is not the last row of the turn, so the positional rule must not see it.
  const guess = e.row('a1');
  e.ctx.turnViewIngest('assistant',
    { turn_id: 'u1', msg_id: 'a1', turn_final: true, turn_final_derived: true, _history: true },
    guess);

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
  eq(e.cues(), 0, 'no cue is rendered before the coalescing window closes');
  e.clock.tick(300);
  eq(e.cues(), 1, 'exactly one cue for the window');
  // It condenses out of the rain: the text starts as glyphs and resolves.
  assert(e.ephemeralText() !== 'tok49', 'the cue arrives scrambled, not typed out');
  e.clock.tick(14 * 40);
  eq(e.ephemeralText(), 'tok49', 'and resolves to the newest excerpt');
});

// The scramble is decoration over a value that is always there: whatever the
// animation is doing, the cue ends on the real text and leaves no timer behind.
test('a cue always resolves to its true text', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  e.ctx.turnViewIngest('token', { msg_id: 'a1', content: 'compiling the plan' }, e.row('a1'));
  e.clock.tick(300 + 14 * 40 + 200);
  eq(e.ephemeralText(), 'compiling the plan');
  const before = e.dom.clock.timers.size;
  e.clock.tick(5000);
  assert(e.dom.clock.timers.size <= before, 'the scramble stopped rescheduling itself');
});

// ── The cue stack: entries coexist, newest in front, oldest pushed off ──

test('cues stack instead of taking turns, and the stack is bounded', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  for (let i = 0; i < 3; i++) {
    e.ctx.turnViewIngest('tool_call', { msg_id: 'c' + i, tc_id: 'tc-' + i }, e.row('c' + i));
  }
  eq(e.cues(), 3, 'three cues are on screen at once');
  for (let i = 3; i < 9; i++) {
    e.ctx.turnViewIngest('tool_call', { msg_id: 'c' + i, tc_id: 'tc-' + i }, e.row('c' + i));
  }
  eq(e.cues(), 4, 'the stack is capped, the oldest fall off the back');
});

// Nothing on this surface disappears because a timer said so: what the reader
// last saw stays readable until there is something newer to read.
test('a cue only leaves when a newer one pushes it out', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  e.ctx.turnViewIngest('tool_call', { msg_id: 'c1', tc_id: 'tc-1' }, e.row('c1'));
  eq(e.cues(), 1);
  e.clock.tick(60000);
  eq(e.cues(), 1, 'a minute of silence does not blank the surface');
  e.ctx.turnViewIngest('tool_call', { msg_id: 'c2', tc_id: 'tc-2' }, e.row('c2'));
  eq(e.cues(), 2, 'the older one is pushed back, not removed');
});

// The rain is the resting state of the surface: it runs while the turn runs,
// on one shared ticker, and leaves nothing behind when the turn ends.
test('the rain runs while the turn runs and stops with it', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, e.row('a1'));
  const rain = () => e.messages.querySelectorAll('.simple-turn-rain').length;
  eq(rain(), 1, 'a running turn rains');

  e.ctx.turnViewFinalize({ msg_id: 'a1', final_msg_id: 'a1' });
  eq(rain(), 0, 'a finished one does not');
  const idle = e.dom.clock.timers.size;
  e.clock.tick(10000);
  eq(e.dom.clock.timers.size, idle, 'and nothing keeps ticking for it');
});

test('two running turns share one ticker', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, e.row('a1'));
  const oneTurn = e.dom.clock.timers.size;
  e.ctx.turnViewRegisterUser({ msg_id: 'u2' }, e.row('u2'));
  e.ctx.turnViewIngest('assistant', { msg_id: 'a2' }, e.row('a2'));

  eq(e.messages.querySelectorAll('.simple-turn-rain').length, 1,
     'the closed turn stopped raining, the new one started');
  assert(e.dom.clock.timers.size <= oneTurn + 1,
         'a second block must not mean a second rain ticker');
});

// Between two events the band must not read as a stall.
test('an empty surface shows the turn is still alive', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  e.ctx.turnViewIngest('token', { msg_id: 'a1', content: 'x' }, e.row('a1'));
  const idle = () => e.messages.querySelectorAll('.simple-turn-idle').length;
  eq(idle(), 1, 'a block with no cue yet pulses instead of sitting blank');
  e.clock.tick(300);
  eq(e.cues(), 1);
  eq(idle(), 0, 'and steps aside as soon as there is something to show');
});

test('a finished turn drops every cue it had on screen', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  e.ctx.turnViewIngest('tool_call', { msg_id: 'c1', tc_id: 'tc-1' }, e.row('c1'));
  e.ctx.turnViewIngest('tool_call', { msg_id: 'c2', tc_id: 'tc-2' }, e.row('c2'));
  eq(e.cues(), 2);
  const answer = e.row('a1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, answer);
  e.ctx.turnViewFinalize({ msg_id: 'a1', final_msg_id: 'a1' });
  eq(e.cues(), 0, 'nothing keeps animating under a completed block');
});

test('discrete tool cues are not delayed by coalescing', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const call = e.row('c1');
  call.appendChild(e.dom.document.createElement('div')).textContent = 'edit(path="x.js")';
  e.ctx.turnViewIngest('tool_call', { turn_id: 'u1', msg_id: 'c1', tc_id: 'tc-1' }, call);
  eq(e.cues(), 1, 'tool cue renders immediately');
});

// "Calling tool..." named neither the tool nor its arguments -- the one surface
// meant to say what is happening said the least at the moment worth watching.
test('a tool cue shows the call itself, copied, not a label', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const call = e.row('c1');
  call.appendChild(e.dom.document.createElement('div')).textContent = 'edit(path="x.js")';
  e.ctx.turnViewIngest('tool_call', { turn_id: 'u1', msg_id: 'c1', tc_id: 'tc-1' }, call);

  e.clock.tick(14 * 40);
  const copy = e.messages.querySelectorAll('.simple-turn-cue-copy');
  eq(copy.length, 1, 'the cue carries a copy of the rendered call');
  assert(copy[0].textContent.indexOf('edit(path="x.js")') >= 0, 'with what the call says');
  assert(copy[0] !== call, 'a copy, never the canonical row');
  assert(call.parentNode !== e.messages, 'which stays filed in its tab');
  eq(copy[0].getAttribute('data-msgid'), null, 'the copy carries no identity of its own');
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
  // What it said before it was cancelled is still what it said: the last
  // message stays readable. Nothing is invented on top of it.
  assert(block.nextSibling === narration, 'the last message it produced is still shown');
  eq(narration.nextSibling, null, 'and nothing was manufactured after it');
});

// The counter is the only thing that keeps moving through a long silence, and
// it is what tells the reader how long that silence has been.
test('the header counts the seconds and freezes them at the end', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, e.row('a1'));
  const elapsed = () => e.block().querySelector('.simple-turn-elapsed').textContent;
  eq(elapsed(), '0s');
  e.clock.tick(7000);
  eq(elapsed(), '7s', 'it ticks while the turn runs');
  e.clock.tick(95000);
  eq(elapsed(), '1m 42s', 'and stays readable past a minute');

  e.ctx.turnViewFinalize({ msg_id: 'a1', final_msg_id: 'a1' });
  const atEnd = elapsed();
  e.clock.tick(60000);
  eq(elapsed(), atEnd, 'a finished turn keeps the time it took');
});

// The ephemeral surface is laid out by CSS only while .turn-working is set.
// A reloaded transcript is nothing but finished turns, so a block that keeps
// the class after a terminal event gives every one of them an empty band.
test('the working class tracks the status and is dropped on every terminal path', () => {
  for (const finish of [
    e => e.ctx.turnViewFinalize({ msg_id: 'a1', final_msg_id: 'a1' }),
    e => e.ctx.turnViewFail('u1', 'cancelled'),
    e => e.ctx.turnViewFail('u1', 'error', 'boom'),
  ]) {
    const e = env('simplified');
    startTurn(e, 'u1');
    e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, e.row('a1'));
    const block = e.block();
    assert(block.classList.contains('turn-working'), 'a running turn animates');
    eq(finish(e), true);
    assert(!block.classList.contains('turn-working'), 'a finished turn does not');
  }
});

// The whole contract in one sequence, which is what the reader actually sees:
//
//   USER > finished block > its last message > USER > running block > its last
//
// A message sent while a turn is still working closes that turn -- it keeps
// its own last message under it -- and opens the next one.
test('a message sent mid-turn closes the block and opens the next', () => {
  const e = env('simplified');
  const user1 = startTurn(e, 'u1');
  const first = e.row('a1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, first);
  const block1 = e.block();
  const user2 = e.row('u2');
  user2.dataset.messageRole = 'user';
  e.ctx.turnViewRegisterUser({ msg_id: 'u2' }, user2);
  const second = e.row('a2');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a2' }, second);
  const blocks = e.messages.querySelectorAll('.simple-turn-block');
  eq(blocks.length, 2, 'one block per turn');
  const top = Array.from(e.messages.children);
  eq(top[0], user1, 'first user row');
  eq(top[1], block1, 'its block');
  eq(top[2], first, 'its last message, under it');
  eq(top[3], user2, 'the new user row');
  eq(top[4], blocks[1], 'the new block');
  eq(top[5], second, 'and its last message');
  eq(top.length, 6, 'nothing else at top level');
});

// The reported failure, end to end. A long autonomous run: the user message
// that opened the turn is hundreds of rows above, so the 50-row history window
// holds nothing but activity. Every one of those rows used to render inline --
// no block anywhere, the simplified view indistinguishable from a broken
// classic one -- and every reload reproduced it exactly.
test('a reload whose window opens mid-turn still shows one block', () => {
  const e = env('simplified');
  const rows = [];
  for (const spec of [['thinking', 't1', ''], ['tool_call', 'c1', ''],
                      ['tool_result', 'r1', ''], ['assistant', 'a1', 'the answer']]) {
    const el = e.row(spec[1]);
    el.dataset.messageRole = spec[0];
    if (spec[2]) el.dataset.rawText = spec[2];
    rows.push(el);
    e.ctx.turnViewIngest(spec[0], { msg_id: spec[1], _history: true }, el);
  }
  e.ctx.turnViewReconcile();
  const block = e.block();
  assert(block, 'the window got a block even with no user row in it');
  const top = Array.from(e.messages.children);
  eq(top.length, 2, 'top level is the block and its last message');
  eq(top[0], block, 'block first');
  eq(top[1], rows[3], 'the answer under it');
  for (const el of rows.slice(0, 3)) {
    assert(el.parentNode !== e.messages, 'activity is inside the block');
  }
});

// After a reload, the work carries on. The rows that arrive next are live, and
// they must land in the block the reconciliation left open -- not at top level,
// which is what turned the rest of a long session into a flat list.
test('live rows after a reload land in the reconciled turn', () => {
  const e = env('simplified');
  const user = e.row('u1');
  user.dataset.messageRole = 'user';
  const answer = e.row('a1');
  answer.dataset.messageRole = 'assistant';
  answer.dataset.rawText = 'replayed answer';
  e.ctx.turnViewReconcile();
  const block = e.block();
  assert(block, 'the replayed turn has a block');
  const late = e.row('c9');
  eq(e.ctx.turnViewIngest('tool_call', { msg_id: 'c9' }, late), true);
  assert(late.parentNode !== e.messages, 'the live tool call went into the block');
  eq(e.messages.querySelectorAll('.simple-turn-block').length, 1,
    'and no second block was invented for it');
});

// Loading an older page while the agent is still working temporarily replays
// old user boundaries. Those boundaries must not complete the live block; the
// final DOM pass restores the newest turn as the open one.
test('loading older history does not complete the live turn', () => {
  const e = env('simplified');
  const liveUser = startTurn(e, 'u-live');
  const liveAnswer = e.row('a-live');
  liveAnswer.dataset.messageRole = 'assistant';
  liveAnswer.dataset.rawText = 'still working';
  e.ctx.turnViewIngest('assistant', { msg_id: 'a-live' }, liveAnswer);
  const liveBlock = e.block();
  eq(liveBlock.querySelector('.simple-turn-status').textContent, 'Working');

  const oldUser = e.row('u-old');
  oldUser.dataset.messageRole = 'user';
  e.messages.insertBefore(oldUser, liveUser);
  e.ctx.turnViewRegisterUser({ msg_id: 'u-old', _history: true }, oldUser);
  const oldAnswer = e.row('a-old');
  oldAnswer.dataset.messageRole = 'assistant';
  oldAnswer.dataset.rawText = 'old answer';
  e.messages.insertBefore(oldAnswer, liveUser);
  e.ctx.turnViewIngest('assistant',
    { msg_id: 'a-old', _history: true }, oldAnswer);

  e.ctx.turnViewReconcile();
  eq(liveBlock.querySelector('.simple-turn-status').textContent, 'Working',
    'history replay did not stop the live block');
  const late = e.row('c-live');
  e.ctx.turnViewIngest('tool_call', { msg_id: 'c-live' }, late);
  assert(late.parentNode !== e.messages, 'new live activity still enters that block');
});

// A delegate box is activity. In simplified mode it used not to be drawn at
// all -- delegate grouping was filed with the classic-only view options and
// forced off, so a sub-agent ran, returned, and left nothing on screen but its
// result message.
test('a delegate box is filed in the block, not left beside it', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const box = e.row('d1');
  box.dataset.messageRole = 'sub_agent_trace';
  // This is the role used by _renderHistoryRow. The live SSE group enters as a
  // tool_call, so using that here would miss the reload regression.
  eq(e.ctx.turnViewIngest('sub_agent_trace', { _history: true }, box), true);
  assert(box.parentNode !== e.messages, 'the box went into the block');
  assert(e.block(), 'the turn has a block');
  assert(String(box.parentNode.className).includes('simple-turn-panel-scroll'),
    'and the box sits in one of its panels');
  assert(String(box.parentNode.parentNode.id).includes('-tools'),
    'the historical delegate is in Tool calls, not Messages');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const f of failures) console.error('  - ' + f);
  process.exit(1);
}
console.log(passed + ' passing');
