"""Composer drawer: the zone above the prompt folds behind a slim handle.

Closed by default, the reader's choice persists across reloads, and the
active-agents mount is not part of the drawer. The composer stop button is
gone: the Active Agents panel is the only stop surface.
"""

import json
from pathlib import Path

TEMPLATE = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
STATE_JS = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")


def test_handle_markup_and_collapse_rules():
    assert 'id="composerDrawerHandle"' in TEMPLATE
    assert 'onclick="toggleComposerDrawer()"' in TEMPLATE
    assert 'data-i18n-title="composerDrawerTitle"' in TEMPLATE
    # Collapse hides the controls panel and the action dock…
    assert (".input-area.composer-drawer-collapsed .composer-context-row > "
            ".prompt-controls-panel") in TEMPLATE
    assert (".input-area.composer-drawer-collapsed .composer-context-row > "
            ".composer-action-mount") in TEMPLATE
    # …but never the active-agents mount.
    assert (".input-area.composer-drawer-collapsed .composer-context-row > "
            ".composer-active-mount") not in TEMPLATE


def test_drawer_defaults_closed_and_persists():
    # Open only when the stored flag is explicitly '1': a fresh browser
    # (no key) starts CLOSED.
    assert "localStorage.getItem(_COMPOSER_DRAWER_KEY) === '1'" in STATE_JS
    assert "localStorage.setItem(_COMPOSER_DRAWER_KEY" in STATE_JS
    assert "document.addEventListener('DOMContentLoaded', _applyComposerDrawer)" in STATE_JS


def test_i18n_key_present_in_all_languages():
    for lang in ("en", "fr", "es"):
        data = json.loads(Path(f"tasks/io/chat_ui/i18n/{lang}.json")
                          .read_text(encoding="utf-8"))
        assert data["composerDrawerTitle"]


def test_composer_stop_button_is_gone():
    assert 'id="stopBtn"' not in TEMPLATE
    for js in Path("tasks/io/chat_ui").glob("*.js"):
        assert "stopBtn" not in js.read_text(encoding="utf-8"), js.name
