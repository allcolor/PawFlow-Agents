from pathlib import Path


def test_cmd_resource_action_returns_promise_for_then_callers():
    js = Path("tasks/io/chat_ui/attachments.js").read_text(encoding="utf-8")
    start = js.index("function cmdResourceAction(action, extra)")
    block = js[start:js.index("\n}\n", start) + 3]

    assert "return rxjs.firstValueFrom(action$(action, payload)).then" in block
    assert "return data;" in block


def test_resource_editor_sends_conversation_id_for_conversation_scope():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")
    rxbus = Path("tasks/io/chat_ui/rxbus.js").read_text(encoding="utf-8")

    assert "if (scope === 'conversation'" in js
    assert "payload.conversation_id = conversationId" in js
    assert "action$('update_resource', payload)" in js
    assert "action$('create_resource', payload, { skipConversationId: scope !== 'conversation' })" in js
    assert "skipConversationId: scope !== 'conversation'" in js
    assert "!opts.skipConversationId" in rxbus


def test_agent_skills_dialog_surfaces_assign_errors():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "results.filter(r => r && r.error).map(r => r.error)" in js
    assert "addMsg('error', errors.join('\\n'))" in js


def test_typing_indicators_use_sweeping_block_animation():
    js = Path("tasks/io/chat_ui/typing.js").read_text(encoding="utf-8")
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")

    assert "function typingSweepText" in js
    assert "const TYPING_SWEEP_MS = 250" in js
    assert "const TYPING_VERB_MS = 8000" in js
    assert "'█'" in js
    assert "raw.slice(0, idx) + '█' + raw.slice(idx + 1)" in js
    assert ".typing .verb { animation: none; }" in template
    assert "typingInterval = startTypingSweep('typing', '')" in js
    assert "contextOpInterval = startTypingSweep('contextOpTyping', label)" in js
