"""Workflow-agent run inspection and safe recovery actions."""

import json

from tasks.ai.actions._agentres_base import _UNHANDLED


def _reply(flowfile, payload, status=None):
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode())
    if status is not None:
        flowfile.set_attribute("http.response.status", str(status))
    return [flowfile]


def _handle_agentres_k8(self, action, body, store, user_id, flowfile):
    if action == "workflow_operations":
        conv_id = str(body.get("conversation_id") or "").strip()
        if not conv_id:
            return _reply(flowfile, {"error": "Missing conversation_id"}, 400)
        from core.workflow_run_inspector import workflow_operational_summary
        return _reply(flowfile, {"operations": workflow_operational_summary(
            conv_id, str(body.get("agent_name") or ""),
            backlog_alert=max(
                1, min(10000, int(body.get("backlog_alert") or 100))))})

    if action in {"list_workflow_runs", "workflow_run_snapshot"}:
        conv_id = str(body.get("conversation_id") or "").strip()
        if not conv_id:
            return _reply(flowfile, {"error": "Missing conversation_id"}, 400)
        from core.workflow_agent_runtime import WorkflowAgentRuntime
        from core.workflow_run_inspector import (
            inspect_workflow_run, list_workflow_runs,
        )
        from core.workflow_run_store import WorkflowRunStore
        runtime = WorkflowAgentRuntime.instance()
        run_store = WorkflowRunStore.instance()
        live_run_ids = runtime.live_run_ids(conv_id)
        runs = list_workflow_runs(
            conv_id, str(body.get("agent_name") or ""),
            max(1, min(200, int(body.get("limit") or 50))),
            store=run_store, live_run_ids=live_run_ids)
        if action == "list_workflow_runs":
            return _reply(flowfile, {"runs": runs})
        run_id = str(body.get("run_id") or "").strip()
        if not run_id and runs:
            run_id = str(runs[0].get("run_id") or "")
        projection = None
        if run_id:
            stored = run_store.get_run(run_id)
            if stored is None or stored.get("conversation_id") != conv_id:
                return _reply(flowfile, {"error": "Workflow run not found"}, 404)
            projection = inspect_workflow_run(
                run_id, store=run_store, live_run_ids=live_run_ids)
        return _reply(flowfile, {"runs": runs, "run": projection})

    if action in {"inspect_workflow_run", "retry_workflow_run"}:
        conv_id = str(body.get("conversation_id") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        if not conv_id or not run_id:
            return _reply(
                flowfile, {"error": "Missing conversation_id or run_id"}, 400)
        from core.workflow_run_store import WorkflowRunStore
        run_store = WorkflowRunStore.instance()
        run = run_store.get_run(run_id)
        if run is None or run.get("conversation_id") != conv_id:
            return _reply(flowfile, {"error": "Workflow run not found"}, 404)
        from core.workflow_agent_runtime import WorkflowAgentRuntime
        runtime = WorkflowAgentRuntime.instance()
        from core.workflow_run_inspector import inspect_workflow_run
        projection = inspect_workflow_run(
            run_id, store=run_store,
            live_run_ids=runtime.live_run_ids(conv_id))
        if action == "inspect_workflow_run":
            return _reply(flowfile, {"run": projection})
        if not projection["safe_retry"]:
            return _reply(
                flowfile, {"error": "Workflow run is not safely recoverable"}, 409)
        result = (
            runtime.retry(run_id)
            if run.get("status") == "retryable_failed"
            else runtime.recover(run_id))
        if result is None:
            return _reply(
                flowfile, {"error": "Workflow run recovery could not be acquired"}, 409)
        return _reply(flowfile, {"ok": True, "recovery": result})

    if action == "delete_workflow_run":
        conv_id = str(body.get("conversation_id") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        if not conv_id or not run_id:
            return _reply(
                flowfile, {"error": "Missing conversation_id or run_id"}, 400)
        from core.workflow_run_store import WorkflowRunStore
        run_store = WorkflowRunStore.instance()
        run = run_store.get_run(run_id)
        if run is None or run.get("conversation_id") != conv_id:
            return _reply(flowfile, {"error": "Workflow run not found"}, 404)
        if not run_store.delete_terminal(run_id, conv_id):
            return _reply(
                flowfile, {"error": "Only terminal workflow runs can be deleted"},
                409)
        return _reply(flowfile, {"ok": True})

    return _UNHANDLED
