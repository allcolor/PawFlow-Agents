"""Workflow-agent run inspection and safe recovery actions."""

import json
from datetime import datetime, timezone

from tasks.ai.actions._agentres_base import _UNHANDLED


def _reply(flowfile, payload, status=None):
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode())
    if status is not None:
        flowfile.set_attribute("http.response.status", str(status))
    return [flowfile]


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _kanban_context(body, conv_id, run_store, runtime):
    """Resolve one conversation-scoped run and its exact redacted graph."""

    run_id = str(body.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Missing run_id")
    run = run_store.get_run(run_id)
    agent_name = str(body.get("agent_name") or "").strip()
    if (
        run is None
        or run.get("conversation_id") != conv_id
        or (agent_name and str(run.get("agent_name") or "").casefold() != agent_name.casefold())
    ):
        raise KeyError("Workflow run not found")
    from core.workflow_run_inspector import inspect_workflow_run

    live_run_ids = runtime.live_run_ids(conv_id)
    projection = inspect_workflow_run(run_id, store=run_store, live_run_ids=live_run_ids)
    if projection is None:
        raise KeyError("Workflow run not found")
    return run, projection, live_run_ids


def _kanban_task_exists(projection, task_id):
    if not task_id:
        return True
    graph = projection.get("flow_graph") or {}
    return any(
        str(task.get("id") or "") == task_id
        for task in graph.get("tasks", [])
        if isinstance(task, dict)
    )


def _kanban_audit_event(events, event_type, idempotency_key):
    for event in reversed(list(events)):
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event.get("event_type") == event_type and data.get("idempotency_key") == idempotency_key:
            return event
    return None


def _kanban_audit_data(plan, user_id, command_id, *, result=None):
    value = {
        "command_id": command_id,
        "actor_user_id": str(user_id),
        "command": plan.command,
        "result_code": plan.code,
        "source_lane": plan.source_lane,
        "target_lane": plan.target_lane,
        "task_id": plan.task_id,
        "created_at": _utc_now(),
        "plan": plan.to_dict(),
    }
    if result is not None:
        value["result"] = result
    return value


def _kanban_require_generation(body, run):
    raw = body.get("expected_generation")
    if isinstance(raw, bool) or raw in (None, ""):
        raise ValueError("expected_generation is required")
    try:
        expected = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("expected_generation must be an integer") from exc
    if expected != int(run.get("run_generation") or 0):
        raise RuntimeError("Workflow run generation changed; refresh the board")
    return expected


def _kanban_latest_review(events, task_id):
    for event in reversed(list(events)):
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if (
            event.get("event_type") == "kanban_review"
            and str(data.get("task_id") or "") == task_id
        ):
            return data
    return None


def _kanban_publish(run, event, task_id=""):
    from core.workflow_kanban import publish_workflow_kanban_update

    publish_workflow_kanban_update(run, event, task_id)


def _handle_agentres_k8(self, action, body, store, user_id, flowfile):
    if action == "workflow_operations":
        conv_id = str(body.get("conversation_id") or "").strip()
        if not conv_id:
            return _reply(flowfile, {"error": "Missing conversation_id"}, 400)
        from core.workflow_run_inspector import workflow_operational_summary

        return _reply(
            flowfile,
            {
                "operations": workflow_operational_summary(
                    conv_id,
                    str(body.get("agent_name") or ""),
                    backlog_alert=max(1, min(10000, int(body.get("backlog_alert") or 100))),
                )
            },
        )

    if action == "workflow_kanban_snapshot":
        conv_id = str(body.get("conversation_id") or "").strip()
        if not conv_id:
            return _reply(flowfile, {"error": "Missing conversation_id"}, 400)
        try:
            from core.workflow_agent_runtime import WorkflowAgentRuntime
            from core.file_store import FileStore
            from core.workflow_kanban import workflow_kanban_snapshot
            from core.workflow_run_store import WorkflowRunStore

            runtime = WorkflowAgentRuntime.instance()
            snapshot = workflow_kanban_snapshot(
                conv_id,
                str(body.get("agent_name") or ""),
                str(body.get("run_id") or ""),
                int(body.get("limit") or 100),
                cursor=str(body.get("cursor") or ""),
                store=WorkflowRunStore.instance(),
                live_run_ids=runtime.live_run_ids(conv_id),
                user_id=str(user_id or ""),
                file_store=FileStore.instance(),
                workers=runtime.active_snapshot(conv_id),
            )
        except KeyError:
            return _reply(flowfile, {"error": "Workflow run not found"}, 404)
        except (TypeError, ValueError) as error:
            return _reply(flowfile, {"error": str(error)}, 400)
        return _reply(flowfile, snapshot)

    if action in (
        "workflow_kanban_comment",
        "workflow_kanban_assign",
        "workflow_kanban_attach",
        "workflow_kanban_review",
    ):
        conv_id = str(body.get("conversation_id") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        if not conv_id or not run_id:
            return _reply(flowfile, {"error": "Missing conversation_id or run_id"}, 400)
        if not str(user_id or "").strip():
            return _reply(flowfile, {"error": "Authentication required"}, 401)
        from core.workflow_agent_runtime import WorkflowAgentRuntime
        from core.workflow_run_store import WorkflowRunStore

        runtime = WorkflowAgentRuntime.instance()
        run_store = WorkflowRunStore.instance()
        try:
            run, projection, _live = _kanban_context(body, conv_id, run_store, runtime)
            _kanban_require_generation(body, run)
        except ValueError as error:
            return _reply(flowfile, {"error": str(error)}, 400)
        except RuntimeError as error:
            return _reply(flowfile, {"error": str(error), "code": "stale_generation"}, 409)
        except KeyError:
            return _reply(flowfile, {"error": "Workflow run not found"}, 404)
        task_id = str(body.get("task_id") or "").strip()
        if not _kanban_task_exists(projection, task_id):
            return _reply(flowfile, {"error": "Workflow task not found"}, 404)
        from core.workflow_run_inspector import _safe_text

        from core.workflow_kanban import validate_command_id

        try:
            mutation_id = validate_command_id(body.get("idempotency_key"))
        except ValueError as error:
            return _reply(flowfile, {"error": str(error)}, 400)
        event_type = {
            "workflow_kanban_comment": "kanban_comment",
            "workflow_kanban_assign": "kanban_assignment",
            "workflow_kanban_attach": "kanban_attachment_added",
            "workflow_kanban_review": "kanban_review",
        }[action]
        existing_mutation = _kanban_audit_event(
            run_store.list_events(run_id), event_type, mutation_id
        )

        if action == "workflow_kanban_comment":
            raw_body = str(body.get("body") or "").replace("\x00", "").strip()
            if not raw_body:
                return _reply(flowfile, {"error": "Comment body is required"}, 400)
            if len(raw_body) > 4000:
                return _reply(flowfile, {"error": "Comment body is too long"}, 400)
            data = {
                "comment_id": mutation_id,
                "task_id": task_id,
                "author_user_id": str(user_id),
                "author_label": str(user_id),
                "body": _safe_text(raw_body, 4000),
                "created_at": _utc_now(),
            }
        elif action == "workflow_kanban_assign":
            assignee = str(body.get("assignee") or "").replace("\x00", "").strip()
            if not assignee:
                return _reply(flowfile, {"error": "Assignee is required"}, 400)
            if len(assignee) > 160:
                return _reply(flowfile, {"error": "Assignee is too long"}, 400)
            data = {
                "assignment_id": mutation_id,
                "task_id": task_id,
                "assignee": _safe_text(assignee, 160),
                "assigned_by_user_id": str(user_id),
                "created_at": _utc_now(),
            }
        elif action == "workflow_kanban_attach":
            file_id = str(body.get("file_id") or "").strip()
            if not file_id:
                return _reply(flowfile, {"error": "file_id is required"}, 400)
            from core.file_store import FileStore

            try:
                metadata = FileStore.instance().get_metadata_required(
                    file_id,
                    user_id=str(user_id),
                    conversation_id=conv_id,
                )
            except (FileNotFoundError, TypeError, ValueError):
                return _reply(flowfile, {"error": "File attachment not found or denied"}, 403)
            data = {
                "attachment_id": mutation_id,
                "task_id": task_id,
                "file_id": file_id,
                "label": _safe_text(
                    body.get("label") or metadata.get("filename"), 240
                ),
                "added_by_user_id": str(user_id),
                "created_at": _utc_now(),
            }
        else:
            decision = str(body.get("decision") or "").strip()
            if decision not in {"approved", "changes_requested", "reopened"}:
                return _reply(flowfile, {"error": "Invalid review decision"}, 400)
            comment = str(body.get("comment") or "").replace("\x00", "").strip()
            if len(comment) > 4000:
                return _reply(flowfile, {"error": "Review comment is too long"}, 400)
            events = list(run_store.list_events(run_id))
            previous = _kanban_latest_review(events, task_id)
            if not existing_mutation and decision == "reopened" and (
                not previous or previous.get("decision") != "approved"
            ):
                return _reply(
                    flowfile,
                    {"error": "Only an approved review can be reopened"},
                    409,
                )
            data = {
                "review_id": mutation_id,
                "task_id": task_id,
                "decision": decision,
                "reviewer_user_id": str(user_id),
                "comment": _safe_text(comment, 4000),
                "created_at": _utc_now(),
            }
        if existing_mutation:
            existing_data = (
                existing_mutation.get("data")
                if isinstance(existing_mutation.get("data"), dict)
                else {}
            )
            data["created_at"] = existing_data.get("created_at") or data["created_at"]
        try:
            event, created = run_store.append_event_once(
                run_id, event_type, data, idempotency_key=mutation_id
            )
        except ValueError as error:
            return _reply(flowfile, {"error": str(error)}, 409)
        if created:
            _kanban_publish(run, event, task_id)
        return _reply(flowfile, {"ok": True, "duplicate": not created, "event": event})

    if action in (
        "workflow_kanban_plan_command",
        "workflow_kanban_execute_command",
    ):
        conv_id = str(body.get("conversation_id") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        target_lane = str(body.get("target_lane") or "").strip()
        if not conv_id or not run_id or not target_lane:
            return _reply(
                flowfile,
                {"error": "Missing conversation_id, run_id, or target_lane"},
                400,
            )
        from core.confirmation_store import ConfirmationStore
        from core.workflow_agent_runtime import WorkflowAgentRuntime
        from core.workflow_kanban import (
            plan_workflow_kanban_command,
            validate_command_id,
        )
        from core.workflow_run_store import WorkflowRunStore

        runtime = WorkflowAgentRuntime.instance()
        run_store = WorkflowRunStore.instance()
        try:
            run, projection, live_run_ids = _kanban_context(body, conv_id, run_store, runtime)
        except ValueError as error:
            return _reply(flowfile, {"error": str(error)}, 400)
        except KeyError:
            return _reply(flowfile, {"error": "Workflow run not found"}, 404)
        task_id = str(body.get("task_id") or "").strip()
        if not _kanban_task_exists(projection, task_id):
            return _reply(flowfile, {"error": "Workflow task not found"}, 404)
        events = list(run_store.list_events(run_id))
        waits = ConfirmationStore.instance().list_waits_for_instances([f"workflow:{run_id}"])
        plan = plan_workflow_kanban_command(
            run,
            task_id,
            target_lane,
            events,
            graph=projection.get("flow_graph") or {},
            live=run_id in set(live_run_ids),
            safe_retry=bool(projection.get("safe_retry")),
            waits=waits,
        )
        if action == "workflow_kanban_plan_command":
            return _reply(flowfile, {"plan": plan.to_dict()})
        if not str(user_id or "").strip():
            return _reply(flowfile, {"error": "Authentication required"}, 401)
        try:
            _kanban_require_generation(body, run)
            command_id = validate_command_id(body.get("idempotency_key"))
        except ValueError as error:
            return _reply(flowfile, {"error": str(error)}, 400)
        except RuntimeError as error:
            return _reply(flowfile, {"error": str(error), "code": "stale_generation"}, 409)

        succeeded = _kanban_audit_event(events, "kanban_command_succeeded", command_id)
        rejected = _kanban_audit_event(events, "kanban_command_rejected", command_id)
        outcome = succeeded or rejected
        if outcome:
            payload = outcome.get("data") or {}
            status = None if succeeded else 409
            return _reply(
                flowfile,
                {
                    "ok": bool(succeeded),
                    "duplicate": True,
                    "plan": payload.get("plan") or plan.to_dict(),
                    "result": payload.get("result") or {},
                    "event": outcome,
                },
                status,
            )

        if not plan.executable:
            result = {"ok": False, "code": plan.code, "message": plan.message}
            event, created = run_store.append_event_once(
                run_id,
                "kanban_command_rejected",
                _kanban_audit_data(plan, user_id, command_id, result=result),
                idempotency_key=command_id,
            )
            if created:
                _kanban_publish(run, event, task_id)
            return _reply(
                flowfile,
                {
                    "ok": False,
                    "plan": plan.to_dict(),
                    "result": result,
                    "event": event,
                },
                409,
            )

        requested = _kanban_audit_event(events, "kanban_command_requested", command_id)
        if requested:
            return _reply(
                flowfile,
                {
                    "ok": False,
                    "duplicate": True,
                    "plan": plan.to_dict(),
                    "result": {
                        "ok": False,
                        "code": "command_in_progress",
                        "message": "The original command has not recorded an outcome.",
                    },
                    "event": requested,
                },
                409,
            )
        requested, _created = run_store.append_event_once(
            run_id,
            "kanban_command_requested",
            _kanban_audit_data(plan, user_id, command_id),
            idempotency_key=command_id,
        )
        try:
            if plan.command == "retry":
                command_result = runtime.retry(run_id)
                acquired = command_result is not None
            elif plan.command == "cancel":
                acquired = runtime.cancel_run(run_id, "kanban_cancel", force=False)
                command_result = {"status": "cancelling"} if acquired else None
            elif plan.command == "force_stop":
                acquired = runtime.cancel_run(run_id, "kanban_force_stop", force=True)
                command_result = {"status": "force_stopped"} if acquired else None
            elif plan.command == "open_interaction":
                acquired = bool(plan.interaction)
                command_result = {"interaction": plan.interaction}
            else:
                acquired = False
                command_result = None
            if not acquired:
                raise RuntimeError("Workflow command could not be acquired")
            result = {"ok": True, "code": plan.code, "value": command_result}
            event, _created = run_store.append_event_once(
                run_id,
                "kanban_command_succeeded",
                _kanban_audit_data(plan, user_id, command_id, result=result),
                idempotency_key=command_id,
            )
            _kanban_publish(run, event, task_id)
            return _reply(
                flowfile,
                {
                    "ok": True,
                    "plan": plan.to_dict(),
                    "result": result,
                    "event": event,
                },
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            from core.workflow_run_inspector import _safe_text

            result = {
                "ok": False,
                "code": "command_not_acquired",
                "message": _safe_text(error, 240),
            }
            event, _created = run_store.append_event_once(
                run_id,
                "kanban_command_rejected",
                _kanban_audit_data(plan, user_id, command_id, result=result),
                idempotency_key=command_id,
            )
            _kanban_publish(run, event, task_id)
            return _reply(
                flowfile,
                {
                    "ok": False,
                    "plan": plan.to_dict(),
                    "result": result,
                    "event": event,
                },
                409,
            )

    if action in {"list_workflow_runs", "workflow_run_snapshot"}:
        conv_id = str(body.get("conversation_id") or "").strip()
        if not conv_id:
            return _reply(flowfile, {"error": "Missing conversation_id"}, 400)
        from core.workflow_agent_runtime import WorkflowAgentRuntime
        from core.workflow_run_inspector import (
            inspect_workflow_run,
            list_workflow_runs,
        )
        from core.workflow_run_store import WorkflowRunStore

        runtime = WorkflowAgentRuntime.instance()
        run_store = WorkflowRunStore.instance()
        live_run_ids = runtime.live_run_ids(conv_id)
        runs = list_workflow_runs(
            conv_id,
            str(body.get("agent_name") or ""),
            max(1, int(body.get("limit") or 50)),
            store=run_store,
            live_run_ids=live_run_ids,
        )
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
            projection = inspect_workflow_run(run_id, store=run_store, live_run_ids=live_run_ids)
        return _reply(flowfile, {"runs": runs, "run": projection})

    if action in {"inspect_workflow_run", "retry_workflow_run"}:
        conv_id = str(body.get("conversation_id") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        if not conv_id or not run_id:
            return _reply(flowfile, {"error": "Missing conversation_id or run_id"}, 400)
        from core.workflow_run_store import WorkflowRunStore

        run_store = WorkflowRunStore.instance()
        run = run_store.get_run(run_id)
        if run is None or run.get("conversation_id") != conv_id:
            return _reply(flowfile, {"error": "Workflow run not found"}, 404)
        from core.workflow_agent_runtime import WorkflowAgentRuntime

        runtime = WorkflowAgentRuntime.instance()
        from core.workflow_run_inspector import inspect_workflow_run

        projection = inspect_workflow_run(
            run_id, store=run_store, live_run_ids=runtime.live_run_ids(conv_id)
        )
        if action == "inspect_workflow_run":
            return _reply(flowfile, {"run": projection})
        if not projection["safe_retry"]:
            return _reply(flowfile, {"error": "Workflow run is not safely recoverable"}, 409)
        result = (
            runtime.retry(run_id)
            if run.get("status") == "retryable_failed"
            else runtime.recover(run_id)
        )
        if result is None:
            return _reply(flowfile, {"error": "Workflow run recovery could not be acquired"}, 409)
        return _reply(flowfile, {"ok": True, "recovery": result})

    if action == "delete_workflow_run":
        conv_id = str(body.get("conversation_id") or "").strip()
        run_id = str(body.get("run_id") or "").strip()
        if not conv_id or not run_id:
            return _reply(flowfile, {"error": "Missing conversation_id or run_id"}, 400)
        from core.workflow_run_store import WorkflowRunStore

        run_store = WorkflowRunStore.instance()
        run = run_store.get_run(run_id)
        if run is None or run.get("conversation_id") != conv_id:
            return _reply(flowfile, {"error": "Workflow run not found"}, 404)
        if not run_store.delete_terminal(run_id, conv_id):
            return _reply(flowfile, {"error": "Only terminal workflow runs can be deleted"}, 409)
        return _reply(flowfile, {"ok": True})

    return _UNHANDLED
