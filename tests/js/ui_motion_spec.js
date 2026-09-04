// Behavioural tests for the shared WebChat motion and disclosure primitives.
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
function eq(actual, expected, message) {
  if (actual !== expected) {
    throw new Error((message ? message + ': ' : '') + 'expected '
      + JSON.stringify(expected) + ' but got ' + JSON.stringify(actual));
  }
}

function env(reduced) {
  delete require.cache[require.resolve(STUB)];
  const dom = require(STUB);
  const frames = [];
  const mediaListeners = [];
  const media = {
    matches: !!reduced,
    addEventListener(type, listener) {
      if (type === 'change') mediaListeners.push(listener);
    },
    removeEventListener(type, listener) {
      const index = mediaListeners.indexOf(listener);
      if (type === 'change' && index >= 0) mediaListeners.splice(index, 1);
    },
  };
  const ctx = {
    document: dom.document,
    AbortController,
    Promise,
    Date,
    console,
    setTimeout: dom.setTimeout,
    clearTimeout: dom.clearTimeout,
    matchMedia: () => media,
    requestAnimationFrame(callback) {
      frames.push(callback);
      return frames.length;
    },
    cancelAnimationFrame() {},
    ResizeObserver: class {
      constructor(callback) { this.callback = callback; this.connected = false; }
      observe() { this.connected = true; }
      disconnect() { this.connected = false; }
    },
    __PF_MOTION_DIAGNOSTICS__: true,
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  for (const file of ['ui_motion.js', 'ui_disclosure.js']) {
    vm.runInContext(fs.readFileSync(path.join(CHAT_UI, file), 'utf8'), ctx,
      {filename: file});
  }
  return {
    ctx,
    dom,
    media,
    flush() {
      const batch = frames.splice(0);
      batch.forEach(callback => callback(0));
    },
    pendingFrames() { return frames.length; },
  };
}

async function pump(e, turns) {
  for (let index = 0; index < (turns || 6); index++) {
    e.flush();
    await Promise.resolve();
  }
}

test('read callbacks always run before writes in a shared frame', async () => {
  const e = env(false);
  const order = [];
  const read = e.ctx.pfMotion.read(() => order.push('read'));
  const write = e.ctx.pfMotion.write(() => order.push('write'));
  eq(e.pendingFrames(), 1, 'read and write did not share a scheduled frame');
  e.flush();
  await Promise.all([read, write]);
  eq(order.join(','), 'read,write');
});

test('reduced motion reaches the same accessible terminal states', async () => {
  const e = env(true);
  const trigger = e.dom.document.createElement('button');
  const panel = e.dom.document.createElement('div');
  panel.scrollHeight = 120;
  e.dom.documentElement.append(trigger, panel);
  const controller = e.ctx.pfDisclosure.create({trigger, panel, open: false});

  assert(panel.hidden, 'closed panel was exposed initially');
  assert(panel.hasAttribute('inert'), 'closed panel was focusable initially');
  const opening = controller.set(true);
  eq(trigger.getAttribute('aria-expanded'), 'true');
  assert(!panel.hidden, 'opening did not expose content immediately');
  assert(!panel.hasAttribute('inert'), 'opening content stayed inert');
  eq(controller.state(), 'open', 'reduced motion did not settle synchronously');
  await pump(e);
  await opening;
  eq(controller.state(), 'open');
  eq(e.ctx.pfMotion.diagnostics().activeAnimations, 0);

  const focusable = e.dom.document.createElement('button');
  panel.appendChild(focusable);
  focusable.focus();
  const closing = controller.set(false);
  eq(trigger.getAttribute('aria-expanded'), 'false');
  assert(panel.hasAttribute('inert'), 'closing panel stayed interactive');
  eq(trigger.focusCount, 1, 'focus was not restored to the trigger');
  await pump(e);
  await closing;
  assert(panel.hidden, 'closed panel remained visually mounted');
  eq(panel.getAttribute('aria-hidden'), 'true');
  eq(controller.state(), 'closed');
});

test('a rapid reversal ignores the stale animation completion', async () => {
  const e = env(false);
  const trigger = e.dom.document.createElement('button');
  const panel = e.dom.document.createElement('div');
  panel.scrollHeight = 160;
  e.dom.documentElement.append(trigger, panel);
  const animations = [];
  panel.animate = function() {
    let resolve;
    let reject;
    const finished = new Promise((ok, fail) => { resolve = ok; reject = fail; });
    const animation = {
      finished,
      cancel() { reject(new Error('cancelled')); },
      finish() { resolve(); },
    };
    animations.push(animation);
    return animation;
  };
  const controller = e.ctx.pfDisclosure.create({trigger, panel, open: false});

  controller.set(true);
  await pump(e, 4);
  eq(animations.length, 1, 'opening animation was not started');
  controller.set(false);
  await pump(e, 4);
  eq(animations.length, 2, 'closing did not replace the opening channel');
  animations[1].finish();
  await pump(e, 4);
  await controller.settled();

  eq(controller.state(), 'closed');
  assert(panel.hidden, 'stale open completion won after reversal');
  eq(trigger.getAttribute('aria-expanded'), 'false');
  eq(e.ctx.pfMotion.diagnostics().activeAnimations, 0);
});

test('opening a closed disclosure animates from zero height', async () => {
  const e = env(false);
  const trigger = e.dom.document.createElement('button');
  const panel = e.dom.document.createElement('div');
  panel.scrollHeight = 120;
  e.dom.documentElement.append(trigger, panel);
  const keyframes = [];
  panel.animate = function(frames) {
    keyframes.push(frames);
    return {finished: Promise.resolve(), cancel() {}};
  };
  const controller = e.ctx.pfDisclosure.create({trigger, panel, open: false});

  const opening = controller.set(true);
  await pump(e, 8);
  await opening;

  eq(keyframes.length, 1, 'opening animation was not created');
  eq(keyframes[0][0].height, '0px');
  eq(keyframes[0][0].opacity, 0);
  eq(keyframes[0][1].height, '120px');
});

test('destroy aborts ownership and clears an active channel', async () => {
  const e = env(false);
  const trigger = e.dom.document.createElement('button');
  const panel = e.dom.document.createElement('div');
  panel.scrollHeight = 80;
  e.dom.documentElement.append(trigger, panel);
  panel.animate = function() {
    let reject;
    return {
      finished: new Promise((_resolve, fail) => { reject = fail; }),
      cancel() { reject(new Error('cancelled')); },
    };
  };
  const controller = e.ctx.pfDisclosure.create({trigger, panel, open: false});
  controller.set(true);
  await pump(e, 4);
  eq(e.ctx.pfMotion.diagnostics().activeAnimations, 1);
  controller.destroy();
  await pump(e, 2);
  eq(e.ctx.pfMotion.diagnostics().activeAnimations, 0);
  assert(controller.signal.aborted, 'controller owner was not aborted');
});

test('FLIP measures on opposite sides of the mutation and animates the inverse', async () => {
  const e = env(false);
  const element = e.dom.document.createElement('div');
  let left = 20;
  const keyframes = [];
  element.getBoundingClientRect = () => ({left, top: 0, height: 10, width: 100});
  element.animate = function(frames) {
    keyframes.push(frames);
    return {finished: Promise.resolve(), cancel() {}};
  };
  e.dom.documentElement.appendChild(element);

  const transition = e.ctx.pfMotion.flip(element, () => { left = 80; }, {duration: 180});
  await pump(e, 10);
  await transition;

  eq(keyframes.length, 1, 'FLIP did not create one animation');
  eq(keyframes[0][0].transform, 'translate(-60px,0px)');
  eq(keyframes[0][1].transform, 'translate(0,0)');
});

test('group FLIP morphs position and size in one shared frame', async () => {
  const e = env(false);
  let tiled = true;
  const elements = [0, 1].map(index => {
    const element = e.dom.document.createElement('div');
    element.getBoundingClientRect = () => ({
      left: tiled ? index * 110 : index * 210,
      top: 0,
      width: tiled ? 100 : 200,
      height: tiled ? 80 : 160,
    });
    element.frames = [];
    element.animate = function(frames) {
      this.frames.push(frames);
      return {finished: Promise.resolve(), cancel() {}};
    };
    e.dom.documentElement.appendChild(element);
    return element;
  });

  const transition = e.ctx.pfMotion.flipGroup(elements, () => { tiled = false; }, {
    duration: 300,
    scale: true,
  });
  await pump(e, 8);
  await transition;

  eq(elements[0].frames[0][0].transform, 'translate(0px,0px) scale(0.5,0.5)');
  eq(elements[1].frames[0][0].transform, 'translate(-100px,0px) scale(0.5,0.5)');
  eq(elements[1].frames[0][1].transform, 'translate(0,0) scale(1,1)');
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
