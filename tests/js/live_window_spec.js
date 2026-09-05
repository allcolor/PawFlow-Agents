// Logical-message window accounting, selection protection and cursor cleanup.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const stub = path.join(__dirname, 'dom_stub.js');
let passed = 0;
function env() {
  delete require.cache[require.resolve(stub)];
  const dom = require(stub);
  const box = dom.document.createElement('div'); box.id = 'messages';
  dom.documentElement.appendChild(box);
  const ctx = {document: dom.document, displayWindow: 50, hasMoreMessages: false,
    _selectedMsgIds: new Set(), _seenMsgIds: new Set(), rewound: 0,
    _rewindHistoryCursor(n) { ctx.rewound += n; }, _updateLoadMoreBanner() {},
    turnViewEvictionGroup: el => [el], turnViewForgetElement() {},
  };
  ctx.window = ctx; vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname,
    '../../tasks/io/chat_ui/messages_render.js'), 'utf8'), ctx);
  function row(id, children = 0) {
    const root = dom.document.createElement('div');
    root.className = 'msg'; root.dataset.msgid = id; ctx._seenMsgIds.add(id);
    for (let i = 0; i < children; i++) {
      const child = dom.document.createElement('div');
      child.dataset.msgid = id + '-' + i;
      ctx._seenMsgIds.add(child.dataset.msgid); root.appendChild(child);
    }
    box.appendChild(root); return root;
  }
  return {ctx, box, row, trim: on => ctx.trimLiveDisplayWindowIfAutoscrolling(on)};
}
function test(name, fn) { fn(); passed++; console.log('PASS ' + name); }
test('nested durable messages count even with only two top-level rows', () => {
  const e = env(); const old = e.row('old', 250), latest = e.row('latest');
  e.trim(true);
  assert.equal(old.isConnected, false); assert.equal(latest.isConnected, true);
  assert.equal(e.ctx.rewound, 251); assert.equal(e.ctx._seenMsgIds.size, 1);
  assert.equal(e.ctx.hasMoreMessages, true);
});
test('a selected nested message protects its entire group', () => {
  const e = env(); const old = e.row('old', 250); e.row('latest');
  e.ctx._selectedMsgIds.add('old-125'); e.trim(true);
  assert.equal(old.isConnected, true); assert.equal(e.ctx.rewound, 0);
  assert.equal(e.ctx.hasMoreMessages, false);
});
test('the most recent large answer is retained before trailing transient content', () => {
  for (const className of ['typing-indicator', 'terminal-exit', 'history-task-detail']) {
    const e = env(); const old = e.row('old'), latest = e.row('latest', 300);
    const transient = e.ctx.document.createElement('div');
    transient.className = className; e.box.appendChild(transient);
    e.trim(true);
    assert.equal(old.isConnected, false); assert.equal(latest.isConnected, true);
    assert.equal(transient.isConnected, true); assert.equal(e.ctx.rewound, 1);
  }
});
test('manual history browsing does not evict content', () => {
  const e = env(); const old = e.row('old', 300); e.row('latest'); e.trim(false);
  assert.equal(old.isConnected, true); assert.equal(e.ctx.rewound, 0);
});
test('browser text selection protects the selected group', () => {
  const e = env(); const old = e.row('old', 250); e.row('latest');
  e.ctx.getSelection = () => ({isCollapsed: false, containsNode: node => node === old});
  e.trim(true); assert.equal(old.isConnected, true);
});
console.log(passed + ' live-window tests passed');
