"""Provider-native context measurement shared by API and CLI clients."""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


class NativeContextObservationMixin:
    """Store, revise, persist, and publish provider-native prompt sizes."""

    _SESSION_CONTEXT_PROVIDERS = {
        "claude-code", "claude-code-interactive", "antigravity-interactive",
        "codex-app-server", "codex-interactive", "gemini",
    }

    def _record_observed_context(self, conversation_id: str, agent_name: str,
                                 tokens: int, *, mode: str) -> bool:
        """Store one provider-native prompt measurement and its revision."""
        if not conversation_id or not agent_name or mode not in {"request", "session"}:
            return False
        try:
            measured = int(tokens or 0)
        except (TypeError, ValueError):
            return False
        if measured <= 0:
            return False
        key = (conversation_id, agent_name)
        counts = getattr(self, "_cli_observed_context_tokens_by_stream", None)
        if not isinstance(counts, dict):
            counts = {}
            self._cli_observed_context_tokens_by_stream = counts
        modes = getattr(self, "_observed_context_mode_by_stream", None)
        if not isinstance(modes, dict):
            modes = {}
            self._observed_context_mode_by_stream = modes
        revisions = getattr(self, "_observed_context_revision_by_stream", None)
        if not isinstance(revisions, dict):
            revisions = {}
            self._observed_context_revision_by_stream = revisions
        counts[key] = measured
        modes[key] = mode
        revisions[key] = int(revisions.get(key, 0) or 0) + 1
        return True

    def _record_response_context_usage(
            self, response: Any, *, call_conversation_id: str = "",
            call_agent_name: str = "", call_user_id: str = "",
            call_event_cid: str = "") -> None:
        """Record and publish native input usage from a stateless API request.

        This runs before the driver's local token fallback. A response without
        provider usage therefore remains an estimate and cannot become the
        authoritative gauge accidentally.
        """
        if self.provider in self._SESSION_CONTEXT_PROVIDERS:
            return
        if getattr(response, "input_usage_native", None) is False:
            return
        total = 0
        for field in ("tokens_in", "cache_read_tokens", "cache_creation_tokens"):
            try:
                total += int(getattr(response, field, 0) or 0)
            except (TypeError, ValueError):
                continue
        if not self._record_observed_context(
                call_conversation_id, call_agent_name, total, mode="request"):
            return
        self.publish_observed_context_usage(
            call_conversation_id, call_agent_name,
            user_id=call_user_id, event_cid=call_event_cid,
            source=f"{self.provider}_native_input_usage")

    def publish_observed_context_usage(self, conversation_id: str,
                                       agent_name: str, *, user_id: str = "",
                                       event_cid: str = "",
                                       source: str = "native_input_usage") -> None:
        """Persist and publish the latest native prompt measurement."""
        if not conversation_id or not agent_name:
            return
        try:
            from core.conversation_event_bus import ConversationEventBus
            from tasks.ai.context_usage import (
                compute_context_usage, persist_context_usage,
                usage_event_payload)

            usage = compute_context_usage(
                conversation_id, agent_name, user_id=user_id, source=source)
            if int(usage.get("max", 0) or 0) <= 0:
                return
            persist_context_usage(conversation_id, agent_name, usage)
            payload = usage_event_payload(usage)
            payload["live"] = True
            ConversationEventBus.instance().publish_event(
                event_cid or conversation_id, "message_meta", payload)
        except Exception:
            logger.debug("native context usage publish failed", exc_info=True)
