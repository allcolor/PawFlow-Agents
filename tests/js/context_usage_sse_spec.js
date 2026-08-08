'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.CSS = {escape: value => String(value)};
global.conversationId = 'conv';
global._sseCid = 'conv';
global.document = {
  addEventListener: () => {},
  querySelector: () => null,
  querySelectorAll: () => [],
  getElementById: () => ({disabled: false, style: {}, textContent: ''}),
};
global.t = key => key;
global.updateActivePanel = () => {};
global.finalizeThinking = () => {};
global.trackAgentDone = () => {};
global.pawflowDebugLog = () => {};
global.streams = {};
global._CONTEXT_ACKS = new Set();
global.clearStream = () => {};
global.scrollBottom = () => {};
global.loadConversations = () => {};
global.sending = false;
global.lastSSEActivity = 0;

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

global.eventSource = new FakeEventSource();
for (const file of [
  'tasks/io/chat_ui/active_agents.js',
  'tasks/io/chat_ui/sse_handlers_a.js',
  'tasks/io/chat_ui/sse_handlers_b.js',
]) {
  vm.runInThisContext(fs.readFileSync(file, 'utf8'), {filename: file});
}

// Rendering the active-agent panel is outside this lifecycle test; replace the
// real DOM-heavy function after loading the production source.
updateActivePanel = () => {};
_sseWireA();
_sseWireB();

function seedWarmGauge(updatedAt) {
  setContextUsage('assistant', {
    conversation_id: 'conv', used: 600, max: 1000, updated_at: updatedAt,
  });
}

seedWarmGauge(1);
trackAgentStart('assistant');
eventSource.emit('message_meta', {
  conversation_id: 'conv', agent_name: 'assistant',
  context_used: 0, context_max: 1000, context_pct: 0,
  cli_context_state: 'cold', updated_at: 2,
  source: {provider: 'codex-interactive'},
});
assert.strictEqual(window._contextUsage.assistant.used, 0,
  'message_meta must carry cold CLI authority into setContextUsage');
assert.strictEqual(activeInteractions.assistant.codexInteractiveLive, false,
  'message_meta must not invent LIVE before reuse_count arrives in the poll');

seedWarmGauge(3);
eventSource.emit('done', {
  conversation_id: 'conv', agent_name: 'assistant', continuing: true,
  context_used: 0, context_max: 1000, context_pct: 0,
  cli_context_state: 'cold', updated_at: 4,
});
assert.strictEqual(window._contextUsage.assistant.used, 0,
  'done must carry cold CLI authority into setContextUsage');

// A provider may give the streaming preview a different id from the durable
// new_message. The durable event must claim and re-key the preview, not create a
// second row that remains duplicated in the simplified detail block.
let addedMessages = 0;
global.addMsg = () => { addedMessages += 1; return {}; };
global.finalizeThinkingFromEvent = () => {};
global.sourceBadge = () => '';
global.renderMarkdown = value => String(value);
global.turnViewIngest = (_role, _data, element) => element === preview;
global._noteLiveHistoryAppend = () => {};
global._seenMsgIds = new Set(['preview-id']);
const previewClasses = new Set(['streaming']);
const contentEl = {innerHTML: ''};
const preview = {
  dataset: {msgid: 'preview-id', transientUi: '1'},
  classList: {
    contains: name => previewClasses.has(name),
    add: name => previewClasses.add(name),
    remove: name => previewClasses.delete(name),
  },
  querySelector: selector => selector === '.msg-content' ? contentEl : null,
};
streams.assistant = {
  el: preview, lastEl: null, text: 'preview', chunks: ['preview'],
  msg_id: 'preview-id',
};
eventSource.emit('new_message', {
  role: 'assistant', content: 'durable answer', agent_name: 'assistant',
  msg_id: 'durable-id', message_count: 4,
});
assert.strictEqual(addedMessages, 0, 'durable message must reuse the preview row');
assert.strictEqual(preview.dataset.msgid, 'durable-id', 'DOM row must carry durable id');
assert.strictEqual(_seenMsgIds.has('preview-id'), false, 'transient id must leave dedup set');
assert.strictEqual(_seenMsgIds.has('durable-id'), true, 'durable id must enter dedup set');
assert.strictEqual(streams.assistant.msg_id, 'durable-id', 'stream state must carry durable id');
assert.strictEqual(streams.assistant.el, null, 'claimed stream must be retired');
assert.strictEqual(contentEl.innerHTML, 'durable answer', 'preview text must become durable text');

console.log('context usage SSE lifecycle: ok');
