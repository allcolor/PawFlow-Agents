"""AgentLoopTask mixin — action dispatcher.

Routes action requests to sub-modules in tasks/ai/actions/.
"""
import atexit
import json
import logging
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

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
from tasks.ai.actions.admin_settings import _handle_admin_settings
from tasks.ai.actions.cc_live import _handle_cc_live
from tasks.ai.actions.codex_live import _handle_codex_live
from tasks.ai.actions.gemini_live import _handle_gemini_live
from tasks.ai.actions.command_dispatch import _handle_command_dispatch
from tasks.ai.actions.pfp_ui import _handle_pfp_ui
from tasks.ai._agent_actions_conv import _AgentActionsConvMixin

logger = logging.getLogger(__name__)

_MAX_BG_ACTIONS = int(os.getenv("PAWFLOW_MAX_BG_ACTIONS", "32") or "32")
_BG_ACTION_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, _MAX_BG_ACTIONS),
    thread_name_prefix="cmd-action",
)
atexit.register(_BG_ACTION_EXECUTOR.shutdown, wait=False, cancel_futures=True)
_BG_ACTION_SUBMIT_DELAY = float(os.getenv("PAWFLOW_BG_ACTION_SUBMIT_DELAY", "1.0") or "1.0")
_BG_ACTION_QUEUE = deque()
_BG_ACTION_QUEUE_COND = threading.Condition()
_BG_ACTION_SCHEDULER_STARTED = False
_BG_ACTION_LAST_ENQUEUE = 0.0
_UI_ACTION_STATUS_LOCK = threading.Lock()
_UI_ACTION_STATUS: Dict[str, dict] = {}
_UI_ACTION_STATUS_TTL = float(os.getenv("PAWFLOW_UI_ACTION_STATUS_TTL", "600") or "600")
_UI_ACTION_STATUS_MAX = int(os.getenv("PAWFLOW_UI_ACTION_STATUS_MAX", "5000") or "5000")
_UI_LIST_CACHE_TTL = float(os.getenv("PAWFLOW_UI_LIST_CACHE_TTL", "2.0") or "2.0")
_UI_LIST_CACHE_MAX = int(os.getenv("PAWFLOW_UI_LIST_CACHE_MAX", "512") or "512")
_UI_LIST_CACHE_LOCK = threading.Lock()
_UI_LIST_CACHE: Dict[str, dict] = {}

_UI_LIST_CACHE_ACTIONS = {
    "list_services",
    "list_resources",
    "pfp_list_installed",
    "list_params_secrets",
    "list_linked_accounts",
}
_UI_LIST_CACHE_IGNORED_BODY_KEYS = {
    "_call_id",
    "_reply_conversation_id",
    "_result_action",
}


def _ui_action_status_key(reply_conversation_id: str, call_id: str) -> str:
    return f"{reply_conversation_id}\0{call_id}"


def _prune_ui_action_status(now: Optional[float] = None) -> None:
    now = now or time.time()
    expired = [
        key for key, row in _UI_ACTION_STATUS.items()
        if now - float(row.get("updated_at", 0) or 0) > _UI_ACTION_STATUS_TTL
    ]
    for key in expired:
        _UI_ACTION_STATUS.pop(key, None)
    overflow = len(_UI_ACTION_STATUS) - _UI_ACTION_STATUS_MAX
    if overflow > 0:
        for key, _row in sorted(
                _UI_ACTION_STATUS.items(), key=lambda kv: kv[1].get("updated_at", 0))[:overflow]:
            _UI_ACTION_STATUS.pop(key, None)


def _record_ui_action_pending(reply_conversation_id: str, call_id: str,
                              action: str, conversation_id: str) -> None:
    if not reply_conversation_id or not call_id:
        return
    now = time.time()
    with _UI_ACTION_STATUS_LOCK:
        _prune_ui_action_status(now)
        _UI_ACTION_STATUS[_ui_action_status_key(reply_conversation_id, call_id)] = {
            "status": "pending", "action": action,
            "conversation_id": conversation_id, "_callId": call_id,
            "reply_conversation_id": reply_conversation_id,
            "created_at": now, "updated_at": now,
        }


def _record_ui_action_done(reply_conversation_id: str, call_id: str,
                           payload: dict) -> None:
    if not reply_conversation_id or not call_id:
        return
    now = time.time()
    with _UI_ACTION_STATUS_LOCK:
        _prune_ui_action_status(now)
        row = dict(payload)
        row.update({
            "status": "done" if not payload.get("error") else "error",
            "reply_conversation_id": reply_conversation_id,
            "created_at": _UI_ACTION_STATUS.get(
                _ui_action_status_key(reply_conversation_id, call_id), {}).get("created_at", now),
            "updated_at": now,
        })
        _UI_ACTION_STATUS[_ui_action_status_key(reply_conversation_id, call_id)] = row


def _list_ui_action_status(reply_conversation_id: str, call_ids: list) -> list:
    with _UI_ACTION_STATUS_LOCK:
        _prune_ui_action_status()
        out = []
        for call_id in call_ids:
            row = _UI_ACTION_STATUS.get(_ui_action_status_key(reply_conversation_id, str(call_id)))
            if row:
                out.append(dict(row))
            else:
                out.append({"_callId": str(call_id), "status": "unknown"})
        return out


def _ui_list_cache_key(action: str, body: dict, user_id: str,
                       conversation_id: str, result_action: str) -> str:
    stable_body = {
        k: v for k, v in body.items()
        if k not in _UI_LIST_CACHE_IGNORED_BODY_KEYS
    }
    payload = {
        "action": action,
        "result_action": result_action,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "body": stable_body,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _get_ui_list_cache(action: str, body: dict, user_id: str,
                       conversation_id: str, result_action: str) -> Optional[str]:
    if action not in _UI_LIST_CACHE_ACTIONS:
        return None
    now = time.monotonic()
    key = _ui_list_cache_key(action, body, user_id, conversation_id, result_action)
    with _UI_LIST_CACHE_LOCK:
        row = _UI_LIST_CACHE.get(key)
        if not row:
            return None
        if now - float(row.get("created_at", 0) or 0) > _UI_LIST_CACHE_TTL:
            _UI_LIST_CACHE.pop(key, None)
            return None
        return str(row.get("content", ""))


def _put_ui_list_cache(action: str, body: dict, user_id: str,
                       conversation_id: str, result_action: str,
                       content: str) -> None:
    if action not in _UI_LIST_CACHE_ACTIONS:
        return
    now = time.monotonic()
    key = _ui_list_cache_key(action, body, user_id, conversation_id, result_action)
    with _UI_LIST_CACHE_LOCK:
        expired = [
            k for k, row in _UI_LIST_CACHE.items()
            if now - float(row.get("created_at", 0) or 0) > _UI_LIST_CACHE_TTL
        ]
        for k in expired:
            _UI_LIST_CACHE.pop(k, None)
        _UI_LIST_CACHE[key] = {"created_at": now, "content": content}
        overflow = len(_UI_LIST_CACHE) - _UI_LIST_CACHE_MAX
        if overflow > 0:
            for k, _row in sorted(
                    _UI_LIST_CACHE.items(), key=lambda kv: kv[1].get("created_at", 0))[:overflow]:
                _UI_LIST_CACHE.pop(k, None)


def _ensure_bg_action_scheduler() -> None:
    global _BG_ACTION_SCHEDULER_STARTED
    with _BG_ACTION_QUEUE_COND:
        if _BG_ACTION_SCHEDULER_STARTED:
            return
        _BG_ACTION_SCHEDULER_STARTED = True

    def _loop() -> None:
        while True:
            with _BG_ACTION_QUEUE_COND:
                while not _BG_ACTION_QUEUE:
                    _BG_ACTION_QUEUE_COND.wait()
                deadline = _BG_ACTION_LAST_ENQUEUE + _BG_ACTION_SUBMIT_DELAY
                wait_for = deadline - time.monotonic()
                if wait_for > 0:
                    _BG_ACTION_QUEUE_COND.wait(wait_for)
                    continue
                fn = _BG_ACTION_QUEUE.popleft()
            try:
                _BG_ACTION_EXECUTOR.submit(fn)
            except RuntimeError:
                logger.debug("action background executor unavailable", exc_info=True)

    threading.Thread(target=_loop, daemon=True, name="cmd-action-scheduler").start()


def _schedule_bg_action(fn) -> None:
    global _BG_ACTION_LAST_ENQUEUE
    _ensure_bg_action_scheduler()
    with _BG_ACTION_QUEUE_COND:
        _BG_ACTION_LAST_ENQUEUE = time.monotonic()
        _BG_ACTION_QUEUE.append(fn)
        _BG_ACTION_QUEUE_COND.notify()

_ACTION_HANDLERS = [
    # PFP UI extension handlers run first: any body carrying `_ext` is
    # routed to its installed handler before built-in dispatchers see it.
    _handle_pfp_ui,
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
    _handle_admin_settings,
    _handle_cc_live,
    _handle_codex_live,
    _handle_gemini_live,
]


class AgentActionsMixin(_AgentActionsConvMixin):
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

        # _inline_response: the caller reads results from the HTTP response
        # (PawCode, VS Code) — never route them to the conversation's SSE
        # channel, even for the early "command" dispatch below. Routing them
        # to SSE deadlocks clients whose SSE is attached to another
        # conversation (or none).
        _inline_early = bool(body.get("_inline_response"))
        reply_conversation_id = body.get("_reply_conversation_id", "") or (
            "" if _inline_early else body.get("conversation_id", ""))
        call_id = body.get("_call_id", "")

        if action == "list_ui_action_status":
            target_reply = body.get("reply_conversation_id", "") or body.get("_reply_conversation_id", "")
            call_ids = body.get("call_ids", [])
            if not isinstance(call_ids, list):
                call_ids = []
            flowfile.set_content(json.dumps({
                "actions": _list_ui_action_status(target_reply, call_ids),
            }, ensure_ascii=False).encode())
            return [flowfile]

        result_action = action

        # Unified command dispatch: parse /command text → action body → redispatch
        if action == "command":
            result = _handle_command_dispatch(self, action, body, store, user_id, flowfile)
            if result is not None:
                if isinstance(result, dict) and result.get("_redispatch"):
                    # Re-dispatch with parsed action, preserving async reply metadata.
                    # The command_result action must remain "command" because the
                    # webchat subscriber is action$('command'); publishing the parsed
                    # action would be filtered out and appear as a silent no-op.
                    body = result["body"]
                    if reply_conversation_id:
                        body["_reply_conversation_id"] = reply_conversation_id
                    if call_id:
                        body["_call_id"] = call_id
                    action = body["action"]
                    result_action = "command"
                    flowfile = result["flowfile"]
                else:
                    return self._return_action_result_async(
                        action, result, reply_conversation_id, call_id, flowfile)

        conversation_id = body.get("conversation_id", "")
        inline_response = bool(body.get("_inline_response"))
        reply_conversation_id = body.get("_reply_conversation_id", "") or (
            "" if inline_response else reply_conversation_id)
        if not inline_response:
            reply_conversation_id = reply_conversation_id or conversation_id
        call_id = body.get("_call_id", "") or call_id

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
            reply_conversation_id=reply_conversation_id, call_id=call_id,
            result_action=result_action)


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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        payload = {
            "action": action,
            "result": _content,
            "conversation_id": _call_conv,
        }
        if call_id:
            payload["_callId"] = call_id
        _record_ui_action_done(reply_conversation_id, call_id, payload)
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
                       reply_conversation_id: str = "", call_id: str = "",
                       result_action: str = ""):
        """Run an action in background. Return ack immediately, result via SSE."""
        result_action = result_action or action
        _record_ui_action_pending(
            reply_conversation_id, call_id, result_action, conversation_id)
        cached_content = _get_ui_list_cache(
            action, body, user_id, conversation_id, result_action)
        if cached_content is not None:
            from core.conversation_event_bus import ConversationEventBus
            payload = {
                "action": result_action, "result": cached_content,
                "conversation_id": conversation_id,
            }
            if call_id:
                payload["_callId"] = call_id
            _record_ui_action_done(reply_conversation_id, call_id, payload)
            ConversationEventBus.instance().publish_event(
                reply_conversation_id, "command_result", payload)
            from tasks.ai.agent_streaming import SERVER_START_TIME
            flowfile.set_content(json.dumps({
                "status": "accepted", "action": action,
                "_callId": call_id,
                "server_start_time": SERVER_START_TIME,
            }).encode())
            return [flowfile]

        import copy
        _body = copy.deepcopy(body)
        _body["_result_action"] = result_action
        # Clone flowfile for bg thread — main thread will overwrite the original with ack
        from core import FlowFile as _FF
        _bg_ff = _FF(content=flowfile.get_content(), attributes=dict(flowfile.attributes))

        def _publish_error(message: str):
            try:
                from core.conversation_event_bus import ConversationEventBus
                payload = {
                    "action": result_action, "error": message,
                    "conversation_id": conversation_id,
                }
                if call_id:
                    payload["_callId"] = call_id
                _record_ui_action_done(reply_conversation_id, call_id, payload)
                ConversationEventBus.instance().publish_event(
                    reply_conversation_id, "command_result", payload)
            except Exception:
                logger.debug("failed to publish action error", exc_info=True)

        def _bg():
            try:
                for handler in _ACTION_HANDLERS:
                    result = handler(self, action, _body, store, user_id, _bg_ff)
                    if result is not None:
                        _content = ""
                        if isinstance(result, list) and result:
                            if result[0].get_attribute("suppress_command_result") == "1":
                                return
                            _content = result[0].get_content().decode("utf-8", errors="replace")
                        _put_ui_list_cache(
                            action, _body, user_id, conversation_id,
                            result_action, _content)
                        from core.conversation_event_bus import ConversationEventBus
                        # The "conversation_id" field is the *call's* scope so
                        # the rxbus filter can route the result back to the
                        # call site that issued it. The reply_conversation_id
                        # (UI bus) is only the SSE channel we deliver on.
                        payload = {
                            "action": result_action, "result": _content,
                            "conversation_id": conversation_id,
                        }
                        if call_id:
                            payload["_callId"] = call_id
                        _record_ui_action_done(reply_conversation_id, call_id, payload)
                        ConversationEventBus.instance().publish_event(
                            reply_conversation_id, "command_result", payload)
                        return
                _publish_error(f"Unhandled UI action: {action}")
            except Exception as e:
                logger.error("[bg-cmd] %s failed: %s", action, e, exc_info=True)
                _publish_error(str(e))

        from tasks.ai.agent_streaming import SERVER_START_TIME
        flowfile.set_content(json.dumps({
            "status": "accepted", "action": action,
            "_callId": call_id,
            "server_start_time": SERVER_START_TIME,
        }).encode())
        # Defer submitting the real handler until after the HTTP ACK has had a
        # chance to leave the request thread. A single scheduler drains bursty
        # UI refreshes without creating a timer thread for each request.
        _schedule_bg_action(_bg)
        return [flowfile]


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
        try:
            _body = json.loads(flowfile.get_content().decode("utf-8", errors="replace"))
            if not isinstance(_body, dict):
                _body = {}
        except Exception:
            _body = {}
        reply_conversation_id = _body.get("_reply_conversation_id", "") or ""
        call_id = _body.get("_call_id", "") or ""
        result_action = _body.get("_result_action", "") or op_name

        def _publish_command_result(payload: dict):
            if not reply_conversation_id:
                return
            try:
                data = {
                    "action": result_action,
                    "result": json.dumps(payload, ensure_ascii=False),
                    "conversation_id": conv_id,
                }
                if call_id:
                    data["_callId"] = call_id
                _record_ui_action_done(reply_conversation_id, call_id, data)
                bus.publish_event(reply_conversation_id, "command_result", data)
            except Exception:
                logger.debug("context op command_result publish failed", exc_info=True)

        def _matching_active_contexts(target_agent: str = ""):
            target = "" if target_agent in ("", "shared", "ALL") else target_agent

            def _agent_from_key(key: str) -> str | None:
                if key == conv_id:
                    return ""
                prefix = conv_id + ":"
                if key.startswith(prefix):
                    return key[len(prefix):]
                return None

            with self._active_contexts_lock:
                active_items = list(self._active_contexts.items())
            for key, active_ctx in active_items:
                active_agent = _agent_from_key(key)
                if active_agent is None:
                    continue
                if target and active_agent != target:
                    continue
                yield active_agent, active_ctx

        def _set_context_usage_suspended(target_agent: str, suspended: bool):
            for _, active_ctx in _matching_active_contexts(target_agent):
                active_ctx["_context_usage_suspended"] = bool(suspended)

        def _refresh_active_context_from_store(target_agent: str = ""):
            """Replace active in-memory messages with the compacted store view."""
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()

                def _load_context_for(agent: str):
                    if agent:
                        data = store.load_agent_context(conv_id, agent)
                        if data is not None:
                            return data
                    data = store.load_context(conv_id)
                    if data is not None:
                        return data
                    return store.load(conv_id)

                for active_agent, active_ctx in _matching_active_contexts(target_agent):
                    raw = _load_context_for(active_agent)
                    if not raw:
                        continue
                    refreshed = self._deserialize_messages(
                        raw, conversation_id=conv_id)
                    active_msgs = active_ctx.get("messages")
                    if isinstance(active_msgs, list):
                        active_msgs[:] = refreshed
                        active_ctx.pop("_context_usage_cache", None)
                        active_ctx.pop("_auto_compact_usage_cache", None)
                        active_ctx["_context_usage_suspended"] = False
            except Exception:
                logger.debug(
                    "active context refresh after compact failed",
                    exc_info=True)

        def _bg():
            _resume_after_compact = False
            _resume_agent = agent_name or ""
            _resume_user_id = flowfile.get_attribute("http.auth.principal") or ""
            if op_name == "compact":
                try:
                    if agent_name:
                        _resume_after_compact = self.is_agent_active(
                            conv_id, agent_name)
                    else:
                        _resume_after_compact = self.is_conversation_active(conv_id)
                        if not _resume_agent:
                            from core.conversation_store import ConversationStore
                            _ares = ConversationStore.instance().get_extra(
                                conv_id, "active_resources") or {}
                            _resume_agent = _ares.get("agent", "") or ""
                    if not _resume_user_id:
                        from core.conversation_store import ConversationStore
                        _resume_user_id = ConversationStore.instance().get_user_id(
                            conv_id) or ""
                except Exception:
                    logger.debug("compact resume detection failed", exc_info=True)
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
                if op_name == "compact":
                    _set_context_usage_suspended(agent_name, True)
                result = fn()
                _agent = result.get("agent", "") or agent_name
                if op_name == "compact" and _agent and _agent != "shared":
                    _resume_agent = _agent
                if result.get("context_changed", True):
                    if _agent and _agent != "shared":
                        self._clear_claude_session(conv_id, _agent)
                    else:
                        # Shared context changed — clear all agent sessions
                        self._clear_claude_session(conv_id, "")
                if op_name == "compact":
                    _refresh_active_context_from_store(_agent)
                    _set_context_usage_suspended(_agent, False)
                    if _agent and _agent != "shared":
                        try:
                            from core.conversation_store import ConversationStore
                            from tasks.ai.context_usage import (
                                compute_context_usage, persist_context_usage,
                                usage_event_payload)
                            _store = ConversationStore.instance()
                            _usage = compute_context_usage(
                                conv_id, _agent, store=_store,
                                source="compact_done")
                            persist_context_usage(
                                conv_id, _agent, _usage, store=_store)
                            bus.publish_event(
                                conv_id, "message_meta",
                                usage_event_payload(_usage))
                        except Exception:
                            logger.debug(
                                "compact context gauge publish failed",
                                exc_info=True)
                else:
                    bus.publish_event(conv_id, "compact_progress", {
                        "stage": "done", **result,
                    })
                _publish_command_result(result)
            except Exception as e:
                bus.publish_event(conv_id, "compact_progress", {
                    "stage": "error", "error": str(e),
                })
                _publish_command_result({"error": str(e), "operation": op_name})
                logger.error("%s failed: %s", op_name, e, exc_info=True)
            finally:
                if op_name == "compact":
                    _set_context_usage_suspended(agent_name, False)
                self._release_context_op(conv_id, agent_name)
                if (op_name == "compact" and _resume_after_compact
                        and _resume_agent and _resume_agent != "shared"):
                    try:
                        from tasks.ai.agent_loop import AgentLoopTask
                        AgentLoopTask.wake_agent(
                            conv_id, _resume_agent,
                            reason=f"[compact_resume:{_resume_agent}] compact completed; resume immediately",
                            user_id=_resume_user_id,
                            delay=0.0,
                            even_if_active=True,
                        )
                    except Exception:
                        logger.debug("compact resume wake failed", exc_info=True)

        thread = threading.Thread(
            target=_bg, daemon=True,
            name=f"{op_name}-{conv_id[:8]}-{agent_name or 'shared'}")
        thread.start()
        flowfile.set_content(json.dumps({
            "status": "accepted", "action": op_name,
        }).encode())
        flowfile.set_attribute("suppress_command_result", "1")
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

