"""AG-UI protocol runtime: run published PawFlow agents as AG-UI servers.

AG-UI (https://github.com/ag-ui-protocol/ag-ui) is an open, event-based
protocol connecting agent backends to user-facing applications: the client
POSTs one RunAgentInput and reads back an SSE stream of camelCase JSON
events (RUN_STARTED, TEXT_MESSAGE_*, TOOL_CALL_*, RUN_FINISHED, ...).

PawFlow exposes agents through the existing A2A publications — same Bearer
keys, same per-client context resolution (the AG-UI ``threadId`` is the A2A
context id) — so one "publish agent" action serves both protocols. This
module maps one RunAgentInput to one PawFlow agent turn and translates the
turn's live conversation-bus events into the AG-UI event stream.

Frontend-declared tools (RunAgentInput.tools) are surfaced to the agent as
context text; PawFlow executes its own server-side tools and streams them
as TOOL_CALL_* / TOOL_CALL_RESULT events. A structured frontend-tool
round-trip (run finishing on a client tool call) is not implemented yet.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from typing import Any, Dict, Iterator, List, Tuple

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 200_000
# SSE comment ping while the turn is silent (long tool runs), so proxies
# and clients keep the connection open. Comments are invisible to SSE
# parsers, so the AG-UI client never sees them as events.
_KEEPALIVE_SECONDS = 15.0


def sse_frame(event: Dict[str, Any]) -> bytes:
    """One AG-UI SSE frame: ``data: {json}\\n\\n`` (camelCase, no nulls)."""
    return ("data: " + json.dumps(event, ensure_ascii=False,
                                  separators=(",", ":")) + "\n\n").encode("utf-8")


def agui_event(event_type: str, **fields: Any) -> Dict[str, Any]:
    """Build one AG-UI event dict. ``fields`` keys are already camelCase;
    ``None`` values are dropped (the protocol serializes without nulls)."""
    event: Dict[str, Any] = {"type": event_type,
                             "timestamp": int(time.time() * 1000)}
    for key, value in fields.items():
        if value is not None:
            event[key] = value
    return event


def _content_text(content: Any) -> str:
    """Flatten an AG-UI user message content (str or multimodal parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "")
            if kind == "text":
                chunks.append(str(part.get("text") or ""))
            else:
                source = part.get("source") or {}
                url = ""
                if isinstance(source, dict) and source.get("type") == "url":
                    url = str(source.get("value") or "")
                label = f"[AG-UI {kind} attachment"
                label += f": {url}]" if url else " (inline payload not supported)]"
                chunks.append(label)
        return "\n".join(chunk for chunk in chunks if chunk)
    return "" if content is None else str(content)


def extract_run(run_input: Dict[str, Any]) -> Tuple[str, str, str]:
    """Validate a RunAgentInput and return ``(thread_id, run_id, prompt)``.

    AG-UI clients send the full message history on every run; PawFlow keeps
    its own durable context per thread, so only the LAST user message is
    forwarded as the new prompt. ``context`` entries and frontend ``tools``
    declarations are appended as text so the agent knows about them.
    """
    if not isinstance(run_input, dict):
        raise ValueError("RunAgentInput must be a JSON object")
    thread_id = str(run_input.get("threadId") or "").strip()
    if not thread_id:
        raise ValueError("threadId is required")
    run_id = str(run_input.get("runId") or "").strip() or (
        "agui_" + uuid.uuid4().hex)
    messages = run_input.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    last_user = next(
        (m for m in reversed(messages)
         if isinstance(m, dict) and str(m.get("role") or "") == "user"),
        None)
    if last_user is None:
        raise ValueError("RunAgentInput carries no user message")
    text = _content_text(last_user.get("content")).strip()
    if not text:
        raise ValueError("The user message has no textual content")
    parts: List[str] = [text]
    context = run_input.get("context")
    if isinstance(context, list) and context:
        lines = []
        for entry in context:
            if isinstance(entry, dict) and (entry.get("value") or ""):
                description = str(entry.get("description") or "context")
                lines.append(f"- {description}: {entry.get('value')}")
        if lines:
            parts.append("[AG-UI context]\n" + "\n".join(lines))
    tools = run_input.get("tools")
    if isinstance(tools, list) and tools:
        names = []
        for tool in tools:
            if isinstance(tool, dict) and (tool.get("name") or ""):
                names.append(f"- {tool.get('name')}: {tool.get('description') or ''}")
        if names:
            parts.append(
                "[AG-UI frontend tools declared by the client (informational; "
                "you cannot call them directly)]\n" + "\n".join(names))
    prompt = "\n\n".join(parts)
    if len(prompt) > _MAX_TEXT_CHARS:
        raise ValueError(f"AG-UI prompt exceeds {_MAX_TEXT_CHARS} characters")
    return thread_id, run_id, prompt


class _TurnTranslator:
    """Translate PawFlow conversation-bus events into AG-UI events.

    Stateful over one run: it opens/closes TEXT_MESSAGE and THINKING
    blocks so the stream always pairs START/END, and dedupes the final
    persisted ``new_message`` against the text already streamed as
    ``token`` deltas (both carry the same ``msg_id``).
    """

    def __init__(self) -> None:
        self._text_msg_id = ""
        self._thinking_open = False
        self._streamed_ids: set = set()

    def close_open_blocks(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if self._thinking_open:
            out.append(agui_event("THINKING_TEXT_MESSAGE_END"))
            out.append(agui_event("THINKING_END"))
            self._thinking_open = False
        if self._text_msg_id:
            out.append(agui_event("TEXT_MESSAGE_END",
                                  messageId=self._text_msg_id))
            self._streamed_ids.add(self._text_msg_id)
            self._text_msg_id = ""
        return out

    def _close_thinking(self) -> List[Dict[str, Any]]:
        if not self._thinking_open:
            return []
        self._thinking_open = False
        return [agui_event("THINKING_TEXT_MESSAGE_END"),
                agui_event("THINKING_END")]

    def translate(self, event_type: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if event_type == "token":
            delta = str(data.get("text") or "")
            msg_id = str(data.get("msg_id") or "")
            if not delta or not msg_id:
                return out
            out.extend(self._close_thinking())
            if self._text_msg_id and self._text_msg_id != msg_id:
                out.extend(self.close_open_blocks())
            if not self._text_msg_id:
                self._text_msg_id = msg_id
                out.append(agui_event("TEXT_MESSAGE_START", messageId=msg_id,
                                      role="assistant"))
            out.append(agui_event("TEXT_MESSAGE_CONTENT", messageId=msg_id,
                                  delta=delta))
            return out
        if event_type in ("thinking_delta", "thinking"):
            delta = str(data.get("text") or "")
            if not delta:
                # Heartbeat "thinking" events carry no text — not reasoning.
                return out
            if not self._thinking_open:
                self._thinking_open = True
                out.append(agui_event("THINKING_START"))
                out.append(agui_event("THINKING_TEXT_MESSAGE_START"))
            out.append(agui_event("THINKING_TEXT_MESSAGE_CONTENT", delta=delta))
            return out
        if event_type == "tool_call":
            out.extend(self.close_open_blocks())
            tc_id = str(data.get("tc_id") or "") or ("tc_" + uuid.uuid4().hex[:12])
            name = str(data.get("tool") or "tool")
            arguments = data.get("arguments")
            try:
                args_json = json.dumps(arguments or {}, ensure_ascii=False)
            except (TypeError, ValueError):
                args_json = "{}"
            out.append(agui_event("TOOL_CALL_START", toolCallId=tc_id,
                                  toolCallName=name,
                                  parentMessageId=str(data.get("msg_id") or "") or None))
            out.append(agui_event("TOOL_CALL_ARGS", toolCallId=tc_id,
                                  delta=args_json))
            out.append(agui_event("TOOL_CALL_END", toolCallId=tc_id))
            return out
        if event_type == "tool_result":
            tc_id = str(data.get("tc_id") or "")
            if not tc_id:
                return out
            out.append(agui_event(
                "TOOL_CALL_RESULT",
                messageId=str(data.get("msg_id") or "") or ("msg_" + uuid.uuid4().hex[:12]),
                toolCallId=tc_id,
                content=str(data.get("result") or ""),
                role="tool"))
            return out
        if event_type == "new_message":
            if str(data.get("role") or "") != "assistant":
                return out
            msg_id = str(data.get("msg_id") or "") or ("msg_" + uuid.uuid4().hex[:12])
            content = str(data.get("content") or "")
            if msg_id == self._text_msg_id:
                # The persisted row for the message currently streaming:
                # its content already went out as token deltas.
                out.extend(self.close_open_blocks())
                return out
            if msg_id in self._streamed_ids or not content:
                return out
            out.extend(self.close_open_blocks())
            self._streamed_ids.add(msg_id)
            out.append(agui_event("TEXT_MESSAGE_START", messageId=msg_id,
                                  role="assistant"))
            out.append(agui_event("TEXT_MESSAGE_CONTENT", messageId=msg_id,
                                  delta=content))
            out.append(agui_event("TEXT_MESSAGE_END", messageId=msg_id))
            return out
        return out


def _ensure_isolated_conversation(publication: Dict[str, Any],
                                  context: Dict[str, Any]) -> None:
    from core.a2a_runtime import _ensure_isolated_conversation as _ensure
    _ensure(publication, context)


def run_agent_stream(publication: Dict[str, Any], key: Dict[str, Any],
                     run_input: Dict[str, Any]) -> Iterator[bytes]:
    """Yield the AG-UI SSE frames of one published-agent run.

    The first frame is always emitted (RUN_STARTED or RUN_ERROR); every
    failure after that becomes a RUN_ERROR frame instead of an exception,
    because the HTTP status has already been sent.
    """
    from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI
    from core.a2a_store import A2AStore

    try:
        if not publication.get("enabled"):
            raise PermissionError("This publication is disabled")
        thread_id, run_id, prompt = extract_run(run_input)
    except (PermissionError, ValueError) as exc:
        yield sse_frame(agui_event("RUN_ERROR", message=str(exc),
                                   code="invalid_input"))
        return

    yield sse_frame(agui_event("RUN_STARTED", threadId=thread_id,
                               runId=run_id))

    events: "queue.Queue[Tuple[str, Any, Any]]" = queue.Queue()

    def _live(_cid: str, event_type: str, data: Any) -> None:
        events.put(("evt", event_type, data))

    try:
        store = A2AStore.instance()
        context = store.resolve_context(publication, key["key_id"],
                                        "agui_" + thread_id)
        _ensure_isolated_conversation(publication, context)
        conversation_id = context["internal_conversation_id"]
        turn_id = "agui:" + uuid.uuid4().hex
        source = {
            "type": "a2a",
            "name": "AG-UI client",
            "target_agent": publication["agent_name"],
            "visibility": "target_only",
            "publication_id": publication["publication_id"],
            "context_id": context["context_id"],
            "agui_thread_id": thread_id,
            "agui_run_id": run_id,
        }
        AgentRuntimeAPI.submit_message(AgentRequest(
            user_id=publication["owner_user_id"],
            conversation_id=conversation_id,
            target_agent=publication["agent_name"],
            message=prompt,
            msg_id=turn_id,
            channel="agui",
            source_attributes={"message_source": json.dumps(source)},
            live_callback=_live,
        ))
    except Exception as exc:
        logger.exception("AG-UI run submission failed")
        yield sse_frame(agui_event("RUN_ERROR", message=str(exc),
                                   code="submission_failed"))
        return

    def _wait_done() -> None:
        try:
            result = AgentRuntimeAPI.wait_for_done(conversation_id, turn_id)
        except Exception as exc:  # pragma: no cover - defensive
            events.put(("error", "", str(exc)))
            return
        events.put(("done", "", result))

    waiter = threading.Thread(target=_wait_done, daemon=True,
                              name=f"agui-run-{run_id[:16]}")
    waiter.start()

    translator = _TurnTranslator()
    try:
        while True:
            try:
                kind, event_type, data = events.get(timeout=_KEEPALIVE_SECONDS)
            except queue.Empty:
                # SSE comment: keeps the connection alive, invisible to the
                # client's event parser.
                yield b": ping\n\n"
                continue
            if kind == "evt":
                if not isinstance(data, dict):
                    continue
                for event in translator.translate(str(event_type), data):
                    yield sse_frame(event)
                continue
            if kind == "error":
                for event in translator.close_open_blocks():
                    yield sse_frame(event)
                yield sse_frame(agui_event("RUN_ERROR", message=str(data),
                                           code="run_failed"))
                return
            # kind == "done"
            result = data
            for event in translator.close_open_blocks():
                yield sse_frame(event)
            if result is None:
                yield sse_frame(agui_event(
                    "RUN_ERROR", code="run_lost",
                    message="The agent turn ended without a final event"))
                return
            if getattr(result, "error", ""):
                yield sse_frame(agui_event("RUN_ERROR", message=result.error,
                                           code="agent_error"))
                return
            yield sse_frame(agui_event(
                "RUN_FINISHED", threadId=thread_id, runId=run_id,
                result=getattr(result, "response", "") or "",
                outcome={"type": "success"}))
            return
    except GeneratorExit:
        # Client disconnected mid-run. An isolated context is private to
        # this AG-UI client, so its turn can be stopped safely; a shared
        # conversation may be serving other users and is left running.
        if publication.get("context_policy") == "isolated":
            try:
                from tasks.ai.agent_loop import AgentLoopTask
                AgentLoopTask.force_stop_agent(
                    conversation_id, publication["agent_name"])
            except Exception:
                logger.warning("Could not force-stop AG-UI run on disconnect",
                               exc_info=True)
        raise
