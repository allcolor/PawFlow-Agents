"""AgentLoopTask mixin — AgentActions methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional


from core import FlowFile
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry, load_agent_tools

logger = logging.getLogger(__name__)



class AgentActionsMixin:
    """Methods extracted from AgentLoopTask."""


    def _handle_action(self, flowfile: FlowFile) -> Optional[List[FlowFile]]:
        """Handle action-based requests (list/load/delete conversations).

        Returns None if the request is not an action (i.e. a normal message).
        Also handles Telegram /conv commands for cross-channel conversation management.
        """
        raw_body = flowfile.get_content().decode("utf-8", errors="replace")

        # Handle Telegram /conv commands (text-based, not JSON)
        tg_user_id = flowfile.get_attribute("telegram.user_id") or ""
        if tg_user_id and raw_body.strip().startswith("/conv"):
            result = self._handle_telegram_conv_command(
                raw_body.strip(), tg_user_id, flowfile,
            )
            if result is not None:
                return result

        if not raw_body.strip().startswith("{"):
            return None
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return None
        if not isinstance(body, dict) or "action" not in body:
            return None

        action = body["action"]
        user_id = flowfile.get_attribute("http.auth.principal") or ""

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        if action == "list_conversations":
            convs = store.list_conversations(user_id=user_id)
            result = json.dumps({"conversations": convs}, ensure_ascii=False)
            flowfile.set_content(result.encode("utf-8"))
            return [flowfile]

        if action == "load_history":
            conv_id = body.get("conversation_id", "")
            limit = int(body.get("limit", 50))
            offset = int(body.get("offset", 0))
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]

            page = store.load_page(conv_id, limit=limit, offset=offset, user_id=user_id)
            if page is None:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]

            history = self._classify_messages_for_display(page["messages"])
            nicknames = store.get_extra(conv_id, "agent_nicknames", user_id=user_id) or {}
            active_res = store.get_extra(conv_id, "active_resources", user_id=user_id) or {}
            active_res = self._ensure_active_agent(conv_id, active_res, user_id or "anonymous")
            custom_css = store.get_extra(conv_id, "custom_css", user_id=user_id) or ""

            result = json.dumps({
                "conversation_id": conv_id,
                "messages": history,
                "message_count": page["total_count"],
                "has_more": page["has_more"],
                "offset": page["offset"],
                "nicknames": nicknames,
                "active_agent": active_res.get("agent", ""),
                "custom_css": custom_css,
            }, ensure_ascii=False)
            flowfile.set_content(result.encode("utf-8"))
            return [flowfile]

        if action == "delete_conversation":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Collect file IDs from conversation before deleting
            history = store.load(conv_id, user_id=user_id)
            if history:
                self._cleanup_conversation_files(history)
            # Cascade cleanup: flows, dynamic tools, secrets
            self._cleanup_conversation_resources(conv_id)
            deleted = store.delete(conv_id, user_id=user_id)
            logger.info(f"[action] delete_conversation {conv_id}: deleted={deleted}, "
                        f"user_id={user_id}")
            result = json.dumps({"deleted": deleted, "conversation_id": conv_id})
            flowfile.set_content(result.encode("utf-8"))
            return [flowfile]

        if action == "set_agent_nickname":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("agent_name", "").strip()
            nickname = body.get("nickname", "").strip()
            if agent_name and conv_id:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not conv_id or not agent_name or not nickname:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id, agent_name, or nickname"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            nicknames = store.get_extra(conv_id, "agent_nicknames") or {}
            nicknames[agent_name] = nickname
            store.set_extra(conv_id, "agent_nicknames", nicknames)
            flowfile.set_content(json.dumps({
                "ok": True, "agent_name": agent_name, "nickname": nickname,
            }).encode())
            return [flowfile]

        if action == "cancel":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("agent_name", "")
            if agent_name:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            self.cancel_agent(conv_id, agent_name=agent_name)
            flowfile.set_content(json.dumps({
                "cancelled": True, "conversation_id": conv_id,
                "agent_name": agent_name or "all",
            }).encode())
            return [flowfile]

        if action == "cost":
            # Read persistent stats from TokenTracker (survives restarts)
            from core.token_tracker import TokenTracker
            from gui.services.global_service_registry import GlobalServiceRegistry
            tracker = TokenTracker.instance()
            usage = tracker.get_usage(user_id)
            agents_data = usage.get("agents", {})
            req_agent = body.get("agent", "ALL")

            # Build service cost info from registry
            greg = GlobalServiceRegistry.get_instance()
            svc_costs = {}
            for svc_id, svc_def in greg.get_all_definitions().items():
                if getattr(svc_def, "service_type", "") == "llmConnection":
                    svc_costs[svc_id] = {
                        "cost_per_1m_input": float(svc_def.config.get("cost_per_1m_input", 0) or 0),
                        "cost_per_1m_output": float(svc_def.config.get("cost_per_1m_output", 0) or 0),
                    }

            stats = []
            for key, agent_stats in agents_data.items():
                agent_name = agent_stats.get("agent", "")
                svc_id = agent_stats.get("llm_service", "default")
                # Filter by agent
                if req_agent.upper() != "ALL" and agent_name.lower() != req_agent.lower():
                    continue
                tok_in = agent_stats.get("in", 0)
                tok_out = agent_stats.get("out", 0)
                calls = agent_stats.get("calls", 0)
                costs = svc_costs.get(svc_id, {})
                cost_in_1m = costs.get("cost_per_1m_input", 0)
                cost_out_1m = costs.get("cost_per_1m_output", 0)
                cost = 0.0
                if cost_in_1m or cost_out_1m:
                    cost = round(tok_in / 1_000_000 * cost_in_1m +
                                 tok_out / 1_000_000 * cost_out_1m, 6)
                stats.append({
                    "agent": agent_name, "llm_service": svc_id,
                    "tokens_in": tok_in, "tokens_out": tok_out,
                    "calls": calls, "cost": cost,
                    "cost_per_1m_input": cost_in_1m,
                    "cost_per_1m_output": cost_out_1m,
                })

            flowfile.set_content(json.dumps({
                "services": stats,
                "total_in": usage.get("total_in", 0),
                "total_out": usage.get("total_out", 0),
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "list_active":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            now = time.time()
            active = []
            with self._interactions_lock:
                for key, info in list(self._active_interactions.items()):
                    if info.get("conversation_id") != conv_id:
                        continue
                    # Auto-cleanup stale entries (>10 min)
                    if now - info.get("started_at", now) > 600:
                        self._active_interactions.pop(key, None)
                        continue
                    active.append({
                        "agent_name": info.get("agent_name", ""),
                        "message_preview": info.get("message_preview", ""),
                        "duration_s": round(now - info.get("started_at", now), 1),
                        "iteration": info.get("iteration", 0),
                        "last_tool": info.get("last_tool", ""),
                        "status": info.get("status", "thinking"),
                    })
            flowfile.set_content(json.dumps({"active": active}).encode())
            return [flowfile]

        if action == "interrupt":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("agent_name", "")
            if agent_name:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            self.interrupt_agent(conv_id, agent_name)
            flowfile.set_content(json.dumps({
                "interrupted": True, "conversation_id": conv_id,
                "agent_name": agent_name or "",
            }).encode())
            return [flowfile]

        if action == "btw":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("agent_name", "")
            if agent_name and agent_name.upper() != "ALL":
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            question = body.get("message", "")
            if not conv_id or not question:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or message"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            user_id = flowfile.get_attribute("http.auth.principal") or ""
            # Handle ALL — spawn btw for each agent + default
            if agent_name.upper() == "ALL":
                from core.resource_store import ResourceStore
                rs = ResourceStore.instance()
                all_agents = rs.list_all("agent", user_id)
                targets = [a["name"] for a in all_agents]
                for t in targets:
                    thread = threading.Thread(
                        target=self._btw_query,
                        args=(conv_id, t, question, user_id),
                        daemon=True,
                        name=f"btw-{t}-{conv_id[:8]}",
                    )
                    thread.start()
            else:
                thread = threading.Thread(
                    target=self._btw_query,
                    args=(conv_id, agent_name, question, user_id),
                    daemon=True,
                    name=f"btw-{agent_name or 'agent'}-{conv_id[:8]}",
                )
                thread.start()
            flowfile.set_content(json.dumps({
                "ok": True, "conversation_id": conv_id,
            }).encode())
            return [flowfile]

        if action == "restart_from":
            conv_id = body.get("conversation_id", "")
            _rf_agent = body.get("agent_name", "")
            keep_last = int(body.get("keep_last", 5))
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _rf_msgs = store.load(conv_id, user_id=user_id)
            if not _rf_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]

            def _do_restart():
                deserialized = self._deserialize_messages(_rf_msgs)
                system_msgs = [m for m in deserialized if m.role == "system"]
                non_system = [m for m in deserialized if m.role != "system"]
                if keep_last == 0:
                    new_context = system_msgs
                else:
                    kept = non_system[-keep_last:] if len(non_system) > keep_last else non_system
                    new_context = system_msgs + kept
                serialized_ctx = self._serialize_messages(new_context)
                store.save_agent_context(conv_id, _rf_agent, serialized_ctx)
                return {"kept_messages": len(new_context) - len(system_msgs),
                        "agent": _rf_agent or "shared"}

            return self._run_bg_context_op(conv_id, "restart_from", _do_restart, flowfile)

        if action == "delete_message":
            conv_id = body.get("conversation_id", "")
            msg_index = body.get("index")
            if not conv_id or msg_index is None:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            deleted = store.delete_message(conv_id, int(msg_index), user_id=user_id)
            flowfile.set_content(json.dumps({
                "deleted": deleted, "conversation_id": conv_id,
                "message_count": store.message_count(conv_id),
            }).encode())
            return [flowfile]

        if action == "resume_conversation":
            conv_id = body.get("conversation_id", "")
            _rs_agent = body.get("agent_name", "")
            max_summary_tokens = int(body.get("max_tokens", 500))
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _rs_msgs = store.load(conv_id, user_id=user_id)
            if not _rs_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            # Resolve LLM client
            _summ_client, _ = self._get_summarizer_client(user_id)
            _rs_client = _summ_client
            if not _rs_client:
                _rs_client, _ = self._resolve_client(
                    self.config.get("llm_service", "default"),
                    user_id, resolve_expressions=False,
                )
            if not _rs_client:
                flowfile.set_content(json.dumps({"error": "No LLM service for summarization"}).encode())
                return [flowfile]

            def _do_resume():
                deserialized = self._deserialize_messages(_rs_msgs)
                content_msgs = [m for m in deserialized if m.role != "system"]
                context_max = int(self.config.get("max_context_size", 64000))
                # Resolve agent's max_tokens
                if _rs_agent:
                    try:
                        from core.resource_store import ResourceStore as _RS_r
                        _ad = _RS_r.instance().get_any("agent", _rs_agent, user_id)
                        if _ad and _ad.get("llm_service"):
                            _sid = _ad["llm_service"]
                            if "${" in _sid:
                                from core.expression import resolve_expression as _re_r
                                _sid = _re_r(_sid, owner=user_id)
                            if _sid and "${" not in _sid:
                                _, _sv = self._resolve_llm_service(_sid, user_id)
                                if _sv:
                                    _v = int((getattr(_sv, 'config', {}) or {}).get("max_context_size", 0))
                                    if _v:
                                        context_max = _v
                    except Exception:
                        pass
                summary = self._summarize_messages(
                    content_msgs, _rs_client, context_max,
                    target_tokens=max_summary_tokens,
                    conversation_id=conv_id,
                )
                sys_prompt = self.config.get("system_prompt", "You are a helpful assistant.")
                from datetime import datetime
                sys_prompt += f"\n\nCurrent date and time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                new_context = [
                    LLMMessage(role="system", content=sys_prompt),
                    LLMMessage(role="user",
                               content=f"[Conversation summary — earlier messages compacted]\n\n{summary}"),
                    LLMMessage(role="assistant",
                               content="Understood. I have the context from our earlier conversation. Continuing from where we left off."),
                ]
                store.save_agent_context(conv_id, _rs_agent, self._serialize_messages(new_context))
                return {"summary_length": len(summary),
                        "messages_summarized": len(_rs_msgs),
                        "agent": _rs_agent or "shared"}

            return self._run_bg_context_op(conv_id, "summary", _do_resume, flowfile)

        if action == "ping":
            # Keep-alive: session renewal happens in validateSessionAuth upstream
            flowfile.set_content(json.dumps({"status": "ok"}).encode())
            return [flowfile]

        if action == "broadcast_agents":
            # Send the same message to ALL defined agents in parallel
            conv_id = body.get("conversation_id", "")
            message = body.get("message", "")
            if not conv_id or not message:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id and message are required",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Launch broadcast in background thread
            thread = threading.Thread(
                target=self._broadcast_agents,
                args=(conv_id, message, user_id),
                daemon=True,
                name=f"broadcast-{conv_id[:8]}",
            )
            thread.start()
            flowfile.set_content(json.dumps({
                "status": "broadcasting",
                "conversation_id": conv_id,
            }).encode())
            return [flowfile]

        if action == "poll":
            # Efficient delta check: client sends last known message_count,
            # server returns new messages only if count increased.
            conv_id = body.get("conversation_id", "")
            last_count = int(body.get("last_count", 0))
            if not conv_id:
                flowfile.set_content(json.dumps({"new_messages": []}).encode())
                return [flowfile]
            current_count = store.message_count(conv_id)
            if current_count <= last_count:
                flowfile.set_content(json.dumps({
                    "new_messages": [], "message_count": current_count,
                }).encode())
                return [flowfile]
            # Load full history and return only the new portion
            all_messages = store.load(conv_id, user_id=user_id)
            if all_messages is None:
                flowfile.set_content(json.dumps({
                    "new_messages": [], "message_count": 0,
                }).encode())
                return [flowfile]
            new_raw = all_messages[last_count:]
            new_classified = self._classify_messages_for_display(new_raw)
            flowfile.set_content(json.dumps({
                "new_messages": new_classified,
                "message_count": len(all_messages),
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "add_secret":
            key = body.get("key", "").strip()
            value = body.get("value", "")
            if not key or not value:
                flowfile.set_content(json.dumps({"error": "key and value are required"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            uid = user_id or "anonymous"
            from pathlib import Path
            from core.secrets import get_secrets_manager
            sm = get_secrets_manager()
            encrypted = sm.encrypt(value)
            secrets_path = Path("config/users") / uid / "secrets.json"
            secrets_path.parent.mkdir(parents=True, exist_ok=True)
            secrets = {}
            if secrets_path.exists():
                try:
                    secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            secrets[key] = encrypted
            secrets_path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8")
            flowfile.set_content(json.dumps({
                "result": f"Secret '{key}' stored. Use ${{secrets.user.{key}}} in flows.",
                "key": key,
            }).encode())
            return [flowfile]

        if action == "add_variable":
            key = body.get("key", "").strip()
            value = body.get("value", "")
            if not key or not value:
                flowfile.set_content(json.dumps({"error": "key and value are required"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            uid = user_id or "anonymous"
            from pathlib import Path
            params_path = Path("config/users") / uid / "parameters.json"
            params_path.parent.mkdir(parents=True, exist_ok=True)
            params = {}
            if params_path.exists():
                try:
                    params = json.loads(params_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            params[key] = value
            params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
            flowfile.set_content(json.dumps({
                "result": f"Parameter '{key}' stored. Use ${{user.{key}}} in flows.",
                "key": key,
            }).encode())
            return [flowfile]

        if action == "list_secrets":
            uid = user_id or "anonymous"
            from pathlib import Path
            secrets_path = Path("config/users") / uid / "secrets.json"
            if not secrets_path.exists():
                flowfile.set_content(json.dumps({"result": "No secrets stored."}).encode())
                return [flowfile]
            try:
                secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            except Exception:
                flowfile.set_content(json.dumps({"result": "Error reading secrets."}).encode())
                return [flowfile]
            if not secrets:
                flowfile.set_content(json.dumps({"result": "No secrets stored."}).encode())
                return [flowfile]
            lines = [f"Secrets ({len(secrets)}):"]
            for k in sorted(secrets.keys()):
                lines.append(f"- {k} → ${{secrets.user.{k}}}")
            flowfile.set_content(json.dumps({"result": "\n".join(lines)}).encode())
            return [flowfile]

        if action == "list_variables":
            uid = user_id or "anonymous"
            from pathlib import Path
            params_path = Path("config/users") / uid / "parameters.json"
            if not params_path.exists():
                flowfile.set_content(json.dumps({"result": "No parameters stored."}).encode())
                return [flowfile]
            try:
                params = json.loads(params_path.read_text(encoding="utf-8"))
            except Exception:
                flowfile.set_content(json.dumps({"result": "Error reading parameters."}).encode())
                return [flowfile]
            if not params:
                flowfile.set_content(json.dumps({"result": "No parameters stored."}).encode())
                return [flowfile]
            lines = [f"Parameters ({len(params)}):"]
            for k, v in sorted(params.items()):
                lines.append(f"- {k} = {v} → ${{user.{k}}}")
            flowfile.set_content(json.dumps({"result": "\n".join(lines)}).encode())
            return [flowfile]

        if action == "file_result":
            # Browser responding to a local_files tool request
            request_id = body.get("request_id", "")
            result = body.get("result", {})
            if not request_id:
                flowfile.set_content(json.dumps({"error": "Missing request_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.tool_registry import LocalFilesHandler
            LocalFilesHandler.resolve_request(request_id, result)
            flowfile.set_content(json.dumps({"status": "ok"}).encode())
            return [flowfile]

        if action == "exec_result":
            # User responding to a remote_exec approval request
            request_id = body.get("request_id", "")
            result = body.get("result", {})
            if not request_id:
                flowfile.set_content(json.dumps({"error": "Missing request_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.tool_registry import RemoteExecutorHandler
            RemoteExecutorHandler.resolve_request(request_id, result)
            flowfile.set_content(json.dumps({"status": "ok"}).encode())
            return [flowfile]

        if action == "tool_approval_result":
            # Plan A: User responding to a universal tool approval dialog
            request_id = body.get("request_id", "")
            result = body.get("result", {})
            if not request_id:
                flowfile.set_content(json.dumps({"error": "Missing request_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.tool_approval import ToolApprovalGate
            ToolApprovalGate.resolve_request(request_id, result)
            flowfile.set_content(json.dumps({"status": "ok"}).encode())
            return [flowfile]

        if action == "list_schedules":
            conv_id = body.get("conversation_id", "")
            from core.poll_scheduler import PollScheduler
            all_scheds = PollScheduler.instance().list_all()
            # Filter to current conversation
            scheds = [s for s in all_scheds if s["conversation_id"] == conv_id]
            flowfile.set_content(json.dumps({"schedules": scheds}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "add_schedule":
            conv_id = body.get("conversation_id", "")
            at_str = body.get("at", "")
            reason = body.get("reason", "manual schedule")
            if not conv_id or not at_str:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or at"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from datetime import datetime, timezone as tz
            from core.poll_scheduler import PollScheduler
            try:
                dt = datetime.strptime(at_str, "%Y%m%d%H%M%S")
                dt = dt.replace(tzinfo=tz.utc)
                recheck_at = dt.timestamp()
            except ValueError:
                flowfile.set_content(json.dumps({"error": f"Invalid date: {at_str}"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            PollScheduler.instance().schedule(conv_id, recheck_at, user_id, reason)
            store.set_status(conv_id, "active")
            flowfile.set_content(json.dumps({"scheduled": True, "at": recheck_at}).encode())
            return [flowfile]

        if action == "delete_schedule":
            conv_id = body.get("conversation_id", "")
            from core.poll_scheduler import PollScheduler
            cancelled = PollScheduler.instance().cancel(conv_id)
            flowfile.set_content(json.dumps({"cancelled": cancelled}).encode())
            return [flowfile]

        if action == "list_conv_files":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"files": []}).encode())
                return [flowfile]
            messages_data = store.load(conv_id, user_id=user_id) or []
            # Also include files from sub-conversations (task contexts)
            all_convs = store.list_conversations(user_id=user_id) if user_id else []
            # list_conversations filters ::task::, so search extras directly
            try:
                extras = store.get_extras(conv_id, user_id=user_id) or {}
                for k in extras:
                    if k.startswith("task_log:"):
                        # There might be a sub-conv for this task
                        tid = k[9:]
                        sub_cid = f"{conv_id}::task::{tid}"
                        sub_msgs = store.load(sub_cid, user_id=user_id)
                        if sub_msgs:
                            messages_data.extend(sub_msgs)
            except Exception:
                pass
            if not messages_data:
                flowfile.set_content(json.dumps({"files": []}).encode())
                return [flowfile]
            import re as _re
            from core.file_store import FileStore
            fstore = FileStore.instance()
            pattern = _re.compile(r'/files/([a-f0-9]{12})/([^\s"<>]+)')
            seen = set()
            files = []
            for msg in messages_data:
                content = msg.get("content", "")
                if not isinstance(content, str):
                    continue
                for match in pattern.finditer(content):
                    fid = match.group(1)
                    fname = match.group(2)
                    if fid in seen:
                        continue
                    seen.add(fid)
                    available = fstore.exists(fid)
                    files.append({
                        "file_id": fid, "filename": fname,
                        "available": available,
                    })
            flowfile.set_content(json.dumps({"files": files}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "delete_file":
            file_id = body.get("file_id", "")
            conv_id = body.get("conversation_id", "")
            if not file_id:
                flowfile.set_content(json.dumps({"error": "Missing file_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Verify the file belongs to a conversation owned by this user
            if conv_id and user_id:
                conv_data = store.load(conv_id, user_id=user_id)
                if conv_data is None:
                    flowfile.set_content(json.dumps({"error": "Access denied"}).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                # Verify file_id is referenced in this conversation
                import re as _re_del
                found = any(
                    file_id in (m.get("content", "") if isinstance(m.get("content"), str) else "")
                    for m in conv_data
                )
                if not found:
                    flowfile.set_content(json.dumps({"error": "File not in this conversation"}).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
            from core.file_store import FileStore
            fstore = FileStore.instance()
            if not fstore.exists(file_id):
                flowfile.set_content(json.dumps({"error": "File not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            fstore.delete(file_id)
            flowfile.set_content(json.dumps({"ok": True, "file_id": file_id}).encode())
            return [flowfile]

        if action == "list_conv_flows":
            # Show all flows belonging to this user (not conversation-scoped)
            try:
                from gui.services.deployment_registry import DeploymentRegistry
                dep_reg = DeploymentRegistry.get_instance()
                dep_reg.sync_with_executors()
                uid = user_id or None
                instances = dep_reg.get_by_owner(uid) if uid else []
                flows_list = []
                for inst in instances:
                    tasks_count = 0
                    try:
                        from pathlib import Path as _Path
                        raw = json.loads(_Path(inst.flow_path).read_text(encoding="utf-8"))
                        tasks_count = len(raw.get("tasks", {}))
                    except Exception:
                        pass
                    flows_list.append({
                        "id": inst.instance_id,
                        "name": inst.flow_name,
                        "status": inst.status,
                        "template": inst.flow_id if inst.flow_id != inst.instance_id else "",
                        "tasks_count": tasks_count,
                    })
            except Exception:
                flows_list = []
            flowfile.set_content(
                json.dumps({"flows": flows_list}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "manage_conv_flow":
            flow_id = body.get("flow_id", "")
            flow_action = body.get("flow_action", "")
            if not flow_id or not flow_action:
                flowfile.set_content(json.dumps(
                    {"error": "flow_id and flow_action required"}).encode())
                return [flowfile]

            from gui.services.deployment_registry import DeploymentRegistry
            dep_reg = DeploymentRegistry.get_instance()
            inst = dep_reg.get(flow_id)
            if not inst:
                flowfile.set_content(json.dumps(
                    {"error": f"Flow '{flow_id}' not found"}).encode())
                return [flowfile]
            # Ownership check
            if user_id and inst.owner != user_id:
                flowfile.set_content(json.dumps(
                    {"error": "Permission denied"}).encode())
                return [flowfile]

            if flow_action == "start":
                try:
                    from gui.services.executor_registry import ExecutorRegistry
                    from engine.parser import FlowParser
                    from engine.continuous_executor import ContinuousFlowExecutor
                    from tasks import register_all_tasks
                    register_all_tasks()
                    raw = json.loads(
                        open(inst.flow_path, encoding="utf-8").read())
                    clean = {k: v for k, v in raw.items()
                             if not k.startswith("_")}
                    if inst.parameters:
                        clean.setdefault("parameters", {}).update(inst.parameters)
                    flow = FlowParser.parse(clean)
                    reg = ExecutorRegistry.get_instance()
                    existing = reg.get(flow_id)
                    if existing:
                        try:
                            existing.stop()
                        except Exception:
                            pass
                        reg.unregister(flow_id)
                    executor = ContinuousFlowExecutor(
                        flow, max_workers=inst.max_workers,
                        max_retries=inst.max_retries,
                        parameters=inst.parameters or None)
                    executor.start()
                    reg.register(flow_id, executor)
                    flowfile.set_content(json.dumps(
                        {"message": f"Flow '{flow_id}' started"}).encode())
                except Exception as e:
                    dep_reg.update_status(flow_id, "error", str(e))
                    flowfile.set_content(json.dumps(
                        {"error": f"Start failed: {e}"}).encode())

            elif flow_action == "stop":
                try:
                    from gui.services.executor_registry import ExecutorRegistry
                    reg = ExecutorRegistry.get_instance()
                    ex = reg.get(flow_id)
                    if ex:
                        ex.stop()
                        reg.unregister(flow_id)
                    flowfile.set_content(json.dumps(
                        {"message": f"Flow '{flow_id}' stopped"}).encode())
                except Exception as e:
                    flowfile.set_content(json.dumps(
                        {"error": f"Stop failed: {e}"}).encode())

            elif flow_action == "delete":
                try:
                    from gui.services.executor_registry import ExecutorRegistry
                    reg = ExecutorRegistry.get_instance()
                    ex = reg.get(flow_id)
                    if ex:
                        ex.stop()
                        reg.unregister(flow_id)
                    dep_reg.undeploy(flow_id)
                    flowfile.set_content(json.dumps(
                        {"message": f"Flow '{flow_id}' deleted"}).encode())
                except Exception as e:
                    flowfile.set_content(json.dumps(
                        {"error": f"Delete failed: {e}"}).encode())
            else:
                flowfile.set_content(json.dumps(
                    {"error": f"Unknown action: {flow_action}"}).encode())
            return [flowfile]

        # ── Per-agent context routing helpers ───────────────────────
        # All context actions below support agent_name param.
        # "ALL" means apply to all agents with diverged contexts.
        def _ctx_load(conv_id, agent_name=""):
            """Load context for an agent (falls back to shared → messages)."""
            if agent_name and agent_name != "ALL":
                return store.load_agent_context(conv_id, agent_name)
            return store.load_context(conv_id, user_id=user_id)

        def _ctx_save(conv_id, data, agent_name=""):
            """Save context for an agent (or shared if no agent)."""
            if agent_name and agent_name != "ALL":
                store.save_agent_context(conv_id, agent_name, data)
            else:
                store.save_context(conv_id, data)

        def _resolve_agent_max_tokens(agent_name):
            """Get max_tokens from an agent's LLM service config."""
            try:
                from core.resource_store import ResourceStore
                adef = ResourceStore.instance().get_any("agent", agent_name, user_id)
                if adef and adef.get("llm_service"):
                    svc_id = adef["llm_service"]
                    if "${" in svc_id:
                        from core.expression import resolve_expression
                        svc_id = resolve_expression(svc_id, owner=user_id)
                    if svc_id and "${" not in svc_id:
                        _, svc = self._resolve_llm_service(svc_id, user_id)
                        if svc:
                            v = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
                            if v:
                                return v
            except Exception:
                pass
            return 0

        def _ctx_max_tokens(agent_name=""):
            """Get max_context_size for an agent or shared context.

            For a specific agent: use that agent's LLM service max_tokens.
            For shared ("" or "ALL"): use the LARGEST max_tokens among all
            agents (the shared context must fit the biggest consumer).
            """
            flow_default = int(self.config.get("max_context_size", 64000))
            if agent_name and agent_name not in ("", "ALL"):
                return _resolve_agent_max_tokens(agent_name) or flow_default
            # Shared: max of all agent LLM services
            try:
                from core.resource_store import ResourceStore
                all_agents = ResourceStore.instance().list_all("agent", user_id)
                max_val = 0
                for a in all_agents:
                    v = _resolve_agent_max_tokens(a["name"])
                    if v > max_val:
                        max_val = v
                # Also check the default LLM service
                default_svc = self.config.get("llm_service", "default")
                if default_svc and "${" not in default_svc:
                    _, svc = self._resolve_llm_service(default_svc, user_id)
                    if svc:
                        v = int((getattr(svc, 'config', {}) or {}).get("max_context_size", 0))
                        if v > max_val:
                            max_val = v
                return max_val or flow_default
            except Exception:
                return flow_default

        if action == "compact":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Load source data
            context_data = _ctx_load(conv_id, _ctx_agent)
            source_data = context_data if context_data is not None else store.load(conv_id, user_id=user_id)
            if not source_data or len(source_data) < 4:
                flowfile.set_content(json.dumps({"error": "Not enough messages to compact"}).encode())
                return [flowfile]
            # Resolve client
            _summ_client, _ = self._get_summarizer_client(user_id)
            if _summ_client:
                _compact_client = _summ_client
            else:
                svc_id = self.config.get("llm_service", "")
                if not svc_id or "${" in svc_id:
                    svc_id = "default"
                _compact_client, _ = self._resolve_client(
                    svc_id, user_id, resolve_expressions=False,
                )
            if not _compact_client:
                flowfile.set_content(json.dumps({"error": "LLM service not found"}).encode())
                return [flowfile]
            _compact_max = _ctx_max_tokens(_ctx_agent)
            _compact_source = source_data
            _compact_conv = conv_id
            _compact_agent_name = _ctx_agent
            _compact_keep = int(self.config.get("context_keep_recent", 6))

            def _do_compact():
                msgs = self._deserialize_messages(_compact_source)
                before = len(msgs)
                estimated = self._estimate_tokens(msgs)
                compacted = self._compact_if_needed(
                    msgs, _compact_client, _compact_max, 0.5,
                    _compact_keep, conversation_id=_compact_conv,
                    agent_name=_compact_agent_name,
                )
                after_tokens = self._estimate_tokens(compacted)
                return {"before": before, "after": len(compacted),
                        "tokens_before": estimated, "tokens_after": after_tokens,
                        "agent": _compact_agent_name or "shared"}

            return self._run_bg_context_op(conv_id, "compact", _do_compact, flowfile)

        if action == "rebuild":
            conv_id = body.get("conversation_id", "")
            _rb_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _rb_msgs = store.load(conv_id, user_id=user_id)
            if not _rb_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            # Resolve client for potential compaction
            _summ_client, _ = self._get_summarizer_client(user_id)
            _rb_client = _summ_client
            if not _rb_client:
                _rb_client, _ = self._resolve_client(
                    self.config.get("llm_service", "default"),
                    user_id, resolve_expressions=False,
                )
            _rb_max = _ctx_max_tokens(_rb_agent)

            def _do_rebuild():
                deserialized = self._deserialize_messages(_rb_msgs)
                estimated = self._estimate_tokens(deserialized)
                limit = int(_rb_max * 0.8)
                if estimated <= limit:
                    _ctx_save(conv_id, _rb_msgs, _rb_agent)
                    return {"action": "full_restore", "before": len(_rb_msgs),
                            "after": len(_rb_msgs), "tokens_after": estimated}
                if not _rb_client:
                    raise ValueError("No LLM service for compaction")
                compacted = self._compact_if_needed(
                    deserialized, _rb_client, _rb_max, 0.8,
                    int(self.config.get("context_keep_recent", 6)),
                    conversation_id=conv_id, agent_name=_rb_agent,
                )
                return {"action": "compacted", "before": len(_rb_msgs),
                        "after": len(compacted),
                        "tokens_after": self._estimate_tokens(compacted)}

            return self._run_bg_context_op(conv_id, "rebuild", _do_rebuild, flowfile)

        if action in ("rebuild_clean", "rebuild_full"):
            conv_id = body.get("conversation_id", "")
            _rf_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _rf_msgs = store.load(conv_id, user_id=user_id)
            if not _rf_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]

            def _do_rebuild_full():
                deserialized = self._deserialize_messages(_rf_msgs)
                estimated = self._estimate_tokens(deserialized)
                if _rf_agent == "ALL":
                    agent_map = store.list_agent_contexts(conv_id)
                    for name in agent_map:
                        if name == "*":
                            store.save_context(conv_id, list(_rf_msgs))
                        else:
                            store.save_agent_context(conv_id, name, list(_rf_msgs))
                else:
                    _ctx_save(conv_id, list(_rf_msgs), _rf_agent)
                return {"action": "full_restore", "messages": len(_rf_msgs),
                        "tokens_after": estimated,
                        "agent": _rf_agent or "shared"}

            return self._run_bg_context_op(conv_id, "rebuild_full", _do_rebuild_full, flowfile)
            return [flowfile]

        if action == "get_context":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Sub-conversation context: load from sub-conv directly
            if _ctx_agent.startswith("task:"):
                _sub_tid = _ctx_agent.split("(")[0].replace("task:", "").strip()
                _sub_cid = f"{conv_id}::task::{_sub_tid}"
                context_data = store.load(_sub_cid) or []
                diverged = True
            else:
                context_data = _ctx_load(conv_id, _ctx_agent)
                diverged = context_data is not None
                if context_data is None:
                    context_data = store.load(conv_id, user_id=user_id) or []
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            # Classify messages for display
            display_msgs = []
            for m in context_data:
                role = m.get("role", "unknown")
                content = m.get("content", "")
                if isinstance(content, list):
                    text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                    content = "\n".join(text_parts) if text_parts else str(content)
                has_tool_calls = bool(m.get("tool_calls"))
                display_msgs.append({
                    "role": role,
                    "content": content[:300] if isinstance(content, str) else str(content)[:300],
                    "has_tool_calls": has_tool_calls,
                    "source": m.get("source"),
                })
            # Include agent context status map
            _agent_ctx_map = store.list_agent_contexts(conv_id)
            # Include active sub-conversations (task contexts)
            _extras = store.get_extras(conv_id, user_id=user_id) or {}
            for ek in _extras:
                if ek.startswith("task_log:"):
                    _tid = ek[9:]
                    _sub_cid = f"{conv_id}::task::{_tid}"
                    _sub_msgs = store.load(_sub_cid)
                    if _sub_msgs:
                        # Find agent name from task data
                        _tasks_data = _extras.get("agent_tasks", {})
                        _t_entry = _tasks_data.get(_tid, {}) if isinstance(_tasks_data, dict) else {}
                        _t_agent = _t_entry.get("agent", "?")
                        _agent_ctx_map[f"task:{_tid} ({_t_agent})"] = "sub-conv"
            flowfile.set_content(json.dumps({
                "context": display_msgs,
                "message_count": len(context_data),
                "token_estimate": estimated,
                "diverged": diverged,
                "agent_name": _ctx_agent or "",
                "agent_contexts": _agent_ctx_map,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "get_context_full":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            diverged = context_data is not None
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            flowfile.set_content(json.dumps({
                "context": context_data,
                "message_count": len(context_data),
                "diverged": diverged,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "edit_context":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            index = body.get("index")
            new_content = body.get("content", "")
            new_role = body.get("role")
            if not conv_id or index is None:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            _using_context = context_data is not None
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            if index < 0 or index >= len(context_data):
                flowfile.set_content(json.dumps({
                    "error": f"Index {index} out of range (0-{len(context_data)-1}). "
                             "The context may have changed — please refresh.",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data[index]["content"] = new_content
            if new_role:
                context_data[index]["role"] = new_role
            _ctx_save(conv_id, context_data)
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message_count": len(context_data),
                "token_estimate": estimated,
            }).encode())
            return [flowfile]

        if action == "delete_sub_context":
            conv_id = body.get("conversation_id", "")
            sub_name = body.get("agent_name", "")  # "task:t_xxx (AgentName)"
            if not conv_id or not sub_name or not sub_name.startswith("task:"):
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or task agent_name"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _sub_tid = sub_name.split("(")[0].replace("task:", "").strip()
            _sub_cid = f"{conv_id}::task::{_sub_tid}"
            try:
                store.delete(_sub_cid)
                # Also clean up sync counter and task log
                store.set_extra(conv_id, f"_sub_sync:{_sub_cid}", None)
                store.set_extra(conv_id, f"task_log:{_sub_tid}", None)
                flowfile.set_content(json.dumps({"ok": True, "deleted": _sub_cid}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "delete_context_message":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            index = body.get("index")
            if not conv_id or index is None:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or index"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            if index < 0 or index >= len(context_data):
                # Index from overlay may target messages if context was compacted;
                # fall back to messages list
                msgs = store.load(conv_id, user_id=user_id) or []
                if 0 <= index < len(msgs):
                    msgs.pop(index)
                    store.save(conv_id, msgs, user_id=user_id)
                    deserialized = self._deserialize_messages(msgs)
                    estimated = self._estimate_tokens(deserialized)
                    flowfile.set_content(json.dumps({
                        "ok": True,
                        "message_count": len(msgs),
                        "token_estimate": estimated,
                    }).encode())
                    return [flowfile]
                flowfile.set_content(json.dumps({
                    "error": f"Index {index} out of range (0-{len(context_data)-1}). "
                             "The context may have changed — please refresh.",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data.pop(index)
            _ctx_save(conv_id, context_data)
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message_count": len(context_data),
                "token_estimate": estimated,
            }).encode())
            return [flowfile]

        if action == "replace_context":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            new_context = body.get("context", [])
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            for msg in new_context:
                if "role" not in msg or "content" not in msg:
                    flowfile.set_content(json.dumps({"error": "Each message must have 'role' and 'content'"}).encode())
                    flowfile.set_attribute("http.response.status", "400")
                    return [flowfile]
            _ctx_save(conv_id, new_context)
            deserialized = self._deserialize_messages(new_context)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message_count": len(new_context),
                "token_estimate": estimated,
            }).encode())
            return [flowfile]

        if action == "add_context_message":
            conv_id = body.get("conversation_id", "")
            _ctx_agent = body.get("agent_name", "")
            role = body.get("role", "user")
            content = body.get("content", "")
            index = body.get("index")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            context_data = _ctx_load(conv_id, _ctx_agent)
            if context_data is None:
                context_data = store.load(conv_id, user_id=user_id) or []
            msg = {"role": role, "content": content}
            if index is not None:
                context_data.insert(index, msg)
            else:
                context_data.append(msg)
            _ctx_save(conv_id, context_data)
            deserialized = self._deserialize_messages(context_data)
            estimated = self._estimate_tokens(deserialized)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message_count": len(context_data),
                "token_estimate": estimated,
            }).encode())
            return [flowfile]

        if action == "create_agent":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("name", "").strip()
            agent_prompt = body.get("prompt", "").strip()
            if not agent_name or not agent_prompt:
                flowfile.set_content(json.dumps({
                    "error": "Missing name or prompt",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            try:
                data = {"prompt": agent_prompt}
                model = body.get("model", "")
                if model:
                    data["model"] = model
                tools = body.get("tools")
                if tools:
                    data["tools"] = tools
                description = body.get("description", "")
                if description:
                    data["description"] = description
                if rs.exists("agent", agent_name, uid):
                    rs.update("agent", agent_name, uid, data)
                else:
                    rs.create("agent", agent_name, uid, data)
                # Auto-activate in conversation
                if conv_id:
                    active = store.get_extra(conv_id, "active_resources") or {}
                    active["agent"] = agent_name
                    store.set_extra(conv_id, "active_resources", active)
                flowfile.set_content(json.dumps({
                    "created": True, "name": agent_name,
                }).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "list_agents":
            conv_id = body.get("conversation_id", "")
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            agents_list = ResourceStore.instance().list_all("agent", uid,
                                                           conversation_id=conv_id)
            agents = {a["name"]: a for a in agents_list}
            # Get selected agent from active_resources
            selected = ""
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                selected = active.get("agent", "")
            flowfile.set_content(json.dumps({
                "agents": agents, "selected": selected,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "agent_disable":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            if not conv_id or not agent:
                flowfile.set_content(json.dumps({"error": "Missing params"}).encode())
                return [flowfile]
            disabled = store.get_extra(conv_id, "disabled_agents") or []
            if agent not in disabled:
                disabled.append(agent)
                store.set_extra(conv_id, "disabled_agents", disabled)
            flowfile.set_content(json.dumps({"result": f"Agent '{agent}' disabled in this conversation."}).encode())
            return [flowfile]

        if action == "agent_enable":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            if not conv_id or not agent:
                flowfile.set_content(json.dumps({"error": "Missing params"}).encode())
                return [flowfile]
            disabled = store.get_extra(conv_id, "disabled_agents") or []
            if agent in disabled:
                disabled.remove(agent)
                store.set_extra(conv_id, "disabled_agents", disabled)
            flowfile.set_content(json.dumps({"result": f"Agent '{agent}' enabled in this conversation."}).encode())
            return [flowfile]

        if action == "agent_promote":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            target_scope = body.get("target_scope", "user")
            if not agent:
                flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore, GLOBAL_USER_ID
            rs = ResourceStore.instance()
            item = rs.get_any("agent", agent, user_id, conversation_id=conv_id)
            if not item:
                flowfile.set_content(json.dumps({"error": f"Agent '{agent}' not found"}).encode())
                return [flowfile]
            current_scope = item.get("_scope", "user")
            promote_data = {k: v for k, v in item.items() if not k.startswith("_") and k != "name"}
            if target_scope == "user":
                rs.create("agent", agent, user_id, promote_data)
            elif target_scope == "global":
                rs.create("agent", agent, GLOBAL_USER_ID, promote_data)
            elif target_scope == "conversation" and conv_id:
                conv_agents = store.get_extra(conv_id, "conversation_agents") or {}
                conv_agents[agent] = promote_data
                store.set_extra(conv_id, "conversation_agents", conv_agents)
            flowfile.set_content(json.dumps({
                "result": f"Agent '{agent}' promoted from {current_scope} to {target_scope}."
            }).encode())
            return [flowfile]

        if action == "create_agent":
            conv_id = body.get("conversation_id", "")
            agent = body.get("name", "")
            prompt = body.get("prompt", "")
            scope = body.get("scope", "user")
            if not agent or not prompt:
                flowfile.set_content(json.dumps({"error": "Missing name or prompt"}).encode())
                return [flowfile]
            agent_data = {"prompt": prompt}
            if scope == "conversation" and conv_id:
                conv_agents = store.get_extra(conv_id, "conversation_agents") or {}
                conv_agents[agent] = agent_data
                store.set_extra(conv_id, "conversation_agents", conv_agents)
            else:
                from core.resource_store import ResourceStore
                ResourceStore.instance().create("agent", agent, user_id, agent_data)
            flowfile.set_content(json.dumps({
                "result": f"Agent '{agent}' created (scope: {scope})."
            }).encode())
            return [flowfile]

        if action == "set_llm_service":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            svc_value = body.get("llm_service", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                return [flowfile]
            overrides = store.get_extra(conv_id, "agent_llm_overrides") or {}
            if svc_value == "restore" or svc_value == "":
                overrides.pop(agent, None)
                store.set_extra(conv_id, "agent_llm_overrides", overrides)
                flowfile.set_content(json.dumps({
                    "result": f"LLM service for '{agent}' restored to default."
                }).encode())
            else:
                # Accept expressions like ${global.xxx} or direct service names
                overrides[agent] = svc_value
                store.set_extra(conv_id, "agent_llm_overrides", overrides)
                flowfile.set_content(json.dumps({
                    "result": f"LLM service for '{agent}' set to '{svc_value}' in this conversation."
                }).encode())
            return [flowfile]

        if action == "select_agent":
            conv_id = body.get("conversation_id", "")
            agent_name = body.get("name", "").strip()
            if agent_name:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            if not agent_name:
                flowfile.set_content(json.dumps({"error": "Missing agent name"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            if ResourceStore.instance().get_any("agent", agent_name, uid) is None:
                flowfile.set_content(json.dumps({
                    "error": f"Agent '{agent_name}' not found",
                }).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            active = store.get_extra(conv_id, "active_resources") or {}
            active["agent"] = agent_name
            store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "selected": agent_name,
            }).encode())
            return [flowfile]

        if action == "delete_agent":
            agent_name = body.get("name", "").strip()
            conv_id = body.get("conversation_id", "")
            if agent_name and conv_id:
                agent_name = self._resolve_agent_name(agent_name, conv_id)
            if not agent_name:
                flowfile.set_content(json.dumps({
                    "error": "Missing name",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            deleted = ResourceStore.instance().delete("agent", agent_name, uid)
            # Fall back to "assistant" if deleted agent was active
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                if active.get("agent") == agent_name:
                    active["agent"] = "assistant"
                    store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "deleted": deleted, "name": agent_name,
            }).encode())
            return [flowfile]

        if action in ("create_skill", "add_skill"):
            skill_name = body.get("name", "").strip()
            skill_prompt = body.get("prompt", "").strip()
            conv_id = body.get("conversation_id", "")
            if not skill_name or not skill_prompt:
                flowfile.set_content(json.dumps({
                    "error": "Missing name or prompt",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            try:
                data = {"prompt": skill_prompt}
                description = body.get("description", "")
                if description:
                    data["description"] = description
                if rs.exists("skill", skill_name, uid):
                    rs.update("skill", skill_name, uid, data)
                else:
                    rs.create("skill", skill_name, uid, data)
                # Auto-activate in conversation
                if conv_id:
                    active = store.get_extra(conv_id, "active_resources") or {}
                    skills = active.get("skills", [])
                    if skill_name not in skills:
                        skills.append(skill_name)
                    active["skills"] = skills
                    store.set_extra(conv_id, "active_resources", active)
                flowfile.set_content(json.dumps({
                    "created": True, "name": skill_name,
                }).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "delete_skill":
            skill_name = body.get("name", "").strip()
            conv_id = body.get("conversation_id", "")
            if not skill_name:
                flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            deleted = ResourceStore.instance().delete("skill", skill_name, uid)
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                skills = active.get("skills", [])
                if skill_name in skills:
                    skills.remove(skill_name)
                active["skills"] = skills
                store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "deleted": deleted, "name": skill_name,
            }).encode())
            return [flowfile]

        if action == "list_skills":
            from core.resource_store import ResourceStore
            uid = user_id or "anonymous"
            skills = ResourceStore.instance().list_all("skill", uid)
            conv_id = body.get("conversation_id", "")
            active_skills = []
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                active_skills = active.get("skills", [])
            flowfile.set_content(json.dumps({
                "skills": [{
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "prompt": s.get("prompt", "")[:80],
                    "active": s["name"] in active_skills,
                } for s in skills],
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "check_files":
            file_ids = body.get("file_ids", [])
            if not file_ids:
                flowfile.set_content(json.dumps({"available": []}).encode())
                return [flowfile]
            from core.file_store import FileStore
            fs = FileStore.instance()
            available = [fid for fid in file_ids if fs.exists(fid)]
            flowfile.set_content(json.dumps({"available": available}).encode())
            return [flowfile]

        if action == "list_resources":
            # List all resource types for the user
            conv_id = body.get("conversation_id", "")
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            active = {}
            if conv_id:
                active = store.get_extra(conv_id, "active_resources") or {}
                active = self._ensure_active_agent(conv_id, active, uid)
            # Build agents list with autoconv status
            agents_out = []
            for a in rs.list_all("agent", uid, conversation_id=conv_id):
                aname = a["name"]
                entry = {
                    "name": aname,
                    "description": a.get("description", ""),
                    "scope": a.get("_scope", ""),
                    "active": active.get("agent") == aname,
                }
                if conv_id:
                    ac_cfg = store.get_extra(conv_id, f"random_thought::{aname.lower()}") or {}
                    if ac_cfg.get("enabled"):
                        entry["autoconv"] = ac_cfg.get("frequency", "on")
                agents_out.append(entry)
            result = {
                "agents": agents_out,
                "skills": [{
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "scope": s.get("_scope", ""),
                    "active": s["name"] in active.get("skills", []),
                } for s in rs.list_all("skill", uid, conversation_id=conv_id)],
                "mcp_servers": [{
                    "name": m["name"],
                    "url": m.get("url", ""),
                    "scope": m.get("_scope", ""),
                    "active": m["name"] in active.get("mcps", []),
                } for m in rs.list_all("mcp", uid, conversation_id=conv_id)],
                "task_defs": [{
                    "name": t["name"],
                    "description": t.get("description", "") or t.get("prompt", "")[:60],
                    "scope": t.get("_scope", ""),
                    "default_interval": t.get("default_interval", "6/1m"),
                } for t in rs.list_all("task_def", uid, conversation_id=conv_id)],
            }
            # Running task instances for this conversation
            if conv_id:
                all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
                running = []
                for tid, t in all_tasks.items():
                    if not isinstance(t, dict):
                        continue
                    running.append({
                        "task_id": tid,
                        "agent": t.get("agent", ""),
                        "task": t.get("task", "")[:80],
                        "status": t.get("status", ""),
                        "iterations": t.get("iterations_done", 0),
                        "max_iterations": t.get("max_iterations", 50),
                        "task_def_name": t.get("task_def_name", ""),
                    })
                result["running_tasks"] = running
            # Services (global + user)
            try:
                from gui.services.global_service_registry import GlobalServiceRegistry
                from gui.services.user_service_registry import UserServiceRegistry
                svcs = []
                greg = GlobalServiceRegistry.get_instance()
                for sid, sdef in greg.get_all_definitions().items():
                    svcs.append({
                        "service_id": sid,
                        "service_type": getattr(sdef, "service_type", ""),
                        "enabled": getattr(sdef, "enabled", True),
                        "description": getattr(sdef, "description", ""),
                        "scope": "global",
                    })
                if uid and uid != "anonymous":
                    ureg = UserServiceRegistry.get_instance()
                    for sid, sdef in ureg.get_all_for_user(uid).items():
                        svcs.append({
                            "service_id": sid,
                            "service_type": getattr(sdef, "service_type", ""),
                            "enabled": getattr(sdef, "enabled", True),
                            "description": getattr(sdef, "description", ""),
                            "scope": "user",
                        })
                result["services"] = svcs
            except Exception:
                result["services"] = []
            # Deployed flows (global=readonly, user+conv visible)
            try:
                from gui.services.deployment_registry import DeploymentRegistry
                flows = []
                dr = DeploymentRegistry.get_instance()
                dr.sync_with_executors()
                uid = user_id or "anonymous"
                for iid, inst in dr.get_all().items():
                    # Determine scope
                    if not inst.owner or inst.owner == "__global__":
                        fscope = "global"
                    elif inst.conversation_id:
                        fscope = "conversation"
                        # Only show conv-scoped flows in their conversation
                        if inst.conversation_id != conv_id:
                            continue
                    else:
                        fscope = "user"
                    # Skip other users' flows
                    if fscope != "global" and inst.owner != uid:
                        continue
                    flows.append({
                        "instance_id": iid,
                        "flow_name": inst.flow_name,
                        "status": inst.status,
                        "owner": inst.owner or "global",
                        "scope": fscope,
                        "template": inst.flow_id,
                    })
                result["flows"] = flows
            except Exception:
                result["flows"] = []
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            return [flowfile]

        if action == "get_resource_detail":
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            if not rtype or not rname:
                flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            conv_id = body.get("conversation_id", "")
            item = rs.get_any(rtype, rname, uid, conversation_id=conv_id)
            if not item:
                flowfile.set_content(json.dumps({"error": f"{rtype} '{rname}' not found"}).encode())
                return [flowfile]
            flowfile.set_content(json.dumps(item, ensure_ascii=False).encode())
            return [flowfile]

        if action == "update_resource":
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            data = body.get("data", {})
            scope = body.get("scope", "user")
            if scope == "global":
                flowfile.set_content(json.dumps({"error": "Cannot update global resources from chat. Use the admin GUI."}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            if not rtype or not rname:
                flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            target_uid = "__global__" if scope == "global" else uid
            try:
                rs.update(rtype, rname, target_uid, data)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "create_resource":
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            data = body.get("data", {})
            scope = body.get("scope", "user")
            if scope == "global":
                flowfile.set_content(json.dumps({"error": "Cannot create global resources from chat. Use the admin GUI."}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            if not rtype or not rname:
                flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            target_uid = "__global__" if scope == "global" else uid
            if rtype == "task_def":
                data.setdefault("created_by", uid)
            try:
                rs.create(rtype, rname, target_uid, data)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "delete_resource":
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            scope = body.get("scope", "user")
            if scope == "global":
                flowfile.set_content(json.dumps({"error": "Cannot delete global resources from chat. Use the admin GUI."}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            if not rtype or not rname:
                flowfile.set_content(json.dumps({"error": "Missing resource_type or name"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            target_uid = "__global__" if scope == "global" else uid
            deleted = rs.delete(rtype, rname, target_uid)
            flowfile.set_content(json.dumps({"ok": True, "deleted": deleted}).encode())
            return [flowfile]

        if action == "copy_resource_scope":
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            target_scope = body.get("target_scope", "")
            if not rtype or not rname or not target_scope:
                flowfile.set_content(json.dumps({"error": "Missing resource_type, name, or target_scope"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            conv_id = body.get("conversation_id", "")
            item = rs.get_any(rtype, rname, uid, conversation_id=conv_id)
            if not item:
                flowfile.set_content(json.dumps({"error": f"{rtype} '{rname}' not found"}).encode())
                return [flowfile]
            target_uid = "__global__" if target_scope == "global" else uid
            data = {k: v for k, v in item.items() if k not in ("name", "_scope")}
            try:
                rs.create(rtype, rname, target_uid, data)
                flowfile.set_content(json.dumps({"ok": True, "copied_to": target_scope}).encode())
            except Exception as e:
                # If exists, update instead
                try:
                    rs.update(rtype, rname, target_uid, data)
                    flowfile.set_content(json.dumps({"ok": True, "copied_to": target_scope, "updated": True}).encode())
                except Exception as e2:
                    flowfile.set_content(json.dumps({"error": str(e2)}).encode())
            return [flowfile]

        if action == "list_params_secrets":
            conv_id = body.get("conversation_id", "")
            uid = user_id or "anonymous"
            params_out = []
            secrets_out = []
            # Global params
            from core.expression import _load_global_parameters, _load_global_secrets
            for k, v in _load_global_parameters().items():
                params_out.append({"key": k, "value": str(v), "scope": "global"})
            # User params
            if uid and uid != "anonymous":
                from core.expression import _load_user_parameters, _load_user_secrets
                for k, v in _load_user_parameters(uid).items():
                    params_out.append({"key": k, "value": str(v), "scope": "user"})
                # User secrets (names only)
                for k in _load_user_secrets(uid).keys():
                    secrets_out.append({"key": k, "scope": "user"})
            # Global secrets (names only)
            for k in _load_global_secrets().keys():
                secrets_out.append({"key": k, "scope": "global"})
            # Conv params/secrets
            if conv_id:
                cp = store.get_extra(conv_id, "conv_parameters") or {}
                for k, v in cp.items():
                    params_out.append({"key": k, "value": str(v), "scope": "conversation"})
                cs = store.get_extra(conv_id, "conv_secrets") or {}
                for k in cs.keys():
                    secrets_out.append({"key": k, "scope": "conversation"})
            flowfile.set_content(json.dumps({
                "parameters": params_out, "secrets": secrets_out,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "set_param":
            key = body.get("key", "").strip()
            value = body.get("value", "")
            scope = body.get("scope", "user")
            conv_id = body.get("conversation_id", "")
            if scope == "global":
                flowfile.set_content(json.dumps({"error": "Cannot write global parameters from chat. Use the admin GUI."}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            if not key:
                flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
                return [flowfile]
            if scope == "conversation" and conv_id:
                cp = store.get_extra(conv_id, "conv_parameters") or {}
                cp[key] = value
                store.set_extra(conv_id, "conv_parameters", cp)
            else:  # user
                uid = user_id or "anonymous"
                from core.config_store import ConfigStore
                from pathlib import Path as _CfgPath
                path = _CfgPath(f"config/users/{uid}/parameters.json")
                path.parent.mkdir(parents=True, exist_ok=True)
                data = ConfigStore.load_params(path)
                data[key] = value
                ConfigStore.save_params(path, data)
            flowfile.set_content(json.dumps({"ok": True}).encode())
            return [flowfile]

        if action == "delete_param":
            key = body.get("key", "").strip()
            scope = body.get("scope", "user")
            conv_id = body.get("conversation_id", "")
            if scope == "global":
                flowfile.set_content(json.dumps({"error": "Cannot delete global parameters from chat. Use the admin GUI."}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            if not key:
                flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
                return [flowfile]
            if scope == "conversation" and conv_id:
                cp = store.get_extra(conv_id, "conv_parameters") or {}
                cp.pop(key, None)
                store.set_extra(conv_id, "conv_parameters", cp)
            else:  # user
                uid = user_id or "anonymous"
                from core.config_store import ConfigStore
                from pathlib import Path as _CfgPath
                path = _CfgPath(f"config/users/{uid}/parameters.json")
                data = ConfigStore.load_params(path)
                data.pop(key, None)
                ConfigStore.save_params(path, data)
            flowfile.set_content(json.dumps({"ok": True}).encode())
            return [flowfile]

        if action == "set_secret":
            key = body.get("key", "").strip()
            value = body.get("value", "")
            scope = body.get("scope", "user")
            conv_id = body.get("conversation_id", "")
            if scope == "global":
                flowfile.set_content(json.dumps({"error": "Cannot write global secrets from chat. Use the admin GUI."}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            if not key:
                flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
                return [flowfile]
            from core.secrets import SecretsManager
            sm = SecretsManager.get_instance()
            if scope == "conversation" and conv_id:
                cs = store.get_extra(conv_id, "conv_secrets") or {}
                cs[key] = sm.encrypt(value)
                store.set_extra(conv_id, "conv_secrets", cs)
            else:  # user
                uid = user_id or "anonymous"
                from core.config_store import ConfigStore
                from pathlib import Path as _CfgPath
                path = _CfgPath(f"config/users/{uid}/secrets.json")
                path.parent.mkdir(parents=True, exist_ok=True)
                data = ConfigStore.load_secrets(path)
                data[key] = value  # ConfigStore.save_secrets encrypts
                ConfigStore.save_secrets(path, data)
            flowfile.set_content(json.dumps({"ok": True}).encode())
            return [flowfile]

        if action == "delete_secret":
            key = body.get("key", "").strip()
            scope = body.get("scope", "user")
            conv_id = body.get("conversation_id", "")
            if scope == "global":
                flowfile.set_content(json.dumps({"error": "Cannot delete global secrets from chat. Use the admin GUI."}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            if not key:
                flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
                return [flowfile]
            if scope == "conversation" and conv_id:
                cs = store.get_extra(conv_id, "conv_secrets") or {}
                cs.pop(key, None)
                store.set_extra(conv_id, "conv_secrets", cs)
            else:  # user
                uid = user_id or "anonymous"
                from core.config_store import ConfigStore
                from pathlib import Path as _CfgPath
                path = _CfgPath(f"config/users/{uid}/secrets.json")
                data = ConfigStore.load_secrets(path)
                data.pop(key, None)
                ConfigStore.save_secrets(path, data)
            flowfile.set_content(json.dumps({"ok": True}).encode())
            return [flowfile]

        if action == "get_service_detail":
            sid = body.get("service_id", "")
            scope = body.get("scope", "global")
            if not sid:
                flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
                return [flowfile]
            try:
                if scope == "user" and user_id:
                    from gui.services.user_service_registry import UserServiceRegistry
                    ureg = UserServiceRegistry.get_instance()
                    sdef = ureg.get_all_for_user(user_id).get(sid)
                else:
                    from gui.services.global_service_registry import GlobalServiceRegistry
                    sdef = GlobalServiceRegistry.get_instance().get_all_definitions().get(sid)
                if not sdef:
                    flowfile.set_content(json.dumps({"error": f"Service '{sid}' not found"}).encode())
                    return [flowfile]
                flowfile.set_content(json.dumps({
                    "service_id": sid,
                    "service_type": getattr(sdef, "service_type", ""),
                    "config": getattr(sdef, "config", {}),
                    "enabled": getattr(sdef, "enabled", True),
                    "description": getattr(sdef, "description", ""),
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "update_service":
            sid = body.get("service_id", "")
            scope = body.get("scope", "global")
            config = body.get("config", {})
            if not sid:
                flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
                return [flowfile]
            try:
                if scope == "user" and user_id:
                    from gui.services.user_service_registry import UserServiceRegistry
                    ureg = UserServiceRegistry.get_instance()
                    ureg.update_config(user_id, sid, config)
                else:
                    from gui.services.global_service_registry import GlobalServiceRegistry
                    GlobalServiceRegistry.get_instance().update_config(sid, config)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "toggle_service":
            sid = body.get("service_id", "")
            enabled = body.get("enabled", True)
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                uid = user_id or "anonymous"
                ureg.set_enabled(uid, sid, enabled)
                flowfile.set_content(json.dumps({"ok": True, "enabled": enabled}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "delete_service":
            sid = body.get("service_id", "")
            scope = body.get("scope", "user")
            if scope == "global":
                flowfile.set_content(json.dumps({"error": "Cannot delete global services from chat"}).encode())
                return [flowfile]
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                uid = user_id or "anonymous"
                UserServiceRegistry.get_instance().uninstall(uid, sid)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action in ("start_flow", "stop_flow", "undeploy_flow"):
            iid = body.get("instance_id", "")
            if not iid:
                flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
                return [flowfile]
            try:
                from gui.services.executor_registry import ExecutorRegistry
                from gui.services.deployment_registry import DeploymentRegistry
                reg = ExecutorRegistry.get_instance()
                dr = DeploymentRegistry.get_instance()
                inst = dr.get(iid)
                if inst and user_id and inst.owner and inst.owner != user_id:
                    flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                    return [flowfile]
                if action == "stop_flow":
                    ex = reg.get(iid)
                    if ex and ex.is_running:
                        ex.stop()
                    reg.unregister(iid)
                    flowfile.set_content(json.dumps({"ok": True, "status": "stopped"}).encode())
                elif action == "start_flow":
                    inst = dr.get_all().get(iid)
                    if not inst:
                        flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                        return [flowfile]
                    reg._restore_instance(iid, inst.flow_path,
                                           inst.max_workers, inst.max_retries,
                                           parameters=inst.parameters)
                    flowfile.set_content(json.dumps({"ok": True, "status": "running"}).encode())
                elif action == "undeploy_flow":
                    ex = reg.get(iid)
                    if ex and ex.is_running:
                        ex.stop()
                    reg.unregister(iid)
                    dr.undeploy(iid)
                    flowfile.set_content(json.dumps({"ok": True, "status": "undeployed"}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "list_available_flows":
            try:
                from pathlib import Path as _Path
                flows_dir = _Path("flows")
                templates = []
                if flows_dir.is_dir():
                    for fp in sorted(flows_dir.glob("*.json")):
                        try:
                            raw = json.loads(fp.read_text(encoding="utf-8"))
                            templates.append({
                                "id": raw.get("id", fp.stem),
                                "name": raw.get("name", fp.stem),
                                "version": raw.get("version", ""),
                                "description": raw.get("description", ""),
                                "tasks_count": len(raw.get("tasks", {})),
                                "services_count": len(raw.get("services", {})),
                                "file_path": str(fp),
                            })
                        except Exception:
                            pass
                flowfile.set_content(json.dumps({"templates": templates}, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "deploy_flow":
            template_id = body.get("template_id", "")
            scope = body.get("scope", "user")
            params = body.get("parameters", {})
            conv_id = body.get("conversation_id", "")
            if scope == "global":
                flowfile.set_content(json.dumps(
                    {"error": "Cannot deploy global flows from chat — use admin GUI"}).encode())
                return [flowfile]
            if not template_id:
                flowfile.set_content(json.dumps({"error": "Missing template_id"}).encode())
                return [flowfile]
            try:
                from pathlib import Path as _Path
                from gui.services.deployment_registry import DeploymentRegistry
                flows_dir = _Path("flows")
                tpath = None
                for fp in flows_dir.glob("*.json"):
                    try:
                        raw = json.loads(fp.read_text(encoding="utf-8"))
                        if raw.get("id", fp.stem) == template_id:
                            tpath = fp
                            break
                    except Exception:
                        pass
                if not tpath:
                    candidate = flows_dir / f"{template_id}.json"
                    if candidate.exists():
                        tpath = candidate
                if not tpath:
                    flowfile.set_content(json.dumps(
                        {"error": f"Template '{template_id}' not found in flows/"}).encode())
                    return [flowfile]
                dr = DeploymentRegistry.get_instance()
                uid = user_id or "anonymous"
                iid = dr.deploy(
                    template_path=str(tpath),
                    owner=uid,
                    parameters=params,
                    source="agent",
                    conversation_id=conv_id if scope == "conversation" else None,
                )
                flowfile.set_content(json.dumps(
                    {"ok": True, "instance_id": iid, "scope": scope}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "promote_flow":
            iid = body.get("instance_id", "")
            target_scope = body.get("target_scope", "user")
            if not iid:
                flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
                return [flowfile]
            if target_scope == "global":
                flowfile.set_content(json.dumps(
                    {"error": "Cannot promote to global from chat — use admin GUI"}).encode())
                return [flowfile]
            try:
                from gui.services.deployment_registry import DeploymentRegistry
                dr = DeploymentRegistry.get_instance()
                inst = dr.get(iid)
                if not inst:
                    flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                    return [flowfile]
                if user_id and inst.owner and inst.owner != user_id:
                    flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                    return [flowfile]
                if not inst.conversation_id:
                    flowfile.set_content(json.dumps({"error": "Flow is already user-scoped"}).encode())
                    return [flowfile]
                inst.conversation_id = None
                dr._save_instance(inst)
                flowfile.set_content(json.dumps({"ok": True, "scope": "user"}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "get_flow_instance":
            iid = body.get("instance_id", "")
            if not iid:
                flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
                return [flowfile]
            try:
                from gui.services.deployment_registry import DeploymentRegistry
                dr = DeploymentRegistry.get_instance()
                inst = dr.get(iid)
                if not inst:
                    flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                    return [flowfile]
                # Load template parameters schema for reference
                template_params = {}
                try:
                    from pathlib import Path as _Path
                    raw = json.loads(_Path(inst.flow_path).read_text(encoding="utf-8"))
                    template_params = raw.get("parameters", {})
                except Exception:
                    pass
                flowfile.set_content(json.dumps({
                    "instance_id": inst.instance_id,
                    "flow_name": inst.flow_name,
                    "flow_id": inst.flow_id,
                    "status": inst.status,
                    "parameters": inst.parameters,
                    "template_parameters": template_params,
                    "owner": inst.owner,
                    "scope": "conversation" if inst.conversation_id else "user" if inst.owner else "global",
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "update_flow_params":
            iid = body.get("instance_id", "")
            params = body.get("parameters", {})
            if not iid:
                flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
                return [flowfile]
            try:
                from gui.services.deployment_registry import DeploymentRegistry
                dr = DeploymentRegistry.get_instance()
                inst = dr.get(iid)
                if not inst:
                    flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                    return [flowfile]
                if user_id and inst.owner and inst.owner != user_id:
                    flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                    return [flowfile]
                inst.parameters.update(params)
                dr._save_instance(inst)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "activate_resource":
            conv_id = body.get("conversation_id", "")
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            if not conv_id or not rtype or not rname:
                flowfile.set_content(json.dumps({
                    "error": "Missing conversation_id, resource_type, or name",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            active = store.get_extra(conv_id, "active_resources") or {}
            if rtype == "agent":
                active["agent"] = rname
            elif rtype == "skill":
                skills = active.get("skills", [])
                if rname not in skills:
                    skills.append(rname)
                active["skills"] = skills
            elif rtype == "mcp":
                mcps = active.get("mcps", [])
                if rname not in mcps:
                    mcps.append(rname)
                active["mcps"] = mcps
            store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "activated": True, "type": rtype, "name": rname,
            }).encode())
            return [flowfile]

        if action == "deactivate_resource":
            conv_id = body.get("conversation_id", "")
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            if not conv_id or not rtype or not rname:
                flowfile.set_content(json.dumps({
                    "error": "Missing conversation_id, resource_type, or name",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            active = store.get_extra(conv_id, "active_resources") or {}
            if rtype == "agent":
                if active.get("agent") == rname:
                    active.pop("agent", None)
            elif rtype == "skill":
                skills = active.get("skills", [])
                if rname in skills:
                    skills.remove(rname)
                active["skills"] = skills
            elif rtype == "mcp":
                mcps = active.get("mcps", [])
                if rname in mcps:
                    mcps.remove(rname)
                active["mcps"] = mcps
            store.set_extra(conv_id, "active_resources", active)
            flowfile.set_content(json.dumps({
                "deactivated": True, "type": rtype, "name": rname,
            }).encode())
            return [flowfile]

        if action == "share_resource":
            rtype = body.get("resource_type", "")
            rname = body.get("name", "").strip()
            target_conv = body.get("target_conversation_id", "")
            if not rtype or not rname or not target_conv:
                flowfile.set_content(json.dumps({
                    "error": "Missing resource_type, name, or target_conversation_id",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            # Verify ownership of target conversation
            target_meta = store.get_metadata(target_conv)
            if not target_meta or (user_id and target_meta.get("user_id") != user_id):
                flowfile.set_content(json.dumps({
                    "error": "Target conversation not found or access denied",
                }).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            # Activate in target
            active = store.get_extra(target_conv, "active_resources") or {}
            if rtype == "agent":
                active["agent"] = rname
            elif rtype == "skill":
                skills = active.get("skills", [])
                if rname not in skills:
                    skills.append(rname)
                active["skills"] = skills
            elif rtype == "mcp":
                mcps = active.get("mcps", [])
                if rname not in mcps:
                    mcps.append(rname)
                active["mcps"] = mcps
            store.set_extra(target_conv, "active_resources", active)
            flowfile.set_content(json.dumps({
                "shared": True, "type": rtype, "name": rname,
                "target": target_conv,
            }).encode())
            return [flowfile]

        if action == "link_telegram":
            tg_user_id = body.get("telegram_user_id", "").strip()
            bot_token = body.get("bot_token", "").strip()
            if not user_id:
                flowfile.set_content(json.dumps({
                    "error": "Authentication required",
                }).encode())
                flowfile.set_attribute("http.response.status", "401")
                return [flowfile]
            if not tg_user_id:
                flowfile.set_content(json.dumps({
                    "error": "Missing telegram_user_id",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.identity_service import IdentityService
            linked = IdentityService.instance().link(
                user_id, "telegram", tg_user_id, bot_token=bot_token,
            )
            if not linked:
                flowfile.set_content(json.dumps({
                    "error": "This Telegram ID is already linked to another user",
                }).encode())
                flowfile.set_attribute("http.response.status", "409")
                return [flowfile]
            result = {"linked": True, "telegram_user_id": tg_user_id}
            # Register personal bot in the pool
            if bot_token:
                try:
                    from services.telegram_bot_service import TelegramBotPool
                    username = TelegramBotPool.instance().register_bot(
                        bot_token, user_id,
                    )
                    result["bot_username"] = username
                except Exception as e:
                    result["bot_warning"] = f"Bot token invalid: {e}"
            flowfile.set_content(json.dumps(result).encode())
            return [flowfile]

        if action == "unlink_telegram":
            if not user_id:
                flowfile.set_content(json.dumps({
                    "error": "Authentication required",
                }).encode())
                flowfile.set_attribute("http.response.status", "401")
                return [flowfile]
            from core.identity_service import IdentityService
            ids = IdentityService.instance()
            # Unregister personal bot from pool before unlinking
            bot_token = ids.get_bot_token(user_id, "telegram")
            if bot_token:
                try:
                    from services.telegram_bot_service import TelegramBotPool
                    TelegramBotPool.instance().unregister_bot(bot_token)
                except Exception:
                    pass
            unlinked = ids.unlink(user_id, "telegram")
            flowfile.set_content(json.dumps({
                "unlinked": unlinked,
            }).encode())
            return [flowfile]

        if action == "get_links":
            if not user_id:
                flowfile.set_content(json.dumps({"links": {}}).encode())
                return [flowfile]
            from core.identity_service import IdentityService
            ids = IdentityService.instance()
            links = ids.get_links(user_id)
            active_conv = ids.get_active_conv(user_id, "telegram")
            flowfile.set_content(json.dumps({
                "links": links, "active_telegram_conv": active_conv,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "get_usage":
            try:
                from core.token_tracker import TokenTracker
                is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
                if is_admin:
                    usage = TokenTracker.instance().get_all_usage()
                else:
                    usage = {user_id: TokenTracker.instance().get_usage(user_id)}
                flowfile.set_content(json.dumps({
                    "usage": usage,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "list_memories":
            try:
                from core.memory_store import MemoryStore
                ms = MemoryStore.instance()
                agent_filter = body.get("agent_name")  # None = all
                if agent_filter is not None:
                    entries = ms.list_by_agent(user_id, agent_filter)
                else:
                    entries = ms.list_all(user_id)
                result = [{
                    "id": e.id, "text": e.text, "tags": e.tags,
                    "created_at": e.created_at, "updated_at": e.updated_at,
                    "source": e.source, "agent": e.agent,
                    "conversation_id": e.conversation_id,
                } for e in entries]
                flowfile.set_content(json.dumps({
                    "memories": result, "count": len(result),
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "delete_memory":
            memory_id = body.get("memory_id", "")
            if not memory_id:
                flowfile.set_content(json.dumps({"error": "Missing memory_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                from core.memory_store import MemoryStore
                deleted = MemoryStore.instance().forget(user_id, memory_id)
                flowfile.set_content(json.dumps({"deleted": deleted}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "edit_memory":
            memory_id = body.get("memory_id", "")
            if not memory_id:
                flowfile.set_content(json.dumps({"error": "Missing memory_id"}).encode())
                return [flowfile]
            from core.memory_store import MemoryStore
            ms = MemoryStore.instance()
            updated = False
            if "text" in body:
                updated = ms.update_text(user_id, memory_id, body["text"]) or updated
            if "tags" in body:
                updated = ms.update_tags(user_id, memory_id, body["tags"]) or updated
            if "agent" in body:
                updated = ms.update_agent(user_id, memory_id, body["agent"]) or updated
            flowfile.set_content(json.dumps({"updated": updated}).encode())
            return [flowfile]

        if action == "add_memory":
            text = body.get("text", "")
            if not text:
                flowfile.set_content(json.dumps({"error": "Missing text"}).encode())
                return [flowfile]
            tags = body.get("tags", [])
            agent = body.get("agent", "")
            conv_id = body.get("conversation_id", "")
            scope = body.get("scope", "agent")  # global/agent/conversation/private
            # Resolve scope
            if scope == "global":
                agent, conv_id = "", ""
            elif scope == "conversation":
                agent = ""
            elif scope == "private":
                pass  # keep both
            else:  # agent
                conv_id = ""
            from core.memory_store import MemoryStore
            entry = MemoryStore.instance().remember(
                user_id, text, tags, source="user",
                agent=agent, conversation_id=conv_id,
            )
            flowfile.set_content(json.dumps({
                "id": entry.id, "text": entry.text,
                "tags": entry.tags, "agent": entry.agent,
                "conversation_id": entry.conversation_id,
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "install_tool":
            filename = body.get("filename", "")
            source = body.get("source", "")
            if not source:
                flowfile.set_content(json.dumps({"error": "Missing source code"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                from core.dynamic_tool_store import DynamicToolStore
                result = DynamicToolStore.instance().install(user_id, filename, source)
                # Reset tool registry so new tool is picked up
                self._tool_registry = None
                flowfile.set_content(json.dumps({
                    "installed": True, **result,
                }).encode())
            except (ValueError, PermissionError) as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        if action == "uninstall_tool":
            tool_name = body.get("tool_name", "")
            if not tool_name:
                flowfile.set_content(json.dumps({"error": "Missing tool_name"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                from core.dynamic_tool_store import DynamicToolStore
                is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
                removed = DynamicToolStore.instance().uninstall(
                    user_id, tool_name, is_admin=is_admin,
                )
                # Reset tool registry
                self._tool_registry = None
                flowfile.set_content(json.dumps({
                    "uninstalled": removed, "tool_name": tool_name,
                }).encode())
            except PermissionError as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                flowfile.set_attribute("http.response.status", "403")
            return [flowfile]

        if action == "list_tools":
            try:
                from core.dynamic_tool_store import DynamicToolStore
                is_admin = "admin" in (flowfile.get_attribute("http.auth.roles") or "")
                tools = DynamicToolStore.instance().list_tools(
                    user_id=user_id, is_admin=is_admin,
                )
                flowfile.set_content(json.dumps({
                    "tools": tools,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        # ── User tool call ─────────────────────────────────────────
        if action == "get_tool_schemas":
            # Return all builtin tool definitions for /call help
            registry = self.get_tool_registry()
            tools = [{
                "name": h.name,
                "description": h.description,
                "parameters": h.parameters_schema,
            } for h in registry.list_tools()]
            flowfile.set_content(json.dumps({"tools": tools}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "call_tool":
            tool_name = body.get("tool_name", "")
            tool_args = body.get("arguments", {})
            positional = body.get("positional_args", [])
            conv_id = body.get("conversation_id", "")
            if not tool_name:
                flowfile.set_content(json.dumps({"error": "Missing tool_name"}).encode())
                return [flowfile]
            registry = self.get_tool_registry()
            if conv_id or user_id:
                self._configure_tool_handlers(
                    registry, conversation_id=conv_id, user_id=user_id,
                )
            # Find handler
            handler = None
            for h in registry.list_tools():
                if h.name == tool_name:
                    handler = h
                    break
            if not handler:
                flowfile.set_content(json.dumps({
                    "error": f"Tool '{tool_name}' not found",
                }).encode())
                return [flowfile]
            # Map positional args to named params using schema
            if positional:
                schema = handler.parameters_schema or {}
                param_names = list((schema.get("properties") or {}).keys())
                for i, val in enumerate(positional):
                    if i < len(param_names):
                        key = param_names[i]
                        if key not in tool_args:
                            tool_args[key] = val
                    else:
                        flowfile.set_content(json.dumps({
                            "error": (
                                f"Too many positional arguments ({len(positional)}) "
                                f"for tool '{tool_name}' which has "
                                f"{len(param_names)} parameters: {param_names}"
                            ),
                        }).encode())
                        return [flowfile]
            # Execute in background thread — publish SSE events + persist
            # exactly like the agent streaming loop does
            _call_registry = registry
            _call_tool_name = tool_name
            _call_tool_args = tool_args
            _call_conv_id = conv_id
            _call_user_id = user_id

            def _run_user_tool_call():
                from core.conversation_event_bus import ConversationEventBus
                from core.conversation_store import ConversationStore
                bus = ConversationEventBus.instance()
                source = {"type": "user", "name": _call_user_id or "anonymous"}
                # Publish tool_call event (same as agent loop)
                bus.publish_event(_call_conv_id, "tool_call", {
                    "tool": _call_tool_name,
                    "arguments": _call_tool_args,
                    "agent_name": "user",
                    "llm_service": "",
                })
                # Execute
                try:
                    result_text = _call_registry.execute(
                        _call_tool_name, _call_tool_args,
                    ) or ""
                except Exception as _te:
                    result_text = f"Error: {_te}"
                    logger.error("User /call tool '%s' failed: %s",
                                 _call_tool_name, _te)
                # Publish tool_result event
                _result_preview = (result_text or "")[:2000]
                bus.publish_event(_call_conv_id, "tool_result", {
                    "tool": _call_tool_name,
                    "result": _result_preview,
                    "agent_name": "user",
                    "llm_service": "",
                })
                # Persist tool_call + tool_result messages in conversation
                if _call_conv_id:
                    import uuid as _uuid
                    tc_id = _uuid.uuid4().hex[:12]
                    msgs = [
                        {
                            "role": "assistant", "content": "",
                            "source": source,
                            "tool_calls": [{
                                "id": tc_id,
                                "name": _call_tool_name,
                                "arguments": _call_tool_args,
                            }],
                        },
                        {
                            "role": "tool",
                            "content": result_text,
                            "tool_call_id": tc_id,
                        },
                    ]
                    try:
                        cstore = ConversationStore.instance()
                        cstore.append_messages(
                            _call_conv_id, msgs,
                            user_id=_call_user_id,
                        )
                    except Exception as _pe:
                        logger.warning("Failed to persist /call messages: %s", _pe)

            thread = threading.Thread(
                target=_run_user_tool_call, daemon=True,
                name=f"user-call-{tool_name}",
            )
            thread.start()
            # Return ack immediately
            flowfile.set_content(json.dumps({
                "status": "accepted", "tool": tool_name,
            }).encode())
            return [flowfile]

        # ── User services ─────────────────────────────────────────
        if action == "service_list":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                defs = registry.get_all_for_user(user_id)
                services = []
                for sid, sdef in sorted(defs.items()):
                    services.append({
                        "id": sid,
                        "type": sdef.service_type,
                        "enabled": sdef.enabled,
                        "connected": registry.is_connected(user_id, sid),
                        "description": sdef.description,
                    })
                flowfile.set_content(json.dumps({
                    "services": services,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "list_service_types":
            from core import ServiceFactory
            types = []
            for stype in sorted(ServiceFactory.list_types()):
                try:
                    cls = ServiceFactory.get(stype)
                    types.append({
                        "type": stype,
                        "name": getattr(cls, "NAME", stype),
                        "description": getattr(cls, "DESCRIPTION", ""),
                    })
                except Exception:
                    types.append({"type": stype, "name": stype, "description": ""})
            flowfile.set_content(json.dumps({"service_types": types}).encode())
            return [flowfile]

        if action == "get_service_schema":
            svc_type = body.get("service_type", "")
            if not svc_type:
                flowfile.set_content(json.dumps({"error": "Missing service_type"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                from core import ServiceFactory
                cls = ServiceFactory.get(svc_type)
                instance = object.__new__(cls)
                instance.config = {}
                schema = instance.get_parameter_schema()
                flowfile.set_content(json.dumps({
                    "type": svc_type,
                    "name": getattr(cls, "NAME", svc_type),
                    "description": getattr(cls, "DESCRIPTION", ""),
                    "parameters": schema,
                }).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                flowfile.set_attribute("http.response.status", "404")
            return [flowfile]

        if action == "service_install":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc_type = body.get("service_type", "")
                svc_name = body.get("service_name", "")
                config_str = body.get("config_str", "")
                if not svc_type or not svc_name:
                    flowfile.set_content(json.dumps({
                        "error": "Usage: /service install <type> <name> [key=val,...]",
                    }).encode())
                    return [flowfile]
                # Accept config as dict or as "key=val,key2=val2" string
                config = body.get("config", {})
                if not config and config_str:
                    for pair in config_str.split(","):
                        pair = pair.strip()
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            config[k.strip()] = v.strip()
                description = body.get("description", "")
                sdef = registry.install(
                    user_id=user_id,
                    service_id=svc_name,
                    service_type=svc_type,
                    config=config,
                    description=description,
                )
                flowfile.set_content(json.dumps({
                    "installed": True, "id": svc_name, "type": svc_type,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "service_uninstall":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc_id = body.get("service_id", "")
                if not registry.get_definition(user_id, svc_id):
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{svc_id}' not found.",
                    }).encode())
                    return [flowfile]
                registry.uninstall(user_id, svc_id)
                flowfile.set_content(json.dumps({
                    "uninstalled": True, "id": svc_id,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "service_enable":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc_id = body.get("service_id", "")
                if not registry.get_definition(user_id, svc_id):
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{svc_id}' not found.",
                    }).encode())
                    return [flowfile]
                registry.enable(user_id, svc_id)
                flowfile.set_content(json.dumps({
                    "enabled": True, "id": svc_id,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "service_disable":
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc_id = body.get("service_id", "")
                if not registry.get_definition(user_id, svc_id):
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{svc_id}' not found.",
                    }).encode())
                    return [flowfile]
                registry.disable(user_id, svc_id)
                flowfile.set_content(json.dumps({
                    "disabled": True, "id": svc_id,
                }, ensure_ascii=False).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "list_prompts":
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            prompts = rs.list_all("prompt", user_id)
            items = [
                {
                    "name": p["name"],
                    "title": p.get("title", p["name"]),
                    "category": p.get("category", ""),
                    "description": p.get("description", ""),
                    "preview": p.get("content", "")[:100],
                }
                for p in prompts
            ]
            flowfile.set_content(json.dumps({"prompts": items}, ensure_ascii=False).encode())
            return [flowfile]

        if action == "get_prompt":
            prompt_name = body.get("name", "")
            if not prompt_name:
                flowfile.set_content(json.dumps({"error": "Missing name"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            prompt_def = rs.get_any("prompt", prompt_name, user_id)
            if not prompt_def:
                flowfile.set_content(json.dumps({"error": "Prompt not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            flowfile.set_content(json.dumps({
                "name": prompt_name,
                "title": prompt_def.get("title", prompt_name),
                "content": prompt_def.get("content", ""),
                "category": prompt_def.get("category", ""),
                "description": prompt_def.get("description", ""),
            }, ensure_ascii=False).encode())
            return [flowfile]

        if action == "random_thought":
            return self._handle_random_thought(body, body.get("conversation_id", ""), user_id, flowfile)

        # ── Task management ───────────────────────────────────────────
        if action == "create_task_def":
            name = body.get("name", "").strip()
            data = body.get("data", {})
            if not name or not data.get("prompt"):
                flowfile.set_content(json.dumps(
                    {"error": "Missing name or prompt"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            data["created_by"] = uid
            try:
                rs.create("task_def", name, uid, data)
                flowfile.set_content(json.dumps(
                    {"ok": True, "name": name}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps(
                    {"error": str(e)}).encode())
            return [flowfile]

        if action == "delete_task_def":
            name = body.get("name", "").strip()
            if not name:
                flowfile.set_content(json.dumps(
                    {"error": "Missing name"}).encode())
                return [flowfile]
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()
            uid = user_id or "anonymous"
            deleted = rs.delete("task_def", name, uid)
            flowfile.set_content(json.dumps(
                {"ok": True, "deleted": deleted}).encode())
            return [flowfile]

        if action == "assign_task":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            task_desc = body.get("task", "") or body.get("task_def_name", "")
            if not conv_id or not agent or not task_desc:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id, agent_name, or task"}).encode())
                return [flowfile]
            from core.tool_registry import AssignTaskHandler
            h = AssignTaskHandler()
            h.set_conversation_id(conv_id)
            h.set_agent_name("user")
            h.set_user_id(user_id)
            result = h.execute({
                "agent": agent,
                "task": body.get("task", ""),
                "task_def_name": body.get("task_def_name", ""),
                "completion_criteria": body.get("completion_criteria", ""),
                "interval": body.get("interval"),
                "max_iterations": body.get("max_iterations", 50),
                "verifier": body.get("verifier", ""),
                "variables": body.get("variables"),
            })
            # Ensure poller is running (task needs it for scheduled wake-ups)
            poll_interval = int(self.config.get("poll_interval", 0))
            if poll_interval > 0 and not self._poller_started:
                self._poller_started = True
                poller_thread = threading.Thread(
                    target=self._poll_conversations,
                    args=(poll_interval,),
                    daemon=True,
                    name="agent-poller",
                )
                poller_thread.start()
                logger.info("Agent poller started (triggered by task assignment)")
            flowfile.set_content(json.dumps({"ok": True, "result": result}).encode())
            return [flowfile]

        if action == "task_status":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                return [flowfile]
            all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
            agent_filter = body.get("agent_name", "")
            tasks_out = []
            for tid, t in all_tasks.items():
                if not isinstance(t, dict):
                    continue
                if agent_filter and t.get("agent") != agent_filter:
                    continue
                tasks_out.append({
                    "task_id": tid, "agent": t.get("agent", ""),
                    "task": t.get("task", ""), "status": t.get("status", ""),
                    "iterations": t.get("iterations_done", 0),
                    "max_iterations": t.get("max_iterations", 50),
                    "last_result": t.get("last_result", ""),
                    "verifier": t.get("verifier", ""),
                    "interval": t.get("interval", 60),
                    "task_def_name": t.get("task_def_name", ""),
                    "created_by": t.get("created_by", ""),
                })
            # Include library definitions if requested
            defs_out = []
            if body.get("include_library"):
                from core.resource_store import ResourceStore
                uid = user_id or "anonymous"
                all_defs = ResourceStore.instance().list_all("task_def", uid)
                for d in all_defs:
                    defs_out.append({
                        "name": d.get("name", ""),
                        "prompt": d.get("prompt", ""),
                        "criteria": d.get("criteria", ""),
                        "default_interval": d.get("default_interval", "6/1m"),
                        "description": d.get("description", ""),
                        "created_by": d.get("created_by", ""),
                    })
            flowfile.set_content(json.dumps({
                "tasks": tasks_out, "definitions": defs_out,
            }).encode())
            return [flowfile]

        if action == "task_log":
            task_name = body.get("name", body.get("task_id", ""))
            conv_id = body.get("conversation_id", "")
            if not task_name:
                # Return all task logs
                extras = store.get_extras(conv_id) or {}
                all_logs = {}
                for k, v in extras.items():
                    if k.startswith("task_log:") and isinstance(v, list):
                        all_logs[k[9:]] = v  # strip "task_log:" prefix
                flowfile.set_content(json.dumps({"logs": all_logs}).encode())
            else:
                log = store.get_extra(conv_id, f"task_log:{task_name}") or []
                flowfile.set_content(json.dumps({"task": task_name, "log": log}).encode())
            return [flowfile]

        if action in ("pause_task", "resume_task", "cancel_task"):
            conv_id = body.get("conversation_id", "")
            target = body.get("task_id", "") or body.get("agent_name", "")
            if not conv_id or not target:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id or task_id/agent_name"}).encode())
                return [flowfile]
            all_tasks = store.get_extra(conv_id, "agent_tasks") or {}
            # Find tasks: by task_id or by agent_name (all tasks of that agent)
            matched = {}
            if target in all_tasks:
                matched[target] = all_tasks[target]
            else:
                for tid, t in all_tasks.items():
                    if isinstance(t, dict) and t.get("agent") == target:
                        matched[tid] = t
            if not matched:
                flowfile.set_content(json.dumps({"error": f"No task found for '{target}'"}).encode())
                return [flowfile]
            from core.poll_scheduler import PollScheduler
            scheduler = PollScheduler.instance()
            for tid, task in matched.items():
                if action == "cancel_task":
                    # Remove cancelled task from dict
                    all_tasks.pop(tid, None)
                    scheduler.cancel(f"{conv_id}::task::{tid}")
                    scheduler.cancel(f"{conv_id}::task_verify::{tid}")
                    continue  # skip the all_tasks[tid] = task below
                elif action == "pause_task":
                    task["status"] = "paused"
                    scheduler.cancel(f"{conv_id}::task::{tid}")
                elif action == "resume_task":
                    task["status"] = "active"
                    scheduler.schedule_delay(
                        conv_id, task.get("interval", 60),
                        key=f"{conv_id}::task::{tid}",
                        reason=f"[agent_task:{tid}] resumed ({task.get('agent', '?')})",
                        user_id=user_id,
                    )
                all_tasks[tid] = task
            store.set_extra(conv_id, "agent_tasks", all_tasks)
            flowfile.set_content(json.dumps({
                "ok": True, "affected": list(matched.keys()),
            }).encode())
            return [flowfile]

        # ── Image service management ──────────────────────────────────
        if action == "list_image_services":
            from services.base_image_generation import BaseImageGenerationService
            services = self._discover_media_services(user_id, BaseImageGenerationService)
            conv_id = body.get("conversation_id", "")
            prefs = {}
            if conv_id:
                prefs = store.get_extra(conv_id, "image_services") or {}
            result = [{
                "id": sid, "type": stype, "scope": scope,
                "selected_for": [
                    k for k, v in prefs.items() if v == sid
                ],
            } for sid, stype, scope in services]
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            return [flowfile]

        if action == "set_image_service":
            conv_id = body.get("conversation_id", "")
            service_name = body.get("service_name", "")
            agent = body.get("agent_name", "*")
            if not conv_id or not service_name:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id and service_name required",
                }).encode())
                return [flowfile]
            prefs = store.get_extra(conv_id, "image_services") or {}
            prefs[agent] = service_name
            store.set_extra(conv_id, "image_services", prefs)
            flowfile.set_content(json.dumps({
                "ok": True, "service": service_name, "agent": agent,
            }).encode())
            return [flowfile]

        if action == "clear_image_service":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id required",
                }).encode())
                return [flowfile]
            if agent:
                prefs = store.get_extra(conv_id, "image_services") or {}
                prefs.pop(agent, None)
                store.set_extra(conv_id, "image_services", prefs)
            else:
                store.set_extra(conv_id, "image_services", {})
            flowfile.set_content(json.dumps({"ok": True}).encode())
            return [flowfile]

        # ── Video service management ──────────────────────────────────
        if action == "list_video_services":
            from services.base_video_generation import BaseVideoGenerationService
            services = self._discover_media_services(user_id, BaseVideoGenerationService)
            conv_id = body.get("conversation_id", "")
            prefs = store.get_extra(conv_id, "video_services") or {} if conv_id else {}
            result = [{
                "id": sid, "type": stype, "scope": scope,
                "selected_for": [k for k, v in prefs.items() if v == sid],
            } for sid, stype, scope in services]
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            return [flowfile]

        if action == "set_video_service":
            conv_id = body.get("conversation_id", "")
            service_name = body.get("service_name", "")
            agent = body.get("agent_name", "*")
            if not conv_id or not service_name:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id and service_name required",
                }).encode())
                return [flowfile]
            prefs = store.get_extra(conv_id, "video_services") or {}
            prefs[agent] = service_name
            store.set_extra(conv_id, "video_services", prefs)
            flowfile.set_content(json.dumps({
                "ok": True, "service": service_name, "agent": agent,
            }).encode())
            return [flowfile]

        if action == "clear_video_service":
            conv_id = body.get("conversation_id", "")
            agent = body.get("agent_name", "")
            if not conv_id:
                flowfile.set_content(json.dumps({
                    "error": "conversation_id required",
                }).encode())
                return [flowfile]
            if agent:
                prefs = store.get_extra(conv_id, "video_services") or {}
                prefs.pop(agent, None)
                store.set_extra(conv_id, "video_services", prefs)
            else:
                store.set_extra(conv_id, "video_services", {})
            flowfile.set_content(json.dumps({"ok": True}).encode())
            return [flowfile]

        if action == "clear":
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _clear_msgs = store.load(conv_id, user_id=user_id)
            if not _clear_msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]

            def _do_clear():
                deserialized = self._deserialize_messages(_clear_msgs)
                system_msgs = [m for m in deserialized if m.role == "system"]
                serialized_ctx = self._serialize_messages(system_msgs)
                # Clear shared context
                store.save_agent_context(conv_id, "", serialized_ctx)
                # Clear all agent-specific contexts
                extras = store.get_extras(conv_id, user_id=user_id) or {}
                agent_contexts = [k for k in extras if k.startswith("agent_context:")]
                for k in agent_contexts:
                    store.set_extra(conv_id, k, None, user_id=user_id)
                return {"cleared": True, "agents_reset": len(agent_contexts) + 1}

            return self._run_bg_context_op(conv_id, "clear", _do_clear, flowfile)

        if action == "model":
            model_value = body.get("model", "").strip()
            agent_name = body.get("agent", "").strip()
            conv_id = body.get("conversation_id", "")
            override_key = f"model_override:{agent_name}"
            if not model_value or model_value == "reset":
                # Clear override
                if conv_id:
                    store.set_extra(conv_id, override_key, None, user_id=user_id)
                flowfile.set_content(json.dumps({
                    "ok": True,
                    "message": f"Model override cleared for '{agent_name}'. Using default model.",
                }).encode())
                return [flowfile]
            # Set override
            if conv_id:
                store.set_extra(conv_id, override_key, model_value, user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Model override for '{agent_name}' set to: {model_value}",
                "model": model_value,
                "agent": agent_name,
            }).encode())
            return [flowfile]

        if action == "export":
            fmt = body.get("format", "markdown")
            conv_id = body.get("conversation_id", "")
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "No conversation to export"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            msgs = store.load(conversation_id=conv_id, user_id=user_id)
            if not msgs:
                flowfile.set_content(json.dumps({"error": "Conversation not found or empty"}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]

            if fmt == "json":
                export = json.dumps([
                    {"role": m.get("role", ""), "content": m.get("content", ""),
                     "source": m.get("source", None)}
                    if isinstance(m, dict) else
                    {"role": m.role, "content": m.content,
                     "source": getattr(m, "source", None)}
                    for m in msgs
                ], indent=2, ensure_ascii=False)
                filename = f"conversation_{conv_id[:8]}.json"
            else:
                lines = [f"# Conversation {conv_id[:8]}\n"]
                for m in msgs:
                    if isinstance(m, dict):
                        role = (m.get("role") or "").upper()
                        source = m.get("source")
                        content = m.get("content", "")
                    else:
                        role = (m.role or "").upper()
                        source = getattr(m, "source", None)
                        content = m.content if isinstance(m.content, str) else str(m.content)
                    if source and isinstance(source, dict) and source.get("name"):
                        role = f"{role} ({source['name']})"
                    lines.append(f"## {role}\n\n{content}\n")
                export = "\n".join(lines)
                filename = f"conversation_{conv_id[:8]}.md"

            # Store in FileStore for download
            from core.file_store import FileStore
            mime = "application/json" if fmt == "json" else "text/markdown"
            fid = FileStore.instance().store(filename, export.encode("utf-8"), mime,
                                               user_id=user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "url": f"/files/{fid}/{filename}",
                "filename": filename,
                "format": fmt,
            }).encode())
            return [flowfile]

        # ── Filesystem explorer actions ─────────────────────────────
        if action == "fs_list_services":
            from core.tool_registry import FilesystemToolHandler
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            services = []
            # Try GlobalServiceRegistry
            try:
                from gui.services.global_service_registry import GlobalServiceRegistry
                greg = GlobalServiceRegistry.get_instance()
                for sid, sdef in greg.get_all_definitions().items():
                    if not getattr(sdef, "enabled", True):
                        continue
                    if getattr(sdef, "service_type", "") in _fsh._FS_TYPES:
                        services.append({"id": sid, "type": getattr(sdef, "service_type", ""), "scope": "global"})
            except Exception:
                pass
            # Try UserServiceRegistry
            if user_id:
                try:
                    from gui.services.user_service_registry import UserServiceRegistry
                    ureg = UserServiceRegistry.get_instance()
                    for fs_type in _fsh._FS_TYPES:
                        for sdef in ureg.get_compatible(fs_type, user_id):
                            if sdef.enabled:
                                services.append({"id": sdef.service_id, "type": fs_type, "scope": "user"})
                except Exception:
                    pass
            flowfile.set_content(json.dumps({"services": services}).encode())
            return [flowfile]

        if action == "fs_list_dir":
            from core.tool_registry import FilesystemToolHandler
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                entries = _fs_svc.list_dir(body.get("path", "."))
                result = [{"name": e.name, "kind": e.kind, "size": e.size, "modified": e.modified} for e in entries]
                flowfile.set_content(json.dumps({"entries": result}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_read_file":
            from core.tool_registry import FilesystemToolHandler
            import base64 as _b64r
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                data = _fs_svc.read_file(body.get("path", ""))
                # Try UTF-8, fallback to base64
                try:
                    text = data.decode("utf-8")
                    flowfile.set_content(json.dumps({"content": text, "encoding": "utf-8", "size": len(data)}).encode())
                except UnicodeDecodeError:
                    flowfile.set_content(json.dumps({"content": _b64r.b64encode(data).decode("ascii"), "encoding": "base64", "size": len(data)}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_write_file":
            from core.tool_registry import FilesystemToolHandler
            import base64 as _b64w
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                content = body.get("content", "")
                encoding = body.get("encoding", "utf-8")
                if encoding == "base64":
                    raw = _b64w.b64decode(content)
                else:
                    raw = content.encode("utf-8")
                _fs_svc.write_file(body.get("path", ""), raw)
                flowfile.set_content(json.dumps({"ok": True, "size": len(raw)}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_delete":
            from core.tool_registry import FilesystemToolHandler
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                _fs_svc.delete_file(body.get("path", ""))
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_mkdir":
            from core.tool_registry import FilesystemToolHandler
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                _fs_svc.mkdir(body.get("path", ""))
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_rename":
            from core.tool_registry import FilesystemToolHandler
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                old_path = body.get("old_path", "")
                new_path = body.get("new_path", "")
                if not old_path or not new_path:
                    raise ValueError("Missing old_path or new_path")
                data = _fs_svc.read_file(old_path)
                _fs_svc.write_file(new_path, data)
                _fs_svc.delete_file(old_path)
                flowfile.set_content(json.dumps({"ok": True}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_search":
            from core.tool_registry import FilesystemToolHandler
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                results = _fs_svc.search(body.get("path", "."), body.get("pattern", "*"))
                flowfile.set_content(json.dumps({"results": results[:200]}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_copy":
            from core.tool_registry import FilesystemToolHandler
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            src_svc = _fsh._find_service(body.get("source_service", ""))
            dst_svc = _fsh._find_service(body.get("dest_service", ""))
            if not src_svc or not dst_svc:
                flowfile.set_content(json.dumps({"error": "Source or dest service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                data = src_svc.read_file(body.get("source_path", ""))
                dst_svc.write_file(body.get("dest_path", ""), data)
                flowfile.set_content(json.dumps({"ok": True, "size": len(data)}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_copy_to_store":
            from core.tool_registry import FilesystemToolHandler
            import mimetypes as _mt_fcs
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                fpath = body.get("path", "")
                data = _fs_svc.read_file(fpath)
                fname = fpath.rsplit("/", 1)[-1] if "/" in fpath else fpath
                mime = _mt_fcs.guess_type(fname)[0] or "application/octet-stream"
                from core.file_store import FileStore
                fid = FileStore.instance().store(fname, data, mime, user_id=user_id)
                flowfile.set_content(json.dumps({"ok": True, "file_id": fid, "url": f"/files/{fid}/{fname}", "filename": fname, "size": len(data)}).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "fs_exec":
            from core.tool_registry import FilesystemToolHandler
            _fsh = FilesystemToolHandler()
            _fsh.set_user_id(user_id)
            _fs_svc = _fsh._find_service(body.get("service", ""))
            if not _fs_svc:
                flowfile.set_content(json.dumps({"error": "Filesystem service not found"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            try:
                result = _fs_svc.exec(".", body.get("command", ""), int(body.get("timeout", 30)))
                flowfile.set_content(json.dumps(result).encode())
            except Exception as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        if action == "theme":
            conv_id = body.get("conversation_id", "")
            operation = body.get("operation", "set")  # set, get, delete
            if not conv_id:
                flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            if operation == "get":
                css = store.get_extra(conv_id, "custom_css", user_id=user_id) or ""
                flowfile.set_content(json.dumps({"ok": True, "css": css}).encode())
                return [flowfile]
            elif operation == "delete":
                store.set_extra(conv_id, "custom_css", None, user_id=user_id)
                # Push empty CSS via SSE to clear theme live
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        conv_id, "theme", {"css": ""})
                except Exception:
                    pass
                flowfile.set_content(json.dumps({
                    "ok": True, "message": "Theme removed",
                }).encode())
                return [flowfile]
            else:  # set
                css = body.get("css", "")
                if not css:
                    flowfile.set_content(json.dumps({"error": "Missing 'css' parameter"}).encode())
                    flowfile.set_attribute("http.response.status", "400")
                    return [flowfile]
                store.set_extra(conv_id, "custom_css", css, user_id=user_id)
                # Push CSS via SSE for live update
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

        return None  # Unknown action — treat as normal message


    def _handle_telegram_conv_command(
        self, text: str, tg_user_id: str, flowfile: FlowFile,
    ) -> Optional[List[FlowFile]]:
        """Handle /conv commands from Telegram for cross-channel conversation management.

        Commands:
          /conv list       — list the user's conversations
          /conv select ID  — switch active conversation
          /conv new        — start a new conversation
          /conv info       — show current active conversation
        """
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        resolved_user = ids.resolve_user("telegram", tg_user_id)
        if not resolved_user:
            flowfile.set_content(
                "Your Telegram account is not linked to a PawFlow user.\n"
                "Use /link telegram YOUR_TG_ID from the web chat to link it."
                .encode("utf-8")
            )
            return [flowfile]

        parts = text.split(maxsplit=2)
        subcmd = parts[1] if len(parts) > 1 else "info"

        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

        if subcmd == "list":
            convs = store.list_conversations(user_id=resolved_user)
            active = ids.get_active_conv(resolved_user, "telegram") or ""
            if not convs:
                flowfile.set_content("No conversations found.".encode("utf-8"))
                return [flowfile]
            lines = []
            for c in convs[:20]:  # limit to 20
                cid = c.get("conversation_id", "")
                short_id = cid[:12]
                marker = " *" if cid == active else ""
                msg_count = c.get("message_count", 0)
                lines.append(f"{'>' if cid == active else ' '} {short_id} ({msg_count} msgs){marker}")
            header = f"Your conversations ({len(convs)}):\n"
            footer = "\n\nUse /conv select ID to switch."
            flowfile.set_content((header + "\n".join(lines) + footer).encode("utf-8"))
            return [flowfile]

        if subcmd == "select":
            conv_id_prefix = parts[2].strip() if len(parts) > 2 else ""
            if not conv_id_prefix:
                flowfile.set_content(
                    "Usage: /conv select <conversation_id>".encode("utf-8")
                )
                return [flowfile]
            # Find conversation matching prefix
            convs = store.list_conversations(user_id=resolved_user)
            match = None
            for c in convs:
                cid = c.get("conversation_id", "")
                if cid == conv_id_prefix or cid.startswith(conv_id_prefix):
                    match = cid
                    break
            if not match:
                flowfile.set_content(
                    f"Conversation '{conv_id_prefix}' not found.".encode("utf-8")
                )
                return [flowfile]
            ids.set_active_conv(resolved_user, "telegram", match)
            flowfile.set_content(
                f"Switched to conversation {match[:12]}".encode("utf-8")
            )
            return [flowfile]

        if subcmd == "new":
            new_id = store.generate_id()
            ids.set_active_conv(resolved_user, "telegram", new_id)
            flowfile.set_content(
                f"New conversation started: {new_id[:12]}".encode("utf-8")
            )
            return [flowfile]

        # /conv info (default)
        active = ids.get_active_conv(resolved_user, "telegram")
        if active:
            count = store.message_count(active)
            flowfile.set_content(
                f"Active conversation: {active[:12]} ({count} msgs)\n"
                f"User: {resolved_user}".encode("utf-8")
            )
        else:
            flowfile.set_content(
                f"No active conversation. Use /conv new or /conv select ID.\n"
                f"User: {resolved_user}".encode("utf-8")
            )
        return [flowfile]

    # ── Random Thought ────────────────────────────────────────────


    def _handle_random_thought(self, body: Dict, conv_id: str,
                               user_id: str, flowfile: FlowFile) -> List[FlowFile]:
        """Handle the ``random_thought`` action (on/off/status/now)."""
        import random as _rng
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        sub = body.get("sub", "status")
        agent_name = body.get("agent", "")
        store = ConversationStore.instance()
        # If no agent specified, use the currently selected agent for this conversation
        if not agent_name and conv_id:
            active_res = store.get_extra(conv_id, "active_resources") or {}
            agent_name = active_res.get("agent", "") or "assistant"
        agent_name = agent_name or "assistant"
        # Resolve nickname → real name (case-insensitive)
        if agent_name:
            agent_name = self._resolve_agent_name(agent_name, conv_id)
        # Normalize agent name for key consistency (case-insensitive)
        _agent_key = agent_name.lower()
        thought_key = f"{conv_id}::thought::{_agent_key}"
        extra_key = f"random_thought::{_agent_key}"
        scheduler = PollScheduler.instance()

        if not conv_id:
            flowfile.set_content(json.dumps({"error": "No conversation"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        # Resolve target agents (ALL = assistant + all ResourceStore agents)
        if agent_name.upper() == "ALL":
            from core.resource_store import ResourceStore
            all_agents = ResourceStore.instance().list_all("agent", user_id)
            target_agents = [a["name"] for a in all_agents]
        else:
            target_agents = [agent_name]

        if sub == "on":
            freq = body.get("frequency", "6/1m")
            try:
                min_iv, max_iv = self._parse_thought_frequency(freq)
            except ValueError as e:
                flowfile.set_content(json.dumps({"error": str(e)}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]

            results = []
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                _tgt_extra_key = f"random_thought::{_tgt_key}"

                scheduler.cancel(_tgt_thought_key)
                if not store.set_extra(conv_id, _tgt_extra_key, {"_probe": True}):
                    store.save(conv_id, [], user_id=user_id)
                store.set_extra(conv_id, _tgt_extra_key, {
                    "enabled": True,
                    "min_interval": min_iv,
                    "max_interval": max_iv,
                    "agent": _tgt,
                    "frequency": freq,
                })
                delay = _rng.randint(min_iv, max_iv)
                scheduler.schedule_delay(
                    conv_id, delay, key=_tgt_thought_key,
                    reason=f"[random_thought] spontaneous thought ({_tgt})",
                    user_id=user_id,
                )
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(conv_id, "thought_scheduled", {
                        "agent": _tgt, "delay": delay, "frequency": freq,
                    })
                except Exception:
                    pass
                results.append({"agent": _tgt, "delay": delay})

            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "frequency": freq,
                "next_in_seconds": results[0]["delay"] if results else 0,
                "agents": [r["agent"] for r in results],
            }).encode())
            return [flowfile]

        if sub == "off":
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_extra_key = f"random_thought::{_tgt_key}"
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                store.set_extra(conv_id, _tgt_extra_key, {"enabled": False})
                scheduler.cancel(_tgt_thought_key)
            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "disabled": True,
                "agents": target_agents,
            }).encode())
            return [flowfile]

        if sub == "now":
            for _tgt in target_agents:
                _tgt_key = _tgt.lower()
                _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
                scheduler.schedule_delay(
                    conv_id, 1, key=_tgt_thought_key,
                    reason=f"[random_thought] manual trigger ({_tgt})",
                    user_id=user_id,
                )
            store.set_status(conv_id, "active")
            flowfile.set_content(json.dumps({
                "ok": True, "agent": agent_name, "triggered": True,
                "agents": target_agents,
            }).encode())
            return [flowfile]

        # sub == "status" (default)
        import time as _t
        statuses = []
        for _tgt in target_agents:
            _tgt_key = _tgt.lower()
            _tgt_extra_key = f"random_thought::{_tgt_key}"
            _tgt_thought_key = f"{conv_id}::thought::{_tgt_key}"
            cfg = store.get_extra(conv_id, _tgt_extra_key)
            enabled = bool(cfg and cfg.get("enabled"))
            sched = scheduler.get(_tgt_thought_key)
            next_at = sched["recheck_at"] if sched else None
            next_in = int(next_at - _t.time()) if next_at else None
            statuses.append({
                "agent": _tgt, "enabled": enabled,
                "frequency": cfg.get("frequency", "") if cfg else "",
                "next_in_seconds": max(0, next_in) if next_in is not None else None,
            })

        any_enabled = any(s["enabled"] for s in statuses)
        flowfile.set_content(json.dumps({
            "enabled": any_enabled, "agent": agent_name,
            "agents": statuses,
        }).encode())
        return [flowfile]


    @staticmethod
    def _parse_thought_frequency(spec: str):
        """Parse frequency spec like '2-3/h' → (min_interval, max_interval) in seconds.

        Format: ``<count_min>[-<count_max>]/<number?><unit>``
        Units: s=1, m=60, h=3600, d=86400.

        Returns ``(min_interval_sec, max_interval_sec)`` or raises ValueError.
        """
        import re
        m = re.match(r'^(\d+)(?:-(\d+))?/(\d*)([smhd])$', spec)
        if not m:
            raise ValueError(f"Invalid frequency: {spec}")
        count_min = int(m.group(1))
        count_max = int(m.group(2) or count_min)
        if count_min <= 0 or count_max < count_min:
            raise ValueError(f"Invalid frequency counts: {spec}")
        duration_num = int(m.group(3) or 1)
        unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[m.group(4)]
        period = duration_num * unit
        # More counts → shorter intervals
        max_interval = period // count_min
        min_interval = period // count_max
        return (min_interval, max_interval)


    def _run_bg_context_op(self, conv_id: str, op_name: str, fn, flowfile):
        """Run a context operation in background with lock + SSE progress.

        Returns immediately with an ack. The background thread:
        1. Cancels the active agent
        2. Acquires the context op lock (blocks FlowFiles)
        3. Runs fn() which returns a result dict
        4. Publishes SSE done/error event
        5. Releases the lock
        """
        from core.conversation_event_bus import ConversationEventBus
        bus = ConversationEventBus.instance()

        def _bg():
            self.cancel_agent(conv_id, silent=True)
            if not self._acquire_context_op(conv_id, timeout=60.0):
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "error",
                    "error": f"Timeout waiting for active agent ({op_name})",
                })
                return
            try:
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "start", "detail": op_name,
                })
                result = fn()
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "done", **result,
                })
            except Exception as e:
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "error", "error": str(e),
                })
                logger.error("%s failed: %s", op_name, e, exc_info=True)
            finally:
                self._release_context_op(conv_id)

        thread = threading.Thread(target=_bg, daemon=True,
                                  name=f"{op_name}-{conv_id[:8]}")
        thread.start()
        flowfile.set_content(json.dumps({
            "status": "accepted", "action": op_name,
        }).encode())
        return [flowfile]


    def _get_context_op_event(self, conversation_id: str) -> threading.Event:
        """Get or create a per-conversation context-op Event (set = free)."""
        with self._context_op_lock:
            evt = self._context_op_events.get(conversation_id)
            if evt is None:
                evt = threading.Event()
                evt.set()  # initially free
                self._context_op_events[conversation_id] = evt
            return evt


    def _acquire_context_op(self, conversation_id: str, timeout: float = 30.0) -> bool:
        """Acquire exclusive context-op lock.  Returns True if acquired."""
        evt = self._get_context_op_event(conversation_id)
        # Wait for any previous op to finish
        if not evt.wait(timeout=timeout):
            return False
        evt.clear()  # mark as busy
        return True


    def _release_context_op(self, conversation_id: str):
        """Release the context-op lock, unblocking waiting FlowFiles."""
        evt = self._get_context_op_event(conversation_id)
        evt.set()


    def _is_context_op_free(self, conversation_id: str) -> bool:
        """Non-blocking check: True if no context op is running."""
        with self._context_op_lock:
            evt = self._context_op_events.get(conversation_id)
            if evt is None:
                return True
            return evt.is_set()

    # All context ops manage their own lock in background threads
    _CONTEXT_OPS = frozenset()

