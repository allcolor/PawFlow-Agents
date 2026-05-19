from pathlib import Path


def test_resource_editor_sends_conversation_id_for_conversation_scope():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "if (scope === 'conversation'" in js
    assert "payload.conversation_id = conversationId" in js
    assert "action$('update_resource', payload)" in js
    assert "action$('create_resource', payload)" in js


def test_typing_indicators_use_sweeping_block_animation():
    js = Path("tasks/io/chat_ui/typing.js").read_text(encoding="utf-8")
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")

    assert "function typingSweepText" in js
    assert "const TYPING_SWEEP_MS = 500" in js
    assert "const TYPING_VERB_MS = 8000" in js
    assert "'█'" in js
    assert "raw.slice(0, idx) + '█' + raw.slice(idx + 1)" in js
    assert ".typing .verb { animation: none; }" in template
    assert "typingInterval = startTypingSweep('typing', '')" in js
    assert "contextOpInterval = startTypingSweep('contextOpTyping', label)" in js
