// Behavioural tests for relay-file previews in file_explorer.js.
// Run directly: node tests/js/file_explorer_spec.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'file_explorer.js');
const source = fs.readFileSync(SOURCE, 'utf8');
const lifecycleSource = source.slice(
  0, source.indexOf('\nfunction _feLoadSvcs'));
const keysSource = source.slice(
  source.indexOf('function _feKeys(e)'),
  source.indexOf('\nfunction _feFmtSz'));
const previewSource = source.slice(
  source.indexOf('function _fePreview(name)'),
  source.indexOf('\nfunction _feSearch'));
const VIEWER_SOURCE = path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'file_viewer.js');
const viewerFileSource = fs.readFileSync(VIEWER_SOURCE, 'utf8');
const viewerSource = viewerFileSource.slice(
  viewerFileSource.indexOf('function openFileViewer(filenameOrUrl, displayName, sourceBlob)'),
  viewerFileSource.indexOf('\nfunction closeFileViewer'));

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
    _fe: { preview: null, svc: 'relay one', path: 'media folder', surface: { dataset: { conversationId: 'law' } } },
    conversationId: 'other',
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
    openFileViewer(url, name, blob) { opened.push({ url, name, blob }); },
  };
  vm.createContext(ctx);
  vm.runInContext(previewSource, ctx, { filename: 'file_explorer_preview.js' });
  return { ctx, opened, calls };
}

function workspaceEnv() {
  const registered = [];
  const railEntries = [];
  const removedRailEntries = [];
  const unregistered = [];
  const focused = [];
  const navigated = [];
  const surfaces = [];
  let keyListeners = 0;
  let loads = 0;
  let selected = 'chat';

  function makeSurface() {
    const content = { addEventListener() {} };
    const panel = {
      addEventListener() {},
      classList: { add() {}, remove() {} },
    };
    const surface = {
      className: '',
      dataset: {},
      id: '',
      innerHTML: '',
      removed: false,
      querySelector(selector) {
        if (selector === '.fe-content') return content;
        if (selector === '.fe-panel') return panel;
        return null;
      },
      remove() { this.removed = true; },
    };
    surfaces.push(surface);
    return surface;
  }

  const ctx = {
    document: {
      activeElement: { tagName: 'DIV' },
      addEventListener(type) { if (type === 'keydown') keyListeners++; },
      removeEventListener(type) { if (type === 'keydown') keyListeners--; },
      body: { appendChild() {} },
      createElement: makeSurface,
      querySelector() { return { appendChild() {} }; },
    },
    _feNav(path) { navigated.push(path); },
    _feLoadSvcs() { loads++; },
    switchTab(tabId) { focused.push(tabId); selected = tabId; },
    t(key) { return key === 'fileExplorer' ? 'File Explorer' : key; },
    workspaceEnsureTabButton(tabId, options) { railEntries.push({ tabId, options }); },
    workspaceFocusSurface(tabId) { focused.push(tabId); selected = tabId; },
    workspaceRegisterSurface(surface, options) { registered.push({ surface, options }); },
    workspaceRemoveTabButton(tabId) { removedRailEntries.push(tabId); },
    workspaceSelectedTab() { return selected; },
    workspaceUnregisterSurface(tabId) { unregistered.push(tabId); selected = 'chat'; },
  };
  vm.createContext(ctx);
  vm.runInContext(lifecycleSource, ctx, { filename: 'file_explorer_lifecycle.js' });
  vm.runInContext(keysSource, ctx, { filename: 'file_explorer_keys.js' });
  return {
    ctx,
    focused,
    navigated,
    registered,
    railEntries,
    removedRailEntries,
    surfaces,
    unregistered,
    run(body) { return vm.runInContext(body, ctx, { filename: 'file_explorer_test.js' }); },
    get keyListeners() { return keyListeners; },
    get loads() { return loads; },
  };
}

function viewerEnv() {
  const fetches = [];
  const elements = {
    fileViewer: { style: {} },
    viewerContent: { innerHTML: '' },
    viewerFileName: { textContent: '' },
    viewerFileSize: { textContent: '' },
    viewerDownload: { download: '', href: '' },
  };
  const ctx = {
    API: 'https://webchat.example.org/api/chat',
    document: { getElementById(id) { return elements[id] || null; } },
    escapeHtml: value => String(value),
    fetch(url, options) {
      fetches.push({ url, options });
      return { then() { return { catch() {} }; } };
    },
    getToken: () => 'secret-token',
    location: { origin: 'https://webchat.example.org' },
    t: key => key,
  };
  vm.createContext(ctx);
  vm.runInContext(viewerSource, ctx, { filename: 'file_viewer_preview.js' });
  return { ctx, elements, fetches };
}

for (const filename of ['clip.mp4', 'document.pdf', 'notes.txt', 'archive.bin']) {
  test(filename + ' preview reads through the relay and delegates to the shared viewer', () => {
    const e = env();
    e.ctx._fePreview(filename);
    assert(e.calls.length === 1, 'filesystem was not read exactly once');
    assert(e.calls[0].action === 'fs_read_file', 'wrong action: ' + e.calls[0].action);
    assert(e.calls[0].payload.service === 'relay one', 'wrong relay service');
    assert(e.calls[0].payload.conversation_id === 'law', 'preview lost its tile conversation');
    assert(e.calls[0].payload.path === 'media folder/' + filename, 'wrong file path');
    assert(e.opened.length === 1, 'viewer was not opened');
    assert(e.opened[0].url === 'blob:relay-preview', 'wrong viewer URL');
    assert(e.opened[0].name === filename, 'viewer lost the file name');
    assert(e.opened[0].blob instanceof Blob, 'viewer did not receive the relay Blob');
  });
}

test('file explorer uses one closable workspace tile and can reopen after close', () => {
  const e = workspaceEnv();
  e.ctx.openExplorer();
  assert(e.registered.length === 1, 'file explorer was not registered as a workspace surface');
  assert(e.registered[0].options.tabId === 'files', 'file explorer used the wrong tile id');
  assert(e.registered[0].options.type === 'file-explorer', 'file explorer used the wrong surface type');
  assert(e.registered[0].options.title === 'File Explorer', 'file explorer lost its translated title');
  assert(e.registered[0].options.closable === true, 'file explorer tile is not closable');
  assert(e.railEntries.length === 1 && e.railEntries[0].tabId === 'files',
    'file explorer did not create its workspace rail entry');
  assert(e.surfaces[0].className.includes('tab-content'), 'file explorer is not a tab surface');
  assert(e.surfaces[0].className.includes('fe-surface'), 'file explorer tile class is missing');
  assert(!e.surfaces[0].className.includes('fe-overlay'), 'legacy modal overlay is still present');
  assert(!e.surfaces[0].innerHTML.includes('onclick="closeExplorer()"'),
    'tile duplicates the workspace close control');
  assert(e.focused.join(',') === 'files', 'new file explorer tile was not focused');
  assert(e.loads === 1, 'new file explorer tile did not load services exactly once');
  assert(e.keyListeners === 1, 'file explorer keyboard handler was not installed');

  e.ctx.openExplorer();
  assert(e.registered.length === 1, 'reopening duplicated the file explorer tile');
  assert(e.railEntries.length === 1, 'reopening duplicated the workspace rail entry');
  assert(e.loads === 1, 'reopening reloaded an already mounted file explorer');
  assert(e.focused.join(',') === 'files,files', 'reopening did not focus the existing tile');

  e.run("_fe.path='child';");
  e.ctx.switchTab('chat');
  let prevented = false;
  e.ctx._feKeys({ key: 'Backspace', ctrlKey: false, preventDefault() { prevented = true; } });
  assert(!prevented, 'hidden file explorer intercepted the Backspace key');
  assert(e.navigated.length === 0, 'hidden file explorer navigated on Backspace');

  e.ctx.switchTab('files');
  e.ctx._feKeys({ key: 'Backspace', ctrlKey: false, preventDefault() { prevented = true; } });
  assert(prevented, 'focused file explorer did not intercept the Backspace key');
  assert(e.navigated.join(',') === '.', 'focused file explorer did not navigate to its parent');

  e.ctx.closeExplorer();
  assert(e.unregistered.join(',') === 'files', 'closing did not unregister the file explorer tile');
  assert(e.removedRailEntries.join(',') === 'files', 'closing did not remove the rail entry');
  assert(e.surfaces[0].removed, 'closing did not remove the file explorer surface');
  assert(e.keyListeners === 0, 'closing leaked the file explorer keyboard handler');
  assert(e.focused[e.focused.length - 1] === 'chat', 'closing did not restore workspace focus');

  e.ctx.openExplorer();
  assert(e.registered.length === 2, 'file explorer could not reopen after close');
  assert(e.loads === 2, 'reopened file explorer did not reload its services');
});

test('explorer service selection and navigation send the same conversation scope', () => {
  const rxbus = fs.readFileSync(path.join(
    __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'rxbus.js'), 'utf8');
  const actionSource = rxbus.slice(
    rxbus.indexOf('function action$('), rxbus.indexOf('\n/**', rxbus.indexOf('function action$(')));
  const navigationSource = source.slice(
    source.indexOf('function _feLoadSvcs()'), source.indexOf('\nfunction _feRender()'));
  const requests = [];
  const elements = { feSvcSel: {}, feTbody: {} };
  let response;
  let renders = 0;
  const ctx = {
    conversationId: 'other',
    API: '/api/agent',
    _fe: { svc: '', svcs: [], entries: [], sel: new Set(), surface: { dataset: { conversationId: 'law' } } },
    document: { getElementById(id) { return elements[id]; } },
    t: key => key,
    _feRender() { renders++; },
    _feBc() {},
    getAuthHeaders: () => ({}),
    _ensureUIActionSSE() {},
    _uiActionConversationId: () => 'ui-tab',
    _trackPendingAction() {},
    _untrackPendingAction() {},
    // Execute the real request builder; only the observable transport is stubbed.
    defer(factory) { return { subscribe(callback) { factory().subscribe(callback); } }; },
    filter() {}, first() {}, map() {}, catchError() {}, finalize() {},
    _commandResult$: {
      pipe() {
        const result = response;
        return { subscribe(callback) { callback(result); } };
      },
    },
    fetch(url, options) {
      const body = JSON.parse(options.body);
      requests.push(body);
      response = body.action === 'fs_list_services'
        ? { services: [{ id: 'permisWS', type: 'relay', scope: 'conv' }] }
        : { entries: [{ name: 'plans', kind: 'directory' }] };
      return { then() { return { catch() {} }; } };
    },
  };
  vm.createContext(ctx);
  vm.runInContext(actionSource + '\n' + navigationSource, ctx);
  ctx._feLoadSvcs();
  assert(requests.length === 2, 'selecting a service did not list its root');
  assert(requests[0].action === 'fs_list_services', 'service picker request missing');
  assert(requests[1].action === 'fs_list_dir', 'directory request missing');
  assert(requests.every(request => request.conversation_id === 'law'),
    'filesystem requests lost the selected conversation');
  assert(requests[1].service === 'permisWS', 'directory request lost the selected relay');
  assert(requests[1].path === '.', 'directory request lost the root path');
  assert(ctx._fe.entries[0].name === 'plans' && renders === 1,
    'directory result did not populate the explorer');
});

test('relay blob preview bypasses the CSP-blocked fetch path', () => {
  const e = viewerEnv();
  const blob = { size: 7, type: 'image/png' };
  e.ctx.openFileViewer('blob:relay-preview', 'pawflow_art.png', blob);
  assert(e.fetches.length === 0, 'viewer fetched a local blob through connect-src');
  assert(e.elements.viewerDownload.href === 'blob:relay-preview',
    'viewer download did not reuse the relay blob URL');
  assert(e.elements.viewerContent.innerHTML.includes('<img src="blob:relay-preview"'),
    'viewer did not render the relay image Blob');
});

test('same-origin FileStore preview keeps authenticated fetch options', () => {
  const e = viewerEnv();
  e.ctx.openFileViewer('/files/fid/pawflow_art.png');
  assert(e.fetches.length === 1, 'viewer did not fetch the FileStore file');
  assert(e.fetches[0].url === 'https://webchat.example.org/files/fid/pawflow_art.png',
    'viewer did not resolve the same-origin file URL');
  assert(e.fetches[0].options.credentials === 'same-origin',
    'same-origin file fetch lost its credentials mode');
  assert(e.fetches[0].options.headers.Authorization === 'Bearer secret-token',
    'same-origin file fetch lost its bearer token');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const failure of failures) console.error('  - ' + failure);
  process.exit(1);
}
console.log(passed + ' passing');
