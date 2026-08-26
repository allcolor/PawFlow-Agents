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


# Below this, an echoed result is not worth a marker: the marker costs about
# as much as the bytes it replaces, and a short string ('ok', a bare count)
# is exactly the kind that collides with unrelated prose by accident.
_MIN_ECHOED_RESULT_CHARS = 40


def elide_echoed_results(result: str, echoed: list) -> str:
    """Drop from a script's output only what the rows beside it already say.

    A code-mode script prints what its calls returned, and the relay published
    each of those results as a row of its own -- the same bytes twice in the
    next context. But a script also computes: it compares two files, counts
    what it read, derives a verdict. Replacing the whole output on the sole
    ground that the script made a call threw that away, and nobody else was
    holding it.

    So the elision is per fragment, not per output. What a row already carries
    verbatim is replaced by a pointer to that row; everything else -- the
    script's own words -- is kept exactly as it was printed. A script whose
    output quotes none of its results is returned untouched: that output is
    not a copy of anything.
    """
    remaining = str(result)
    for index, text in enumerate(echoed, start=1):
        text = str(text or "")
        if len(text) < _MIN_ECHOED_RESULT_CHARS or text not in remaining:
            continue
        remaining = remaining.replace(
            text, f"<result of call {index}, {len(text)} chars — "
                  f"the row beside this one>")
    return remaining


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
        # Code-mode call id -> how many MCP rows existed when it was emitted.
        # The relay's rows for a script all arrive before that script's own
        # result, so the difference at result time is what this script did.
        self._code_mode_calls: dict = {}
        # Relay rows in emission order, each mapped to what it returned.
        # Keyed by row, not counted: a Responses call item is observed twice
        # -- streamed, then replayed in the next request's input -- and the
        # base class renders the second observation onto the first row. A
        # counter bumped after that call counted the re-read as a new call,
        # and the next script paid for a row it never produced. Ordered
        # (dict, 3.7+) because a script answers only for the rows that
        # appeared while it ran; valued because eliding its output needs to
        # know what those rows actually say.
        self._mcp_row_results: dict = {}

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
        #
        # Responses includes cache hits inside input_tokens. Keep that gross
        # value for the gauge, but split it before usage accounting just like
        # the regular OpenAI Responses provider does. Otherwise a tool-heavy
        # Codex turn displays (and prices) the same cached prefix as ordinary
        # input once per exchange.
        self._record_context_tokens(usage)

        try:
            prompt_total = max(
                0, int((usage or {}).get("input_tokens", 0) or 0))
        except (TypeError, ValueError):
            prompt_total = 0
        try:
            cached = max(0, int(
                ((usage or {}).get("input_tokens_details") or {}).get(
                    "cached_tokens", 0) or 0))
        except (AttributeError, TypeError, ValueError):
            cached = 0
        # Defensive clamp: malformed provider usage must never create negative
        # ordinary input or more cache reads than prompt tokens.
        cached = min(cached, prompt_total)
        uncached = prompt_total - cached
        if uncached:
            self.usage["input_tokens"] = int(
                self.usage.get("input_tokens", 0) or 0) + uncached
        if cached:
            self.usage["cached_input_tokens"] = int(
                self.usage.get("cached_input_tokens", 0) or 0) + cached

        for key in ("output_tokens", "total_tokens"):
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
        is_body = code_mode_body(_event_tool_args(event))
        if is_body:
            self.event_service.mark_code_mode(self.session_token)
        super()._emit_observed_tool_use(event)
        # After the emit: the base class is what resolves aliases to the id the
        # row was drawn with, and that is the id the result will arrive under.
        tc_id = str(event.get("tool_use_id", "") or event.get("id", "") or "")
        block = self.tool_by_id.get(tc_id) or {}
        row_id = str(block.get("id") or tc_id)
        if not row_id:
            return
        if is_body:
            self._code_mode_calls.setdefault(
                row_id, len(self._mcp_row_results))
        elif block.get("tool_origin") == "mcp":
            # setdefault, not append: the second observation of the same call
            # is the same row, and it must not count twice.
            self._mcp_row_results.setdefault(row_id, "")

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
        # The full body is deliberately discarded, but the live block callback
        # still needs to recognize every paginated read of initial_context.md.
        # Preserve only that non-sensitive fact so the linked result can never
        # be counted or serialized into the next cold bootstrap.
        from tasks.ai.context_usage_cache import _is_cli_bootstrap_read
        if _is_cli_bootstrap_read({"arguments": args}):
            elided["_pawflow_bootstrap_read"] = True
        return elided

    def _displayable_result(self, block: dict, result: str) -> str:
        """Keep the code body's result, drop what the rows beside it already say.

        Eliding the body fixed half the duplication. The other half is what the
        script printed: a code-mode script's output quotes the very calls the
        relay reported one by one, so those bytes reached the next context
        twice -- once as each call's own tool result, once more inside the
        script's. Invisible while the session is warm (Codex never sees the
        relay's rows, only its own script and its output), and paid for real at
        every cold start, which is to say at every compaction restart: exactly
        when the window is already full.

        Only the quoted bytes, and only the rows THIS script produced. A
        script that reached no PawFlow tool -- it read a file with Codex's own
        runtime, or computed something and printed it -- is described by
        nobody else. Neither is the part of a script's output it derived
        rather than echoed, so that part stays even when the calls beside it
        do not.

        This is also where a relay row's own result is recorded: the rows for
        a script all land before the script's own result, so by the time the
        aggregate arrives its pieces are known.
        """
        row_id = str((block or {}).get("id") or "")
        before = self._code_mode_calls.get(row_id)
        if before is None:
            if row_id in self._mcp_row_results:
                self._mcp_row_results[row_id] = str(result)
            return result
        echoed = list(self._mcp_row_results.values())[before:]
        if not echoed:
            return result
        return elide_echoed_results(result, echoed)

    def run(self, abort_event=None):
        from core.llm_client import CCCompactDetected, LLMResponse

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
                    if (_NO_PROXY_EVENT_TIMEOUT_SECONDS > 0
                            and time.time() - started_at
                            >= _NO_PROXY_EVENT_TIMEOUT_SECONDS):
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
                hook_name = event.get("hook_event_name", "")
                if hook_name in {"PreCompact", "PostCompact"}:
                    logger.warning(
                        "[codex-interactive] %s detected — rejecting native "
                        "compaction and handing context to PawFlow",
                        hook_name)
                    raise CCCompactDetected(
                        f"Codex interactive {hook_name} hook detected")
                if hook_name == "Stop":
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
        cached_tokens = int(
            self.usage.get("cached_input_tokens", 0) or 0)
        return LLMResponse(
            content=text, tool_calls=[], tokens_in=tokens_in,
            tokens_out=tokens_out,
            total_tokens=(total or tokens_in + cached_tokens + tokens_out),
            cache_read_tokens=cached_tokens,
            thinking="".join(self.thinking_parts),
            model=self.effective_model,
            raw={
                "provider": "codex-interactive",
                "usage": self.usage,
                "effective_model": self.effective_model,
                "lifecycle_events": self.lifecycle_events,
            })
