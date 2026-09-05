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
// Conversation-session scoping is outside this lifecycle test; run callbacks
// directly instead of binding them to a ConversationSession.
global.captureConversationSessionCallback = callback => callback;
global.updateActivePanel = () => {};
global.finalizeThinking = () => {};
global.trackAgentDone = () => {};
global.pawflowDebugLog = () => {};
global.streams = {};
// Stream rendering is exercised by stream_render_spec.js; this fixture drives lifecycle events.
global._flushStreamRender = () => {};
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

// A terminal event from the consumer replaced during compact belongs to its
// old turn. It must neither show an error nor remove the successor's activity.
let staleErrorsShown = 0;
global.addMsg = role => { if (role === 'error') staleErrorsShown += 1; return {}; };
trackAgentStart('assistant', '', '', 'turn-new');
eventSource.emit('error_event', {
  conversation_id: 'conv', agent_name: 'assistant', turn_id: 'turn-old',
  message: 'CCIConsumerEvicted: newer consumer owns the session',
});
assert.strictEqual(staleErrorsShown, 0, 'stale supersession error must stay hidden');
assert.strictEqual(activeInteractions.assistant.turnId, 'turn-new',
  'stale terminal must not remove the successor active turn');

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
  isConnected: true,
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

// Actual provider ordering can finalize the token preview first and publish the
// durable row afterwards. The retired lastEl must be claimed when (and only
// when) its saved text proves that both events describe the same message.
addedMessages = 0;
preview.dataset.msgid = 'late-preview-id';
preview.dataset.rawText = '';
previewClasses.delete('streaming');
previewClasses.add('finalized');
streams.assistant = {
  el: null, lastEl: preview, lastText: 'late durable answer', text: '',
  chunks: [preview], msg_id: 'late-preview-id',
};
global._seenMsgIds = new Set(['late-preview-id']);
eventSource.emit('new_message', {
  role: 'assistant', content: 'late durable answer', agent_name: 'assistant',
  msg_id: 'late-durable-id', message_count: 5,
});
assert.strictEqual(addedMessages, 0, 'late durable message must reuse the retired preview');
assert.strictEqual(preview.dataset.msgid, 'late-durable-id', 'retired preview must receive durable id');
assert.strictEqual(streams.assistant.lastText, '', 'claimed preview text must be consumed');

addedMessages = 0;
streams.assistant = {
  el: null, lastEl: preview, lastText: 'a genuinely different message',
  text: '', chunks: [], msg_id: 'late-durable-id',
};
eventSource.emit('new_message', {
  role: 'assistant', content: 'next durable answer', agent_name: 'assistant',
  msg_id: 'next-durable-id', message_count: 6,
});
assert.strictEqual(addedMessages, 1, 'different text must create a distinct message row');

// A tool boundary may rotate the in-memory stream record before the writer
// publishes the durable message. The token row is still the same logical
// message and remains explicitly tagged as a preview, so reconciliation must
// not depend on streams[agent] retaining lastEl.
addedMessages = 0;
preview.dataset.msgid = 'orphan-preview-id';
preview.dataset.streamPreviewAgent = 'assistant';
preview._streamPreviewText = 'orphan durable answer';
streams.assistant = {
  el: null, lastEl: null, lastText: '', text: '', chunks: [],
  msg_id: 'orphan-preview-id',
};
global._seenMsgIds = new Set(['orphan-preview-id']);
document.querySelectorAll = selector => selector === '[data-stream-preview-agent]'
  ? [preview] : [];
eventSource.emit('new_message', {
  role: 'assistant', content: 'orphan durable answer', agent_name: 'assistant',
  msg_id: 'orphan-durable-id', message_count: 7,
});
assert.strictEqual(addedMessages, 0,
  'durable message must reclaim its tagged preview after stream-state rotation');
assert.strictEqual(preview.dataset.msgid, 'orphan-durable-id',
  'reclaimed preview must receive the durable id');
assert.strictEqual(preview.dataset.streamPreviewAgent, undefined,
  'claiming the preview must consume its provenance marker');
assert.strictEqual(preview._streamPreviewText, undefined,
  'claiming the preview must consume its private full text');

// Content equality alone is never a dedup key. Once the preview marker has
// been consumed, a distinct durable message with the same text must render.
addedMessages = 0;
streams.assistant = {
  el: null, lastEl: null, lastText: '', text: '', chunks: [],
  msg_id: 'orphan-durable-id',
};
eventSource.emit('new_message', {
  role: 'assistant', content: 'orphan durable answer', agent_name: 'assistant',
  msg_id: 'genuine-repeat-id', message_count: 8,
});
assert.strictEqual(addedMessages, 1,
  'an untagged durable message must not be content-deduplicated');

console.log('context usage SSE lifecycle: ok');
