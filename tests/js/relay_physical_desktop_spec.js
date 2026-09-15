'use strict';

// Exercise the actual Electron IPC handlers with process creation stubbed.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'pawflow-relay-desktop/src/main.js'), 'utf8');

function harness(failAt = '', externalRunning = false, backendStopsProcess = false) {
  const handlers = new Map();
  const calls = [];
  const processes = [];
  const electron = {
    BrowserWindow: { getAllWindows: () => [] },
    app: { isPackaged: false, whenReady: () => ({ then() {} }), on() {} },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
  };
  const physical = {
    name: 'Laptop', physical_id: 'ExistingPhysical', running: externalRunning,
    workspaces: [{ name: 'Code', relay_id: 'ExistingCode' }],
  };
  const context = vm.createContext({
    require: name => {
      if (name === 'electron') return electron;
      if (name === 'child_process') return { spawn: (_command, args) => {
        calls.push('start:' + args.at(-1));
        const proc = new EventEmitter();
        proc.stdout = new EventEmitter();
        proc.stderr = new EventEmitter();
        proc.exitCode = null;
        proc.signalCode = null;
        proc.kill = signal => {
          calls.push('kill:' + signal);
          proc.exitCode = 0;
          proc.emit('close', 0);
          return true;
        };
        processes.push(proc);
        return proc;
      } };
      return require(name);
    },
    __dirname: path.join(root, 'pawflow-relay-desktop/src'),
    process: { platform: 'linux', env: {} },
    console, setTimeout, clearTimeout,
    fixture: { physicals: [physical], servers: [], workspaces: physical.workspaces },
    cli: async (args, stdin) => {
      const operation = args.includes('--validate-only') ? 'validate' : args[0] === 'cleanup' ? 'cleanup' : 'save';
      calls.push(operation);
      if (failAt === 'cleanup-once' && operation === 'cleanup' && calls.filter(c => c === 'cleanup').length === 1) {
        physical.running = false;
        physical.cleanup_pending = true;
        throw new Error('cleanup failed');
      }
      if (failAt === operation) throw new Error(operation + ' failed');
      if (operation !== 'cleanup') {
        const config = JSON.parse(stdin);
        assert.equal(config.physical_id, 'ExistingPhysical');
        assert.equal(config.workspaces[0].relay_id, 'ExistingCode');
        assert.equal(config.workspaces.length, 2);
        if (operation === 'save') physical.name = args[2];
      }
      if (operation === 'cleanup') {
        physical.running = false;
        physical.cleanup_pending = false;
        if (backendStopsProcess) {
          for (const proc of processes) {
            assert.equal(proc.exitCode, null, 'Desktop killed the child before backend cleanup');
            proc.exitCode = 0;
            proc.emit('close', 0);
          }
        }
      }
      return { ok: true, already_stopped: false };
    },
  });
  vm.runInContext(source, context, { filename: 'main.js' });
  vm.runInContext(`
    getRelayState = async () => fixture;
    relayClientCommand = args => ({ command: 'fake-relay', args, env: {}, cwd: '.' });
    runRelayClientJson = cli;
  `, context);
  return {
    calls, processes, physical,
    invoke: (channel, input) => handlers.get('relay:' + channel)(null, input),
    config: {
      name: 'Laptop', physicalId: 'ExistingPhysical', server: 'Server', dockerImage: 'relay:test',
      workspaces: [
        { name: 'Code', relay_id: 'ExistingCode', path: '/code', mode: 'ro' },
        { name: 'Docs', path: '/docs', mode: 'rw' },
      ],
    },
  };
}

test('logical relays cannot be started independently', async () => {
  const h = harness();
  await assert.rejects(h.invoke('start', 'Code'), /physical relay/);
  assert.equal(h.processes.length, 0);
});

test('overlapping connect requests create one physical process', async () => {
  const h = harness();
  await Promise.all([h.invoke('start', 'Laptop'), h.invoke('start', 'Laptop')]);
  assert.equal(h.processes.length, 1);
  assert.deepEqual(Array.from(await h.invoke('running')), ['Laptop']);
});

test('invalid configuration leaves the existing process running', async () => {
  const h = harness('validate');
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await assert.rejects(h.invoke('save-physical', h.config), /validate failed/);
  assert.deepEqual(h.calls, ['validate']);
  assert.equal(h.processes[0].exitCode, null);
});

test('saving a running group validates, stops, saves and starts the parent', async () => {
  const h = harness();
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await h.invoke('save-physical', h.config);
  assert.deepEqual(h.calls, ['validate', 'cleanup', 'kill:SIGINT', 'cleanup', 'save', 'start:Laptop']);
  assert.equal(h.processes.length, 2);
  assert.equal(h.processes[0].exitCode, 0);
});

test('failed runtime cleanup prevents saving and restarting', async () => {
  const h = harness('cleanup');
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await assert.rejects(h.invoke('save-physical', h.config), /cleanup failed/);
  assert.deepEqual(h.calls, ['validate', 'cleanup']);
  assert.equal(h.processes.length, 1);
});

test('failed persistence restarts the original saved group', async () => {
  const h = harness('save');
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await assert.rejects(h.invoke('save-physical', h.config), /save failed/);
  assert.deepEqual(h.calls, ['validate', 'cleanup', 'kill:SIGINT', 'cleanup', 'save', 'start:Laptop']);
});

test('saving a stopped group does not implicitly connect it', async () => {
  const h = harness();
  await h.invoke('save-physical', h.config);
  assert.deepEqual(h.calls, ['validate', 'save']);
  assert.equal(h.processes.length, 0);
});

test('external CLI runtimes are visible and connect does not duplicate them', async () => {
  const h = harness('', true);
  assert.deepEqual(Array.from(await h.invoke('running')), ['Laptop']);
  await h.invoke('start', 'Laptop');
  assert.equal(h.processes.length, 0);
});

test('saving an external CLI runtime disconnects and restarts the whole parent', async () => {
  const h = harness('', true);
  await h.invoke('save-physical', h.config);
  assert.deepEqual(h.calls, ['validate', 'cleanup', 'save', 'start:Laptop']);
});

test('disconnecting an idle parent performs no cleanup', async () => {
  const h = harness();
  const result = await h.invoke('stop', 'Laptop');
  assert.equal(result.alreadyStopped, true);
  assert.deepEqual(h.calls, []);
});

test('renaming a running parent stops its old name and starts its new name', async () => {
  const h = harness();
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await h.invoke('save-physical', { ...h.config, name: 'Renamed' });
  assert.deepEqual(h.calls, ['validate', 'cleanup', 'kill:SIGINT', 'cleanup', 'save', 'start:Renamed']);
  assert.deepEqual(Array.from(await h.invoke('running')), ['Renamed']);
});

test('failed rename restarts the saved parent under its old name', async () => {
  const h = harness('save');
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await assert.rejects(h.invoke('save-physical', { ...h.config, name: 'Renamed' }), /save failed/);
  assert.deepEqual(h.calls, ['validate', 'cleanup', 'kill:SIGINT', 'cleanup', 'save', 'start:Laptop']);
});

test('backend observes the live child before graceful stop removes its runtime lock', async () => {
  const h = harness('', false, true);
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await h.invoke('stop', 'Laptop');
  assert.deepEqual(h.calls, ['cleanup']);
  assert.equal(h.processes[0].exitCode, 0);
});

test('a failed cleanup can be retried after the launcher has exited', async () => {
  const h = harness('cleanup-once', true);
  await assert.rejects(h.invoke('stop', 'Laptop'), /cleanup failed/);
  assert.deepEqual(Array.from(await h.invoke('running')), []);
  assert.equal(h.physical.cleanup_pending, true);
  await h.invoke('stop', 'Laptop');
  assert.deepEqual(h.calls, ['cleanup', 'cleanup']);
  assert.equal(h.physical.cleanup_pending, false);
});

test('connecting settles pending cleanup before starting a new launcher', async () => {
  const h = harness();
  h.physical.cleanup_pending = true;
  await h.invoke('start', 'Laptop');
  assert.deepEqual(h.calls, ['cleanup', 'start:Laptop']);
});

test('saving a parent with pending cleanup settles it and keeps the parent stopped', async () => {
  const h = harness();
  h.physical.cleanup_pending = true;
  await h.invoke('save-physical', h.config);
  assert.deepEqual(h.calls, ['validate', 'cleanup', 'save']);
  assert.equal(h.processes.length, 0);
});

test('overlapping connects with pending cleanup create one launcher', async () => {
  const h = harness();
  h.physical.cleanup_pending = true;
  await Promise.all([h.invoke('start', 'Laptop'), h.invoke('start', 'Laptop')]);
  assert.equal(h.processes.length, 1);
  assert.equal(h.calls.filter(call => call === 'cleanup').length, 1);
});
