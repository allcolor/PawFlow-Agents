// Behavioural tests for the shared long-operation modal.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui', 'dialogs.js');
let passed = 0;
const failures = [];

function test(name, fn) {
  try { fn(); passed++; } catch (error) { failures.push(name + ': ' + error.message); }
}
function equal(actual, expected) {
  if (actual !== expected) throw new Error('expected ' + JSON.stringify(expected) + ' but got ' + JSON.stringify(actual));
}

function env() {
  const ids = new Map();
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {};
      this.className = ''; this.textContent = ''; this.hidden = false; this.removed = false;
      this.classList = { values: new Set(), add: value => this.classList.values.add(value) };
    }
    set id(value) { this._id = value; if (value) ids.set(value, this); }
    get id() { return this._id || ''; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
    focus() { this.focused = true; }
    remove() { this.removed = true; if (this.id) ids.delete(this.id); }
  }
  const document = {
    body: new Element('body'),
    createElement: tag => new Element(tag),
    getElementById: id => ids.get(id) || null,
  };
  const context = {
    document,
    t: (key, values) => values && values.name ? key + ':' + values.name : key,
    escapeHtml: value => String(value),
    console,
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), context, { filename: 'dialogs.js' });
  return { context, document };
}

test('only one blocking transaction can be active', () => {
  const e = env();
  const first = e.context.showOperationProgress({ title: 'One', phase: 'Working' });
  equal(!!first, true);
  equal(e.document.getElementById('operationProgressOverlay').getAttribute('aria-busy'), 'true');
  equal(e.context.showOperationProgress({ title: 'Two' }), null);
});

test('phase updates remain reactive while the modal blocks input', () => {
  const e = env();
  const progress = e.context.showOperationProgress({ phase: 'Review' });
  progress.setPhase('Create', 'Server is writing');
  equal(e.document.getElementById('operationProgressPhase').textContent, 'Create');
  equal(e.document.getElementById('operationProgressDetail').textContent, 'Server is writing');
});

test('failure is dismissible and closing releases the transaction lock', () => {
  const e = env();
  const progress = e.context.showOperationProgress({ phase: 'Review' });
  progress.fail('Rejected');
  const overlay = e.document.getElementById('operationProgressOverlay');
  equal(overlay.getAttribute('aria-busy'), 'false');
  equal(e.document.getElementById('operationProgressClose').hidden, false);
  progress.close();
  equal(overlay.removed, true);
  equal(!!e.context.showOperationProgress({ phase: 'Retry' }), true);
});

if (failures.length) {
  console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
  failures.forEach(failure => console.error('  - ' + failure));
  process.exit(1);
}
console.log(passed + ' passing');
