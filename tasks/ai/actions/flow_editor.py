"""Flow Editor actions — the HTTP face of ``FlowAuthoringService``.

No authoring logic lives here: every action validates its input, checks
permissions and delegates to the domain service (docs/flow_editor.md).

Permissions (server-side, never trusted from the client):
- a session user is required for everything;
- drafts are private to the user who created them;
- ``global`` scope (new / fork / publish) requires the admin role;
- ``conv`` scope requires a ``conversation_id``.

Optimistic locking: ``flow_editor_save_draft`` carries ``base_revision``;
a stale revision answers HTTP 409 ``{"error": "draft_changed_elsewhere"}``.
"""

import json
import logging

from core.flow_authoring import (
    DraftConflict,
    DraftNotFound,
    FlowAuthoringService,
    FlowValidationFailed,
    normalize_scope,
)

logger = logging.getLogger(__name__)

_ACTIONS = {
    "flow_editor_get", "flow_editor_versions", "flow_editor_delete_version",
    "flow_editor_new",
    "flow_editor_fork", "flow_editor_create_draft", "flow_editor_load_draft",
    "flow_editor_list_drafts", "flow_editor_save_draft",
    "flow_editor_discard_draft", "flow_editor_validate", "flow_editor_diff",
    "flow_editor_publish", "flow_editor_task_catalog",
    "flow_editor_task_schema", "flow_editor_service_catalog",
    "flow_editor_service_schema",
    "flow_editor_declarative_catalog", "flow_editor_declarative_project",
    "flow_editor_declarative_preview", "flow_editor_declarative_apply",
}


def _is_admin(flowfile) -> bool:
    roles = flowfile.get_attribute("http.auth.roles") or ""
    return "admin" in roles


def _scope_gate(scope: str, conv_id: str, flowfile, *, write: bool) -> str:
    """Empty string when allowed, else the refusal message."""
    if scope == "global" and write and not _is_admin(flowfile):
        return "Global scope requires admin role"
    if scope == "conv" and not conv_id:
        return "conversation_id is required for conversation scope"
    return ""


def _handle_flow_editor(self, action, body, store, user_id, flowfile):
    """Handle flow editor actions. Returns [flowfile] or None."""
    if action not in _ACTIONS:
        return None

    def _reply(payload, status=""):
        flowfile.set_content(json.dumps(payload, ensure_ascii=False, default=str).encode())
        if status:
            flowfile.set_attribute("http.response.status", status)
        return [flowfile]

    if not user_id:
        return _reply({"error": "Authentication required"}, "401")

    service = FlowAuthoringService.instance()
    conv_id = str(body.get("conversation_id", "") or "")
    try:
        # ── catalogs (no scope) ──────────────────────────────────
        if action == "flow_editor_task_catalog":
            return _reply({"tasks": service.task_catalog()})
        if action == "flow_editor_task_schema":
            params = body.get("parameters")
            return _reply(service.task_schema(
                str(body.get("task_type", "") or ""),
                params if isinstance(params, dict) else {}))
        if action == "flow_editor_service_catalog":
            return _reply({"services": service.service_catalog()})
        if action == "flow_editor_service_schema":
            params = body.get("parameters")
            return _reply(service.service_schema(
                str(body.get("service_type", "") or ""),
                params if isinstance(params, dict) else {}))
        if action == "flow_editor_declarative_catalog":
            from core.declarative_flow.registry import DeclarativeBlockRegistry
            return _reply({"schema_version": 1,
                           "blocks": DeclarativeBlockRegistry.catalog()})
        if action == "flow_editor_declarative_project":
            definition = body.get("definition")
            if not isinstance(definition, dict):
                return _reply({"error": "definition is required"}, "400")
            from core.declarative_flow.projection import project_definition
            return _reply(project_definition(definition))
        if action == "flow_editor_validate":
            definition = body.get("definition")
            if not isinstance(definition, dict):
                return _reply({"error": "definition is required"}, "400")
            return _reply(service.validate(definition))

        # ── published flows ──────────────────────────────────────
        if action in ("flow_editor_get", "flow_editor_versions"):
            fqn = str(body.get("fqn", "") or "")
            scope = normalize_scope(body.get("scope", "user"))
            if not fqn:
                return _reply({"error": "fqn is required"}, "400")
            denied = _scope_gate(scope, conv_id, flowfile, write=False)
            if denied:
                return _reply({"error": denied}, "400")
            if action == "flow_editor_get":
                return _reply({"flow": service.load(fqn, scope, user_id=user_id,
                                                    conv_id=conv_id),
                               "scope": scope})
            return _reply(service.versions(fqn, scope, user_id=user_id, conv_id=conv_id))

        if action == "flow_editor_delete_version":
            fqn = str(body.get("fqn", "") or "")
            scope = normalize_scope(body.get("scope", "user"))
            if not fqn:
                return _reply({"error": "fqn is required"}, "400")
            denied = _scope_gate(scope, conv_id, flowfile, write=True)
            if denied:
                return _reply({"error": denied}, "403")
            result = service.delete_version(fqn, scope, user_id=user_id, conv_id=conv_id)
            from tasks.ai.actions.agent_resource import invalidate_flow_templates_cache
            invalidate_flow_templates_cache(user_id)
            return _reply({"ok": True, **result})

        if action in ("flow_editor_new", "flow_editor_fork"):
            scope = normalize_scope(body.get("scope", "user"))
            denied = _scope_gate(scope, conv_id, flowfile, write=True)
            if denied:
                return _reply({"error": denied}, "403")
            package = str(body.get("package", "") or "")
            name = str(body.get("name", "") or "")
            version = str(body.get("version", "") or "1.0.0")
            if action == "flow_editor_new":
                draft = service.new(package, name, version, scope, user_id,
                                    conv_id=conv_id,
                                    description=str(body.get("description", "") or ""),
                                    template_kind=str(
                                        body.get("template_kind") or "standard"))
            else:
                source_fqn = str(body.get("source_fqn", "") or "")
                if not source_fqn:
                    return _reply({"error": "source_fqn is required"}, "400")
                draft = service.fork(
                    source_fqn, str(body.get("source_scope", "global") or "global"),
                    package, name, version, scope, user_id, conv_id=conv_id)
            return _reply({"draft": draft})

        # ── drafts ───────────────────────────────────────────────
        if action == "flow_editor_create_draft":
            fqn = str(body.get("fqn", "") or "")
            scope = normalize_scope(body.get("scope", "user"))
            if not fqn:
                return _reply({"error": "fqn is required"}, "400")
            denied = _scope_gate(scope, conv_id, flowfile, write=True)
            if denied:
                return _reply({"error": denied}, "403")
            draft = service.create_draft(
                fqn, scope, user_id, conv_id=conv_id,
                reuse_existing=bool(body.get("reuse_existing", True)))
            return _reply({"draft": draft})
        if action == "flow_editor_list_drafts":
            return _reply({"drafts": service.list_drafts(user_id)})

        draft_id = str(body.get("draft_id", "") or "")
        if not draft_id:
            return _reply({"error": "draft_id is required"}, "400")
        if action == "flow_editor_load_draft":
            return _reply({"draft": service.load_draft(draft_id, user_id)})
        if action in (
                "flow_editor_declarative_preview",
                "flow_editor_declarative_apply"):
            if body.get("base_revision") is None:
                return _reply({"error": "base_revision is required"}, "400")
            operation = body.get("operation")
            if not isinstance(operation, dict):
                return _reply({"error": "operation is required"}, "400")
            return _reply(service.apply_declarative_operation(
                draft_id, user_id, operation, body["base_revision"],
                preview=action.endswith("_preview")))
        if action == "flow_editor_save_draft":
            definition = body.get("definition")
            if not isinstance(definition, dict):
                return _reply({"error": "definition is required"}, "400")
            if body.get("base_revision") is None:
                return _reply({"error": "base_revision is required"}, "400")
            draft = service.save_draft(draft_id, user_id, definition,
                                       body.get("base_revision"))
            proposal = None
            from core.workflow_proposal_store import (
                WorkflowProposalStore,
                definition_digest,
            )
            proposal = WorkflowProposalStore.instance().note_draft_changed(
                draft_id=draft_id, draft_revision=int(draft["revision"]),
                digest=definition_digest(draft["definition"]),
                actor_id=user_id)
            if proposal is not None:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    proposal["conversation_id"],
                    "workflow_proposal_updated", {"proposal": proposal})
            return _reply({"ok": True, "draft_id": draft_id,
                           "revision": draft["revision"],
                           "updated_at": draft["updated_at"],
                           "workflow_proposal": proposal})
        if action == "flow_editor_discard_draft":
            return _reply({"ok": service.discard_draft(draft_id, user_id)})
        if action == "flow_editor_diff":
            return _reply(service.diff_draft(draft_id, user_id))
        if action == "flow_editor_publish":
            draft = service.load_draft(draft_id, user_id)
            denied = _scope_gate(draft["scope"], draft.get("conv_id", ""),
                                 flowfile, write=True)
            if denied:
                return _reply({"error": denied}, "403")
            result = service.publish(
                draft_id, user_id, str(body.get("version", "") or ""),
                keep_draft=bool(body.get("keep_draft", False)))
            return _reply({"ok": True, **result})
        return None

    except DraftConflict as exc:
        return _reply({"error": exc.code, "draft_id": exc.draft_id,
                       "base_revision": exc.base_revision,
                       "current_revision": exc.current_revision}, "409")
    except FlowValidationFailed as exc:
        return _reply({"error": "validation_failed", "validation": exc.report}, "422")
    except DraftNotFound as exc:
        return _reply({"error": f"Draft not found: {exc.args[0] if exc.args else ''}"}, "404")
    except KeyError as exc:
        return _reply({"error": str(exc.args[0] if exc.args else exc)}, "404")
    except ValueError as exc:
        return _reply({"error": str(exc)}, "400")
    except Exception as exc:
        logger.exception("flow editor action '%s' failed", action)
        return _reply({"error": str(exc)}, "500")


__all__ = ["_handle_flow_editor"]
