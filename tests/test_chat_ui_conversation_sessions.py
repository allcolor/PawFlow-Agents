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

    assert "autoScroll:" in sessions
    assert "suppressTopLoadUntil:" in sessions
    assert "installMessagesRootHandlers(messages, session)" in sessions
    assert "function installMessagesRootHandlers" in markdown
    assert "_wrapConversationSessionCallback(session" in markdown


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
context._saveConversationSessionState(a);
context.focusConversationSession(b, { project: false });
context.selectedAgent = 'agent-b';
context.document.getElementById('status').textContent = 'status-b';
context._saveConversationSessionState(b);

const onA = context._wrapConversationSessionCallback(a, () => {
  const messages = context.document.getElementById('messages');
  if (messages !== a.messagesRoot) throw new Error('A did not own canonical messages');
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
    # Theme, cost badge, confirmations, context gauges and UI surfaces live
    # outside the tile and must follow a tile switch (loaded sessions only:
    # a fresh load rehydrates them in loadConversationSession).
    assert "if (session.loaded)" in project
    for hook in ("loadThemeSelector()", "hydrateContextUsage()",
                 "hydrateUsageCost()", "hydrateConfirmations()",
                 "loadUiSurfaces(session.conversationId)"):
        assert hook in project, hook
    assert "renderUsageCostBadge()" in project
    # A stale 'new conversation' fallback title re-resolves on focus.
    assert "_conversationTitle(session.conversationId, session.title)" in project
