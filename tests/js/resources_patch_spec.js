// Behavioural contracts for incremental Resources ownership.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CHAT_UI = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui');
const STUB = path.join(__dirname, 'dom_stub.js');
const tests = [];

function test(name, fn) { tests.push([name, fn]); }
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}

function env() {
  delete require.cache[require.resolve(STUB)];
  const dom = require(STUB);
  const disclosureCalls = [];
  const ctx = {
    document: dom.document,
    AbortController,
    Promise,
    console,
    _isSectionCollapsed: () => false,
    pfDisclosure: {
      create(options) {
        const record = {options, sets: [], destroyed: false};
        disclosureCalls.push(record);
        return {
          set(open) { record.sets.push(open); return Promise.resolve({status: open ? 'open' : 'closed'}); },
          destroy() { record.destroyed = true; },
        };
      },
    },
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(CHAT_UI, 'resources_patch.js'), 'utf8'), ctx,
    {filename: 'resources_patch.js'});
  return {ctx, dom, disclosureCalls};
}

function row(dom, key, text) {
  const node = dom.document.createElement('div');
  node.dataset.resourceRow = key;
  node.textContent = text;
  return node;
}

function keyed(dom, key, text) {
  const node = dom.document.createElement('div');
  node.dataset.pfKey = key;
  node.textContent = text;
  return node;
}

function section(dom, id, rows) {
  const owner = dom.document.createElement('section');
  owner.dataset.resourceSection = id;
  const header = dom.document.createElement('div');
  header.className = 'resource-section-header-row';
  const trigger = dom.document.createElement('button');
  trigger.className = 'resource-section-toggle';
  trigger.textContent = id;
  header.appendChild(trigger);
  const body = dom.document.createElement('div');
  body.className = 'resource-section-body';
  body.id = 'res-section-' + id;
  rows.forEach(item => body.appendChild(row(dom, item[0], item[1])));
  owner.append(header, body);
  return owner;
}

test('a changed row does not replace its section or unchanged siblings', () => {
  const e = env();
  const currentRoot = e.dom.document.createElement('div');
  const currentSection = section(e.dom, 'services', [['a', 'same'], ['b', 'old']]);
  currentRoot.appendChild(currentSection);
  const first = currentSection.querySelector('[data-resource-row="a"]');
  const second = currentSection.querySelector('[data-resource-row="b"]');

  const nextRoot = e.dom.document.createElement('div');
  nextRoot.appendChild(section(e.dom, 'services', [['b', 'new'], ['a', 'same'], ['c', 'added']]));
  e.ctx.pfResources.patchChildren(currentRoot, nextRoot);

  assert(currentRoot.children[0] === currentSection, 'section identity changed');
  const rows = currentSection.querySelector('.resource-section-body').children;
  assert(rows[0] !== second && rows[0].textContent === 'new', 'changed row was not replaced');
  assert(rows[1] === first, 'unchanged row identity changed');
  assert(rows[2].dataset.resourceRow === 'c', 'new row was not inserted');
});

test('removed rows disappear without clearing the keyed parent', () => {
  const e = env();
  const current = e.dom.document.createElement('div');
  const retained = row(e.dom, 'a', 'same');
  current.append(retained, row(e.dom, 'b', 'gone'));
  const next = e.dom.document.createElement('div');
  next.appendChild(row(e.dom, 'a', 'same'));

  e.ctx.pfResources.patchChildren(current, next);

  assert(current.children.length === 1, 'removed row survived');
  assert(current.children[0] === retained, 'retained row lost identity');
});

test('neutral keyed nodes retain identity while their local content changes', () => {
  const e = env();
  const current = e.dom.document.createElement('div');
  const first = keyed(e.dom, 'run:a', 'same');
  const second = keyed(e.dom, 'run:b', 'old');
  current.append(first, second);
  const next = e.dom.document.createElement('div');
  next.append(keyed(e.dom, 'run:b', 'new'), keyed(e.dom, 'run:a', 'same'));

  e.ctx.pfDomPatch.patchChildren(current, next);

  assert(current.children[0] === second, 'changed keyed node lost identity');
  assert(current.children[0].textContent === 'new', 'changed keyed content was stale');
  assert(current.children[1] === first, 'unchanged keyed node lost identity');
});

test('section visibility delegates to one persistent disclosure controller', async () => {
  const e = env();
  const owner = section(e.dom, 'agent', []);
  e.dom.documentElement.appendChild(owner);

  await e.ctx.pfResources.setSectionOpen('agent', false);
  await e.ctx.pfResources.setSectionOpen('agent', true);

  assert(e.disclosureCalls.length === 1, 'disclosure controller was recreated');
  assert(e.disclosureCalls[0].sets.join(',') === 'false,true', 'logical targets were not forwarded');
});

(async function run() {
  let passed = 0;
  const failures = [];
  for (const [name, fn] of tests) {
    try {
      await fn();
      passed += 1;
    } catch (error) {
      failures.push(name + ': ' + (error && error.stack ? error.stack : error));
    }
  }
  if (failures.length) {
    console.error('\n' + failures.length + ' failing, ' + passed + ' passing');
    failures.forEach(failure => console.error('  - ' + failure));
    process.exit(1);
  }
  console.log(passed + ' passing');
})();
