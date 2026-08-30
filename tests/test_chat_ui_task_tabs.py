import shutil
import subprocess
from pathlib import Path

import pytest

from chat_ui_testing import rendered_chat_html

CHAT_UI = Path("tasks/io/chat_ui")


def test_task_tabs_module_registered_and_loads_after_its_deps():
    src = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    assert '"workspace.js"' in src
    assert '"task_tabs.js"' in src
    assert (CHAT_UI / "task_tabs.js").exists()

    # The registry exists before filtered views or OpenSpace can register
    # surfaces. Active-agent rows expose the filtered-view entry point.
    idx_workspace = src.index('"workspace.js"')
    idx_active = src.index('"active_agents.js"')
    idx_task_tabs = src.index('"task_tabs.js"')
    assert idx_workspace < idx_active < idx_task_tabs


def test_filtered_webchats_use_workspace_surfaces_not_the_legacy_drawer():
    html = rendered_chat_html()
    assert 'id="workspaceShell"' in html
    assert 'id="workspaceBoard"' in html
    assert 'id="taskTabDock"' not in html
    assert 'id="taskTabPanel"' not in html
    assert ".task-tab-panel.open" not in html
    assert ".task-tab-dock" not in html


def test_task_tabs_js_exposes_expected_api():
    src = (CHAT_UI / "task_tabs.js").read_text(encoding="utf-8")
    for fn in [
        "function openAgentView(",
        "function closeFilteredView(",
        "function openTaskTab(",
        "function closeTaskTab(",
        "function closeActiveTaskTab(",
        "function switchTaskTab(",
        "function filteredViewRoute(",
        "function activeFilteredViewRoute(",
        "function activeFilteredViewTargetAgent(",
        "function activateFilteredView(",
    ]:
        assert fn in src
    assert "window._taskTabsReset" in src
    assert "info.source" in src
    assert "node.cloneNode(true)" in src
    assert "new MutationObserver(_queueFilteredViewRender)" in src
    assert "_taskTabObserver.observe(source" in src
    assert "workspaceRegisterSurface(panel" in src
    assert "new EventSource" not in src
    assert "function _stopTaskTabObserverIfIdle()" in src
    assert "_taskTabObserver.disconnect()" in src
    assert "if (wasSelected && typeof switchTab === 'function')" in src


def test_filtered_tile_identity_and_source_are_conversation_scoped():
    src = (CHAT_UI / "task_tabs.js").read_text(encoding="utf-8")

    assert "function _filteredViewTabId(conversationId, agentName, taskId)" in src
    assert "_filteredViewToken(conversationId)" in src
    assert "conversationId: sourceConversationId" in src
    assert "source: sourceSession.messagesRoot" in src
    assert "conversationId: sourceConversationId" in src[
        src.index("workspaceRegisterSurface(panel"):
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_same_agent_filter_has_distinct_tile_ids_in_two_conversations():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const context = {
  console,
  encodeURIComponent,
  document: { addEventListener: () => {} },
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const a = context._filteredViewTabId('A', 'worker', '');
const b = context._filteredViewTabId('B', 'worker', '');
if (a === b || !a.includes('A') || !b.includes('B')) {
  throw new Error('filtered tile identity did not include conversation');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "task_tabs.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_filtered_webchats_match_tasks_agents_and_parallel_flash_blocks():
    src = (CHAT_UI / "task_tabs.js").read_text(encoding="utf-8")
    assert "node.dataset.taskId === taskId" in src
    assert "node.dataset.delegateTaskId === taskId" in src
    assert "'[data-task-id=\"' + safe" in src
    assert "node.dataset.agentName || node.dataset.agent" in src
    assert "function _filteredViewAgentValue" in src
    assert "if (direct) return direct === wanted" in src

    sse_state_src = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    assert "subEl.dataset.delegateTaskId = taskId" in sse_state_src
    assert "subEl.dataset.agentName = dstAgent" in sse_state_src

    render_src = (CHAT_UI / "messages_render.js").read_text(encoding="utf-8")
    assert "firstSubEl.dataset.delegateTaskId" in render_src
    assert "subEl.dataset.delegateTaskId = taskId" in render_src
    assert "subEl.dataset.agentName = subAgent" in render_src


def test_filtered_agent_sources_tag_thinking_tools_and_delegate_rows():
    task_tabs = (CHAT_UI / "task_tabs.js").read_text(encoding="utf-8")
    sse_state = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    sse_handlers = (CHAT_UI / "sse_handlers_a.js").read_text(encoding="utf-8")
    render = (CHAT_UI / "messages_render.js").read_text(encoding="utf-8")

    assert "_filteredViewPruneAgents(clone, info.agentName)" in task_tabs
    assert "details.dataset.agent = agent.toLowerCase()" in sse_state
    assert "thinking_content missing agent identity" in sse_state
    assert "tool_call event missing agent identity" in sse_handlers
    assert "tcEl.dataset.agent = tcAgent.toLowerCase()" in sse_handlers
    assert "_inner.dataset.agent = _from.toLowerCase()" in render
    assert "tool call missing agent identity" in render


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_shared_delegate_frames_are_scoped_to_one_request_and_pair_replies():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const context = { console };
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);

const requestOne = {
  msg_id: 'request-message-1',
  source: {type: 'agent_delegate', from: 'assistant', to: 'claude', task_id: 'task-1'},
};
const replyOne = {
  msg_id: 'reply-message-1',
  source: {type: 'agent_delegate', from: 'claude', to: 'assistant', task_id: 'task-1'},
};
const requestTwo = {
  msg_id: 'request-message-2',
  source: {type: 'agent_delegate', from: 'assistant', to: 'claude', task_id: 'task-2'},
};
const frameA = {
  dataset: {delegatePair: 'assistant::claude', delegateTaskId: 'task-1'},
};
const frameB = {
  dataset: {delegatePair: 'assistant::claude', delegateTaskId: 'task-1'},
};
const rootA = {querySelectorAll: () => [frameA]};
const rootB = {querySelectorAll: () => [frameB]};
const keyOne = context._delegateFrameKey(requestOne.source, requestOne.msg_id);
if (keyOne !== context._delegateFrameKey(replyOne.source, replyOne.msg_id)) {
  throw new Error('request and reverse-direction reply did not share a frame');
}
if (keyOne === context._delegateFrameKey(requestTwo.source, requestTwo.msg_id)) {
  throw new Error('a later request for the same agent pair reused the old frame');
}
if (context._delegateFrameForSource(replyOne.source, rootA) !== frameA
    || context._delegateFrameForSource(replyOne.source, rootB) !== frameB) {
  throw new Error('delegate lookup escaped its canonical transcript root');
}
if (context._delegateFrameForSource(requestTwo.source, rootA) !== null) {
  throw new Error('a new delegate request matched an older task frame');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "messages_render.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_simplified_delegate_keeps_the_complete_frame_as_its_timeline_row():
    render = (CHAT_UI / "messages_render.js").read_text(encoding="utf-8")
    existing = render[
        render.index("if (_existing) {"):
        render.index("const _arrow =", render.index("if (_existing) {"))
    ]
    thinking = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    thinking = thinking[
        thinking.index("if (_dsrc.type === 'agent_delegate'"):
        thinking.index("if (!_placed && data.task_id)")
    ]

    assert "return _existing;" in existing
    assert "return _inner;" not in existing
    assert "_delegateFrameForSource(_dsrc, _messageRoot)" in thinking
    assert "document.querySelector('[data-delegate-key=" not in thinking


def test_delegate_request_identity_reaches_reply_history_and_live_events():
    context = Path("tasks/ai/_agentctx_p3.py").read_text(encoding="utf-8")
    append = Path("tasks/ai/_alc_closures1.py").read_text(encoding="utf-8")
    completion = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    streaming = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
    cc_stream = Path("core/llm_providers/_cc_stream_turn.py").read_text(
        encoding="utf-8")

    assert '"task_id": st._ms.get("task_id", "")' in context
    assert '"task_id": _tm.get("task_id", "")' in append
    assert '"task_id": st._tm_end.get("task_id", "")' in completion
    assert ('_incoming_mode.get("task_id") == '
            '_running_mode.get("task_id")') in streaming
    assert '"task_id": _tm.get("task_id", "")' in cc_stream


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_agent_filter_prunes_foreign_and_unidentified_aggregate_rows():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
class Node {
  constructor(classes, dataset) {
    this.children = [];
    this.parentElement = null;
    this.dataset = dataset || {};
    const names = new Set(classes || []);
    this.classList = { contains: name => names.has(name) };
  }
  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    const index = siblings.indexOf(this);
    if (index !== -1) siblings.splice(index, 1);
    this.parentElement = null;
  }
}
const context = {
  console,
  encodeURIComponent,
  document: { addEventListener: () => {} },
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);

const root = new Node(['root']);
const task = root.appendChild(new Node(['task-block']));
const taskBody = task.appendChild(new Node(['task-block-body']));
const thinkingA = taskBody.appendChild(new Node(['thinking-block'], {agent: 'assistant'}));
const thinkingB = taskBody.appendChild(new Node(['thinking-block'], {agent: 'claude'}));
const toolA = taskBody.appendChild(new Node(['tool'], {agent: 'assistant'}));
const resultA = toolA.appendChild(new Node(['tc-result']));
const toolB = taskBody.appendChild(new Node(['tool'], {agent: 'claude'}));
const unidentified = taskBody.appendChild(new Node(['tool']));

const delegate = root.appendChild(new Node(['delegate-shared']));
const delegateBody = delegate.appendChild(new Node(['delegate-body']));
const delegateA = delegateBody.appendChild(new Node(['delegate-message'], {agent: 'assistant'}));
const delegateB = delegateBody.appendChild(new Node(['delegate-message'], {agent: 'claude'}));

context._filteredViewPrune(root, {agentName: 'assistant', taskId: ''});
if (taskBody.children.length !== 2
    || taskBody.children[0] !== thinkingA
    || taskBody.children[1] !== toolA) {
  throw new Error('task aggregate retained foreign or unidentified rows');
}
if (toolA.children.length !== 1 || toolA.children[0] !== resultA) {
  throw new Error('agent-owned untagged tool result did not inherit identity');
}
if (thinkingB.parentElement || toolB.parentElement || unidentified.parentElement) {
  throw new Error('foreign task content remained attached');
}
if (delegateBody.children.length !== 1 || delegateBody.children[0] !== delegateA) {
  throw new Error('shared delegate frame retained the other agent');
}
if (delegateB.parentElement) throw new Error('foreign delegate row remained attached');
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "task_tabs.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_add_msg_and_task_block_tag_dataset_task_id():
    render_src = (CHAT_UI / "messages_render.js").read_text(encoding="utf-8")
    assert "el.dataset.taskId = _msgTaskId" in render_src

    sse_state_src = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    assert "details.dataset.taskId = taskId" in sse_state_src
    assert "openTaskTab(" in sse_state_src


def test_active_agents_panel_can_open_agent_or_task_filtered_webchat():
    src = (CHAT_UI / "active_agents.js").read_text(encoding="utf-8")
    assert "openAgentView(" in src
    assert "info.taskId || ''" in src
    assert "openFilteredView" in src


def test_focused_filtered_webchat_selects_and_routes_to_its_agent():
    tabs_src = (CHAT_UI / "tabs.js").read_text(encoding="utf-8")
    attachments_src = (CHAT_UI / "attachments.js").read_text(encoding="utf-8")
    render_src = (CHAT_UI / "messages_render.js").read_text(encoding="utf-8")

    assert "activateFilteredView(tabId)" in tabs_src
    assert "activeFilteredViewTargetAgent()" in attachments_src
    assert "filteredTargetAgent || selectedAgent || ''" in attachments_src
    assert "el.dataset.targetAgent = _rowTargetAgent" in render_src


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_filtered_view_focus_selects_agent_at_runtime():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const selected = [];
const context = {
  console,
  encodeURIComponent,
  selectedAgent: 'assistant',
  currentTab: 'chat',
  document: {
    querySelectorAll: () => [],
    getElementById: () => null,
    addEventListener: () => {},
  },
};
context.window = context;
context.CSS = { escape: value => String(value) };
context.workspaceSelectedTab = () => context.currentTab;
context.workspaceFocusSurface = tabId => { context.currentTab = tabId; return true; };
context.cmdAgentSelect = name => {
  selected.push(name);
  context.selectedAgent = name;
  return Promise.resolve(true);
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._taskTabRegistry['filter-agent-worker'] = {
  tabId: 'filter-agent-worker', agentName: 'worker', taskId: '',
};
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
context.switchTab('filter-agent-worker');
if (context.currentTab !== 'filter-agent-worker') throw new Error('surface not focused');
if (selected.join(',') !== 'worker') throw new Error('filtered agent not selected');
if (context.activeFilteredViewTargetAgent() !== 'worker') throw new Error('composer route missing');
"""
    result = subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(CHAT_UI / "task_tabs.js"),
            str(CHAT_UI / "tabs.js"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_terminal_layout_wheel_and_existing_desktop_refresh_at_runtime():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
let replaced = null;
let currentTab = 'chat';
const oldIframe = {
  style: { cssText: 'flex:1;border:none;width:100%;height:100%;' },
  allow: 'clipboard-read; clipboard-write',
  replaceWith: value => { replaced = value; },
};
const panel = { querySelector: selector => selector === 'iframe' ? oldIframe : null };
const context = {
  console,
  document: {
    querySelectorAll: () => [],
    querySelector: () => null,
    getElementById: id => id === 'tabContent_desktop-MyWorkspace' ? panel : null,
    createElement: () => ({ style: {} }),
    addEventListener: () => {},
    body: { classList: { contains: () => false } },
  },
};
context.window = context;
context.CSS = { escape: value => String(value) };
context.workspaceFocusSurface = tabId => { currentTab = tabId; return true; };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context.addDesktopTab('MyWorkspace', '/vnc/fresh-capability/vnc.html');
if (!replaced || replaced.src !== '/vnc/fresh-capability/vnc.html') {
  throw new Error('desktop iframe did not refresh');
}
if (currentTab !== 'desktop-MyWorkspace') throw new Error('desktop did not stay selected');

const viewport = {
  scrollTop: 0, scrollLeft: 0,
  scrollHeight: 1000, clientHeight: 250,
  scrollWidth: 1200, clientWidth: 400,
};
let prevented = 0;
const down = {
  deltaX: 0, deltaY: 120, shiftKey: false,
  preventDefault: () => { prevented += 1; },
  stopPropagation: () => {},
};
if (!context._scrollTerminalLayoutViewport(viewport, down)) throw new Error('layout did not pan');
if (viewport.scrollTop !== 120 || prevented !== 1) throw new Error('wrong layout scroll');
viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
if (context._scrollTerminalLayoutViewport(viewport, down)) {
  throw new Error('wheel was not released to tmux at the layout boundary');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "tabs.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_conversation_switch_preserves_conversation_scoped_task_tabs():
    conversations = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")
    task_tabs = (CHAT_UI / "task_tabs.js").read_text(encoding="utf-8")
    resume = conversations[
        conversations.index("function resumeConv"):
        conversations.index("function loadConversationSession")
    ]

    assert "_taskTabsReset" not in resume
    assert "window._taskTabsReset" in task_tabs


def test_filtered_workspace_i18n_keys_present_in_every_locale():
    import json

    required = {
        "openInTaskTab",
        "openFilteredView",
        "workspaceCloseTitle",
        "workspaceFilterEmpty",
        "workspaceLayout",
        "workspaceLayoutTitle",
        "workspaceMaximizeTitle",
        "workspaceRestoreLayoutTitle",
        "workspaceSingle",
        "workspaceTargetArmed",
        "workspaceTargetTitle",
    }
    obsolete = {"closeTaskTabTitle", "taskTabEmpty", "taskTabPanelTitle"}
    for locale_file in (CHAT_UI / "i18n").glob("*.json"):
        if locale_file.name == "languages.json":
            continue
        data = json.loads(locale_file.read_text(encoding="utf-8"))
        missing = required - data.keys()
        assert not missing, f"{locale_file.name} missing {missing}"
        assert not obsolete & data.keys()
