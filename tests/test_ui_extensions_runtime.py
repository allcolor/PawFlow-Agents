"""Structural tests for the PawFlow UI extension runtime (ui.v1).

Phase 1 ships the browser-side plumbing only: a `pawflow` API surface,
slot containers in the HTML template, hook firing points in existing JS
modules, and an empty bootstrap manifest. PFP integration (object type,
asset serving, server handlers) lands in phase 2.
"""

from pathlib import Path

import pytest

_CHAT_UI = Path("tasks/io/chat_ui")


@pytest.fixture(scope="module")
def ext_runtime_src():
    return (_CHAT_UI / "ext_runtime.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def serve_chat_ui_src():
    return Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template_src():
    return (_CHAT_UI / "template.html").read_text(encoding="utf-8")


# ── ext_runtime.js public surface ────────────────────────────────────────────────

UI_API_VERSION = "ui.v1"


def test_ext_runtime_file_exists():
    assert (_CHAT_UI / "ext_runtime.js").is_file()


def test_ext_runtime_exposes_pawflow_namespace(ext_runtime_src):
    assert "window.pawflow = {" in ext_runtime_src
    assert "window._pawflowExtRuntime" in ext_runtime_src


def test_ext_runtime_declares_ui_api_version(ext_runtime_src):
    assert f"UI_API_VERSION = '{UI_API_VERSION}'" in ext_runtime_src
    assert f"version: UI_API_VERSION" in ext_runtime_src


def test_ext_runtime_exposes_register_listpackages_listcommands_getcommand(ext_runtime_src):
    assert "register: register," in ext_runtime_src
    assert "listPackages: listPackages," in ext_runtime_src
    assert "listCommands: listCommands," in ext_runtime_src
    assert "getCommand: getCommand," in ext_runtime_src


def test_ext_runtime_internal_exposes_fireHook_fireFilter_renderSlot(ext_runtime_src):
    assert "fireHook: _fireHook," in ext_runtime_src
    assert "fireFilter: _fireFilter," in ext_runtime_src
    assert "renderSlot: _renderSlot," in ext_runtime_src
    assert "renderAllSlots: _renderAllSlots," in ext_runtime_src


KNOWN_SLOTS = [
    "action_menu", "gear_menu", "resources_panel",
    "sidebar_top", "sidebar_bottom",
    "header_actions", "tab_bar",
]

KNOWN_HOOKS = [
    "boot", "shutdown",
    "conversation_changed", "conversation_created", "conversation_deleted",
    "message_appended", "message_streaming",
    "tool_call_started", "tool_call_completed",
    "command_submitted", "command_result",
    "before_send",
    "agent_changed", "theme_changed",
    "tab_switched", "permission_mode_changed",
    "sse_event",
]


def test_ext_runtime_declares_all_known_slots(ext_runtime_src):
    for slot in KNOWN_SLOTS:
        assert f"'{slot}'" in ext_runtime_src, f"missing slot {slot!r}"


def test_ext_runtime_declares_all_known_hooks(ext_runtime_src):
    for hook in KNOWN_HOOKS:
        assert f"'{hook}'" in ext_runtime_src, f"missing hook {hook!r}"


def test_ext_runtime_pfp_api_exposes_slot_on_call_command(ext_runtime_src):
    # The per-package pfp object surface.
    assert "slot: function (slotName" in ext_runtime_src
    assert "on: function (hookName" in ext_runtime_src
    assert "call: function (action" in ext_runtime_src
    assert "command: function (name" in ext_runtime_src
    assert "openDialog: function (title" in ext_runtime_src
    assert "openPanel: function (panelId" in ext_runtime_src


def test_ext_runtime_call_wrapper_injects_ext_field(ext_runtime_src):
    # `pfp.call(action, body)` must auto-tag the payload with `_ext: packageId`
    assert "_ext: packageId" in ext_runtime_src


def test_ext_runtime_boot_replay_for_late_subscribers(ext_runtime_src):
    assert "if (hookName === 'boot' && _booted)" in ext_runtime_src


def test_ext_runtime_uses_setTimeout_for_async_dispatch(ext_runtime_src):
    # Slow extension listeners must not block the firing caller.
    assert "setTimeout(function () {" in ext_runtime_src


# ── serve_chat_ui.py wiring ─────────────────────────────────────────────────────────

def test_ext_runtime_module_listed_in_js_load_order(serve_chat_ui_src):
    assert '"ext_runtime.js"' in serve_chat_ui_src
    # Must load AFTER rxbus.js (so action$ is available for pfp.call)
    # and AFTER state.js (so it can read conversationId etc.).
    pre = serve_chat_ui_src.split('"ext_runtime.js"', 1)[0]
    assert '"rxbus.js"' in pre, "ext_runtime.js must load after rxbus.js"
    assert '"state.js"' in pre, "ext_runtime.js must load after state.js"


def test_pawflow_extensions_bootstrap_is_injected(serve_chat_ui_src):
    assert "_initial_extensions_block" in serve_chat_ui_src
    assert "window.PAWFLOW_EXTENSIONS=" in serve_chat_ui_src


# ── template.html slot containers ────────────────────────────────────────────────

def test_template_has_slot_containers(template_src):
    for slot in KNOWN_SLOTS:
        marker = f'data-pf-slot="{slot}_ext"'
        assert marker in template_src, f"missing slot container {marker}"


def test_template_has_modal_and_panel_hosts(template_src):
    assert 'id="pf-ext-modal-host"' in template_src
    assert 'id="pf-ext-panel-host"' in template_src


# ── Hook firing points ───────────────────────────────────────────────────────────────

def _firing_in(file_path: str, hook_name: str) -> bool:
    src = Path(file_path).read_text(encoding="utf-8")
    needle = f"fireHook('{hook_name}'"
    return needle in src


def test_hook_conversation_changed_fires_in_resumeConv():
    assert _firing_in(_CHAT_UI / "conversations.js", "conversation_changed")


def test_hook_message_appended_fires_in_addMsg():
    # addMsg (and its message_appended hook) moved to messages_render.js (<=800 split).
    assert _firing_in(_CHAT_UI / "messages_render.js", "message_appended")


def test_hook_command_submitted_fires_in_handleSlashCommand():
    assert _firing_in(_CHAT_UI / "commands.js", "command_submitted")


def test_hook_command_result_fires_in_rxbus():
    assert _firing_in(_CHAT_UI / "rxbus.js", "command_result")


def test_hook_theme_changed_fires_in_themes():
    assert _firing_in(_CHAT_UI / "themes.js", "theme_changed")


def test_hook_agent_changed_fires_in_cmd_agent():
    assert _firing_in(_CHAT_UI / "cmd_agent.js", "agent_changed")


def test_hook_permission_mode_changed_fires_in_state():
    assert _firing_in(_CHAT_UI / "state.js", "permission_mode_changed")


def test_hook_tab_switched_fires_in_tabs():
    assert _firing_in(_CHAT_UI / "tabs.js", "tab_switched")


def test_hook_sse_event_wrapper_in_connectSSE():
    src = (_CHAT_UI / "sse.js").read_text(encoding="utf-8")
    assert "_wrapSseForExtensions(eventSource)" in src
    assert "fireHook('sse_event'" in src
    assert "fireHook('tool_call_started'" in src
    assert "fireHook('tool_call_completed'" in src


def test_before_send_filter_in_attachments():
    src = (_CHAT_UI / "attachments.js").read_text(encoding="utf-8")
    assert "fireFilter('before_send'" in src
    # Cancel flag must short-circuit send()
    assert "_bsPayload.cancel === true" in src


# ── Extension command dispatch hook ────────────────────────────────────────────────

def test_handleSlashCommand_resolves_extension_commands_before_builtins():
    src = (_CHAT_UI / "commands.js").read_text(encoding="utf-8")
    # Extension commands resolved BEFORE _CMD_ALIASES / _CMD_HANDLERS lookup.
    assert "window.pawflow.getCommand(cmd)" in src
    ext_pos = src.index("window.pawflow.getCommand(cmd)")
    alias_pos = src.index("_CMD_ALIASES[cmd]")
    assert ext_pos < alias_pos, (
        "extension command lookup must precede built-in alias resolution"
    )
