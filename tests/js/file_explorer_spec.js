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
  let reads = 0;
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
    document: {
      createElement() { return pane; },
      body: { appendChild() {} },
    },
    action$() {
      reads++;
      return { subscribe() {} };
    },
    openFileViewer(url) { opened.push(url); },
  };
  vm.createContext(ctx);
  vm.runInContext(previewSource, ctx, { filename: 'file_explorer_preview.js' });
  return { ctx, opened, reads: () => reads };
}

for (const filename of ['clip.mp4', 'document.pdf', 'notes.txt', 'archive.bin']) {
  test(filename + ' preview delegates to the shared viewer', () => {
    const e = env();
    e.ctx._fePreview(filename);
    assert(e.opened.length === 1, 'viewer was not opened');
    assert(
      e.opened[0] === '/fs/relay%20one/media%20folder/' + encodeURIComponent(filename),
      'wrong viewer URL: ' + e.opened[0]);
    assert(e.reads() === 0, 'preview must not read file content through fs_read_file');
  });
}

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const failure of failures) console.error('  - ' + failure);
  process.exit(1);
}
console.log(passed + ' passing');
