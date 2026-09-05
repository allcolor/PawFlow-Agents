'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const elements = [];
const document = {
  createElement(tag) {
    const element = { tag, style: {}, appendChild() {} };
    elements.push(element);
    return element;
  },
  body: { appendChild() {} },
  getElementById() { return {}; },
};
const context = vm.createContext({
  document, window: {}, setInterval() {}, clearInterval() {}, t: key => key,
});
vm.runInContext(
  fs.readFileSync('tasks/io/chat_ui/resources_service_login.js', 'utf8'),
  context,
);
context._openVncLoginDialog('login-test', 'service-test', 'test-capability');
const iframe = elements.find(element => element.tag === 'iframe');
assert.ok(iframe);
function checkViewer(url) {
  const page = new URL(url, 'https://pawflow.example/chat');
  // noVNC resolves its path setting against the viewer page.
  const socket = new URL(page.searchParams.get('path'), page);
  assert.equal(socket.href,
    'https://pawflow.example/vnc/login-test/test-capability/websockify');
}
checkViewer(iframe.src);

const installer = fs.readFileSync(
  'data/repository/flows/global/default/pawflow_installer/versions/assets/install.html',
  'utf8',
);
const start = installer.indexOf('function buildVncUrl(');
const end = installer.indexOf('function showVncDialog(', start);
assert.ok(start >= 0 && end > start);
vm.runInContext(installer.slice(start, end), context);
checkViewer(context.buildVncUrl('/vnc/login-test/test-capability/vnc.html'));
console.log('VNC login viewer URL checks passed');
