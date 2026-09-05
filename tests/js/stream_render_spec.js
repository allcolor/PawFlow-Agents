// Deterministic token-render scheduling and durable-boundary regressions.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const ui = path.join(__dirname, '../../tasks/io/chat_ui');
const stub = path.join(__dirname, 'dom_stub.js');
let passed = 0;

function env() {
  delete require.cache[require.resolve(stub)];
  const dom = require(stub);
  const listeners = {};
  const frames = new Map(), timers = new Map();
  let sequence = 0;
  const ctx = {
    document: dom.document, console, Date, Map, Set,
    requestAnimationFrame: fn => { const id = ++sequence; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    setTimeout: fn => { const id = ++sequence; timers.set(id, fn); return id; },
    clearTimeout: id => timers.delete(id),
    eventSource: {addEventListener(name, fn) { (listeners[name] ||= []).push(fn); }},
    renderMarkdown: text => { ctx.renders.push(text); return text; },
    sourceBadge: () => '', t: k => k, escapeHtml: s => s,
    finalizeThinking() {}, finalizeThinkingFromEvent() {},
    collapseTechnicalGroups() {}, scrollBottom() {}, isNearBottom: () => false,
    _noteLiveHistoryAppend() {}, _CONTEXT_ACKS: new Set(),
    conversationTTSOnToken: data => ctx.tts.push(data.text),
    turnViewIngest: (kind, data) => { if (kind === 'token') ctx.deltas.push(data.text); },
    renders: [], tts: [], deltas: [], _seenMsgIds: new Set(),
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  const state = fs.readFileSync(path.join(ui, 'state.js'), 'utf8');
  vm.runInContext(state.slice(state.indexOf('// Per-agent streaming state'),
                             state.indexOf("let permissionMode =")), ctx);
  vm.runInContext("let sessionId = 'A';\nconst sessionStreams = {A: streams, B: {}};\nfunction enterSession(id) { sessionId = id; streams = sessionStreams[id]; }\nfunction captureConversationSessionCallback(fn) {\n const owner = sessionId;\n return () => { const prev = sessionId; enterSession(owner);\n try { return fn(); } finally { enterSession(prev); } };\n}\nfunction addMsg(role, text, data) {\n if (_seenMsgIds.has(data.msg_id)) return null;\n if (data.msg_id) _seenMsgIds.add(data.msg_id);\n const el = document.createElement('article');\n el.className = 'msg streaming'; el.dataset.msgid = data.msg_id || '';\n const span = document.createElement('span'); span.className = 'msg-content';\n el.appendChild(span); document.documentElement.appendChild(el); return el;\n}", ctx);
  const status = dom.document.createElement('div'); status.id = 'status';
  dom.documentElement.appendChild(status);
  vm.runInContext(fs.readFileSync(path.join(ui, 'sse_handlers_a.js'), 'utf8'), ctx);
  ctx._sseWireA();
  const emit = (type, data = {}) => {
    for (const fn of listeners[type] || []) fn({data: JSON.stringify(data)});
  };
  const token = (text, id = 'm1', agent = 'alpha') =>
    emit('token', {agent_name: agent, msg_id: id, text});
  return {ctx, emit, token, frames, timers,
    run: code => vm.runInContext(code, ctx),
    frame() { const batch = [...frames.values()]; frames.clear(); batch.forEach(fn => fn()); },
    timer() { const batch = [...timers.values()]; timers.clear(); batch.forEach(fn => fn()); },
    boundary(name) { listeners[name][0]({data: '{}'}); },
  };
}
function test(name, fn) {
  fn(); passed++; console.log('PASS ' + name);
}
test('1024 tokens accumulate and speak immediately, with one complete render', () => {
  const e = env(); for (let i = 0; i < 1024; i++) e.token('word ');
  assert.equal(e.run("getStream('alpha').text"), 'word '.repeat(1024));
  assert.equal(e.ctx.tts.join(''), 'word '.repeat(1024));
  assert.equal(e.ctx.renders.length, 0);
  assert.equal(e.frames.size, 1); assert.equal(e.timers.size, 1);
  e.frame();
  assert.deepEqual(e.ctx.renders, ['word '.repeat(1024)]);
  assert.equal(e.ctx.deltas.join(''), 'word '.repeat(1024));
  assert.equal(e.timers.size, 0);
});
test('timer renders partial answer if frames are paused, exactly once', () => {
  const e = env(); e.token('partial'); e.timer(); e.frame();
  assert.deepEqual(e.ctx.renders, ['partial']);
  assert.equal(e.frames.size, 0);
});
test('rotation flushes the old bubble before resetting accumulated text', () => {
  const e = env(); e.token('old', 'old'); e.token('new', 'new');
  assert.deepEqual(e.ctx.renders, ['old']); e.frame();
  assert.deepEqual(e.ctx.renders, ['old', 'new']);
});
test('durable text wins and cannot be replaced by an old pending preview', () => {
  const e = env(); e.token('draft');
  e.emit('new_message', {role: 'assistant', agent_name: 'alpha',
    msg_id: 'm1', content: 'durable corrected'});
  e.frame(); e.timer();
  const el = e.ctx.document.querySelector('[data-msgid="m1"]');
  assert.equal(el.querySelector('.msg-content').innerHTML, 'durable corrected');
  assert.equal(e.run("getStream('alpha').text"), '');
  assert.deepEqual(e.ctx.renders, ['draft', 'durable corrected']);
});
test('turn completion displays the full pending answer', () => {
  const e = env(); e.token('complete'); e.emit('turn_complete', {agent_name: 'alpha'});
  e.frame(); assert.deepEqual(e.ctx.renders, ['complete']);
  assert.equal(e.run("getStream('alpha').lastText"), 'complete');
});
test('all destructive and semantic boundaries flush first', () => {
  for (const kind of ['done', 'tool_call', 'tool_result', 'discard',
    'error', 'active_released', 'task_stopped', 'thinking']) {
    const e = env(); e.token(kind); e.boundary(kind); e.frame();
    assert.deepEqual(e.ctx.renders, [kind]);
  }
});
test('deleted preview does not render again', () => {
  const e = env(); e.token('remove'); e.run('clearAllStreams()');
  e.frame(); e.timer(); assert.equal(e.ctx.renders.length, 0);
  assert.equal(e.frames.size, 0); assert.equal(e.timers.size, 0);
});
test('stream retirement retains complete visible text', () => {
  const e = env(); e.token('retire'); e.run("clearStream('alpha')");
  e.frame(); assert.deepEqual(e.ctx.renders, ['retire']);
});
test('session capture isolates concurrent streams for the same agent', () => {
  const e = env(); e.token('A', 'a1');
  e.run("enterSession('B')"); e.token('B', 'b1'); e.frame();
  assert.deepEqual(e.ctx.renders, ['A', 'B']);
  assert.equal(e.run('sessionId'), 'B');
  assert.equal(e.run('sessionStreams.A.alpha.text'), 'A');
  assert.equal(e.run('sessionStreams.B.alpha.text'), 'B');
});
test('already durable ids do not create phantom streams', () => {
  const e = env(); e.ctx._seenMsgIds.add('m1'); e.token('duplicate'); e.frame();
  assert.equal(e.ctx.renders.length, 0);
  assert.equal(e.run("getStream('alpha').text"), '');
});
console.log(passed + ' stream-render tests passed');
