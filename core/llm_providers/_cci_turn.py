"""Claude Code interactive turn coordinator.

``_CCITurnCoordinator`` drives one streamed CC interactive turn: it consumes
proxy/native events, assembles text / thinking / tool-use blocks and emits the
turn callback. Extracted from ``claude_code_interactive`` to keep that module
<=800 lines. Leaf module (no import back into claude_code_interactive); the
coordinator and helpers are re-exported there for back-compat (invariant 1).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid

from core.tool_json import parse_tool_arguments, tool_argument_parse_error
from tools.cc_interactive_filters import (
    is_hidden_native_tool, normalize_observed_tool, observed_tool_origin)

logger = logging.getLogger(__name__)


def _env_seconds(names: tuple[str, ...], ms_names: tuple[str, ...] = (),
                 default: float = 0.0) -> float:
    for name in names:
        raw = os.environ.get(name, "")
        if raw.strip():
            try:
                return max(0.0, float(raw))
            except ValueError:
                return default
    for name in ms_names:
        raw = os.environ.get(name, "")
        if raw.strip():
            try:
                return max(0.0, float(raw) / 1000.0)
            except ValueError:
                return default
    return default


_POST_STOP_IDLE_DRAIN_SECONDS = _env_seconds(
    ("PAWFLOW_CCI_POST_STOP_IDLE_DRAIN_SECONDS", "PAWFLOW_CCI_DRAIN_SECONDS"),
    ("PAWFLOW_CCI_POST_STOP_IDLE_DRAIN_MS", "PAWFLOW_CCI_DRAIN_MS"),
    default=2.5,
)
_NO_PROXY_EVENT_TIMEOUT_SECONDS = _env_seconds(
    ("PAWFLOW_CCI_NO_PROXY_EVENT_TIMEOUT_SECONDS", "PAWFLOW_CCI_NOEVENT_TIMEOUT_SECONDS"),
    ("PAWFLOW_CCI_NO_PROXY_EVENT_TIMEOUT_MS", "PAWFLOW_CCI_NOEVENT_TIMEOUT_MS"),
    default=300.0,
)


def _event_tool_args(event: dict) -> dict:
    """Return tool args from any observed CCI event shape."""
    for key in ("arguments", "input", "tool_input"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
    payload = event.get("payload") or {}
    if isinstance(payload, dict):
        block = payload.get("content_block") or {}
        if isinstance(block, dict) and isinstance(block.get("input"), dict):
            return block["input"]
        delta = payload.get("delta") or {}
        if isinstance(delta, dict) and isinstance(delta.get("partial_json"), str):
            try:
                parsed = json.loads(delta["partial_json"])
            except (TypeError, ValueError):
                return {}
            if isinstance(parsed, dict):
                return parsed
    return {}


def _loads_tolerant(raw: str) -> dict:
    """Parse observed tool-input JSON for DISPLAY, tolerating EOF truncation.

    Large tool inputs (e.g. an `edit` with a big new_string) stream as many
    input_json_delta chunks; when the observed JSON is truncated at EOF strict
    parsing fails and the display args are lost, so the call renders as a bare
    "Update()". Best-effort close the truncated JSON before giving up.
    autoclose returns the input unchanged when there is nothing to fix, so a
    valid payload and the genuinely-unrecoverable case both behave exactly as
    the previous `try/except -> {}`. Always returns a dict. Display-only: the
    real Claude Code process executes from its own complete stream regardless.
    """
    parsed = parse_tool_arguments(raw, tool_name="cci-display", provider="cci")
    return parsed if (isinstance(parsed, dict)
                      and not tool_argument_parse_error(parsed)) else {}


class _CCITurnCoordinator:
    def __init__(self, event_service, session_token: str, callback=None,
                 thinking_callback=None, block_callback=None,
                 turn_callback=None, touch_callback=None,
                 emitted_tool_use_ids=None, emitted_tool_result_ids=None):
        self.event_service = event_service
        self.session_token = session_token
        self.touch_callback = touch_callback
        self.callback = callback
        self.thinking_callback = thinking_callback
        self.block_callback = block_callback
        self.turn_callback = turn_callback
        self.text_parts: list[str] = []
        # Per-API-message text tracking: a CCI turn spans several
        # /v1/messages calls (text → tool use → text …). The final visible
        # answer is the LAST message's text, not the whole-turn join —
        # channel bridges (Telegram) relay LLMResponse.content verbatim.
        self._message_text_parts: list[str] = []
        self._last_message_text = ""
        self.thinking_parts: list[str] = []
        self.turn_tool_calls: list[dict] = []
        self.tool_blocks: dict[int, dict] = {}
        self.tool_by_id: dict[str, dict] = {}
        self.pending_tool_results: dict[str, list[dict]] = {}
        # Dedup of observed tool_use/tool_result ids. Owned by the
        # persistent session (InteractiveContainer) when provided, so an
        # id observed on an earlier turn — a live Claude Code session
        # replays its whole context on every API request — is not
        # re-emitted and re-appended to the PawFlow agent context.
        # Falls back to per-coordinator sets when no session set is given.
        self.emitted_tool_use_ids: set[str] = (
            emitted_tool_use_ids if emitted_tool_use_ids is not None else set())
        self.emitted_tool_result_ids: set[str] = (
            emitted_tool_result_ids if emitted_tool_result_ids is not None else set())
        self.usage = {}
        # Effective model resolved by Anthropic for the configured alias
        # (e.g. "best" -> "claude-opus-4-5-..."), observed on the wire via
        # the message_start SSE event. Empty until a message_start is seen.
        self.effective_model = ""
        self.lifecycle_events: list[dict] = []
        self.current_block_type = None
        self._block_types: dict[int, str] = {}
        self._text_block_bufs: dict[int, str] = {}
        self._thinking_block_bufs: dict[int, str] = {}
        self._thinking_redacted: dict[int, bool] = {}
        self._thinking_start: dict[int, float] = {}
        self._thinking_end: dict[int, float] = {}
        self._request_stop_reasons: dict[str, str] = {}
        self._request_saw_model_content: dict[str, bool] = {}
        self._request_saw_tool_use: dict[str, bool] = {}
        self._saw_model_content = False
        self._stop_seen = False
        self._post_stop_last_event_at = 0.0
        self._turn_callback_sent = False
        self._saw_proxy_event = False
        self._first_event_at = 0.0
        self._first_model_content_at = 0.0
        self._last_event_at = 0.0
        self._max_event_gap = 0.0

    def run(self, abort_event=None):
        from core.llm_client import LLMResponse

        started_at = time.time()
        done = False
        while not done:
            if abort_event is not None and abort_event.is_set():
                raise RuntimeError("claude-code-interactive aborted")
            timeout = 0.05 if self._stop_seen else 0.25
            event = self.event_service.wait_event(self.session_token, timeout=timeout)
            if not event:
                if not self._saw_proxy_event:
                    waited = time.time() - started_at
                    if waited >= _NO_PROXY_EVENT_TIMEOUT_SECONDS:
                        raise RuntimeError(
                            "Claude Code interactive produced no observed proxy "
                            "events after tmux prompt submit")
                if self._stop_seen:
                    if not self._saw_proxy_event:
                        continue
                    idle_for = time.time() - self._post_stop_last_event_at
                    if idle_for >= _POST_STOP_IDLE_DRAIN_SECONDS:
                        done = self._finish_turn_if_ready()
                continue
            if self.touch_callback:
                self.touch_callback()
            now = time.time()
            if self._last_event_at and now - self._last_event_at >= 2.0:
                self._max_event_gap = max(self._max_event_gap, now - self._last_event_at)
            self._last_event_at = now
            if not self._first_event_at:
                self._first_event_at = now
            if self._stop_seen:
                self._post_stop_last_event_at = now
            etype = event.get("type", "")
            if etype == "request_error":
                self._saw_proxy_event = True
                raise RuntimeError(event.get("error", "CC interactive proxy request failed"))
            if etype == "request_start":
                self._saw_proxy_event = True
                request_id = event.get("request_id", "") or ""
                path = event.get("path", "") or ""
                if request_id and path.startswith("/v1/messages") and not event.get("ignore_reason"):
                    self._request_saw_model_content.setdefault(request_id, False)
                    self._request_saw_tool_use.setdefault(request_id, False)
                    # A fresh /v1/messages request after a Stop means the turn
                    # is still going — typically a PawFlow preempt injected a
                    # new prompt into the live session, extending the turn past
                    # the earlier Stop hook. The latch is now stale: leaving it
                    # set lets a later idle gap (e.g. the model churning on a
                    # large tool result) trip _finish_turn_if_ready and return
                    # the coordinator mid-answer, abandoning the in-flight
                    # response so it only ever reaches tmux. Clear it; the new
                    # turn fires its own Stop when it truly ends.
                    if self._stop_seen:
                        logger.info(
                            "[cci-provider] new request after Stop (session=%s) — "
                            "clearing stale stop latch; turn continues",
                            self.session_token[:8])
                        self._stop_seen = False
                continue
            if etype == "request_stop":
                self._saw_proxy_event = True
                continue
            if etype == "response_ignored":
                self._saw_proxy_event = True
                continue
            if etype == "response_start":
                self._saw_proxy_event = True
                continue
            if etype == "tool_use":
                self._saw_proxy_event = True
                self._emit_observed_tool_use(event)
                continue
            if etype == "tool_result":
                self._saw_proxy_event = True
                self._emit_tool_result(event)
                continue
            if etype == "hook":
                self.lifecycle_events.append(event)
                hook_name = event.get("hook_event_name", "")
                if hook_name == "Stop":
                    self._stop_seen = True
                    self._post_stop_last_event_at = time.time()
                elif hook_name == "StopFailure":
                    info = event.get("input") or {}
                    detail = info.get("error") or "Claude Code interactive turn failed"
                    raise RuntimeError(str(detail))
                continue
            if etype != "sse":
                continue
            self._saw_proxy_event = True
            name = event.get("event", "")
            payload = event.get("payload") or {}
            ptype = payload.get("type") or name
            request_id = event.get("request_id", "") or ""
            if ptype == "message_start":
                # A new API message begins — fold the previous one so
                # _last_message_text always holds the latest completed
                # message that produced text.
                self._finalize_message_text()
                msg = payload.get("message") or {}
                model = msg.get("model")
                if model:
                    # Last observed wins: a turn may issue several
                    # /v1/messages calls; the final assistant request
                    # carries the model actually used for the response.
                    self.effective_model = str(model)
                usage = msg.get("usage") or {}
                if usage:
                    self.usage.update(usage)
            elif ptype == "content_block_start":
                self._saw_model_content = True
                if not self._first_model_content_at:
                    self._first_model_content_at = time.time()
                if request_id:
                    self._request_saw_model_content[request_id] = True
                block = payload.get("content_block") or {}
                idx = int(payload.get("index", 0) or 0)
                block_type = block.get("type")
                self.current_block_type = block_type
                if block_type:
                    self._block_types[idx] = block_type
                if block_type == "thinking":
                    thinking = (
                        block.get("thinking", "")
                        or block.get("text", "")
                        or block.get("reasoning_content", ""))
                    if thinking:
                        self._append_thinking(thinking, idx)
                    elif block.get("signature"):
                        self._mark_redacted_thinking(idx)
                elif block_type == "tool_use":
                    if request_id:
                        self._request_saw_tool_use[request_id] = True
                    block_state = {
                        "id": block.get("id") or f"cci_{uuid.uuid4().hex[:12]}",
                        "name": block.get("name", ""),
                        "json": "",
                        "emitted": False,
                        "hidden": False,
                    }
                    self.tool_blocks[idx] = block_state
                    self.tool_by_id[block_state["id"]] = block_state
                    tool_input = block.get("input")
                    if isinstance(tool_input, dict) and tool_input:
                        self.tool_blocks[idx]["json"] = json.dumps(tool_input, ensure_ascii=False)
                elif block_type == "text":
                    self._append_text(block.get("text", ""), idx)
            elif ptype == "content_block_delta":
                self._saw_model_content = True
                if not self._first_model_content_at:
                    self._first_model_content_at = time.time()
                if request_id:
                    self._request_saw_model_content[request_id] = True
                idx = int(payload.get("index", 0) or 0)
                delta = payload.get("delta") or {}
                dtype = delta.get("type", "")
                block_type = self._block_types.get(idx) or self.current_block_type
                if dtype == "signature_delta":
                    if block_type == "thinking" or delta.get("signature"):
                        self._mark_redacted_thinking(idx)
                    continue
                if dtype == "input_json_delta" and idx in self.tool_blocks:
                    self.tool_blocks[idx]["json"] += delta.get("partial_json", "")
                    continue
                thinking_text = (
                    delta.get("thinking", "")
                    or delta.get("reasoning_content", "")
                    or delta.get("reasoning", ""))
                if dtype == "thinking_delta" or (
                        block_type == "thinking" and thinking_text):
                    self._append_thinking(thinking_text or delta.get("text", ""), idx)
                else:
                    self._append_text(delta.get("text", ""), idx)
            elif ptype == "content_block_stop":
                idx = int(payload.get("index", 0) or 0)
                if idx in self.tool_blocks:
                    self._emit_tool_use(idx)
                block_type = self._block_types.pop(idx, self.current_block_type)
                if block_type == "thinking":
                    self._flush_thinking_block(idx)
                elif block_type == "text":
                    self._flush_text_block(idx)
                else:
                    self._flush_text_block(idx)
                    self._flush_thinking_block(idx)
                if not self._block_types:
                    self.current_block_type = None
            elif ptype == "message_delta":
                request_id = event.get("request_id", "") or ""
                delta = payload.get("delta") or {}
                stop_reason = delta.get("stop_reason") or payload.get("stop_reason") or ""
                if request_id and stop_reason:
                    self._request_stop_reasons[request_id] = str(stop_reason)
                usage = payload.get("usage") or {}
                if usage:
                    self.usage.update(usage)
            elif ptype == "message_stop":
                continue

        self._finalize_message_text()
        text = self._last_message_text
        total_ms = (time.time() - started_at) * 1000.0
        first_event_ms = ((self._first_event_at - started_at) * 1000.0
                          if self._first_event_at else 0.0)
        first_model_ms = ((self._first_model_content_at - started_at) * 1000.0
                          if self._first_model_content_at else 0.0)
        slow_turn = (
            first_model_ms >= 2000
            or self._max_event_gap >= 2.0
            or total_ms >= 5000
        )
        log = logger.info if slow_turn else logger.debug
        log(
            "[cci-provider] timing session=%s total_ms=%.1f "
            "first_event_ms=%.1f first_model_ms=%.1f "
            "max_event_gap_ms=%.1f text_len=%d thinking_len=%d "
            "tool_calls=%d",
            self.session_token[:8], total_ms, first_event_ms, first_model_ms,
            self._max_event_gap * 1000.0, len(text),
            len("".join(self.thinking_parts)), len(self.turn_tool_calls))
        return LLMResponse(
            content=text,
            tool_calls=[],
            tokens_in=int(self.usage.get("input_tokens", 0) or 0),
            tokens_out=int(self.usage.get("output_tokens", 0) or 0),
            total_tokens=(int(self.usage.get("input_tokens", 0) or 0)
                          + int(self.usage.get("output_tokens", 0) or 0)),
            thinking="".join(self.thinking_parts),
            model=self.effective_model,
            raw={
                "provider": "claude-code-interactive",
                "usage": self.usage,
                "effective_model": self.effective_model,
                "lifecycle_events": self.lifecycle_events,
            },
        )

    def _finish_turn_if_ready(self) -> bool:
        if not self._stop_seen:
            return False
        self._flush_all_text_blocks()
        self._flush_all_thinking_blocks()
        self._emit_pending_tool_uses()
        self._emit_turn_callback()
        return True

    def _append_text(self, text: str, idx: int = 0) -> None:
        if text:
            self._text_block_bufs[idx] = self._text_block_bufs.get(idx, "") + text
            self.text_parts.append(text)
            self._message_text_parts.append(text)
            if self.callback:
                self.callback(text)

    def _finalize_message_text(self) -> None:
        if self._message_text_parts:
            self._last_message_text = "".join(self._message_text_parts)
            self._message_text_parts = []

    def _append_thinking(self, text: str, idx: int = 0) -> None:
        if text:
            self._thinking_block_bufs[idx] = self._thinking_block_bufs.get(idx, "") + text
            if self.thinking_callback:
                self.thinking_callback(text)

    def _mark_redacted_thinking(self, idx: int = 0) -> None:
        self._thinking_redacted[idx] = True
        if self._thinking_start.get(idx, 0.0) == 0.0:
            self._thinking_start[idx] = time.time()
        self._thinking_end[idx] = time.time()

    def _flush_text_block(self, idx: int) -> None:
        if idx not in self._text_block_bufs:
            return
        text = self._text_block_bufs.pop(idx, "")
        if self.block_callback:
            self.block_callback("text", {"text": text})

    def _flush_all_text_blocks(self) -> None:
        for idx in sorted(self._text_block_bufs):
            self._flush_text_block(idx)

    def _flush_thinking_block(self, idx: int) -> None:
        redacted = self._thinking_redacted.get(idx, False)
        if idx not in self._thinking_block_bufs and not redacted:
            return
        thinking = self._thinking_block_bufs.pop(idx, "")
        synthesized = False
        if not thinking and redacted:
            duration = max(0.0, self._thinking_end.get(idx, 0.0) - self._thinking_start.get(idx, 0.0))
            thinking = (
                f"[Thought for {duration:.1f}s - reasoning content redacted "
                "by the Anthropic API; the signature is preserved by Claude Code.]"
            )
            synthesized = True
            self.thinking_parts.append(thinking)
        elif len(thinking.strip()) <= 1:
            self._thinking_redacted.pop(idx, None)
            self._thinking_start.pop(idx, None)
            self._thinking_end.pop(idx, None)
            return
        else:
            self.thinking_parts.append(thinking)
        self._thinking_redacted.pop(idx, None)
        self._thinking_start.pop(idx, None)
        self._thinking_end.pop(idx, None)
        if synthesized and thinking and self.thinking_callback:
            self.thinking_callback(thinking)
        if self.block_callback and thinking:
            self.block_callback("thinking_content", {"text": thinking})

    def _flush_all_thinking_blocks(self) -> None:
        keys = set(self._thinking_block_bufs) | set(self._thinking_redacted)
        for idx in sorted(keys):
            self._flush_thinking_block(idx)


    def _emit_turn_callback(self) -> None:
        if self._turn_callback_sent or not self.turn_callback:
            return
        text = "" if self.block_callback else "".join(self.text_parts).strip()
        thinking = "" if self.block_callback else "".join(self.thinking_parts)
        tool_calls = [] if self.block_callback else [
            dict(tc) for tc in self.turn_tool_calls]
        if thinking and tool_calls:
            tool_calls[0]["thinking"] = thinking
        if not text and not thinking and not tool_calls:
            self._turn_callback_sent = True
            return
        self.turn_callback(text, tool_calls, thinking)
        self._turn_callback_sent = True

    def _emit_tool_use(self, idx: int) -> None:
        block = self.tool_blocks.get(idx) or {}
        if not block or block.get("emitted"):
            return
        tool_id = block.get("id") or f"cci_{uuid.uuid4().hex[:12]}"
        raw = block.get("json", "") or "{}"
        args = _loads_tolerant(raw)
        display_name, display_args = normalize_observed_tool(block.get("name", ""), args)
        block["display_name"] = display_name
        block["display_args"] = display_args
        if not display_args and str(block.get("name") or "").strip():
            logger.warning(
                "[cci-args-debug] STREAM empty args: raw_name=%r raw_json=%r display_name=%r",
                block.get("name", ""), (raw or "")[:400], display_name)
        block["hidden"] = (
            is_hidden_native_tool(block.get("name", ""), args)
            or is_hidden_native_tool(display_name, display_args)
        )
        block["emitted"] = True
        if tool_id in self.emitted_tool_use_ids:
            self._emit_pending_tool_results(tool_id)
            return
        self.emitted_tool_use_ids.add(tool_id)
        if not block.get("hidden"):
            self._remember_turn_tool_call(tool_id, display_name, display_args)
        block["tool_origin"] = observed_tool_origin(block.get("name", ""))
        if self.block_callback and not block.get("hidden"):
            self.block_callback("tool_use", {
                "id": tool_id,
                "name": display_name,
                "arguments": display_args,
                "tool_origin": block["tool_origin"],
            })
        self._emit_pending_tool_results(tool_id)

    def _emit_pending_tool_uses(self) -> None:
        for idx in list(self.tool_blocks):
            self._emit_tool_use(idx)

    def _emit_tool_result(self, event: dict) -> None:
        tc_id = event.get("tool_use_id", "") or ""
        if not tc_id:
            return
        if tc_id in self.emitted_tool_result_ids:
            return
        block = self.tool_by_id.get(tc_id) or {}
        if not block.get("emitted"):
            self.pending_tool_results.setdefault(tc_id, []).append(dict(event))
            return
        self._emit_tool_result_now(event, block)

    def _emit_observed_tool_use(self, event: dict) -> None:
        tc_id = event.get("tool_use_id", "") or event.get("id", "") or ""
        if not tc_id:
            return
        block = self.tool_by_id.get(tc_id)
        if block is None:
            args = _event_tool_args(event)
            block = {
                "id": tc_id,
                "name": event.get("name", ""),
                "json": json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False),
                "emitted": False,
                "hidden": is_hidden_native_tool(event.get("name", ""), args if isinstance(args, dict) else {}),
            }
            self.tool_by_id[tc_id] = block
        if block.get("emitted") or tc_id in self.emitted_tool_use_ids:
            block["emitted"] = True
            self._emit_pending_tool_results(tc_id)
            return
        block["emitted"] = True
        self.emitted_tool_use_ids.add(tc_id)
        args = _loads_tolerant(block.get("json", "") or "{}")
        display_name, display_args = normalize_observed_tool(block.get("name", ""), args)
        block["display_name"] = display_name
        block["display_args"] = display_args
        if not display_args and str(block.get("name") or "").strip():
            logger.warning(
                "[cci-args-debug] OBSERVED empty args: raw_name=%r event=%r block_json=%r display_name=%r",
                block.get("name", ""),
                {k: event.get(k) for k in ("name", "arguments", "input", "tool_input")},
                (block.get("json") or "")[:400], display_name)
        block["hidden"] = (
            is_hidden_native_tool(block.get("name", ""), args)
            or is_hidden_native_tool(display_name, display_args)
        )
        # Prefer the MITM's tool_origin field (computed from the RAW name before
        # unwrapping). Fall back to classifying the observed name: native tools
        # keep their own name (-> native); MCP wrappers are unwrapped to a
        # display name, but the updated MITM always sends tool_origin for those.
        block["tool_origin"] = (
            event.get("tool_origin", "")
            or observed_tool_origin(block.get("name", "")))
        if self.block_callback and not block.get("hidden"):
            self.block_callback("tool_use", {
                "id": tc_id,
                "name": display_name,
                "arguments": display_args,
                "tool_origin": block["tool_origin"],
            })
        if not block.get("hidden"):
            self._remember_turn_tool_call(tc_id, display_name, display_args)
        self._emit_pending_tool_results(tc_id)

    def _remember_turn_tool_call(self, tc_id: str, name: str, args: dict) -> None:
        if not tc_id:
            return
        entry = {"id": tc_id, "name": name or "", "arguments": args or {}}
        for idx, existing in enumerate(self.turn_tool_calls):
            if existing.get("id") == tc_id:
                existing_result = existing.get("result")
                if existing_result is not None:
                    entry["result"] = existing_result
                self.turn_tool_calls[idx] = entry
                return
        self.turn_tool_calls.append(entry)

    def _emit_pending_tool_results(self, tc_id: str) -> None:
        if not tc_id:
            return
        block = self.tool_by_id.get(tc_id) or {}
        if not block.get("emitted"):
            return
        pending = self.pending_tool_results.pop(tc_id, [])
        for event in pending:
            self._emit_tool_result_now(event, block)

    def _emit_tool_result_now(self, event: dict, block: dict) -> None:
        tc_id = event.get("tool_use_id", "") or ""
        if not tc_id or tc_id in self.emitted_tool_result_ids:
            return
        self.emitted_tool_result_ids.add(tc_id)
        result = event.get("content", "") or "(no output)"
        if not block.get("hidden"):
            for tc in self.turn_tool_calls:
                if tc.get("id") == tc_id:
                    tc["result"] = result
                    break
        if self.block_callback and not block.get("hidden"):
            display_name = block.get("display_name") or block.get("name", "")
            self.block_callback("tool_result", {
                "tc_id": tc_id,
                "tool": display_name,
                "result": result,
                "tool_origin": block.get("tool_origin", ""),
            })
