'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

function tick() {
  return new Promise(resolve => setTimeout(resolve, 5));
}

async function main() {
  const fetches = [];
  global.Node = function Node() {};
  global.conversationId = 'conv-1';
  global.selectedAgent = 'assistant';
  global.getAuthHeaders = () => ({ Authorization: 'Bearer test' });
  global.fetch = async (_url, options) => {
    fetches.push(JSON.parse(options.body));
    return { ok: true, json: async () => ({ ok: true }) };
  };
  global._actionClientId = () => 'tab-a';
  global._uiActionConversationId = () => '__ui__:tab-a';
  global._ensureUIActionSSE = () => {};
  global.document = {
    readyState: 'complete',
    visibilityState: 'visible',
    hasFocus: () => true,
    documentElement: { lang: 'en' },
    body: { appendChild() {} },
    head: { appendChild() {} },
    addEventListener() {},
    querySelector() { return null; },
    getElementById() { return null; },
    createElement() {
      return {
        addEventListener() {}, appendChild() {}, remove() {},
        setAttribute() {}, style: {},
      };
    },
  };
  const listeners = {};
  global.window = {
    PAWFLOW_EXTENSIONS: [
      { package: 'example.semantic', version_compat: 'ui.v1',
        assets: [], slots: [] },
    ],
    PAWFLOW_EXTENSION_CONTEXT: {
      user: 'alice', conversation: 'conv-1',
    },
    _userId: 'alice',
    addEventListener(name, cb) { listeners[name] = cb; },
    setInterval() { return 1; },
    clearInterval() {},
  };

  vm.runInThisContext(fs.readFileSync(
    'tasks/io/chat_ui/semantic_runtime.js', 'utf8'), {
    filename: 'semantic_runtime.js',
  });
  vm.runInThisContext(fs.readFileSync(
    'tasks/io/chat_ui/ext_runtime.js', 'utf8'), {
    filename: 'ext_runtime.js',
  });

  let pfp = null;
  assert.strictEqual(window.pawflow.register(
    'example.semantic', api => { pfp = api; }), true);
  await tick();
  assert.ok(pfp && pfp.semantic);

  assert.strictEqual(pfp.semantic.register({
    id: 'stage.test',
    role: 'figure',
    label: 'Test stage',
    parent: 'conversation.stage',
    state: () => ({ selected: 'none' }),
    actions: {
      select: {
        parameters: {
          name: { type: 'string', required: true },
        },
        run: args => ({ selected: args.name }),
      },
    },
  }), 'example.semantic:stage.test');

  assert.throws(() => pfp.semantic.register({
    id: 'stage.bad',
    role: 'figure',
    label: 'Bad',
    state: () => ({ node: document }),
    actions: {},
  }), /JSON-serializable/);

  const nodes = pfp.semantic.list();
  assert.ok(Object.isFrozen(nodes));
  assert.ok(Object.isFrozen(nodes[0]));
  assert.strictEqual(nodes[0].id, 'example.semantic:stage.test');
  assert.deepStrictEqual(nodes[0].state, { selected: 'none' });
  assert.deepStrictEqual(
    await pfp.semantic.invoke('stage.test', 'select', { name: 'luna' }),
    { selected: 'luna' });
  await assert.rejects(
    pfp.semantic.invoke('stage.test', 'select', {}),
    /required/);
  await assert.rejects(
    pfp.semantic.invoke('other.package:stage.test', 'select', { name: 'x' }),
    /does not own/);

  await window._pawflowSemanticRuntime.handleRequest({
    request_id: 'req-1',
    operation: 'get',
    target_package: 'example.semantic',
    arguments: {
      package: 'example.semantic',
      node: 'example.semantic:stage.test',
    },
  });
  const response = fetches.find(row =>
    row.action === 'pfp_semantic_result' && row.request_id === 'req-1');
  assert.ok(response);
  assert.strictEqual(response.result.id, 'example.semantic:stage.test');

  assert.strictEqual(window.pawflow.unregister('example.semantic'), true);
  assert.deepStrictEqual(
    window._pawflowSemanticRuntime.list('example.semantic'), []);
  await window._pawflowSemanticRuntime.handleRequest({
    request_id: 'req-disabled',
    operation: 'list',
    target_package: 'example.semantic',
    arguments: { package: 'example.semantic' },
  });
  const disabled = fetches.find(row => row.request_id === 'req-disabled');
  assert.match(disabled.error, /not registered/);

  console.log('pfp semantic runtime spec passed');
}

main().catch(err => {
  console.error(err);
  process.exitCode = 1;
});
