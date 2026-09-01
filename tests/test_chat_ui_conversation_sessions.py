import shutil
import subprocess
from pathlib import Path

import pytest


CHAT_UI = Path("tasks/io/chat_ui")
SESSIONS_JS = CHAT_UI / "conversation_sessions.js"


def test_conversation_session_module_is_loaded_before_sse_wiring():
    source = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")

    assert SESSIONS_JS.exists()
    assert source.index('"sse_state.js"') < source.index('"conversation_sessions.js"')
    assert source.index('"conversation_sessions.js"') < source.index('"sse_handlers_a.js"')


def test_sidebar_and_resume_route_through_workspace_conversation_sessions():
    conversations = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")

    assert "openWorkspaceConversation(c.conversation_id" in conversations
    resume = conversations[
        conversations.index("function resumeConv"):
        conversations.index("function loadConversationSession")
    ]
    assert "openWorkspaceConversation(cid" in resume
    assert "_clearConvState();" not in resume


def test_open_conversation_titles_stay_synchronized_with_sidebar_and_sse():
    conversations = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")
    sse = (CHAT_UI / "sse_handlers_b.js").read_text(encoding="utf-8")
    sessions = SESSIONS_JS.read_text(encoding="utf-8")

    assert "syncConversationSessionTitles(convs.concat(shared))" in conversations
    assert "updateConversationSessionTitle(cid, title)" in sse
    assert "function updateConversationSessionTitle" in sessions
    assert "workspaceSetConversationTitle(session.conversationId, title)" in sessions


def test_each_conversation_root_owns_scroll_state_and_handlers():
    sessions = SESSIONS_JS.read_text(encoding="utf-8")
    markdown = (CHAT_UI / "messages_markdown.js").read_text(encoding="utf-8")
    conversations = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")

    assert "autoScroll:" in sessions
    assert "scrollTop:" in sessions
    assert "suppressTopLoadUntil:" in sessions
    assert "session.scrollTop = session.messagesRoot.scrollTop" in sessions
    assert "session.messagesRoot.scrollTop = session.scrollTop" in sessions
    assert "installMessagesRootHandlers(messages, session)" in sessions
    assert "function installMessagesRootHandlers" in markdown
    assert "_wrapConversationSessionCallback(session" in markdown
    theme_settle = conversations[
        conversations.index("if (themeLoad && typeof themeLoad.then"):
        conversations.index("document.getElementById('input').focus()")]
    assert "refreshMessagesScrollMetrics(false)" in theme_settle
    assert "refreshMessagesScrollMetrics(true)" not in theme_settle


def test_conversation_session_lives_until_its_last_bound_surface_closes():
    sessions = SESSIONS_JS.read_text(encoding="utf-8")
    workspace = (CHAT_UI / "workspace.js").read_text(encoding="utf-8")

    assert "function releaseConversationSessionIfUnused" in sessions
    assert "ensureConversationSurface(session)" in sessions
    assert "releaseConversationSessionIfUnused(entry.conversationId)" in workspace


def test_composer_captures_its_conversation_before_async_work():
    attachments = (CHAT_UI / "attachments.js").read_text(encoding="utf-8")
    send = attachments[
        attachments.index("async function send()"):
        attachments.index("let _lastEscapeTime")
    ]

    assert "const sendSession = captureConversationSession()" in send
    assert "const sendConversationId" in send
    assert "_ensureSSEBeforeUserAction(sendConversationId)" in send
    assert "body.conversation_id = sendConversationId" in send
    assert send.index("const targetAgent") < send.index("await _ensureSSEBeforeUserAction")
    assert send.index("input.value = ''") < send.index("await _ensureSSEBeforeUserAction")


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_stale_resource_batch_cannot_replace_the_focused_conversation_panel():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
let focused = 'A';
let nextTimer = 0;
const timers = new Map();
const calls = [];
const rendered = [];
function observable(kind, params) {
  return { subscribe: callback => { calls.push({ kind, params, callback }); } };
}
const context = {
  console,
  conversationId: 'A',
  window: null,
  focusedConversationId: () => focused,
  setTimeout: callback => { const id = ++nextTimer; timers.set(id, callback); return id; },
  clearTimeout: id => timers.delete(id),
  action$: (kind, params) => observable(kind, params || {}),
  listServices$: (_type, _view, cid) => observable('list_services', { conversation_id: cid }),
  _withView: params => params,
};
context.window = context;
context._cachedTools = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._renderResourcesFromSSE = data => rendered.push(data.marker);

context.loadResources('A');
timers.get(nextTimer)();
const aCalls = calls.splice(0);
focused = 'B';
context.conversationId = 'B';
context.loadResources('B');
timers.get(nextTimer)();
const bCalls = calls.splice(0);

for (const call of aCalls) {
  if (call.kind === 'list_resources') call.callback({ marker: 'A' });
  else if (call.kind === 'list_services') call.callback({ services: [] });
  else call.callback({ packages: [] });
}
if (rendered.length) throw new Error('stale A resources repainted the B panel');

for (const call of bCalls) {
  if (call.kind === 'list_resources') call.callback({ marker: 'B' });
  else if (call.kind === 'list_services') call.callback({ services: [] });
  else call.callback({ packages: [] });
}
if (rendered.join(',') !== 'B') throw new Error('focused B resources did not render');
if (bCalls.some(call => call.params.conversation_id !== 'B')) {
  throw new Error('resource request was not explicitly bound to B');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "resources_render.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_sse_callbacks_are_bound_to_the_owning_conversation_session():
    source = (CHAT_UI / "sse.js").read_text(encoding="utf-8")

    assert "ensureConversationSession(cid)" in source
    assert "_wrapConversationSessionCallback(session" in source
    assert "session.eventSource" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_background_session_callback_restores_focused_state_and_dom():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
function classes() { return { toggle: () => {} }; }
function root(name) {
  return {
    id: name,
    dataset: { conversationLocalId: 'messages' },
    items: [],
    scrollTop: 0,
    querySelectorAll: () => [],
  };
}
const roots = [];
const titleUpdates = [];
const status = { id: 'status', textContent: '' };
const context = {
  console,
  document: {
    documentElement: { classList: classes() },
    getElementById: id => id === 'status' ? status : (roots.find(node => node.id === id) || null),
    querySelectorAll: () => [],
  },
  window: null,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  workspaceSetSurfaceTitle: (tabId, title) => titleUpdates.push([tabId, title]),
};
context.window = context;
Object.assign(context, {
  conversationId: null,
  sending: false,
  eventSource: null,
  pendingAgent: null,
  selectedAgent: '',
  sseRetryCount: 0,
  sseReconnectTimer: null,
  streams: {},
  permissionMode: 'default',
  nicknameMap: {},
  pendingFiles: [],
  lastSSEActivity: 0,
  serverMsgCount: 0,
  sseHealthTimer: null,
  resourcesTimer: null,
  currentOffset: 0,
  historyCursor: { offset: 0, before_msg_id: '' },
  hasMoreMessages: false,
  loadingMore: false,
  _replyTo: null,
  _seenMsgIds: new Set(),
  _liveCountedMsgIds: new Set(),
  _selectedMsgIds: new Set(),
  _histTaskBlocks: {},
  activeInteractions: {},
  _activeDoneAt: {},
  typingInterval: null,
  PAWFLOW_CHAT_VIEW_MODE: 'classic',
  simplifiedTurns: new Map(),
  _turnRuntime: new Map(),
  _turnOpen: null,
  _turnSeq: 0,
  _taskBlocks: {},
  _pendingToolResults: {},
  thinkingElements: {},
  delegateThinkingElements: {},
  _delegateGroups: {},
  _delegateSubBlocks: {},
  btwElements: {},
  btwTexts: {},
  _pendingThinkingPreviews: {},
  _sseCid: null,
  _sseOnReadyCallback: null,
  _sseCreatedAt: 0,
});
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);

const a = context._newConversationSession('A');
const b = context._newConversationSession('B');
a.messagesRoot = root('messages-a');
b.messagesRoot = root('messages-b');
roots.push(a.messagesRoot, b.messagesRoot);
context._conversationSessions.set('A', a);
context._conversationSessions.set('B', b);

context.updateConversationSessionTitle('A', 'Conversation Alpha');
if (a.title !== 'Conversation Alpha') {
  throw new Error('conversation session title was not retained');
}
if (titleUpdates.length !== 1
    || titleUpdates[0][0] !== a.surfaceId
    || titleUpdates[0][1] !== 'Conversation Alpha') {
  throw new Error('conversation title was not projected to its tile header');
}

context.focusConversationSession(a, { project: false });
context.selectedAgent = 'agent-a';
context.document.getElementById('status').textContent = 'status-a';
a.messagesRoot.scrollTop = 111;
context._saveConversationSessionState(a);
context.focusConversationSession(b, { project: false });
context.selectedAgent = 'agent-b';
context.document.getElementById('status').textContent = 'status-b';
b.messagesRoot.scrollTop = 222;
context._saveConversationSessionState(b);
a.messagesRoot.scrollTop = 999;

const onA = context._wrapConversationSessionCallback(a, () => {
  const messages = context.document.getElementById('messages');
  if (messages !== a.messagesRoot) throw new Error('A did not own canonical messages');
  if (messages.scrollTop !== 111) {
    throw new Error('A scroll position was not restored before its callback');
  }
  messages.items.push('event-a');
  context.selectedAgent = 'agent-a-updated';
  context.document.getElementById('status').textContent = 'working-a';
});
onA();

if (a.messagesRoot.items.join(',') !== 'event-a') {
  throw new Error('background event did not render in A');
}
if (context.document.getElementById('messages') !== b.messagesRoot) {
  throw new Error('focused transcript was not restored to B');
}
if (b.messagesRoot.scrollTop !== 222) {
  throw new Error('focused B scroll position was not restored after background A');
}
if (context.selectedAgent !== 'agent-b') {
  throw new Error('focused agent projection was not restored to B');
}
if (a.selectedAgent !== 'agent-a-updated') {
  throw new Error('A session state was not retained');
}
if (a.statusText !== 'working-a' || status.textContent !== 'status-b') {
  throw new Error('prompt status was not restored with the focused session');
}

context.focusConversationSession(a, { project: false });
const focusBFromA = context._wrapConversationSessionCallback(a, () => {
  context.focusConversationSession(b, { project: false });
});
focusBFromA();
if (context.focusedConversationSession() !== b || context.selectedAgent !== 'agent-b') {
  throw new Error('intentional focus change was restored over by the old callback');
}
if (a.selectedAgent !== 'agent-a-updated') {
  throw new Error('focus-changing callback corrupted its previous session');
}

a.panel = { remove: () => {}, querySelectorAll: () => [] };
context._workspaceSurfaces = {
  [a.surfaceId]: { conversationId: 'A' },
  'term-a': { conversationId: 'A' },
};
context.workspaceUnregisterSurface = tabId => {
  delete context._workspaceSurfaces[tabId];
};
context.closeConversationSession('A');
if (!context.getConversationSession('A')) {
  throw new Error('closing Webchat released a session still owned by a tool tile');
}
delete context._workspaceSurfaces['term-a'];
context.releaseConversationSessionIfUnused('A');
if (context.getConversationSession('A')) {
  throw new Error('session survived after its last bound surface closed');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(SESSIONS_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_taskbar_gets_one_entry_per_conversation_tile():
    sessions = SESSIONS_JS.read_text(encoding="utf-8")
    workspace = (CHAT_UI / "workspace.js").read_text(encoding="utf-8")

    # Every conversation surface ensures its own rail button; the canonical
    # claim repoints the permanent chat button instead of duplicating it.
    assert "workspaceEnsureTabButton(session.surfaceId" in sessions
    assert '.tab-btn[data-tab="chat"]' in sessions
    assert "chatBtn.dataset.tab = session.surfaceId" in sessions
    # Closing or releasing a conversation removes its rail entry.
    assert sessions.count("workspaceRemoveTabButton(session.surfaceId)") == 2
    # Title renames follow through to the rail button tooltip.
    assert workspace.count("railButton.title = title") == 2


def test_tile_focus_reprojects_out_of_tile_conversation_surfaces():
    sessions = SESSIONS_JS.read_text(encoding="utf-8")
    project = sessions[
        sessions.index("function _projectFocusedConversation"):
        sessions.index("function focusConversationSession")]
    # Theme CSS is global and must switch immediately even before a restored
    # session has loaded its transcript. The other remote surfaces rehydrate
    # after the session load completes.
    assert "if (session.loaded)" in project
    assert project.index("loadThemeSelector()") < project.index("if (session.loaded)")
    for hook in ("hydrateContextUsage()", "hydrateUsageCost()",
                 "hydrateConfirmations()", "loadUiSurfaces(session.conversationId)"):
        assert hook in project, hook
    assert "renderUsageCostBadge()" in project
    # A stale 'new conversation' fallback title re-resolves on focus.
    assert "_conversationTitle(session.conversationId, session.title)" in project


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_background_session_cannot_paint_shared_agent_surfaces():
    cmd_agent = (CHAT_UI / "cmd_agent.js").read_text(encoding="utf-8")
    active_agents = (CHAT_UI / "active_agents.js").read_text(encoding="utf-8")
    composer = (CHAT_UI / "file_mention.js").read_text(encoding="utf-8")
    assert "!canProjectConversationSharedSurfaces()) return;" in active_agents
    assert "!canProjectConversationSharedSurfaces()) return;" in composer
    assert "selectedAgent = data.active_agent || selectedAgent" in (
        CHAT_UI / "conversations.js").read_text(encoding="utf-8")

    harness = r"""
const fs = require('fs');
const vm = require('vm');
function node() { return { style: {}, innerHTML: '', textContent: '' }; }
const nodes = {
  activeAgentBadge: node(),
  ctxGaugeWrap: node(),
  ctxGaugeFill: node(),
  ctxGaugePct: node(),
};
let allowPaint = false;
let composerPaints = 0;
let grabs = 0;
const context = {
  console,
  window: null,
  selectedAgent: 'agent-b',
  document: { getElementById: id => nodes[id] || null },
  canProjectConversationSharedSurfaces: () => allowPaint,
  grabOnAgentSwitch: () => { grabs += 1; },
  updateComposerAgentBadge: () => { composerPaints += 1; },
  displayAgentName: value => value,
  escapeHtml: value => value,
  t: key => key,
};
context.window = context;
context._contextUsage = { 'agent-b': { used: 5, max: 10, pct: 0.5 } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context.updateActiveAgentBadge();
if (nodes.activeAgentBadge.innerHTML || nodes.ctxGaugeFill.style.height
    || composerPaints || grabs) {
  throw new Error('background session painted a shared agent surface');
}
allowPaint = true;
context.updateActiveAgentBadge();
if (!nodes.activeAgentBadge.innerHTML || nodes.ctxGaugeFill.style.height !== '50%'
    || composerPaints !== 1 || grabs !== 1) {
  throw new Error('focused session did not paint every shared agent surface');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "cmd_agent.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_focus_transfers_global_runtime_ownership_once():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const calls = [];
const context = {
  console,
  window: null,
  document: {},
  stopRealtimeMediaForConversationChange: () => calls.push('realtime-stop'),
  stopConversationTTSForConversationChange: () => calls.push('tts-stop'),
  openspaceSetConversationOwner: cid => calls.push('openspace:' + cid),
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const a = { conversationId: 'A' };
const b = { conversationId: 'B' };
context._conversationActiveSession = a;
context._conversationFocusedSession = a;
context._transferConversationRuntimeOwnership(a, b);
if (calls.join(',') !== 'realtime-stop,tts-stop,openspace:B') {
  throw new Error('focus did not transfer every global runtime: ' + calls.join(','));
}
context._transferConversationRuntimeOwnership(b, b);
if (calls.length !== 3) throw new Error('same-session focus released its runtimes');
"""
    result = subprocess.run(
        ["node", "-e", harness, str(SESSIONS_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_stt_transcript_stays_with_recording_conversation_after_focus_change():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const inputs = {
  A: { value: '', style: {}, scrollHeight: 20, dispatchEvent: () => {} },
  B: { value: '', style: {}, scrollHeight: 20, dispatchEvent: () => {} },
};
const sessions = { A: { conversationId: 'A' }, B: { conversationId: 'B' } };
const sends = [];
let request = null;
let pending = null;
const context = {
  console,
  window: null,
  conversationId: 'B',
  selectedAgent: 'agent',
  document: {
    hidden: false,
    addEventListener: () => {},
    getElementById: id => id === 'input' ? inputs[context.conversationId] : null,
  },
  localStorage: { getItem: () => null, removeItem: () => {} },
  Event: function Event() {},
  addMsg: (_kind, text) => { throw new Error(text); },
  send: () => sends.push(context.conversationId),
  captureConversationSession: () => sessions[context.conversationId],
  getConversationSession: cid => sessions[cid] || null,
  withConversationSession: (session, callback) => {
    const previous = context.conversationId;
    context.conversationId = session.conversationId;
    try { return callback(); } finally { context.conversationId = previous; }
  },
  action$: (action, args) => ({ subscribe: (next, error) => {
    request = { action, args };
    pending = { next, error };
  }}),
  setTimeout,
  clearTimeout,
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._convSttBlobToBase64 = async () => 'YXVkaW8=';
const recording = {
  session: sessions.A,
  conversationId: 'A',
  config: { service: 'stt', language: '', autoSend: true },
  inputWasEmpty: true,
};
(async () => {
  await context._convSttTranscribeBlob({ size: 4, type: 'audio/webm' }, recording);
  if (!request || request.action !== 'stt_transcribe'
      || request.args.conversation_id !== 'A') {
    throw new Error('transcription request escaped recording conversation A');
  }
  pending.next({ text: 'hello from A' });
  if (inputs.A.value !== 'hello from A' || inputs.B.value !== '') {
    throw new Error('transcript was inserted into the focused conversation B');
  }
  if (sends.join(',') !== 'A' || context.conversationId !== 'B') {
    throw new Error('auto-send or focus restoration used the wrong conversation');
  }
})().catch(err => { console.error(err); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "conversation_stt.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_openspace_ignores_background_conversation_sse():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
function eventSource() {
  const listeners = {};
  return {
    listeners,
    addEventListener: (type, callback) => { listeners[type] = callback; },
  };
}
let ensured = 0;
const context = {
  console,
  window: null,
  _osSeedConvId: 'B',
  _osActive: false,
  _osEventAgent: () => 'agent',
  _osEnsureAgent: () => { ensured += 1; return { state: 'idle' }; },
  _osSetState: () => {},
  _osStreamBubble: () => {},
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const a = eventSource();
context.openspaceWireSSE(a, 'A');
a.listeners.token({ data: JSON.stringify({ content: 'wrong room' }) });
if (ensured !== 0) throw new Error('background A event mutated OpenSpace B');
const b = eventSource();
context.openspaceWireSSE(b, 'B');
b.listeners.token({ data: JSON.stringify({ content: 'right room' }) });
if (ensured !== 1) throw new Error('owner B event did not reach OpenSpace');
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "openspace_runtime.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_openspace_background_history_is_cached_without_stealing_focus():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const resets = [];
const styles = [];
const context = {
  console,
  window: null,
  _osSeedConvId: 'B',
  _osHistoryByConversation: new Map(),
  _osAgents: new Map(),
  openspaceResetTransient: () => resets.push('reset'),
  _osApplyRoomStyle: cid => styles.push(cid),
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context.openspaceResetTransient = () => resets.push('reset');
context._osApplyRoomStyle = cid => styles.push(cid);
context.openspaceSeedHistory([], 'A');
if (context._osSeedConvId !== 'B' || resets.length || styles.length) {
  throw new Error('background history stole OpenSpace ownership');
}
if (!context._osHistoryByConversation.has('A')) {
  throw new Error('background history was not cached for a later focus');
}
context.openspaceSetConversationOwner('A');
if (context._osSeedConvId !== 'A' || resets.length !== 1 || styles.join(',') !== 'A') {
  throw new Error('focus did not project the cached OpenSpace owner');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "openspace_agents.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_tts_focus_transfer_stops_both_pipelines_in_owner_context():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const deletes = [];
let pauses = 0;
const context = {
  console,
  window: null,
  conversationId: 'A',
  document: {
    hidden: false,
    addEventListener: () => {},
    getElementById: () => null,
    querySelectorAll: () => [],
  },
  localStorage: { getItem: () => null },
  action$: (action, args) => ({ subscribe: () => {
    if (action === 'tts_delete') deletes.push(args);
  }}),
  setTimeout,
  clearTimeout,
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._convTtsEnabled = true;
context._convTtsOwnerConversationId = 'A';
context._convTtsCurrentAudio = { pause: () => { pauses += 1; } };
context._convTtsCurrentAudioFileId = 'live-current';
context._convTtsCurrentAudioConversationId = 'A';
context._convTtsPendingAudio = {
  1: { file_id: 'live-pending', conversation_id: 'A' },
};
context._convTtsOneShotOwnerConversationId = 'A';
context._convTtsOneShotAudio = { pause: () => { pauses += 1; } };
context._convTtsOneShotFileId = 'one-current';
context._convTtsOneShotFileConversationId = 'A';
context._convTtsOneShotPendingAudio = {
  1: { file_id: 'one-pending', conversation_id: 'A' },
};
context.stopConversationTTSForConversationChange();
if (pauses !== 2 || context._convTtsEnabled
    || context._convTtsOwnerConversationId
    || context._convTtsOneShotOwnerConversationId) {
  throw new Error('TTS pipelines survived focus transfer');
}
if (deletes.length !== 4 || deletes.some(row => row.conversation_id !== 'A')) {
  throw new Error('TTS cleanup escaped owner A: ' + JSON.stringify(deletes));
}
context._convTtsEnabled = true;
context._convTtsOwnerConversationId = 'B';
context.conversationId = 'A';
context._convTtsQueue = [];
context.conversationTTSOnMessage({ role: 'assistant', content: 'background A' });
if (context._convTtsQueue.length) throw new Error('background A entered owner B queue');

const oldLive = { play: () => Promise.resolve() };
const newLive = {};
context._convTtsRunId = 10;
context._convTtsEnabled = true;
context._convTtsPlayUrl({
  audio: oldLive, url: 'old-live', file_id: '', conversation_id: 'A',
});
context._convTtsRunId = 11;
context._convTtsCurrentAudio = newLive;
context._convTtsPlaying = true;
oldLive.onended();
if (context._convTtsCurrentAudio !== newLive || !context._convTtsPlaying) {
  throw new Error('stale live playback callback mutated the new TTS run');
}

const oldOneShot = { play: () => Promise.resolve() };
const newOneShot = {};
context._convTtsOneShotRunId = 20;
context._convTtsPlayOneShot({
  audio: oldOneShot, url: 'old-one', file_id: '', conversation_id: 'A',
}, 20, () => { throw new Error('stale one-shot advanced the new queue'); });
context._convTtsOneShotRunId = 21;
context._convTtsOneShotAudio = newOneShot;
context._convTtsOneShotPlaying = true;
oldOneShot.onended();
if (context._convTtsOneShotAudio !== newOneShot || !context._convTtsOneShotPlaying) {
  throw new Error('stale one-shot callback mutated the new TTS run');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "conversation_tts.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_voice_and_livekit_starts_are_owned_and_cancellable():
    voice = (CHAT_UI / "conversation_voice.js").read_text(encoding="utf-8")
    livekit = (CHAT_UI / "conversation_livekit.js").read_text(encoding="utf-8")
    sse = (CHAT_UI / "sse.js").read_text(encoding="utf-8")

    assert "var _voiceOwnerConversationId = ''" in voice
    assert "var _voiceStartGeneration = 0" in voice
    assert "_voiceStartStillOwned(cid, generation)" in voice
    assert "if (_voiceWs !== ws || _voiceOwnerConversationId !== cid) return" in voice
    assert "var _lkOwnerConversationId = ''" in livekit
    assert "var _lkGeneration = 0" in livekit
    assert "_lkStopServerSession(payload)" in livekit
    assert "_lkOwnerConversationId !== ownerId" in livekit
    assert "_lkWireSSE(cid)" in sse


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_voice_capture_commits_only_after_ownership_check():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function stream(name) {
  const track = { stopped: false, stop() { this.stopped = true; } };
  return { name, track, getTracks: () => [track] };
}
class AudioContext {
  constructor() { this.state = 'running'; this.sampleRate = 48000; this.destination = {}; }
  createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
  createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
  close() {}
}
const requestA = deferred();
const requestB = deferred();
const requests = [requestA, requestB];
const context = {
  console,
  window: null,
  document: { addEventListener: () => {}, getElementById: () => null },
  navigator: { mediaDevices: { getUserMedia: () => requests.shift().promise } },
  localStorage: { getItem: () => null },
  AudioContext,
  setTimeout,
  clearTimeout,
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const streamA = stream('A');
const streamB = stream('B');
context._voiceOwnerConversationId = 'A';
context._voiceStartGeneration = 1;
const pendingA = context._voiceStartCapture('A', 1);
context._voiceOwnerConversationId = 'B';
context._voiceStartGeneration = 2;
const pendingB = context._voiceStartCapture('B', 2);
(async () => {
  requestB.resolve(streamB);
  if (!await pendingB || context._voiceStream !== streamB) {
    throw new Error('owned B capture was not committed');
  }
  requestA.resolve(streamA);
  if (await pendingA || !streamA.track.stopped) {
    throw new Error('stale A capture was not rejected and released');
  }
  if (context._voiceStream !== streamB || streamB.track.stopped) {
    throw new Error('stale A capture replaced or stopped B');
  }
})().catch(err => { console.error(err); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "conversation_voice.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_cancelled_livekit_start_cannot_stop_the_new_owner():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
let rejectFetch;
let errors = 0;
const context = {
  console,
  window: null,
  document: {
    head: { appendChild: () => {} },
    body: { appendChild: () => {} },
    getElementById: () => null,
    querySelector: () => null,
    createElement: () => ({}),
  },
  fetch: () => new Promise((_resolve, reject) => { rejectFetch = reject; }),
  addMsg: () => { errors += 1; },
  _voiceT: (_key, fallback) => fallback,
  _voiceSetState: () => {},
  _voiceRemoveCaptions: () => {},
  _voiceHideOverlay: () => {},
  _voiceUpdateButton: () => {},
  setImmediate,
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._lkLoadSdk = () => Promise.resolve();
const pendingA = context.startLiveKitVoiceMode('A', { id: 'livekit' });
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  context._lkGeneration += 1;
  context._lkOwnerConversationId = 'B';
  context._lkActive = true;
  rejectFetch(new Error('cancelled A'));
  await pendingA;
  if (errors || !context._lkActive || context._lkOwnerConversationId !== 'B') {
    throw new Error('cancelled A start mutated active owner B');
  }
})().catch(err => { console.error(err); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "conversation_livekit.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
