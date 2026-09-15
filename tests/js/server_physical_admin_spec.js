'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.resolve(__dirname, '../../tasks/io/chat_ui/admin_settings.js'), 'utf8');

function harness() {
  const calls = [];
  const context = vm.createContext({
    document: { addEventListener() {} },
    _isAdmin: () => true,
    addMsg: (kind, message) => calls.push({ kind, message }),
    action$: (action, body) => ({
      subscribe: callback => { calls.push({ action, body }); callback({ ok: true }); },
    }),
  });
  vm.runInContext(source, context);
  return { context, calls };
}

test('management panel separates the physical name from logical permission targets', () => {
  const { context } = harness();
  const physical = {
    physical_id: 'parent', name: 'MyWorkspace (physical)', scope: 'user', scope_id: 'alice',
    owner_id: 'alice', status: 'partial',
    logical_relays: [
      { service_id: 'MyWorkspace', scope: 'user', scope_id: 'alice', connected: true, server_local_exec: true },
      { service_id: 'Docs', scope: 'user', scope_id: 'alice', connected: false, mode: 'readonly' },
    ],
  };
  const html = context.adminServerPhysicalPanel(physical);
  assert.equal((html.match(/class="adm-physical-relay"/g) || []).length, 1);
  assert.equal((html.match(/class="adm-logical-relay"/g) || []).length, 2);
  assert.match(html, /MyWorkspace \(physical\)/);
  assert.match(html, /adminSetServerRelayLocalExec\(this,"MyWorkspace","user","alice"\)/);
  assert.match(html, /adminSetServerRelayLocalExec\(this,"Docs","user","alice"\)/);
  assert.doesNotMatch(html, /adminSetServerRelayLocalExec\(this,"parent"/);
  assert.match(html, /partial/);
  assert.match(html, /Read-only/);
});

test('names and paths are escaped before interpolation into HTML', () => {
  const { context } = harness();
  const html = context.adminServerPhysicalPanel({
    physical_id: '" onclick="bad', name: '<img src=x>', scope: 'global', status: 'connected',
    logical_relays: [{
      service_id: "A'<script>", scope: 'user', scope_id: "owner'", connected: true,
      workspace_dir: '<img src=x onerror=bad>', mode: 'readwrite',
    }],
  });
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;img/);
  assert.match(html, /data-physical-id="&quot; onclick=&quot;bad"/);
});

test('permission changes still invoke the logical service endpoint', () => {
  const { context, calls } = harness();
  const input = { checked: true, disabled: false };
  context.adminSetServerRelayLocalExec(input, 'MyWorkspace', 'user', 'alice');
  assert.equal(calls[0].action, 'admin_server_relay_local_exec_set');
  assert.equal(calls[0].body.service_id, 'MyWorkspace');
  assert.equal(calls[0].body.scope_id, 'alice');
  assert.equal(calls[0].body.enabled, true);
  assert.equal(input.disabled, false);
});

test('physical controls always target the parent and include its scope', () => {
  const { context } = harness();
  const html = context.adminServerPhysicalPanel({
    physical_id: 'parent', name: 'Machine', configured: true, scope: 'user', scope_id: 'alice',
    logical_relays: [{ service_id: 'Child', scope: 'user', scope_id: 'alice' }],
  });
  assert.match(html, /adminRunPhysical\(this,"start","parent","user","alice"\)/);
  assert.match(html, /adminRunPhysical\(this,"stop","parent","user","alice"\)/);
  assert.doesNotMatch(html, /adminRunPhysical\(this,"start","Child"/);
});

test('directory form retains existing IDs, access mode and permission values', () => {
  const { context } = harness();
  const html = context.adminPhysicalWorkspaceFields({
    service_id: 'Existing', mode: 'readonly', allow_exec: false,
    server_local_exec: true, allow_service_tunnels: true,
  });
  assert.match(html, /value="Existing" readonly/);
  assert.match(html, /value="readonly" selected/);
  assert.doesNotMatch(html, /class="adm-directory-exec" checked/);
  assert.match(html, /class="adm-directory-local" checked/);
  assert.match(html, /class="adm-directory-tunnels" checked/);
  assert.doesNotMatch(context.adminPhysicalWorkspaceFields({}), / readonly/);
});

test('whole group payload includes all directories and the optimistic revision', () => {
  const { context } = harness();
  const nodes = {
    '.adm-physical-scope': { value: 'user' },
    '.adm-physical-owner': { value: 'alice' },
    '.adm-physical-name': { value: ' My machine ' },
  };
  function row(id, mode) {
    const inputs = {
      '.adm-directory-id': { value: id }, '.adm-directory-mode': { value: mode },
      '.adm-directory-exec': { checked: true }, '.adm-directory-local': { checked: false },
      '.adm-directory-tunnels': { checked: true },
    };
    return { querySelector: s => inputs[s] };
  }
  const overlay = {
    _physicalRecord: { physical_id: 'parent', revision: 7 },
    querySelector: s => nodes[s],
    querySelectorAll: () => [row('Code', 'readwrite'), row('Docs', 'readonly')],
  };
  const payload = JSON.parse(JSON.stringify(context.adminPhysicalPayload(overlay)));
  assert.equal(payload.physical_id, 'parent');
  assert.equal(payload.revision, 7);
  assert.equal(payload.name, 'My machine');
  assert.deepEqual(payload.workspaces.map(r => r.service_id), ['Code', 'Docs']);
  assert.equal(payload.workspaces[1].mode, 'readonly');
  assert.equal(payload.workspaces[1].server_local_exec, false);
  assert.equal(payload.workspaces[1].allow_service_tunnels, true);
  assert.equal('workspace_dir' in payload.workspaces[0], false);
});

test('asynchronous save failure restores controls and keeps the form for correction', () => {
  const { context } = harness();
  const calls = [], status = { textContent: '' };
  const inputs = [{ disabled: false }, { disabled: true }];
  let removed = false;
  const overlay = {
    querySelector: () => status, querySelectorAll: () => inputs,
    remove: () => { removed = true; }, isConnected: true,
  };
  const button = { closest: selector => selector === '.exec-overlay' ? overlay : null };
  context.action$ = (action, payload) => ({
    subscribe(callback) {
      calls.push({ action, payload });
      callback(action === 'admin_server_physical_save'
        ? { accepted: true, operation_id: 'op-1' }
        : { status: 'failed', error: 'Disk full' });
    },
  });
  context.adminSubmitPhysical(button, 'save', { physical_id: 'parent', scope: 'user', scope_id: 'alice' });
  assert.deepEqual(calls.map(c => c.action), ['admin_server_physical_save', 'admin_server_physical_operation']);
  assert.equal(calls[1].payload.operation_id, 'op-1');
  assert.equal(status.textContent, 'Disk full');
  assert.deepEqual(inputs.map(i => i.disabled), [false, true]);
  assert.equal(removed, false);
});

test('running operation remains visible until a completed status refreshes the management view', () => {
  const { context } = harness();
  const queued = [], status = { textContent: '' }, control = { disabled: false };
  let removed = false, refreshed = false, reads = 0;
  const overlay = {
    querySelector: () => status, querySelectorAll: () => [control],
    remove: () => { removed = true; }, isConnected: true,
  };
  const button = { closest: selector => selector === '.exec-overlay' ? overlay : null };
  context.document.querySelector = () => null;
  context.openAdminServerRelaysDialog = () => { refreshed = true; };
  context.setTimeout = callback => queued.push(callback);
  context.action$ = action => ({
    subscribe(callback) {
      callback(action === 'admin_server_physical_start'
        ? { accepted: true, operation_id: 'op' }
        : { status: ++reads === 1 ? 'running' : 'completed' });
    },
  });
  context.adminRunPhysical(button, 'start', 'parent', 'user', 'alice');
  assert.equal(control.disabled, true);
  assert.equal(removed, false);
  assert.equal(queued.length, 1);
  queued.shift()();
  assert.equal(removed, true);
  assert.equal(refreshed, true);
});
