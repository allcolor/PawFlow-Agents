"""Contracts for the native WebChat motion and projection foundation."""

import shutil
import subprocess
from pathlib import Path

import pytest

from chat_ui_testing import rendered_chat_html


ROOT = Path(__file__).resolve().parents[1]
CHAT_UI = ROOT / "tasks" / "io" / "chat_ui"
SPEC = ROOT / "tests" / "js" / "ui_motion_spec.js"
FLOATING_SPEC = ROOT / "tests" / "js" / "ui_floating_layer_spec.js"
RESOURCES_SPEC = ROOT / "tests" / "js" / "resources_patch_spec.js"
WORKFLOW_ACTION_SPEC = ROOT / "tests" / "js" / "workflow_run_inspector_spec.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_motion_and_disclosure_behaviour():
    proc = subprocess.run(
        ["node", str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )
    assert proc.returncode == 0, "JS suite failed:\n" + proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_floating_layer_behaviour():
    proc = subprocess.run(
        ["node", str(FLOATING_SPEC)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )
    assert proc.returncode == 0, "JS suite failed:\n" + proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_incremental_resources_behaviour():
    proc = subprocess.run(
        ["node", str(RESOURCES_SPEC)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )
    assert proc.returncode == 0, "JS suite failed:\n" + proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_workflow_action_state_behaviour():
    proc = subprocess.run(
        ["node", str(WORKFLOW_ACTION_SPEC)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=180,
    )
    assert proc.returncode == 0, "JS suite failed:\n" + proc.stdout + proc.stderr


def test_motion_assets_are_registered_before_consumers():
    source = (ROOT / "tasks" / "io" / "serve_chat_ui.py").read_text(encoding="utf-8")
    for name in (
        "ui_motion.js",
        "ui_disclosure.js",
        "ui_projection.js",
        "ui_floating_layer.js",
        "resources_patch.js",
    ):
        assert (CHAT_UI / name).is_file()
        assert f'"{name}"' in source
    assert source.index('"ui_motion.js"') < source.index('"ui_disclosure.js"')
    assert source.index('"ui_disclosure.js"') < source.index('"turn_view.js"')
    assert source.index('"ui_projection.js"') < source.index('"openspace_scene.js"')
    assert source.index('"ui_projection.js"') < source.index('"task_tabs.js"')
    assert source.index('"ui_floating_layer.js"') < source.index('"tooltips.js"')
    assert source.index('"resources.js"') < source.index('"resources_patch.js"')
    assert source.index('"resources_patch.js"') < source.index('"resources_render.js"')
    assert source.index('"00_base.css"') < source.index('"05_motion.css"')
    assert source.index('"05_motion.css"') < source.index('"10_chrome.css"')
    assert 'href="/chat/js/css/05_motion.css?' in rendered_chat_html(inline_css=False)


def test_migrated_hot_paths_have_no_full_projection_clear_or_click_layout_read():
    task_tabs = (CHAT_UI / "task_tabs.js").read_text(encoding="utf-8")
    openspace = (CHAT_UI / "openspace_scene.js").read_text(encoding="utf-8")
    turn_view = (CHAT_UI / "turn_view.js").read_text(encoding="utf-8")

    assert "body.innerHTML = ''" not in task_tabs
    assert "_osProjectedMessages.innerHTML = ''" not in openspace
    assert "_queueFilteredViewRender" not in task_tabs
    assert "_osQueueMessageProjection" not in openspace

    click_path = turn_view[
        turn_view.index("function _turnSetExpanded"):
        turn_view.index("function _turnActivateTab")
    ]
    tab_path = turn_view[
        turn_view.index("function _turnActivateTab"):
        turn_view.index("function _turnQueuePanelScroll")
    ]
    for source in (click_path, tab_path):
        assert "scrollHeight" not in source
        assert "clientHeight" not in source
        assert "getBoundingClientRect" not in source


def test_all_non_message_projection_rows_have_explicit_durable_keys():
    projection = (CHAT_UI / "ui_projection.js").read_text(encoding="utf-8")
    conversations = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")
    sse_state = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    dialogs = (CHAT_UI / "dialogs.js").read_text(encoding="utf-8")

    assert "['projection', node.dataset.projectionKey]" in projection
    assert "details.dataset.projectionKey = 'task:' + blockKey" in conversations
    assert "details.dataset.projectionKey = 'task:' + blockKey" in sse_state
    assert "el.dataset.projectionKey = eventId" in dialogs
    assert "data.msg_id || data.message_id || data.event_id" in dialogs


def test_reduced_motion_tokens_remove_temporal_work():
    css = (CHAT_UI / "css" / "05_motion.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    for token in (
        "--pf-motion-fast",
        "--pf-motion-standard",
        "--pf-motion-disclosure",
        "--pf-motion-layout",
        "--pf-motion-chrome",
        "--pf-motion-rail",
        "--pf-motion-exit",
    ):
        assert f"{token}: 0ms;" in css


def test_live_cues_do_not_retain_permanent_will_change():
    messages = (CHAT_UI / "css" / "20_messages.css").read_text(encoding="utf-8")
    openspace = (CHAT_UI / "css" / "60_openspace.css").read_text(encoding="utf-8")
    start = messages.index(".simple-turn-cue {")
    cue_rule = messages[start:messages.index(".simple-turn-block.expanded", start)]

    assert "will-change" not in cue_rule
    assert "will-change" not in openspace


def test_small_position_controls_animate_with_transforms():
    messages = (CHAT_UI / "css" / "20_messages.css").read_text(encoding="utf-8")
    chrome = (CHAT_UI / "css" / "10_chrome.css").read_text(encoding="utf-8")
    state = (CHAT_UI / "state.js").read_text(encoding="utf-8")

    assert "transition: left" not in messages
    assert ".view-menu-item.active .vmi-switch::after { transform: translateX(14px); }" in messages
    assert "transition: left" not in chrome
    assert "translate(var(--pf-sidebar-toggle-x, 0px), -50%)" in chrome
    assert "btn.style.setProperty('--pf-sidebar-toggle-x'" in state


def test_sidebar_uses_the_same_whole_rail_slide_as_the_taskbar():
    state = (CHAT_UI / "state.js").read_text(encoding="utf-8")
    base = (CHAT_UI / "css" / "00_base.css").read_text(encoding="utf-8")
    chrome = (CHAT_UI / "css" / "10_chrome.css").read_text(encoding="utf-8")

    toggle = state[state.index("function toggleSidebar()"):]
    toggle = toggle[:toggle.index("document.addEventListener")]
    assert "_setSidebarCollapsed(!current, true)" in toggle
    assert "classList.toggle('collapsed')" not in toggle
    assert "window.pfMotion.replace(shell, 'sidebar-rail'" in state
    assert "_SIDEBAR_RAIL_DURATION = 900" in state
    assert "window.pfMotion.flip(main, apply" not in state
    assert ".sidebar-shell.collapsed { transform: translateX(-100%);" in base
    assert ".sidebar.collapsed > * { display: none; }" not in base
    assert ".sidebar-toggle { position: absolute;" in chrome
    assert "transition: width" not in base
    assert "transition: flex-grow" not in base


def test_resource_acquisition_is_generation_owned_and_deduplicated():
    render = (CHAT_UI / "resources_render.js").read_text(encoding="utf-8")

    assert "requestKey === _resourcesPendingKey" in render
    assert "function refreshResources(targetConversationId)" in render
    assert "parameters: _paramsData.parameters || []" in render
    assert "secrets: _paramsData.secrets || []" in render
    render_body = render[render.index("function _renderResourcesData"):]
    assert "action$('list_params_secrets'" not in render_body


def test_context_menus_use_shared_lifecycle_without_delayed_document_listeners():
    floating = (CHAT_UI / "ui_floating_layer.js").read_text(encoding="utf-8")
    tooltip = (CHAT_UI / "tooltips.js").read_text(encoding="utf-8")
    action_dock = (CHAT_UI / "css" / "95_action_dock.css").read_text(encoding="utf-8")
    owners = (
        "conversations_menu.js",
        "conversations_share.js",
        "file_viewer.js",
        "files_panel.js",
        "plans_panel.js",
        "resources_create_dialogs.js",
        "resources_flow_templates.js",
        "resources_menus.js",
        "resources_service_dialogs.js",
        "services.js",
        "themes.js",
    )

    assert "root._positionMenu = function(menu, event)" in floating
    assert "placement: verticalDock ? 'left' : 'top'" in tooltip
    assert "setDescribedBy(target, true)" in tooltip
    assert "const grouped = activeTarget" in tooltip
    assert "window.addEventListener('scroll', hideTooltip" not in tooltip
    assert "transition: opacity .12s ease, transform .12s ease, visibility .12s" not in action_dock
    for filename in owners:
        source = (CHAT_UI / filename).read_text(encoding="utf-8")
        assert "setTimeout(() => document.addEventListener('click'" not in source
