// Delayed mutation responses must never drive a reopened explorer.
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.join(
  __dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'file_explorer.js'), 'utf8');
function env() {
  const requests = [];
  const ctx = {
    conversationId: 'tileA',
    confirm: () => true, t: key => key, addMsg() {},
    document: {
      addEventListener() {}, removeEventListener() {},
      querySelector: () => ({ appendChild() {} }),
      createElement: () => ({
        dataset: {}, remove() {},
        querySelector: () => ({
          addEventListener() {}, classList: { add() {}, remove() {} },
        }),
      }),
    },
    action$(action, payload) {
      return { subscribe(callback) { requests.push({ action, payload, callback }); } };
    },
  };
  vm.createContext(ctx);
  vm.runInContext(source.slice(0, source.indexOf("\naddMsg('system'")), ctx);
  ctx._feLoadSvcs = () => {};
  ctx._feNav = () => {};
  ctx._feStatus = () => {};
  ctx._feRender = () => {};
  const run = code => vm.runInContext(code, ctx);
  ctx.openExplorer();
  run("_fe.svc='relayA';_fe.path='dirA';_fe.sel=new Set(['first.txt','second.txt']);");
  const reopen = () => {
    ctx.closeExplorer();
    ctx.conversationId = 'tileB';
    ctx.openExplorer();
    run("_fe.svc='relayB';_fe.path='dirB';");
  };
  return { ctx, requests, run, reopen };
}
let failed = 0;
function test(name, fn) {
  try { fn(); console.log('PASS ' + name); }
  catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.message); }
}
test('delete batch stops when its surface closes and reopens in B', () => {
  const e = env();
  e.ctx._feDelSelected();
  assert.equal(e.requests.length, 1);
  assert.equal(e.requests[0].payload.conversation_id, 'tileA');
  e.reopen();
  e.requests[0].callback({ ok: true });
  assert.equal(e.requests.length, 1, 'pending delete issued a request in the reopened tile');
});
test('delete batch retains original service and paths while tile stays open', () => {
  const e = env();
  e.ctx._feDelSelected();
  e.run("_fe.svc='relayB';_fe.path='dirB';");
  e.requests[0].callback({ ok: true });
  assert.equal(e.requests.length, 2);
  assert.equal(e.requests[1].payload.conversation_id, 'tileA');
  assert.equal(e.requests[1].payload.service, 'relayA');
  assert.equal(e.requests[1].payload.path, 'dirA/second.txt');
});
for (const reopen of [false, true]) {
  test('cut completion stops after closing, reopen=' + reopen, () => {
    const e = env();
    e.ctx._feCutSelected();
    e.run("_fe.path='destA';");
    e.ctx._fePaste();
    if (reopen) {
      e.reopen();
      e.run("_fe.sel=new Set(['new.txt']);");
      e.ctx._feCutSelected();
    } else e.ctx.closeExplorer();
    e.requests[0].callback({ ok: true });
    assert.equal(e.requests.length, 1, 'old copy completion deleted from another tile');
  });
}
test('cut batch retains source, destination and clipboard through navigation', () => {
  const e = env();
  e.ctx._feCutSelected();
  e.run("_fe.path='destA';");
  e.ctx._fePaste();
  e.run("_fe.svc='relayB';_fe.path='dirB';_fe.sel=new Set(['new.txt']);");
  e.ctx._feCutSelected();
  e.requests[0].callback({ ok: true });
  const remove = e.requests[1];
  assert.equal(remove.action, 'fs_delete');
  assert.equal(remove.payload.conversation_id, 'tileA');
  assert.equal(remove.payload.service, 'relayA');
  assert.equal(remove.payload.path, 'dirA/first.txt');
  remove.callback({ ok: true });
  const second = e.requests[2];
  assert.equal(second.action, 'fs_copy');
  assert.equal(second.payload.conversation_id, 'tileA');
  assert.equal(second.payload.source_service, 'relayA');
  assert.equal(second.payload.source_path, 'dirA/second.txt');
  assert.equal(second.payload.dest_service, 'relayA');
  assert.equal(second.payload.dest_path, 'destA/second.txt');
  second.callback({ ok: true });
  e.requests[3].callback({ ok: true });
  assert.equal(e.run('_fe.clip.service'), 'relayB', 'completion erased newer clipboard');
});
test('failed cut copy never deletes the source', () => {
  const e = env();
  e.ctx._feCutSelected();
  e.ctx._fePaste();
  e.requests[0].callback({ error: 'copy failed' });
  assert.equal(e.requests.length, 1);
});
test('cut deletion completion cannot start another copy after reopen', () => {
  const e = env();
  e.ctx._feCutSelected();
  e.ctx._fePaste();
  e.requests[0].callback({ ok: true });
  assert.equal(e.requests.length, 2);
  e.reopen();
  e.requests[1].callback({ ok: true });
  assert.equal(e.requests.length, 2);
});
test('failed source deletion stops a cut batch and preserves its clipboard', () => {
  const e = env();
  e.ctx._feCutSelected();
  e.ctx._fePaste();
  e.requests[0].callback({ ok: true });
  e.requests[1].callback({ error: 'delete failed' });
  assert.equal(e.requests.length, 2);
  assert.equal(e.run('_fe.clip.items.length'), 2);
});
test('failed deletion stops the remaining delete batch', () => {
  const e = env();
  e.ctx._feDelSelected();
  e.requests[0].callback({ error: 'delete failed' });
  assert.equal(e.requests.length, 1);
});
process.exitCode = failed ? 1 : 0;
