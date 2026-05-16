from pathlib import Path


def test_resource_editor_sends_conversation_id_for_conversation_scope():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "if (scope === 'conversation'" in js
    assert "payload.conversation_id = conversationId" in js
    assert "action$('update_resource', payload)" in js
    assert "action$('create_resource', payload)" in js
