'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

const elements = {
  themeSelect: {innerHTML: '', value: '', style: {}},
  conversationThemeSelect: {innerHTML: '', value: '', style: {}},
  themeSelectControl: {style: {}},
  convThemeLabel: {style: {}},
  'custom-theme': {textContent: ''},
};
const requests = [];

global.window = global;
global.conversationId = 'conv-a';
global.document = {
  cookie: 'pawflow_theme_ref=global:pawflow_dark',
  head: {appendChild: node => { elements[node.id] = node; }},
  addEventListener: () => {},
  createElement: () => ({id: '', textContent: ''}),
  getElementById: id => elements[id] || null,
};
global.escapeHtml = value => String(value);
global.t = key => key;
global.addMsg = (kind, message) => { throw new Error(kind + ': ' + message); };
global.action$ = (action, args) => {
  const request = deferred();
  requests.push({action, args, ...request});
  return request.promise;
};
global.rxjs = {firstValueFrom: value => value};

vm.runInThisContext(
  fs.readFileSync('tasks/io/chat_ui/themes.js', 'utf8'),
  {filename: 'tasks/io/chat_ui/themes.js'});

const dark = {ref: 'global:pawflow_dark', scope: 'global', name: 'pawflow_dark'};
const red = {ref: 'conversation:red', scope: 'conversation', name: 'red'};
const blue = {ref: 'conversation:blue', scope: 'conversation', name: 'blue'};

(async () => {
  const firstLoad = loadThemeSelector();
  assert.strictEqual(requests[0].action, 'list_chat_themes');
  assert.strictEqual(requests[0].args.conversation_id, 'conv-a');
  requests[0].resolve({themes: [dark, red], conversation_theme_ref: red.ref});
  await Promise.resolve();
  await Promise.resolve();
  assert.strictEqual(requests[1].action, 'apply_chat_theme');
  assert.strictEqual(requests[1].args.conversation_id, 'conv-a');

  conversationId = 'conv-b';
  const secondLoad = loadThemeSelector();
  assert.strictEqual(requests[2].args.conversation_id, 'conv-b');
  requests[2].resolve({themes: [dark, blue], conversation_theme_ref: blue.ref});
  await Promise.resolve();
  await Promise.resolve();
  assert.strictEqual(requests[3].action, 'apply_chat_theme');
  assert.strictEqual(requests[3].args.conversation_id, 'conv-b');

  requests[3].resolve({ok: true, theme_ref: blue.ref, css: 'body { color: blue; }'});
  await secondLoad;
  assert.strictEqual(elements['custom-theme'].textContent, 'body { color: blue; }');

  requests[1].resolve({ok: true, theme_ref: red.ref, css: 'body { color: red; }'});
  await firstLoad;
  assert.strictEqual(
    elements['custom-theme'].textContent,
    'body { color: blue; }',
    'late CSS from conv-a replaced the active conv-b theme');

  console.log('conversation theme race: ok');
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
