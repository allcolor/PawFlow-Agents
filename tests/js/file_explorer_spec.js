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
  viewerFileSource.indexOf('const VIEWER_TEXT_PREVIEW_BYTES'),
  viewerFileSource.indexOf('\n// Intercept file links'));

let passed = 0;
const failures = [];
const pending = [];

function test(name, fn) {
  const fail = err => failures.push(name + ': ' + (err && err.message ? err.message : err));
  try {
    const result = fn();
    if (result && typeof result.then === 'function') pending.push(result.then(() => { passed++; }, fail));
    else passed++;
  } catch (err) { fail(err); }
}

// Let every queued promise callback of the code under test run.
const settle = () => new Promise(resolve => setImmediate(resolve));

function assert(cond, msg) {
  if (!cond) throw new Error(msg || 'assertion failed');
}

function env() {
  const opened = [];
  const calls = [];
  const ctx = {
    _fe: { svc: 'relay one', path: 'media folder', surface: { dataset: { conversationId: 'law' } } },
    conversationId: 'other',
    _fePath: name => 'media folder/' + name,
    URLSearchParams,
    location: { origin: 'https://webchat.example.org' },
    action$(action, payload) {
      calls.push({ action, payload });
      return { subscribe() {} };
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

// `responses` are served to fetch() in order; a missing one never settles.
function viewerEnv(responses = []) {
  const fetches = [];
  const media = { onerror: null };
  const elements = {
    fileViewer: { style: {} },
    viewerContent: {
      innerHTML: '',
      querySelector() { return /<(img|video|audio)\b/.test(this.innerHTML) ? media : null; },
    },
    viewerFileName: { textContent: '' },
    viewerFileSize: { textContent: '' },
    viewerDownload: { download: '', href: '' },
  };
  const ctx = {
    API: 'https://webchat.example.org/api/chat',
    AbortController,
    TextDecoder,
    URL,
    document: { getElementById(id) { return elements[id] || null; } },
    escapeHtml: value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    fetch(url, options) {
      fetches.push({ url, options });
      const response = responses.shift();
      return response ? Promise.resolve(response) : new Promise(() => {});
    },
    getToken: () => 'secret-token',
    location: { origin: 'https://webchat.example.org' },
    t: key => key,
  };
  vm.createContext(ctx);
  vm.runInContext(viewerSource, ctx, { filename: 'file_viewer_preview.js' });
  const limit = vm.runInContext('VIEWER_TEXT_PREVIEW_BYTES', ctx);
  return { ctx, elements, fetches, media, limit };
}

// A fetch() Response whose body is read chunk by chunk.
function streamResponse(chunks, headers) {
  const body = { reads: 0 };
  body.getReader = () => ({
    read() {
      if (body.reads >= chunks.length) return Promise.resolve({ done: true, value: undefined });
      return Promise.resolve({ done: false, value: chunks[body.reads++] });
    },
  });
  return {
    ok: true, status: 200, body,
    headers: { get(name) { return headers[name.toLowerCase()] || null; } },
  };
}

for (const filename of ['clip.mp4', 'document.pdf', 'notes.txt', 'archive.bin']) {
  test(filename + ' preview opens the relay streaming route in the shared viewer', () => {
    const e = env();
    e.ctx._fePreview(filename);
    assert(e.calls.length === 0, 'preview still read the file through a UI action');
    assert(e.opened.length === 1, 'viewer was not opened');
    const url = new URL(e.opened[0].url);
    assert(url.origin + url.pathname === 'https://webchat.example.org/api/fs/download',
      'preview did not use the streaming route: ' + e.opened[0].url);
    assert(url.searchParams.get('conversation_id') === 'law', 'preview lost its tile conversation');
    assert(url.searchParams.get('service') === 'relay one', 'wrong relay service');
    assert(url.searchParams.get('path') === 'media folder/' + filename, 'wrong file path');
    assert(url.searchParams.get('inline') === '1', 'preview asked for an attachment');
    assert(e.opened[0].name === filename, 'viewer lost the file name');
    assert(e.opened[0].blob === undefined, 'preview handed the viewer a page Blob');
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

test('blob preview bypasses the CSP-blocked fetch path', () => {
  const e = viewerEnv();
  const blob = { size: 7, type: 'image/png' };
  e.ctx.openFileViewer('blob:relay-preview', 'pawflow_art.png', blob);
  assert(e.fetches.length === 0, 'viewer fetched a local blob through connect-src');
  assert(e.elements.viewerDownload.href === 'blob:relay-preview',
    'viewer download did not reuse the blob URL');
  assert(e.elements.viewerContent.innerHTML.includes('<img src="blob:relay-preview"'),
    'viewer did not render the image Blob');
});

const STREAM_URL = 'https://webchat.example.org/api/fs/download?conversation_id=law'
  + '&service=relay+one&path=media+folder%2Fclip.mp4&inline=1';

test('relay video preview plays from the streaming URL without fetching it', () => {
  const e = viewerEnv();
  e.ctx.openFileViewer(STREAM_URL, 'clip.mp4');
  const html = e.elements.viewerContent.innerHTML;
  assert(e.fetches.length === 0, 'viewer downloaded the video before playing it');
  assert(html.includes('<video controls src="' + STREAM_URL.replace(/&/g, '&amp;') + '"'),
    'video element does not stream the relay URL: ' + html);
  assert(!html.includes('blob:'), 'viewer rebuilt the video as a Blob');
  assert(e.elements.viewerDownload.href === STREAM_URL, 'viewer download does not stream the file');
});

test('same-origin images, PDFs and HTML render from their URL', () => {
  const e = viewerEnv();
  e.ctx.openFileViewer('/files/fid/pawflow_art.png');
  assert(e.elements.viewerContent.innerHTML.includes(
    '<img src="https://webchat.example.org/files/fid/pawflow_art.png"'), 'image not rendered from its URL');
  e.ctx.openFileViewer('/files/fid/report.pdf');
  const pdf = e.elements.viewerContent.innerHTML;
  assert(pdf.includes('<iframe src="https://webchat.example.org/files/fid/report.pdf"'),
    'PDF not framed from its URL');
  assert(!pdf.includes('sandbox'), 'PDF frame is sandboxed');
  e.ctx.openFileViewer('/files/fid/page.html');
  assert(e.elements.viewerContent.innerHTML.includes('sandbox="allow-same-origin"'),
    'HTML preview lost its script sandbox');
  assert(e.fetches.length === 0, 'viewer fetched a file it renders from its URL');
});

test('a streamed preview that fails to load reports the error', () => {
  const e = viewerEnv();
  e.ctx.openFileViewer(STREAM_URL, 'clip.mp4');
  e.media.onerror();
  assert(e.elements.viewerContent.innerHTML.includes('Could not load file'),
    'media load failure left an empty player');
});

test('a quote in the file URL cannot break out of the src attribute', () => {
  const e = viewerEnv();
  e.ctx.openFileViewer('/files/fid/x" onerror="alert(1).png');
  const html = e.elements.viewerContent.innerHTML;
  assert(html.includes('<img src='), 'image preview not rendered');
  assert(!html.includes('" onerror="'), 'file URL injected an attribute: ' + html);
});

test('a foreign origin is never framed', () => {
  const e = viewerEnv();
  e.ctx.openFileViewer('https://evil.example/files/fid/report.pdf');
  assert(e.fetches.length === 1, 'foreign file skipped the connect-src guarded fetch');
  assert(!e.elements.viewerContent.innerHTML.includes('<iframe'), 'viewer framed a foreign page');
});

test('same-origin text preview keeps authenticated fetch options', async () => {
  const text = new TextEncoder().encode('hello <b>world</b>');
  const e = viewerEnv([streamResponse([text],
    { 'content-type': 'text/plain', 'content-length': String(text.length) })]);
  e.ctx.openFileViewer('/files/fid/notes.txt');
  assert(e.fetches.length === 1, 'viewer did not fetch the text file');
  assert(e.fetches[0].url === 'https://webchat.example.org/files/fid/notes.txt',
    'viewer did not resolve the same-origin file URL');
  assert(e.fetches[0].options.credentials === 'same-origin',
    'same-origin file fetch lost its credentials mode');
  assert(e.fetches[0].options.headers.Authorization === 'Bearer secret-token',
    'same-origin file fetch lost its bearer token');
  await settle();
  const html = e.elements.viewerContent.innerHTML;
  assert(html.includes('hello &lt;b&gt;world&lt;/b&gt;'), 'text preview not rendered: ' + html);
  assert(!html.includes('Preview shows the first'), 'a complete file was reported as truncated');
  assert(!e.fetches[0].options.signal.aborted, 'complete text preview aborted its transfer');
  assert(e.elements.viewerDownload.href === 'https://webchat.example.org/files/fid/notes.txt',
    'text preview download does not stream the file');
});

test('large text preview stops reading at the preview limit', async () => {
  const half = new Uint8Array(viewerEnv().limit / 2).fill(97);
  const response = streamResponse([half, half, half],
    { 'content-type': 'text/plain', 'content-length': String(half.length * 3) });
  const e = viewerEnv([response]);
  e.ctx.openFileViewer('/files/fid/huge.log');
  await settle();
  assert(response.body.reads === 2, 'viewer kept reading past the preview limit: ' + response.body.reads);
  assert(e.fetches[0].options.signal.aborted, 'viewer did not stop the text transfer');
  assert(e.elements.viewerContent.innerHTML.includes('Preview shows the first 4 MB'),
    'truncated text preview did not say so');
  assert(e.elements.viewerFileSize.textContent === (half.length * 3 / 1024).toFixed(1) + ' KB',
    'size label lost the file size');
});

test('binary preview does not download the body', async () => {
  const response = streamResponse([new Uint8Array(8)],
    { 'content-type': 'application/octet-stream', 'content-length': '5368709120' });
  const e = viewerEnv([response]);
  e.ctx.openFileViewer('/files/fid/archive.bin');
  await settle();
  assert(response.body.reads === 0, 'viewer downloaded a binary it cannot show');
  assert(e.fetches[0].options.signal.aborted, 'viewer did not stop the binary transfer');
  assert(e.elements.viewerContent.innerHTML.includes('Binary file'), 'binary notice missing');
  assert(e.elements.viewerFileSize.textContent === '5242880.0 KB', 'size label lost the file size');
  assert(e.elements.viewerDownload.href === 'https://webchat.example.org/files/fid/archive.bin',
    'binary preview download does not stream the file');
});

test('media found by its content type streams from its URL', async () => {
  const response = streamResponse([new Uint8Array(8)],
    { 'content-type': 'video/mp4', 'content-length': '8' });
  const e = viewerEnv([response]);
  e.ctx.openFileViewer('/files/fid/recording');
  await settle();
  assert(response.body.reads === 0, 'viewer downloaded the video body');
  assert(e.fetches[0].options.signal.aborted, 'viewer kept the probe transfer open');
  assert(e.elements.viewerContent.innerHTML.includes(
    '<video controls src="https://webchat.example.org/files/fid/recording"'), 'video not streamed from its URL');
});

test('opening another file stops the previous transfer', () => {
  const e = viewerEnv();
  e.ctx.openFileViewer('/files/fid/first.log');
  e.ctx.openFileViewer('/files/fid/second.log');
  assert(e.fetches[0].options.signal.aborted, 'previous preview kept streaming');
  assert(!e.fetches[1].options.signal.aborted, 'new preview was aborted');
});

test('closing the viewer stops the transfer and the player', () => {
  const e = viewerEnv();
  e.ctx.openFileViewer('/files/fid/first.log');
  e.ctx.closeFileViewer();
  assert(e.fetches[0].options.signal.aborted, 'closed preview kept streaming');
  e.ctx.openFileViewer(STREAM_URL, 'clip.mp4');
  e.ctx.closeFileViewer();
  assert(e.elements.fileViewer.style.display === 'none', 'viewer stayed open');
  assert(e.elements.viewerContent.innerHTML === '', 'closed viewer kept its video element');
});

test('a late response does not overwrite a newer preview', async () => {
  let resolveLate;
  const late = new Promise(resolve => { resolveLate = resolve; });
  const e = viewerEnv([late]);
  e.ctx.openFileViewer('/files/fid/slow.log');
  e.ctx.openFileViewer('/files/fid/pawflow_art.png');
  resolveLate(streamResponse([new TextEncoder().encode('stale')],
    { 'content-type': 'text/plain', 'content-length': '5' }));
  await settle();
  assert(e.elements.viewerContent.innerHTML.includes('<img src='),
    'stale text replaced the newer image preview');
});

Promise.all(pending).then(() => {
  if (failures.length) {
    console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
    for (const failure of failures) console.error('  - ' + failure);
    process.exit(1);
  }
  console.log(passed + ' passing');
});
