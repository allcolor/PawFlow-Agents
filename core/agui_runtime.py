"""AG-UI protocol runtime: run published PawFlow agents as AG-UI servers.

AG-UI (https://github.com/ag-ui-protocol/ag-ui) is an open, event-based
protocol connecting agent backends to user-facing applications: the client
POSTs one RunAgentInput and reads back an SSE stream of camelCase JSON
events (RUN_STARTED, TEXT_MESSAGE_*, TOOL_CALL_*, RUN_FINISHED, ...).

PawFlow exposes agents through the existing A2A publications — same Bearer
keys, same per-client context resolution (the AG-UI ``threadId`` maps to a
deterministic per-key A2A context) — so one "publish agent" action serves
both protocols. This module maps one RunAgentInput to one PawFlow agent
turn and translates the turn's live conversation-bus events into the AG-UI
event stream.

On isolated publications the full interactive protocol is supported:

- **Frontend tools** (``RunAgentInput.tools``) are declared to the agent as
  real callable tools (see ``core.agui_tools``); a call streams as
  ``TOOL_CALL_*`` events, executes in the client, and the client sends the
  result back as a ``role:"tool"`` message in the next run.
- **Shared state**: ``RunAgentInput.state`` seeds the thread state, a
  ``STATE_SNAPSHOT`` opens every run, and the agent's ``agui_state`` tool
  streams ``STATE_SNAPSHOT`` / ``STATE_DELTA`` (RFC 6902) live.
- **Interrupts**: the agent's ``agui_interrupt`` tool pauses the run —
  ``RUN_FINISHED`` carries an interrupt outcome and the client answers in
  the next run's ``resume`` array.

Shared (non-isolated) publications keep the plain chat behavior: frontend
tools and context are surfaced as text only, state and interrupts are off —
the underlying conversation belongs to the owner and must not grow
client-declared tools.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import threading
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 200_000
_MAX_PROPS_CHARS = 8_000
# SSE comment ping while the turn is silent (long tool runs), so proxies
# and clients keep the connection open. Comments are invisible to SSE
# parsers, so the AG-UI client never sees them as events.
_KEEPALIVE_SECONDS = 15.0

_UNSET = object()


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


def _attachment_filename(kind: str, mime: str, index: int) -> str:
    extension = mimetypes.guess_extension(mime or "") or ".bin"
    return f"agui_{kind or 'file'}_{index}{extension}"


def _flatten_user_content(content: Any, attachments: List[Dict[str, Any]]) -> str:
    """Flatten an AG-UI user message content (str or multimodal parts).

    Inline base64 parts (``source.type == "data"``) become PawFlow
    attachments; URL parts stay a text label (the agent can fetch them).
    """
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
                continue
            source = part.get("source") or {}
            if not isinstance(source, dict):
                source = {}
            if source.get("type") == "data" and source.get("value"):
                mime = str(source.get("mime_type")
                           or source.get("mimeType") or "application/octet-stream")
                attachments.append({
                    "filename": _attachment_filename(kind, mime,
                                                     len(attachments) + 1),
                    "mime_type": mime,
                    "data": str(source.get("value")),
                })
                continue
            url = ""
            if source.get("type") == "url":
                url = str(source.get("value") or "")
            label = f"[AG-UI {kind} attachment"
            label += f": {url}]" if url else " (no payload)]"
            chunks.append(label)
        return "\n".join(chunk for chunk in chunks if chunk)
    return "" if content is None else str(content)


def parse_run_input(run_input: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a RunAgentInput and split it into its PawFlow ingredients.

    AG-UI clients send the full message history on every run; PawFlow keeps
    its own durable context per thread, so only the TRAILING segment after
    the last assistant message is new input: user text (and inline
    attachments) plus frontend-tool results. ``resume`` entries answer
    interrupts raised by a previous run.
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

    trailing: List[Dict[str, Any]] = []
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role == "assistant":
            break
        if role in ("user", "tool"):
            trailing.append(message)
    trailing.reverse()

    attachments: List[Dict[str, Any]] = []
    user_texts: List[str] = []
    tool_results: List[Dict[str, str]] = []
    for message in trailing:
        if str(message.get("role")) == "user":
            text = _flatten_user_content(message.get("content"),
                                         attachments).strip()
            if text:
                user_texts.append(text)
        else:
            tool_results.append({
                "tool_call_id": str(message.get("toolCallId") or ""),
                "content": str(message.get("content") or ""),
                "error": str(message.get("error") or ""),
            })

    resume: List[Dict[str, Any]] = []
    for entry in run_input.get("resume") or []:
        if not isinstance(entry, dict):
            continue
        interrupt_id = str(entry.get("interruptId") or "").strip()
        if not interrupt_id:
            continue
        resume.append({
            "interrupt_id": interrupt_id,
            "status": str(entry.get("status") or "resolved"),
            "payload": entry.get("payload"),
        })

    if not user_texts and not tool_results and not resume and not attachments:
        raise ValueError("RunAgentInput carries no new user input "
                         "(no trailing user/tool message and no resume entry)")

    context_lines: List[str] = []
    for entry in run_input.get("context") or []:
        if isinstance(entry, dict) and (entry.get("value") or ""):
            description = str(entry.get("description") or "context")
            context_lines.append(f"- {description}: {entry.get('value')}")

    tools: List[Dict[str, Any]] = []
    for tool in run_input.get("tools") or []:
        if isinstance(tool, dict) and str(tool.get("name") or "").strip():
            tools.append({
                "name": str(tool.get("name")).strip(),
                "description": str(tool.get("description") or ""),
                "parameters": tool.get("parameters")
                if isinstance(tool.get("parameters"), dict) else None,
            })

    forwarded_props = run_input.get("forwardedProps")
    state = run_input["state"] if run_input.get("state") is not None else _UNSET

    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "user_texts": user_texts,
        "tool_results": tool_results,
        "attachments": attachments,
        "resume": resume,
        "context_lines": context_lines,
        "tools": tools,
        "forwarded_props": forwarded_props,
        "state": state,
    }


def _assemble_prompt(spec: Dict[str, Any], resume_texts: List[str],
                     frontend_tools_live: bool) -> str:
    """Build the PawFlow turn prompt from the parsed run ingredients."""
    parts: List[str] = []
    parts.extend(resume_texts)
    for result in spec["tool_results"]:
        header = (f"[AG-UI frontend tool result for call "
                  f"{result['tool_call_id'] or '?'}]")
        body = result["content"]
        if result["error"]:
            body = f"ERROR: {result['error']}\n{body}".strip()
        parts.append(f"{header}\n{body}")
    parts.extend(spec["user_texts"])
    if spec["context_lines"]:
        parts.append("[AG-UI context]\n" + "\n".join(spec["context_lines"]))
    props = spec.get("forwarded_props")
    if props is not None:
        try:
            props_json = json.dumps(props, ensure_ascii=False)
        except (TypeError, ValueError):
            props_json = str(props)
        if props_json and props_json not in ("{}", "null", "[]"):
            if len(props_json) > _MAX_PROPS_CHARS:
                props_json = props_json[:_MAX_PROPS_CHARS] + "… (truncated)"
            parts.append("[AG-UI forwardedProps]\n" + props_json)
    if spec["tools"]:
        names = [f"- {t['name']}: {t['description']}" for t in spec["tools"]]
        if frontend_tools_live:
            parts.append(
                "[AG-UI frontend tools declared by the client — available "
                "as real tools; each call executes in the client "
                "application and its result arrives in a later message]\n"
                + "\n".join(names))
        else:
            parts.append(
                "[AG-UI frontend tools declared by the client "
                "(informational; you cannot call them directly)]\n"
                + "\n".join(names))
    prompt = "\n\n".join(part for part in parts if part)
    if len(prompt) > _MAX_TEXT_CHARS:
        raise ValueError(f"AG-UI prompt exceeds {_MAX_TEXT_CHARS} characters")
    return prompt


class _TurnTranslator:
    """Translate PawFlow conversation-bus events into AG-UI events.

    Stateful over one run: it opens/closes TEXT_MESSAGE and THINKING
    blocks so the stream always pairs START/END, dedupes the final
    persisted ``new_message`` against the text already streamed as
    ``token`` deltas (both carry the same ``msg_id``), suppresses the
    server-side placeholder result of frontend tool calls (the real result
    is produced by the client), and collects interrupts raised during the
    run for the RUN_FINISHED outcome.
    """

    def __init__(self, frontend_tool_names: Optional[Set[str]] = None) -> None:
        self._text_msg_id = ""
        self._thinking_open = False
        self._streamed_ids: set = set()
        self._frontend_names: Set[str] = frontend_tool_names or set()
        self._frontend_call_ids: Set[str] = set()
        self.interrupts: List[Dict[str, Any]] = []
        self.frontend_calls = 0

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
            if name in self._frontend_names:
                self._frontend_call_ids.add(tc_id)
                self.frontend_calls += 1
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
            if not tc_id or tc_id in self._frontend_call_ids:
                # A frontend tool's server-side result is a placeholder; the
                # client produces the real one, so no TOOL_CALL_RESULT here.
                return out
            out.append(agui_event(
                "TOOL_CALL_RESULT",
                messageId=str(data.get("msg_id") or "") or ("msg_" + uuid.uuid4().hex[:12]),
                toolCallId=tc_id,
                content=str(data.get("result") or ""),
                role="tool"))
            return out
        if event_type == "agui_state_snapshot":
            out.extend(self.close_open_blocks())
            out.append(agui_event("STATE_SNAPSHOT", snapshot=data.get("state")))
            return out
        if event_type == "agui_state_delta":
            delta = data.get("delta")
            if isinstance(delta, list) and delta:
                out.extend(self.close_open_blocks())
                out.append(agui_event("STATE_DELTA", delta=delta))
            return out
        if event_type == "agui_interrupt":
            interrupt = data.get("interrupt")
            if isinstance(interrupt, dict) and interrupt.get("id"):
                self.interrupts.append(interrupt)
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


def _prepare_agui_doc(conversation_id: str,
                      spec: Dict[str, Any]) -> Tuple[Any, List[str]]:
    """Sync the conversation's AG-UI document with this run's input.

    Stores the declared frontend tools, seeds/replaces the shared state
    when the client sent one, and settles ``resume`` entries against the
    pending interrupts. Returns ``(state, resume_texts)``.
    """
    from core.agui_tools import load_agui_doc, save_agui_doc
    doc = load_agui_doc(conversation_id) or {}
    doc["tools"] = spec["tools"]
    if spec["state"] is not _UNSET:
        doc["state"] = spec["state"]
    pending = {i.get("id"): i for i in doc.get("interrupts") or []
               if isinstance(i, dict)}
    resume_texts: List[str] = []
    for entry in spec["resume"]:
        interrupt = pending.pop(entry["interrupt_id"], None)
        reason = (interrupt or {}).get("reason", "unknown")
        try:
            payload_json = json.dumps(entry["payload"], ensure_ascii=False)
        except (TypeError, ValueError):
            payload_json = str(entry["payload"])
        resume_texts.append(
            f"[AG-UI interrupt {entry['interrupt_id']} ({reason}) "
            f"{entry['status']}] Client payload: {payload_json}")
    doc["interrupts"] = list(pending.values())
    save_agui_doc(conversation_id, doc)
    return doc.get("state"), resume_texts


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
        spec = parse_run_input(run_input)
    except (PermissionError, ValueError) as exc:
        yield sse_frame(agui_event("RUN_ERROR", message=str(exc),
                                   code="invalid_input"))
        return

    thread_id, run_id = spec["thread_id"], spec["run_id"]
    yield sse_frame(agui_event("RUN_STARTED", threadId=thread_id,
                               runId=run_id))

    isolated = publication.get("context_policy") == "isolated"
    events: "queue.Queue[Tuple[str, Any, Any]]" = queue.Queue()

    def _live(_cid: str, event_type: str, data: Any) -> None:
        events.put(("evt", event_type, data))

    try:
        store = A2AStore.instance()
        context = store.ensure_named_context(publication, key["key_id"],
                                             "agui_" + thread_id)
        _ensure_isolated_conversation(publication, context)
        conversation_id = context["internal_conversation_id"]
        state: Any = None
        resume_texts: List[str] = []
        if isolated:
            state, resume_texts = _prepare_agui_doc(conversation_id, spec)
        prompt = _assemble_prompt(spec, resume_texts,
                                  frontend_tools_live=isolated)
        if not prompt and not spec["attachments"]:
            raise ValueError("RunAgentInput carries no new user input")
        if isolated and state is not None:
            yield sse_frame(agui_event("STATE_SNAPSHOT", snapshot=state))
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
            attachments=spec["attachments"],
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

    frontend_names = ({t["name"] for t in spec["tools"]}
                      if isolated else set())
    translator = _TurnTranslator(frontend_tool_names=frontend_names)
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
            outcome: Dict[str, Any] = {"type": "success"}
            if translator.interrupts:
                outcome = {"type": "interrupt",
                           "interrupts": translator.interrupts}
            yield sse_frame(agui_event(
                "RUN_FINISHED", threadId=thread_id, runId=run_id,
                result=getattr(result, "response", "") or "",
                outcome=outcome))
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
