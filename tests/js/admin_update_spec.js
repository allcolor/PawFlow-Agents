// Behavioural tests for the admin panel's "wait for the server to restart"
// loop, run under Node against a hand-rolled stub.
//
// The loop decides when the update is over and reloads the page. Asserting
// that '/health' appears in the source says nothing about that decision: the
// version that shipped reloaded on the first successful poll, so an updater
// that died before stopping anything looked exactly like a finished update --
// the page reloaded at once, onto the version it started from.
//
// Run directly: node tests/js/admin_update_spec.js
// Run via pytest: tests/test_admin_update_js.py

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CHAT_UI = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui');

let passed = 0;
const failures = [];

function test(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { passed++; })
    .catch(err => { failures.push(name + ': ' + (err && err.message ? err.message : err)); });
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }

// A document with exactly what the wait loop touches.
function fakeDocument() {
  const nodes = {};
  return {
    addEventListener() {},
    getElementById(id) {
      if (!nodes[id]) nodes[id] = { textContent: '', style: {}, classList: { toggle() {} } };
      return nodes[id];
    },
    // _adminOverlay appends a real .exec-overlay in production; this stub
    // reports the overlay as present (the wait loop keeps polling until the
    // server restarts or the dialog is dismissed).
    querySelectorAll() { return [{ _stubOverlay: true, remove() {} }]; },
    _nodes: nodes,
  };
}

// `health` is called per poll and returns either a body or null for "down".
function env(health) {
  const state = {
    overlays: [],
    reloads: 0,
    polls: 0,
    interval: null,
    now: 0,
  };
  const ctx = {
    console,
    document: fakeDocument(),
    location: { reload() { state.reloads++; } },
    Date: { now: () => state.now },
    setTimeout: fn => { fn(); return 0; },
    clearTimeout() {},
    setInterval: fn => { state.interval = fn; return 1; },
    clearInterval: () => { state.interval = null; },
    fetch: () => {
      state.polls++;
      const body = health(state.polls);
      if (body === null) return Promise.reject(new Error('connection refused'));
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    },
    addMsg() {},
    action$: () => ({ subscribe() {} }),
    _isAdmin: () => true,
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(CHAT_UI, 'admin_settings.js'), 'utf8'),
                  ctx, { filename: 'admin_settings.js' });
  // Replaces the real overlay, which wants a DOM this stub does not have. Set
  // after the load: the file declares the function, and a declaration wins over
  // anything seeded into the context beforehand.
  ctx._adminOverlay = (title, body) => { state.overlays.push(String(body)); };
  state.ctx = ctx;
  // One poll plus enough microtask turns for fetch -> json -> decision.
  state.tick = async (seconds) => {
    state.now += (seconds || 2) * 1000;
    state.interval && state.interval();
    for (let i = 0; i < 8; i++) await Promise.resolve();
  };
  return state;
}

const RUNNING = { ok: true, version: '1.0.0b47', instance: 'aaaa' };
const RESTARTED = { ok: true, version: '1.0.0b48', instance: 'bbbb' };

async function main() {
  await test('a server that never went down is never mistaken for a restart', async () => {
    const s = env(() => RUNNING);
    s.ctx._admWaitForServer({ container: 'pawflow-updater' }, RUNNING);
    for (let i = 0; i < 5; i++) await s.tick();
    assert(s.polls === 5, 'expected to keep polling, got ' + s.polls);
    assert(s.reloads === 0, 'reloaded onto the version it started from');
  });

  await test('a different process answering ends the wait and reloads', async () => {
    const s = env(n => (n < 3 ? null : RESTARTED));
    s.ctx._admWaitForServer({ container: 'pawflow-updater' }, RUNNING);
    for (let i = 0; i < 4; i++) await s.tick();
    assert(s.reloads === 1, 'expected exactly one reload, got ' + s.reloads);
    assert(s.interval === null, 'the poll was left running');
  });

  await test('an update that never touched the server is reported, not hidden', async () => {
    const s = env(() => RUNNING);
    s.ctx._admWaitForServer({ container: 'pawflow-updater' }, RUNNING);
    await s.tick(s.ctx.ADM_UPDATE_TIMEOUT_S + 2);
    const last = s.overlays[s.overlays.length - 1];
    assert(/did not restart/.test(last), 'the panel did not say the update failed');
    assert(/never stopped answering/.test(last), 'it did not distinguish the two failures');
    assert(/docker logs pawflow-updater/.test(last), 'no way to find out why');
    assert(s.reloads === 0, 'reloaded anyway');
  });

  await test('a server that went down and stayed down is reported differently', async () => {
    const s = env(() => null);
    s.ctx._admWaitForServer({ container: 'pawflow-updater' }, RUNNING);
    await s.tick();
    await s.tick(s.ctx.ADM_UPDATE_TIMEOUT_S + 2);
    const last = s.overlays[s.overlays.length - 1];
    assert(/never came back/.test(last), 'a dead new container reads as an untouched one');
  });

  await test('CLI image progress reaches the restart wait state', async () => {
    const s = env(() => RUNNING);
    s.ctx._admImageBuildBefore = RUNNING;
    s.ctx.adminBuildProgress({ status: 'started', forced: false });
    assert(/Rebuild in progress/.test(s.ctx.document._nodes['adm-image-stage'].innerHTML),
           'build stage was not rendered');
    s.ctx.adminBuildProgress({ status: 'built', output: 'image ready' });
    assert(/Restarting PawFlow/.test(s.ctx.document._nodes['adm-image-stage'].innerHTML),
           'restart stage was not rendered');
    let wait = null;
    s.ctx._admWaitForServer = (...args) => { wait = args; };
    s.ctx.adminBuildProgress({ status: 'restarting', container: 'pawflow-restarter' });
    assert(wait && wait[1].instance === RUNNING.instance,
           'restart polling did not keep the original server instance');
  });

  await test('relay workflow reports each failed recreation', async () => {
    const s = env(() => RUNNING);
    s.ctx.adminRelayBuildProgress({
      status: 'error',
      error: 'One or more relays could not be recreated',
      failed: [{ kind: 'workspace', conv_id: 'conv-1', error: 'boom' }],
    });
    const log = s.ctx.document._nodes['adm-image-progress'].textContent;
    assert(/conv-1: boom/.test(log), 'failed relay details were hidden');
  });

  console.log(passed + ' passing');
  if (failures.length) {
    console.error(failures.length + ' failing');
    failures.forEach(f => console.error('  - ' + f));
    process.exit(1);
  }
}

main();
