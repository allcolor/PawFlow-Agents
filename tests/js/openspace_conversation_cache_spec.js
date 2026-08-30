'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class FakeEventSource {
  constructor() { this.listeners = {}; }
  addEventListener(type, callback) {
    (this.listeners[type] ||= []).push(callback);
  }
  emit(type, payload) {
    for (const callback of this.listeners[type] || []) {
      callback({data: JSON.stringify(payload)});
    }
  }
}

const context = {
  assert, console, Map, Set, Date, String, Array, Object, Number, Math, JSON,
  setTimeout: () => 0,
  clearTimeout() {},
};
vm.createContext(context);
vm.runInContext(`
  globalThis.window = globalThis;
  window._userId = 'alice';
  let selectedAgent = 'assistant';
  let _osSeedConvId = null;
  const _osSeededIds = new Set();
  const _osHistoryByConversation = new Map();
  const _osAgents = new Map();
  const _osFreeSeats = [];
  let _osSeatCount = 0, _osUserCount = 0;
  let _osScene = null, _osThree = null, _osActive = false;
  const OSV_LOG_MAX = 120;
  function _osApplyRoomStyle() {}
  function openspaceTvStop() {}
  function _osRetireAgent() {}
  function _osUpdateCamera() {}
  function t(key) { return key; }
`, context);

for (const file of [
  'tasks/io/chat_ui/openspace_agents.js',
  'tasks/io/chat_ui/openspace_runtime.js',
]) {
  vm.runInContext(fs.readFileSync(file, 'utf8'), context, {filename: file});
}

context.FakeEventSource = FakeEventSource;
vm.runInContext(`
  const applied = [];
  let rendered = 0;
  const rec = {speechAt: 0, state: 'idle'};
  openspaceResetTransient = () => { _osSeededIds.clear(); };
  _openspaceApplyHistory = rows => {
    applied.push(rows.map(row => row.msg_id));
  };
  _osEnsureAgent = () => rec;
  _osEnsureUser = () => rec;
  _osEventAgent = data => data.agent_name || (data.source || {}).name || '';
  _osLog = () => { rendered += 1; };
  _osShowBubble = () => {};
  _osFlushBubbles = () => {};

  openspaceSeedHistory([
    {role: 'assistant', content: 'A0', msg_id: 'a0', timestamp: 1,
     source: {type: 'agent', name: 'assistant'}},
  ], 'A');
  openspaceSeedHistory([
    {role: 'assistant', content: 'B0', msg_id: 'b0', timestamp: 2,
     source: {type: 'agent', name: 'assistant'}},
  ], 'B');
  openspaceSetConversationOwner('A');

  const streamA = new FakeEventSource();
  const streamB = new FakeEventSource();
  openspaceWireSSE(streamA, 'A');
  openspaceWireSSE(streamB, 'B');
  streamA.emit('new_message', {
    role: 'assistant', content: 'A1', msg_id: 'a1', ts: 3,
    source: {type: 'agent', name: 'assistant'},
  });
  assert.strictEqual(rendered, 1, 'the focused owner should render live');

  openspaceSetConversationOwner('B');
  streamA.emit('new_message', {
    role: 'assistant', content: 'A2', msg_id: 'a2', ts: 5,
    source: {type: 'agent', name: 'assistant'},
  });
  assert.strictEqual(rendered, 1, 'a background owner must not mutate the room');
  streamB.emit('new_message', {
    role: 'assistant', content: 'B1', msg_id: 'b1', ts: 4,
    source: {type: 'agent', name: 'assistant'},
  });
  openspaceUserMessage('B local', [], 'assistant', 'b2');

  openspaceSetConversationOwner('A');
  assert.strictEqual(applied.at(-1).join(','), 'a0,a1,a2',
    'A/B/A refocus must restore active and background durable messages');
  assert.strictEqual(
    _osHistoryByConversation.get('B').map(row => row.msg_id).join(','),
    'b0,b1,b2', 'the local composer echo must survive a room switch');

  openspaceSeedHistory([
    {role: 'assistant', content: 'A0', msg_id: 'a0', timestamp: 1,
     source: {type: 'agent', name: 'assistant'}},
    {role: 'assistant', content: 'A1', msg_id: 'a1', timestamp: 3,
     source: {type: 'agent', name: 'assistant'}},
  ], 'A');
  assert.strictEqual(applied.at(-1).join(','), 'a0,a1,a2',
    'a stale history response must not erase a newer SSE row');
  assert.strictEqual(new Set(
    _osHistoryByConversation.get('A').map(row => row.msg_id)).size, 3,
    'history and SSE rows must merge by message ID');
`, context);

console.log('openspace conversation cache spec: ok');
