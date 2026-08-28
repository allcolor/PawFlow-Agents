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
    assert "document.getElementById('messages')" in src
    assert "node.cloneNode(true)" in src
    assert "new MutationObserver(_queueFilteredViewRender)" in src
    assert "workspaceRegisterSurface(panel" in src
    assert "new EventSource" not in src
    assert "function _stopTaskTabObserverIfIdle()" in src
    assert "_taskTabObserver.disconnect()" in src
    assert "if (wasSelected && typeof switchTab === 'function')" in src


def test_filtered_webchats_match_tasks_agents_and_parallel_flash_blocks():
    src = (CHAT_UI / "task_tabs.js").read_text(encoding="utf-8")
    assert "node.dataset.taskId === taskId" in src
    assert "node.dataset.delegateTaskId === taskId" in src
    assert "'[data-task-id=\"' + safe" in src
    assert "node.dataset.agentName || node.dataset.agent" in src
    assert "'[data-agent-name], [data-agent], [data-target-agent]'" in src

    sse_state_src = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    assert "subEl.dataset.delegateTaskId = taskId" in sse_state_src
    assert "subEl.dataset.agentName = dstAgent" in sse_state_src

    render_src = (CHAT_UI / "messages_render.js").read_text(encoding="utf-8")
    assert "firstSubEl.dataset.delegateTaskId" in render_src
    assert "subEl.dataset.delegateTaskId = taskId" in render_src
    assert "subEl.dataset.agentName = subAgent" in render_src


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


def test_conversation_switch_resets_task_tabs():
    src = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")
    assert "window._taskTabsReset" in src


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
