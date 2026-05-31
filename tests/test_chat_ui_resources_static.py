from pathlib import Path


def test_modal_overlays_do_not_close_from_background_clicks():
    ui_files = list(Path("tasks/io/chat_ui").glob("*.js"))
    ui_files += list(Path("tasks/io/admin_ui").glob("*.js"))
    forbidden = [
        "if (e.target === overlay) overlay.remove()",
        "if(e.target===overlay)overlay.remove()",
        "if (ev.target === overlay) overlay.remove()",
        "if(ev.target===overlay)overlay.remove()",
        "if (e.target === this) closeModal()",
        "if (e.target === overlay) closeDialog(null)",
        "if (e.target === overlay) cleanup(null)",
        "if(e.target===o)closeExplorer()",
    ]

    offenders = []
    for path in ui_files:
        src = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in src:
                offenders.append(f"{path}:{needle}")

    assert offenders == []


def test_agent_attach_dialog_has_only_explicit_close_handlers():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")
    start = js.index("async function showAddAgentToConvDialog")
    block = js[start:js.index("\n// ── Assign task dialog", start)]

    assert "closeBtn.onclick = function() { overlay.remove(); }" in block
    assert "e.target === overlay" not in block
    assert "overlay.addEventListener('click'" not in block


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
    # The submit call is wrapped in a _submit(force) helper so a blocked
    # review can be rerun with force; `p` is payload (optionally + force).
    assert "action$('update_resource', p)" in js
    assert "action$('create_resource', p, { skipConversationId: scope !== 'conversation' })" in js
    assert "skipConversationId: scope !== 'conversation'" in js
    assert "!opts.skipConversationId" in rxbus


def test_agent_skills_dialog_surfaces_assign_errors():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "results.filter(r => r && r.error).map(r => r.error)" in js
    assert "addMsg('error', errors.join('\\n'))" in js


def test_skill_list_uses_real_newlines_and_symbols():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "lines.join('\\\\n')" not in js
    assert "\\\\u2705" not in js
    assert "\\\\u2B1C" not in js
    assert "\\\\u26A0" not in js
    assert "lines.join('\\n')" in js


def test_skill_creator_can_assign_after_create():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "(rtype === 'task_def' || rtype === 'skill')" in js
    assert "assignAfterCreate && rtype === 'skill'" in js
    assert "_showSkillAssignDialog(name)" in js


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
