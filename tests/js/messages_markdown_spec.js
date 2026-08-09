// Behavioural tests for FileStore links rendered by messages_markdown.js.
// Run directly: node tests/js/messages_markdown_spec.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'messages_markdown.js');

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
  const messages = { addEventListener() {} };
  const ctx = {
    console,
    setTimeout() {},
    hasMoreMessages: false,
    loadingMore: false,
    document: {
      getElementById(id) { return id === 'messages' ? messages : null; },
    },
    window: {
      addEventListener() {},
      requestAnimationFrame(fn) { fn(); },
    },
    t: key => key,
    escapeHtml: value => String(value === null || value === undefined ? '' : value)
      .replace(/[&<>"']/g, char => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])),
    normalizePawFlowFileUrl: url => String(url || '').replace(
      /^https?:\/\/[^/]+(\/files\/[a-f0-9]+\/[^\s<"'`]+)$/i, '$1'),
    isImageFile: name => /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(name || ''),
    isAudioFile: name => /\.(mp3|wav|ogg|m4a|aac|flac)$/i.test(name || ''),
    isVideoFile: name => /\.(mp4|webm|mov|m4v)$/i.test(name || ''),
    inlineImageHtml: (url, name) => '<img src="' + url + '" alt="' + name + '">',
    inlineAudioHtml: (url, name) => '<audio src="' + url + '" data-name="' + name + '"></audio>',
    inlineVideoHtml: (url, name) => '<video src="' + url + '" data-name="' + name + '"></video>',
    openFileViewer() {},
    fetchFsFile() {},
    updateScrollNav() {},
    loadMoreMessages() {},
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), ctx, {
    filename: 'messages_markdown.js',
  });
  return ctx;
}

test('Markdown FileStore document link resolves to the HTTP file route', () => {
  const html = env().renderMarkdown(
    '[rapport](fs://filestore/abc123def456/report.pdf)');
  assert(html.includes('data-file-url="/files/abc123def456/report.pdf"'), html);
  assert(!html.includes('report.pdf)'), html);
});

test('Markdown FileStore video link renders an inline player', () => {
  const html = env().renderMarkdown(
    '[demo.mp4](fs://filestore/abc123def456/demo.mp4)');
  assert(html.includes('<video src="/files/abc123def456/demo.mp4"'), html);
  assert(!html.includes('demo.mp4)'), html);
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  for (const failure of failures) console.error('  - ' + failure);
  process.exit(1);
}
console.log(passed + ' passing');
