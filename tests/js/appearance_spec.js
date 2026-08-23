// Behavioural tests for inherited user and conversation appearance preferences.
// Run directly: node tests/js/appearance_spec.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'appearance.js');

let passed = 0;
const failures = [];

function test(name, fn) {
  try { fn(); passed++; }
  catch (error) {
    failures.push(name + ': ' + (error && error.message ? error.message : error));
  }
}
function equal(actual, expected, message) {
  if (actual !== expected) {
    throw new Error((message ? message + ': ' : '') + 'expected '
      + JSON.stringify(expected) + ' but got ' + JSON.stringify(actual));
  }
}

function env() {
  const values = new Map();
  const listeners = {};
  const styleValues = {};
  const document = {
    hidden: false,
    body: { style: {} },
    documentElement: {
      dataset: {},
      style: { setProperty(name, value) { styleValues[name] = value; } },
    },
    addEventListener(name, fn) { listeners['document:' + name] = fn; },
    getElementById() { return null; },
  };
  const window = {
    location: { href: 'https://pawflow.test/chat', origin: 'https://pawflow.test' },
    PAWFLOW_EXTENSION_CONTEXT: { user: 'bootstrap-user' },
    addEventListener(name, fn) { listeners['window:' + name] = fn; },
    matchMedia() { return { matches: false }; },
    indexedDB: null,
  };
  const context = {
    console,
    document,
    window,
    localStorage: {
      getItem(key) { return values.has(key) ? values.get(key) : null; },
      setItem(key, value) { values.set(key, value); },
      removeItem(key) { values.delete(key); },
    },
    URL,
    setTimeout,
    clearTimeout,
    t: key => key,
    addMsg() {},
    conversationId: '',
  };
  window.localStorage = context.localStorage;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), context, {
    filename: 'appearance.js',
  });
  return { context, document, listeners, styleValues, values, window };
}

test('normalization clamps every numeric preference to safe bounds', () => {
  const e = env();
  const prefs = e.context._appearanceNormalize({
    scale: 999, dim: -3, blur: 100, saturation: 1, panel: 0,
  });
  equal(prefs.scale, 150);
  equal(prefs.dim, 0);
  equal(prefs.blur, 24);
  equal(prefs.saturation, 50);
  equal(prefs.panel, 55);
});

test('storage key follows the authenticated user when available', () => {
  const e = env();
  equal(e.context._appearanceStorageKey(), 'pawflow.appearance.v1:bootstrap-user');
  e.window._userId = 'alice';
  equal(e.context._appearanceStorageKey(), 'pawflow.appearance.v1:alice');
});

test('conversation storage and blob keys are isolated from the global preference', () => {
  const e = env();
  e.window._userId = 'alice';
  e.context.conversationId = 'conv-7';
  equal(e.context._appearanceStorageKey('conversation'),
    'pawflow.appearance.v1:alice:conversation:conv-7');
  equal(e.context._appearanceBlobKey('global'), 'alice');
  equal(e.context._appearanceBlobKey('conversation'), 'alice:conversation:conv-7');
});

test('conversation override wins while a missing override inherits the global value', () => {
  const e = env();
  e.window._userId = 'alice';
  const globalKey = 'pawflow.appearance.v1:alice';
  e.values.set(globalKey, JSON.stringify({ scale: 110 }));
  e.context.conversationId = 'conv-7';
  e.context._appearanceLoadPrefs();
  e.context._appearanceApplyEffects();
  equal(e.document.body.style.zoom, '1.1');

  e.values.set(globalKey + ':conversation:conv-7', JSON.stringify({ scale: 125 }));
  e.context._appearanceLoadPrefs();
  e.context._appearanceApplyEffects();
  equal(e.document.body.style.zoom, '1.25');
});

test('scale is persisted for the active user and applied immediately', () => {
  const e = env();
  e.window._userId = 'alice';
  e.context._appearanceLoadPrefs();
  e.context.setAppearanceScale(125);
  equal(e.document.body.style.zoom, '1.25');
  const saved = JSON.parse(e.values.get('pawflow.appearance.v1:alice'));
  equal(saved.scale, 125);
});

test('remote backgrounds require HTTPS while same-origin URLs stay valid', () => {
  const e = env();
  equal(e.context._appearanceValidatedUrl('http://remote.test/a.jpg'), '');
  equal(e.context._appearanceValidatedUrl('https://remote.test/a.jpg'),
    'https://remote.test/a.jpg');
  equal(e.context._appearanceValidatedUrl('/files/bg.webp'),
    'https://pawflow.test/files/bg.webp');
});

test('server-backed uploads keep their private file identifier', () => {
  const e = env();
  const prefs = e.context._appearanceNormalize({
    source: 'upload', file_id: 'file-42', url: '/files/file-42/background.webp',
  });
  equal(prefs.file_id, 'file-42');
  equal(prefs.source, 'upload');
});

test('migration marker is isolated per authenticated user', () => {
  const e = env();
  equal(e.context._appearanceMigrationKey(),
    'pawflow.appearance.serverMigrated.v1:bootstrap-user');
  e.window._userId = 'alice';
  equal(e.context._appearanceMigrationKey(),
    'pawflow.appearance.serverMigrated.v1:alice');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const failure of failures) console.error('  - ' + failure);
  process.exit(1);
}
console.log(passed + ' passing');
