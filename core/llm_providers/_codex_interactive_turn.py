"""Turn coordinator for MITM-observed Codex Responses API streams."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

from core.llm_providers._cci_turn import (
    _CCITurnCoordinator, _event_tool_args, _NO_PROXY_EVENT_TIMEOUT_SECONDS,
    _POST_STOP_IDLE_DRAIN_SECONDS)
from tools.cc_interactive_filters import (
    code_mode_body, observed_call_ids, observed_call_item)

logger = logging.getLogger(__name__)


def observed_context_tokens(usage) -> int:
    """Return the prompt size one observed Responses exchange reported.

    This is the context gauge measured where it is exact: on the wire. Codex
    owns its context window -- its own system prompt, its tool schemas, and
    everything it has read since the session started, none of which PawFlow
    can enumerate. Rebuilding a gauge from the messages PawFlow knows about
    estimates the wrong set, and when Codex reads its context from inside a
    code body PawFlow knows about none of it, so the estimate was 0.

    ``input_tokens`` already includes the cached prefix (Responses details it
    separately under ``input_tokens_details.cached_tokens``); adding that back
    would count the cache twice. Returns 0 when no input side was reported,
    so the caller keeps its previous measurement instead of dropping to zero.
    """
    if not isinstance(usage, dict) or "input_tokens" not in usage:
        return 0
    try:
        return max(0, int(usage.get("input_tokens") or 0))
    except (TypeError, ValueError):
        return 0


class _CodexInteractiveTurnCoordinator(_CCITurnCoordinator):
    """Assemble one Codex TUI turn from proxy and lifecycle events.

    A single TUI turn can contain several ``/responses`` exchanges around MCP
    calls. A Responses terminal event closes one exchange; the Codex ``Stop``
    hook closes the user-visible turn.
    """

    def __init__(self, *args, context_tokens_callback=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Prompt size of the LAST observed exchange -- the context gauge.
        # Last, not max: a compaction inside Codex shrinks the window and the
        # gauge has to follow it down.
        self.observed_context_tokens = 0
        self.context_tokens_callback = context_tokens_callback

    def _record_context_tokens(self, usage) -> None:
        measured = observed_context_tokens(usage)
        if measured <= 0:
            return
        self.observed_context_tokens = measured
        if self.context_tokens_callback:
            try:
                self.context_tokens_callback(measured)
            except Exception:
                logger.debug(
                    "observed context gauge callback failed", exc_info=True)

    def _merge_usage(self, usage: dict) -> None:
        # Cost accounting sums every exchange of the turn. The context gauge
        # must NOT be read from that sum: it is the size of one prompt, and
        # the last exchange is the one that says how full the window is now.
        self._record_context_tokens(usage)
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            try:
                value = int((usage or {}).get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                self.usage[key] = int(self.usage.get(key, 0) or 0) + value

    def _emit_observed_tool_use(self, event: dict) -> None:
        """Render every observed call, and arm the relay on a code body.

        A code-mode harness calls nothing directly: it runs one script in a
        freeform tool and drives everything from inside it. Flagging the
        session makes the relay report the calls it executes for that script,
        with the name, arguments and result it really used.

        The body itself still draws its row. Hiding it hid more than the
        noise it was meant to remove: a script does not have to reach a tool
        through PawFlow, and what it runs with Codex's own runtime -- reading
        the bootstrap context is the first thing it does -- is executed by no
        relay and reported by nobody. Suppressed here, those calls existed in
        no view at all. The row is coarse, but it is the only evidence that
        the turn ran anything, and the relay's rows name the rest.
        """
        if code_mode_body(_event_tool_args(event)):
            self.event_service.mark_code_mode(self.session_token)
        super()._emit_observed_tool_use(event)

    def _displayable_args(self, name: str, args: dict) -> dict:
        """Keep the code body's row, drop the body.

        The script is plumbing twice over. On screen it is the aggregator the
        eye lands on -- ``exec(const r=await tools.mcp__pawflow__use_tool...)``
        in front of every group of calls -- while the calls it actually made
        are the rows worth reading, and the relay already published each of
        them by name. In the record it is worse: PawFlow persists a call's
        arguments and replays them into the next context, so kilobytes of
        generated JavaScript come back at every bootstrap, describing work
        that is already described by the rows around it.

        The row stays -- it is the only evidence the turn ran a script, and
        what the script did with Codex's own runtime is reported by nobody
        else -- but it says how big the body was instead of quoting it.
        """
        if not code_mode_body(args):
            return args
        source = (args or {}).get("input")
        elided = dict(args or {})
        elided["input"] = f"<code-mode script, {len(str(source))} chars>"
        return elided

    def run(self, abort_event=None):
        from core.llm_client import LLMResponse

        if self._consumer_refused:
            logger.info(
                "[codex-interactive] session=%s already has an event consumer",
                self.session_token[:8])
            return LLMResponse(content="", tool_calls=[])

        started_at = time.time()
        while True:
            if abort_event is not None and abort_event.is_set():
                raise RuntimeError("codex-interactive aborted")
            timeout = 0.05 if self._stop_seen else 0.25
            event = self._wait_event(timeout)
            if not event:
                if not self._saw_proxy_event:
                    if time.time() - started_at >= _NO_PROXY_EVENT_TIMEOUT_SECONDS:
                        raise RuntimeError(
                            "Codex interactive produced no observed proxy events "
                            "after tmux prompt submit")
                elif (self._stop_seen and
                      time.time() - self._post_stop_last_event_at >=
                      _POST_STOP_IDLE_DRAIN_SECONDS):
                    self._finish_turn_if_ready()
                    break
                continue

            if self.touch_callback:
                self.touch_callback()
            now = time.time()
            self._last_event_at = now
            if not self._first_event_at:
                self._first_event_at = now
            if self._stop_seen:
                self._post_stop_last_event_at = now

            etype = event.get("type", "")
            if etype == "request_error":
                self._saw_proxy_event = True
                raise RuntimeError(event.get(
                    "error", "Codex interactive proxy request failed"))
            if etype == "request_start":
                self._saw_proxy_event = True
                path = event.get("path", "") or ""
                if (urlsplit(path).path.rstrip("/").endswith("/responses")
                        and not event.get("ignore_reason")
                        and self._stop_seen):
                    logger.info(
                        "[codex-interactive] new Responses request after Stop; "
                        "turn continues (session=%s)",
                        self.session_token[:8])
                    self._stop_seen = False
                continue
            if etype in {"request_stop", "response_start",
                         "response_ignored"}:
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
                if event.get("hook_event_name") == "Stop":
                    self._stop_seen = True
                    self._post_stop_last_event_at = time.time()
                continue
            if etype != "sse":
                continue

            self._saw_proxy_event = True
            payload = event.get("payload") or {}
            ptype = payload.get("type") or event.get("event", "")
            try:
                output_index = int(payload.get("output_index", 0) or 0)
            except (TypeError, ValueError):
                output_index = 0

            if ptype in {"response.created", "response.in_progress"}:
                if ptype == "response.created":
                    self._finalize_message_text()
                response = payload.get("response") or {}
                if response.get("model"):
                    self.effective_model = str(response["model"])
                continue
            if ptype == "response.output_text.delta":
                delta = payload.get("delta", "") or ""
                if delta and not self._first_model_content_at:
                    self._first_model_content_at = time.time()
                self._append_text(delta, output_index)
                continue
            if ptype in {"response.reasoning_text.delta",
                         "response.reasoning_summary_text.delta"}:
                self._append_thinking(
                    payload.get("delta", "") or "", output_index)
                continue
            if ptype == "response.output_item.done":
                item = payload.get("item") or {}
                # Every `*_call` item is a tool call, not only `function_call`:
                # Codex runs its own shell as a `local_shell_call` and its
                # freeform tools as a `custom_tool_call`. Matching one literal
                # showed the user a turn with no tools while the model was
                # visibly running them.
                call = observed_call_item(item)
                if call:
                    call_id, name, args = call
                    self._emit_observed_tool_use({
                        "tool_use_id": call_id,
                        "alias_ids": list(observed_call_ids(item)),
                        "name": name,
                        "arguments": args,
                    })
                elif str(item.get("type") or "").endswith("_call"):
                    logger.warning(
                        "[codex-interactive] tool call item without an id: "
                        "type=%s keys=%s", item.get("type"),
                        sorted(item.keys())[:8])
                continue
            if ptype in {"response.completed", "response.incomplete",
                         "response.failed"}:
                response = payload.get("response") or {}
                self._merge_usage(response.get("usage") or {})
                if response.get("model"):
                    self.effective_model = str(response["model"])
                self._flush_all_text_blocks()
                self._flush_all_thinking_blocks()
                self._finalize_message_text()
                if ptype == "response.failed":
                    error = response.get("error") or {}
                    detail = (error.get("message") if isinstance(error, dict)
                              else str(error or ""))
                    raise RuntimeError(
                        detail or "Codex Responses request failed")

        self._finalize_message_text()
        text = self._last_message_text
        total = int(self.usage.get("total_tokens", 0) or 0)
        tokens_in = int(self.usage.get("input_tokens", 0) or 0)
        tokens_out = int(self.usage.get("output_tokens", 0) or 0)
        return LLMResponse(
            content=text, tool_calls=[], tokens_in=tokens_in,
            tokens_out=tokens_out, total_tokens=total or tokens_in + tokens_out,
            thinking="".join(self.thinking_parts),
            model=self.effective_model,
            raw={
                "provider": "codex-interactive",
                "usage": self.usage,
                "effective_model": self.effective_model,
                "lifecycle_events": self.lifecycle_events,
            })
