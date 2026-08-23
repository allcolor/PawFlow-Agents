// Behavioural tests for the compact permission-mode button and menu.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'state.js'), 'utf8');
const permissionSource = source.slice(
  source.indexOf("let permissionMode = 'default'"),
  source.indexOf('let nicknameMap = {}'));

let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed++; }
  catch (error) { failures.push(name + ': ' + (error.message || error)); }
}
function equal(actual, expected, message) {
  if (actual !== expected) throw new Error(
    (message ? message + ': ' : '') + 'expected ' + JSON.stringify(expected)
    + ' but got ' + JSON.stringify(actual));
}

function env() {
  const listeners = {};
  const calls = [];
  const wrap = { style: {}, contains(target) { return target === button; } };
  const menu = { hidden: true };
  const icon = { textContent: '' };
  const button = {
    title: '', attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
  };
  const items = ['default', 'approve_edits', 'read_only', 'auto'].map(mode => ({
    dataset: { permissionMode: mode }, attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
  }));
  const elements = {
    permissionModeWrap: wrap, permissionModeMenu: menu,
    permissionModeIcon: icon, permissionModeBtn: button,
  };
  const context = {
    conversationId: 'conv-1',
    document: {
      getElementById(id) { return elements[id] || null; },
      querySelectorAll() { return items; },
      addEventListener(name, handler) { listeners[name] = handler; },
    },
    window: {
      _pawflowExtRuntime: { fireHook(name, payload) { calls.push(['hook', name, payload]); } },
    },
    fireAction(name, payload) { calls.push(['action', name, payload]); },
    action$() { return { subscribe() {} }; },
    t(key) { return key; },
  };
  vm.createContext(context);
  vm.runInContext(permissionSource, context, { filename: 'state.permission.js' });
  return { context, listeners, calls, wrap, menu, icon, button, items };
}

test('button exposes the current mode and opens a separate menu', () => {
  const e = env();
  e.context.updatePermissionBadge();
  equal(e.wrap.style.display, 'inline-flex');
  equal(e.icon.textContent, '🔒');
  equal(e.button.attributes['aria-label'], 'permissionModeTitle — permissionDefault');
  equal(e.items[0].attributes['aria-checked'], 'true');
  e.context.togglePermissionModeMenu();
  equal(e.menu.hidden, false);
  equal(e.button.attributes['aria-expanded'], 'true');
});

test('selecting a mode closes the menu and updates action, icon, and checked row', () => {
  const e = env();
  e.context.togglePermissionModeMenu(true);
  e.context.setPermissionMode('auto');
  equal(e.menu.hidden, true);
  equal(e.icon.textContent, '⚡');
  equal(e.items[3].attributes['aria-checked'], 'true');
  equal(e.calls[0][0], 'action');
  equal(e.calls[0][1], 'set_permission_mode');
  equal(e.calls[0][2].mode, 'auto');
});

test('outside click and Escape close the permission menu', () => {
  const e = env();
  e.context.togglePermissionModeMenu(true);
  e.listeners.click({ target: {} });
  equal(e.menu.hidden, true);
  e.context.togglePermissionModeMenu(true);
  e.listeners.keydown({ key: 'Escape' });
  equal(e.menu.hidden, true);
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  failures.forEach(failure => console.error('  - ' + failure));
  process.exit(1);
}
console.log(passed + ' passing');
