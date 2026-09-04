'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CHAT_UI = path.join(__dirname, '..', '..', 'tasks', 'io', 'chat_ui');

function env() {
  const labels = {
    workflowRunActionPending: 'Working...',
    workflowRunActionSucceeded: 'Done',
    workflowRunActionFailed: 'Failed',
  };
  const ctx = {
    console,
    escapeHtml: value => String(value),
    _pfpAttr: value => String(value),
    t: key => labels[key] || key,
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(
    fs.readFileSync(path.join(CHAT_UI, 'workflow_run_inspector.js'), 'utf8'),
    ctx,
    {filename: 'workflow_run_inspector.js'},
  );
  return ctx;
}

function fakeButton() {
  const faces = ['idle', 'pending', 'success', 'error'].map(state => ({
    dataset: {actionFace: state},
    textContent: state,
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
  }));
  return {
    dataset: {},
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    querySelectorAll() { return faces; },
    faces,
  };
}

test('action buttons render stable idle, pending, success, and error faces', () => {
  const ctx = env();
  const html = ctx._workflowRunActionButtonHtml('retry', 'Retry safely', 'color:red;');
  assert.match(html, /data-action-state="idle"/);
  for (const state of ['idle', 'pending', 'success', 'error']) {
    assert.match(html, new RegExp('data-action-face="' + state + '"'));
  }
});

test('only the latest action generation owns completion', () => {
  const owner = env()._workflowRunActionOwner();
  const first = owner.begin('run-a');
  assert.equal(owner.isCurrent(first, 'run-a'), true);
  const second = owner.begin('run-a');
  assert.equal(owner.isCurrent(first, 'run-a'), false);
  assert.equal(owner.isCurrent(second, 'run-a'), true);
  assert.equal(owner.isCurrent(second, 'run-b'), false);
  owner.invalidate();
  assert.equal(owner.isCurrent(second, 'run-a'), false);
});

test('state changes expose exactly one accessible face', () => {
  const ctx = env();
  const button = fakeButton();
  ctx._workflowRunSetActionState(button, 'pending');
  assert.equal(button.dataset.actionState, 'pending');
  assert.equal(button.attributes['aria-busy'], 'true');
  assert.equal(button.attributes['aria-label'], 'pending');
  assert.deepEqual(
    button.faces.map(face => face.attributes['aria-hidden']),
    ['true', 'false', 'true', 'true'],
  );
  ctx._workflowRunSetActionState(button, 'error');
  assert.equal(button.attributes['aria-busy'], 'false');
  assert.equal(button.attributes['aria-label'], 'error');
});
