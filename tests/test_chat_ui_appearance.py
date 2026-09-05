"""Tests for the user-owned chat appearance panel."""

from pathlib import Path
import subprocess

from chat_ui_testing import rendered_chat_html


ROOT = Path(__file__).resolve().parent.parent
APPEARANCE_JS = ROOT / "tasks" / "io" / "chat_ui" / "appearance.js"


def test_appearance_behavioural_js_suite():
    result = subprocess.run(
        ["node", "tests/js/appearance_spec.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passing" in result.stdout


def test_appearance_panel_is_loaded_with_theme_safe_tokens():
    html = rendered_chat_html()
    serve = (ROOT / "tasks" / "io" / "serve_chat_ui.py").read_text(
        encoding="utf-8"
    )

    assert 'id="appearanceBtn"' in html
    assert 'id="appearanceDialog"' in html
    assert 'id="appearanceScope"' in html
    assert 'id="appearanceThemeSelect"' in html
    assert 'id="conversationAppearanceBtn"' in html
    assert 'id="themeSelectControl"' not in html
    assert 'id="conversationQuickThemeControl"' not in html
    assert 'id="conversationThemeSelect"' not in html
    assert 'id="pfAtmosphere"' in html
    assert "--pf-atmosphere-panel-opacity" in html
    assert '"appearance.js"' in serve
    assert '"55_appearance.css"' in serve


def test_background_translucency_reaches_messages_and_composer_only_when_active():
    css = (ROOT / "tasks" / "io" / "chat_ui" / "css" / "55_appearance.css").read_text(
        encoding="utf-8"
    )

    for selector in (".msg.user", ".msg.btw", ".msg.tool", ".composer-shell", ".action-dock"):
        assert f'html[data-pf-atmosphere="on"] {selector}' in css
    assert "var(--pf-atmosphere-panel-opacity)" in css
    assert "backdrop-filter: blur" in css


def test_atmosphere_keeps_desktop_tab_bar_fixed_as_an_overlay():
    css = (ROOT / "tasks" / "io" / "chat_ui" / "css" / "55_appearance.css").read_text(
        encoding="utf-8"
    )

    assert 'body > .tab-bar { position: relative' not in css
    assert 'body > .tab-bar { z-index: 190; }' in css
    # Popovers mount at body level; lifting the entire chat covers side controls.
    assert 'body:has(.hdr-pop.open) > .main' not in css


def test_appearance_preferences_are_user_scoped_and_motion_aware():
    source = APPEARANCE_JS.read_text(encoding="utf-8")

    assert "window._userId ||" in source
    assert "'pawflow.appearance.v1:' + _appearanceUserId()" in source
    assert "':conversation:' + cid" in source
    assert "refreshAppearanceContext" in source
    assert "pawflow:userchange" in source
    assert "prefers-reduced-motion: reduce" in source
    assert "visibilitychange" in source
    assert "appearance_get" in source
    assert "appearance_save" in source
    assert "appearance_clear_conversation" in source
    assert "purpose=appearance" in source
    assert "showConversationAppearanceDialog" in source
