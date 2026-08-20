"""A2A message validation and execution on the shared PawFlow agent runtime."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Dict, List, Tuple

from core.a2a_store import A2AStore
from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI


logger = logging.getLogger(__name__)
_MAX_TEXT_CHARS = 200_000


def _message_text(message: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    """Convert supported A2A Parts into PawFlow text and URL attachments."""
    if not isinstance(message, dict):
        raise ValueError("message must be an object")
    message_id = str(message.get("messageId") or "").strip()
    if not message_id:
        raise ValueError("message.messageId is required")
    if str(message.get("role") or "user").lower() != "user":
        raise ValueError("Only role='user' messages can be sent to an agent")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("message.parts must be a non-empty array")
    chunks: List[str] = []
    attachments: List[Dict[str, Any]] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            raise ValueError(f"message.parts[{index}] must be an object")
        if "text" in part:
            chunks.append(str(part.get("text") or ""))
        elif "data" in part:
            chunks.append(json.dumps(part.get("data"), ensure_ascii=False))
        elif "url" in part:
            url = str(part.get("url") or "").strip()
            if not url:
                raise ValueError(f"message.parts[{index}].url is required")
            label = str(part.get("filename") or "attachment")
            media_type = str(part.get("mediaType") or "")
            suffix = f" ({media_type})" if media_type else ""
            chunks.append(f"[A2A attachment URL: {label}{suffix} — {url}]")
        elif "raw" in part:
            raise ValueError("Raw/base64 A2A parts are not supported; send text, data, or a URL")
        else:
            raise ValueError(f"message.parts[{index}] has no supported content")
    text = "\n".join(chunk for chunk in chunks if chunk).strip()
    if len(text) > _MAX_TEXT_CHARS:
        raise ValueError(f"A2A message exceeds {_MAX_TEXT_CHARS} characters")
    if not text and not attachments:
        raise ValueError("A2A message has no content")
    return text, attachments


def _task_payload(task: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": task["task_id"],
        "contextId": task["context_id"],
        "status": {"state": task["state"]},
    }
    if task.get("response"):
        payload["status"]["message"] = task["response"]
    if task.get("error"):
        payload["status"]["message"] = {
            "messageId": "error_" + task["task_id"],
            "taskId": task["task_id"],
            "contextId": task["context_id"],
            "role": "agent",
            "parts": [{"text": task["error"]}],
        }
    return payload


def public_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Return the A2A HTTP representation of a persisted task."""
    return _task_payload(task)


def _ensure_isolated_conversation(publication: Dict[str, Any], context: Dict[str, Any]) -> None:
    if publication["context_policy"] != "isolated":
        return
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    conversation_id = context["internal_conversation_id"]
    if store.exists(conversation_id):
        return
    store.save(conversation_id, [], user_id=publication["owner_user_id"])
    store.set_extra(conversation_id, "title", f"A2A · {publication['label']}")
    store.set_extra(conversation_id, "a2a_parent_conversation_id",
                    publication["conversation_id"])
    store.set_extra(conversation_id, "a2a_context_id", context["context_id"])


def _response_message(task: Dict[str, Any], response: str) -> Dict[str, Any]:
    return {
        "messageId": "msg_" + uuid.uuid4().hex,
        "taskId": task["task_id"],
        "contextId": task["context_id"],
        "role": "agent",
        "parts": [{"text": response}],
    }


def _finish_task(store: A2AStore, task: Dict[str, Any]) -> None:
    try:
        result = AgentRuntimeAPI.wait_for_done(
            task["internal_conversation_id"], task["turn_id"])
        current = store.get_task(task["publication_id"], task["task_id"], task["key_id"])
        if not current or current["state"] == "canceled":
            return
        if result is None:
            store.update_task(task["task_id"], "failed",
                              error="The PawFlow agent turn ended without a final event")
        elif result.error:
            store.update_task(task["task_id"], "failed", error=result.error)
        else:
            store.update_task(task["task_id"], "completed",
                              response=_response_message(task, result.response))
    except Exception as exc:
        logger.exception("A2A task %s failed while waiting", task["task_id"])
        store.update_task(task["task_id"], "failed", error=str(exc))


def send_message(publication: Dict[str, Any], key: Dict[str, Any],
                 body: Dict[str, Any]) -> Dict[str, Any]:
    """Submit an A2A SendMessageRequest and return a Task."""
    if not publication.get("enabled"):
        raise PermissionError("This A2A publication is disabled")
    from core.conv_agent_config import get_agent_config
    agent_config = get_agent_config(
        publication["conversation_id"], publication["agent_name"]) or {}
    runtime_kind = str(agent_config.get("runtime_kind") or "llm")
    if (runtime_kind in {"external_mcp", "external_agui"}
            and publication.get("context_policy") != "shared"):
        raise ValueError(
            f"{runtime_kind} A2A publications require shared context")
    message = body.get("message") if isinstance(body, dict) else None
    text, attachments = _message_text(message)
    requested_context = str((message or {}).get("contextId") or "").strip()
    store = A2AStore.instance()
    context = store.resolve_context(publication, key["key_id"], requested_context)
    _ensure_isolated_conversation(publication, context)
    turn_id = "a2a:" + uuid.uuid4().hex
    task = store.create_task(
        publication["publication_id"], context["context_id"], key["key_id"],
        context["internal_conversation_id"], turn_id, body,
    )
    source = {
        "type": "a2a",
        "name": "A2A client",
        "target_agent": publication["agent_name"],
        "visibility": "target_only",
        "publication_id": publication["publication_id"],
        "context_id": context["context_id"],
        "task_id": task["task_id"],
    }
    try:
        submission = AgentRuntimeAPI.submit_message(AgentRequest(
            user_id=publication["owner_user_id"],
            conversation_id=context["internal_conversation_id"],
            target_agent=publication["agent_name"],
            message=text,
            attachments=attachments,
            msg_id=turn_id,
            channel="a2a",
            source_attributes={"message_source": json.dumps(source)},
        ))
        store.update_task(task["task_id"], "working")
    except Exception as exc:
        store.update_task(task["task_id"], "failed", error=str(exc))
        failed = store.get_task(publication["publication_id"], task["task_id"], key["key_id"])
        return public_task(failed or task)

    configuration = body.get("configuration") or {}
    return_immediately = bool(configuration.get("returnImmediately", True))
    task = store.get_task(publication["publication_id"], task["task_id"], key["key_id"]) or task
    if return_immediately or not submission.wait_for_done:
        threading.Thread(
            target=_finish_task, args=(store, task),
            name=f"a2a-task-{task['task_id'][:16]}", daemon=True,
        ).start()
    else:
        _finish_task(store, task)
        task = store.get_task(publication["publication_id"], task["task_id"], key["key_id"]) or task
    return public_task(task)
