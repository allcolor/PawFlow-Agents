// Behavioural regression tests for the ACP registry import service flow.
const fs = require('fs');
const vm = require('vm');
const dom = require('./dom_stub');

const tests = [];
function test(name, fn) { tests.push([name, fn]); }
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}
function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const calls = [];
const responses = [];
const messages = [];
const context = {
  console,
  document: dom.document,
  setTimeout: dom.setTimeout,
  clearTimeout: dom.clearTimeout,
  Date: dom.Date,
  Event: class Event {
    constructor(type, options) { this.type = type; this.bubbles = !!(options && options.bubbles); }
  },
  t(key) { return key; },
  escapeHtml,
  action$(name, payload) {
    calls.push([name, payload]);
    return { response: responses.shift() };
  },
  rxjs: {
    firstValueFrom(observable) { return Promise.resolve(observable.response); },
  },
  addMsg(kind, message) { messages.push([kind, message]); },
  _flowToCli() { return 'claude'; },
  _collectSchemaValues() { return { acp_cwd: '/existing/workspace' }; },
  confirm() { return true; },
  fireAction() {},
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(
  fs.readFileSync('tasks/io/chat_ui/resources_service_acp_registry.js', 'utf8'),
  context,
  { filename: 'resources_service_acp_registry.js' },
);
vm.runInContext(
  fs.readFileSync('tasks/io/chat_ui/resources_service_login.js', 'utf8'),
  context,
  { filename: 'resources_service_login.js' },
);

const catalogue = {
  ok: true,
  registry_version: '1.0.0',
  platform: 'linux-x86_64',
  runners: { npx: true, uvx: false },
  entries: [
    {
      id: 'codex-acp',
      name: 'Codex',
      version: '1.8.0',
      description: 'ACP adapter',
      license: 'Apache-2.0',
      license_url: 'https://example.invalid/license',
      distributions: ['npx', 'binary'],
      platforms: ['linux-aarch64'],
      auth_types: ['agent'],
      load_session: true,
      quarantined: false,
    },
    {
      id: 'fast-agent',
      name: 'Fast Agent',
      version: '0.10.1',
      description: 'quarantined',
      license: 'proprietary',
      distributions: ['uvx'],
      platforms: [],
      auth_types: ['terminal'],
      load_session: false,
      quarantined: true,
      quarantine_reason: 'Timeout after 120s',
    },
  ],
};

function appendInput(panel, id, type) {
  const input = dom.document.createElement('input');
  input.id = id;
  input.type = type || 'text';
  input.value = '';
  input.checked = false;
  input.dispatchEvent = function(event) { this.dispatch(event.type, event); return true; };
  panel.appendChild(input);
  return input;
}

test('catalogue renders decision metadata and labels unavailable distributions', () => {
  const html = context.PawFlowAcpRegistry.renderCatalogue(catalogue);
  assert(html.includes('Codex'), 'agent name is missing');
  assert(html.includes('1.8.0'), 'version is missing');
  assert(html.includes('Apache-2.0'), 'license is missing');
  assert(html.includes('agent'), 'auth type is missing');
  assert(html.includes('loadSession'), 'capability is missing');
  assert(html.includes('value="npx"'), 'available npx distribution is missing');
  assert(html.includes('value="binary"'), 'binary distribution should remain visible');
  assert(html.includes('value="binary" disabled'), 'wrong-platform binary must be disabled');
  assert(html.includes('not available for linux-x86_64'), 'wrong-platform binary needs an explicit reason');
  assert(html.includes('data-acp-registry-agent="fast-agent"'), 'quarantined agent should stay visible');
  assert(html.includes('disabled'), 'quarantined agent must not be importable');
  assert(!html.includes('archive'), 'download URLs or archive metadata must not reach the picker');
});

test('prepared config fills schema inputs and dispatches changes', () => {
  const overlay = dom.document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  const panel = dom.document.createElement('div');
  panel.dataset.schema = '{}';
  overlay.appendChild(panel);
  dom.documentElement.appendChild(overlay);
  const baseQuerySelector = dom.document.querySelector;
  dom.document.querySelector = function(selector) {
    return selector === '#resourceEditorOverlay > div' ? panel : baseQuerySelector(selector);
  };
  const provider = appendInput(panel, 'svc-p-provider');
  const args = appendInput(panel, 'svc-p-acp_args');
  const load = appendInput(panel, 'svc-p-acp_load_session', 'checkbox');
  let changes = 0;
  provider.addEventListener('change', () => { changes++; });
  args.addEventListener('change', () => { changes++; });
  load.addEventListener('change', () => { changes++; });

  const applied = context.PawFlowAcpRegistry.applyConfig({
    provider: 'acp',
    acp_args: '["--yes","codex-acp"]',
    acp_load_session: true,
  });

  assert(applied === 3, 'all matching fields should be applied');
  assert(provider.value === 'acp', 'provider was not filled');
  assert(args.value.includes('codex-acp'), 'args were not filled');
  assert(load.checked === true, 'boolean field was not filled');
  assert(changes === 3, 'rule listeners were not notified');
});

test('service action opens the picker, prepares selection, and applies it', async () => {
  calls.length = 0;
  responses.push(catalogue, {
    ok: true,
    status: 'ready',
    config: {
      provider: 'acp',
      acp_command: '/usr/bin/npx',
      acp_args: '["--yes","@agentclientprotocol/codex-acp@1.8.0"]',
      acp_cwd: '/existing/workspace',
      acp_registry: '{"id":"codex-acp","version":"1.8.0"}',
    },
  });

  const actions = dom.document.createElement('div');
  const button = dom.document.createElement('button');
  actions.appendChild(button);
  dom.documentElement.appendChild(actions);
  context.event = { target: button };

  await vm.runInContext(
    "_executeServiceAction('acp_registry_import', '', 'acp_registry_import', 'acp_registry_catalogue', '')",
    context,
  );
  const picker = actions.querySelector('[data-acp-registry-picker]');
  assert(picker, 'dispatcher did not open the ACP registry picker');
  const row = picker.querySelector('[data-acp-registry-agent="codex-acp"]');
  const select = row.querySelector('[data-acp-registry-distribution]');
  select.value = 'npx';
  row.querySelector('[data-acp-registry-import]').click();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();

  assert(calls[0][0] === 'acp_registry_catalogue', 'catalogue action was not called');
  assert(calls[1][0] === 'acp_registry_prepare', 'prepare action was not called');
  assert(calls[1][1].agent_id === 'codex-acp', 'wrong agent was prepared');
  assert(calls[1][1].distribution === 'npx', 'wrong distribution was prepared');
  assert(calls[1][1].cwd === '/existing/workspace', 'existing cwd was not preserved');
  assert(dom.document.getElementById('svc-p-acp_args').value.includes('codex-acp'),
    'prepared config was not applied to the live form');
});

(async () => {
  let passed = 0;
  const failures = [];
  for (const [name, fn] of tests) {
    try { await fn(); passed++; }
    catch (error) { failures.push(name + ': ' + (error.message || error)); }
  }
  if (failures.length) {
    console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
    failures.forEach(failure => console.error('  - ' + failure));
    process.exit(1);
  }
  console.log(passed + ' passing');
})();
