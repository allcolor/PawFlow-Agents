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


def test_service_parameter_fill_helper_is_wired_in_chat_ui():
    src = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "get_service_parameter_helper" in src
    assert "_renderParamFillHelper" in src
    assert "_openParamFillHelper" in src
    assert "_applyParamFillSuggestion" in src


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


def test_flow_template_graph_passes_conversation_id():
    services_js = Path("tasks/io/chat_ui/services.js").read_text(encoding="utf-8")
    flow_graph = Path("tasks/io/chat_ui/flow_graph.html").read_text(encoding="utf-8")

    assert "window.__PAWFLOW_FLOW_CONVERSATION_ID" in services_js
    assert "conversation_id=' + encodeURIComponent(convId)" in services_js
    assert "const CONVERSATION_ID = params.get('conversation_id')" in flow_graph
    assert "template_id: TEMPLATE_ID, conversation_id: CONVERSATION_ID" in flow_graph


def test_chat_ui_html_helpers_escape_attribute_and_js_contexts():
    js = Path("tasks/io/chat_ui/messages.js").read_text(encoding="utf-8")
    conv_js = Path("tasks/io/chat_ui/conversations.js").read_text(encoding="utf-8")

    for src in (js, conv_js):
        assert "function escapeAttr" in src
        assert "function jsStringArg" in src
        assert ".replace(/\"/g, '&quot;')" in src
        assert ".replace(/'/g, '&#39;')" in src
        assert "JSON.stringify(String(" in src


def test_context_editor_escapes_agent_names_sources_and_message_ids():
    js = Path("tasks/io/chat_ui/context_editor.js").read_text(encoding="utf-8")

    assert "'<option value=\"' + escapeAttr(n)" in js
    assert "+ escapeHtml(label) + '</option>'" in js
    assert "escapeHtml(m.source.name || '')" in js
    assert "const safeMid = jsStringArg(mid)" in js
    assert "data-msgid=\"' + escapeAttr(mid)" in js
    assert js.count("_ctxVisibleById.set(mid, m)") >= 2
    assert "<option value=\"' + n + '\"" not in js
    assert "[' + (m.source.name||'') + ']" not in js


def test_resource_panel_uses_safe_js_args_for_user_resource_names():
    js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "function _pfpJsArg" in js
    assert "showAgentMenu(event,' + _pfpJsArg(aName)" in js
    assert "showResourceMenu(event,'agent',' + aName" not in js
    assert "showResourceMenu(event,'skill',${_pfpJsArg(s.name)}" in js
    assert "_usePrompt(${_pfpJsArg(p.name)}" in js
    assert "_applyThemeFromResource(${_pfpJsArg(ref)})" in js
    assert "_renameVoiceClone(${_pfpJsArg(v.name)})" in js
    assert "_deleteVoiceClone(${_pfpJsArg(v.name)})" in js
    assert "showRunningTaskMenu(event,${_pfpJsArg(t.task_id)}" in js
    assert "showDeployFlowDialog(${_pfpJsArg(t.id)})" in js
    assert "showResourceMenu(event,'mcp',${_pfpJsArg(m.name)}" in js
    assert "showResourceMenu(event,'agent_hook',${_pfpJsArg(h.name)}" in js
    assert "showToolCallDialog(${_pfpJsArg(t.name)})" in js
    assert "_saveResourceEdit(${_pfpJsArg(rtype)},${_pfpJsArg(name)},${_pfpJsArg(scope)})" in js
    assert "_submitAssign(${_pfpJsArg(taskDefName)})" in js
    assert "_executeServiceAction(' + _pfpJsArg(a.id)" in js


def test_sse_plan_and_ask_user_events_escape_user_controlled_html():
    js = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")

    assert "escapeHtml(title) + '</strong> ('" in js
    assert "planAction(\\'approve_plan\\',' + jsStringArg(planId)" in js
    assert "document.getElementById(\\'input\\').value=' + jsStringArg(opt)" in js
    assert "'<strong>' + title + '</strong>'" not in js
    assert "opt.replace(/'/g" not in js
