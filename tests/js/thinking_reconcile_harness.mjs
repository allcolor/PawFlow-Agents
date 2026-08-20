// Functional harness for the thinking preview → durable reconciliation in
// tasks/io/chat_ui/sse_state.js. Loads the real script into a VM with a
// minimal DOM stub and replays the REAL event sequence of the bug: the
// streamed preview is truncated by design (the emitter never flushes its
// final <250-char fragment) and a tool_call finalizes the live block before
// the durable thinking_content lands. Exits non-zero on any failure.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const source = readFileSync(
  join(root, 'tasks', 'io', 'chat_ui', 'sse_state.js'), 'utf-8');

function makeElement(tag) {
  return {
    tag, children: [], dataset: {}, style: {}, textContent: '',
    classList: { add() {}, remove() {}, contains() { return false; } },
    setAttribute() {}, removeAttribute() {},
    appendChild(child) { this.children.push(child); child.parentNode = this; },
    insertBefore(child) { this.children.push(child); child.parentNode = this; },
    remove() {
      this.removed = true;
      if (this.parentNode) {
        this.parentNode.children = this.parentNode.children.filter(c => c !== this);
      }
    },
    closest() { return null; },
    querySelector() { return null; },
  };
}

const messages = makeElement('div');
messages.id = 'messages';

const sandbox = {
  document: {
    createElement: makeElement,
    getElementById: (id) => (id === 'messages' ? messages : null),
    querySelector: () => null,
  },
  window: {},
  console,
  Date, JSON, Set, Map, String, Number, Array, Object, Math,
  setTimeout, clearTimeout,
  agentKey: (name) => String(name || '').toLowerCase(),
  t: (key, opts) => key + (opts ? JSON.stringify(opts) : ''),
  scrollBottom: () => {},
  escapeHtml: (s) => String(s),
  CSS: { escape: (s) => s },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'sse_state.js' });

let failures = 0;
function check(label, condition) {
  if (!condition) { failures += 1; console.error('FAIL:', label); }
  else console.log('ok:', label);
}

function thinkingBlocks() {
  return messages.children.filter(
    (el) => el.dataset && el.dataset.messageRole === 'thinking' && !el.removed);
}
function blockText(el) {
  const content = el.children.find((c) => c.tag === 'div');
  return content ? content.textContent : '';
}

// ── Scenario 1: tool_call finalizes the live preview BEFORE the durable
// thinking_content lands (the reported bug: truncated copy + duplicate). ──
const preview1 =
  'Je note deux points : le bug des pensées tronquées à corriger (possiblement dans la';
const full1 = preview1 + ' b221), et gh qui se trouve dans /home/pawflow/bin.';
sandbox.renderThinkingContent({ agent_name: 'claude', text: preview1 }, false);
sandbox.finalizeThinking('claude', 'tool_call');
sandbox.renderThinkingContent(
  { agent_name: 'claude', text: full1, msg_id: 'm1' }, true);
check('one single thinking block after reconcile',
      thinkingBlocks().length === 1);
check('the block shows the COMPLETE durable text',
      blockText(thinkingBlocks()[0]) === full1);

// ── Scenario 2: durable lands while the preview is still live (existing
// path — must keep working). ──
const preview2 = 'Second thought streamed';
const full2 = preview2 + ' with its unflushed tail.';
sandbox.renderThinkingContent({ agent_name: 'claude', text: preview2 }, false);
sandbox.renderThinkingContent(
  { agent_name: 'claude', text: full2, msg_id: 'm2' }, true);
sandbox.finalizeThinking('claude', 'token');
const blocks2 = thinkingBlocks();
check('second block appended (previous stays reconciled)',
      blocks2.length === 2);
check('live reconcile still extends to the full text',
      blockText(blocks2[1]) === full2);

// ── Scenario 3: interleaved blocks — the next block is already streaming
// when the previous block''s durable text arrives. ──
const preview3 = 'Third block partial';
const full3 = preview3 + ' now complete.';
sandbox.renderThinkingContent({ agent_name: 'claude', text: preview3 }, false);
sandbox.finalizeThinking('claude', 'tool_call');
const preview4 = 'Fourth block streaming';
sandbox.renderThinkingContent({ agent_name: 'claude', text: preview4 }, false);
sandbox.renderThinkingContent(
  { agent_name: 'claude', text: full3, msg_id: 'm3' }, true);
const blocks3 = thinkingBlocks();
check('durable of block 3 reconciles into ITS OWN block',
      blocks3.some((el) => blockText(el) === full3));
check('the still-live block 4 preview is untouched',
      blocks3.some((el) => blockText(el) === preview4));
check('no duplicated truncated copy remains',
      !blocks3.some((el) => blockText(el) === preview3));

// ── Scenario 4: done purges pending previews (no stale reconcile across
// turns). ──
sandbox.finalizeThinking('claude', 'done');
const before = thinkingBlocks().length;
sandbox.renderThinkingContent(
  { agent_name: 'claude', text: preview4 + ' from a NEW turn.', msg_id: 'm9' },
  true);
check('after done, a durable text creates a fresh block',
      thinkingBlocks().length === before + 1);

process.exit(failures ? 1 : 0);
