// Behavioural tests for the conversation search overlay and composer shortcuts.
// Run directly: node tests/js/search_spec.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'search.js');

let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed++; }
  catch (error) {
    failures.push(name + ': ' + (error && error.message ? error.message : error));
  }
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}
function equal(actual, expected, message) {
  if (actual !== expected) {
    throw new Error((message ? message + ': ' : '') + 'expected '
      + JSON.stringify(expected) + ' but got ' + JSON.stringify(actual));
  }
}

function env(messages) {
  const elements = {
    conversationSearchDialog: {
      style: {},
      addEventListener() {},
    },
    conversationSearchInput: {
      value: '',
      selectionStart: 0,
      selectionEnd: 0,
      focus() {},
      select() {},
    },
    conversationSearchResults: { innerHTML: '' },
    conversationSearchCount: { textContent: '' },
    input: {
      value: 'hello',
      selectionStart: 5,
      selectionEnd: 5,
      setRangeText(value, start, end) {
        this.value = this.value.slice(0, start) + value + this.value.slice(end);
        this.selectionStart = this.selectionEnd = start + value.length;
      },
      dispatchEvent() {},
      focus() {},
    },
  };
  const document = {
    addEventListener() {},
    getElementById(id) { return elements[id] || null; },
    querySelector() { return null; },
  };
  let historyPayload = null;
  const context = {
    console,
    document,
    window: {
      requestAnimationFrame(fn) { fn(); },
      setTimeout() {},
    },
    CSS: { escape: String },
    Event: function Event() {},
    conversationId: 'conv-1',
    action$(action, payload) {
      assert(action === 'load_history', action);
      historyPayload = payload;
      return {
        subscribe(observer) {
          observer.next({ messages: messages || [] });
        },
      };
    },
    addMsg() {},
    t(key, values) {
      if (key === 'matchesFound') return String(values.n) + ' matches';
      return key;
    },
    escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), context, {
    filename: 'search.js',
  });
  return { context, elements, get historyPayload() { return historyPayload; } };
}

test('search opens the shared overlay and renders matching messages safely', () => {
  const e = env([
    { role: 'user', content: 'Needle <script>', msg_id: 'm1' },
    { role: 'assistant', content: 'unrelated', msg_id: 'm2' },
  ]);
  equal(e.context.showConversationSearch('needle'), true);
  equal(e.elements.conversationSearchDialog.style.display, 'flex');
  equal(e.historyPayload.limit, 500);
  assert(e.elements.conversationSearchResults.innerHTML.includes('Needle &lt;script&gt;'));
  assert(!e.elements.conversationSearchResults.innerHTML.includes('unrelated'));
  equal(e.elements.conversationSearchCount.textContent, '1 matches');
});

test('composer token insertion preserves existing text and caret position', () => {
  const e = env([]);
  e.context.insertComposerToken('@');
  equal(e.elements.input.value, 'hello@');
  equal(e.elements.input.selectionStart, 6);
});

test('non-string message content remains searchable', () => {
  const e = env([{ type: 'tool_result', content: { status: 'ready' } }]);
  e.context.showConversationSearch('ready');
  assert(e.elements.conversationSearchResults.innerHTML.includes('tool_result'));
  assert(e.elements.conversationSearchResults.innerHTML.includes('ready'));
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const failure of failures) console.error('  - ' + failure);
  process.exit(1);
}
console.log(passed + ' passing');
