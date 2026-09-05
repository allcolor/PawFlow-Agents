// Behavioural tests for filtered Webchat projections. These use the same DOM
// stub as the turn and conversation suites so mixed-agent pruning and cloned
// turn interaction stay covered on CI without requiring Chromium.

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

function append(parent, tag, className, text, dataset) {
  const el = parent.ownerDocument
    ? parent.ownerDocument.createElement(tag)
    : null;
  // The local stub does not expose ownerDocument, so use its document through
  // the helper installed by env().
  const node = el || append.document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  for (const [key, value] of Object.entries(dataset || {})) node.dataset[key] = value;
  parent.appendChild(node);
  return node;
}

function buildMixedTurn(document, source) {
  const block = append(source, 'section', 'msg simple-turn-block', undefined, {turnId: 'turn-1'});
  const header = append(block, 'button', 'simple-turn-header');
  header.setAttribute('aria-expanded', 'false');
  append(header, 'span', 'simple-turn-title', 'claude');
  append(header, 'span', 'simple-turn-status completed', 'Completed');

  const ephemeral = append(block, 'div', 'simple-turn-ephemeral');
  append(ephemeral, 'span', 'simple-turn-cue thinking', 'assistant cue', {agentName: 'assistant'});
  append(ephemeral, 'span', 'simple-turn-cue thinking', 'claude cue', {agentName: 'claude'});
  append(ephemeral, 'span', 'simple-turn-cue thinking', 'unidentified cue');

  const details = append(block, 'div', 'simple-turn-details');
  const tabs = append(details, 'div', 'simple-turn-tabs');
  tabs.setAttribute('role', 'tablist');
  const panels = append(details, 'div', 'simple-turn-panels');
  for (const [index, kind] of ['messages', 'thinking', 'tools', 'artifacts'].entries()) {
    const tab = append(tabs, 'button', 'simple-turn-tab', kind);
    tab.id = 'turn-tab-turn-1-' + kind;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', index ? 'false' : 'true');
    tab.setAttribute('tabindex', index ? '-1' : '0');
    tab.setAttribute('aria-controls', 'turn-panel-turn-1-' + kind);
    const panel = append(panels, 'div', 'simple-turn-panel');
    panel.id = 'turn-panel-turn-1-' + kind;
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', tab.id);
    panel.hidden = index !== 0;
    const scroll = append(panel, 'div', 'simple-turn-panel-scroll');
    if (kind !== 'artifacts') {
      const assistant = append(scroll, 'div', 'msg ' + kind, 'assistant ' + kind,
        {agentName: 'assistant'});
      if (kind === 'tools') append(assistant, 'div', 'tc-result', 'assistant result');
      append(scroll, 'div', 'msg ' + kind, 'claude ' + kind, {agentName: 'claude'});
      append(scroll, 'div', 'msg ' + kind, 'unidentified ' + kind);
    }
  }
  return block;
}

function env() {
  delete require.cache[require.resolve(STUB)];
  const dom = require(STUB);
  append.document = dom.document;
  const source = dom.document.createElement('div');
  source.id = 'messages';
  dom.documentElement.appendChild(source);
  const main = dom.document.createElement('main');
  main.className = 'main';
  dom.documentElement.appendChild(main);
  const calls = [];
  const observers = [];
  let activeConversation = '';
  const ctx = {
    document: dom.document,
    AbortController,
    console,
    encodeURIComponent,
    CSS: { escape: value => String(value) },
    matchMedia: () => ({
      matches: true,
      addEventListener() {},
      removeEventListener() {},
    }),
    requestAnimationFrame: callback => { callback(); return 1; },
    cancelAnimationFrame: () => {},
    MutationObserver: class {
      constructor(callback) {
        this.callback = callback;
        this.connected = false;
        observers.push(this);
      }
      observe() { this.connected = true; }
      disconnect() { this.connected = false; }
    },
    t: key => key,
    displayAgentName: value => String(value || ''),
    captureConversationSession: () => ({conversationId: 'conv-A', messagesRoot: source}),
    workspaceRegisterSurface: panel => dom.documentElement.appendChild(panel),
    workspaceEnsureTabButton: () => {},
    workspaceRemoveTabButton: () => {},
    workspaceUnregisterSurface: () => {},
    workspaceSelectedTab: () => ctx.currentTab,
    workspaceFocusSurface: tabId => { ctx.currentTab = tabId; },
    switchTab: tabId => { ctx.currentTab = tabId; },
    currentTab: 'chat',
    selectedAgent: 'assistant',
    cmdAgentSelect: () => Promise.resolve(true),
    withConversationSession: (conversationId, callback) => {
      const previous = activeConversation;
      activeConversation = conversationId;
      calls.push('session:' + conversationId);
      try { return callback(); }
      finally { activeConversation = previous; }
    },
    loadMoreMessages: () => calls.push('load:' + activeConversation),
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  for (const file of ['ui_motion.js', 'ui_disclosure.js', 'ui_projection.js', 'task_tabs.js']) {
    vm.runInContext(fs.readFileSync(path.join(CHAT_UI, file), 'utf8'), ctx,
      {filename: file});
  }
  return {ctx, dom, source, calls, observers};
}

test('an agent projection keeps only that agent inside mixed turns and aggregates', () => {
  const e = env();
  buildMixedTurn(e.dom.document, e.source);

  const group = append(e.source, 'div', 'msg delegate-group', undefined,
    {agentName: 'claude', groupKey: 'delegate:group-1'});
  const body = append(group, 'div', 'delegate-body');
  append(body, 'div', 'delegate-message', 'assistant delegate', {agent: 'assistant'});
  append(body, 'div', 'delegate-message', 'claude delegate', {agent: 'claude'});

  const parentTool = append(e.source, 'div', 'msg tool', undefined,
    {agentName: 'claude', messageRole: 'tool_call', msgid: 'tool-parent-1'});
  append(parentTool, 'span', 'tc-summary', 'claude parent tool');
  const children = append(parentTool, 'div', 'tc-children');
  append(children, 'div', 'msg tool', 'assistant nested tool',
    {agentName: 'assistant', messageRole: 'tool_call'});

  const banner = append(e.source, 'div', 'load-more-banner', 'Load more messages');
  banner.id = 'loadMoreBanner';

  const tabId = e.ctx.openAgentView('assistant', '');
  const projection = e.ctx.filteredViewRoute(tabId).body;
  const text = projection.textContent;
  for (const expected of [
    'assistant cue', 'assistant messages', 'assistant thinking',
    'assistant tools', 'assistant result', 'assistant delegate',
    'assistant nested tool',
  ]) assert(text.includes(expected), 'missing selected-agent content: ' + expected);
  for (const forbidden of [
    'claude cue', 'claude messages', 'claude thinking', 'claude tools',
    'claude delegate', 'claude parent tool', 'unidentified cue', 'unidentified messages',
    'unidentified thinking', 'unidentified tools',
  ]) assert(!text.includes(forbidden), 'foreign or unidentified content leaked: ' + forbidden);
  eq(projection.querySelector('.simple-turn-title').textContent, 'assistant',
    'the mixed turn header must name the filtered agent');
});

test('cloned turn controls work after rerender and after close/reopen', () => {
  const e = env();
  buildMixedTurn(e.dom.document, e.source);
  let tabId = e.ctx.openAgentView('assistant', '');
  let projection = e.ctx.filteredViewRoute(tabId).body;
  let block = projection.querySelector('.simple-turn-block');
  block.querySelector('.simple-turn-header').click();
  assert(block.classList.contains('expanded'), 'the cloned header did not expand the turn');
  const tabs = block.querySelectorAll('.simple-turn-tab');
  tabs[1].click();
  eq(tabs[1].getAttribute('aria-selected'), 'true', 'the cloned thinking tab did not activate');

  e.ctx._renderFilteredView(tabId);
  projection = e.ctx.filteredViewRoute(tabId).body;
  block = projection.querySelector('.simple-turn-block');
  assert(block.classList.contains('expanded'), 'observer rerender lost expanded state');
  eq(block.querySelectorAll('.simple-turn-tab')[1].getAttribute('aria-selected'), 'true',
    'observer rerender lost the active tab');

  e.ctx.closeFilteredView(tabId);
  tabId = e.ctx.openAgentView('assistant', '');
  block = e.ctx.filteredViewRoute(tabId).body.querySelector('.simple-turn-block');
  block.querySelector('.simple-turn-header').click();
  assert(block.classList.contains('expanded'), 'the reopened clone did not regain interaction');
});

test('filtered load more executes in the owning conversation session', () => {
  const e = env();
  buildMixedTurn(e.dom.document, e.source);
  const banner = append(e.source, 'div', 'load-more-banner', 'Load more messages');
  banner.id = 'loadMoreBanner';
  const tabId = e.ctx.openAgentView('assistant', '');
  const proxy = e.ctx.filteredViewRoute(tabId).body.querySelector('.workspace-filter-load-more');
  assert(proxy, 'the filtered projection has no load-more control');
  proxy.click();
  eq(e.calls.join(','), 'session:conv-A,load:conv-A');
});

test('one character mutation replaces only its keyed projected row', () => {
  const e = env();
  e.ctx.__PF_MOTION_DIAGNOSTICS__ = true;
  append(e.source, 'div', 'msg', 'first', {msgid: 'm-1', agentName: 'assistant'});
  append(e.source, 'div', 'msg', 'second', {msgid: 'm-2', agentName: 'assistant'});
  const tabId = e.ctx.openAgentView('assistant', '');
  const info = e.ctx.filteredViewRoute(tabId);
  const before = Array.from(info.body.children);
  const diagnosticsBefore = e.ctx.pfProjection.diagnostics();

  e.source.children[0].textContent = 'first changed';
  const text = e.source.children[0].firstChild;
  const observer = e.observers.find(candidate => candidate.connected);
  observer.callback([{type: 'characterData', target: text}]);

  const after = Array.from(info.body.children);
  assert(after[0] !== before[0], 'the dirty row was not replaced');
  assert(after[1] === before[1], 'an unchanged keyed row lost DOM identity');
  eq(after[0].textContent, 'first changed');
  const diagnosticsAfter = e.ctx.pfProjection.diagnostics();
  eq(diagnosticsAfter.clones - diagnosticsBefore.clones, 1,
    'one character mutation cloned more than its owning row');
});

test('a hidden projection disconnects before doing clone work', () => {
  const e = env();
  e.ctx.__PF_MOTION_DIAGNOSTICS__ = true;
  append(e.source, 'div', 'msg', 'first', {msgid: 'm-1', agentName: 'assistant'});
  const tabId = e.ctx.openAgentView('assistant', '');
  const info = e.ctx.filteredViewRoute(tabId);
  const clone = info.body.children[0];
  const observer = e.observers.find(candidate => candidate.connected);
  const before = e.ctx.pfProjection.diagnostics().clones;

  info.panel.hidden = true;
  e.source.children[0].textContent = 'changed while hidden';
  observer.callback([{type: 'characterData', target: e.source.children[0].firstChild}]);

  eq(e.ctx.pfProjection.diagnostics().clones, before);
  assert(info.body.children[0] === clone, 'hidden projection changed DOM');
  assert(!observer.connected, 'hidden projection observer stayed connected');
});

test('flash task inspection preserves the conversation agent and composer target', () => {
  const e = env();
  const selected = [];
  e.ctx.cmdAgentSelect = name => { selected.push(name); return Promise.resolve(true); };
  const flash = 'assistant::flash::native_acp_runtime';
  append(e.source, 'div', 'msg', 'finished task output',
    {agentName: flash, taskId: 'de3c3638'});
  const tabId = e.ctx.openAgentView(flash, 'de3c3638');
  e.ctx.activateFilteredView(tabId);
  eq(selected.length, 0, 'a temporary flash agent was selected as a conversation member');
  eq(e.ctx.selectedAgent, 'assistant');
  eq(e.ctx.activeFilteredViewTargetAgent(), '', 'composer targets a finished flash agent');
  assert(e.ctx.filteredViewRoute(tabId).body.textContent.includes('finished task output'));
});

test('a permanent agent filter still selects and targets its conversation agent', () => {
  const e = env();
  const selected = [];
  e.ctx.cmdAgentSelect = name => { selected.push(name); return Promise.resolve(true); };
  const tabId = e.ctx.openAgentView('claude', '');
  e.ctx.activateFilteredView(tabId);
  eq(selected.join(','), 'claude');
  eq(e.ctx.activeFilteredViewTargetAgent(), 'claude');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const failure of failures) console.error('  - ' + failure);
  process.exit(1);
}
console.log(passed + ' passing');
