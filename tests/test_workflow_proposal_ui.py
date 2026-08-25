from pathlib import Path

ROOT = Path(__file__).parents[1]


def _text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_proposals_are_projected_into_generic_surfaces():
    loader = _text("tasks/io/chat_ui/ui_surface_loader.js")
    runtime = _text("tasks/io/chat_ui/ui_surfaces.js")
    conversations = _text("tasks/io/chat_ui/conversations.js")
    sse = _text("tasks/io/chat_ui/sse_handlers_b.js")
    serve = _text("tasks/io/serve_chat_ui.py")
    assert '"ui_surfaces.js", "ui_surface_loader.js"' in serve
    assert "loadUiSurfaces(cid)" in conversations
    assert "ui_surface_list" in loader
    assert "data.surfaces" in loader
    assert "pawflow.ui-surface.v1" in runtime
    assert "workflow_proposal_submit_to_planner" not in runtime
    assert "workflow_proposal_accept" not in runtime
    assert "workflow_proposal_cancel" not in runtime
    assert "workflow_proposal_created" not in sse
    assert "workflow_proposal_updated" not in sse
    assert "ui_surface_upserted" in sse


def test_generic_runtime_negotiates_rich_semantic_and_handoff_modes():
    runtime = _text("tasks/io/chat_ui/ui_surfaces.js")
    extension_runtime = _text("tasks/io/chat_ui/ext_runtime.js")
    assert "UI_SURFACE_CAPABILITIES" in runtime
    assert "_uiSurfaceHasCapabilities" in runtime
    assert "presentation.component" in runtime
    assert "ext.renderComponent" in runtime
    assert "action.handoff" in runtime
    assert "Open compatible client" in runtime
    assert "component: function (componentId, renderFn)" in extension_runtime
    assert "undeclared component" in extension_runtime


def test_proposal_opens_same_editor_and_can_submit_saved_revision():
    runtime = _text("tasks/io/chat_ui/ui_surfaces.js")
    services = _text("tasks/io/chat_ui/services.js")
    canvas = _text("tasks/io/chat_ui/flow_graph.html")
    assert "_openFlowEditorTab(args.draft_id || '', '', args.proposal_id || '')" in runtime
    assert "window.__PAWFLOW_WORKFLOW_PROPOSAL_ID" in services
    assert "const WORKFLOW_PROPOSAL_ID = params.get('proposal_id')" in canvas
    assert "if (!await saveNow()) return;" in canvas
    assert "const comment = window.prompt('Comment for the planner (optional)'" in canvas
    assert "comment," in canvas
    assert "editBtn('Send to planner', sendToPlanner" in canvas


def test_web_semantic_card_renders_bounded_workflow_mini_graph():
    runtime = _text("tasks/io/chat_ui/ui_surfaces.js")
    css = _text("tasks/io/chat_ui/css/80_dialogs.css")
    assert "pawflow.builtin:workflow-mini-graph" in runtime
    assert "_renderWorkflowMiniGraph(surface, card)" in runtime
    assert "document.createElementNS(ns, 'line')" in runtime
    assert ".ui-surface-mini-graph" in css
