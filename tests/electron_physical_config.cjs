'use strict';

// Run with Electron on a disposable CI runner. Configuration and all application
// data live under the required output directory; no relay runtime is started.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

if (process.env.PAWFLOW_DISPOSABLE_ACCEPTANCE !== '1') {
  throw new Error('This probe requires a disposable acceptance environment');
}
const output = process.env.PAWFLOW_DESKTOP_ACCEPTANCE_DIR;
const phase = process.env.PAWFLOW_DESKTOP_ACCEPTANCE_PHASE;
if (!output || !path.isAbsolute(output) || !['edit', 'reopen'].includes(phase)) {
  throw new Error('An absolute acceptance directory and edit/reopen phase are required');
}
const config = path.join(output, 'config');
const runtime = path.join(output, 'empty-runtime');
const workspaceFile = path.join(config, 'workspaces.json');
const legacy = {
  name: 'Legacy share', relay_id: 'fs_fixture_legacy', server: 'Fixture',
  path: path.join(output, 'legacy directory'), docker_image: 'relay:fixture',
  mode: 'ro', allow_exec: false, allow_local: true,
  allow_remote_desktop: false, allow_service_tunnels: true,
  created_at: '2025-01-01T00:00:00Z', updated_at: '2025-02-01T00:00:00Z',
};
const readConfig = () => JSON.parse(fs.readFileSync(workspaceFile, 'utf8'));
const python = args => JSON.parse(execFileSync(process.env.PAWFLOW_RELAY_PYTHON, args, {
  cwd: path.resolve(__dirname, '..'), env: process.env, encoding: 'utf8', timeout: 15000,
}));
const homeVolumes = () => python(['-c',
  "import json; from pawflow_relay.physical_config import get_physical; "
  + "from pawflow_relay.physical_plan import plan_physical_relay; "
  + "p = get_physical('Legacy share'); "
  + "print(json.dumps({e.relay_id: e.home_volume for e in "
  + "plan_physical_relay(p['physical_id'], p['workspaces']).exports}))"]);
if (phase === 'edit') {
  fs.mkdirSync(config, { recursive: true });
  assert.equal(fs.existsSync(workspaceFile), false, 'The fixture must be fresh');
  fs.mkdirSync(runtime, { recursive: true });
  fs.mkdirSync(legacy.path);
  fs.mkdirSync(path.join(output, 'second directory'));
  fs.writeFileSync(path.join(legacy.path, 'sentinel'), 'preserve existing workspace');
  fs.writeFileSync(path.join(config, 'servers.json'), JSON.stringify({
    Fixture: { name: 'Fixture', url: 'https://fixture.invalid' },
  }));
  fs.writeFileSync(workspaceFile, JSON.stringify({ [legacy.name]: legacy }));
}
process.env.PAWFLOW_RELAY_HOME = config;
process.env.PAWFLOW_RELAY_RUNTIME_ROOT = runtime;
process.env.PAWFLOW_RELAY_BIN = path.join(runtime, 'missing-relay');
process.env.PAWFLOW_RELAY_DOCKER = path.join(runtime, 'missing-docker');
process.env.PYTHON_KEYRING_BACKEND = 'tests.relay_config_keyring.EmptyKeyring';
for (const item of ['userData', 'sessionData', 'crashDumps']) {
  const directory = path.join(output, item);
  fs.mkdirSync(directory, { recursive: true });
  app.setPath(item, directory);
}
app.disableHardwareAcceleration();
require('../pawflow-relay-desktop/src/main.js');

let window;
const timer = setTimeout(() => {
  console.error('Electron configuration acceptance timed out');
  app.exit(1);
}, 90000);
const run = (fn, arg) => window.webContents.executeJavaScript(
  '(' + fn.toString() + ')(' + JSON.stringify(arg) + ')', true);
const screenshot = async name => {
  const captured = await window.webContents.capturePage();
  fs.writeFileSync(path.join(output, name + '.png'), captured.toPNG());
};

async function main() {
  await app.whenReady();
  window = BrowserWindow.getAllWindows()[0];
  assert.ok(window, 'The production main process must create a window');
  if (window.webContents.isLoadingMainFrame()) {
    await new Promise(resolve => window.webContents.once('did-finish-load', resolve));
  }
  await run(async () => {
    const check = (value, message) => { if (!value) throw new Error(message); };
    const until = async predicate => {
      const deadline = Date.now() + 15000;
      while (!predicate()) {
        if (Date.now() > deadline) throw new Error('UI timeout: ' + document.querySelector('#toast').textContent);
        await new Promise(resolve => setTimeout(resolve, 25));
      }
    };
    const clickName = name => {
      const button = [...document.querySelectorAll('#workspaceTree button')]
        .find(item => item.querySelector('.name').textContent === name);
      check(button, 'Missing tree item ' + name);
      button.click();
    };
    const save = async (count, invalid = false) => {
      const form = document.querySelector('#workspaceForm');
      document.querySelector('#toast').textContent = '';
      form.requestSubmit();
      await until(() => document.querySelector('#toast').textContent.includes(
        invalid ? 'not an existing directory' : 'Saved physical relay'));
      if (invalid) {
        await until(() => !form.querySelector('[type="submit"]').disabled);
        check(form === document.querySelector('#workspaceForm'), 'Rejected save must retain the form');
      } else {
        await until(() => form !== document.querySelector('#workspaceForm')
          && document.querySelectorAll('.logical-config').length === count);
      }
    };
    window.acceptance = { check, until, clickName, save };
    await until(() => document.querySelectorAll('#workspaceTree .logical-relay').length > 0);
    clickName('fs_fixture_legacy');
    check(!document.querySelector('#startRelayBtn'), 'Logical relays must not expose connect controls');
    document.querySelector('#configurePhysicalBtn').click();
    check(document.querySelector('#panelTitle').textContent === 'Legacy share', 'Missing physical form');
  });

  if (phase === 'edit') {
    const migrated = readConfig()[legacy.name];
    for (const [key, value] of Object.entries(legacy)) assert.equal(migrated[key], value, key);
    assert.deepEqual(migrated, legacy, 'Reading the legacy group must not write migration');
    await run(async directory => {
      const { check, save } = window.acceptance;
      const original = document.querySelector('.logical-config');
      check(original.querySelector('[name="relay_id"]').value === 'fs_fixture_legacy', 'Identity changed');
      check(original.querySelector('[name="name"]').readOnly, 'Existing identity must be readonly');
      document.querySelector('#addLogicalBtn').click();
      const row = document.querySelectorAll('.logical-config')[1];
      row.querySelector('[name="name"]').value = 'PublishedDocs';
      row.querySelector('[name="path"]').value = directory;
      row.querySelector('[name="mode"]').value = 'ro';
      row.querySelector('[name="allowExec"]').checked = false;
      row.querySelector('[name="allowRemoteDesktop"]').checked = false;
      await save(2);
    }, path.join(output, 'second directory'));
    const saved = readConfig();
    for (const key of Object.keys(legacy).filter(key => key !== 'updated_at')) {
      assert.equal(saved[legacy.name][key], legacy[key], key);
    }
    assert.equal(saved.PublishedDocs.relay_id, 'PublishedDocs');
    assert.equal(saved.PublishedDocs.physical_id, legacy.relay_id);
    assert.equal(saved[legacy.name].physical_id, legacy.relay_id);
    assert.equal(saved.PublishedDocs.allow_exec, false);
    assert.equal(saved.PublishedDocs.allow_remote_desktop, false);
    assert.equal(saved.PublishedDocs.mode, 'ro');
    python(['-m', 'pawflow_relay', '--json', 'physical', 'save', legacy.name,
      '--server', legacy.server, '--docker-image', legacy.docker_image,
      '--workspace', legacy.name, legacy.path,
      '--workspace', 'PublishedDocs', path.join(output, 'second directory')]);
    assert.equal(readConfig()[legacy.name].mode, 'ro');
    assert.equal(readConfig().PublishedDocs.mode, 'ro');
    const beforeInvalid = fs.readFileSync(workspaceFile, 'utf8');
    await run(async invalidPath => {
      const { check, save } = window.acceptance;
      document.querySelector('.logical-config [name="path"]').value = invalidPath;
      await save(2, true);
      check(document.querySelector('.logical-config [name="path"]').value === invalidPath,
        'Rejected input must remain available for correction');
    }, path.join(output, 'missing-directory'));
    assert.equal(fs.readFileSync(workspaceFile, 'utf8'), beforeInvalid);
    await screenshot('edit-invalid-retained');
  } else {
    const saved = readConfig();
    assert.equal(Object.keys(saved).length, 2);
    assert.equal(saved[legacy.name].relay_id, legacy.relay_id);
    assert.equal(saved.PublishedDocs.relay_id, 'PublishedDocs');
    assert.equal(saved.PublishedDocs.mode, 'ro');
    const previous = JSON.parse(fs.readFileSync(path.join(output, 'edit-result.json'), 'utf8'));
    assert.deepEqual(homeVolumes(), previous.home_volumes);
    await run(async () => {
      const { check, save } = window.acceptance;
      check(document.querySelectorAll('.logical-config').length === 2, 'Second process lost persisted group');
      const rows = [...document.querySelectorAll('.logical-config')];
      const docs = rows.find(row => row.querySelector('[name="name"]').value === 'PublishedDocs');
      check(!docs.querySelector('[name="allowExec"]').checked, 'Permission changed after reopening');
      docs.querySelector('.remove-logical').click();
      await save(1);
      document.querySelector('.remove-logical').click();
      check(document.querySelectorAll('.logical-config').length === 1, 'The last directory was removable');
    });
    const remaining = readConfig();
    assert.deepEqual(Object.keys(remaining), [legacy.name]);
    assert.equal(remaining[legacy.name].relay_id, legacy.relay_id);
    assert.equal(remaining[legacy.name].physical_id, legacy.relay_id);
    assert.equal(fs.readFileSync(path.join(legacy.path, 'sentinel'), 'utf8'),
      'preserve existing workspace');
    await screenshot('reopen-singleton');
  }
  const running = await run(() => window.pawflowRelay.running());
  assert.deepEqual(running, []);
  fs.writeFileSync(path.join(output, phase + '-result.json'), JSON.stringify({
    status: 'passed', phase, platform: process.platform, electron: process.versions.electron,
    logical_ids: Object.values(readConfig()).map(item => item.relay_id),
    home_volumes: homeVolumes(),
    checks: phase === 'edit'
      ? ['migration', 'logical_controls', 'add_directory', 'permissions', 'cli_mode_preservation', 'invalid_path_atomicity']
      : ['fresh_process_reload', 'remove_directory', 'nonempty_group', 'identity', 'workspace_sentinel'],
    runtime_started: false,
  }, null, 2));
  clearTimeout(timer);
  app.quit();
}
main().catch(async error => {
  console.error(error.stack || error);
  if (window && !window.isDestroyed()) {
    try { await screenshot(phase + '-failure'); } catch (captureError) { console.error(captureError); }
  }
  clearTimeout(timer);
  app.exit(1);
});
