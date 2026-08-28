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
    assert "localStorage.setItem(_workspaceStorageKey, String(next))" in WORKSPACE_JS
    assert "localStorage.getItem(_workspaceStorageKey)" in WORKSPACE_JS
    assert ".workspace-tiled .workspace-scroller" in WORKSPACE_CSS
    assert "overflow-x: auto" in WORKSPACE_CSS
    assert "width: max-content" in WORKSPACE_CSS
    assert "grid-auto-flow: column" in WORKSPACE_CSS


def test_surfaces_stay_mounted_and_support_targeted_insertion():
    assert "panel.classList.toggle('active', selected)" in WORKSPACE_JS
    assert "display: flex !important" in WORKSPACE_CSS
    assert "board.insertBefore(panel, target)" in WORKSPACE_JS
    assert "replaceChild" not in WORKSPACE_JS
    assert "workspaceArmTarget" in WORKSPACE_JS
    assert "workspaceClearTarget" in WORKSPACE_JS
    assert "switchTab(panel.dataset.tab)" in WORKSPACE_JS


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
        '#tabContentChat.workspace-surface.tab-content'
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
