"""Full AG-UI client runtime for external conversation participants.

PawFlow owns the canonical transcript and acts as the AG-UI client: it streams
remote output into the normal conversation event bus, executes only explicitly
exposed PawFlow tools through the normal approval gate, and persists protocol
state per conversation member.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from collections.abc import Iterable
from typing import Any

import requests

from core.relay_proxy_url import resolve_relay_aware_url

logger = logging.getLogger(__name__)
_QUEUES: dict[str, queue.Queue] = {}
_ACTIVE: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _key(conversation_id: str, agent_name: str) -> str:
    return conversation_id + ":" + agent_name.lower()


def _doc_key(agent_name: str) -> str:
    return "external_agui::" + agent_name.lower()


def _load_doc(conversation_id: str, agent_name: str) -> dict:
    from core.conversation_store import ConversationStore
    value = ConversationStore.instance().get_extra(
        conversation_id, _doc_key(agent_name)) or {}
    if not isinstance(value, dict):
        value = {}
    value.setdefault("thread_id", str(uuid.uuid5(
        uuid.NAMESPACE_URL, conversation_id + ":" + agent_name)))
    value.setdefault("state", {})
    value.setdefault("pending_interrupts", [])
    return value


def _save_doc(conversation_id: str, agent_name: str, doc: dict) -> None:
    from core.conversation_store import ConversationStore
    ConversationStore.instance().set_extra(
        conversation_id, _doc_key(agent_name), doc)


def _secret(name: str, user_id: str, conversation_id: str) -> str:
    if not name:
        return ""
    from core.pfp_runtime._helpers import _resolve_secret_value
    value = _resolve_secret_value(
        name, user_id=user_id, conversation_id=conversation_id)
    if value is None:
        raise ValueError(f"AG-UI authentication secret is unavailable: {name}")
    return str(value)


def _events(response: requests.Response) -> Iterable[dict[str, Any]]:
    """Parse an SSE stream, including comments and multiline data fields."""
    data: list[str] = []
    for raw in response.iter_lines(decode_unicode=True):
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = str(line or "")
        if not line:
            if data:
                try:
                    value = json.loads("\n".join(data))
                    if isinstance(value, dict):
                        yield value
                except json.JSONDecodeError:
                    logger.warning("Ignoring malformed AG-UI SSE event")
                data = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        try:
            value = json.loads("\n".join(data))
            if isinstance(value, dict):
                yield value
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed final AG-UI SSE event")


def _attachment_part(item: dict) -> dict | None:
    mime = str(item.get("mime_type") or item.get("mimeType") or "application/octet-stream")
    if mime.startswith("image/"):
        kind = "image"
    elif mime.startswith("audio/"):
        kind = "audio"
    elif mime.startswith("video/"):
        kind = "video"
    else:
        kind = "document"
    data = item.get("data") or item.get("base64")
    if data:
        return {"type": kind, "source": {"type": "data", "value": str(data),
                                          "mimeType": mime}}
    url = item.get("url") or item.get("file_url")
    if url:
        return {"type": kind, "source": {"type": "url", "value": str(url),
                                          "mimeType": mime}}
    return None


def _current_content(text: str, attachments: list) -> Any:
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for item in attachments or []:
        if isinstance(item, dict):
            part = _attachment_part(item)
            if part:
                parts.append(part)
    return parts if len(parts) > 1 or (parts and parts[0].get("type") != "text") else text


def _text_content(content: Any) -> str | None:
    """Flatten a persisted row body to text; None when nothing is replayable."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [str(part.get("text") or "") for part in content
                 if isinstance(part, dict) and part.get("type") == "text"]
        return "\n".join(text for text in texts if text) or None
    return None


def _replay_tool_calls(row: dict) -> list:
    calls = []
    for call in row.get("tool_calls") or []:
        if not isinstance(call, dict) or not call.get("id"):
            continue
        arguments = call.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(
                arguments if isinstance(arguments, dict) else {},
                ensure_ascii=False)
        calls.append({"id": str(call["id"]), "type": "function",
                      "function": {"name": str(call.get("name") or ""),
                                   "arguments": arguments}})
    return calls


def _messages(conversation_id: str, user_id: str, current: dict) -> list:
    from core.conversation_store import ConversationStore
    rows = ConversationStore.instance().load(
        conversation_id, user_id=user_id) or []
    result = []
    known_calls: set[str] = set()
    for row in rows[-200:]:
        role = str(row.get("role") or "")
        if role not in {"user", "assistant", "system", "tool"}:
            continue
        content = _text_content(row.get("content"))
        tool_calls = _replay_tool_calls(row) if role == "assistant" else []
        if content is None and not tool_calls:
            continue
        item = {"id": str(row.get("msg_id") or uuid.uuid4().hex),
                "role": role, "content": content or ""}
        if role == "tool":
            call_id = str(row.get("tool_call_id") or "")
            # A tool message must answer a replayed assistant toolCall; strict
            # agents reject orphan tool rows for the whole run.
            if call_id not in known_calls:
                continue
            item["toolCallId"] = call_id
        if tool_calls:
            item["toolCalls"] = tool_calls
            known_calls.update(call["id"] for call in tool_calls)
        result.append(item)
    current_id = str(current.get("id") or "")
    if current_id and not any(item.get("id") == current_id for item in result):
        result.append({"id": current_id, "role": "user",
                       "content": current.get("content", "")})
    return result


def _publish(conversation_id: str, event_type: str, data: dict) -> None:
    from core.conversation_event_bus import ConversationEventBus
    ConversationEventBus.instance().publish_event(conversation_id, event_type, data)


def _source(agent: str) -> dict:
    return {"type": "external_agui_agent", "name": agent,
            "runtime_kind": "external_agui"}


def _connection_config(job: dict) -> dict:
    config = dict(job["config"])
    service_id = str(config.get("agui_service") or "").strip()
    if not service_id:
        # A direct endpoint is public and unauthenticated: the Bearer secret
        # and private-target policy live only on an aguiConnection service,
        # whose endpoint and secret are bound together by its owner.
        config.pop("agui_auth_secret", None)
        config.pop("agui_allow_private", None)
        return config
    from core.service_registry import ServiceRegistry
    service = ServiceRegistry.get_instance().resolve(
        service_id, user_id=job["user_id"], conv_id=job["conversation_id"])
    if not service or getattr(service, "TYPE", "") != "aguiConnection":
        raise ValueError(f"AG-UI connection service is unavailable: {service_id}")
    resolved = service.runtime_config()
    resolved["tools"] = list(config.get("tools") or [])
    resolved["agui_service"] = service_id
    return resolved


def _persist_block(job: dict, content: str, message_id: str,
                   tool_calls: list | None = None, final: bool = False,
                   thinking: str = "", metadata: dict | None = None,
                   reasoning_metadata: dict | None = None,
                   run_metadata: dict | None = None) -> None:
    if not content.strip() and not tool_calls and not thinking.strip():
        return
    from core.conversation_writer import ConversationWriter
    from core.llm_client import stamp_message
    source = _source(job["agent_name"])
    row = {"role": "assistant", "content": content,
           "msg_id": message_id or "agui_" + uuid.uuid4().hex,
           "turn_id": job["message_id"], "turn_final": bool(final),
           "source": source}
    if thinking.strip():
        row["thinking"] = thinking
    if metadata:
        row["metadata"] = dict(metadata)
    if reasoning_metadata:
        row["agui_reasoning_metadata"] = dict(reasoning_metadata)
    if run_metadata:
        row["agui_run_metadata"] = dict(run_metadata)
    if tool_calls:
        row["tool_calls"] = tool_calls
    message = stamp_message(row, job["conversation_id"])
    events = []
    if thinking.strip():
        events.append({"type": "thinking_content", "data": {
            "text": thinking, "msg_id": message["msg_id"],
            "turn_id": job["message_id"], "agent_name": job["agent_name"],
            "source": source}})
    if content.strip():
        events.append({"type": "new_message", "data": {
            "role": "assistant", "content": content, "msg_id": message["msg_id"],
            "turn_id": job["message_id"], "agent_name": job["agent_name"],
            "source": source}})
    for call in tool_calls or []:
        events.append({"type": "tool_call", "data": {
            "tool": call["name"], "arguments": call["arguments"],
            "tc_id": call["id"], "msg_id": message["msg_id"],
            "turn_id": job["message_id"], "agent_name": job["agent_name"],
            "source": source}})
    ConversationWriter.for_conversation(job["conversation_id"]).enqueue_message(
        message, agent_name=job["agent_name"], user_id=job["user_id"],
        sse_events=events, wait=True)


def _finish(job: dict, content: str, error: str = "", message_id: str = "",
            thinking: str = "", metadata: dict | None = None,
            reasoning_metadata: dict | None = None,
            run_metadata: dict | None = None) -> None:
    # One settlement per turn: cancel() and the worker may both reach here.
    with _LOCK:
        if job.get("_settled"):
            return
        job["_settled"] = True
    # Text that already streamed to the UI stays durable even when the run
    # ends in an error; otherwise it would vanish on the next reload.
    if content.strip() or thinking.strip():
        _persist_block(job, content, message_id, final=True, thinking=thinking,
                       metadata=metadata, reasoning_metadata=reasoning_metadata,
                       run_metadata=run_metadata)
    from services.mcp_terminal_router import complete_published_terminal_turn
    complete_published_terminal_turn({
        "conversation_id": job["conversation_id"],
        "owner_user_id": job["user_id"], "agent_name": job["agent_name"],
    }, job["message_id"], content, error=error)


def _registry(job: dict):
    from tasks.ai.agent_loop import AgentLoopTask
    live = AgentLoopTask._live_instance
    if live:
        registry = live.get_tool_registry().fork()
    else:
        from core.tool_registry import create_default_registry
        registry = create_default_registry()
    from core.tool_loader import load_tools_into_registry
    load_tools_into_registry(registry, job["user_id"], job["conversation_id"])
    if live:
        live._configure_tool_handlers(  # pylint: disable=protected-access
            registry, conversation_id=job["conversation_id"],
            user_id=job["user_id"], agent_name=job["agent_name"])
    else:
        for handler in registry.list_tools():
            if hasattr(handler, "set_user_id"):
                handler.set_user_id(job["user_id"])
            if hasattr(handler, "set_conversation_id"):
                handler.set_conversation_id(job["conversation_id"])
            if hasattr(handler, "set_agent_name"):
                handler.set_agent_name(job["agent_name"])
    return registry


def _tool_definitions(registry, allowed: list) -> list:
    names = {str(name) for name in allowed or [] if str(name).strip()}
    return [{"name": h.name, "description": h.description,
             "parameters": h.parameters_schema}
            for h in registry.list_tools() if h.name in names]


def _execute_tool(job: dict, registry, call: dict) -> str:
    name, args = call["name"], call["arguments"]
    allowed = {str(item) for item in job["config"].get("tools") or []}
    if name not in allowed:
        return f"Error: remote AG-UI tool '{name}' is not in this agent's allowlist."
    if registry.get(name) is None:
        return f"Error: PawFlow tool '{name}' is unavailable."
    if args.get("_error"):
        return f"Error: Tool '{name}' received invalid JSON arguments."
    prepared = registry.prepare(name, args)
    if isinstance(prepared, str):
        return prepared
    from core.llm_client import unwrap_mcp_tool
    effective_name, effective_args = unwrap_mcp_tool(
        prepared.name, prepared.arguments)
    if not isinstance(effective_args, dict):
        return f"Error: Tool '{effective_name}' arguments must be a JSON object."
    if effective_name != name and effective_name not in allowed:
        return (f"Error: wrapped tool '{effective_name}' is not in this "
                "agent's allowlist.")
    name, args = effective_name, effective_args
    from core.conversation_store import ConversationStore
    mode = ConversationStore.instance().get_extra(
        job["conversation_id"], "permission_mode") or "default"
    from core.tool_approval import ToolApprovalGate
    if mode == "read_only" and not ToolApprovalGate.is_read_only_allowed(name, args):
        return f"Error: Tool '{name}' is blocked in read-only mode."
    if mode != "auto" and ToolApprovalGate.normalize_tool_name(name) not in ToolApprovalGate.EXEMPT_TOOLS:
        decision = ToolApprovalGate.check(
            name, f"{name}({json.dumps(args, ensure_ascii=False)[:200]})",
            job["conversation_id"], job["user_id"], arguments=args,
            agent_name=job["agent_name"])
        if decision != "approved":
            return f"Error: Tool '{name}' was {decision} by the user."
    return str(registry.execute_prepared(prepared))


def _persist_tool_result(job: dict, call: dict, result: str) -> None:
    from core.conversation_writer import ConversationWriter
    from core.llm_client import stamp_message
    source = _source(job["agent_name"])
    message = stamp_message({"role": "tool", "content": result,
        "msg_id": "agui_tool_" + uuid.uuid4().hex,
        "tool_call_id": call["id"], "turn_id": job["message_id"],
        "source": source}, job["conversation_id"])
    ConversationWriter.for_conversation(job["conversation_id"]).enqueue_message(
        message, agent_name=job["agent_name"], user_id=job["user_id"],
        sse_events=[{"type": "tool_result", "data": {
            "tool": call["name"], "result": result, "tc_id": call["id"],
            "msg_id": message["msg_id"], "turn_id": job["message_id"],
            "agent_name": job["agent_name"], "source": source}}], wait=True)


def _resume_entries(doc: dict, answer: str) -> list:
    pending = [item for item in doc.get("pending_interrupts") or []
               if isinstance(item, dict) and item.get("id")]
    if not pending:
        return []
    doc["pending_interrupts"] = []
    return [{"interruptId": str(item["id"]), "status": "resolved",
             "payload": {"answer": answer}} for item in pending]


def _merge_metadata(current: dict, event: dict) -> dict:
    incoming = event.get("metadata")
    if not isinstance(incoming, dict):
        return current
    return {**current, **incoming}


def _apply_protocol_state(job: dict, doc: dict, event: dict) -> None:
    kind = str(event.get("type") or "").upper()
    if kind == "STATE_SNAPSHOT":
        doc["state"] = event.get("snapshot")
        _publish(job["conversation_id"], "agui_state_snapshot", {
            "state": doc["state"], "agent_name": job["agent_name"]})
    elif kind == "STATE_DELTA":
        delta = event.get("delta")
        if isinstance(delta, list):
            from core.agui_tools import apply_json_patch
            doc["state"] = apply_json_patch(doc.get("state"), delta)
            _publish(job["conversation_id"], "agui_state_delta", {
                "delta": delta, "state": doc["state"],
                "agent_name": job["agent_name"]})
    elif kind == "MESSAGES_SNAPSHOT":
        snapshot = event.get("messages")
        if isinstance(snapshot, list):
            # Remote-private diagnostic state only; never replace PawFlow rows.
            bounded = snapshot[-100:]
            try:
                if len(json.dumps(bounded, ensure_ascii=False)) > 200_000:
                    bounded = bounded[-20:]
            except (TypeError, ValueError):
                bounded = []
            doc["remote_messages_snapshot"] = bounded
    elif kind in {"ACTIVITY_SNAPSHOT", "ACTIVITY_DELTA"}:
        message_id = str(event.get("messageId") or "activity")
        activity_type = str(event.get("activityType") or "")
        activities = doc.setdefault("activities", {})
        if kind == "ACTIVITY_SNAPSHOT":
            content = event.get("content")
            if content is None:
                content = event.get("activity") or event.get("snapshot") or {}
            if event.get("replace", True) or message_id not in activities:
                activities[message_id] = {"id": message_id,
                    "activityType": activity_type, "content": content}
        else:
            patch = event.get("patch")
            if patch is None:
                patch = event.get("delta")
            current = activities.setdefault(message_id, {"id": message_id,
                "activityType": activity_type, "content": {}})
            if isinstance(patch, list):
                from core.agui_tools import apply_json_patch
                current["content"] = apply_json_patch(current.get("content"), patch)
            if activity_type:
                current["activityType"] = activity_type
        activity = activities.get(message_id, {})
        doc["activity"] = activity
        _publish(job["conversation_id"], "agui_activity", {
            "activity": activity, "event_type": kind,
            "agent_name": job["agent_name"]})
    elif kind.startswith("STEP_"):
        steps = doc.setdefault("steps", {})
        step_id = str(event.get("stepId") or event.get("id")
                      or event.get("stepName") or "step")
        steps[step_id] = event
        _publish(job["conversation_id"], "agui_step", {
            "step": event, "event_type": kind,
            "agent_name": job["agent_name"]})
    elif kind in {"RAW", "CUSTOM"}:
        _publish(job["conversation_id"], "agui_custom", {
            "event": event, "agent_name": job["agent_name"]})
    elif kind == "REASONING_ENCRYPTED_VALUE":
        entity_id = str(event.get("entityId") or "")
        encrypted = event.get("encryptedValue")
        if entity_id and isinstance(encrypted, str):
            values = doc.setdefault("encrypted_values", {})
            values[entity_id] = {"subtype": str(event.get("subtype") or "message"),
                                 "encryptedValue": encrypted}
            while len(values) > 100:
                values.pop(next(iter(values)))
    elif kind in {"USAGE", "RUN_USAGE"}:
        doc["usage"] = event.get("usage") or event
        _publish(job["conversation_id"], "agui_usage", {
            "usage": doc["usage"], "agent_name": job["agent_name"]})
    # The protocol document is saved once per run by _run(); a per-event
    # write would hit the conversation store on every STATE_DELTA/STEP.


def _one_run(job: dict, endpoint: str, headers: dict, payload: dict,
             doc: dict) -> dict:
    cid, agent = job["conversation_id"], job["agent_name"]
    response = requests.post(
        endpoint, headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        stream=True, timeout=(10, max(1, int(job["config"].get("agui_timeout") or 300))),
        allow_redirects=False)
    if not 200 <= response.status_code < 300:
        response.close()
        raise ValueError(f"AG-UI endpoint returned HTTP {response.status_code}")
    active_key = _key(cid, agent)
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    current_msg = "agui_" + uuid.uuid4().hex
    # Shared with cancel() so a force-stop can persist what already streamed.
    partial = {"text": text_parts, "thinking": reasoning_parts,
               "message_id": current_msg}
    with _LOCK:
        active = _ACTIVE.get(active_key)
        if active is not None:
            active["response"] = response
            active["partial"] = partial
    current_tool_call = ""
    calls: dict[str, dict] = {}
    error = ""
    outcome: dict = {}
    usage = None
    finished_result = ""
    text_metadata: dict = {}
    reasoning_metadata: dict = {}
    run_metadata: dict = {}
    started = False
    finished = False
    try:
        for event in _events(response):
            with _LOCK:
                cancelled = bool((_ACTIVE.get(active_key) or {}).get("cancel"))
            if cancelled:
                raise RuntimeError("AG-UI run cancelled")
            kind = str(event.get("type") or "").upper()
            if kind.startswith("TEXT_MESSAGE_"):
                text_metadata = _merge_metadata(text_metadata, event)
            elif kind == "REASONING_ENCRYPTED_VALUE":
                entity_id = str(event.get("entityId") or "")
                if str(event.get("subtype") or "") == "tool-call" and entity_id in calls:
                    calls[entity_id]["encryptedValue"] = str(
                        event.get("encryptedValue") or "")
            elif kind.startswith(("REASONING_", "THINKING_")):
                reasoning_metadata = _merge_metadata(reasoning_metadata, event)
            elif kind.startswith("RUN_"):
                run_metadata = _merge_metadata(run_metadata, event)
            if not started:
                if kind != "RUN_STARTED":
                    error = "AG-UI lifecycle violation: first event is not RUN_STARTED"
                    break
                started = True
                continue
            _apply_protocol_state(job, doc, event)
            if kind == "TEXT_MESSAGE_START":
                current_msg = str(event.get("messageId") or current_msg)
                partial["message_id"] = current_msg
            elif kind in {"TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_CHUNK"}:
                if kind == "TEXT_MESSAGE_CHUNK" and event.get("messageId"):
                    current_msg = str(event["messageId"])
                    partial["message_id"] = current_msg
                delta = str(event.get("delta") or event.get("text") or "")
                if delta:
                    text_parts.append(delta)
                    _publish(cid, "token", {"text": delta, "content": delta,
                        "msg_id": current_msg, "turn_id": job["message_id"],
                        "agent_name": agent, "source": _source(agent)})
            elif kind.startswith(("REASONING_", "THINKING_")):
                delta = str(event.get("delta") or event.get("text") or event.get("content") or "")
                if delta:
                    reasoning_parts.append(delta)
                _publish(cid, "thinking_delta" if delta else "thinking", {
                    "text": delta, "agent_name": agent,
                    "turn_id": job["message_id"], "source": _source(agent)})
            elif kind == "TOOL_CALL_START":
                call_id = str(event.get("toolCallId") or "tc_" + uuid.uuid4().hex)
                current_tool_call = call_id
                calls[call_id] = {"id": call_id,
                    "name": str(event.get("toolCallName") or event.get("name") or ""),
                    "args_text": "", "arguments": {},
                    "metadata": dict(event.get("metadata") or {})}
            elif kind == "TOOL_CALL_ARGS":
                call_id = str(event.get("toolCallId") or "")
                if call_id in calls:
                    calls[call_id]["args_text"] += str(event.get("delta") or "")
                    calls[call_id]["metadata"] = _merge_metadata(
                        calls[call_id].get("metadata") or {}, event)
            elif kind == "TOOL_CALL_CHUNK":
                call_id = str(event.get("toolCallId") or current_tool_call
                              or "tc_" + uuid.uuid4().hex)
                current_tool_call = call_id
                call = calls.setdefault(call_id, {"id": call_id,
                    "name": str(event.get("toolCallName") or event.get("name") or ""),
                    "args_text": "", "arguments": {}, "metadata": {}})
                if event.get("toolCallName") or event.get("name"):
                    call["name"] = str(event.get("toolCallName") or event.get("name"))
                call["args_text"] += str(event.get("delta") or event.get("args") or "")
                call["metadata"] = _merge_metadata(call.get("metadata") or {}, event)
            elif kind == "TOOL_CALL_END":
                call_id = str(event.get("toolCallId") or "")
                call = calls.get(call_id)
                if call:
                    call["metadata"] = _merge_metadata(
                        call.get("metadata") or {}, event)
                    try:
                        call["arguments"] = json.loads(call["args_text"] or "{}")
                    except json.JSONDecodeError:
                        call["arguments"] = {"_error": "invalid JSON arguments"}
            elif kind == "TOOL_CALL_RESULT":
                call_id = str(event.get("toolCallId") or "")
                if call_id in calls:
                    calls[call_id]["remote_result"] = str(event.get("content") or "")
                _publish(cid, "tool_result", {"tc_id": event.get("toolCallId") or "",
                    "result": event.get("content") or "", "agent_name": agent,
                    "turn_id": job["message_id"], "source": _source(agent)})
            elif kind == "RUN_ERROR":
                error = str(event.get("message") or event.get("error") or "AG-UI run failed")
                break
            elif kind == "RUN_FINISHED":
                outcome = event.get("outcome") if isinstance(event.get("outcome"), dict) else {}
                usage = event.get("usage")
                finished_result = str(event.get("result") or "")
                finished = True
                break
    finally:
        response.close()
        with _LOCK:
            active = _ACTIVE.get(active_key)
            if active is not None:
                active.pop("response", None)
    if usage is not None:
        doc["usage"] = usage
        _publish(cid, "agui_usage", {"usage": usage, "agent_name": agent})
    if not error and not finished:
        error = "AG-UI stream ended without RUN_FINISHED"
    for call in calls.values():
        if call.get("args_text") and not call.get("arguments"):
            try:
                call["arguments"] = json.loads(call["args_text"])
            except json.JSONDecodeError:
                call["arguments"] = {"_error": "invalid JSON arguments"}
    client_calls = []
    remote_calls = []
    for call in calls.values():
        clean = {key: value for key, value in call.items()
                 if key not in {"args_text", "remote_result"}}
        if "remote_result" in call:
            clean["result"] = call["remote_result"]
            remote_calls.append(clean)
        else:
            client_calls.append(clean)
    return {"content": "".join(text_parts) or finished_result,
            "thinking": "".join(reasoning_parts),
            "message_id": current_msg,
            "calls": client_calls, "remote_calls": remote_calls,
            "error": error, "outcome": outcome,
            "metadata": text_metadata,
            "reasoning_metadata": reasoning_metadata,
            "run_metadata": run_metadata}


def _run(job: dict) -> None:
    cid, agent = job["conversation_id"], job["agent_name"]
    active_key = _key(cid, agent)
    # Register before any setup work so a force-stop issued while the tool
    # registry is still loading marks this run cancelled instead of leaking it.
    with _LOCK:
        _ACTIVE[active_key] = {"cancel": False,
                               "request_id": job["message_id"], "job": job}
    try:
        cfg = _connection_config(job)
        job["config"] = cfg
        endpoint = resolve_relay_aware_url(
            str(cfg.get("agui_url") or ""), user_id=job["user_id"],
            conversation_id=cid, agent_name=agent,
            allow_private=bool(cfg.get("agui_allow_private")),
            service_name="AG-UI endpoint")
        headers = {"Accept": "text/event-stream", "Content-Type": "application/json",
                   "User-Agent": "PawFlow-AGUI/1.0"}
        token = _secret(str(cfg.get("agui_auth_secret") or ""), job["user_id"], cid)
        if token:
            headers["Authorization"] = "Bearer " + token
        doc = _load_doc(cid, agent)
        resume = _resume_entries(doc, job["content"])
        messages = _messages(cid, job["user_id"], {
            "id": job["message_id"],
            "content": _current_content(job["content"], job.get("attachments") or [])})
        registry = _registry(job)
        rounds_value = cfg.get("agui_max_tool_rounds")
        max_rounds = max(0, min(32, int(
            8 if rounds_value in (None, "") else rounds_value)))
        # With zero follow-up rounds no call could ever be answered, so do not
        # advertise tools the remote agent would then be refused to use.
        tool_defs = (_tool_definitions(registry, cfg.get("tools") or [])
                     if max_rounds > 0 else [])
        _publish(cid, "thinking", {"agent_name": agent,
            "turn_id": job["message_id"], "source": _source(agent)})
        for round_index in range(max_rounds + 1):
            payload = {"threadId": doc["thread_id"],
                "runId": "run_" + uuid.uuid4().hex, "state": doc.get("state"),
                "messages": messages, "tools": tool_defs, "context": [],
                "forwardedProps": {"pawflow": {"conversationId": cid,
                    "agentName": agent, "toolRound": round_index}}}
            if resume:
                payload["resume"] = resume
                resume = []
            result = _one_run(job, endpoint, headers, payload, doc)
            _save_doc(cid, agent, doc)
            with _LOCK:
                cancelled = bool((_ACTIVE.get(active_key) or {}).get("cancel"))
            if cancelled:
                return  # cancel() already settled this turn
            if result["error"]:
                _finish(job, result["content"], error=result["error"],
                        message_id=result["message_id"],
                        thinking=result["thinking"])
                return
            calls = result["calls"]
            outcome = result["outcome"]
            if outcome.get("type") == "interrupt":
                interrupts = [item for item in outcome.get("interrupts") or []
                              if isinstance(item, dict) and item.get("id")]
                doc["pending_interrupts"] = interrupts
                _save_doc(cid, agent, doc)
                for interrupt in interrupts:
                    question = str(interrupt.get("question") or interrupt.get("message")
                                   or interrupt.get("reason") or "AG-UI agent needs your input")
                    _publish(cid, "ask_user", {"question": question,
                        "interrupt_id": interrupt["id"], "agent_name": agent,
                        "turn_id": job["message_id"], "source": _source(agent)})
                finish_kwargs = {"message_id": result["message_id"],
                                 "thinking": result["thinking"]}
                if result.get("metadata"):
                    finish_kwargs["metadata"] = result["metadata"]
                if result.get("reasoning_metadata"):
                    finish_kwargs["reasoning_metadata"] = result["reasoning_metadata"]
                if result.get("run_metadata"):
                    finish_kwargs["run_metadata"] = result["run_metadata"]
                _finish(job, result["content"], **finish_kwargs)
                return
            if not calls:
                finish_kwargs = {"message_id": result["message_id"],
                                 "thinking": result["thinking"]}
                if result.get("metadata"):
                    finish_kwargs["metadata"] = result["metadata"]
                if result.get("reasoning_metadata"):
                    finish_kwargs["reasoning_metadata"] = result["reasoning_metadata"]
                if result.get("run_metadata"):
                    finish_kwargs["run_metadata"] = result["run_metadata"]
                _finish(job, result["content"], **finish_kwargs)
                return
            if round_index >= max_rounds:
                _finish(job, "", error=f"AG-UI tool round limit reached ({max_rounds})")
                return
            _persist_block(job, result["content"], result["message_id"], calls,
                           thinking=result["thinking"], metadata=result.get("metadata"),
                           reasoning_metadata=result.get("reasoning_metadata"),
                           run_metadata=result.get("run_metadata"))
            remote_calls = []
            for call in calls:
                remote_calls.append({"id": call["id"], "type": "function",
                    "function": {"name": call["name"],
                                 "arguments": json.dumps(call["arguments"], ensure_ascii=False)}})
            messages.append({"id": result["message_id"], "role": "assistant",
                             "content": result["content"], "toolCalls": remote_calls})
            for call in calls:
                value = _execute_tool(job, registry, call)
                _persist_tool_result(job, call, value)
                messages.append({"id": "tool_" + uuid.uuid4().hex,
                    "role": "tool", "toolCallId": call["id"], "content": value})
    except Exception as exc:  # noqa: BLE001 - worker boundary must settle the turn
        with _LOCK:
            cancelled = bool((_ACTIVE.get(active_key) or {}).get("cancel"))
        if not cancelled:
            logger.warning("External AG-UI run failed for %s/%s: %s", cid[:8], agent, exc)
            _finish(job, "", error=str(exc))
    finally:
        with _LOCK:
            _ACTIVE.pop(active_key, None)


def _worker(key: str, jobs: queue.Queue) -> None:
    while True:
        try:
            job = jobs.get(timeout=60)
        except queue.Empty:
            with _LOCK:
                if jobs.empty() and _QUEUES.get(key) is jobs:
                    _QUEUES.pop(key, None)
                    return
            continue
        try:
            _run(job)
        finally:
            jobs.task_done()


def submit(conversation_id: str, agent_name: str, message_id: str,
           content: str, config: dict, attachments: list | None = None) -> bool:
    if (not str(config.get("agui_url") or "").strip()
            and not str(config.get("agui_service") or "").strip()):
        return False
    from core.conversation_store import ConversationStore
    meta = ConversationStore.instance().get_metadata(conversation_id) or {}
    job = {"conversation_id": conversation_id, "agent_name": agent_name,
           "message_id": message_id, "content": str(content),
           "attachments": list(attachments or []), "config": dict(config),
           "user_id": str(meta.get("user_id") or "")}
    key = _key(conversation_id, agent_name)
    with _LOCK:
        jobs = _QUEUES.get(key)
        if jobs is None:
            jobs = queue.Queue()
            _QUEUES[key] = jobs
            threading.Thread(target=_worker, args=(key, jobs), daemon=True,
                             name="agui-" + agent_name[:20]).start()
        jobs.put(job)
    return True


def cancel(conversation_id: str, agent_name: str) -> bool:
    """Cancel the active remote stream and discard queued turns for an agent."""
    key = _key(conversation_id, agent_name)
    found = False
    cancelled_jobs = []
    with _LOCK:
        active = _ACTIVE.get(key)
        if active is not None:
            active["cancel"] = True
            response = active.get("response")
            partial = active.get("partial") or {}
            if isinstance(active.get("job"), dict):
                cancelled_jobs.append((active["job"], partial))
            found = True
        else:
            response = None
        jobs = _QUEUES.get(key)
        if jobs is not None:
            while True:
                try:
                    queued_job = jobs.get_nowait()
                    if isinstance(queued_job, dict):
                        cancelled_jobs.append((queued_job, {}))
                    jobs.task_done()
                    found = True
                except queue.Empty:
                    break
    if response is not None:
        try:
            response.close()
        except Exception:
            logger.debug("AG-UI response close during cancel failed", exc_info=True)
    seen = set()
    for job, partial in cancelled_jobs:
        request_id = str(job.get("message_id") or "")
        if request_id and request_id not in seen:
            seen.add(request_id)
            _finish(job, "".join(partial.get("text") or []),
                    error="AG-UI run cancelled",
                    message_id=str(partial.get("message_id") or ""),
                    thinking="".join(partial.get("thinking") or []))
    return found


__all__ = ["_events", "_one_run", "_run", "cancel", "submit"]
