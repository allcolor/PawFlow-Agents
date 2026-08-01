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

test('the detail block always contains every message including its live last', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const first = e.row('a1');
  first.dataset.messageRole = 'assistant'; first.dataset.rawText = 'first';
  first.dataset.live = '1';
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1', content: 'first' }, first);

  const block = e.block();
  const body = block.querySelector('#turn-panel-u1-messages .simple-turn-panel-scroll');
  eq(body.children.length, 1, 'one message means one detail row');
  assert(body.children[0].classList.contains('simple-turn-last-detail'),
         'the current last is mirrored inside the detail block');
  assert(body.children[0].dataset.live === undefined,
         'the visual copy never keeps the live eviction marker');
  assert(block.nextSibling === first, 'the interactive original stays readable outside');

  const second = e.row('a2');
  second.dataset.messageRole = 'assistant'; second.dataset.rawText = 'second';
  e.ctx.turnViewIngest('assistant', { msg_id: 'a2', content: 'second' }, second);

  eq(body.children.length, 2, 'two messages mean two detail rows');
  assert(body.children[0] === first, 'the former last becomes its canonical detail row');
  assert(body.children[1].classList.contains('simple-turn-last-detail'),
         'the new last is mirrored inside the detail block');
  assert(block.nextSibling === second, 'the live outside last advances immediately');
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

// The other half of the same rule. /compact answers with a notice and nothing
// else: barred from the outside spot on principle, it was filed inside the
// block, and a collapsed turn reported its own result nowhere the reader could
// see it -- the detail block had swallowed it.
test('a system notice takes the outside spot while nothing else holds it', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const notice = e.row('s1');
  eq(e.ctx.turnViewIngest('system', {}, notice), true, 'the notice is ingested');
  assert(e.block().nextSibling === notice, 'the notice holds the outside spot');
});

test('an answer takes the outside spot back from a system notice', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const notice = e.row('s1');
  e.ctx.turnViewIngest('system', {}, notice);
  const answer = e.row('a1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, answer);
  assert(e.block().nextSibling === answer, 'the answer displaces the notice');
  assert(notice.parentNode !== e.messages, 'and the notice is filed in the block');
});

test('a newer system notice replaces the one holding the spot', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  e.ctx.turnViewIngest('system', {}, e.row('s1'));
  const second = e.row('s2');
  e.ctx.turnViewIngest('system', {}, second);
  assert(e.block().nextSibling === second, 'the newest notice holds the spot');
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
  // Correlating here would insert the first turn's answer ABOVE the message
  // the reader has just sent. This expectation IS the product rule; if it
  // fails, the router was reintroduced, not the test that went stale.
  const answer = e.row('a3');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a3' }, answer);
  e.ctx.turnViewFinalize({ turn_id: 'u1', final_msg_id: 'a3' });

  // Each turn keeps its own last message where the reader can read it: a1 for
  // the interrupted first turn, a3 for the second. a2 went back into the second
  // block when a3 replaced it.
  eq(topLevelIds(e).join(','), 'u1,BLOCK,a1,u2,BLOCK,a3');
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

// Reported from a real session: after a server restart the reader sent a
// message, the turn died on an auth error, and the answer of a turn from half
// an hour earlier reappeared at the bottom of the page under a fresh, empty,
// "Completed in 0s" block. Nothing re-rendered it -- the done named it, the
// name was resolved with a document-wide lookup, and the row was MOVED.
//
// An id names a row, it never selects one. A named final that sits above this
// turn is not this turn's answer, and the page must not move it.
test('a final naming a row from an earlier turn moves nothing', () => {
  const e = env('simplified');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, e.row('u1'));
  const oldAnswer = e.row('a1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, oldAnswer);
  e.ctx.turnViewFinalize({ final_msg_id: 'a1' });
  const firstBlock = e.block();
  eq(topLevelIds(e).join(','), 'u1,BLOCK,a1');

  // The reader sends again; this turn produces nothing of its own.
  e.ctx.turnViewRegisterUser({ msg_id: 'u2' }, e.row('u2'));
  // The done carries the only assistant id the server still had: a1.
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1', turn_final: true }, oldAnswer);
  e.ctx.turnViewFinalize({ final_msg_id: 'a1' });

  eq(topLevelIds(e).join(','), 'u1,BLOCK,a1,u2,BLOCK',
     'the earlier answer stays where it was read');
  eq(firstBlock.nextSibling, oldAnswer, 'and it still belongs to its own block');
});

// Same rule, the other mover: reconcile hands a stray row to the open turn by
// position, so a row that is positionally before the block can never be it.
test('the outside spot only ever goes to a row of this turn', () => {
  const e = env('simplified');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1' }, e.row('u1'));
  const answer = e.row('a1');
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1' }, answer);
  const block = e.block();
  // A row inserted ABOVE the block -- an older page arriving late.
  const older = e.dom.document.createElement('div');
  older.className = 'msg'; older.dataset.msgid = 'a0';
  older.dataset.messageRole = 'assistant'; older.dataset.rawText = 'older';
  e.messages.insertBefore(older, e.messages.children[0]);

  e.ctx.turnViewFinalize({ final_msg_id: 'a0' });
  assert(e.messages.children[0] === older, 'the older row keeps its place');
  assert(block.nextSibling === answer, 'and the turn keeps its own answer');
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

// The runtime snapshot says what was running when the page was built. It keeps
// the live turn open -- no promotion, no closing -- and stops being true the
// moment that turn ends. Left behind, it would hold a finished turn open and
// keep its answer buried at the next reconciliation (a load-more, a recovery).
test('a runtime turn stops protecting itself once it has ended', () => {
  const e = env('simplified');
  e.ctx.turnViewSetRuntimeTurns([
    { turn_id: 'u1', started_at: 1000, duration: 3, status: 'running' },
  ]);
  // An active turn carries no turn_final: the server refuses to derive one.
  const user = e.row('u1');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1', turn_id: 'u1', _history: true }, user);
  const answer = e.row('a1');
  answer.dataset.messageRole = 'assistant';
  answer.dataset.rawText = 'voila';
  e.ctx.turnViewIngest('assistant',
    { msg_id: 'a1', turn_id: 'u1', content: 'voila', _history: true }, answer);
  e.ctx.turnViewReconcile();
  eq(topLevelIds(e).join(','), 'u1,BLOCK,a1',
     'a live turn keeps its last readable while staying open');

  e.ctx.turnViewFinalize({ turn_id: 'u1' });   // done, naming nothing
  e.ctx.turnViewReconcile();                    // load more, recovery, anything
  eq(topLevelIds(e).join(','), 'u1,BLOCK,a1',
     'ending the turn keeps the same last message in place');
});

// ── Load more, while a turn is live ───────────────────────────────────────
//
// loadMoreMessages renders its page with deferTurnView:true -- deliberately no
// ingestion, because replaying it through the live path would let an old USER
// close the live turn -- prepends the whole page above everything, and leans on
// turnViewReconcile to group it. That is exactly the case the display rule
// promises to cover: a page of older history.

// What _renderHistoryRow(m, {deferTurnView:true}) leaves behind: a row in the
// DOM carrying its role, and nothing at all told to the turn view.
function prependDeferredRows(e, rows) {
  const anchor = e.messages.firstChild;
  for (const m of rows) {
    const el = e.dom.document.createElement('div');
    el.className = 'msg';
    el.dataset.msgid = m.msg_id;
    el.dataset.messageRole = m.type || m.role;
    if (m.content) el.dataset.rawText = m.content;
    e.messages.insertBefore(el, anchor);
  }
}

test('a load-more page groups into USER > BLOCK > answer, not one block per row', () => {
  const e = env('simplified');
  // A live turn is on screen and still working -- loading more while it spins.
  startTurn(e, 'u1');
  const partial = e.row('a1');
  partial.dataset.messageRole = 'assistant';
  partial.dataset.rawText = 'je regarde';
  e.ctx.turnViewIngest('assistant', { msg_id: 'a1', turn_id: 'u1' }, partial);
  e.ctx.turnViewReconcile();
  eq(topLevelIds(e).join(','), 'u1,BLOCK,a1', 'the live turn starts well formed');

  // Load more prepends one older, complete turn above it.
  prependDeferredRows(e, [
    { type: 'user', msg_id: 'u0' },
    { type: 'tool_call', msg_id: 'c0' },
    { type: 'tool_result', msg_id: 'r0' },
    { type: 'assistant', msg_id: 'a0', content: 'fini' },
  ]);
  e.ctx.turnViewReconcile();

  eq(topLevelIds(e).join(','), 'u0,BLOCK,a0,u1,BLOCK,a1',
     'the older turn is one block, and the live turn is left intact');
});

test('a load-more page starting mid-turn does not feed the live turn', () => {
  // The page begins at an arbitrary offset, so most of the time its first rows
  // are the tail of a turn whose user message was not loaded. This pass walks
  // the DOM from the top while `_turnOpen` still points at the live turn at
  // the BOTTOM, so those older rows were filed into the live turn's block --
  // far below where they sit -- and the fragment's own answer was left at top
  // level with no block above it. Observed only while a turn was running,
  // because `_turnOpen` is what a running turn leaves behind.
  const e = env('simplified');
  startTurn(e, 'u9');
  const live = e.row('a9');
  live.dataset.messageRole = 'assistant';
  live.dataset.rawText = 'je regarde';
  e.ctx.turnViewIngest('assistant', { msg_id: 'a9', turn_id: 'u9' }, live);
  e.ctx.turnViewReconcile();

  prependDeferredRows(e, [
    // tail of an older turn: no user row above it
    { type: 'tool_call', msg_id: 'c0' },
    { type: 'tool_result', msg_id: 'r0' },
    { type: 'assistant', msg_id: 'a0', content: 'fin du tour precedent' },
    // then one complete older turn
    { type: 'user', msg_id: 'u0' },
    { type: 'tool_call', msg_id: 'c1' },
    { type: 'tool_result', msg_id: 'r1' },
    { type: 'assistant', msg_id: 'a1', content: 'reponse' },
  ]);
  e.ctx.turnViewReconcile();

  eq(topLevelIds(e).join(','), 'BLOCK,a0,u0,BLOCK,a1,u9,BLOCK,a9',
     'the head fragment gets its own block instead of joining the live turn');
  eq(e.messages.querySelectorAll('.simple-turn-block').length, 3,
     'one block per turn on screen');
  const liveBlock = e.messages.querySelectorAll('.simple-turn-block')[2];
  assert(!liveBlock.querySelector('[data-msgid="c0"]'),
         'an older tool row must never land in the live turn');
});

// The browser-level copy of this lives in
// tests/test_webchat_durable_state_behavior.py, which skips wherever headless
// Chromium cannot render. This is the copy that always runs.
test('the runtime snapshot rehydrates a live turn, and a done still closes the open one', () => {
  const e = env('simplified');
  const now = e.ctx.Date.now();
  e.ctx.turnViewSetRuntimeTurns([
    { turn_id: 'turn-A', started_at: (now / 1000) - 12, duration: 12,
      status: 'thinking', agent_name: 'alpha', message_preview: 'still working' },
    { turn_id: 'turn-B', started_at: (now / 1000) - 4, duration: 4,
      status: 'running', agent_name: 'beta' },
  ]);

  const rowFor = (turnId, msgId, text) => {
    const user = e.row(turnId);
    e.ctx.turnViewRegisterUser(
      { msg_id: turnId, turn_id: turnId, _history: true }, user);
    const row = e.row(msgId);
    row.dataset.messageRole = 'assistant';
    row.dataset.rawText = text;
    e.ctx.turnViewIngest('assistant',
      { msg_id: msgId, turn_id: turnId, content: text, _history: true }, row);
    return row;
  };
  const partialA = rowFor('turn-A', 'partial-A', 'partial A');
  const partialB = rowFor('turn-B', 'partial-B', 'partial B');
  e.ctx.turnViewReconcile();
  e.ctx.turnViewHydrateRuntimeTurns();

  // Asserted on the DOM rather than on module state: the reader's view is the
  // contract, and a vm context cannot see a top-level `const` anyway.
  const blocks = e.messages.querySelectorAll('.simple-turn-block');
  eq(blocks.length, 2, 'one block per turn');
  const [blockA, blockB] = blocks;
  eq(topLevelIds(e).join(','),
     'turn-A,BLOCK,partial-A,turn-B,BLOCK,partial-B',
     'each live turn keeps its current last message readable');
  assert(blockA.classList.contains('turn-working'),
         'a turn the server still runs stays open');
  eq(blockA.querySelector('.simple-turn-status').className,
     'simple-turn-status working');
  // 12s of runtime, not 0s from the moment the page was built.
  const elapsed = blockA.querySelector('.simple-turn-elapsed').textContent;
  assert(/1[1-9]s|[2-9]\ds/.test(elapsed), 'elapsed shows runtime age: ' + elapsed);
  assert(blockA.querySelectorAll('.simple-turn-cue').length > 0,
         'the runtime preview is offered as a cue');
  assert(!!blockB.querySelector('.simple-turn-idle'),
         'a live turn with nothing to say shows its idle pulse');
  const inside = (el, block) => {
    for (let p = el.parentNode; p; p = p.parentNode) if (p === block) return true;
    return false;
  };
  assert(partialA.parentNode === e.messages, "partial A is A's outside last");
  assert(partialB.parentNode === e.messages, "partial B is B's outside last");
  assert(inside(blockA.querySelector('.simple-turn-last-detail'), blockA),
         'partial A is also represented in A details');
  assert(inside(blockB.querySelector('.simple-turn-last-detail'), blockB),
         'partial B is also represented in B details');

  // The done carries turn-A's id while turn-B is the turn on screen. An id
  // names a turn, it never selects one: the open block closes.
  const finalB = e.row('final-B');
  finalB.dataset.messageRole = 'assistant';
  finalB.dataset.rawText = 'final B';
  e.ctx.turnViewIngest('assistant',
    { msg_id: 'final-B', turn_id: 'turn-A', content: 'final B' }, finalB);
  e.ctx.turnViewFinalize({ turn_id: 'turn-A', final_msg_id: 'final-B' });

  eq(blockB.querySelector('.simple-turn-status').className,
     'simple-turn-status completed', 'the open turn is the one that closed');
  eq(topLevelIds(e).join(','),
     'turn-A,BLOCK,partial-A,turn-B,BLOCK,final-B',
     'the answer sits under the block that closed');
  assert(blockA.classList.contains('turn-working'),
         'the turn the done NAMED was not touched');
});

test('a reloaded turn files every intermediate row into its tab', () => {
  const e = env('simplified');
  replayHistory(e, HISTORY_ROWS);
  const rows = key => {
    const panel = e.block().querySelector('#turn-panel-u1-' + key + ' .simple-turn-panel-scroll');
    return panel ? panel.children.length : -1;
  };
  eq(rows('messages'), 2, 'narration plus the last-message detail mirror');
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

// ── A reconstruction may not end a turn that is still running ───────────
//
// The page classifier names the last assistant row of every turn it believes
// is over, and only the server's active-turn set keeps it away from a live
// one. When that set is momentarily empty -- a capture owning the marker, a
// tail re-read through gap recovery -- the guess reaches a turn still at work
// and the block said "completed", clock frozen, cues gone, over an agent
// visibly still working.

const statusOf = block =>
  block.querySelector('.simple-turn-status').className.replace('simple-turn-status ', '');

test('a replayed guess cannot close a turn the live channel is feeding', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const narration = e.row('a1');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a1' }, narration);
  const call = e.row('c1');
  e.ctx.turnViewIngest('tool_call', { turn_id: 'u1', msg_id: 'c1', tc_id: 'tc-1' }, call);
  const block = e.block();
  eq(statusOf(block), 'working', 'the turn is running');

  // Gap recovery re-reads the tail mid-turn and replays it.
  eq(e.ctx.turnViewIngest('assistant',
    { turn_id: 'u1', msg_id: 'a1', turn_final: true, turn_final_derived: true,
      _history: true }, narration), true);

  eq(statusOf(block), 'working', 'a guess does not end a live turn');
  assert(block.classList.contains('turn-working'), 'the live surface stays up');
});

test('the runtime snapshot alone also refuses the guess', () => {
  const e = env('simplified');
  e.ctx.turnViewSetRuntimeTurns([
    { turn_id: 'u1', started_at: 1000, duration: 3, status: 'running' },
  ]);
  const user = e.row('u1');
  e.ctx.turnViewRegisterUser({ msg_id: 'u1', turn_id: 'u1', _history: true }, user);
  const answer = e.row('a1');
  e.ctx.turnViewIngest('assistant',
    { msg_id: 'a1', turn_id: 'u1', turn_final: true, turn_final_derived: true,
      _history: true }, answer);
  eq(statusOf(e.block()), 'working',
     'a turn the server says is running is not closed by a reconstruction');
});

test('a guessed ending is undone by the turn itself, a real one never is', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  // A page rendered before anything live: nothing yet contradicts the guess,
  // so it stands and the answer is placed.
  const guess = e.row('a1');
  e.ctx.turnViewIngest('assistant',
    { turn_id: 'u1', msg_id: 'a1', turn_final: true, turn_final_derived: true,
      _history: true }, guess);
  const block = e.block();
  eq(statusOf(block), 'completed', 'the guess stands while nothing refutes it');

  // Then the turn keeps talking: it was never over.
  const live = e.row('a2');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a2' }, live);
  eq(statusOf(block), 'working', 'the turn refutes the guess by talking');
  assert(block.classList.contains('turn-working'), 'its surface comes back');

  // A done is the server speaking, and nothing after it reopens the turn.
  e.ctx.turnViewFinalize({ turn_id: 'u1', final_msg_id: 'a2' });
  eq(statusOf(block), 'completed');
  const after = e.row('a3');
  e.ctx.turnViewIngest('assistant', { turn_id: 'u1', msg_id: 'a3' }, after);
  eq(statusOf(block), 'completed', 'a closed turn stays closed');
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
  const stream = e.row('a1');
  for (let i = 0; i < 3; i++) {
    e.ctx.turnViewIngest('token', { msg_id: 'a1', content: 'step ' + i }, stream);
    e.clock.tick(300);
  }
  eq(e.cues(), 3, 'three cues are on screen at once');
  for (let i = 3; i < 9; i++) {
    e.ctx.turnViewIngest('token', { msg_id: 'a1', content: 'step ' + i }, stream);
    e.clock.tick(300);
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

// The copy sits above the tabs, so a lookup by tc_id reaches it first. Keeping
// the id on it gave the tool_result to a node about to fade: the canonical row
// stayed pending and the end of the turn stamped it "[Stopped]", next to a cue
// showing the very output it never got.
test('a cue copy answers to no tc_id, so the result reaches the real row', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const call = e.row('c1');
  call.dataset.tcId = 'tc-1';
  call.dataset.messageRole = 'tool_call';
  const nested = call.appendChild(e.dom.document.createElement('div'));
  nested.dataset.tcId = 'tc-1';
  nested.dataset.msgid = 'c1';
  e.ctx.turnViewIngest('tool_call', { turn_id: 'u1', msg_id: 'c1', tc_id: 'tc-1' }, call);

  const found = e.messages.querySelectorAll('[data-tc-id="tc-1"]');
  for (const el of found) {
    assert(!el.classList.contains('simple-turn-cue-copy'), 'no copy answers to the id');
    assert(el === call || el === nested, 'only the canonical row and its own children do');
  }
  const copy = e.messages.querySelectorAll('.simple-turn-cue-copy')[0];
  eq(copy.getAttribute('data-tc-id'), null, 'the copy is not addressable by call id');
  eq(copy.querySelectorAll('[data-tc-id], [data-msgid]').length, 0,
     'nor is anything nested inside it');
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

// ── The cue surface names the work, not the wrapper ─────────────────
//
// A code-mode turn is ONE native call -- exec(<code-mode script, N chars>) --
// and everything it does is the MCP calls the relay reports underneath it.
// Cueing every tool row identically put the wrapper in front and the work
// behind: the animation read exec(...), exec(...), exec(...) while the names
// worth reading went past unseen.

function toolRow(e, msgId, tcId, origin, text) {
  const el = e.row(msgId);
  el.textContent = text || '';
  if (origin) {
    const badge = e.dom.document.createElement('span');
    badge.className = 'tc-origin tc-origin-' + origin;
    el.appendChild(badge);
  }
  e.ctx.turnViewIngest('tool_call', { msg_id: msgId, tc_id: tcId }, el);
  return el;
}

test('an mcp call takes the cue away from the native wrapper', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c1', 'tc-1', 'native', 'exec(<code-mode script, 2411 chars>)');
  eq(e.cues(), 0, 'the wrapper is held back, not shown at once');
  toolRow(e, 'c2', 'tc-2', 'mcp', 'read(path=/workspace/core/llm_client.py)');
  eq(e.cues(), 1, 'the mcp call is cued immediately');
  e.clock.tick(2000);
  eq(e.cues(), 1, 'and the wrapper never arrives behind it');
});

test('a native call on its own is still shown', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c1', 'tc-1', 'native', 'exec(...)');
  eq(e.cues(), 0);
  e.clock.tick(2000);
  eq(e.cues(), 1, 'suppressing it outright would leave the surface blank');
});

test('two native calls both reach the surface', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c1', 'tc-1', 'native', 'exec(a)');
  toolRow(e, 'c2', 'tc-2', 'native', 'exec(b)');
  e.clock.tick(2000);
  eq(e.cues(), 2, 'a second native row means the first was no wrapper');
});

test('a row with no origin badge is cued as before', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c1', 'tc-1', '', 'something(...)');
  eq(e.cues(), 1, 'unclassified rows must not be delayed or dropped');
});

test('every mcp call of a group is cued', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c0', 'tc-0', 'native', 'exec(<code-mode script, 900 chars>)');
  toolRow(e, 'c1', 'tc-1', 'mcp', 'read(a)');
  toolRow(e, 'c2', 'tc-2', 'mcp', 'grep(b)');
  toolRow(e, 'c3', 'tc-3', 'mcp', 'edit(c)');
  e.clock.tick(2000);
  eq(e.cues(), 3, 'the three calls, and not the wrapper');
});

// A turn is a sequence of groups, and deferring each wrapper only against the
// calls that follow it let every later script win its own race: the surface
// read exec(...) most of the time even though four calls in five were MCP.
test('a turn that has reached mcp never shows a wrapper again', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c0', 'tc-0', 'native', 'exec(<code-mode script, 900 chars>)');
  toolRow(e, 'c1', 'tc-1', 'mcp', 'read(a)');
  e.clock.tick(2000);
  eq(e.cues(), 1);
  toolRow(e, 'c2', 'tc-2', 'native', 'exec(<code-mode script, 1200 chars>)');
  e.clock.tick(2000);
  eq(e.cues(), 1, 'the second script is transport too, not work');
  toolRow(e, 'c3', 'tc-3', 'mcp', 'grep(b)');
  eq(e.cues(), 2, 'and the calls it makes still reach the surface');
});

// The first MCP row does not arrive within the wrapper's window by grace of the
// UI: it waits on the TUI, the relay round trip and the script's own preamble.
test('the wrapper waits long enough for the relay to answer', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c0', 'tc-0', 'native', 'exec(<code-mode script, 900 chars>)');
  e.clock.tick(900);
  eq(e.cues(), 0, 'half a second lost that race on nearly every group');
  toolRow(e, 'c1', 'tc-1', 'mcp', 'read(a)');
  e.clock.tick(2000);
  eq(e.cues(), 1, 'the call, and not the script that ran it');
});

// A tool row is offered twice -- once as a call, once when its result lands on
// it. For a code body the second offer carries the whole output block.
test('a row is cued once, not again when its result lands', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const row = toolRow(e, 'c1', 'tc-1', 'mcp', 'read(a)');
  eq(e.cues(), 1);
  row.textContent = 'read(a)\nScript completed\nWall time 0.9 seconds';
  e.ctx.turnViewIngest('tool_result', { msg_id: 'c1', tc_id: 'tc-1' }, row);
  e.clock.tick(2000);
  eq(e.cues(), 1, 'the result is not a second call');
});

// A native row is not automatically the transport around MCP calls. The code
// body announces itself -- its arguments are elided to `<code-mode script, N
// chars>` before the row is drawn -- and suppressing every native row once the
// turn had reached MCP hid a real local_shell_call in a mixed turn: the one
// thing the surface exists to name went past unseen while the turn ran it.
test('a genuine native call is still cued in a mixed turn', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c0', 'tc-0', 'native', 'exec(<code-mode script, 900 chars>)');
  toolRow(e, 'c1', 'tc-1', 'mcp', 'read(a)');
  e.clock.tick(2000);
  eq(e.cues(), 1, 'the wrapper still yields to the calls it drove');
  toolRow(e, 'c2', 'tc-2', 'native', 'local_shell(ls -la)');
  e.clock.tick(2000);
  eq(e.cues(), 2, 'a tool the agent ran itself is work, not transport');
});

// The same mixed turn, in the order that actually happens when the agent runs
// a shell command and then reaches for a file: the native row is still inside
// its deferral window when the first MCP row arrives. That window asks whether
// the row was a wrapper -- it is not a verdict on whatever is waiting in it.
test('a genuine native call just before the first mcp call is still cued', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c1', 'tc-1', 'native', 'local_shell(ls -la)');
  toolRow(e, 'c2', 'tc-2', 'mcp', 'read(a)');
  e.clock.tick(2000);
  eq(e.cues(), 2, 'the shell command the agent ran is not transport for the read');
});

test('a wrapper just before the first mcp call still yields', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c1', 'tc-1', 'native', 'exec(<code-mode script, 2411 chars>)');
  toolRow(e, 'c2', 'tc-2', 'mcp', 'read(a)');
  e.clock.tick(2000);
  eq(e.cues(), 1, 'the row that only carried the call must not be cued beside it');
});

// ── A tool that is still running holds its place ────────────────────────
//
// The surface says what is happening now. A call that has not answered yet is
// exactly that, and the thinking and the message that arrive while it runs used
// to push it out of sight -- so the one moment the reader wants to know what is
// running was the moment it stopped being shown.

function cueCopies(e) {
  return e.messages.querySelectorAll('.simple-turn-cue-copy').length;
}

test('a running tool stays on the surface while the turn talks over it', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  toolRow(e, 'c1', 'tc-1', 'mcp', 'grep(a very slow pattern)');
  eq(cueCopies(e), 1);
  const stream = e.row('a1');
  for (let i = 0; i < 6; i++) {
    e.ctx.turnViewIngest('token', { msg_id: 'a1', content: 'step ' + i }, stream);
    e.clock.tick(300);
  }
  eq(cueCopies(e), 1, 'six cues later, the call it is waiting on is still shown');
});

test('and it leaves as soon as its result lands', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  const row = toolRow(e, 'c1', 'tc-1', 'mcp', 'grep(a very slow pattern)');
  const stream = e.row('a1');
  for (let i = 0; i < 6; i++) {
    e.ctx.turnViewIngest('token', { msg_id: 'a1', content: 'step ' + i }, stream);
    e.clock.tick(300);
  }
  e.ctx.turnViewIngest('tool_result', { msg_id: 'c1', tc_id: 'tc-1' }, row);
  eq(cueCopies(e), 0, 'answered, it is history like anything else');
});

test('the held column is bounded too', () => {
  const e = env('simplified');
  startTurn(e, 'u1');
  for (let i = 0; i < 12; i++) toolRow(e, 'c' + i, 'tc-' + i, 'mcp', 'read(' + i + ')');
  eq(e.cues(), 8, 'a dozen calls at once must not turn the column into a wall');
});

// A show_file result is taken by the artifact path, which is the whole reason
// it never reaches the tool cue a second time: the SSE handler hands the
// result to `turnViewHandleToolResult` and stops there when it is claimed.
// The pin has to be released on that path too, or a finished show_file goes on
// being shown as the thing the agent is doing until the turn ends.
//
// `parseShowFileArtifact` lives in messages_markdown.js, which wires the page's
// scroll handlers as it loads and cannot run against the stub; the parser
// itself is exercised where it is defined.
function withArtifactParser(e) {
  e.ctx.parseShowFileArtifact = (resultText, toolName) => (
    String(toolName || '') === 'show_file'
      ? { file_id: 'f1', filename: 'chart.png', url: 'fs://filestore/f1/chart.png',
          content_type: 'image/png', size_kb: 12 }
      : null);
}

test('an artifact result releases the cue its call pinned', () => {
  const e = env('simplified');
  withArtifactParser(e);
  startTurn(e, 'u1');
  toolRow(e, 'c1', 'tc-1', 'mcp', 'show_file(chart.png)');
  const stream = e.row('a1');
  for (let i = 0; i < 6; i++) {
    e.ctx.turnViewIngest('token', { msg_id: 'a1', content: 'step ' + i }, stream);
    e.clock.tick(300);
  }
  eq(cueCopies(e), 1, 'still running, so still held');
  // Exactly what sse_handlers_a.js does: the artifact claims the result and
  // the ordinary tool_result ingest never runs.
  const owned = e.ctx.turnViewHandleToolResult(
    { msg_id: 'c1', tc_id: 'tc-1', tool: 'show_file', result: '{}' }, null);
  eq(owned, true, 'the artifact path claimed it');
  eq(cueCopies(e), 0, 'released the moment its result lands, artifact or not');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const f of failures) console.error('  - ' + f);
  process.exit(1);
}
console.log(passed + ' passing');
