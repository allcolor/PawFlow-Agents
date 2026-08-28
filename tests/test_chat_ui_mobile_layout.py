"""Structural assertions for the chat UI's mobile (narrow viewport) layout."""

import json

from chat_ui_testing import rendered_chat_html
import re
from pathlib import Path

TEMPLATE_HTML = rendered_chat_html()
ATTACHMENTS_JS = Path("tasks/io/chat_ui/attachments.js").read_text(encoding="utf-8")
GRAB_JS = Path("tasks/io/chat_ui/grab.js").read_text(encoding="utf-8")
I18N_JS = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")


def _mobile_block() -> str:
    """The body of the `@media (max-width: 768px)` rule."""
    start = TEMPLATE_HTML.index("@media (max-width: 768px) {")
    depth = 0
    for i in range(TEMPLATE_HTML.index("{", start), len(TEMPLATE_HTML)):
        if TEMPLATE_HTML[i] == "{":
            depth += 1
        elif TEMPLATE_HTML[i] == "}":
            depth -= 1
            if depth == 0:
                return TEMPLATE_HTML[start:i + 1]
    raise AssertionError("unterminated mobile media query")


def test_full_height_elements_use_dvh_so_the_header_stays_on_screen():
    """Regression: with plain `100vh` + `overflow: hidden`, mobile browsers size
    the layout to the URL-bar-hidden height, pushing .header above the visible
    viewport with no way to scroll it back — the top bar simply vanished."""
    for selector in ("body {", ".sidebar {"):
        start = TEMPLATE_HTML.index(selector)
        rule = TEMPLATE_HTML[start:TEMPLATE_HTML.index("}", start)]
        assert "height: 100dvh" in rule, f"{selector} must size to the dynamic viewport"
        # The plain vh declaration stays as the fallback, and must come first
        # so dvh wins wherever it is supported.
        assert rule.index("height: 100vh") < rule.index("height: 100dvh")


def test_mobile_taskbar_persists_and_sidebar_toggle_stays_above_the_open_drawer():
    """Regression: on mobile the sidebar becomes a fixed overlay (z-index 150).
    The task rail stays visible while the drawer is closed. The toggle sits
    on its outer edge, then both move outside the fixed drawer while it is
    open so the menu can always be closed."""
    mobile = _mobile_block()
    assert ".sidebar { position: fixed; top: 0; left: 0; bottom: 0; z-index: 150;" in mobile
    assert ".sidebar-toggle { left: 35px !important; z-index: 200; }" in mobile
    assert ".tab-bar { position: fixed; top: 0; bottom: 0; left: 0;" in mobile
    assert "body:has(.sidebar:not(.collapsed)) .tab-bar { left: 260px; }" in mobile
    assert (
        "body:has(.sidebar:not(.collapsed)) .sidebar-toggle"
        " { left: 295px !important; }" in mobile
    )
    # The desktop-only tooltip handle must not add another chevron on mobile.
    assert ".tab-bar-handle { display: none; }" in TEMPLATE_HTML


def test_mobile_action_dock_stays_below_the_open_sidebar():
    """The composer dock must not paint over the fixed mobile drawer."""
    mobile = _mobile_block()
    selector = "body:has(.sidebar:not(.collapsed)) .action-dock"
    match = re.search(re.escape(selector) + r"\s*\{[^}]*z-index:\s*(\d+)", mobile)
    assert match, "the mobile layout must override the desktop dock layer"
    assert int(match.group(1)) < 150


def test_mobile_bumps_the_type_scale_above_the_desktop_sizes():
    """Desktop uses 11-12px for technical/tool output, which is unreadable on a
    phone. Every size the mobile block sets must be larger than the desktop
    default for the same selector."""
    mobile = _mobile_block()

    def desktop_size(selector: str) -> int:
        # Last desktop declaration wins; the mobile block is excluded because
        # it is matched separately and always sits after these rules.
        body = TEMPLATE_HTML.replace(mobile, "")
        pattern = re.escape(selector) + r"[^{]*\{[^}]*?font-size:\s*(\d+)px"
        found = re.findall(pattern, body)
        assert found, f"no desktop font-size found for {selector}"
        return int(found[-1])

    def mobile_size(selector: str) -> int:
        pattern = re.escape(selector) + r"[^{]*\{[^}]*?font-size:\s*(\d+)px"
        found = re.search(pattern, mobile)
        assert found, f"{selector} not sized in the mobile block"
        return int(found.group(1))

    for selector in (".msg", ".msg code", ".msg.tool", ".tc-md"):
        assert mobile_size(selector) > desktop_size(selector), selector


def test_mobile_input_is_at_least_16px_to_avoid_focus_zoom():
    """iOS Safari zooms the whole page when a focused input is under 16px."""
    mobile = _mobile_block()
    match = re.search(r"\.input-row textarea[^{]*\{[^}]*?font-size:\s*(\d+)px", mobile)
    assert match and int(match.group(1)) >= 16


def test_mobile_bubbles_use_the_available_width():
    mobile = _mobile_block()
    assert re.search(r"\.msg\s*\{[^}]*max-width:\s*9\d%", mobile)
    assert re.search(r"\.technical-group,\s*\.delegate-block\s*\{[^}]*max-width:\s*100%", mobile)


def test_mobile_enter_inserts_a_newline_and_only_send_submits():
    """A touch keyboard has no Shift key, so mobile Enter must edit the draft."""
    assert "window.matchMedia('(max-width: 768px)').matches" in I18N_JS
    key = ATTACHMENTS_JS[ATTACHMENTS_JS.index("function handleKey(e)"):]
    enter = key[key.index("if (e.key === 'Enter') {"):]
    enter = enter[:enter.index("\n  }", enter.index("send();"))]
    assert "composerEnterCreatesNewline()" in enter
    assert "_composerInsertNewline(input)" in enter
    assert enter.index("_composerInsertNewline(input)") < enter.index("send();")


def test_mobile_enter_keeps_grabbed_tui_and_web_draft_in_sync():
    helper = GRAB_JS[GRAB_JS.index("function _grabInsertNewline(input)"):]
    helper = helper[:helper.index("\n}", helper.index("_grab.sentDraft")) + 2]
    assert "_grabWrite(_GRAB_CTRL_ENTER)" in helper
    assert "_composerInsertNewline(input)" in helper
    key = GRAB_JS[GRAB_JS.index("function grabHandleKey(e)"):]
    assert "plainEnter && composerEnterCreatesNewline()" in key
    assert key.index("_grabInsertNewline(input)") < key.index("grabSend();")


def test_mobile_composer_hint_is_localized_and_applied_last():
    for locale in ("en", "fr", "es"):
        catalog = json.loads(
            Path(f"tasks/io/chat_ui/i18n/{locale}.json").read_text(
                encoding="utf-8"))
        assert catalog["placeholderMobile"]
    apply = I18N_JS[I18N_JS.index("function applyI18n(root)"):]
    apply = apply[:apply.index("\n}", apply.index("_setComposerPlaceholder")) + 2]
    assert apply.index("_applyGenericI18n") < apply.index("_setComposerPlaceholder")


def test_mobile_composer_stacks_secondary_actions_behind_one_toggle():
    assert 'id="composerMobileActionsBtn"' in TEMPLATE_HTML
    assert 'id="composerMobileActions"' in TEMPLATE_HTML
    assert 'data-open="false"' in TEMPLATE_HTML
    css = Path("tasks/io/chat_ui/css/75_composer_shell.css").read_text(
        encoding="utf-8")
    assert '.composer-mobile-actions[data-open="true"]' in css
    assert "flex-direction: column" in css
    # The compact icon rule is scoped through `.input-row.composer-shell`.
    # Match that specificity here or the menu buttons stay 32 px wide and
    # their labels overflow to the left, clipped by the panel scrollbox.
    assert (
        ".input-row.composer-shell .composer-mobile-actions"
        " > .composer-icon-button {" in css
    )
    mobile_action_rule = css[
        css.index(
            ".input-row.composer-shell .composer-mobile-actions"
            " > .composer-icon-button {"
        ):
    ]
    mobile_action_rule = mobile_action_rule[:mobile_action_rule.index("}")]
    assert "width: 100%" in mobile_action_rule
    assert "justify-content: flex-start" in mobile_action_rule
    assert "toggleComposerMobileActions" in Path(
        "tasks/io/chat_ui/file_mention.js").read_text(encoding="utf-8")


def test_selected_agent_button_is_right_aligned_compact_and_mobile_safe():
    assert 'id="composerAgentBadge"' in TEMPLATE_HTML
    assert 'id="composerAgentPicker"' in TEMPLATE_HTML
    assert 'onclick="toggleComposerAgentPicker()"' in TEMPLATE_HTML
    for locale in ("en", "fr", "es"):
        catalog = json.loads(Path(
            f"tasks/io/chat_ui/i18n/{locale}.json").read_text(encoding="utf-8"))
        assert "{name}" in catalog["selectedAgentLabel"]
    css = Path("tasks/io/chat_ui/css/76_composer_agent.css").read_text(
        encoding="utf-8")
    trailing = TEMPLATE_HTML[TEMPLATE_HTML.index('<div class="composer-trailing">'):]
    assert trailing.index('id="composerAgentBadge"') < trailing.index('id="sendBtn"')
    assert 'composer-agent-chevron' not in trailing
    assert ".composer-agent-overlay" in css
    assert "var(--pf-accent)" in css
    assert "height: 24px" in css
    assert "padding: 1px 5px !important" in css
    assert "top: 0; right: 50px" in css
    assert "transform: translateY(-50%)" in css
    assert "position: fixed" in css
    assert "left: 12px; right: 12px" in css
