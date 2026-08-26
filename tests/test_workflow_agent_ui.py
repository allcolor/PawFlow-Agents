"""WP9 workflow-agent authoring UI contracts."""

import json
from pathlib import Path

ROOT = Path("tasks/io/chat_ui")


def test_add_agent_dialog_builds_a_validated_workflow_binding():
    source = (ROOT / "resources_create_dialogs.js").read_text(encoding="utf-8")
    assert "list_agent_workflow_versions" in source
    assert "conversation_id: conversationId" in source
    assert "PawFlowSchemaForm.renderSchemaFields" in source
    assert "PawFlowSchemaForm.populateServiceRefs" in source
    assert "PawFlowSchemaForm.collectSchemaValues" in source
    assert "flow_fqn: selectedWorkflow.flow_fqn" in source
    assert "input_port: selectedWorkflow.input_port" in source
    assert "terminal_port: selectedWorkflow.terminal_port" in source
    assert "allowed_effects: selectedWorkflow.allowed_effects" in source
    assert "runtime === 'workflow'" in source
    assert "runtime === 'llm' && !llm" in source
    assert "runtime === 'external_agui'" in source


def test_workflow_agent_labels_exist_in_every_locale():
    keys = {
        "agentRuntimeWorkflow", "workflowExactFlow", "workflowSelectFlow",
        "workflowContractParameters", "workflowPreemptPolicy", "workflowLimits",
        "workflowMaxDuration", "workflowMaxLlmCalls", "workflowMaxFlowFiles",
        "workflowMaxFanout", "workflowFlowRequiredMessage",
        "workflowRequiredParameters", "workflowPositiveLimits",
        "workflowServiceSelect", "workflowServiceDisabled",
        "workflowUpgrade", "workflowRunsMenu", "workflowRunsTitle",
        "workflowRunsEmpty", "workflowRunInspect", "workflowRunStatus",
        "workflowRunFlow", "workflowRunGeneration", "workflowRunCreated",
        "workflowRunUpdated", "workflowRunFailure", "workflowRunUsage",
        "workflowRunStages", "workflowRunNoStages", "workflowRunTerminal",
        "workflowRunMessageCommit", "workflowRunInboxAck",
        "workflowRunEventDelivery", "workflowRunRetry",
        "workflowRunRetryConfirm", "workflowRunRetryStarted",
        "workflowRunDeleteConfirm", "workflowRunLoadFailed",
        "workflowRunZoomIn", "workflowRunZoomOut", "workflowRunResetView",
        "workflowRunCurrentStage",
        "workflowRunExecution", "workflowRunNoExecution",
    }
    for locale in ("en", "fr", "es"):
        data = json.loads((ROOT / "i18n" / f"{locale}.json").read_text(
            encoding="utf-8"))
        assert keys <= set(data)


def test_workflow_service_fields_distinguish_required_and_disabled():
    source = (ROOT / "schema_form.js").read_text(encoding="utf-8")
    workflow = json.loads(Path(
        "data/repository/flows/global/pawflow/agents/wiki/versions/1.0.0.json"
    ).read_text(encoding="utf-8"))
    assert "data-service-required" in source
    assert "workflowServiceSelect" in source
    assert "workflowServiceDisabled" in source
    assert "<option value=\"\">(auto)</option>" not in source
    assert {
        workflow["agent_contract"]["parameters"][name]["capability"]
        for name in ("extractor_llm", "writer_llm", "reviewer_llm")
    } == {"llm_resolvable"}


def test_agent_configuration_supports_explicit_exact_version_upgrade():
    source = (ROOT / "resources_menus.js").read_text(encoding="utf-8")
    helpers = (ROOT / "workflow_agent_forms.js").read_text(encoding="utf-8")
    renderer = (ROOT / "resources_render.js").read_text(encoding="utf-8")
    serve = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    assert "mountWorkflowAgentForm" in source
    assert "id=\"acc-upgrade\"" in source
    assert "workflowController.isUpgrade()" in source
    assert "workflow: workflow" in source
    assert "getBinding" in helpers and "flow_fqn: workflow.flow_fqn" in helpers
    assert "aRuntime === 'workflow'" in renderer
    assert "workflow_agent_forms.js" in serve


def test_flow_editor_offers_agent_workflow_starter_and_strict_validation():
    dialog = (ROOT / "resources_flow_templates.js").read_text(encoding="utf-8")
    authoring = Path("core/flow_authoring.py").read_text(encoding="utf-8")
    assert 'value="agent_workflow"' in dialog
    assert "template_kind: overlay.querySelector('#_feTemplateKind').value" in dialog
    assert 'definition.get("kind") == "agent_workflow"' in authoring
    assert "validate_agent_workflow_definition(definition)" in authoring


def test_workflow_run_inspector_is_accessible_redacted_and_recovery_aware():
    inspector = (ROOT / "workflow_run_inspector.js").read_text(encoding="utf-8")
    menus = (ROOT / "resources_menus.js").read_text(encoding="utf-8")
    renderer = (ROOT / "resources_render.js").read_text(encoding="utf-8")
    serve = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")

    assert "workflow_run_snapshot" in inspector
    assert "list_workflow_runs" not in inspector
    assert "inspect_workflow_run" not in inspector
    assert "retry_workflow_run" in inspector
    assert "run.safe_retry" in inspector
    assert 'role="dialog" aria-modal="true"' in inspector
    assert 'aria-live="polite"' in inspector
    assert 'role="listitem"><button type="button"' in inspector
    assert "event.data || {}" in inspector
    assert "_workflowRunGroupMetaHtml" in inspector
    assert "_workflowFlowHtml" in inspector
    assert "run.flow_graph" in inspector
    assert "<svg" in inspector
    assert "marker-end" in inspector
    assert "graph.relations" in inspector
    assert "task.status" in inspector
    assert "data-run-selected" in inspector
    assert "document.visibilityState === 'hidden'" in inspector
    assert "function scheduleRefresh(delay)" in inspector
    assert "window.addEventListener('pawflow:workflow-progress'" in inspector
    assert "window.removeEventListener('pawflow:workflow-progress'" in inspector
    assert "if (refreshTimer) clearTimeout(refreshTimer)" in inspector
    assert "run.error" in inspector
    assert 'role="progressbar"' in inspector
    assert "currentStep" in inspector
    assert "data-workflow-flow-svg" in inspector
    assert "data-flow-zoom" in inspector
    assert "addEventListener('wheel'" in inspector
    assert "addEventListener('pointermove'" in inspector
    assert "previousViewBox" in inspector
    assert "data-workflow-run-metadata" in inspector
    assert "previousMetadataOpen" in inspector
    assert "nextMetadata.open = true" in inspector
    assert "workflowRunCurrentStage" in inspector
    assert "data-workflow-run-execution" in inspector
    assert "workflowRunExecution" in inspector
    assert "executionEvents" in inspector
    assert "_workflowRunStructuredValueHtml" in inspector
    assert "_workflowRunMessageValueHtml" in inspector
    assert "workflowRunStructuredIncomplete" in inspector
    assert "data.structured_content" in inspector
    assert "<textarea" not in inspector
    assert "data.arguments" in inspector
    assert "_workflowRunStructuredValueHtml(data.arguments)" in inspector
    assert "latestReturn" in inspector
    assert '<details data-workflow-run-metadata style=' in inspector
    assert "delete_workflow_run" in inspector
    assert "run.can_delete" in inspector
    assert "workflowRunDeleteConfirm" in inspector
    assert "data.usage || data.token_usage" in inspector
    assert "_workflowRunMessageValueHtml(data)" in inspector
    assert "JSON.parse(trimmed)" in inspector
    assert "let refreshPending = false" in inspector
    assert "if (refreshing) { refreshPending = true; return; }" in inspector
    assert "if (refreshPending) scheduleRefresh(0)" in inspector
    assert "source_body" not in inspector
    assert "runtimeKind === 'workflow'" in menus
    assert "showWorkflowRunInspector(name)" in menus
    assert "_pfpJsArg(aRuntime)" in renderer
    assert '"workflow_run_inspector.js"' in serve


def test_workflow_progress_drives_active_agents_and_typing_immediately():
    active = (ROOT / "active_agents.js").read_text(encoding="utf-8")
    sse = (ROOT / "sse_handlers_a.js").read_text(encoding="utf-8")

    assert "function trackWorkflowProgress(data)" in active
    assert "trackAgentStart(agentName, '', '', turnId)" in active
    assert "info.status = status.replace(/_/g, ' ')" in active
    assert "addEventListener('workflow_progress'" in sse
    assert "trackWorkflowProgress(data)" in sse
    assert "new CustomEvent('pawflow:workflow-progress'" in sse
    assert "runtimeKind: a.runtime_kind" in active
    assert "workflowRunId: a.workflow_run_id" in active
    assert "onclick=\"showWorkflowRunInspector(" in active
    assert "info.runtimeKind === 'workflow'" in active
