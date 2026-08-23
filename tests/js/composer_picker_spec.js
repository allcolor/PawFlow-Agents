// Behavioural tests for the slash-command and agent-mention composer picker.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'file_mention.js');

let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed++; }
  catch (error) { failures.push(name + ': ' + (error.message || error)); }
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}
function equal(actual, expected, message) {
  if (actual !== expected) throw new Error(
    (message ? message + ': ' : '') + 'expected ' + JSON.stringify(expected)
    + ' but got ' + JSON.stringify(actual));
}

function env() {
  function container(children) {
    const parent = {
      children: children || [],
      insertBefore(node, anchor) {
        if (node.parentNode) {
          node.parentNode.children = node.parentNode.children.filter(child => child !== node);
        }
        const index = anchor ? this.children.indexOf(anchor) : -1;
        if (index < 0) this.children.push(node);
        else this.children.splice(index, 0, node);
        node.parentNode = this;
      },
    };
    parent.children.forEach(child => { child.parentNode = parent; });
    return parent;
  }
  function node(id) {
    return { id, parentNode: null, setAttribute() {} };
  }
  const input = {
    value: '', selectionStart: 0, selectionEnd: 0,
    setRangeText(value, start, end) {
      this.value = this.value.slice(0, start) + value + this.value.slice(end);
      this.selectionStart = this.selectionEnd = start + value.length;
    },
    addEventListener() {}, dispatchEvent() {}, focus() {}, setAttribute() {},
  };
  const picker = {
    hidden: true, innerHTML: '', dataset: {},
    querySelectorAll() { return []; }, contains() { return false; },
  };
  const agentPicker = {
    hidden: true, innerHTML: '',
    querySelectorAll() { return []; },
  };
  const agentOverlay = {
    id: 'composerAgentOverlay', hidden: true, contains() { return false; },
  };
  const agentLabel = { textContent: '' };
  const agentBadge = {
    title: '', attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
  };
  const extensionSlot = node('composerExtensionSlot');
  const actionsPanel = Object.assign(container([extensionSlot]), {
    dataset: { open: 'false' }, attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    querySelector(selector) {
      return selector === '.composer-extension-slot' ? extensionSlot : null;
    },
  });
  const mobileToggle = {
    attributes: {}, setAttribute(name, value) { this.attributes[name] = value; },
  };
  const speechInput = node('speechInputBtn');
  const grab = node('grabBtn');
  const send = node('sendBtn');
  const trailing = container([speechInput, grab, agentOverlay, send]);
  const media = { matches: true };
  const buttons = {
    composerSlashBtn: { setAttribute() {} }, composerMentionBtn: { setAttribute() {} },
    composerMobileActions: actionsPanel, composerMobileActionsBtn: mobileToggle,
    composerAgentPicker: agentPicker, composerAgentOverlay: agentOverlay,
    composerAgentBadge: agentBadge, composerAgentBadgeLabel: agentLabel,
    speechInputBtn: speechInput, grabBtn: grab, sendBtn: send,
  };
  const context = {
    console,
    document: {
      addEventListener() {},
      getElementById(id) { return id === 'input' ? input : id === 'composerPicker' ? picker : buttons[id] || null; },
      querySelector(selector) { return selector === '.composer-trailing' ? trailing : null; },
    },
    window: { matchMedia() { return media; } }, Event: function Event() {},
    HELP_DATA: {
      '/help': { usage: '/help [command]', short: 'Show help' },
      '/history': { usage: '/history', short: 'Show history' },
      '/h': { alias: '/help' },
    },
    selectedAgent: 'assistant',
    nicknameMap: { assistant: 'Helper' },
    activeInteractions: { one: { name: 'claude' } },
    _lastResourcesData: { agents: [{ name: 'assistant' }, { name: 'research agent' }] },
    displayAgentName(name) { return context.nicknameMap[name] || name; },
    escapeHtml(value) { return String(value); },
    t(key, values) {
      if (key === 'selectedAgentLabel') return 'Selected agent: ' + values.name;
      return key;
    },
    cmdAgentSelect(name) { context.selectedAgent = name; return Promise.resolve(true); },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), context, { filename: 'file_mention.js' });
  return { context, input, picker, actionsPanel, mobileToggle, agentPicker,
    agentOverlay, agentBadge, agentLabel, extensionSlot, speechInput, grab,
    send, trailing, media };
}

test('slash choices use real commands, filter them, and hide aliases', () => {
  const e = env();
  const rows = e.context._composerSlashChoices('hist');
  equal(rows.length, 1);
  equal(rows[0].value, '/history');
  assert(!e.context._composerSlashChoices('').some(row => row.value === '/h'));
});

test('mention choices combine configured, active, and selected agents without duplicates', () => {
  const rows = env().context._composerMentionChoices('');
  equal(rows.map(row => row.value).join(','), 'assistant,research agent,claude');
  equal(rows[0].label, 'Helper');
});

test('trigger parsing follows the token at the caret', () => {
  const e = env();
  e.input.value = 'please /his';
  e.input.selectionStart = e.input.selectionEnd = e.input.value.length;
  const trigger = e.context._composerTriggerAtCaret(e.input);
  equal(trigger.kind, 'slash');
  equal(trigger.query, 'his');
  equal(trigger.start, 7);
});

test('selecting a mention replaces the active token and quotes spaces', () => {
  const e = env();
  e.input.value = '@rese';
  e.input.selectionStart = e.input.selectionEnd = 5;
  e.context._composerPickerState = {
    kind: 'mention', start: 0, end: 5, selected: 0,
    items: [{ value: 'research agent', label: 'research agent', description: '' }],
  };
  e.context._composerChoose(0);
  equal(e.input.value, '@"research agent" ');
});

test('mobile secondary actions open behind one explicit toggle', () => {
  const e = env();
  e.context.toggleComposerMobileActions();
  equal(e.actionsPanel.dataset.open, 'true');
  equal(e.mobileToggle.attributes['aria-expanded'], 'true');
});

test('closing mobile actions updates both panel and accessibility state', () => {
  const e = env();
  e.context.toggleComposerMobileActions(true);
  e.context.toggleComposerMobileActions(false);
  equal(e.actionsPanel.dataset.open, 'false');
  equal(e.actionsPanel.attributes['aria-hidden'], 'true');
  equal(e.mobileToggle.attributes['aria-expanded'], 'false');
});

test('responsive composer moves Micro and Grab into the mobile menu and restores desktop order', () => {
  const e = env();
  e.context._composerPlaceResponsiveActions();
  equal(e.speechInput.parentNode, e.actionsPanel);
  equal(e.grab.parentNode, e.actionsPanel);
  equal(
    e.actionsPanel.children.map(child => child.id).join(','),
    'speechInputBtn,grabBtn,composerExtensionSlot'
  );

  e.media.matches = false;
  e.context._composerPlaceResponsiveActions();
  equal(e.speechInput.parentNode, e.trailing);
  equal(e.grab.parentNode, e.trailing);
  equal(
    e.trailing.children.map(child => child.id).join(','),
    'speechInputBtn,grabBtn,composerAgentOverlay,sendBtn'
  );
});

test('selected agent badge uses the nickname and exposes the quick picker', () => {
  const e = env();
  e.context.updateComposerAgentBadge();
  equal(e.agentOverlay.hidden, false);
  equal(e.agentLabel.textContent, 'Selected agent: Helper');
  e.context.toggleComposerAgentPicker(true);
  equal(e.agentPicker.hidden, false);
  equal(e.agentBadge.attributes['aria-expanded'], 'true');
  assert(e.agentPicker.innerHTML.includes('research agent'));
});

test('selected agent badge hides cleanly when no agent is available', () => {
  const e = env();
  e.context.selectedAgent = '';
  e.context.updateComposerAgentBadge();
  equal(e.agentOverlay.hidden, true);
  equal(e.agentBadge.attributes['aria-expanded'], 'false');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  failures.forEach(failure => console.error('  - ' + failure));
  process.exit(1);
}
console.log(passed + ' passing');
