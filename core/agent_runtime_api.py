"""Shared agent runtime API for non-HTTP transports.

This module is the first extraction step away from treating ``/api/agent`` as
the only agent client contract. It normalizes a client request into the same
FlowFile shape consumed by ``AgentLoopTask`` and provides a correlated wait for
the final ``done`` event.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core import FlowFile


@dataclass
class AgentRequest:
    user_id: str
    message: str
    conversation_id: str = ""
    target_agent: str = ""
    attachments: list = field(default_factory=list)
    msg_id: str = ""
    channel: str = "web"
    runtime_port: str = ""
    source_attributes: Dict[str, str] = field(default_factory=dict)


@dataclass
class AgentSubmission:
    status: str
    conversation_id: str
    turn_id: str
    target_agent: str = ""
    server_start_time: float = 0.0


@dataclass
class AgentFinalResult:
    conversation_id: str
    turn_id: str
    response: str = ""
    agent_name: str = ""
    channel: str = ""
    finish_reason: str = ""
    error: str = ""
    event_type: str = "done"
    data: Dict[str, Any] = field(default_factory=dict)


class AgentResultWaiter:
    """Wait for correlated agent final events without changing SSE broadcast."""

    _instance: Optional["AgentResultWaiter"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._pending: Dict[str, Dict[str, Any]] = {}
        self._pending_lock = threading.Lock()
        self._listener_registered = False

    @classmethod
    def instance(cls) -> "AgentResultWaiter":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_listener(self) -> None:
        if self._listener_registered:
            return
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().add_listener(self._on_event)
        self._listener_registered = True

    def register(self, conversation_id: str, turn_id: str) -> None:
        if not conversation_id or not turn_id:
            return
        self._ensure_listener()
        key = self._key(conversation_id, turn_id)
        with self._pending_lock:
            self._pending[key] = {
                "event": threading.Event(),
                "result": None,
                "created_at": time.time(),
            }

    def wait(self, conversation_id: str, turn_id: str,
             timeout: float = 600.0) -> Optional[AgentFinalResult]:
        key = self._key(conversation_id, turn_id)
        with self._pending_lock:
            item = self._pending.get(key)
        if not item:
            return None
        item["event"].wait(timeout=max(0.0, float(timeout)))
        with self._pending_lock:
            item = self._pending.pop(key, item)
        return item.get("result")

    def cancel(self, conversation_id: str, turn_id: str) -> None:
        with self._pending_lock:
            self._pending.pop(self._key(conversation_id, turn_id), None)

    def _on_event(self, conversation_id: str, event_type: str, data: Any) -> None:
        if event_type not in {"done", "error_event"} or not isinstance(data, dict):
            return
        turn_id = str(data.get("turn_id") or data.get("request_msg_id") or "")
        if not turn_id:
            return
        key = self._key(conversation_id, turn_id)
        with self._pending_lock:
            item = self._pending.get(key)
        if not item:
            return
        result = AgentFinalResult(
            conversation_id=conversation_id,
            turn_id=turn_id,
            response=str(data.get("response") or ""),
            agent_name=str(data.get("agent_name") or ""),
            channel=str(data.get("channel") or ""),
            finish_reason=str(data.get("finish_reason") or ""),
            error=str(data.get("message") or "") if event_type == "error_event" else "",
            event_type=event_type,
            data=dict(data),
        )
        item["result"] = result
        item["event"].set()

    @staticmethod
    def _key(conversation_id: str, turn_id: str) -> str:
        return f"{conversation_id}\x1f{turn_id}"


class AgentRuntimeAPI:
    """Shared submission API used by transports such as Telegram."""

    @staticmethod
    def submit_message(request: AgentRequest) -> AgentSubmission:
        if not request.user_id:
            raise ValueError("AgentRequest.user_id is required")
        if not request.message and not request.attachments:
            raise ValueError("AgentRequest.message or attachments is required")

        turn_id = request.msg_id or f"{request.channel}:{uuid.uuid4().hex}"
        body = {
            "conversation_id": request.conversation_id,
            "message": request.message,
            "attachments": request.attachments,
            "msg_id": turn_id,
        }
        if request.target_agent:
            body["target_agent"] = request.target_agent

        ff = FlowFile(content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
        ff.set_attribute("http.auth.principal", request.user_id)
        ff.set_attribute("agent.client_channel", request.channel or "web")
        ff.set_attribute("agent.request_msg_id", turn_id)
        for key, value in (request.source_attributes or {}).items():
            ff.set_attribute(str(key), str(value))

        inst = None
        if request.runtime_port:
            from core.agent_runtime_ports import resolve_agent_runtime_task
            inst = resolve_agent_runtime_task(request.runtime_port)
        else:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
        if inst is None:
            if request.runtime_port:
                raise RuntimeError(
                    f"No live AgentLoopTask is available for runtime port: {request.runtime_port}")
            raise RuntimeError("No live AgentLoopTask instance is available")

        waiter = AgentResultWaiter.instance()
        if request.conversation_id:
            waiter.register(request.conversation_id, turn_id)
        outputs = inst.execute(ff)
        out = outputs[0] if outputs else ff
        try:
            ack = json.loads(out.get_content().decode("utf-8", errors="replace"))
        except Exception:
            ack = {}
        conversation_id = str(ack.get("conversation_id") or request.conversation_id or
                              out.get_attribute("agent.conversation_id") or "")
        if conversation_id and not request.conversation_id:
            waiter.register(conversation_id, turn_id)
        return AgentSubmission(
            status=str(ack.get("status") or "accepted"),
            conversation_id=conversation_id,
            turn_id=turn_id,
            target_agent=request.target_agent,
            server_start_time=float(ack.get("server_start_time") or 0.0),
        )

    @staticmethod
    def wait_for_done(conversation_id: str, turn_id: str,
                      timeout: float = 600.0) -> Optional[AgentFinalResult]:
        return AgentResultWaiter.instance().wait(conversation_id, turn_id, timeout)

