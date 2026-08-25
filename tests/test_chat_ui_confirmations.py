"""Chat UI surfaces for durable typed user interactions."""

import json

from chat_ui_testing import rendered_chat_html
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_module_is_served():
    source = _text("tasks/io/serve_chat_ui.py")
    assert '"confirmations_panel.js"' in source


def test_sse_events_render_and_reconcile():
    source = _text("tasks/io/chat_ui/sse_handlers_b.js")
    assert "addEventListener('confirmation_request'" in source
    assert "addEventListener('confirmation_answered'" in source
    assert "renderConfirmationBlock(data)" in source
    assert "addEventListener('interaction_request'" in source
    assert "addEventListener('interaction_answered'" in source
    assert "renderInteractionBlock(data)" in source


def test_blocks_are_actionable_and_durable():
    source = _text("tasks/io/chat_ui/confirmations_panel.js")
    # Single-choice buttons and multi-choice checkboxes + validate.
    assert "respondInteraction(c.request_id, o.value)" in source
    assert "kind === 'multi'" in source
    assert "filter((b) => b.checked)" in source
    # Durable: pending requests re-render from the store after a reload...
    assert "function hydrateInteractions()" in source
    assert "action$('list_interactions', { status: 'pending' })" in source
    for kind in ("multiline", "integer", "decimal", "date", "datetime", "file", "form"):
        assert f"kind === '{kind}'" in source or f"kind === '{kind}'" in source
    # ...and the panel lists EVERY conversation's pending requests.
    assert "conf-panel-row" in source
    conversations = _text("tasks/io/chat_ui/conversations.js")
    assert "hydrateConfirmations()" in conversations


def test_header_button_badge_panel_and_command():
    template = rendered_chat_html()
    assert 'id="confirmationsBtn"' in template
    assert 'id="confirmationsBadge"' in template
    assert 'id="confirmationsPanel"' in template
    commands = _text("tasks/io/chat_ui/commands.js")
    assert "'/confirmations':" in commands


def test_openspace_poster_opens_the_panel():
    source = _text("tasks/io/chat_ui/openspace_scene.js")
    assert "'confirmations'" in source
    assert "toggleConfirmationsPanel" in source


def test_i18n_keys_exist():
    for lang in ("en", "fr", "es"):
        data = json.loads(_text(f"tasks/io/chat_ui/i18n/{lang}.json"))
        for key in ("confTitle", "confPending", "confValidate",
                    "confNonePending", "confAnswered"):
            assert key in data, (lang, key)
