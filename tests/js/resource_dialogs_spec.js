// Behavioural regression tests for resource forms opened from repository '+'.
const fs = require('fs');
const vm = require('vm');

let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed++; }
  catch (error) { failures.push(name + ': ' + (error.message || error)); }
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}

const context = {
  console,
  window: {},
  t(key) { return key; },
  escapeHtml(value) { return String(value); },
  _resourceScopeOptions() { return '<option value="user">user</option>'; },
};
context.window = context;
vm.createContext(context);
vm.runInContext(
  fs.readFileSync('tasks/io/chat_ui/resources_resource_dialogs.js', 'utf8'),
  context,
  { filename: 'resources_resource_dialogs.js' },
);

test('prompt repository form renders its parameters editor', () => {
  const html = vm.runInContext("_buildResourceForm('prompt', {}, true, false)", context);
  assert(html.includes('id="res-name"'), 'new prompt name field is missing');
  assert(html.includes('id="res-parameters"'), 'prompt parameters editor is missing');
  assert(html.includes('+ Add Parameter'), 'editable prompt should allow parameters');
});

test('readonly prompt form omits parameter mutations', () => {
  const html = vm.runInContext("_buildResourceForm('prompt', {}, false, true)", context);
  assert(!html.includes('+ Add Parameter'), 'readonly prompt exposes parameter mutation');
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  failures.forEach(failure => console.error('  - ' + failure));
  process.exit(1);
}
console.log(passed + ' passing');
