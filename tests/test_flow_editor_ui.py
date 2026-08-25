"""Flow Editor canvas (edit mode of the single flow_graph.html) — source invariants."""

import json


def _text(relative):
    return open(relative, encoding="utf-8").read()


def test_one_canvas_switches_to_edit_mode_by_draft_id():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    assert "const DRAFT_ID = params.get('draft_id') || window.__PAWFLOW_FLOW_DRAFT_ID || '';" in source
    # The JSON definition is the source of truth; ReactFlow is a projection.
    assert "function flowToReactFlow(def, onOpenSubflow)" in source
    assert "function patchLayoutNode(def, id, pos)" in source
    assert "function removeFromDraft(def, taskIds, connectionIds)" in source
    assert "return `conn_${rel.from}__${rel.type}__${rel.to}`;" in source
    # Draft lifecycle through the authoring actions with optimistic locking.
    assert "action: 'flow_editor_load_draft'" in source
    assert "base_revision: revisionRef.current" in source
    assert "d.error === 'draft_changed_elsewhere'" in source
    assert "action: 'flow_editor_publish'" in source
    assert "action: 'flow_editor_validate'" in source
    # Autosave writes the draft only; Discard draft deletes it and locks the canvas.
    assert "action: 'flow_editor_discard_draft'" in source
    assert "editBtn('\\u{1F5D1} Discard draft', discardDraft" in source
    assert "setSaveState('discarded');" in source
    # One drag = one history entry (recorded on drop), undo/redo, autosave.
    assert "onNodeDragStop: editing ? onNodeDragStop : undefined" in source
    assert "h.past.push(draftRef.current);" in source
    assert "saveTimer.current = setTimeout(saveNow, AUTOSAVE_DELAY);" in source
    # No runtime polling while editing a static draft; edits only on the root graph.
    assert "if (graphStack.length === 1) { if (draftRef.current) projectDraft(draftRef.current); return undefined; }" in source
    assert "const editing = IS_EDIT && graphStack.length === 1 && !lockedRef.current;" in source
    assert "nodesDraggable: editing," in source
    # Dagre is an explicit Auto Layout action, never imposed on open.
    assert "if (nodes.some(n => !n.position)) {" in source
    assert "function autoLayoutDraft(def, rfNodes, rfEdges)" in source


def test_canvas_renders_layout_frames_and_human_task_metadata():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    assert "const FlowFrame = memo" in source
    assert "flowFrame: FlowFrame" in source
    assert "Object.entries(layout?.frames || {})" in source
    assert "description: n?.description || ''" in source
    assert "data.description && jsx('div', { className: 'node-description'" in source
    assert "positionRuntimeNodes(rfNodes, rfEdges)" in source


def test_repository_menu_opens_a_draft_in_the_same_canvas():
    services = _text("tasks/io/chat_ui/services.js")
    assert "function _openFlowEditorTab(draftId, instanceId, proposalId)" in services
    assert "'/chat/js/flow_graph.html?draft_id='" in services
    assert "action$('flow_editor_create_draft', payload" in services
    # Repository items carry a bare directory id; the draft must be created on
    # the qualified package.name[:version] or the service rejects it.
    assert "const fqn = typeof _flowEditorFqn === 'function' ? _flowEditorFqn(templateId, tpl) : templateId;" in services
    assert "const payload = { fqn, scope };" in services
    assert "fqn: templateId" not in services
    menu = _text("tasks/io/chat_ui/resources_flow_templates.js")
    assert "_editFlowTemplate(templateId, tpl)" in menu
    assert "function _flowEditorFqn(templateId, tpl)" in menu
    for lang in ("en", "fr", "es"):
        assert "flowEditDraft" in json.load(open(f"tasks/io/chat_ui/i18n/{lang}.json", encoding="utf-8"))


def test_running_instance_edit_previews_impact_before_apply():
    services = _text("tasks/io/chat_ui/services.js")
    renderer = _text("tasks/io/chat_ui/resources_render.js")
    resources = _text("tasks/ai/actions/_agentres_k3.py")
    canvas = _text("tasks/io/chat_ui/flow_graph.html")

    assert "function showFlowInstanceMenu(e, instanceId, status, scope, flowFqn)" in services
    assert "action$('flow_runtime_create_draft', { instance_id: instanceId })" in services
    assert "_openFlowEditorTab(d.draft.draft_id, instanceId)" in services
    assert "f.flow_fqn || ''" in renderer
    assert '"flow_fqn": getattr(inst, "flow_fqn", "") or ""' in resources
    assert "const INSTANCE_ID = params.get('instance_id')" in canvas
    assert "keep_draft: !!INSTANCE_ID" in canvas
    assert "function RuntimeImpactDrawer" in canvas
    assert "action: 'flow_runtime_update_preview'" in canvas
    assert "action: 'flow_runtime_update_apply'" in canvas
    assert "preview_token: impact.preview_token, ...policies" in canvas
    assert "removed_queue_policy: removed ? 'drop' : 'reject'" in canvas
    assert "in_flight_policy: inFlight.length ? 'wait' : 'reject'" in canvas
    for lang in ("en", "fr", "es"):
        assert "flowEditRuntime" in json.load(open(f"tasks/io/chat_ui/i18n/{lang}.json", encoding="utf-8"))


def test_repository_ui_exposes_the_complete_authoring_loop():
    menu = _text("tasks/io/chat_ui/resources_flow_templates.js")
    renderer = _text("tasks/io/chat_ui/resources_render.js")
    assert "function _showNewFlowDialog()" in menu
    assert "action$('flow_editor_new'" in menu
    assert "function _showForkFlowDialog(templateId, tpl)" in menu
    assert "action$('flow_editor_fork'" in menu
    assert "function _showFlowVersionsDialog(templateId, tpl)" in menu
    assert "action$('flow_editor_versions'" in menu
    assert "function _showFlowDiffDialog(templateId, tpl)" in menu
    assert "action$('flow_editor_create_draft'" in menu
    assert "action$('flow_editor_diff'" in menu
    assert "_openFlowEditorTab(d.draft.draft_id)" in menu
    # No authoring dialog is ever stuck open: a close ✕, Escape and a Close
    # footer; opening the graph/editor from the Versions dialog closes it.
    assert "function _flowAuthoringDialog(title, bodyHtml)" in menu
    assert "<button data-close-dialog title=" in menu
    assert "if (ev.key === 'Escape')" in menu
    assert "function _flowDialogCloseFooter()" in menu
    assert "'<div data-content>' + escapeHtml(t('loading')) + '</div>' + _flowDialogCloseFooter()" in menu
    assert "overlay.remove(); _editFlowTemplate(button.dataset.edit, { scope })" in menu
    assert "overlay.remove(); _openFlowTemplateGraphTab(button.dataset.view)" in menu
    # Published versions can be deleted (never edited) from the Versions dialog.
    assert "action$('flow_editor_delete_version'" in menu
    assert "canAuthor && versions.length > 1" in menu
    assert 'createOnclick: "_showNewFlowDialog()"' in renderer
    for key in ("flowNew", "flowFork", "flowVersions", "flowDiff",
                "flowDeleteVersion", "flowDeleteVersionConfirm", "flowVersionDeleted"):
        for lang in ("en", "fr", "es"):
            assert key in json.load(open(f"tasks/io/chat_ui/i18n/{lang}.json", encoding="utf-8"))


def test_task_palette_and_properties_use_the_shared_schema_renderer():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    schema_form = _text("tasks/io/chat_ui/schema_form.js")
    serve = _text("tasks/io/serve_chat_ui.py")
    assert '"schema_form.js"' in serve
    assert "window.PawFlowSchemaForm" in schema_form
    assert "action: 'flow_editor_task_catalog'" in source
    assert "application/pawflow-task-type" in source
    assert "screenToFlowPosition" in source
    assert "function nextTaskId(def, taskType)" in source
    assert "function TaskPropertiesDrawer" in source
    assert "action: 'flow_editor_task_schema'" in source
    assert "parameters: task.parameters || {}" in source
    assert "PawFlowSchemaForm.collectSchemaValues" in source
    assert "...current," in source
    assert "...(current.parameters || {}), ...values" in source
    # Opening a newly dropped or existing task must actually mount the editor
    # drawer. Editor context menus must not expose runtime start/stop actions.
    assert "drawer && drawer.kind === 'editTask' && jsx(TaskPropertiesDrawer" in source
    assert "contextMenu.nodeId && !contextMenu.edit && jsx('button'" in source
    assert "kind: contextMenu.edit ? 'editTask' : 'task'" in source


def test_relation_wiring_uses_task_relationships_and_stable_connection_ids():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    assert "function RelationPropertiesDrawer" in source
    assert "relationships: data.relationships" not in source
    assert "const options = Array.isArray(data.relationships) ? data.relationships : [];" in source
    assert "setRelationships(options.length ? options : ['success']);" in source
    assert "nodesConnectable: editing," in source
    assert "onConnect: editing ? onConnect : undefined" in source
    assert "kind: 'editRelation', source: connection.source" in source
    assert "source: edge?.data?.originalSource || edge.source" in source
    assert "target: edge?.data?.originalTarget || edge.target" in source
    assert "function upsertRelationInDraft(def, previousConnectionId, relation)" in source
    assert "connectionIdOf(normalizeRelation(rel))" in source
    assert "max_queue_size" in source
    assert "max_queue_bytes" in source
    assert "flowfile_ttl_seconds" in source
    assert "prioritizer" in source
    assert "removeFromDraft(definition, [], [connectionId])" in source
    assert "drawer && drawer.kind === 'editRelation' && jsx(RelationPropertiesDrawer" in source


def test_flow_resources_are_edited_on_the_canonical_definition():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    assert "parameterNames.map(name => '${flow.' + name + '}')" in source
    assert "function FlowMetadataDrawer" in source
    assert "function FlowParametersDrawer" in source
    assert "function FlowServicesDrawer" in source
    assert "function FlowPortsDrawer" in source
    assert "function ExpressionHelperDrawer" in source
    assert "function parseJsonValue(value, label)" in source
    assert "next.parameters[key] = parseJsonValue" in source
    assert "action: 'flow_editor_service_catalog'" in source
    assert "action: 'flow_editor_service_schema'" in source
    assert "parameters: current?.parameters || {}" in source
    assert "PawFlowSchemaForm.collectSchemaValues" in source
    assert "parameters: { ...(current.parameters || {}), ...values }" in source
    assert "window._flowEditorEmbeddedServices" in source
    assert "...(embedded.services || [])" in source
    assert "next.entries = selectedEntries" in source
    assert "next.exits = selectedExits" in source
    for kind in ("metadata", "parameters", "services", "ports", "expressions"):
        assert f"kind: '{kind}'" in source
    assert "setShowProblems(true)" in source
    assert "autoLayoutDraft(draftRef.current, nodes, edges)" in source


def test_process_groups_and_versioned_subflows_share_the_same_canvas():
    source = _text("tasks/io/chat_ui/flow_graph.html")
    assert "function groupSelectionInDraft(def, taskIds)" in source
    assert "next.groups[groupId]" in source
    assert "function groupAsDefinition(group)" in source
    assert "inline_group_id" in source
    assert "function GroupPropertiesDrawer" in source
    assert "input_ports" in source and "output_ports" in source
    assert "function SubflowPropertiesDrawer" in source
    assert "flow_ref: { path: path.trim(), version: version.trim() }" in source
    assert "parameter_mapping" in source
    assert "port_mapping" in source
    assert "pass_attributes" in source
    assert "editBtn('Group selection'" in source
    assert "kind: 'subflow'" in source
    assert "drawer && drawer.kind === 'group' && jsx(GroupPropertiesDrawer" in source
    assert "drawer && drawer.kind === 'subflow' && jsx(SubflowPropertiesDrawer" in source
    assert "collectGroupTaskIds" in source
