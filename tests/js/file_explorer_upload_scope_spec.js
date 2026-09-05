// Exercise real explorer upload and XHR query construction across focus changes.
const assert = require('assert/strict');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const root = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui');
const attachments = fs.readFileSync(path.join(root, 'attachments.js'), 'utf8');
const explorer = fs.readFileSync(path.join(root, 'file_explorer.js'), 'utf8');
const requests = [];
class XHR {
  constructor() { this.upload = {}; }
  open(method, url) { this.url = url; }
  setRequestHeader() {}
  send(file) { this.file = file; requests.push(this); }
  complete() {
    this.status = 200;
    this.responseText = JSON.stringify({ ok: true, files: [{ filename: this.file.name }] });
    this.onload();
  }
}
const ctx = {
  XMLHttpRequest: XHR, URLSearchParams,
  getAuthHeaders: () => ({}), t: key => key,
  conversationId: 'law',
  _fe: { surface: { dataset: { conversationId: 'law' } },
    svc: 'permisWS', path: 'plans' },
  _fePath(name) { return ctx._fe.path + '/' + name; },
  _feStatus() {}, _feNav() {}, addMsg() {},
};
vm.createContext(ctx);
vm.runInContext(
  attachments.slice(attachments.indexOf('function _uploadRawFile('),
    attachments.indexOf('\nfunction handleFiles(')) +
  explorer.slice(explorer.indexOf('async function _feUploadFiles('),
    explorer.indexOf('\nfunction _feCopyToStore(')), ctx);
async function main() {
  const pending = ctx._feUploadFiles([{ name: 'a.txt' }, { name: 'b.txt' }]);
  assert.equal(requests.length, 1);
  ctx.conversationId = 'other';
  ctx._fe.svc = 'other-relay';
  ctx._fe.path = 'other-directory';
  requests[0].complete();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 2);
  requests[1].complete();
  await pending;
  for (const [index, request] of requests.entries()) {
    const query = new URLSearchParams(request.url.split('?')[1]);
    assert.equal(query.get('conversation_id'), 'law', 'upload lost its tile conversation');
    assert.equal(query.get('service'), 'permisWS', 'batch changed relay while awaiting upload');
    assert.equal(query.get('path'), 'plans/' + ['a.txt', 'b.txt'][index]);
  }
  console.log('Explorer upload retains its tile conversation and batch destination');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
