"""AgentLoopTask actions — misc (model, theme, effort, fast, plan, doctor, fork)"""

import json
import logging
import time
from typing import Dict, Any, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_misc(self, action, body, store, user_id, flowfile):
    """Handle misc actions. Returns [flowfile] or None."""

    if action == "model":
        model_value = body.get("model", "").strip()
        agent_name = body.get("agent", "").strip()
        conv_id = body.get("conversation_id", "")
        override_key = f"model_override:{agent_name}"
        if not model_value or model_value == "reset":
            if conv_id:
                store.set_extra(conv_id, override_key, None, user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Model override cleared for '{agent_name}'. Using default model.",
            }).encode())
        else:
            if conv_id:
                store.set_extra(conv_id, override_key, model_value, user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Model for '{agent_name}' set to '{model_value}' in this conversation.",
                "model": model_value,
            }).encode())
        return [flowfile]

    if action == "theme":
        conv_id = body.get("conversation_id", "")
        css = body.get("css", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        store.set_extra(conv_id, "custom_css", css, user_id=user_id)
        if css:
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conv_id, "theme", {"css": css})
            except Exception:
                pass
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Theme applied",
            "css_length": len(css),
        }).encode())
        return [flowfile]

    # ── /effort ──
    if action == "set_effort":
        conv_id = body.get("conversation_id", "")
        value = body.get("value", "").strip()
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        if value == "reset":
            store.set_extra(conv_id, "effort_override", None, user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True, "message": "Effort override cleared. Using default.",
            }).encode())
        else:
            # Value can be a number or an expression like ${user.effort}
            store.set_extra(conv_id, "effort_override", value, user_id=user_id)
            # Resolve for display
            display = value
            if "${" in value:
                try:
                    from core.expression import resolve_expression
                    display = resolve_expression(value, owner=user_id) or value
                except Exception:
                    pass
            _labels = {"0": "low", "5000": "medium", "10000": "high", "20000": "max"}
            label = _labels.get(display, f"budget={display}")
            flowfile.set_content(json.dumps({
                "ok": True, "message": f"Effort set to {label}.",
                "thinking_budget": display,
            }).encode())
        return [flowfile]

    # ── /fast ──
    if action == "set_fast":
        conv_id = body.get("conversation_id", "")
        enabled = body.get("enabled", True)
        model = body.get("model", "")
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        if not enabled:
            store.set_extra(conv_id, "fast_mode", None, user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True, "message": "Fast mode disabled. Using normal model.",
            }).encode())
        else:
            # Model can be explicit, expression, or default to ${user.fast_model}
            fast_val = model or "${user.fast_model}"
            store.set_extra(conv_id, "fast_mode", fast_val, user_id=user_id)
            display = fast_val
            if "${" in fast_val:
                try:
                    from core.expression import resolve_expression
                    display = resolve_expression(fast_val, owner=user_id) or fast_val
                except Exception:
                    pass
            if "${" in display:
                flowfile.set_content(json.dumps({
                    "ok": True,
                    "message": f"Fast mode enabled but no fast_model configured. "
                               f"Set it with: /add-variable fast_model <model_name>",
                }).encode())
            else:
                flowfile.set_content(json.dumps({
                    "ok": True, "message": f"Fast mode enabled: {display}",
                    "model": display,
                }).encode())
        return [flowfile]

    # ── /plan mode ──
    if action == "set_plan_mode":
        conv_id = body.get("conversation_id", "")
        enabled = body.get("enabled", True)
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        store.set_extra(conv_id, "plan_mode", enabled, user_id=user_id)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": f"Plan mode {'enabled' if enabled else 'disabled'}. "
                       + ("Agent will propose plans before executing." if enabled
                          else "Agent will execute tools directly."),
        }).encode())
        return [flowfile]

    if action == "get_plan_mode":
        conv_id = body.get("conversation_id", "")
        enabled = store.get_extra(conv_id, "plan_mode") if conv_id else False
        flowfile.set_content(json.dumps({
            "plan_mode": bool(enabled),
            "message": f"Plan mode is {'enabled' if enabled else 'disabled'}.",
        }).encode())
        return [flowfile]

    # ── /fork ──
    if action == "fork_conversation":
        conv_id = body.get("conversation_id", "")
        fork_name = body.get("name", "").strip()
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        # Load source conversation
        src_msgs = store.load(conv_id, user_id=user_id)
        if not src_msgs:
            flowfile.set_content(json.dumps({"error": "Source conversation not found"}).encode())
            return [flowfile]
        # Create new conversation
        new_id = store.generate_id()
        store.save(new_id, src_msgs, user_id=user_id)
        # Copy extras (active_resources, nicknames, overrides, etc.)
        src_extras = store.get_extras(conv_id, user_id=user_id) or {}
        for key, val in src_extras.items():
            if key.startswith("agent_context:") or key == "agent_context":
                # Copy agent contexts too
                store.set_extra(new_id, key, val, user_id=user_id)
            elif not key.startswith("cancel_checkpoint:"):
                store.set_extra(new_id, key, val, user_id=user_id)
        # Copy per-agent diverged contexts
        for key in list(src_extras.keys()):
            if key.startswith("agent_context:"):
                ctx_data = store.load_agent_context(conv_id, key.split(":", 1)[1])
                if ctx_data:
                    store.save_agent_context(new_id, key.split(":", 1)[1], ctx_data)
        # Set fork name
        if fork_name:
            store.set_metadata_field(new_id, "title", fork_name)
        else:
            src_meta = store.get_metadata(conv_id)
            src_title = src_meta.get("title", "") if src_meta else ""
            store.set_metadata_field(new_id, "title",
                                     f"Fork of {src_title or conv_id[:8]}")
        flowfile.set_content(json.dumps({
            "ok": True,
            "conversation_id": new_id,
            "message": f"Conversation forked → {new_id[:12]}",
            "source": conv_id,
        }).encode())
        return [flowfile]

    # ── /doctor ──
    if action == "doctor":
        checks = []
        # Check LLM services
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for sid, sdef in greg.get_all_definitions().items():
                if getattr(sdef, "service_type", "") in ("llm", "openai_llm"):
                    svc = greg.get_live_instance(sid)
                    if svc and hasattr(svc, "get_client"):
                        client = svc.get_client()
                        checks.append({
                            "component": f"LLM: {sid}",
                            "status": "ok",
                            "detail": f"provider={getattr(svc, 'provider', '?')}, "
                                      f"model={getattr(svc, 'default_model', '?')}",
                        })
                    else:
                        checks.append({
                            "component": f"LLM: {sid}",
                            "status": "error",
                            "detail": "Service not live or no get_client()",
                        })
        except Exception as e:
            checks.append({"component": "LLM Services", "status": "error",
                           "detail": str(e)})
        # Check FileStore
        try:
            from core.file_store import FileStore
            fs = FileStore.instance()
            checks.append({"component": "FileStore", "status": "ok",
                           "detail": f"path={getattr(fs, '_base_dir', '?')}"})
        except Exception as e:
            checks.append({"component": "FileStore", "status": "error",
                           "detail": str(e)})
        # Check agents
        try:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            agents = rs.list_all("agent", user_id or "anonymous")
            checks.append({"component": "Agents", "status": "ok",
                           "detail": f"{len(agents)} defined: "
                                     f"{', '.join(a['name'] for a in agents[:5])}"})
        except Exception as e:
            checks.append({"component": "Agents", "status": "error",
                           "detail": str(e)})
        # Check ConversationStore
        try:
            convs = store.list_conversations(user_id=user_id)
            checks.append({"component": "Conversations", "status": "ok",
                           "detail": f"{len(convs)} conversations"})
        except Exception as e:
            checks.append({"component": "Conversations", "status": "error",
                           "detail": str(e)})
        # Format output
        lines = ["## System Diagnostics\n"]
        for c in checks:
            icon = "✅" if c["status"] == "ok" else "❌"
            lines.append(f"{icon} **{c['component']}**: {c['detail']}")
        flowfile.set_content(json.dumps({
            "ok": True, "checks": checks,
            "message": "\n".join(lines),
        }).encode())
        return [flowfile]

    # ── /add-dir ──
    if action == "add_dir":
        path = body.get("path", "").strip()
        if not path:
            flowfile.set_content(json.dumps({"error": "Missing path"}).encode())
            return [flowfile]
        # Create filesystem service
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            import os
            greg = GlobalServiceRegistry.get_instance()
            # Generate name from path
            name = os.path.basename(path.rstrip("/\\")) or "workspace"
            name = f"fs_{name}"
            # Check if already exists
            existing = greg.get_definition(name)
            if existing:
                flowfile.set_content(json.dumps({
                    "ok": True,
                    "message": f"Service '{name}' already exists for that path.",
                }).encode())
                return [flowfile]
            from services.filesystem_service import FilesystemService
            greg.register_definition(name, FilesystemService, {
                "root_path": path,
                "read_only": False,
            })
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Filesystem service '{name}' created for {path}",
                "service_name": name,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({
                "error": f"Failed to create filesystem service: {e}",
            }).encode())
        return [flowfile]

    return None
