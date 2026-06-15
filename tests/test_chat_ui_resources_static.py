from pathlib import Path


def test_modal_overlays_do_not_close_from_background_clicks():
    ui_files = list(Path("tasks/io/chat_ui").glob("*.js"))
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
    html = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")

    assert "get_service_parameter_helper" in src
    assert "_renderParamFillHelper" in src
    assert "_openParamFillHelper" in src
    assert "_applyParamFillSuggestion" in src
    assert ">[...]</button>" in src
    assert "button.svc-param-help, button.svc-param-help:hover" in html
    assert "button.svc-param-fill, button.svc-param-fill:hover" in html


def test_admin_settings_menu_exposes_oauth_onboarding_tokens():
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
    admin_js = Path("tasks/io/chat_ui/admin_settings.js").read_text(encoding="utf-8")

    assert "openOAuthTokensDialog()" in template
    assert "admin_oauth_token_create" in admin_js
    assert "admin_oauth_tokens_list" in admin_js
    assert "Tokens are one-time and disappear when used, expired, or deleted." in admin_js
    assert '<option value="user">user</option><option value="admin">admin</option>' in admin_js


def test_admin_user_management_can_edit_identity_links():
    admin_js = Path("tasks/io/chat_ui/admin_settings.js").read_text(encoding="utf-8")

    assert "admin_identity_link" in admin_js
    assert "function adminSaveIdentity" in admin_js
    assert "function adminAddIdentity" in admin_js
    assert "adm-id-channel" in admin_js
    assert "adm-new-id-channel" in admin_js


def test_chat_ui_exposes_oauth_link_account_button():
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
    state_js = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")

    assert "linkAccountBtn" in template
    assert "function beginOAuthAccountLink" in state_js
    assert "begin_oauth_account_link" in state_js
    assert "fetch(_uiUrl" in state_js
    assert "credentials: 'same-origin'" in state_js
    assert "_reply_conversation_id" not in state_js[state_js.index("function beginOAuthAccountLink"):state_js.index("function doLogout")]


def test_relay_install_hides_token_parameter():
    src = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")
    relay_src = Path("services/filesystem_service.py").read_text(encoding="utf-8")

    assert "function _installSchemaForServiceType" in src
    assert "if (serviceType === 'relay')" in src
    assert "delete schema.token" in src
    assert "Leave empty to create a managed server relay" not in relay_src
    assert "Managed server relays generate this token server-side" in relay_src


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


def test_resource_scope_options_include_global_only_for_admin():
    state_js = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")
    resources_js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

    assert "function _resourceWritableScopes()" in state_js
    assert "_isAdmin() ? ['global', 'user', 'conversation'] : ['user', 'conversation']" in state_js
    assert "function _resourceScopeOptions()" in state_js
    assert "<select id=\"skill-import-scope\"" in resources_js
    assert "+ _resourceScopeOptions() + '</select>'" in resources_js
    assert "<select id=\"res-scope\"" in resources_js


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
    assert "template_id: graph.template_id" in flow_graph
    assert "conversation_id: graph.conversation_id || ''" in flow_graph
    assert "API_BASE + '/api/ui'" in flow_graph


def test_flow_graph_fetch_busts_immutable_chat_js_cache():
    services_js = Path("tasks/io/chat_ui/services.js").read_text(encoding="utf-8")

    assert "'&v=' + encodeURIComponent(Date.now())" in services_js
    assert "cache: 'no-store'" in services_js


def test_flow_graph_handles_missing_nodes_edges_response():
    flow_graph = Path("tasks/io/chat_ui/flow_graph.html").read_text(encoding="utf-8")

    assert "apiNodes && typeof apiNodes === 'object' ? apiNodes : {}" in flow_graph
    assert "function safeLayoutGraph(nodes, edges)" in flow_graph
    assert "!validNodeIds.has(source) || !validNodeIds.has(target)" in flow_graph
    assert "Flow graph response is missing nodes or edges" in flow_graph


def test_flow_graph_supports_subflow_navigation():
    flow_graph = Path("tasks/io/chat_ui/flow_graph.html").read_text(encoding="utf-8")

    assert "'flow-node', 'nodrag', 'nopan'" in flow_graph
    assert "onNodeContextMenu" in flow_graph
    assert "onNodeDoubleClick" in flow_graph
    assert "elementsSelectable: true" in flow_graph
    assert "Open subflow" in flow_graph
    assert "graph-context-menu" in flow_graph
    assert "flow_ref: graph.flow_ref" in flow_graph
    assert "setGraphStack(prev => [...prev, next])" in flow_graph
    assert "id: 'backButton'" in flow_graph


def test_flow_graph_renders_runtime_port_links():
    flow_graph = Path("tasks/io/chat_ui/flow_graph.html").read_text(encoding="utf-8")

    assert "runtime-port" in flow_graph
    assert "MarkerType" in flow_graph
    assert "runtime request/response port" in flow_graph
    assert "runtime request/response target" in flow_graph
    assert "request/response" in flow_graph
    assert "markerStart: isRuntimeEdge" in flow_graph
    assert "markerEnd: isRuntimeEdge" in flow_graph
    assert "isRuntimeLink: !!n?.runtime_link" in flow_graph
    assert "isRuntimePort: !!(n?.runtime_link || n?.runtime_port)" in flow_graph
    assert "portDirection: n?.port_direction || 'input'" in flow_graph
    assert "edges.filter(e => !e?.data?.runtimeLink)" in flow_graph
    assert "data: { runtimeLink: !!e?.runtime_link, runtimePort: !!e?.runtime_port }" in flow_graph
    assert "strokeDasharray: '6 4'" in flow_graph


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


def test_param_secret_scope_actions_do_not_force_conversation_scope():
    js = Path("tasks/io/chat_ui/file_viewer.js").read_text(encoding="utf-8")

    assert "skipConversationId: scope !== 'conversation'" in js
    assert "move_secret_scope" in js
    assert "move_param_scope" in js
    assert "from_scope" in js
    assert "to_scope" in js


def test_scoped_resource_menus_send_explicit_source_scope():
    resources_js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")
    services_js = Path("tasks/io/chat_ui/services.js").read_text(encoding="utf-8")

    assert "function _moveResource" in resources_js
    assert "from_scope: fromScope" in resources_js
    assert "skipConversationId: !(fromScope === 'conversation' || targetScope === 'conversation')" in resources_js
    assert "move_service_scope" in resources_js
    assert "from_scope: normScope" in resources_js
    assert "Promote to global" in resources_js
    assert "target_scope: targetScope" in services_js
    assert "skipConversationId: !(normScope === 'conversation' || targetScope === 'conversation')" in services_js


def test_resource_panel_renders_with_no_conversation_selected():
    # Regression: a user with no conversation (e.g. a freshly-created/technical
    # user) saw no resource panel at all. _loadResourcesNow used to hide the
    # panel and return early when !conversationId, so _renderResourcesData
    # (which already adapts to the no-conv case) was never reached.
    resources_js = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")
    file_explorer_js = Path("tasks/io/chat_ui/file_explorer.js").read_text(encoding="utf-8")
    conversations_js = Path("tasks/io/chat_ui/conversations.js").read_text(encoding="utf-8")

    # The early hide-and-return on no conversation must be gone.
    assert "style.display = 'none'; return; }" not in resources_js
    # The panel is shown and the conversation-scoped pfp fetch is guarded.
    assert "var _noConv = !conversationId;" in resources_js
    assert "if (_panel) _panel.style.display = 'block';" in resources_js
    assert "{ scope: 'user', conversation_id: conversationId || '' }" in resources_js
    assert "if (_noConv) {" in resources_js
    # _renderResourcesData still detects the no-conv case to drop conv-scoped sections.
    assert "const noConv = !(typeof conversationId !== 'undefined' && conversationId);" in resources_js

    # Boot: with no conversation to resume, loadResources() must still fire so
    # the panel hydrates instead of staying hidden.
    boot = file_explorer_js[file_explorer_js.index("} else if (!convs.length) {"):]
    assert "if (typeof loadResources === 'function') loadResources();" in boot

    # Deleting the last conversation re-renders the panel for the no-conv state.
    empty_state = conversations_js[conversations_js.index("function renderEmptyState()"):]
    empty_state = empty_state[:empty_state.index("\n}")]
    assert "if (typeof loadResources === 'function') loadResources();" in empty_state
