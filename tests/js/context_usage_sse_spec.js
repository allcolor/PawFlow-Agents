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
eventSource.emit('message_meta', {
  conversation_id: 'conv', agent_name: 'assistant',
  context_used: 0, context_max: 1000, context_pct: 0,
  cli_context_state: 'cold', updated_at: 2,
});
assert.strictEqual(window._contextUsage.assistant.used, 0,
  'message_meta must carry cold CLI authority into setContextUsage');

seedWarmGauge(3);
eventSource.emit('done', {
  conversation_id: 'conv', agent_name: 'assistant', continuing: true,
  context_used: 0, context_max: 1000, context_pct: 0,
  cli_context_state: 'cold', updated_at: 4,
});
assert.strictEqual(window._contextUsage.assistant.used, 0,
  'done must carry cold CLI authority into setContextUsage');

console.log('context usage SSE lifecycle: ok');
