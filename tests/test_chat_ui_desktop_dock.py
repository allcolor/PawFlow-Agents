"""Source invariants for the Active Desktops dock (desktop_dock.js, WS7).

Locks plan §13: a dedicated dock button with a count badge, backend-truth
listing, attach-never-starts, exact-session stop confirmation, no bulk
stop, and the detach-vs-stop distinction in the slash command.
"""
import json
from pathlib import Path

from chat_ui_testing import rendered_chat_html, chat_ui_css

CHAT_UI = Path("tasks/io/chat_ui")


def test_dock_module_registered_and_button_rendered():
    src = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    assert '"desktop_dock.js"' in src
    assert (CHAT_UI / "desktop_dock.js").exists()

    html = rendered_chat_html()
    assert 'id="desktopDockBtn"' in html
    assert 'id="desktopDockBadge"' in html
    # Accessible label + keyboard focus (plan §13.1).
    assert 'data-i18n-aria-label="desktopDockLabel"' in html
    assert 'tabindex="0"' in html.split('id="desktopDockBtn"')[1][:200]


def test_dock_styles_present_with_mobile_bottom_sheet():
    css = chat_ui_css("96_desktop_dock.css")
    for cls in [".desktop-dock-pop", ".desktop-dock-row",
                ".desktop-dock-confirm", ".desktop-dock-badge.isolated",
                ".desktop-dock-badge.shared", ".desktop-dock-state.state-unknown"]:
        assert cls in css
    # Mobile bottom sheet presentation.
    assert "@media (max-width: 640px)" in css.split(".desktop-dock-pop", 1)[1]


def test_dock_module_contract():
    src = (CHAT_UI / "desktop_dock.js").read_text(encoding="utf-8")
    # Inventory comes from the server, never from open tabs.
    assert "desktop_list_active" in src
    assert "desktop_inventory_changed" in src
    # Attach never starts: the module must not call open_desktop.
    assert "desktop_attach" in src
    assert "open_desktop" not in src
    # Stop = request/confirm with the observed exact session ID.
    assert "desktop_stop_confirm" in src
    assert "desktop_session_id: row.desktop_session_id" in src
    assert "session_conflict" in src
    # No bulk stop in the first release.
    assert "stop_all" not in src.lower().replace(" ", "_")
    # The badge counts unknown sessions too (unknown != stopped).
    assert "r.state === 'unknown'" in src.split("const count", 1)[1].split(
        ".length")[0]
    # Viewer tabs are stamped with backend identity; post-stop close
    # matches relay AND mode so one mode never detaches the other.
    assert "function desktopDockStampTab(" in src
    assert "p.dataset.desktopMode || 'docker'" in src
    assert "mode === row.mode" in src


def test_dock_accessibility_contract():
    src = (CHAT_UI / "desktop_dock.js").read_text(encoding="utf-8")
    # div[role=button] must be keyboard-activable.
    assert "ev.key === 'Enter'" in src
    # Popover and confirmation carry dialog semantics and Escape handling.
    assert "setAttribute('role', 'dialog')" in src
    assert "setAttribute('role', 'alertdialog')" in src
    assert "setAttribute('aria-modal', 'true')" in src
    assert src.count("'Escape'") >= 2
    # Focus lands in the dialog and returns to the opener on close.
    assert "cancelBtn.focus()" in src
    assert "previousFocus.focus()" in src


def test_sse_wires_the_dock_listener():
    src = (CHAT_UI / "sse.js").read_text(encoding="utf-8")
    assert "_desktopDockWireSSE" in src


def test_slash_stop_goes_through_confirmation_not_fireaction():
    src = (CHAT_UI / "terminal_commands.js").read_text(encoding="utf-8")
    src = src.split("async function cmdDesktop", 1)[1]
    stop_block = src.split("if (sub === 'stop')", 1)[1].split("let relayId")[0]
    assert "desktop_stop_request" in stop_block
    assert "desktopDockRequestStopRow" in stop_block
    assert "fireAction('close_desktop'" not in stop_block
    # close stays a pure detach.
    close_block = src.split("if (sub === 'close')", 1)[1].split("if (sub ===")[0]
    assert "closeDesktopTab" in close_block
    assert "stop" not in close_block.replace("desktopTabClosed", "")
    # New read-only subcommands exist.
    assert "if (sub === 'list')" in src
    assert "if (sub === 'status')" in src
    assert "if (sub === 'attach')" in src


def test_slash_commands_carry_mode_and_stamp_tabs():
    src = (CHAT_UI / "terminal_commands.js").read_text(encoding="utf-8")
    body = src.split("async function cmdDesktop", 1)[1]
    attach_block = body.split("if (sub === 'attach')", 1)[1].split(
        "if (sub === 'stop')")[0]
    assert "mode: attachMode" in attach_block
    stop_block = body.split("if (sub === 'stop')", 1)[1].split("let relayId")[0]
    assert "mode: stopMode" in stop_block
    # Mode falls back to the stamped tab identity, then docker.
    assert "_stopTabMode || 'docker'" in stop_block
    # Every open path stamps the real relay id and mode on the tab.
    assert body.count("desktopDockStampTab(") >= 2


def test_i18n_keys_exist_in_all_locales():
    keys = [
        "desktopDockLabel", "desktopDockDesc", "desktopDockTitle",
        "desktopDockEmpty", "desktopDockOpen", "desktopDockStop",
        "desktopDockIsolated", "desktopDockSharedHost",
        "desktopDockStateRunning", "desktopDockStateStopping",
        "desktopDockStateUnknown", "desktopDockUnknownNote",
        "desktopDockConfirmTitle", "desktopDockConfirmBody",
        "desktopDockConflict", "desktopStatusUsage",
        "desktopStatusStopped", "desktopAttachUsage",
    ]
    for locale in ("en", "fr", "es"):
        data = json.loads(
            (CHAT_UI / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        missing = [k for k in keys if k not in data]
        assert not missing, f"{locale}.json missing {missing}"


def test_confirmation_dialog_names_session_and_offers_cancel():
    src = (CHAT_UI / "desktop_dock.js").read_text(encoding="utf-8")
    assert "desktopDockConfirmTitle" in src
    assert "desktopDockConfirmBody" in src
    assert "row.desktop_session_id" in src
    assert "ddc-cancel" in src
    # Rows leave the list only after backend acknowledgement.
    assert "_desktopDockRefresh(true)" in src
