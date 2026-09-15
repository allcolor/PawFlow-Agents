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

function harness(failAt = '') {
  const handlers = new Map();
  const calls = [];
  const processes = [];
  const electron = {
    BrowserWindow: { getAllWindows: () => [] },
    app: { isPackaged: false, whenReady: () => ({ then() {} }), on() {} },
    ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
  };
  const physical = { name: 'Laptop', workspaces: [{ name: 'Code', relay_id: 'ExistingCode' }] };
  const context = vm.createContext({
    require: name => {
      if (name === 'electron') return electron;
      if (name === 'child_process') return { spawn: (_command, args) => {
        calls.push('start:' + args.at(-1));
        const proc = new EventEmitter();
        proc.stdout = new EventEmitter();
        proc.stderr = new EventEmitter();
        proc.exitCode = null;
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
      if (failAt === operation) throw new Error(operation + ' failed');
      if (operation !== 'cleanup') {
        const config = JSON.parse(stdin);
        assert.equal(config.workspaces[0].relay_id, 'ExistingCode');
        assert.equal(config.workspaces.length, 2);
      }
      return { ok: true };
    },
  });
  vm.runInContext(source, context, { filename: 'main.js' });
  vm.runInContext(`
    getRelayState = async () => fixture;
    relayClientCommand = args => ({ command: 'fake-relay', args, env: {}, cwd: '.' });
    runRelayClientJson = cli;
  `, context);
  return {
    calls, processes,
    invoke: (channel, input) => handlers.get('relay:' + channel)(null, input),
    config: {
      name: 'Laptop', server: 'Server', dockerImage: 'relay:test',
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
  assert.deepEqual(h.calls, ['validate', 'kill:SIGINT', 'cleanup', 'save', 'start:Laptop']);
  assert.equal(h.processes.length, 2);
  assert.equal(h.processes[0].exitCode, 0);
});

test('failed runtime cleanup prevents saving and restarting', async () => {
  const h = harness('cleanup');
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await assert.rejects(h.invoke('save-physical', h.config), /cleanup failed/);
  assert.deepEqual(h.calls, ['validate', 'kill:SIGINT', 'cleanup']);
  assert.equal(h.processes.length, 1);
});

test('failed persistence restarts the original saved group', async () => {
  const h = harness('save');
  await h.invoke('start', 'Laptop');
  h.calls.length = 0;
  await assert.rejects(h.invoke('save-physical', h.config), /save failed/);
  assert.deepEqual(h.calls, ['validate', 'kill:SIGINT', 'cleanup', 'save', 'start:Laptop']);
});

test('saving a stopped group does not implicitly connect it', async () => {
  const h = harness();
  await h.invoke('save-physical', h.config);
  assert.deepEqual(h.calls, ['validate', 'save']);
  assert.equal(h.processes.length, 0);
});
