"""OpenAI Chat Completions adapter for published PawFlow agents."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence

from core.standard_api_canonical import (
    canonical_request_fingerprint,
    canonical_visible_item,
    compute_hash_chain,
    eligible_prefixes,
)
from core.standard_api_contract import OPENAI_CHAT_ACCEPTED_FIELDS, OPENAI_SSE_DONE
from core.standard_api_runtime import (
    finalize_api_run_with_checkpoint,
    resolve_api_turn,
)
from core.standard_api_types import (
    ApiTurnResolution,
    NormalizedApiTurn,
    NormalizedVisibleItem,
    StandardApiNamespace,
)


logger = logging.getLogger(__name__)

_MAX_MESSAGES = 512
_MAX_TOOLS = 128
_MAX_TEXT_CHARS = 1_000_000
_KEEPALIVE_SECONDS = 15.0
_HEARTBEAT_SECONDS = 30.0
_HASH_DOMAIN_V1 = b"standard-agent-api-hash-v1"
_BOUNDARY_KINDS = frozenset({"assistant_message", "client_tool_call_batch"})
_COMPATIBILITY_NOOP_FIELDS = frozenset({
    "temperature",
    "top_p",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "stop",
    "user",
    "metadata",
    "max_tokens",
    "max_completion_tokens",
})


class OpenAIChatError(ValueError):
    """A pre-stream error with a native OpenAI error vocabulary."""

    def __init__(
            self,
            message: str,
            *,
            status: int = 400,
            error_type: str = "invalid_request_error",
            code: str = "invalid_request",
            param: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.error_type = error_type
        self.code = code
        self.param = param


class OpenAIChatRunError(RuntimeError):
    """A terminal run failure that happened after admission."""

    def __init__(
            self,
            message: str,
            *,
            status: int = 500,
            code: str = "agent_error",
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = code


def standard_api_hash_secret(version: int = 1) -> bytes:
    """Derive the versioned transcript key from PawFlow's master keyring."""

    if version != 1:
        raise ValueError("Unsupported standard API hash secret version")
    from core.secrets import get_secrets_manager
    return get_secrets_manager().derive_subkey(_HASH_DOMAIN_V1)


def _invalid(message: str, *, param: Optional[str] = None) -> OpenAIChatError:
    return OpenAIChatError(message, param=param)


def _content(
        value: Any,
        *,
        param: str,
        allow_empty: bool = True,
) -> str | list[Dict[str, str]]:
    if value is None and allow_empty:
        return ""
    if isinstance(value, str):
        if not allow_empty and not value:
            raise _invalid("Message content must not be empty", param=param)
        if len(value) > _MAX_TEXT_CHARS:
            raise _invalid("Message content exceeds its size limit", param=param)
        return value
    if not isinstance(value, list):
        raise _invalid("Message content must be text or an array", param=param)
    parts: list[Dict[str, str]] = []
    total = 0
    for index, part in enumerate(value):
        part_param = f"{param}.{index}"
        if not isinstance(part, Mapping):
            raise _invalid("Message content parts must be objects", param=part_param)
        unknown = sorted(set(part) - {"type", "text"})
        if unknown:
            raise _invalid(
                "Unsupported message content field: " + unknown[0],
                param=f"{part_param}.{unknown[0]}",
            )
        if part.get("type") != "text" or not isinstance(part.get("text"), str):
            raise _invalid(
                "Only text content parts are enabled for this publication",
                param=part_param,
            )
        total += len(part["text"])
        if total > _MAX_TEXT_CHARS:
            raise _invalid("Message content exceeds its size limit", param=param)
        parts.append({"type": "text", "text": part["text"]})
    if not allow_empty and not parts:
        raise _invalid("Message content must not be empty", param=param)
    return parts


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, (list, tuple)):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in value
        if isinstance(part, Mapping) and part.get("type") == "text"
    )


def _message_name(message: Mapping[str, Any], param: str) -> str:
    name = message.get("name")
    if name in (None, ""):
        return ""
    if not isinstance(name, str) or len(name) > 64:
        raise _invalid("Message name must be a string of at most 64 characters",
                       param=param)
    return name


def _assistant_calls(
        value: Any,
        *,
        param: str,
) -> list[Dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise _invalid("Assistant tool_calls must be a non-empty array", param=param)
    if len(value) > _MAX_TOOLS:
        raise _invalid("Assistant tool_calls exceed the batch limit", param=param)
    calls = []
    seen = set()
    for index, item in enumerate(value):
        item_param = f"{param}.{index}"
        if not isinstance(item, Mapping):
            raise _invalid("Assistant tool calls must be objects", param=item_param)
        unknown = sorted(set(item) - {"id", "type", "function"})
        if unknown:
            raise _invalid(
                "Unsupported assistant tool call field: " + unknown[0],
                param=f"{item_param}.{unknown[0]}",
            )
        call_id = item.get("id")
        function = item.get("function")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 256:
            raise _invalid("Assistant tool call id is required",
                           param=f"{item_param}.id")
        if call_id in seen:
            raise _invalid("Assistant tool call ids must be unique",
                           param=f"{item_param}.id")
        seen.add(call_id)
        if item.get("type") != "function" or not isinstance(function, Mapping):
            raise _invalid("Only function tool calls are supported", param=item_param)
        unknown_function = sorted(set(function) - {"name", "arguments"})
        if unknown_function:
            raise _invalid(
                "Unsupported function call field: " + unknown_function[0],
                param=f"{item_param}.function.{unknown_function[0]}",
            )
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not name:
            raise _invalid("Function call name is required",
                           param=f"{item_param}.function.name")
        if not isinstance(arguments, str):
            raise _invalid("Function call arguments must be a JSON string",
                           param=f"{item_param}.function.arguments")
        calls.append({"id": call_id, "name": name, "arguments": arguments})
    return calls


def _parse_messages(messages: Any) -> tuple[NormalizedVisibleItem, ...]:
    if not isinstance(messages, list) or not messages:
        raise _invalid("messages must be a non-empty array", param="messages")
    if len(messages) > _MAX_MESSAGES:
        raise _invalid("messages exceeds the request limit", param="messages")

    items: list[NormalizedVisibleItem] = []
    pending_call_ids: Optional[set[str]] = None
    index = 0
    while index < len(messages):
        message = messages[index]
        param = f"messages.{index}"
        if not isinstance(message, Mapping):
            raise _invalid("Each message must be an object", param=param)
        role = message.get("role")

        if role in {"system", "developer", "user"}:
            if pending_call_ids is not None:
                raise _invalid(
                    "Every assistant tool call requires a tool result before "
                    "the next message",
                    param=param,
                )
            allowed = {"role", "content", "name"}
            unknown = sorted(set(message) - allowed)
            if unknown:
                raise _invalid("Unsupported message field: " + unknown[0],
                               param=f"{param}.{unknown[0]}")
            content = _content(
                message.get("content"),
                param=f"{param}.content",
                allow_empty=False,
            )
            name = _message_name(message, f"{param}.name")
            data: Dict[str, Any] = {"content": content}
            if name:
                data["name"] = name
            if role in {"system", "developer"}:
                data["role"] = role
                items.append(NormalizedVisibleItem(
                    kind="client_instruction", data=data))
            else:
                items.append(NormalizedVisibleItem(
                    kind="user_message", data=data))
            index += 1
            continue

        if role == "assistant":
            if pending_call_ids is not None:
                raise _invalid(
                    "Every assistant tool call requires a tool result before "
                    "the next assistant message",
                    param=param,
                )
            allowed = {
                "role", "content", "name", "tool_calls", "refusal",
                "annotations",
            }
            unknown = sorted(set(message) - allowed)
            if unknown:
                raise _invalid("Unsupported assistant message field: " + unknown[0],
                               param=f"{param}.{unknown[0]}")
            if message.get("refusal") not in (None, ""):
                raise _invalid("Assistant refusal replay is not supported",
                               param=f"{param}.refusal")
            if message.get("annotations") not in (None, []):
                raise _invalid("Assistant annotations replay is not supported",
                               param=f"{param}.annotations")
            tool_calls = message.get("tool_calls")
            if tool_calls:
                if message.get("content") not in (None, ""):
                    raise _invalid(
                        "Assistant tool-call messages cannot also contain text",
                        param=f"{param}.content",
                    )
                calls = _assistant_calls(
                    tool_calls, param=f"{param}.tool_calls")
                pending_call_ids = {call["id"] for call in calls}
                items.append(NormalizedVisibleItem(
                    kind="client_tool_call_batch", data={"calls": calls}))
            else:
                content = _content(
                    message.get("content"),
                    param=f"{param}.content",
                    allow_empty=True,
                )
                data = {"content": content}
                name = _message_name(message, f"{param}.name")
                if name:
                    data["name"] = name
                items.append(NormalizedVisibleItem(
                    kind="assistant_message", data=data))
            index += 1
            continue

        if role == "tool":
            if pending_call_ids is None:
                raise _invalid(
                    "Tool results require a preceding assistant tool-call batch",
                    param=param,
                )
            results = []
            result_ids = set()
            while index < len(messages):
                tool_message = messages[index]
                tool_param = f"messages.{index}"
                if not isinstance(tool_message, Mapping):
                    raise _invalid("Each message must be an object", param=tool_param)
                if tool_message.get("role") != "tool":
                    break
                unknown = sorted(
                    set(tool_message) - {"role", "content", "tool_call_id"})
                if unknown:
                    raise _invalid(
                        "Unsupported tool message field: " + unknown[0],
                        param=f"{tool_param}.{unknown[0]}",
                    )
                call_id = tool_message.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise _invalid("tool_call_id is required",
                                   param=f"{tool_param}.tool_call_id")
                if call_id in result_ids:
                    raise _invalid("Tool results must be unique",
                                   param=f"{tool_param}.tool_call_id")
                result_ids.add(call_id)
                results.append({
                    "id": call_id,
                    "content": _content(
                        tool_message.get("content"),
                        param=f"{tool_param}.content",
                        allow_empty=True,
                    ),
                    "error": False,
                })
                index += 1
            if result_ids != pending_call_ids:
                raise _invalid(
                    "The request must contain exactly one result for every "
                    "assistant tool call",
                    param=param,
                )
            items.append(NormalizedVisibleItem(
                kind="client_tool_result_batch", data={"results": results}))
            pending_call_ids = None
            continue

        raise _invalid("Unsupported message role", param=f"{param}.role")

    if pending_call_ids is not None:
        raise _invalid(
            "Assistant tool calls must be followed by their tool results",
            param="messages",
        )
    if items[-1].kind in _BOUNDARY_KINDS:
        raise _invalid(
            "The final message must carry new client input or tool results",
            param="messages",
        )
    return tuple(items)


def _parse_tools(value: Any) -> tuple[Dict[str, Any], ...]:
    if value in (None, []):
        return ()
    if not isinstance(value, list):
        raise _invalid("tools must be an array", param="tools")
    if len(value) > _MAX_TOOLS:
        raise _invalid("tools exceeds the request limit", param="tools")
    definitions = []
    for index, item in enumerate(value):
        param = f"tools.{index}"
        if not isinstance(item, Mapping):
            raise _invalid("Tool definitions must be objects", param=param)
        unknown = sorted(set(item) - {"type", "function"})
        if unknown:
            raise _invalid("Unsupported tool field: " + unknown[0],
                           param=f"{param}.{unknown[0]}")
        function = item.get("function")
        if item.get("type") != "function" or not isinstance(function, Mapping):
            raise _invalid("Only function tools are supported", param=param)
        unknown_function = sorted(
            set(function) - {"name", "description", "parameters", "strict"})
        if unknown_function:
            raise _invalid(
                "Unsupported function definition field: " + unknown_function[0],
                param=f"{param}.function.{unknown_function[0]}",
            )
        if function.get("strict") not in (None, False):
            raise _invalid(
                "Strict function schemas are not supported",
                param=f"{param}.function.strict",
            )
        if "parameters" not in function:
            raise _invalid(
                "Function parameters are required",
                param=f"{param}.function.parameters",
            )
        definitions.append({
            "name": function.get("name"),
            "description": function.get("description") or "",
            "parameters": function.get("parameters"),
        })
    from core.client_tools import validate_client_tool_definitions
    try:
        return validate_client_tool_definitions(definitions)
    except ValueError as exc:
        raise _invalid(str(exc), param="tools") from exc


def _strict_noops(publication: Mapping[str, Any], body: Mapping[str, Any]) -> None:
    if not publication.get("strict_fields"):
        return
    for field_name in sorted(_COMPATIBILITY_NOOP_FIELDS):
        if field_name not in body:
            continue
        value = body[field_name]
        if value not in (None, 0, "", [], {}):
            raise _invalid(
                f"{field_name} is unavailable in strict field mode",
                param=field_name,
            )


def parse_openai_chat_request(
        publication: Mapping[str, Any],
        key: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        request_id: str,
        hash_secret: Optional[bytes] = None,
) -> tuple[NormalizedApiTurn, bool]:
    """Validate one Chat request completely before session lookup."""

    if not isinstance(body, Mapping):
        raise _invalid("The request body must be a JSON object")
    unknown = sorted(set(body) - OPENAI_CHAT_ACCEPTED_FIELDS)
    if unknown:
        raise _invalid("Unsupported request field: " + unknown[0],
                       param=unknown[0])
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise _invalid("model is required", param="model")
    if model != publication.get("api_model_id"):
        raise OpenAIChatError(
            "The requested model does not exist.",
            status=404,
            code="model_not_found",
            param="model",
        )
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise _invalid("stream must be a boolean", param="stream")
    stream_options = body.get("stream_options")
    include_usage = False
    if stream_options is not None:
        if not isinstance(stream_options, Mapping):
            raise _invalid("stream_options must be an object",
                           param="stream_options")
        unknown_stream = sorted(set(stream_options) - {"include_usage"})
        if unknown_stream:
            raise _invalid(
                "Unsupported stream option: " + unknown_stream[0],
                param=f"stream_options.{unknown_stream[0]}",
            )
        include_usage = stream_options.get("include_usage", False)
        if not isinstance(include_usage, bool):
            raise _invalid("include_usage must be a boolean",
                           param="stream_options.include_usage")
        if not stream and include_usage:
            raise _invalid("include_usage requires stream=true",
                           param="stream_options.include_usage")

    n = body.get("n", 1)
    if n is None:
        n = 1
    if isinstance(n, bool) or not isinstance(n, int) or n != 1:
        raise _invalid("Only n=1 is supported", param="n")
    if body.get("store") not in (None, False):
        raise _invalid("Stored Chat Completions are not supported", param="store")
    response_format = body.get("response_format")
    if response_format not in (None, {"type": "text"}):
        raise _invalid("Only text response_format is supported",
                       param="response_format")
    parallel = body.get("parallel_tool_calls")
    if parallel is not None and not isinstance(parallel, bool):
        raise _invalid("parallel_tool_calls must be a boolean",
                       param="parallel_tool_calls")
    if parallel is False:
        raise _invalid(
            "parallel_tool_calls=false cannot be enforced by this publication",
            param="parallel_tool_calls",
        )
    tool_choice = body.get("tool_choice", "auto")
    if not isinstance(tool_choice, str) or tool_choice not in {"auto", "none"}:
        raise _invalid(
            "Only tool_choice values 'auto' and 'none' are supported",
            param="tool_choice",
        )

    _strict_noops(publication, body)
    visible_items = _parse_messages(body.get("messages"))
    tools = _parse_tools(body.get("tools"))
    runtime_tools = () if tool_choice == "none" else tools
    last_boundary = -1
    for index, item in enumerate(visible_items):
        if item.kind in _BOUNDARY_KINDS:
            last_boundary = index
    actionable_suffix_start = last_boundary + 1
    secret = hash_secret or standard_api_hash_secret(1)
    namespace = StandardApiNamespace(
        publication_id=str(publication["publication_id"]),
        api_generation=int(publication["api_generation"]),
        key_id=str(key["key_id"]),
        dialect="chat_completions",
        api_model_id=model,
    )
    fingerprint = canonical_request_fingerprint({
        "model": model,
        "visible_items": [
            canonical_visible_item(item) for item in visible_items
        ],
        "tools": list(tools),
        "tool_choice": tool_choice,
    }, secret)
    return NormalizedApiTurn(
        namespace=namespace,
        visible_items=visible_items,
        actionable_suffix_start=actionable_suffix_start,
        request_id=request_id,
        body_fingerprint=fingerprint,
        client_tools=runtime_tools,
        tool_choice=tool_choice,
        stream=stream,
    ), include_usage


def _completion_id(run_id: str) -> str:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    return "chatcmpl_" + digest


def _tool_arguments(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _public_tool_calls(calls: Sequence[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    return [{
        "id": str(call.get("id") or ""),
        "type": "function",
        "function": {
            "name": str(call.get("name") or ""),
            "arguments": _tool_arguments(call.get("arguments", {})),
        },
    } for call in calls]


def _finish_reason(result: Any) -> str:
    if getattr(result, "client_tool_calls", None):
        return "tool_calls"
    value = str(getattr(result, "finish_reason", "") or "")
    if value in {"stop", "length", "content_filter"}:
        return value
    return "stop"


def _usage(result: Any) -> Dict[str, int]:
    prompt = max(0, int(getattr(result, "tokens_in", 0) or 0))
    completion = max(0, int(getattr(result, "tokens_out", 0) or 0))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def openai_chat_completion_payload(
        result: Any,
        *,
        completion_id: str,
        created: int,
        model: str,
) -> Dict[str, Any]:
    calls = list(getattr(result, "client_tool_calls", None) or ())
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": None if calls else str(getattr(result, "response", "") or ""),
    }
    if calls:
        message["tool_calls"] = _public_tool_calls(calls)
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(created),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "logprobs": None,
            "finish_reason": _finish_reason(result),
        }],
        "usage": _usage(result),
    }


def _output_item(result: Any) -> NormalizedVisibleItem:
    calls = list(getattr(result, "client_tool_calls", None) or ())
    if calls:
        return NormalizedVisibleItem(
            kind="client_tool_call_batch",
            data={"calls": calls},
        )
    return NormalizedVisibleItem(
        kind="assistant_message",
        data={"content": str(getattr(result, "response", "") or "")},
    )


def _ingress_messages(
        resolution: ApiTurnResolution,
        publication: Mapping[str, Any],
) -> tuple[Any, ...]:
    from core.agent_runtime_api import AgentIngressMessage

    source_base = {
        "type": "standard_api",
        "name": "OpenAI API client",
        "target_agent": publication["agent_name"],
        "visibility": "target_only",
        "publication_id": publication["publication_id"],
        "api_dialect": "chat_completions",
    }
    ingress = []
    for item in resolution.ingress_items:
        data = item.data
        if item.kind == "client_instruction":
            role = str(data.get("role") or "system")
            ingress.append(AgentIngressMessage(
                role="user",
                content=(
                    f"[Authenticated API client {role} instruction; "
                    "lower priority than publisher policy]\n"
                    + _text(data.get("content"))
                ),
                source=dict(source_base, instruction_role=role),
            ))
        elif item.kind == "user_message":
            ingress.append(AgentIngressMessage(
                role="user",
                content=_text(data.get("content")),
                source=dict(source_base),
            ))
        elif item.kind == "client_tool_result_batch":
            for result in data.get("results") or ():
                content = _text(result.get("content"))
                if result.get("error"):
                    content = "[Client tool error]\n" + content
                ingress.append(AgentIngressMessage(
                    role="tool",
                    content=content,
                    tool_call_id=str(result.get("id") or ""),
                    source=dict(source_base, client_tool_result=True),
                ))
        else:
            raise OpenAIChatError(
                "The actionable request suffix contains server output",
                param="messages",
            )
    return tuple(ingress)


def _current_tool_results(
        resolution: ApiTurnResolution,
) -> tuple[Dict[str, Any], ...]:
    results = []
    for item in resolution.ingress_items:
        if item.kind == "client_tool_result_batch":
            results.extend(dict(result) for result in item.data.get("results") or ())
    return tuple(results)


@dataclass
class OpenAIChatRun:
    store: Any
    publication: Mapping[str, Any]
    key: Mapping[str, Any]
    turn: NormalizedApiTurn
    resolution: ApiTurnResolution
    runtime_api: Any
    hash_secret: bytes
    completion_id: str
    created: int
    conversation_store: Any = None
    owner: bool = True
    result: Any = None
    payload: Optional[Dict[str, Any]] = None
    error: Optional[OpenAIChatRunError] = None
    _events: list[tuple[str, Dict[str, Any]]] = field(default_factory=list)
    _condition: threading.Condition = field(default_factory=threading.Condition)
    _done: threading.Event = field(default_factory=threading.Event)
    _cancel_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def publish(self, _cid: str, event_type: str, data: Any) -> None:
        if not isinstance(data, Mapping):
            return
        event_data = dict(data)
        try:
            self.store.append_api_run_event(
                self.resolution.run["run_id"],
                {"event_type": str(event_type), "data": event_data},
            )
        except Exception:
            logger.debug("Could not append Chat replay event", exc_info=True)
        with self._condition:
            self._events.append((str(event_type), event_data))
            self._condition.notify_all()

    def event_slice(
            self,
            offset: int,
            *,
            timeout: float,
    ) -> tuple[list[tuple[str, Dict[str, Any]]], int, bool]:
        with self._condition:
            if offset >= len(self._events) and not self.done:
                self._condition.wait(timeout=timeout)
            events = list(self._events[offset:])
            return events, len(self._events), self.done

    def complete(self, result: Any, payload: Dict[str, Any]) -> None:
        self.result = result
        self.payload = payload
        self._done.set()
        with self._condition:
            self._condition.notify_all()

    def fail(self, error: OpenAIChatRunError) -> None:
        self.error = error
        self._done.set()
        with self._condition:
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._cancel_lock:
            if self.done:
                return
            try:
                from tasks.ai.agent_loop import AgentLoopTask
                AgentLoopTask.force_stop_agent(
                    self.resolution.session["internal_conversation_id"],
                    self.publication["agent_name"],
                )
            except Exception:
                logger.warning("Could not force-stop disconnected Chat run",
                               exc_info=True)
            try:
                self.store.fail_api_run(
                    self.resolution.run["run_id"],
                    self.resolution.lease_id,
                    error_code="client_disconnected",
                    canceled=True,
                )
            except Exception:
                logger.debug("Could not cancel disconnected Chat lease",
                             exc_info=True)
            self.fail(OpenAIChatRunError(
                "The client disconnected.", status=499,
                code="client_disconnected"))


@dataclass(frozen=True)
class OpenAIChatAdmission:
    run: OpenAIChatRun
    include_usage: bool
    owner: bool


_ACTIVE_RUNS: Dict[str, OpenAIChatRun] = {}
_ACTIVE_RUNS_LOCK = threading.RLock()


def _fail_lease(run: OpenAIChatRun, code: str, *, canceled: bool = False) -> None:
    try:
        run.store.fail_api_run(
            run.resolution.run["run_id"],
            run.resolution.lease_id,
            error_code=code,
            canceled=canceled,
        )
    except Exception:
        logger.debug("Could not fail Chat run lease", exc_info=True)


def _drive_run(run: OpenAIChatRun, initial_result: Any = None) -> None:
    try:
        result = initial_result
        while result is None:
            result = run.runtime_api.wait_for_done(
                run.resolution.session["internal_conversation_id"],
                run.resolution.run["run_id"],
                timeout=_HEARTBEAT_SECONDS,
            )
            if result is None:
                if not run.store.heartbeat_api_run(
                        run.resolution.run["run_id"],
                        run.resolution.lease_id):
                    raise OpenAIChatRunError(
                        "The agent run lease was lost.",
                        code="run_lost",
                    )
        if getattr(result, "error", ""):
            raise OpenAIChatRunError(
                "The published agent run failed.",
                code="agent_error",
            )
        visible_items = run.turn.visible_items + (_output_item(result),)
        heads = compute_hash_chain(
            run.turn.namespace, visible_items, run.hash_secret)
        prefixes = eligible_prefixes(visible_items, heads)
        finalize_api_run_with_checkpoint(
            run.store,
            run.conversation_store,
            str(run.resolution.session["internal_conversation_id"]),
            run.resolution.run["run_id"],
            run.resolution.lease_id,
            visible_head_hash=heads[-1],
            item_count=len(visible_items),
            prefixes=prefixes[-1:],
            pending_client_tool_calls=tuple(
                getattr(result, "client_tool_calls", None) or ()),
            client_tool_definitions=run.turn.client_tools,
        )
        payload = openai_chat_completion_payload(
            result,
            completion_id=run.completion_id,
            created=run.created,
            model=str(run.publication["api_model_id"]),
        )
        run.complete(result, payload)
    except OpenAIChatRunError as exc:
        _fail_lease(run, exc.code)
        run.fail(exc)
    except Exception:
        logger.exception("OpenAI Chat run finalization failed")
        _fail_lease(run, "internal_error")
        run.fail(OpenAIChatRunError(
            "The published agent run failed.",
            code="internal_error",
        ))
    finally:
        with _ACTIVE_RUNS_LOCK:
            if _ACTIVE_RUNS.get(run.resolution.run["run_id"]) is run:
                _ACTIVE_RUNS.pop(run.resolution.run["run_id"], None)


def prepare_openai_chat_run(
        store: Any,
        publication: Mapping[str, Any],
        key: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        request_id: str,
        conversation_store: Any = None,
        runtime_api: Any = None,
        hash_secret: Optional[bytes] = None,
) -> OpenAIChatAdmission:
    """Validate, resolve, settle, and submit before the HTTP stream opens."""

    from core.agent_runtime_api import (
        AgentRuntimeAPI,
        AgentStructuredRequest,
    )
    from core._a2a_standard_api import (
        ApiRunQuotaExceeded,
        ApiSessionQuotaExceeded,
    )

    runtime = runtime_api or AgentRuntimeAPI
    secret = hash_secret or standard_api_hash_secret(1)
    turn, include_usage = parse_openai_chat_request(
        publication, key, body,
        request_id=request_id,
        hash_secret=secret,
    )
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    with _ACTIVE_RUNS_LOCK:
        try:
            resolution = resolve_api_turn(
                store,
                publication,
                key,
                turn,
                hash_secret=secret,
                conversation_store=conversation_store,
            )
        except (ApiSessionQuotaExceeded, ApiRunQuotaExceeded) as exc:
            raise OpenAIChatError(
                "The publication API quota has been reached.",
                status=429,
                error_type="rate_limit_error",
                code="rate_limit_exceeded",
            ) from exc
        except ValueError as exc:
            raise _invalid(str(exc)) from exc

        run_id = str(resolution.run["run_id"])
        if resolution.outcome == "attached":
            active = _ACTIVE_RUNS.get(run_id)
            if active is None:
                raise OpenAIChatError(
                    "The active retry stream is unavailable; retry the request.",
                    status=409,
                    code="run_conflict",
                )
            return OpenAIChatAdmission(
                run=active, include_usage=include_usage, owner=False)

        run = OpenAIChatRun(
            store=store,
            conversation_store=conversation_store,
            publication=dict(publication),
            key=dict(key),
            turn=turn,
            resolution=resolution,
            runtime_api=runtime,
            hash_secret=secret,
            completion_id=_completion_id(run_id),
            created=int(float(resolution.run.get("started_at") or time.time())),
        )
        _ACTIVE_RUNS[run_id] = run
        try:
            current_results = _current_tool_results(resolution)
            pending = store.get_pending_api_tool_batch(
                resolution.session["session_id"])
            if pending is not None and not current_results:
                raise _invalid(
                    "The request must settle every pending client tool call",
                    param="messages",
                )
            if current_results:
                store.settle_api_tool_batch(
                    run_id,
                    resolution.lease_id,
                    results=current_results,
                    client_tool_definitions=turn.client_tools,
                )
            ingress = _ingress_messages(resolution, publication)
            source = {
                "type": "standard_api",
                "name": "OpenAI API client",
                "target_agent": publication["agent_name"],
                "visibility": "target_only",
                "publication_id": publication["publication_id"],
                "key_id": key["key_id"],
                "api_dialect": "chat_completions",
                "api_run_id": run_id,
            }
            submission = runtime.submit_structured(AgentStructuredRequest(
                user_id=str(publication["owner_user_id"]),
                conversation_id=str(
                    resolution.session["internal_conversation_id"]),
                target_agent=str(publication["agent_name"]),
                ingress_messages=ingress,
                client_tools=turn.client_tools,
                tool_choice=turn.tool_choice,
                provider_overrides=turn.provider_overrides,
                msg_id=run_id,
                channel="standard_api",
                permission_mode=str(publication["api_permission_mode"]),
                source_attributes={
                    "message_source": json.dumps(
                        source, ensure_ascii=False, sort_keys=True),
                },
                live_callback=run.publish,
            ))
        except OpenAIChatError:
            _ACTIVE_RUNS.pop(run_id, None)
            _fail_lease(run, "invalid_tool_results")
            raise
        except ValueError as exc:
            _ACTIVE_RUNS.pop(run_id, None)
            _fail_lease(run, "invalid_request")
            raise _invalid(str(exc)) from exc
        except Exception as exc:
            _ACTIVE_RUNS.pop(run_id, None)
            _fail_lease(run, "submission_failed")
            raise OpenAIChatError(
                "The published agent could not start the request.",
                status=503,
                error_type="server_error",
                code="submission_failed",
            ) from exc

        initial_result = None
        if not submission.wait_for_done:
            from core.agent_runtime_api import AgentFinalResult
            initial_result = AgentFinalResult(
                conversation_id=submission.conversation_id,
                turn_id=submission.turn_id,
                response=submission.response,
                final_msg_id=submission.final_msg_id,
            )
        threading.Thread(
            target=_drive_run,
            args=(run, initial_result),
            daemon=True,
            name=f"openai-chat-{run_id[:16]}",
        ).start()
        return OpenAIChatAdmission(
            run=run, include_usage=include_usage, owner=True)


def wait_openai_chat_payload(run: OpenAIChatRun) -> Dict[str, Any]:
    run._done.wait()
    if run.error is not None:
        raise run.error
    if run.payload is None:
        raise OpenAIChatRunError(
            "The published agent run ended without a response.",
            code="run_lost",
        )
    return run.payload


def _chunk(
        run: OpenAIChatRun,
        *,
        delta: Optional[Mapping[str, Any]] = None,
        finish_reason: Optional[str] = None,
        choices: Optional[list[Dict[str, Any]]] = None,
        usage: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if choices is None:
        choices = [{
            "index": 0,
            "delta": dict(delta or {}),
            "logprobs": None,
            "finish_reason": finish_reason,
        }]
    value: Dict[str, Any] = {
        "id": run.completion_id,
        "object": "chat.completion.chunk",
        "created": run.created,
        "model": str(run.publication["api_model_id"]),
        "choices": choices,
    }
    if usage is not None:
        value["usage"] = dict(usage)
    return value


def _sse(value: Mapping[str, Any]) -> bytes:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return ("data: " + payload + "\n\n").encode("utf-8")


def iter_openai_chat_sse(
        admission: OpenAIChatAdmission,
) -> Iterator[bytes]:
    """Translate one shared run to OpenAI Chat Completions SSE."""

    run = admission.run
    offset = 0
    emitted_text = ""
    streamed_ids = set()
    normal_end = False
    try:
        yield _sse(_chunk(run, delta={"role": "assistant", "content": ""}))
        while True:
            events, offset, done = run.event_slice(
                offset, timeout=_KEEPALIVE_SECONDS)
            if not events and not done:
                yield b": ping\n\n"
                continue
            for event_type, data in events:
                if event_type == "token":
                    delta = str(data.get("text") or "")
                    msg_id = str(data.get("msg_id") or "")
                    if not delta:
                        continue
                    if msg_id:
                        streamed_ids.add(msg_id)
                    emitted_text += delta
                    yield _sse(_chunk(run, delta={"content": delta}))
                elif event_type == "new_message":
                    if data.get("role") != "assistant":
                        continue
                    msg_id = str(data.get("msg_id") or "")
                    content = str(data.get("content") or "")
                    if not content or (msg_id and msg_id in streamed_ids):
                        continue
                    if not emitted_text:
                        emitted_text = content
                        yield _sse(_chunk(run, delta={"content": content}))
            if done and offset >= len(run._events):
                break

        if run.error is not None:
            yield _sse({"error": {
                "message": str(run.error),
                "type": "server_error",
                "param": None,
                "code": run.error.code,
            }})
            normal_end = True
            yield OPENAI_SSE_DONE.encode("utf-8")
            return

        result = run.result
        calls = list(getattr(result, "client_tool_calls", None) or ())
        if calls:
            for index, call in enumerate(_public_tool_calls(calls)):
                yield _sse(_chunk(run, delta={"tool_calls": [{
                    "index": index,
                    **call,
                }]}))
        else:
            final_text = str(getattr(result, "response", "") or "")
            if final_text.startswith(emitted_text):
                remainder = final_text[len(emitted_text):]
                if remainder:
                    yield _sse(_chunk(run, delta={"content": remainder}))
            elif not emitted_text and final_text:
                yield _sse(_chunk(run, delta={"content": final_text}))
        yield _sse(_chunk(
            run, delta={}, finish_reason=_finish_reason(result)))
        if admission.include_usage:
            yield _sse(_chunk(
                run, choices=[], usage=_usage(result)))
        normal_end = True
        yield OPENAI_SSE_DONE.encode("utf-8")
    finally:
        if (not normal_end and admission.owner
                and run.publication.get("api_disconnect_policy") == "cancel"):
            run.cancel()


__all__ = [
    "OpenAIChatAdmission",
    "OpenAIChatError",
    "OpenAIChatRun",
    "OpenAIChatRunError",
    "iter_openai_chat_sse",
    "openai_chat_completion_payload",
    "parse_openai_chat_request",
    "prepare_openai_chat_run",
    "standard_api_hash_secret",
    "wait_openai_chat_payload",
]
