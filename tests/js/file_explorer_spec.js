// Behavioural tests for relay-file previews in file_explorer.js.
// Run directly: node tests/js/file_explorer_spec.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'file_explorer.js');
const source = fs.readFileSync(SOURCE, 'utf8');
const previewSource = source.slice(
  source.indexOf('function _fePreview(name)'),
  source.indexOf('\nfunction _feSearch'));

let passed = 0;
const failures = [];

function test(name, fn) {
  try { fn(); passed++; }
  catch (err) { failures.push(name + ': ' + (err && err.message ? err.message : err)); }
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg || 'assertion failed');
}

function env() {
  const opened = [];
  const calls = [];
  const pane = {
    className: '',
    innerHTML: '',
    remove() {},
    querySelector() { return { textContent: '' }; },
  };
  const ctx = {
    _fe: { preview: null, svc: 'relay one', path: 'media folder' },
    _fePath: name => 'media folder/' + name,
    _feEsc: value => String(value),
    _feFmtSz: value => String(value),
    t: key => key,
    atob: value => value,
    Blob,
    Uint8Array,
    URL: { createObjectURL() { return 'blob:relay-preview'; } },
    document: {
      createElement() { return pane; },
      body: { appendChild() {} },
    },
    action$(action, payload) {
      calls.push({ action, payload });
      return { subscribe(callback) {
        callback({ content: 'file body', encoding: 'utf-8' });
      } };
    },
    openFileViewer(url, name) { opened.push({ url, name }); },
  };
  vm.createContext(ctx);
  vm.runInContext(previewSource, ctx, { filename: 'file_explorer_preview.js' });
  return { ctx, opened, calls };
}

for (const filename of ['clip.mp4', 'document.pdf', 'notes.txt', 'archive.bin']) {
  test(filename + ' preview reads through the relay and delegates to the shared viewer', () => {
    const e = env();
    e.ctx._fePreview(filename);
    assert(e.calls.length === 1, 'filesystem was not read exactly once');
    assert(e.calls[0].action === 'fs_read_file', 'wrong action: ' + e.calls[0].action);
    assert(e.calls[0].payload.service === 'relay one', 'wrong relay service');
    assert(e.calls[0].payload.path === 'media folder/' + filename, 'wrong file path');
    assert(e.opened.length === 1, 'viewer was not opened');
    assert(e.opened[0].url === 'blob:relay-preview', 'wrong viewer URL');
    assert(e.opened[0].name === filename, 'viewer lost the file name');
  });
}

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const failure of failures) console.error('  - ' + failure);
  process.exit(1);
}
console.log(passed + ' passing');
