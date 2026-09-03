"""Managed MCP turn coordinator: one turn, one native final hook.

``_ManagedMcpTurnCoordinator`` reuses the interactive event-service consumer
(claim/epoch/eviction, submission proof, liveness probe, tool-row dedup) but
reads nothing from vendor traffic. The turn ends when the CLI's own ``Stop``
hook delivers the bounded final text; PawFlow MCP tool rows still arrive
through the relay's existing publication path and are mirrored here without
being executed again.

Leaf module: imports the CCI coordinator for its block/tool machinery and
never imports a provider facade back.
"""

from __future__ import annotations

import logging
import re
import time

from core._llm_types import LLMCallError
from core.llm_providers._cci_turn import (
    _POST_STOP_IDLE_DRAIN_SECONDS,
    _CCITurnCoordinator,
    _env_seconds,
)
from core.managed_mcp_spec import (
    FINAL_SOURCE_STOP_HOOK,
    TELEMETRY_UNAVAILABLE,
    managed_mcp_spec,
)

logger = logging.getLogger(__name__)

# How long a managed turn may stay silent (no hook, no relay tool row) before
# the missing final hook fails the turn with a typed, non-retryable error.
# 0 disables the deadline: the liveness probe still fails a dead container,
# and a CLI busy with a long local tool emits nothing for minutes legitimately.
_MANAGED_FINAL_TIMEOUT_SECONDS = _env_seconds(
    ("PAWFLOW_MANAGED_MCP_FINAL_TIMEOUT_SECONDS",),
    ("PAWFLOW_MANAGED_MCP_FINAL_TIMEOUT_MS",),
    default=0.0,
)

# A Stop that carries a timestamp older than the turn's own start belongs to
# a previous turn whose hook arrived late (hooks open one short connection
# each and can land out of order). This slack absorbs clock granularity.
_STALE_FINAL_SLACK_SECONDS = 1.0

#: Event types that only a vendor-traffic observer produces. A managed session
#: has no proxy, so seeing one means the session was launched in the wrong
#: mode; it is logged once and ignored, never assembled into an answer.
_VENDOR_EVENT_TYPES = frozenset({
    "sse", "wire", "request_start", "request_stop", "request_error",
    "response_start", "response_ignored",
})


def _stop_failure_error(provider: str, info: dict) -> LLMCallError:
    detail = str(info.get("error") or info.get("reason")
                 or f"{provider} turn failed")
    lowered = detail.lower()
    rate_limited = bool(
        re.search(r"\b429\b", detail)
        or "rate limit" in lowered or "rate_limit" in lowered
        or "usage limit" in lowered)
    return LLMCallError(
        f"{provider} turn failed: {detail}",
        category="rate_limited" if rate_limited else "unknown",
        retryable=False, provider=provider)


class _ManagedMcpTurnCoordinator(_CCITurnCoordinator):
    """Wait for the native final of one managed CLI turn."""

    def __init__(self, event_service, session_token: str, *, provider: str,
                 callback=None, block_callback=None, turn_callback=None,
                 touch_callback=None, emitted_tool_use_ids=None,
                 emitted_tool_result_ids=None, consumer_epoch: int = 0,
                 consumer_kind: str = "request", liveness_callback=None,
                 final_timeout: float | None = None,
                 started_at: float = 0.0):
        super().__init__(
            event_service, session_token, callback=callback,
            thinking_callback=None, block_callback=block_callback,
            turn_callback=turn_callback, touch_callback=touch_callback,
            usage_callback=None,
            emitted_tool_use_ids=emitted_tool_use_ids,
            emitted_tool_result_ids=emitted_tool_result_ids,
            consumer_epoch=consumer_epoch, consumer_kind=consumer_kind,
            liveness_callback=liveness_callback)
        self.provider = provider
        spec = managed_mcp_spec(provider)
        self._spec = spec
        self.final_timeout = (
            _MANAGED_FINAL_TIMEOUT_SECONDS if final_timeout is None
            else max(0.0, float(final_timeout)))
        # The turn's own start: a paste happens after this, so every final
        # older than it describes a previous turn.
        self._started_at = float(started_at or 0.0)
        self.final_text = ""
        self.final_source = ""
        self.prompt_submitted = False
        self._vendor_event_logged = False
        self._finished = False

    # -- helpers -----------------------------------------------------------

    def _raise_if_final_overdue(self, started_at: float) -> None:
        if self.final_timeout <= 0 or self._finished:
            return
        since = self._last_event_at or started_at
        waited = time.time() - since
        if waited < self.final_timeout:
            return
        raise LLMCallError(
            f"{self.provider}: no final Stop hook within "
            f"{self.final_timeout:.0f}s (prompt_submitted="
            f"{self.prompt_submitted})",
            category="timeout", retryable=False, provider=self.provider)

    def _is_stale_final(self, event: dict) -> bool:
        stamp = float(event.get("timestamp") or 0.0)
        if not stamp or not self._started_at:
            return False
        return stamp < self._started_at - _STALE_FINAL_SLACK_SECONDS

    def _complete(self, text: str, info: dict, source: str) -> None:
        """Emit the final text exactly once through the existing callbacks."""
        self._finished = True
        self.final_text = text
        self.final_source = source or FINAL_SOURCE_STOP_HOOK
        model = str(info.get("model") or "")
        if model:
            self.effective_model = model
        # Version 1 has no deltas: the final answer is one text callback,
        # one text block and the turn callback, in that order.
        self._append_text(text, 0)
        self._flush_all_text_blocks()
        self._emit_pending_tool_uses()
        self._emit_turn_callback()
        self._finalize_message_text()

    def _telemetry(self) -> dict:
        spec = self._spec
        return {
            "final_source": self.final_source,
            "text_streaming": "final_only",
            "thinking": (spec.thinking_source if spec
                         else TELEMETRY_UNAVAILABLE),
            "usage": spec.usage_source if spec else TELEMETRY_UNAVAILABLE,
            "context": spec.context_source if spec else TELEMETRY_UNAVAILABLE,
            "builtin_tools_visible": bool(
                spec.builtin_tools_visible) if spec else False,
        }

    def _response(self):
        from core.llm_client import LLMResponse
        return LLMResponse(
            content=self.final_text,
            tool_calls=[],
            tokens_in=0, tokens_out=0, total_tokens=0,
            thinking="",
            model=self.effective_model,
            raw={
                "provider": self.provider,
                "usage": {},
                "effective_model": self.effective_model,
                "telemetry": self._telemetry(),
                "lifecycle_events": self.lifecycle_events,
            })

    # -- main loop ----------------------------------------------------------

    def run(self, abort_event=None):
        from core.llm_client import CCCompactDetected

        if self._consumer_refused:
            logger.info(
                "[%s] session=%s already has a live event consumer "
                "\u2014 skipping this capture", self.provider,
                self.session_token[:8])
            return self._response()
        started_at = time.time()
        if not self._started_at:
            self._started_at = started_at
        while True:
            if abort_event is not None and abort_event.is_set():
                raise RuntimeError(f"{self.provider} aborted")
            event = self._wait_event(0.25)
            if not event:
                self._probe_liveness(started_at)
                self._raise_if_final_overdue(started_at)
                continue
            if self.touch_callback:
                self.touch_callback()
            now = time.time()
            self._last_event_at = now
            if not self._first_event_at:
                self._first_event_at = now
            etype = event.get("type", "")
            if etype == "tool_use":
                self._emit_observed_tool_use(event)
                continue
            if etype == "tool_result":
                self._emit_tool_result(event)
                continue
            if etype in _VENDOR_EVENT_TYPES:
                if not self._vendor_event_logged:
                    self._vendor_event_logged = True
                    logger.warning(
                        "[%s] session=%s received vendor-traffic event %s on "
                        "a managed session; ignored (no MITM fallback)",
                        self.provider, self.session_token[:8], etype)
                continue
            if etype != "hook":
                continue
            self.lifecycle_events.append(event)
            hook_name = str(event.get("hook_event_name", "") or "")
            info = event.get("input") or {}
            if not isinstance(info, dict):
                info = {}
            if hook_name == "UserPromptSubmit":
                self.prompt_submitted = True
                continue
            if hook_name in {"PreCompact", "PostCompact"}:
                logger.warning(
                    "[%s] %s detected \u2014 rejecting native compaction and "
                    "handing context to PawFlow", self.provider, hook_name)
                raise CCCompactDetected(
                    f"{self.provider} {hook_name} hook detected")
            if hook_name == "StopFailure":
                raise _stop_failure_error(self.provider, info)
            if hook_name == "SessionEnd":
                raise LLMCallError(
                    f"{self.provider}: the CLI session ended before its final "
                    "Stop hook (reason="
                    f"{info.get('reason') or 'unknown'!s})",
                    category="unknown", retryable=False,
                    provider=self.provider)
            if hook_name != "Stop":
                continue
            if self._is_stale_final(event):
                logger.info(
                    "[%s] session=%s ignoring a Stop older than this turn",
                    self.provider, self.session_token[:8])
                continue
            text = str(info.get("last_assistant_message") or "")
            if not text.strip():
                raise LLMCallError(
                    f"{self.provider}: the Stop hook carried no extractable "
                    "final answer (hook field empty and no transcript "
                    "fallback)",
                    category="unknown", retryable=False,
                    provider=self.provider)
            self._complete(text, info, str(info.get("final_source") or ""))
            break

        self._drain_post_stop_tool_rows()
        total_ms = (time.time() - started_at) * 1000.0
        logger.info(
            "[%s] final session=%s source=%s total_ms=%.1f text_len=%d "
            "tool_calls=%d", self.provider, self.session_token[:8],
            self.final_source, total_ms, len(self.final_text),
            len(self.turn_tool_calls))
        return self._response()

    def _drain_post_stop_tool_rows(self) -> None:
        """Take relay tool rows that raced the Stop hook, briefly.

        A tool row published by the relay for this turn can reach the queue a
        few milliseconds after the CLI's Stop hook. Only tool rows are taken;
        a second Stop is left for the next drain and can complete nothing.
        """
        deadline = time.time() + min(_POST_STOP_IDLE_DRAIN_SECONDS, 1.0)
        while time.time() < deadline:
            try:
                event = self._wait_event(0.05)
            except Exception:  # noqa: BLE001 - eviction or a dead service both end the drain
                return
            if not event:
                continue
            etype = event.get("type", "")
            if etype == "tool_use":
                self._emit_observed_tool_use(event)
            elif etype == "tool_result":
                self._emit_tool_result(event)
            else:
                # Not ours to consume; push it back for the next consumer.
                try:
                    state = self.event_service.session_state(self.session_token)
                    if state is not None:
                        with state.stream_condition:
                            state.pushback.append(event)
                except Exception:
                    logger.debug("post-stop pushback failed", exc_info=True)
                return
