"""AgentLoopTask mixin — action dispatcher.

Routes action requests to sub-modules in tasks/ai/actions/.
"""
import json
import logging
import os
import threading
import time
from typing import Dict, Any, List, Optional

from core import FlowFile

from tasks.ai.actions.conversation import _handle_conversation
from tasks.ai.actions.cancel_interrupt import _handle_cancel_interrupt
from tasks.ai.actions.context_ops import _handle_context_ops
from tasks.ai.actions.agent_resource import _handle_agent_resource
from tasks.ai.actions.service_flow import _handle_service_flow
from tasks.ai.actions.secrets_variables import _handle_secrets_variables
from tasks.ai.actions.scheduling import _handle_scheduling
from tasks.ai.actions.tools_exec import _handle_tools_exec
from tasks.ai.actions.media import _handle_media
from tasks.ai.actions.files_fs import _handle_files_fs
from tasks.ai.actions.misc import _handle_misc
from tasks.ai.actions.account_linking import _handle_account_linking
from tasks.ai.actions.memory_prompts import _handle_memory_prompts
from tasks.ai.actions.cognitive_ui import _handle_cognitive_ui
from tasks.ai.actions.usage import _handle_usage
from tasks.ai.actions.plans import _handle_plans
from tasks.ai.actions.cc_live import _handle_cc_live
from tasks.ai.actions.codex_live import _handle_codex_live
from tasks.ai.actions.gemini_live import _handle_gemini_live
from tasks.ai.actions.command_dispatch import _handle_command_dispatch

logger = logging.getLogger(__name__)

_MAX_BG_ACTIONS = int(os.getenv("PAWFLOW_MAX_BG_ACTIONS", "32") or "32")
_BG_ACTION_SEMAPHORE = threading.BoundedSemaphore(max(1, _MAX_BG_ACTIONS))


_ACTION_HANDLERS = [
    _handle_conversation,
    _handle_cancel_interrupt,
    _handle_context_ops,
    _handle_agent_resource,
    _handle_service_flow,
    _handle_secrets_variables,
    _handle_scheduling,
    _handle_tools_exec,
    _handle_media,
    _handle_files_fs,
    _handle_misc,
    _handle_account_linking,
    _handle_memory_prompts,
    _handle_cognitive_ui,
    _handle_usage,
    _handle_plans,
    _handle_cc_live,
    _handle_codex_live,
    _handle_gemini_live,
]


class AgentActionsMixin:
    """Action request dispatcher — routes to sub-modules."""

    def _handle_action(self, flowfile: FlowFile) -> Optional[List[FlowFile]]:
        """Handle action-based requests. Returns None if not an action."""
        raw_body = flowfile.get_content().decode("utf-8", errors="replace")

        # Handle Telegram /conv commands
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

        reply_conversation_id = body.get("_reply_conversation_id", "") or body.get("conversation_id", "")
        call_id = body.get("_call_id", "")

        # Unified command dispatch: parse /command text → action body → redispatch
        if action == "command":
            result = _handle_command_dispatch(self, action, body, store, user_id, flowfile)
            if result is not None:
                if isinstance(result, dict) and result.get("_redispatch"):
                    # Re-dispatch with parsed action, preserving async reply metadata.
                    body = result["body"]
                    if reply_conversation_id:
                        body["_reply_conversation_id"] = reply_conversation_id
                    if call_id:
                        body["_call_id"] = call_id
                    action = body["action"]
                    flowfile = result["flowfile"]
                else:
                    return self._return_action_result_async(
                        action, result, reply_conversation_id, call_id, flowfile)

        conversation_id = body.get("conversation_id", "")
        reply_conversation_id = body.get("_reply_conversation_id", "") or reply_conversation_id or conversation_id
        call_id = body.get("_call_id", "") or call_id

        if action == "load_history":
            for handler in _ACTION_HANDLERS:
                result = handler(self, action, body, store, user_id, flowfile)
                if result is not None:
                    return result
            return None

        # No reply bus available → run synchronously and return the payload
        # in the HTTP response. System clients (relay, CLI, registration
        # bootstraps) call /api/ui without a chat conversation context, so
        # there is no SSE channel to publish a command_result on.
        if not reply_conversation_id:
            for handler in _ACTION_HANDLERS:
                result = handler(self, action, body, store, user_id, flowfile)
                if result is not None:
                    return result
            return None

        return self._run_action_bg(
            action, body, store, user_id, flowfile, conversation_id,
            reply_conversation_id=reply_conversation_id, call_id=call_id)


    def _return_action_result_async(self, action, result, reply_conversation_id,
                                    call_id, flowfile):
        """Publish an already-computed command result via SSE and return ACK.

        When the caller did not provide a reply bus (system clients without
        a chat conversation context), the result is returned inline in the
        HTTP response instead.
        """
        if not reply_conversation_id:
            if isinstance(result, list):
                return result
            if isinstance(result, FlowFile):
                return [result]
            flowfile.set_content(json.dumps(result, ensure_ascii=False).encode())
            return [flowfile]
        _content = ""
        if isinstance(result, list) and result:
            _content = result[0].get_content().decode("utf-8", errors="replace")
        elif isinstance(result, FlowFile):
            _content = result.get_content().decode("utf-8", errors="replace")
        else:
            _content = json.dumps(result, ensure_ascii=False)
        from core.conversation_event_bus import ConversationEventBus
        # See _run_action_bg: the result's conversation_id is the call's
        # scope (so the rxbus per-call filter routes it back to the right
        # subscriber); reply_conversation_id is only the SSE channel.
        _call_conv = flowfile.get_attribute("conversation_id") or ""
        try:
            _body = json.loads(flowfile.get_content().decode("utf-8", errors="replace"))
            if isinstance(_body, dict):
                _call_conv = _body.get("conversation_id", "") or _call_conv
        except Exception:
            pass
        payload = {
            "action": action,
            "result": _content,
            "conversation_id": _call_conv,
        }
        if call_id:
            payload["_callId"] = call_id
        ConversationEventBus.instance().publish_event(
            reply_conversation_id, "command_result", payload)
        from tasks.ai.agent_streaming import SERVER_START_TIME
        flowfile.set_content(json.dumps({
            "status": "accepted", "action": action,
            "_callId": call_id,
            "server_start_time": SERVER_START_TIME,
        }).encode())
        return [flowfile]


    def _run_action_bg(self, action, body, store, user_id, flowfile, conversation_id,
                       reply_conversation_id: str = "", call_id: str = ""):
        """Run an action in background. Return ack immediately, result via SSE."""
        import copy
        _body = copy.deepcopy(body)
        # Clone flowfile for bg thread — main thread will overwrite the original with ack
        from core import FlowFile as _FF
        _bg_ff = _FF(content=flowfile.get_content(), attributes=dict(flowfile.attributes))

        def _publish_error(message: str):
            try:
                from core.conversation_event_bus import ConversationEventBus
                payload = {
                    "action": action, "error": message,
                    "conversation_id": conversation_id,
                }
                if call_id:
                    payload["_callId"] = call_id
                ConversationEventBus.instance().publish_event(
                    reply_conversation_id, "command_result", payload)
            except Exception:
                logger.debug("failed to publish action error", exc_info=True)

        if not _BG_ACTION_SEMAPHORE.acquire(blocking=False):
            logger.warning("[bg-cmd] dropping %s: too many background actions", action)
            _publish_error("Too many UI actions are already running")
            from tasks.ai.agent_streaming import SERVER_START_TIME
            flowfile.set_content(json.dumps({
                "status": "accepted", "action": action,
                "_callId": call_id,
                "server_start_time": SERVER_START_TIME,
            }).encode())
            return [flowfile]

        def _bg():
            try:
                for handler in _ACTION_HANDLERS:
                    result = handler(self, action, _body, store, user_id, _bg_ff)
                    if result is not None:
                        _content = ""
                        if isinstance(result, list) and result:
                            _content = result[0].get_content().decode("utf-8", errors="replace")
                        from core.conversation_event_bus import ConversationEventBus
                        # The "conversation_id" field is the *call's* scope so
                        # the rxbus filter can route the result back to the
                        # call site that issued it. The reply_conversation_id
                        # (UI bus) is only the SSE channel we deliver on.
                        payload = {
                            "action": action, "result": _content,
                            "conversation_id": conversation_id,
                        }
                        if call_id:
                            payload["_callId"] = call_id
                        ConversationEventBus.instance().publish_event(
                            reply_conversation_id, "command_result", payload)
                        return
            except Exception as e:
                logger.error("[bg-cmd] %s failed: %s", action, e, exc_info=True)
                _publish_error(str(e))
            finally:
                _BG_ACTION_SEMAPHORE.release()

        threading.Thread(target=_bg, daemon=True,
                         name=f"cmd-{action}-{(conversation_id or reply_conversation_id)[:8]}").start()
        from tasks.ai.agent_streaming import SERVER_START_TIME
        flowfile.set_content(json.dumps({
            "status": "accepted", "action": action,
            "_callId": call_id,
            "server_start_time": SERVER_START_TIME,
        }).encode())
        return [flowfile]


    def _handle_telegram_conv_command(
        self, text: str, tg_user_id: str, flowfile: FlowFile,
    ) -> Optional[List[FlowFile]]:
        """Handle /conv commands from Telegram for cross-channel conversation management.

        Commands:
          /conv list       â€” list the user's conversations
          /conv select ID  â€” switch active conversation
          /conv new        â€” start a new conversation
          /conv info       â€” show current active conversation
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

    # â”€â”€ Random Thought â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


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
            agent_name = active_res.get("agent", "")
        if not agent_name:
            raise RuntimeError("No agent resolved for this conversation. Add an agent first.")
        # Resolve nickname â†’ real name (case-insensitive)
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
        """Parse frequency spec like '2-3/h' â†’ (min_interval, max_interval) in seconds.

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
        # More counts â†’ shorter intervals
        max_interval = period // count_min
        min_interval = period // count_max
        return (min_interval, max_interval)


    @staticmethod
    def _clear_claude_session(conv_id: str, agent_name: str):
        """Clear Claude Code session_id + purge stale jsonl on disk.

        Called after rebuild/compact/summary/restart — the context changed,
        so Claude Code must start a new session with the updated context
        and the old session jsonl must not survive (orphan workers may
        still be writing to it).

        Delegates to `ConversationStore.invalidate_claude_session_for_agent`
        which knows the real on-disk layout:
            CLAUDE_SESSIONS_DIR/<user>/<conv>/claude/projects/-workspace/<sid>.jsonl
        plus the companion `<sid>/` directory.
        """
        if not conv_id:
            return
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            if agent_name:
                store.invalidate_claude_session_for_agent(conv_id, agent_name)
            else:
                # Shared context changed — invalidate every agent's CC session
                # in this conversation (clears extras + purges jsonls on disk).
                store.invalidate_claude_sessions(conv_id)
        except Exception as _e:
            logger.warning("_clear_claude_session failed for %s/%s: %s",
                           conv_id[:8], agent_name, _e)

    def _run_bg_context_op(self, conv_id: str, op_name: str, fn, flowfile,
                            agent_name: str = ""):
        """Run a context operation in background with lock + SSE progress.

        Lock scope:
          - agent_name="" → whole-conv lock (blocks every agent in conv).
            Used by manual /compact without agent target, /clear, and
            other ops that touch shared state (shared.jsonl, extras).
          - agent_name="X" → agent-scoped lock (blocks only agent X in
            this conv). Other agents in the same conv continue to
            respond freely.

        Returns immediately with an ack. The background thread:
        1. Cancels the specific agent (or all agents if whole-conv)
        2. Acquires the context op lock (scoped)
        3. Runs fn()
        4. Publishes SSE done/error
        5. Releases the lock
        """
        from core.conversation_event_bus import ConversationEventBus
        bus = ConversationEventBus.instance()

        def _bg():
            self.cancel_agent(conv_id, agent_name=agent_name, silent=True)
            if not self._acquire_context_op(conv_id, agent_name,
                                             timeout=60.0):
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "error",
                    "error": f"Timeout waiting for active agent ({op_name})",
                })
                return
            try:
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "start", "detail": op_name,
                    "agent": agent_name or "",
                })
                result = fn()
                _agent = result.get("agent", "") or agent_name
                if _agent and _agent != "shared":
                    self._clear_claude_session(conv_id, _agent)
                else:
                    # Shared context changed — clear all agent sessions
                    self._clear_claude_session(conv_id, "")
                if op_name != "compact":
                    bus.publish_event(conv_id, "compact_progress", {
                        "stage": "done", **result,
                    })
            except Exception as e:
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "error", "error": str(e),
                })
                logger.error("%s failed: %s", op_name, e, exc_info=True)
            finally:
                self._release_context_op(conv_id, agent_name)

        thread = threading.Thread(
            target=_bg, daemon=True,
            name=f"{op_name}-{conv_id[:8]}-{agent_name or 'shared'}")
        thread.start()
        flowfile.set_content(json.dumps({
            "status": "accepted", "action": op_name,
        }).encode())
        return [flowfile]

    # ═════════════════════════════════════════════════════════════════
    #  Context-op lock — per (conv, agent)
    # ═════════════════════════════════════════════════════════════════
    # Keyed by (conversation_id, agent_name). agent_name="" represents
    # the "whole conv" sentinel: when held, it blocks EVERY agent of
    # that conv. Agent-specific locks (agent_name != "") block only
    # that agent; other agents on the same conv continue.
    #
    # _is_context_op_free(conv, agent):
    #   - False if (conv, "") is held — whole-conv op in progress.
    #   - False if (conv, agent) is held and agent != "".
    #   - True otherwise.

    def _get_context_op_event(self, conversation_id: str,
                                agent_name: str = "") -> threading.Event:
        """Get or create the context-op Event for (conv, agent)."""
        key = (conversation_id, agent_name or "")
        with self._context_op_lock:
            evt = self._context_op_events.get(key)
            if evt is None:
                evt = threading.Event()
                evt.set()  # initially free
                self._context_op_events[key] = evt
            return evt

    def _acquire_context_op(self, conversation_id: str,
                              agent_name: str = "",
                              timeout: float = 30.0) -> bool:
        """Acquire exclusive context-op lock for (conv, agent).
        Returns True if acquired."""
        evt = self._get_context_op_event(conversation_id, agent_name)
        if not evt.wait(timeout=timeout):
            return False
        evt.clear()
        return True

    def _release_context_op(self, conversation_id: str,
                              agent_name: str = ""):
        """Release the context-op lock for (conv, agent)."""
        evt = self._get_context_op_event(conversation_id, agent_name)
        evt.set()

    def _is_context_op_free(self, conversation_id: str,
                              agent_name: str = "") -> bool:
        """True if neither the agent-specific lock NOR the whole-conv
        sentinel is held. Callers pass the agent_name this FlowFile
        targets; empty agent_name checks only the sentinel."""
        with self._context_op_lock:
            # Whole-conv sentinel blocks everyone
            sentinel = self._context_op_events.get((conversation_id, ""))
            if sentinel is not None and not sentinel.is_set():
                return False
            # Agent-specific lock blocks only that agent
            if agent_name:
                evt = self._context_op_events.get(
                    (conversation_id, agent_name))
                if evt is not None and not evt.is_set():
                    return False
            return True

    # All context ops manage their own lock in background threads
    _CONTEXT_OPS = frozenset()

