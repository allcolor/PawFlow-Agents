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
            from core.expression import resolve_value
            display = resolve_value(value, owner=user_id) or value
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
            fast_val = model or "${fast_model}"
            store.set_extra(conv_id, "fast_mode", fast_val, user_id=user_id)
            from core.expression import resolve_value
            display = resolve_value(fast_val, owner=user_id) or fast_val
            if not display or display == fast_val and fast_val.startswith("$"):
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
            if not key.startswith("cancel_checkpoint:"):
                store.set_extra(new_id, key, val, user_id=user_id)
        # Copy agent contexts
        agent_ctxs = store.list_agent_contexts(conv_id)
        for agent_name, status in agent_ctxs.items():
            if agent_name == "*":
                continue  # skip shared status marker
            if status == "diverged":
                ctx_data = store.load_agent_context(conv_id, agent_name)
                if ctx_data:
                    store.save_agent_context(new_id, agent_name, ctx_data)
        # Set fork name via extra
        if fork_name:
            store.set_extra(new_id, "title", fork_name, user_id=user_id)
        else:
            src_title = (store.get_extra(conv_id, "title", user_id=user_id)
                         or conv_id[:8])
            store.set_extra(new_id, "title",
                            f"Fork of {src_title}", user_id=user_id)
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
            from services.filesystem_service import RelayService
            greg.register_definition(name, RelayService, {
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

    # ── /stats ──
    if action == "stats":
        conv_id = body.get("conversation_id", "")
        # Aggregate token usage from conversation messages
        lines = ["## Session Statistics\n"]
        try:
            # Per-agent usage from conversation source metadata
            all_msgs = store.load(conv_id, user_id=user_id) or [] if conv_id else []
            agent_stats = {}  # agent -> {tokens_in, tokens_out, calls, models}
            for m in all_msgs:
                src = m.get("source") if isinstance(m, dict) else getattr(m, "source", None)
                if not src or not isinstance(src, dict):
                    continue
                name = src.get("name", "")
                if not name or src.get("type") != "agent":
                    continue
                s = agent_stats.setdefault(name, {
                    "tokens_in": 0, "tokens_out": 0, "calls": 0, "models": set()})
                s["tokens_in"] += src.get("tokens_in", 0) or 0
                s["tokens_out"] += src.get("tokens_out", 0) or 0
                s["calls"] += 1
                model = src.get("model", "")
                if model:
                    s["models"].add(model)

            if agent_stats:
                total_in = sum(s["tokens_in"] for s in agent_stats.values())
                total_out = sum(s["tokens_out"] for s in agent_stats.values())
                lines.append(f"**Total**: {total_in:,} in / {total_out:,} out\n")
                for name, s in sorted(agent_stats.items(), key=lambda x: -x[1]["tokens_out"]):
                    models = ", ".join(sorted(s["models"])) if s["models"] else "?"
                    lines.append(
                        f"  **{name}** ({models}): "
                        f"{s['tokens_in']:,} in / {s['tokens_out']:,} out "
                        f"({s['calls']} messages)")
            else:
                lines.append("No agent activity recorded in this conversation.")

            # Conversation count
            all_convs = store.list_conversations(user_id=user_id)
            lines.append(f"\n**Conversations**: {len(all_convs)}")
            lines.append(f"**Messages in current**: {len(all_msgs)}")

        except Exception as e:
            lines.append(f"Error collecting stats: {e}")

        flowfile.set_content(json.dumps({
            "ok": True, "message": "\n".join(lines),
        }).encode())
        return [flowfile]

    # ── /pr-comments ──
    if action == "pr_comments":
        pr = body.get("pr", "").strip()
        conv_id = body.get("conversation_id", "")
        # Build command for the agent to run via relay
        if pr:
            cmd = f"gh pr view {pr} --comments --json comments"
        else:
            cmd = "gh pr view --comments --json comments"
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": f"Fetching PR comments... Run this via relay:\n```\n{cmd}\n```\n\n"
                       f"Or ask your agent: \"show me the PR comments\"",
            "relay_command": cmd,
        }).encode())
        return [flowfile]

    # ── /security-review ──
    if action == "security_review":
        conv_id = body.get("conversation_id", "")
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": "Starting security review...",
            "_inject_message": (
                "[System: SECURITY REVIEW MODE]\n"
                "Run `git diff` to see pending changes, then analyze them for:\n"
                "1. Injection vulnerabilities (SQL, XSS, command injection)\n"
                "2. Authentication/authorization issues\n"
                "3. Sensitive data exposure (secrets, tokens, PII)\n"
                "4. Input validation gaps\n"
                "5. Dependency vulnerabilities\n\n"
                "Report findings with severity (critical/high/medium/low) and fix suggestions."
            ),
        }).encode())
        return [flowfile]

    # ── /insights ──
    if action == "insights":
        conv_id = body.get("conversation_id", "")
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": "Generating session insights...",
            "_inject_message": (
                "[System: SESSION INSIGHTS]\n"
                "Analyze the conversation history and provide insights:\n"
                "1. What were the main topics/tasks worked on?\n"
                "2. Were there recurring friction points or repeated errors?\n"
                "3. Which tools were used most? Any underutilized tools?\n"
                "4. What patterns emerged in the workflow?\n"
                "5. Suggestions for improving productivity in future sessions.\n\n"
                "Be concise — 5-10 bullet points max."
            ),
        }).encode())
        return [flowfile]

    # ── /feedback ──
    if action == "feedback":
        report = body.get("report", "").strip()
        if not report:
            flowfile.set_content(json.dumps({
                "message": "To report an issue:\n"
                           "  /feedback <description of the issue>\n\n"
                           "Or open an issue directly at the project's issue tracker.",
            }).encode())
        else:
            # Store feedback as a notification
            logger.info(f"[feedback] from {user_id}: {report}")
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Thank you for your feedback! Logged for review.\n\n> {report[:200]}",
            }).encode())
        return [flowfile]

    return None
