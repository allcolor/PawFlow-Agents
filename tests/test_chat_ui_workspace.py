import shutil
import subprocess
from pathlib import Path

import pytest

from chat_ui_testing import rendered_chat_html


CHAT_UI = Path("tasks/io/chat_ui")
WORKSPACE_JS = (CHAT_UI / "workspace.js").read_text(encoding="utf-8")
WORKSPACE_CSS = (CHAT_UI / "css/92_workspace.css").read_text(encoding="utf-8")
APPEARANCE_CSS = (CHAT_UI / "css/55_appearance.css").read_text(
    encoding="utf-8")
TABS_JS = (CHAT_UI / "tabs.js").read_text(encoding="utf-8")


def test_workspace_partial_owns_the_stage_and_composer_stays_outside():
    skeleton = (CHAT_UI / "templates/chat.html").read_text(encoding="utf-8")
    assert '{% include "chat/workspace.html" %}' in skeleton
    assert skeleton.index('{% include "chat/workspace.html" %}') < skeleton.index(
        '<div class="input-area">'
    )

    html = rendered_chat_html()
    for element_id in (
        "workspaceShell",
        "workspaceToolbar",
        "workspaceLayoutSelect",
        "workspaceScroller",
        "workspaceBoard",
        "tabContentChat",
        "tabContentOpenspace",
    ):
        assert f'id="{element_id}"' in html
    for layout in range(1, 7):
        assert f'<option value="{layout}"' in html


def test_workspace_layouts_persist_and_extend_horizontally():
    for layout in ("2: [2, 1]", "3: [3, 1]", "4: [2, 2]", "5: [3, 2]", "6: [3, 2]"):
        assert layout in WORKSPACE_JS
    assert "localStorage.setItem(_workspaceStorageKey, JSON.stringify(state))" in WORKSPACE_JS
    assert "JSON.parse(localStorage.getItem(_workspaceStorageKey)" in WORKSPACE_JS
    assert ".workspace-tiled .workspace-scroller" in WORKSPACE_CSS
    assert ".workspace-scroller.workspace-overflowing" in WORKSPACE_CSS
    assert "width: max-content" in WORKSPACE_CSS
    assert "grid-auto-flow: column" in WORKSPACE_CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_workspace_persists_and_restores_conversation_titles():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
let stored = null;
const context = {
  console,
  localStorage: {
    setItem: (key, value) => {
      if (key === 'pawflow.workspace.state.v2') stored = value;
    },
    getItem: key => key === 'pawflow.workspace.state.v2' ? stored : null,
  },
  document: {
    readyState: 'loading',
    addEventListener: () => {},
    getElementById: () => null,
    querySelectorAll: () => [],
  },
  matchMedia: () => ({ matches: false }),
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._workspaceSurfaces = {
  'webchat-A': {
    title: 'Conversation Alpha',
    conversationTitle: 'Conversation Alpha',
    type: 'webchat',
    conversationId: 'A',
  },
};
context._workspaceSaveState();
const state = JSON.parse(stored);
const saved = state.surfaces[0];
if (saved.title !== 'Conversation Alpha'
    || saved.conversationTitle !== 'Conversation Alpha') {
  throw new Error('workspace state dropped the persisted conversation title');
}
context._workspaceRestoredOrder = [];
context._workspaceRestoredSurfaces = {};
context._workspaceLoadState();
if (context.workspaceRestoredSurfaceTitle('webchat-A') !== 'Conversation Alpha') {
  throw new Error('workspace restore did not expose the persisted title');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "workspace.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_workspace_scrolls_only_beyond_the_visible_tile_capacity():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
function classes() {
  const values = new Set();
  return {
    add: (...names) => names.forEach(name => values.add(name)),
    remove: (...names) => names.forEach(name => values.delete(name)),
    toggle: (name, enabled) => enabled ? values.add(name) : values.delete(name),
    contains: name => values.has(name),
  };
}
function panel(tabId) {
  return {
    dataset: { tab: tabId },
    classList: classes(),
    querySelector: () => null,
    setAttribute: () => {},
  };
}
const shell = { dataset: {}, classList: classes() };
const scroller = {
  clientWidth: 900,
  clientHeight: 600,
  scrollLeft: 27,
  classList: classes(),
};
const board = { style: { setProperty: () => {} } };
const byId = {
  workspaceShell: shell,
  workspaceScroller: scroller,
  workspaceBoard: board,
};
const context = {
  console,
  localStorage: { setItem: () => {}, getItem: () => null },
  document: {
    readyState: 'loading',
    addEventListener: () => {},
    getElementById: id => byId[id] || null,
    querySelectorAll: () => [],
  },
  matchMedia: () => ({ matches: false }),
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._workspaceSurfaces = {
  a: { panel: panel('a') },
  b: { panel: panel('b') },
  c: { panel: panel('c') },
};
context._workspaceLayout = 3;
context._workspaceResize();
if (scroller.classList.contains('workspace-overflowing')) {
  throw new Error('three tiles overflowed a three-tile viewport');
}
if (scroller.scrollLeft !== 0) {
  throw new Error('non-overflowing board retained a stale horizontal offset');
}
context._workspaceSurfaces.d = { panel: panel('d') };
context._workspaceResize();
if (!scroller.classList.contains('workspace-overflowing')) {
  throw new Error('fourth tile did not enable overflow past capacity three');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "workspace.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_simple_layout_is_the_same_persistent_board_at_one_by_one():
    assert "pawflow.workspace.state.v2" in WORKSPACE_JS
    assert "shell.classList.toggle('workspace-tiled', true)" in WORKSPACE_JS
    assert "if (workspaceIsTiled() && panel" not in WORKSPACE_JS
    assert "if (!workspaceIsTiled() || !_workspaceSurfaces[tabId])" not in WORKSPACE_JS
    assert ".workspace-tiled .workspace-surface" in WORKSPACE_CSS
    assert "display: flex !important" in WORKSPACE_CSS
    assert "grid-auto-columns: var(--workspace-tile-width)" in WORKSPACE_CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_simple_board_runtime_inserts_scrolls_and_closes_by_visual_order():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
function classes() {
  const values = new Set();
  return {
    add: (...names) => names.forEach(name => values.add(name)),
    remove: (...names) => names.forEach(name => values.delete(name)),
    toggle: (name, enabled) => enabled ? values.add(name) : values.delete(name),
    contains: name => values.has(name),
  };
}
function panel(tabId, left) {
  return {
    dataset: { tab: tabId },
    classList: classes(),
    offsetLeft: left,
    offsetWidth: 500,
    parentNode: null,
    querySelector: () => null,
    setAttribute: () => {},
  };
}
const board = {
  children: [],
  style: { setProperty: () => {} },
  insertBefore(node, reference) {
    const old = this.children.indexOf(node);
    if (old !== -1) this.children.splice(old, 1);
    const index = reference ? this.children.indexOf(reference) : -1;
    if (index === -1) this.children.push(node);
    else this.children.splice(index, 0, node);
    node.parentNode = this;
  },
  appendChild(node) { this.insertBefore(node, null); },
};
const a = panel('a', 0);
const b = panel('b', 508);
const c = panel('c', 1016);
board.appendChild(a);
board.appendChild(b);
const shell = { dataset: {}, classList: classes() };
let scrollRequest = null;
const scroller = {
  clientWidth: 500,
  clientHeight: 400,
  scrollLeft: 0,
  classList: classes(),
  scrollTo: options => { scrollRequest = options; },
};
const byId = {
  workspaceBoard: board,
  workspaceShell: shell,
  workspaceScroller: scroller,
};
const context = {
  console,
  localStorage: { setItem: () => {}, getItem: () => null },
  document: {
    readyState: 'loading',
    addEventListener: () => {},
    getElementById: id => byId[id] || null,
    querySelectorAll: () => [],
  },
  matchMedia: () => ({ matches: false }),
  setTimeout: () => {},
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);

context._workspaceInsertAfter(board, c, a);
if (board.children.map(node => node.dataset.tab).join(',') !== 'a,c,b') {
  throw new Error('new surface was not inserted after the selected surface');
}
context._workspaceSurfaces = { a: { panel: a }, b: { panel: b }, c: { panel: c } };
context._workspaceSelectedTab = 'c';
context.workspaceSetLayout(1);
if (!shell.classList.contains('workspace-tiled')) {
  throw new Error('layout 1 did not activate the persistent board');
}
context.workspaceFocusSurface('c');
if (!scrollRequest || scrollRequest.left !== c.offsetLeft) {
  throw new Error('layout 1 focus did not reveal the whole selected tile');
}
context.workspaceUnregisterSurface('c');
if (context.workspaceSelectedTab() !== 'b') {
  throw new Error('closing the selected tile did not focus its right neighbour');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "workspace.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_surfaces_stay_mounted_and_support_targeted_insertion():
    assert "panel.classList.toggle('active', selected)" in WORKSPACE_JS
    assert "display: flex !important" in WORKSPACE_CSS
    assert "_workspaceInsertAfter(board, panel, anchor)" in WORKSPACE_JS
    assert "replaceChild" not in WORKSPACE_JS
    assert "workspaceArmTarget" in WORKSPACE_JS
    assert "workspaceClearTarget" in WORKSPACE_JS
    assert "switchTab(panel.dataset.tab)" in WORKSPACE_JS


def test_selected_tile_header_is_distinct_and_owns_persistent_reordering():
    assert (".workspace-surface.workspace-selected > .workspace-surface-header"
            in WORKSPACE_CSS)
    selected_header = WORKSPACE_CSS[
        WORKSPACE_CSS.index(
            ".workspace-surface.workspace-selected > .workspace-surface-header"
        ):
    ].split("}", 1)[0]
    assert "background:" in selected_header
    assert "border-bottom-color: var(--pf-accent)" in selected_header
    assert "header.draggable = true" in WORKSPACE_JS
    assert "event.target.closest('.workspace-surface-actions')" in WORKSPACE_JS
    assert "board.addEventListener('dragstart'" in WORKSPACE_JS
    assert "board.addEventListener('dragover'" in WORKSPACE_JS
    assert "board.addEventListener('drop'" in WORKSPACE_JS
    assert "function _workspaceMoveSurface" in WORKSPACE_JS
    move = WORKSPACE_JS[WORKSPACE_JS.index("function _workspaceMoveSurface"):]
    move = move[:move.index("\n}") + 2]
    assert "board.insertBefore" in move
    assert "_workspaceSaveState()" in move


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_workspace_surface_title_updates_the_visible_tile_header():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const titleNode = { textContent: 'Old title', title: '' };
const conversationNode = { textContent: 'Old conversation', title: '' };
const panel = {
  dataset: { tab: 'webchat-a', surfaceLabel: 'Old title' },
  querySelector: selector => selector.includes('workspace-surface-title') ? titleNode : null,
};
const toolPanel = {
  dataset: { tab: 'term-a', surfaceLabel: 'Terminal' },
  querySelector: selector => selector.includes('workspace-surface-conversation')
    ? conversationNode : null,
};
const context = {
  console,
  localStorage: { setItem: () => {}, getItem: () => null },
  document: {
    readyState: 'loading',
    addEventListener: () => {},
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  },
  matchMedia: () => ({ matches: false }),
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._workspaceSurfaces['webchat-a'] = {
  panel,
  title: 'Old title',
  type: 'webchat',
  conversationId: 'A',
};
context._workspaceSurfaces['term-a'] = {
  panel: toolPanel,
  title: 'Terminal',
  type: 'terminal',
  conversationId: 'A',
};
if (!context.workspaceSetSurfaceTitle('webchat-a', 'Conversation Alpha')) {
  throw new Error('surface title update was rejected');
}
if (titleNode.textContent !== 'Conversation Alpha'
    || titleNode.title !== 'Conversation Alpha'
    || panel.dataset.surfaceLabel !== 'Conversation Alpha') {
  throw new Error('visible tile header was not updated');
}
if (!context.workspaceSetConversationTitle('A', 'Conversation Alpha')) {
  throw new Error('conversation title update was rejected');
}
if (titleNode.textContent !== 'Conversation Alpha'
    || conversationNode.textContent !== 'Conversation Alpha'
    || conversationNode.title !== 'Conversation Alpha'
    || toolPanel.dataset.conversationTitle !== 'Conversation Alpha') {
  throw new Error('bound conversation was not shown on every tile header');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "workspace.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_tool_panels_register_as_equal_workspace_surfaces():
    for surface_type in ("terminal", "vscode", "desktop", "audio", "browser"):
        assert f"type: '{surface_type}'" in TABS_JS
    assert TABS_JS.count("workspaceRegisterSurface(panel") >= 6
    assert "workspaceOpenOpenspace" in WORKSPACE_JS
    assert "type: 'openspace'" in WORKSPACE_JS
    assert "type: 'webchat'" in WORKSPACE_JS
    assert "function _tabIsSelected(tabId)" in TABS_JS
    assert TABS_JS.count("const wasSelected = _tabIsSelected(tabId)") == 5


def test_terminal_tiles_pan_fixed_tmux_layout_independently_from_tmux_scrollback():
    terminal_js = (CHAT_UI / "terminal.js").read_text(encoding="utf-8")

    assert "terminal-layout-viewport" in TABS_JS
    assert "_scrollTerminalLayoutViewport(termViewport, event)" in TABS_JS
    assert "overflow: auto" in WORKSPACE_CSS
    assert "const screen = container.querySelector('.xterm-screen')" in terminal_js
    assert "container.style.height = height + 'px'" in terminal_js


def test_existing_desktop_surface_refreshes_its_capability_iframe():
    assert "const existingPanel = document.getElementById('tabContent_' + tabId)" in TABS_JS
    assert "iframe.replaceWith(nextIframe)" in TABS_JS
    assert "nextIframe.src = iframeSrc" in TABS_JS


def test_desktop_relay_picker_uses_the_conversation_binding_catalog():
    terminal_js = (CHAT_UI / "terminal.js").read_text(encoding="utf-8")
    get_relays = terminal_js[
        terminal_js.index("function _getRelays()"):
        terminal_js.index("function _relaySupportsLocal")
    ]

    assert "action$('list_resources')" in get_relays
    assert "data.relay_bindings" in get_relays
    assert "relay.connected !== false" in get_relays
    assert "action$('relay_list_available')" not in get_relays


def test_every_tile_header_can_maximize_its_surface_into_single_layout():
    assert "workspace-maximize-btn" in WORKSPACE_JS
    assert "function workspaceMaximizeSurface(tabId)" in WORKSPACE_JS
    assert "_workspaceRestoreLayout = _workspaceLayout" in WORKSPACE_JS
    assert "workspaceSetLayout(1)" in WORKSPACE_JS
    assert "switchTab(tabId)" in WORKSPACE_JS
    assert "workspace-maximized" in WORKSPACE_CSS
    assert "window.pfMotion.flipGroup(panels, apply" in WORKSPACE_JS
    assert "channel: 'workspace-layout'" in WORKSPACE_JS
    assert "duration: 300" in WORKSPACE_JS
    assert "scale: true" in WORKSPACE_JS


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_unmaximize_restores_the_previous_tiled_layout_at_runtime():
    harness = r"""
const fs = require('fs');
const vm = require('vm');
function classes() {
  const values = new Set();
  return {
    toggle: (name, enabled) => enabled ? values.add(name) : values.delete(name),
    contains: name => values.has(name),
  };
}
const shell = { dataset: {}, classList: classes() };
const panel = {
  classList: classes(),
  setAttribute: () => {},
  querySelector: () => null,
};
let selected = '';
const context = {
  console,
  localStorage: { setItem: () => {}, getItem: () => null },
  document: {
    readyState: 'loading',
    addEventListener: () => {},
    getElementById: id => id === 'workspaceShell' ? shell : null,
    querySelectorAll: () => [],
  },
  matchMedia: () => ({ matches: false }),
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context._workspaceSurfaces.term = { panel };
context._workspaceLayout = 4;
context.switchTab = tabId => { selected = tabId; };
if (!context.workspaceMaximizeSurface('term')) throw new Error('maximize failed');
if (context._workspaceLayout !== 1 || context._workspaceRestoreLayout !== 4) {
  throw new Error('previous layout was not retained');
}
if (!shell.classList.contains('workspace-maximized') || selected !== 'term') {
  throw new Error('restore control was not exposed');
}
if (!context.workspaceMaximizeSurface('term')) throw new Error('restore failed');
if (context._workspaceLayout !== 4 || context._workspaceRestoreLayout !== 0) {
  throw new Error('layout 4 was not restored');
}
if (shell.classList.contains('workspace-maximized')) {
  throw new Error('restore control stayed active');
}
"""
    result = subprocess.run(
        ["node", "-e", harness, str(CHAT_UI / "workspace.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_workspace_tiles_follow_configured_atmosphere_transparency():
    for selector in (
        ".workspace-shell",
        ".workspace-scroller",
        ".workspace-board",
    ):
        assert (
            f'html[data-pf-atmosphere="on"] {selector}'
            in APPEARANCE_CSS
        )
    tile_selector = (
        'html[data-pf-atmosphere="on"] .workspace-surface.tab-content'
    )
    tile_rule = APPEARANCE_CSS[
        APPEARANCE_CSS.index(tile_selector):
    ].split("}", 1)[0]
    assert "var(--pf-atmosphere-panel-opacity)" in tile_rule
    assert (
        'html[data-pf-atmosphere="on"] .workspace-surface-header'
        in APPEARANCE_CSS
    )
    webchat_selector = (
        'html[data-pf-atmosphere="on"] '
        '.conversation-workspace-surface.workspace-surface.tab-content'
    )
    webchat_rule = APPEARANCE_CSS[
        APPEARANCE_CSS.index(webchat_selector):
    ].split("}", 1)[0]
    assert "background: transparent !important" in webchat_rule
    assert "backdrop-filter: none" in webchat_rule


def test_mobile_tiled_workspace_shows_one_tile_with_taskbar_navigation():
    assert "matchMedia('(max-width: 700px)')" in WORKSPACE_JS
    mobile = WORKSPACE_CSS.split("@media (max-width: 700px)")[1]
    assert ".workspace-tiled .workspace-surface" in mobile
    assert "scroll-snap-align: center" in mobile
