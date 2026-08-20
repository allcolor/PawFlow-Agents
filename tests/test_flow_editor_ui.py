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


def test_repository_menu_opens_a_draft_in_the_same_canvas():
    services = _text("tasks/io/chat_ui/services.js")
    assert "function _openFlowEditorTab(draftId)" in services
    assert "'/chat/js/flow_graph.html?draft_id='" in services
    assert "action$('flow_editor_create_draft', payload" in services
    menu = _text("tasks/io/chat_ui/resources_flow_templates.js")
    assert "_editFlowTemplate(templateId, tpl)" in menu
    for lang in ("en", "fr", "es"):
        assert "flowEditDraft" in json.load(open(f"tasks/io/chat_ui/i18n/{lang}.json", encoding="utf-8"))
