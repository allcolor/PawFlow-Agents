"""Generic, scope-checked PawFlow API facade for deployed flows.

``FlowPawflowApi`` is the single reusable surface a flow uses to drive PawFlow
from inside an ``executeScript`` task (it is injected into the sandbox namespace
as ``pawflow``, the same way ``fs`` and ``tools`` are). It is NOT importable
from sandboxed scripts: the host builds it with the task's runtime scope and
hands the bound object to the script, so every operation is authorized against
that scope.

Authorization is delegated to :mod:`core.flow_runtime_access`:
- a conversation-scoped flow can only touch its own conversation;
- a user-scoped flow can only touch conversations/users it owns;
- a global flow must be bounded by a trusted requester user or opt into admin.

This keeps a single source of truth for the deployment-scope authorization
boundary across every flow task that already uses it (``createConversation``,
``publishMessage``, ``spawnAgent`` ...).

Every method raises :class:`~core.flow_runtime_access.FlowRuntimeAccessError`
(a ``PermissionError``) when a target is outside the flow's scope.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from core.flow_runtime_access import (
    FlowRuntimeContext,
    authorize_conversation_target,
    authorize_user_target,
)

logger = logging.getLogger(__name__)

_EXPIRES_EXTRA = "_meta_expires_at"


class FlowPawflowApi:
    """Scope-bounded PawFlow operations exposed to a flow script."""

    def __init__(self, ctx: FlowRuntimeContext, requester_user_id: str = "",
                 default_runtime_port: str = ""):
        self._ctx = ctx
        self._requester = str(requester_user_id or "").strip()
        self._default_runtime_port = str(default_runtime_port or "").strip()

    # ── authorization helpers ─────────────────────────────────────────

    def _auth_user(self, target_user_id: str = "") -> str:
        return authorize_user_target(
            self._ctx, target_user_id,
            requester_user_id=self._requester,
            allow_global_admin=self._ctx.allow_global_admin)

    def _auth_conv(self, conversation_id: str) -> str:
        return authorize_conversation_target(
            self._ctx, conversation_id,
            requester_user_id=self._requester,
            allow_global_admin=self._ctx.allow_global_admin)

    @property
    def user_id(self) -> str:
        """The effective owner user this flow acts as (runtime/requester)."""
        return self._auth_user("")

    # ── conversations ─────────────────────────────────────────────────

    def create_conversation(self, agents: List[Dict[str, Any]],
                            title: str = "", relays: Optional[List[str]] = None,
                            default_relay: str = "", ttl: int = 0,
                            user_id: str = "") -> str:
        """Create a conversation owned by the flow's user. Returns its id.

        ``agents`` is the same association list ``create_conversation`` expects
        (``definition``/``instance_name``/``llm_service``/``skills``/``tools``/
        ``model``/``max_depth``). Pass ``relays=[]`` for a relay-less agent.
        ``ttl`` (seconds, >0) stamps ``_meta_expires_at`` for TTL-based purging.
        """
        owner = self._auth_user(user_id)
        from core.conversation_creation import create_conversation
        payload: Dict[str, Any] = {"agents": agents, "title": title or ""}
        if relays:
            payload["relays"] = list(relays)
            payload["default_relay"] = default_relay or relays[0]
        result = create_conversation(owner, payload)
        cid = result["conversation_id"]
        if ttl and int(ttl) > 0:
            self.set_conversation_ttl(cid, int(ttl))
        return cid

    def delete_conversation(self, conversation_id: str) -> bool:
        cid = self._auth_conv(conversation_id)
        from core.conversation_store import ConversationStore
        from core.flow_runtime_access import conversation_owner
        return ConversationStore.instance().delete(
            cid, user_id=conversation_owner(cid))

    def list_conversations(self) -> List[Dict[str, Any]]:
        """List conversations owned by the flow's user."""
        owner = self._auth_user("")
        from core.conversation_store import ConversationStore
        return ConversationStore.instance().list_conversations(user_id=owner)

    def find_conversations(self, extra_key: str, extra_value: Any
                           ) -> List[str]:
        """Return ids of the flow user's conversations whose ``extra_key``
        equals ``extra_value``."""
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        out: List[str] = []
        for conv in self.list_conversations():
            cid = conv.get("conversation_id") or ""
            if cid and store.get_extra(cid, extra_key) == extra_value:
                out.append(cid)
        return out

    # ── conversation extras / TTL ─────────────────────────────────────

    def get_extra(self, conversation_id: str, key: str,
                  default: Any = None) -> Any:
        cid = self._auth_conv(conversation_id)
        from core.conversation_store import ConversationStore
        return ConversationStore.instance().get_extra(cid, key, default=default)

    def set_extra(self, conversation_id: str, key: str, value: Any) -> bool:
        cid = self._auth_conv(conversation_id)
        from core.conversation_store import ConversationStore
        return ConversationStore.instance().set_extra(cid, key, value)

    def set_conversation_ttl(self, conversation_id: str, ttl_seconds: int
                             ) -> float:
        """(Re-)arm the conversation expiry to ``now + ttl_seconds``.

        Returns the new ``_meta_expires_at`` epoch. Call again on each turn for
        a sliding TTL. Purging is the caller's job (PawFlow has no global
        conversation sweeper) — see :meth:`is_conversation_expired` /
        :meth:`delete_conversation`.
        """
        expires_at = time.time() + max(0, int(ttl_seconds))
        self.set_extra(conversation_id, _EXPIRES_EXTRA, expires_at)
        return expires_at

    def is_conversation_expired(self, conversation_id: str,
                                now: float = 0.0) -> bool:
        expires = self.get_extra(conversation_id, _EXPIRES_EXTRA, default=0) or 0
        try:
            expires = float(expires)
        except (TypeError, ValueError):
            expires = 0.0
        return expires > 0 and (now or time.time()) >= expires

    # ── tool availability ─────────────────────────────────────────────

    def set_tool_filters(self, conversation_id: str, agent: str,
                         allow: List[str]) -> None:
        """Restrict ``agent`` to exactly the ``allow`` tool list (allowlist).

        Uses ``tool_mcp_filters`` custom mode: any tool not in ``allow`` is
        unavailable to that agent in this conversation, regardless of what the
        agent definition advertises.
        """
        cid = self._auth_conv(conversation_id)
        from core.tool_mcp_filters import get_filters, set_filters
        filters = get_filters(cid)
        overrides = dict(filters.get("agent_overrides") or {})
        overrides[agent] = {"tools": {"mode": "custom",
                                      "selected": list(allow or [])}}
        filters["agent_overrides"] = overrides
        set_filters(cid, filters)

    # ── agent execution (queued, with hard timeout + cancel) ──────────

    def _runtime_instance(self, runtime_port: str):
        port = str(runtime_port or self._default_runtime_port or "").strip()
        if port:
            from core.agent_runtime_ports import resolve_agent_runtime_task
            return resolve_agent_runtime_task(port), port
        from tasks.ai.agent_loop import AgentLoopTask
        return AgentLoopTask._live_instance, ""

    def cancel_agent(self, conversation_id: str, agent: str = "",
                     runtime_port: str = "", reason: str = "timeout") -> bool:
        """Force-cancel a running agent turn in a conversation."""
        cid = self._auth_conv(conversation_id)
        inst, _ = self._runtime_instance(runtime_port)
        if inst is None or not hasattr(inst, "cancel_agent"):
            return False
        try:
            inst.cancel_agent(cid, agent_name=agent or "", reason=reason)
            return True
        except Exception:
            logger.debug("cancel_agent failed for %s", cid[:8], exc_info=True)
            return False

    def submit_agent(self, conversation_id: str, agent: str, message: str,
                     channel: str = "flow", runtime_port: str = "",
                     msg_id: str = "", attachments: Optional[list] = None,
                     source_attributes: Optional[Dict[str, str]] = None
                     ) -> Dict[str, Any]:
        """Submit a message to a conversation's agent without waiting.

        The shared runtime queues the message behind any running turn, so
        message->response ordering is preserved per conversation. Returns the
        submission metadata (``conversation_id``, ``turn_id``, ``status``).
        """
        cid = self._auth_conv(conversation_id)
        from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI
        owner = self._auth_user("")
        request = AgentRequest(
            user_id=owner,
            conversation_id=cid,
            target_agent=agent,
            message=message,
            attachments=list(attachments or []),
            msg_id=msg_id or "",
            channel=channel or "flow",
            runtime_port=str(runtime_port or self._default_runtime_port or "").strip(),
            source_attributes=dict(source_attributes or {}),
        )
        submission = AgentRuntimeAPI.submit_message(request)
        return {
            "conversation_id": submission.conversation_id,
            "turn_id": submission.turn_id,
            "status": submission.status,
            "wait_for_done": submission.wait_for_done,
        }

    def run_agent(self, conversation_id: str, agent: str, message: str,
                  timeout: float = 600.0, channel: str = "flow",
                  runtime_port: str = "", msg_id: str = "",
                  attachments: Optional[list] = None,
                  source_attributes: Optional[Dict[str, str]] = None
                  ) -> Dict[str, Any]:
        """Submit a message and wait up to ``timeout`` seconds for the reply.

        Hard timeout for unattended flows (e.g. a public bot with nobody to
        cancel a stuck turn): if the agent does not finish within ``timeout``,
        the turn is force-cancelled and the result is returned with
        ``timed_out=True``. Returns a dict with ``response``, ``error``,
        ``timed_out``, ``status``, ``conversation_id`` and ``turn_id``.
        """
        cid = self._auth_conv(conversation_id)
        from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI
        owner = self._auth_user("")
        port = str(runtime_port or self._default_runtime_port or "").strip()
        request = AgentRequest(
            user_id=owner,
            conversation_id=cid,
            target_agent=agent,
            message=message,
            attachments=list(attachments or []),
            msg_id=msg_id or f"{channel or 'flow'}:{uuid.uuid4().hex}",
            channel=channel or "flow",
            runtime_port=port,
            source_attributes=dict(source_attributes or {}),
        )
        submission = AgentRuntimeAPI.submit_message(request)
        base = {
            "conversation_id": submission.conversation_id,
            "turn_id": submission.turn_id,
            "status": submission.status,
            "response": "",
            "error": "",
            "timed_out": False,
        }
        # Always wait on the correlated `done` for this turn_id, even when the
        # runtime queued the message behind a running turn: the waiter is
        # registered per turn_id and fires when this message is finally
        # processed. This preserves strict message->response ordering per
        # conversation without needing a live event bridge.
        result = AgentRuntimeAPI.wait_for_done(
            submission.conversation_id, submission.turn_id,
            timeout=max(0.0, float(timeout)))
        if result is None:
            # Hard timeout: nobody will cancel this for us — do it now so a
            # blocked turn does not run forever.
            self.cancel_agent(submission.conversation_id, agent=agent,
                              runtime_port=port, reason="response_timeout")
            AgentRuntimeAPI.wait_for_done(
                submission.conversation_id, submission.turn_id, timeout=0.1)
            base["timed_out"] = True
            return base
        base["response"] = str(result.response or "")
        base["error"] = str(result.error or "")
        return base
