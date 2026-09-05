// Behavioural tests for the shared floating-layer controller.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CHAT_UI = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui');
const tests = [];

function test(name, fn) { tests.push([name, fn]); }
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}
function eq(actual, expected, message) {
  if (actual !== expected) {
    throw new Error((message ? message + ': ' : '') + 'expected '
      + JSON.stringify(expected) + ' but got ' + JSON.stringify(actual));
  }
}

class EventHub {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  removeEventListener(type, listener) {
    const items = this.listeners.get(type) || [];
    this.listeners.set(type, items.filter(item => item !== listener));
  }
  dispatch(type, event) {
    for (const listener of [...(this.listeners.get(type) || [])]) {
      listener(Object.assign({type: type, target: null}, event || {}));
    }
  }
  count() {
    let total = 0;
    for (const listeners of this.listeners.values()) total += listeners.length;
    return total;
  }
}

class FakeElement extends EventHub {
  constructor(rect) {
    super();
    this.rect = Object.assign({left: 0, top: 0, width: 0, height: 0}, rect || {});
    this.style = {};
    this.parentNode = null;
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.focusCount = 0;
    this.className = '';
    this.tagName = 'DIV';
    this.textContent = '';
    this.ownerDocument = null;
    this.classList = {
      add: (...names) => {
        const classes = new Set(String(this.className || '').split(/\s+/).filter(Boolean));
        names.forEach(name => classes.add(name));
        this.className = Array.from(classes).join(' ');
      },
      remove: (...names) => {
        const removed = new Set(names);
        this.className = String(this.className || '').split(/\s+/).filter(
          name => name && !removed.has(name)
        ).join(' ');
      },
      contains: name => String(this.className || '').split(/\s+/).includes(name),
    };
  }
  getBoundingClientRect() {
    return Object.assign({
      right: this.rect.left + this.rect.width,
      bottom: this.rect.top + this.rect.height,
    }, this.rect);
  }
  appendChild(child) {
    if (child.parentNode) child.remove();
    child.parentNode = this;
    child.ownerDocument = this.ownerDocument;
    child.children.forEach(grandchild => { grandchild.ownerDocument = this.ownerDocument; });
    this.children.push(child);
    return child;
  }
  replaceChildren(...children) {
    this.children.forEach(child => { child.parentNode = null; });
    this.children = [];
    children.forEach(child => this.appendChild(child));
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter(child => child !== this);
    this.parentNode = null;
  }
  contains(node) {
    if (node === this) return true;
    return this.children.some(child => child.contains(node));
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) || null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  _matches(selector) {
    return selector.split(',').some(raw => {
      const part = raw.trim();
      if (part === '[data-pf-title]') return this.dataset.pfTitle !== undefined;
      if (part === '[title]') return this.attributes.has('title');
      if (part.startsWith('button')) return this.tagName === 'BUTTON';
      if (part.startsWith('.') && !part.includes(' ')) {
        return String(this.className || '').split(/\s+/).includes(part.slice(1));
      }
      return false;
    });
  }
  closest(selector) {
    let current = this;
    while (current) {
      if (current._matches(selector)) return current;
      current = current.parentNode;
    }
    return null;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const matches = [];
    const visit = element => {
      for (const child of element.children) {
        if (child._matches(selector)) {
          matches.push(child);
        }
        visit(child);
      }
    };
    visit(this);
    return matches;
  }
  click() { this.dispatch('click', {target: this}); }
  focus() {
    this.focusCount += 1;
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }
}

function env(options) {
  options = options || {};
  const documentHub = new EventHub();
  const windowHub = new EventHub();
  const body = new FakeElement();
  const document = Object.assign(documentHub, {
    body: body,
    activeElement: null,
    createElement() { return new FakeElement(); },
    getElementById(id) {
      let found = null;
      const visit = element => {
        if (element.id === id) found = element;
        if (!found) element.children.forEach(visit);
      };
      visit(body);
      return found;
    },
  });
  body.ownerDocument = document;
  const animations = [];
  const timers = new Map();
  let nextTimer = 1;
  const ctx = Object.assign(windowHub, {
    document: document,
    innerWidth: 300,
    innerHeight: 200,
    console: console,
    Promise: Promise,
    AbortController: AbortController,
    pfMotion: {
      reduced() { return false; },
      read(callback) { return Promise.resolve().then(callback); },
      write(callback) { return Promise.resolve().then(callback); },
      replace(element, channel, keyframes) {
        animations.push({
          element, channel, keyframes,
          transformOrigin: element.style.transformOrigin,
        });
        return Promise.resolve({status: 'finished'});
      },
    },
    getComputedStyle() { return {flexDirection: 'row'}; },
    setTimeout(callback, delay) {
      const id = nextTimer++;
      timers.set(id, {callback, delay: Number(delay || 0)});
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    __PF_FLOATING_DIAGNOSTICS__: true,
  });
  ctx.window = ctx;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(
    fs.readFileSync(path.join(CHAT_UI, 'ui_floating_layer.js'), 'utf8'),
    ctx,
    {filename: 'ui_floating_layer.js'}
  );
  if (options.tooltips) {
    vm.runInContext(
      fs.readFileSync(path.join(CHAT_UI, 'tooltips.js'), 'utf8'),
      ctx,
      {filename: 'tooltips.js'}
    );
  }
  return {
    ctx, document, body, animations,
    runTimers() {
      while (timers.size) {
        const next = Array.from(timers.entries()).sort(function(a, b) {
          return a[1].delay - b[1].delay || a[0] - b[0];
        })[0];
        timers.delete(next[0]);
        next[1].callback();
      }
    },
    timerCount() { return timers.size; },
  };
}

async function settle() {
  for (let index = 0; index < 8; index++) await Promise.resolve();
}

test('cursor placement clamps to the viewport and Escape restores focus', async () => {
  const e = env();
  const trigger = new FakeElement({left: 280, top: 180, width: 12, height: 12});
  const menu = new FakeElement({width: 80, height: 60});

  e.ctx.pfFloatingLayer.open({
    channel: 'context-menu',
    element: menu,
    trigger: trigger,
    point: {x: 290, y: 190},
    placement: 'cursor',
    removeOnClose: true,
    restoreFocus: true,
  });
  await settle();

  eq(menu.style.left, '210px');
  eq(menu.style.top, '130px');
  eq(e.animations[0].transformOrigin, 'bottom right',
    'entry animation started before placement wrote its transform origin');
  eq(e.ctx.pfFloatingLayer.diagnostics().activeLayers, 1);
  e.document.dispatch('keydown', {key: 'Escape', preventDefault() {}});
  await settle();

  eq(menu.parentNode, null, 'Escape did not remove the menu');
  eq(trigger.focusCount, 1, 'focus was not restored');
  eq(e.ctx.pfFloatingLayer.diagnostics().activeLayers, 0);
  eq(e.document.count(), 0, 'document listeners leaked');
  eq(e.ctx.count(), 0, 'window listeners leaked');
});

test('grouped tooltip replacement transfers aria ownership without leaking timers', async () => {
  const e = env({tooltips: true});
  const tooltip = new FakeElement({width: 100, height: 30});
  tooltip.id = 'pfCssTooltip';
  e.body.appendChild(tooltip);
  const first = new FakeElement({left: 30, top: 40, width: 20, height: 20});
  first.dataset.pfTitle = 'First';
  const second = new FakeElement({left: 80, top: 40, width: 20, height: 20});
  second.dataset.pfTitle = 'Second';

  e.document.dispatch('mouseover', {target: first});
  e.runTimers();
  await settle();
  eq(first.getAttribute('aria-describedby'), 'pfCssTooltip');

  e.document.dispatch('mouseover', {target: second});
  e.runTimers();
  await settle();
  eq(first.getAttribute('aria-describedby'), null,
    'the replaced tooltip trigger kept stale aria ownership');
  eq(second.getAttribute('aria-describedby'), 'pfCssTooltip');

  e.document.dispatch('mouseout', {target: second, relatedTarget: new FakeElement()});
  e.runTimers();
  await settle();
  eq(second.getAttribute('aria-describedby'), null);
  eq(tooltip.getAttribute('aria-hidden'), 'true');
  eq(e.timerCount(), 0, 'tooltip timers leaked');
});

test('outside interaction and pointer cancellation close the owned layer', async () => {
  const e = env();
  const trigger = new FakeElement();
  const outside = new FakeElement();
  const first = new FakeElement({width: 40, height: 20});
  e.ctx._positionMenu(first, {clientX: 20, clientY: 30, currentTarget: trigger});
  await settle();
  e.document.dispatch('pointerdown', {target: outside});
  await settle();
  eq(first.parentNode, null, 'outside pointer did not close the menu');

  const second = new FakeElement({width: 40, height: 20});
  e.ctx._positionMenu(second, {clientX: 20, clientY: 30, currentTarget: trigger});
  await settle();
  e.document.dispatch('pointercancel', {target: outside});
  await settle();
  eq(second.parentNode, null, 'pointercancel did not close the menu');
});

test('transcript scrolling preserves a sidebar menu until its own panel scrolls', async () => {
  const e = env();
  const sidebar = e.body.appendChild(new FakeElement());
  const transcript = e.body.appendChild(new FakeElement());
  const trigger = sidebar.appendChild(new FakeElement());
  const menu = new FakeElement({width: 80, height: 60});
  e.ctx._positionMenu(menu, {clientX: 20, clientY: 30, currentTarget: trigger});
  await settle();

  e.ctx.dispatch('scroll', {target: transcript});
  await settle();
  assert(menu.parentNode === e.body, 'incoming-message auto-scroll dismissed the menu');
  eq(menu.getAttribute('aria-hidden'), 'false');
  eq(trigger.getAttribute('aria-expanded'), 'true');
  eq(trigger.focusCount, 0, 'unrelated scrolling moved focus');
  eq(e.ctx.pfFloatingLayer.diagnostics().activeLayers, 1);

  e.ctx.dispatch('scroll', {target: sidebar});
  await settle();
  eq(menu.parentNode, null, 'scrolling the trigger panel left a stale menu');
  eq(trigger.focusCount, 0, 'panel scrolling stole focus');
  eq(e.ctx.pfFloatingLayer.diagnostics().listeners, 0);
});

test('a long menu can scroll internally and still closes on page scroll', async () => {
  const e = env();
  const trigger = e.body.appendChild(new FakeElement());
  const menu = new FakeElement({width: 80, height: 60});
  const menuBody = menu.appendChild(new FakeElement());
  e.ctx._positionMenu(menu, {clientX: 20, clientY: 30, currentTarget: trigger});
  await settle();

  for (const target of [menu, menuBody]) {
    e.ctx.dispatch('scroll', {target});
    await settle();
    eq(menu.getAttribute('aria-hidden'), 'false', 'menu scrolling dismissed itself');
    eq(e.ctx.pfFloatingLayer.diagnostics().activeLayers, 1);
  }

  e.ctx.dispatch('scroll', {target: e.document});
  await settle();
  eq(menu.parentNode, null);
  eq(e.ctx.pfFloatingLayer.diagnostics().listeners, 0);
});

test('one hundred cycles return listeners and portal nodes to baseline', async () => {
  const e = env();
  const trigger = new FakeElement();
  const outside = new FakeElement();
  for (let index = 0; index < 100; index++) {
    const menu = new FakeElement({width: 50, height: 30});
    e.ctx._positionMenu(menu, {
      clientX: 10 + (index % 20),
      clientY: 10 + (index % 20),
      currentTarget: trigger,
    });
    await settle();
    e.document.dispatch('pointerdown', {target: outside});
    await settle();
  }

  eq(e.body.children.length, 0, 'portal nodes leaked');
  eq(e.document.count(), 0, 'document listeners leaked');
  eq(e.ctx.count(), 0, 'window listeners leaked');
  const diagnostics = e.ctx.pfFloatingLayer.diagnostics();
  eq(diagnostics.activeLayers, 0);
  eq(diagnostics.listeners, 0);
});

test('a retained tooltip portal is hidden rather than removed on scroll', async () => {
  const e = env();
  const target = new FakeElement({left: 130, top: 80, width: 40, height: 20});
  const tooltip = new FakeElement({width: 100, height: 30});
  e.body.appendChild(tooltip);
  e.ctx.pfFloatingLayer.open({
    channel: 'tooltip',
    element: tooltip,
    trigger: target,
    placement: 'top',
    removeOnClose: false,
    restoreFocus: false,
  });
  await settle();
  e.ctx.dispatch('scroll', {target: e.document});
  await settle();
  eq(tooltip.parentNode, e.body, 'tooltip portal was removed');
  eq(tooltip.getAttribute('aria-hidden'), 'true');
  eq(e.ctx.pfFloatingLayer.diagnostics().activeLayers, 0);
});

test('menu arrows, Home/End and typeahead move the roving focus', async () => {
  const e = env();
  const trigger = new FakeElement();
  const menu = new FakeElement({width: 100, height: 80});
  const alpha = new FakeElement();
  alpha.className = 'ctx-menu-item';
  alpha.textContent = 'Alpha';
  const beta = new FakeElement();
  beta.className = 'ctx-menu-item';
  beta.textContent = 'Beta';
  const gamma = new FakeElement();
  gamma.className = 'ctx-menu-item';
  gamma.textContent = 'Gamma';
  menu.appendChild(alpha);
  menu.appendChild(beta);
  menu.appendChild(gamma);

  e.ctx._positionMenu(menu, {
    type: 'keydown', clientX: 20, clientY: 20, currentTarget: trigger,
  });
  await settle();
  eq(alpha.focusCount, 1, 'keyboard opening did not focus the first item');
  menu.dispatch('keydown', {key: 'End', preventDefault() {}});
  eq(gamma.focusCount, 1, 'End did not focus the final item');
  menu.dispatch('keydown', {key: 'b', preventDefault() {}});
  eq(beta.focusCount, 1, 'typeahead did not focus the matching item');
  menu.dispatch('keydown', {key: 'Home', preventDefault() {}});
  eq(alpha.focusCount, 2, 'Home did not restore the first item');
});

test('resize and blur close without restoring focus', async () => {
  const e = env();
  const trigger = new FakeElement();
  const first = new FakeElement({width: 40, height: 20});
  e.ctx._positionMenu(first, {clientX: 20, clientY: 30, currentTarget: trigger});
  await settle();
  e.ctx.dispatch('resize', {});
  await settle();
  eq(first.parentNode, null);
  eq(trigger.focusCount, 0, 'resize stole focus');

  const second = new FakeElement({width: 40, height: 20});
  e.ctx._positionMenu(second, {clientX: 20, clientY: 30, currentTarget: trigger});
  await settle();
  e.ctx.dispatch('blur', {});
  await settle();
  eq(second.parentNode, null);
  eq(trigger.focusCount, 0, 'blur stole focus');
});

test('modal ownership preserves overlay layout and settles focus after exit', async () => {
  const e = env();
  const previous = new FakeElement();
  const outside = new FakeElement();
  const overlay = new FakeElement({width: 300, height: 200});
  const dialog = new FakeElement({width: 220, height: 120});
  const first = new FakeElement();
  first.tagName = 'BUTTON';
  const last = new FakeElement();
  last.tagName = 'BUTTON';
  dialog.appendChild(first);
  dialog.appendChild(last);
  overlay.appendChild(dialog);

  e.ctx.pfFloatingLayer.open({
    channel: 'workflow-dialog',
    element: overlay,
    motionElement: dialog,
    trigger: previous,
    initialFocus: dialog,
    managePlacement: false,
    modal: true,
    closeOnOutside: false,
    closeOnEnvironment: false,
    closeOnSelect: false,
    removeOnClose: true,
    restoreFocus: true,
  });
  await settle();

  eq(overlay.style.left, undefined, 'controller overwrote modal overlay layout');
  assert(e.animations[0].element === dialog, 'modal card was not the motion owner');
  eq(dialog.focusCount, 1, 'modal did not receive initial focus');
  e.document.dispatch('pointerdown', {target: outside});
  e.ctx.dispatch('resize', {});
  e.ctx.dispatch('scroll', {});
  eq(e.ctx.pfFloatingLayer.diagnostics().activeLayers, 1,
    'environment interaction dismissed the modal');

  first.focus();
  e.document.dispatch('keydown', {
    key: 'Tab', shiftKey: true, preventDefault() {},
  });
  eq(last.focusCount, 1, 'Shift+Tab did not wrap modal focus');

  e.document.dispatch('keydown', {key: 'Escape', preventDefault() {}});
  assert(overlay.hasAttribute('inert'), 'closing modal stayed interactive');
  assert(overlay.parentNode === e.body, 'modal unmounted before its exit settled');
  await settle();
  eq(overlay.parentNode, null);
  eq(previous.focusCount, 1, 'focus was not restored after modal exit');
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
