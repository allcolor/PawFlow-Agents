"""Agent runtime hooks for tool, message, thinking, and compact events.

Hooks are repository resources of type ``agent_hook``. A conversation does not
store hook code directly; it stores bindings in ``conversation_hooks`` that
select installed hooks by name/scope and optionally narrow them by event, agent,
or tool.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

EVENT_PRE_TOOL_CALL = "pre_tool_call"
EVENT_POST_TOOL_CALL = "post_tool_call"
EVENT_PRE_USER_MESSAGE = "pre_user_message"
EVENT_POST_LLM_MESSAGE = "post_llm_message"
EVENT_POST_LLM_THINKING = "post_llm_thinking"
EVENT_PRE_COMPACT = "pre_compact"
EVENT_POST_COMPACT = "post_compact"

VALID_AGENT_HOOK_EVENTS = frozenset({
    EVENT_PRE_TOOL_CALL,
    EVENT_POST_TOOL_CALL,
    EVENT_PRE_USER_MESSAGE,
    EVENT_POST_LLM_MESSAGE,
    EVENT_POST_LLM_THINKING,
    EVENT_PRE_COMPACT,
    EVENT_POST_COMPACT,
})

_tls = threading.local()


class AgentHookError(RuntimeError):
    """Raised when an agent hook returns an invalid control decision."""


class AgentHookRunner:
    """Resolve and execute installed agent hooks for a runtime event."""

    def __init__(self, *, user_id: str = "", conversation_id: str = "",
                 agent_name: str = "", provider: str = "", model: str = "",
                 agent_service: str = "", turn_id: str = "",
                 iteration: int = 0):
        self.user_id = user_id or ""
        self.conversation_id = conversation_id or ""
        self.agent_name = agent_name or ""
        self.provider = provider or ""
        self.model = model or ""
        self.agent_service = agent_service or ""
        self.turn_id = turn_id or ""
        self.iteration = int(iteration or 0)

    @classmethod
    def is_running_hook(cls) -> bool:
        return int(getattr(_tls, "depth", 0) or 0) > 0

    def run(self, event: str, payload: Dict[str, Any], *,
            fail_policy: str = "open") -> Dict[str, Any]:
        event = str(event or "").strip()
        if event not in VALID_AGENT_HOOK_EVENTS:
            raise AgentHookError(f"unsupported hook event: {event}")
        if not self.conversation_id or self.is_running_hook():
            return _allow_result(payload)

        hooks = self._resolve_hooks(event, payload)
        if not hooks:
            return _allow_result(payload)

        current_payload = dict(payload or {})
        final: Dict[str, Any] = _allow_result(current_payload)
        for item in hooks:
            hook = item["hook"]
            binding = item["binding"]
            hook_id = str(binding.get("id") or hook.get("name") or "hook")
            envelope = self._envelope(event, hook_id, current_payload, hook, binding)
            started = time.monotonic()
            try:
                result = self._invoke(hook, envelope)
                result = _normalize_result(result, current_payload)
            except Exception as exc:
                duration = (time.monotonic() - started) * 1000.0
                logger.warning(
                    "[agent-hook] %s failed event=%s conv=%s agent=%s %.1fms: %s",
                    hook_id, event, self.conversation_id[:8], self.agent_name,
                    duration, exc, exc_info=True)
                if _fail_closed(binding, hook, fail_policy):
                    return {
                        "decision": "block",
                        "reason": f"hook {hook_id} failed: {exc}",
                        "payload": current_payload,
                        "metadata": {"hook_error": str(exc), "hook_id": hook_id},
                    }
                continue

            duration = (time.monotonic() - started) * 1000.0
            logger.info(
                "[agent-hook] %s event=%s decision=%s conv=%s agent=%s %.1fms",
                hook_id, event, result.get("decision"), self.conversation_id[:8],
                self.agent_name, duration)
            decision = result.get("decision")
            final = result
            if decision == "replace":
                next_payload = result.get("payload")
                if isinstance(next_payload, dict):
                    current_payload = next_payload
            elif decision == "block":
                return result
        final.setdefault("payload", current_payload)
        return final

    def _resolve_hooks(self, event: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            from core.conversation_store import ConversationStore
            raw = ConversationStore.instance().get_extra(
                self.conversation_id, "conversation_hooks", user_id=self.user_id) or []
        except Exception:
            logger.debug("conversation_hooks load failed", exc_info=True)
            return []
        bindings = _normalize_bindings(raw)
        if not bindings:
            return []
        try:
            from core.resource_store import ResourceStore
            store = ResourceStore.instance()
        except Exception:
            logger.debug("agent_hook ResourceStore unavailable", exc_info=True)
            return []
        selected: List[Dict[str, Any]] = []
        for binding in bindings:
            if not binding.get("enabled", True):
                continue
            if not _event_matches(binding.get("events"), event):
                continue
            if not _agent_matches(binding.get("agents"), self.agent_name):
                continue
            if not _tool_matches(binding.get("tools"), payload):
                continue
            name = str(binding.get("name") or binding.get("ref") or "").strip()
            if not name:
                continue
            hook = store.get_any(
                "agent_hook", name, self.user_id,
                conversation_id=self.conversation_id) or None
            if not hook:
                logger.warning("[agent-hook] binding references missing hook: %s", name)
                continue
            if not _event_matches(hook.get("events"), event):
                continue
            if not _tool_matches(hook.get("tools"), payload):
                continue
            selected.append({"binding": binding, "hook": hook})
        selected.sort(key=lambda item: int(item["binding"].get("priority", 0) or 0))
        return selected

    def _envelope(self, event: str, hook_id: str, payload: Dict[str, Any],
                  hook: Dict[str, Any], binding: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event": event,
            "hook_id": hook_id,
            "timestamp": time.time(),
            "event_id": uuid.uuid4().hex,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "agent_name": self.agent_name,
            "agent_service": self.agent_service,
            "provider": self.provider,
            "model": self.model,
            "turn_id": self.turn_id,
            "iteration": self.iteration,
            "payload": payload or {},
            "hook": {
                "name": hook.get("name", ""),
                "scope": hook.get("_scope", ""),
                "description": hook.get("description", ""),
            },
            "binding": {
                "id": binding.get("id", ""),
                "priority": binding.get("priority", 0),
            },
        }

    def _invoke(self, hook: Dict[str, Any], envelope: Dict[str, Any]) -> Dict[str, Any]:
        depth = int(getattr(_tls, "depth", 0) or 0)
        _tls.depth = depth + 1
        try:
            runtime = hook.get("package_runtime") or {}
            if runtime:
                from core import pfp_runtime
                return pfp_runtime.invoke_agent_hook(
                    runtime, hook.get("installed_from", {}) or {}, envelope, {
                        "user_id": self.user_id,
                        "conversation_id": self.conversation_id,
                        "agent_name": self.agent_name,
                        "scope": "conversation" if self.conversation_id else "user",
                    })
            return _invoke_source_hook(hook, envelope)
        finally:
            _tls.depth = depth


def run_agent_hooks(event: str, payload: Dict[str, Any], **context: Any) -> Dict[str, Any]:
    return AgentHookRunner(**context).run(event, payload)


def _allow_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"decision": "allow", "payload": payload or {}, "metadata": {}}


def _normalize_bindings(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        if isinstance(raw.get("hooks"), list):
            raw = raw.get("hooks")
        else:
            raw = list(raw.values())
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"name": item, "enabled": True})
        elif isinstance(item, dict):
            out.append(dict(item))
    return out


def _event_matches(events: Any, event: str) -> bool:
    if not events:
        return True
    if isinstance(events, str):
        events = [events]
    return event in set(str(e) for e in events or [])


def _agent_matches(agents: Any, agent_name: str) -> bool:
    if not agents:
        return True
    if isinstance(agents, str):
        agents = [agents]
    allowed = {str(a) for a in agents or [] if str(a)}
    return "*" in allowed or (agent_name or "") in allowed


def _tool_matches(tools: Any, payload: Dict[str, Any]) -> bool:
    if not tools:
        return True
    if isinstance(tools, str):
        tools = [tools]
    allowed = {str(t) for t in tools or [] if str(t)}
    tool_name = str((payload or {}).get("tool_name") or "")
    return "*" in allowed or tool_name in allowed


def _fail_closed(binding: Dict[str, Any], hook: Dict[str, Any], default: str) -> bool:
    policy = str(binding.get("fail_policy") or hook.get("fail_policy") or default or "open")
    return policy in {"closed", "fail_closed", "block"}


def _normalize_result(result: Any, current_payload: Dict[str, Any]) -> Dict[str, Any]:
    if result is None:
        return _allow_result(current_payload)
    if isinstance(result, str):
        result = result.strip()
        if not result:
            return _allow_result(current_payload)
        try:
            result = json.loads(result)
        except Exception as exc:
            raise AgentHookError("hook returned non-JSON text") from exc
    if not isinstance(result, dict):
        raise AgentHookError("hook result must be an object")
    decision = str(result.get("decision") or "allow")
    if decision not in {"allow", "block", "replace"}:
        raise AgentHookError(f"invalid hook decision: {decision}")
    payload = result.get("payload", current_payload)
    if payload is None:
        payload = current_payload
    if not isinstance(payload, dict):
        raise AgentHookError("hook payload must be an object")
    return {
        "decision": decision,
        "reason": str(result.get("reason") or ""),
        "payload": payload,
        "metadata": result.get("metadata") if isinstance(result.get("metadata"), dict) else {},
    }


def _invoke_source_hook(hook: Dict[str, Any], envelope: Dict[str, Any]) -> Dict[str, Any]:
    source = str(hook.get("source") or hook.get("code") or "")
    if not source.strip():
        return _allow_result(envelope.get("payload") or {})
    code = _source_wrapper(source, envelope)
    try:
        from core.handlers.web_fetch import ExecuteScriptHandler
        output = ExecuteScriptHandler().execute({
            "code": code,
            "destination": "sandbox",
        })
    except Exception as exc:
        raise AgentHookError(str(exc)) from exc
    return _parse_hook_stdout(output)


def _source_wrapper(source: str, envelope: Dict[str, Any]) -> str:
    envelope_json = json.dumps(envelope, ensure_ascii=False)
    return (
        "import json\n"
        f"event = json.loads({envelope_json!r})\n"
        "result = None\n"
        + source
        + "\n"
        "if 'handle' in globals() and callable(handle):\n"
        "    result = handle(event)\n"
        "elif result is None:\n"
        "    result = {'decision': 'allow', 'payload': event.get('payload') or {}}\n"
        "print(json.dumps(result if isinstance(result, dict) else {'decision': 'allow', 'payload': event.get('payload') or {}}, ensure_ascii=False))\n"
    )


def _parse_hook_stdout(output: Any) -> Dict[str, Any]:
    text = str(output or "").strip()
    if not text:
        return {}
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        try:
            parsed = json.loads(line)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AgentHookError("hook stdout did not contain a JSON object")
