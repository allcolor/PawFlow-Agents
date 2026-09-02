"""OpenAI Responses adapter for published PawFlow agents."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence

from core.standard_api_canonical import (
    canonical_request_fingerprint,
    canonical_visible_item,
    compute_hash_chain,
    eligible_prefixes,
)
from core.standard_api_chat import standard_api_hash_secret
from core.standard_api_contract import OPENAI_RESPONSES_ACCEPTED_FIELDS
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

_MAX_ITEMS = 512
_MAX_TOOLS = 128
_MAX_TEXT_CHARS = 1_000_000
_KEEPALIVE_SECONDS = 15.0
_HEARTBEAT_SECONDS = 30.0
_BOUNDARY_KINDS = frozenset({
    "assistant_message",
    "client_tool_call_batch",
    "response_output",
})
_COMPATIBILITY_NOOP_FIELDS = frozenset({
    "temperature",
    "top_p",
    "max_output_tokens",
})


class OpenAIResponsesError(ValueError):
    """A pre-stream error using OpenAI's public error vocabulary."""

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


class OpenAIResponsesRunError(RuntimeError):
    """A terminal Responses failure that happened after admission."""

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


@dataclass(frozen=True)
class ParsedOpenAIResponse:
    """Validated request options kept outside the durable visible chain."""

    turn: NormalizedApiTurn
    instructions: str
    store: bool
    public_tools: tuple[Dict[str, Any], ...]
    metadata: Dict[str, Any]
    temperature: Optional[float]
    top_p: Optional[float]
    max_output_tokens: Optional[int]


def _invalid(
        message: str,
        *,
        param: Optional[str] = None,
) -> OpenAIResponsesError:
    return OpenAIResponsesError(message, param=param)


def _bounded_text(value: Any, *, param: str, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise _invalid("Text content must be a string", param=param)
    if not allow_empty and not value:
        raise _invalid("Text content must not be empty", param=param)
    if len(value) > _MAX_TEXT_CHARS:
        raise _invalid("Text content exceeds its size limit", param=param)
    return value


def _content_text(
        value: Any,
        *,
        param: str,
        allow_empty: bool = True,
) -> str:
    if isinstance(value, str):
        return _bounded_text(value, param=param, allow_empty=allow_empty)
    if not isinstance(value, list):
        raise _invalid("Message content must be text or an array", param=param)
    chunks = []
    total = 0
    for index, part in enumerate(value):
        part_param = f"{param}.{index}"
        if not isinstance(part, Mapping):
            raise _invalid("Message content parts must be objects", param=part_param)
        part_type = part.get("type")
        allowed = {"type", "text"}
        if part_type == "output_text":
            allowed |= {"annotations", "logprobs"}
        unknown = sorted(set(part) - allowed)
        if unknown:
            raise _invalid(
                "Unsupported message content field: " + unknown[0],
                param=f"{part_param}.{unknown[0]}",
            )
        if part_type not in {"input_text", "output_text", "text"}:
            raise _invalid(
                "Only text content parts are enabled for this publication",
                param=part_param,
            )
        text = part.get("text")
        if not isinstance(text, str):
            raise _invalid("Text content parts require text", param=f"{part_param}.text")
        total += len(text)
        if total > _MAX_TEXT_CHARS:
            raise _invalid("Message content exceeds its size limit", param=param)
        chunks.append(text)
    result = "".join(chunks)
    if not allow_empty and not result:
        raise _invalid("Message content must not be empty", param=param)
    return result


def _parse_message(item: Mapping[str, Any], *, param: str) -> NormalizedVisibleItem:
    allowed = {"type", "role", "content", "id", "status"}
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise _invalid(
            "Unsupported message item field: " + unknown[0],
            param=f"{param}.{unknown[0]}",
        )
    if item.get("type") not in (None, "message"):
        raise _invalid("Input message type must be 'message'", param=f"{param}.type")
    status = item.get("status")
    if status not in (None, "completed"):
        raise _invalid(
            "Historical input messages must be completed",
            param=f"{param}.status",
        )
    item_id = item.get("id")
    if item_id is not None and (
            not isinstance(item_id, str) or not item_id or len(item_id) > 256):
        raise _invalid("Message item id must be bounded", param=f"{param}.id")
    role = item.get("role")
    if role not in {"system", "developer", "user", "assistant"}:
        raise _invalid("Unsupported message role", param=f"{param}.role")
    content = _content_text(
        item.get("content"),
        param=f"{param}.content",
        allow_empty=role == "assistant",
    )
    if role in {"system", "developer"}:
        return NormalizedVisibleItem(
            kind="client_instruction",
            data={"role": role, "content": content},
        )
    if role == "user":
        return NormalizedVisibleItem(
            kind="user_message",
            data={"content": content},
        )
    return NormalizedVisibleItem(
        kind="assistant_message",
        data={"content": content},
    )


def _native_output_message(
        item: Mapping[str, Any],
        *,
        param: str,
) -> Dict[str, Any]:
    allowed = {"type", "role", "content", "id", "status"}
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise _invalid(
            "Unsupported message item field: " + unknown[0],
            param=f"{param}.{unknown[0]}",
        )
    if item.get("type") not in (None, "message"):
        raise _invalid("Input message type must be 'message'", param=f"{param}.type")
    if item.get("role") != "assistant":
        raise _invalid(
            "Response output messages must use the assistant role",
            param=f"{param}.role",
        )
    status = item.get("status")
    if status not in (None, "completed"):
        raise _invalid(
            "Historical response output must be completed",
            param=f"{param}.status",
        )
    item_id = item.get("id")
    if item_id is not None and (
            not isinstance(item_id, str) or not item_id
            or len(item_id) > 256):
        raise _invalid("Message item id must be bounded", param=f"{param}.id")
    content = item.get("content")
    if isinstance(content, str):
        parts = [{
            "type": "output_text",
            "annotations": [],
            "text": _bounded_text(
                content, param=f"{param}.content", allow_empty=True),
        }]
    elif isinstance(content, list):
        parts = []
        total = 0
        for index, part in enumerate(content):
            part_param = f"{param}.content.{index}"
            if not isinstance(part, Mapping):
                raise _invalid(
                    "Response output content parts must be objects",
                    param=part_param,
                )
            unknown_part = sorted(
                set(part) - {"type", "text", "annotations", "logprobs"})
            if unknown_part:
                raise _invalid(
                    "Unsupported response output content field: "
                    + unknown_part[0],
                    param=f"{part_param}.{unknown_part[0]}",
                )
            if part.get("type") not in {"output_text", "text"}:
                raise _invalid(
                    "Only output_text response content is supported",
                    param=part_param,
                )
            text = part.get("text")
            annotations = part.get("annotations", [])
            if not isinstance(text, str) or not isinstance(annotations, list):
                raise _invalid(
                    "output_text requires text and an annotations array",
                    param=part_param,
                )
            total += len(text)
            if total > _MAX_TEXT_CHARS:
                raise _invalid(
                    "Response output exceeds its size limit",
                    param=f"{param}.content",
                )
            normalized_part = {
                "type": "output_text",
                "annotations": list(annotations),
                "text": text,
            }
            if "logprobs" in part:
                if not isinstance(part["logprobs"], list):
                    raise _invalid(
                        "output_text logprobs must be an array",
                        param=f"{part_param}.logprobs",
                    )
                normalized_part["logprobs"] = list(part["logprobs"])
            parts.append(normalized_part)
    else:
        raise _invalid(
            "Response output message content must be text or an array",
            param=f"{param}.content",
        )
    normalized = {
        "type": "message",
        "role": "assistant",
        "content": parts,
    }
    if item_id is not None:
        normalized["id"] = item_id
    if status is not None:
        normalized["status"] = status
    return normalized


def _native_function_call(
        item: Mapping[str, Any],
        *,
        param: str,
) -> tuple[Dict[str, Any], str]:
    unknown = sorted(
        set(item) - {"type", "id", "call_id", "name", "arguments", "status"})
    if unknown:
        raise _invalid(
            "Unsupported function call field: " + unknown[0],
            param=f"{param}.{unknown[0]}",
        )
    if item.get("status") not in (None, "completed"):
        raise _invalid(
            "Historical function calls must be completed",
            param=f"{param}.status",
        )
    item_id = item.get("id")
    call_id = item.get("call_id")
    name = item.get("name")
    arguments = item.get("arguments")
    if item_id is not None and (
            not isinstance(item_id, str) or not item_id
            or len(item_id) > 256):
        raise _invalid("Function item id must be bounded", param=f"{param}.id")
    if (not isinstance(call_id, str) or not call_id
            or len(call_id) > 256):
        raise _invalid("Function call_id is required", param=f"{param}.call_id")
    if not isinstance(name, str) or not name:
        raise _invalid("Function name is required", param=f"{param}.name")
    if not isinstance(arguments, str) or len(arguments) > _MAX_TEXT_CHARS:
        raise _invalid(
            "Function arguments must be a bounded JSON string",
            param=f"{param}.arguments",
        )
    normalized = {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }
    if item_id is not None:
        normalized["id"] = item_id
    if item.get("status") is not None:
        normalized["status"] = item["status"]
    return normalized, call_id


def _parse_response_output(
        value: Sequence[Mapping[str, Any]],
        *,
        start: int,
) -> tuple[NormalizedVisibleItem, int, set[str]]:
    output = []
    call_ids = set()
    index = start
    while index < len(value):
        item = value[index]
        if not isinstance(item, Mapping):
            break
        item_type = item.get("type")
        if item_type in (None, "message") and item.get("role") == "assistant":
            output.append(_native_output_message(
                item, param=f"input.{index}"))
        elif item_type == "function_call":
            call, call_id = _native_function_call(
                item, param=f"input.{index}")
            if call_id in call_ids:
                raise _invalid(
                    "Function call ids must be unique",
                    param=f"input.{index}.call_id",
                )
            call_ids.add(call_id)
            output.append(call)
        else:
            break
        index += 1
    return (
        NormalizedVisibleItem(
            kind="response_output",
            data={"output": output},
        ),
        index,
        call_ids,
    )


def _parse_function_outputs(
        value: Sequence[Mapping[str, Any]],
        *,
        start: int,
) -> tuple[NormalizedVisibleItem, int, set[str]]:
    results = []
    seen = set()
    index = start
    while index < len(value) and (
            isinstance(value[index], Mapping)
            and value[index].get("type") == "function_call_output"):
        item = value[index]
        param = f"input.{index}"
        unknown = sorted(
            set(item) - {"type", "id", "call_id", "output", "status"})
        if unknown:
            raise _invalid(
                "Unsupported function call output field: " + unknown[0],
                param=f"{param}.{unknown[0]}",
            )
        if item.get("status") not in (None, "completed"):
            raise _invalid(
                "Function call outputs must be completed",
                param=f"{param}.status",
            )
        call_id = item.get("call_id")
        if (not isinstance(call_id, str) or not call_id
                or len(call_id) > 256):
            raise _invalid(
                "Function call output requires call_id",
                param=f"{param}.call_id",
            )
        if call_id in seen:
            raise _invalid(
                "Function call outputs must be unique",
                param=f"{param}.call_id",
            )
        seen.add(call_id)
        results.append({
            "id": call_id,
            "content": _content_text(
                item.get("output"),
                param=f"{param}.output",
                allow_empty=True,
            ),
            "error": False,
        })
        index += 1
    return (
        NormalizedVisibleItem(
            kind="client_tool_result_batch",
            data={"results": results},
        ),
        index,
        seen,
    )


def _parse_input(
        value: Any,
        *,
        previous_response_id: str,
) -> tuple[tuple[NormalizedVisibleItem, ...], int]:
    if isinstance(value, str):
        text = _bounded_text(value, param="input", allow_empty=False)
        return (
            NormalizedVisibleItem(
                kind="user_message", data={"content": text}),), 0
    if not isinstance(value, list) or not value:
        raise _invalid("input must be a non-empty string or array", param="input")
    if len(value) > _MAX_ITEMS:
        raise _invalid("input exceeds the request limit", param="input")

    items = []
    pending_call_ids: Optional[set[str]] = None
    index = 0
    while index < len(value):
        raw = value[index]
        param = f"input.{index}"
        if not isinstance(raw, Mapping):
            raise _invalid("Input items must be objects", param=param)
        item_type = raw.get("type")
        if item_type in (None, "message"):
            if raw.get("role") == "assistant":
                if previous_response_id:
                    raise _invalid(
                        "Response output history cannot be combined with "
                        "previous_response_id",
                        param=f"{param}.role",
                    )
                if pending_call_ids is not None:
                    raise _invalid(
                        "Every function call requires an output before "
                        "another response output",
                        param=param,
                    )
                batch, index, call_ids = _parse_response_output(
                    value, start=index)
                pending_call_ids = call_ids or None
                items.append(batch)
                continue
            message = _parse_message(raw, param=param)
            if previous_response_id and message.kind != "user_message":
                raise _invalid(
                    "previous_response_id accepts only new user messages or "
                    "function call outputs",
                    param=f"{param}.role",
                )
            if pending_call_ids is not None:
                raise _invalid(
                    "Every function call requires an output before the next message",
                    param=param,
                )
            items.append(message)
            index += 1
            continue
        if item_type == "function_call":
            if previous_response_id:
                raise _invalid(
                    "Function call history cannot be combined with "
                    "previous_response_id",
                    param=param,
                )
            if pending_call_ids is not None:
                raise _invalid(
                    "Every function call requires an output before another call",
                    param=param,
                )
            batch, index, call_ids = _parse_response_output(
                value, start=index)
            pending_call_ids = call_ids or None
            items.append(batch)
            continue
        if item_type == "function_call_output":
            batch, index, result_ids = _parse_function_outputs(
                value, start=index)
            if not previous_response_id:
                if pending_call_ids is None:
                    raise _invalid(
                        "Function call outputs require preceding function calls",
                        param=param,
                    )
                if result_ids != pending_call_ids:
                    raise _invalid(
                        "The input must contain exactly one output for every "
                        "function call",
                        param=param,
                    )
            pending_call_ids = None
            items.append(batch)
            continue
        raise _invalid("Unsupported input item type", param=f"{param}.type")

    if pending_call_ids is not None:
        raise _invalid(
            "Historical function calls must be followed by their outputs",
            param="input",
        )
    last_boundary = -1
    for item_index, item in enumerate(items):
        if item.kind in _BOUNDARY_KINDS:
            last_boundary = item_index
    actionable_suffix_start = last_boundary + 1
    if items[-1].kind in {"client_instruction", "user_message"}:
        actionable_suffix_start = len(items) - 1
        while (
                actionable_suffix_start > 0
                and items[actionable_suffix_start - 1].kind
                in {"client_instruction", "user_message"}):
            actionable_suffix_start -= 1
    if actionable_suffix_start == len(items):
        raise _invalid("input must end with new client input", param="input")
    return tuple(items), actionable_suffix_start


def _parse_tools(
        value: Any,
) -> tuple[tuple[Dict[str, Any], ...], tuple[Dict[str, Any], ...]]:
    if value in (None, []):
        return (), ()
    if not isinstance(value, list):
        raise _invalid("tools must be an array", param="tools")
    if len(value) > _MAX_TOOLS:
        raise _invalid("tools exceeds the request limit", param="tools")
    definitions = []
    public = []
    for index, item in enumerate(value):
        param = f"tools.{index}"
        if not isinstance(item, Mapping):
            raise _invalid("Tool definitions must be objects", param=param)
        unknown = sorted(
            set(item) - {"type", "name", "description", "parameters", "strict"})
        if unknown:
            raise _invalid(
                "Unsupported tool field: " + unknown[0],
                param=f"{param}.{unknown[0]}",
            )
        if item.get("type") != "function":
            raise _invalid("Only function tools are supported", param=param)
        if item.get("strict") not in (None, False):
            raise _invalid(
                "Strict function schemas are not supported",
                param=f"{param}.strict",
            )
        if "parameters" not in item:
            raise _invalid(
                "Function parameters are required",
                param=f"{param}.parameters",
            )
        definition = {
            "name": item.get("name"),
            "description": item.get("description") or "",
            "parameters": item.get("parameters"),
        }
        definitions.append(definition)
        normalized_public = {
            "type": "function",
            "name": item.get("name"),
            "description": item.get("description") or "",
            "parameters": item.get("parameters"),
        }
        if "strict" in item:
            normalized_public["strict"] = item["strict"]
        public.append(normalized_public)
    from core.client_tools import validate_client_tool_definitions
    try:
        validated = validate_client_tool_definitions(definitions)
    except ValueError as exc:
        raise _invalid(str(exc), param="tools") from exc
    return tuple(validated), tuple(public)


def _number(
        value: Any,
        *,
        param: str,
        minimum: float,
        maximum: float,
) -> Optional[float]:
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))):
        raise _invalid(f"{param} must be a finite number", param=param)
    number = float(value)
    if number < minimum or number > maximum:
        raise _invalid(
            f"{param} must be between {minimum:g} and {maximum:g}",
            param=param,
        )
    return number


def _metadata(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _invalid("metadata must be an object", param="metadata")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise _invalid("metadata must contain finite JSON", param="metadata") from exc
    if len(encoded.encode("utf-8")) > 65536:
        raise _invalid("metadata exceeds its size limit", param="metadata")
    return json.loads(encoded)


def _strict_noops(
        publication: Mapping[str, Any],
        body: Mapping[str, Any],
) -> None:
    if not publication.get("strict_fields"):
        return
    for field_name in sorted(_COMPATIBILITY_NOOP_FIELDS):
        if field_name in body and body[field_name] is not None:
            raise _invalid(
                f"{field_name} is unavailable in strict field mode",
                param=field_name,
            )


def parse_openai_response_request(
        publication: Mapping[str, Any],
        key: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        request_id: str,
        hash_secret: Optional[bytes] = None,
) -> ParsedOpenAIResponse:
    """Validate a Responses create request before any session lookup."""

    if not isinstance(body, Mapping):
        raise _invalid("The request body must be a JSON object")
    unknown = sorted(set(body) - OPENAI_RESPONSES_ACCEPTED_FIELDS)
    if unknown:
        raise _invalid(
            "Unsupported request field: " + unknown[0], param=unknown[0])
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise _invalid("model is required", param="model")
    if model != publication.get("api_model_id"):
        raise OpenAIResponsesError(
            "The requested model does not exist.",
            status=404,
            code="model_not_found",
            param="model",
        )
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise _invalid("stream must be a boolean", param="stream")
    store = body.get("store", True)
    if not isinstance(store, bool):
        raise _invalid("store must be a boolean", param="store")
    previous_value = body.get("previous_response_id")
    if previous_value is None:
        previous_response_id = ""
    elif (not isinstance(previous_value, str) or not previous_value
          or len(previous_value) > 256):
        raise _invalid(
            "previous_response_id must be a bounded response id",
            param="previous_response_id",
        )
    else:
        previous_response_id = previous_value
    instructions_value = body.get("instructions")
    if instructions_value is None:
        instructions = ""
    else:
        instructions = _bounded_text(
            instructions_value,
            param="instructions",
            allow_empty=True,
        )
    parallel = body.get("parallel_tool_calls")
    if parallel is not None and not isinstance(parallel, bool):
        raise _invalid(
            "parallel_tool_calls must be a boolean",
            param="parallel_tool_calls",
        )
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
    temperature = _number(
        body.get("temperature"),
        param="temperature",
        minimum=0,
        maximum=2,
    )
    top_p = _number(
        body.get("top_p"),
        param="top_p",
        minimum=0,
        maximum=1,
    )
    max_output_value = body.get("max_output_tokens")
    if max_output_value is None:
        max_output_tokens = None
    elif (isinstance(max_output_value, bool)
          or not isinstance(max_output_value, int)
          or max_output_value < 1
          or max_output_value > 1_000_000):
        raise _invalid(
            "max_output_tokens must be a positive bounded integer",
            param="max_output_tokens",
        )
    else:
        max_output_tokens = max_output_value
    metadata = _metadata(body.get("metadata"))
    _strict_noops(publication, body)
    visible_items, actionable_suffix_start = _parse_input(
        body.get("input"),
        previous_response_id=previous_response_id,
    )
    client_tools, public_tools = _parse_tools(body.get("tools"))
    runtime_tools = () if tool_choice == "none" else client_tools
    secret = hash_secret or standard_api_hash_secret(1)
    namespace = StandardApiNamespace(
        publication_id=str(publication["publication_id"]),
        api_generation=int(publication["api_generation"]),
        key_id=str(key["key_id"]),
        dialect="responses",
        api_model_id=model,
    )
    fingerprint = canonical_request_fingerprint({
        "request_id": request_id,
        "model": model,
        "visible_items": [
            canonical_visible_item(item) for item in visible_items
        ],
        "instructions": instructions,
        "previous_response_id": previous_response_id,
        "tools": list(client_tools),
        "tool_choice": tool_choice,
        "store": store,
        "metadata": metadata,
    }, secret)
    turn = NormalizedApiTurn(
        namespace=namespace,
        visible_items=visible_items,
        actionable_suffix_start=actionable_suffix_start,
        request_id=request_id,
        body_fingerprint=fingerprint,
        client_tools=runtime_tools,
        tool_choice=tool_choice,
        stream=stream,
        previous_response_id=previous_response_id or None,
    )
    return ParsedOpenAIResponse(
        turn=turn,
        instructions=instructions,
        store=store,
        public_tools=public_tools,
        metadata=metadata,
        temperature=temperature,
        top_p=top_p,
        max_output_tokens=max_output_tokens,
    )


def _response_id(run_id: str) -> str:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
    return "resp_" + digest


def _item_id(prefix: str, response_id: str, index: int, suffix: str = "") -> str:
    digest = hashlib.sha256(
        f"{response_id}:{index}:{suffix}".encode("utf-8")).hexdigest()[:24]
    return prefix + digest


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


def _public_output(
        result: Any,
        response_id: str,
) -> list[Dict[str, Any]]:
    calls = list(getattr(result, "client_tool_calls", None) or ())
    if calls:
        return [{
            "id": _item_id(
                "fc_", response_id, index, str(call.get("id") or "")),
            "type": "function_call",
            "status": "completed",
            "call_id": str(call.get("id") or ""),
            "name": str(call.get("name") or ""),
            "arguments": _tool_arguments(call.get("arguments", {})),
        } for index, call in enumerate(calls)]
    return [{
        "id": _item_id("msg_", response_id, 0),
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{
            "type": "output_text",
            "annotations": [],
            "text": str(getattr(result, "response", "") or ""),
        }],
    }]


def _usage(result: Any) -> Dict[str, Any]:
    input_tokens = max(0, int(getattr(result, "tokens_in", 0) or 0))
    output_tokens = max(0, int(getattr(result, "tokens_out", 0) or 0))
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
    }


def _response_envelope(
        run: "OpenAIResponsesRun",
        *,
        status: str,
        output: Sequence[Mapping[str, Any]],
        usage: Optional[Mapping[str, Any]],
        error: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "id": run.response_id,
        "object": "response",
        "created_at": run.created,
        "status": status,
        "background": False,
        "error": dict(error) if error else None,
        "incomplete_details": None,
        "instructions": run.parsed.instructions or None,
        "max_output_tokens": run.parsed.max_output_tokens,
        "model": str(run.publication["api_model_id"]),
        "output": [dict(item) for item in output],
        "parallel_tool_calls": True,
        "previous_response_id": run.turn.previous_response_id,
        "reasoning": {"effort": None, "summary": None},
        "store": run.parsed.store,
        "temperature": run.parsed.temperature,
        "text": {"format": {"type": "text"}},
        "tool_choice": run.turn.tool_choice,
        "tools": [dict(item) for item in run.parsed.public_tools],
        "top_p": run.parsed.top_p,
        "truncation": "disabled",
        "usage": dict(usage) if usage is not None else None,
        "metadata": dict(run.parsed.metadata),
    }


def _ingress_messages(
        resolution: ApiTurnResolution,
        publication: Mapping[str, Any],
        instructions: str,
) -> tuple[Any, ...]:
    from core.agent_runtime_api import AgentIngressMessage

    source_base = {
        "type": "standard_api",
        "name": "OpenAI API client",
        "target_agent": publication["agent_name"],
        "visibility": "target_only",
        "publication_id": publication["publication_id"],
        "api_dialect": "responses",
    }
    ingress = []
    if instructions:
        ingress.append(AgentIngressMessage(
            role="user",
            content=(
                "[Authenticated API client request-scoped instruction; "
                "lower priority than publisher policy]\n" + instructions
            ),
            source=dict(
                source_base,
                instruction_role="instructions",
                request_scoped=True,
            ),
        ))
    for item in resolution.ingress_items:
        data = item.data
        if item.kind == "client_instruction":
            role = str(data.get("role") or "system")
            ingress.append(AgentIngressMessage(
                role="user",
                content=(
                    f"[Authenticated API client {role} instruction; "
                    "lower priority than publisher policy]\n"
                    + str(data.get("content") or "")
                ),
                source=dict(source_base, instruction_role=role),
            ))
        elif item.kind == "user_message":
            ingress.append(AgentIngressMessage(
                role="user",
                content=str(data.get("content") or ""),
                source=dict(source_base),
            ))
        elif item.kind == "client_tool_result_batch":
            for result in data.get("results") or ():
                content = str(result.get("content") or "")
                if result.get("error"):
                    content = "[Client tool error]\n" + content
                ingress.append(AgentIngressMessage(
                    role="tool",
                    content=content,
                    tool_call_id=str(result.get("id") or ""),
                    source=dict(source_base, client_tool_result=True),
                ))
        else:
            raise OpenAIResponsesError(
                "The actionable request suffix contains server output",
                param="input",
            )
    return tuple(ingress)


def _current_tool_results(
        resolution: ApiTurnResolution,
) -> tuple[Dict[str, Any], ...]:
    results = []
    for item in resolution.ingress_items:
        if item.kind == "client_tool_result_batch":
            results.extend(
                dict(result) for result in item.data.get("results") or ())
    return tuple(results)


@dataclass
class OpenAIResponsesRun:
    store: Any
    publication: Mapping[str, Any]
    key: Mapping[str, Any]
    parsed: ParsedOpenAIResponse
    turn: NormalizedApiTurn
    resolution: ApiTurnResolution
    runtime_api: Any
    hash_secret: bytes
    response_id: str
    created: int
    conversation_store: Any = None
    owner: bool = True
    result: Any = None
    payload: Optional[Dict[str, Any]] = None
    error: Optional[OpenAIResponsesRunError] = None
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
            logger.debug(
                "Could not append Responses replay event", exc_info=True)
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

    def fail(self, error: OpenAIResponsesRunError) -> None:
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
                logger.warning(
                    "Could not force-stop disconnected Responses run",
                    exc_info=True,
                )
            try:
                self.store.fail_api_run(
                    self.resolution.run["run_id"],
                    self.resolution.lease_id,
                    error_code="client_disconnected",
                    canceled=True,
                )
            except Exception:
                logger.debug(
                    "Could not cancel disconnected Responses lease",
                    exc_info=True,
                )
            self.fail(OpenAIResponsesRunError(
                "The client disconnected.",
                status=499,
                code="client_disconnected",
            ))


@dataclass(frozen=True)
class OpenAIResponsesAdmission:
    run: OpenAIResponsesRun
    owner: bool


_ACTIVE_RUNS: Dict[str, OpenAIResponsesRun] = {}
_ACTIVE_RUNS_LOCK = threading.RLock()


def _fail_lease(
        run: OpenAIResponsesRun,
        code: str,
        *,
        canceled: bool = False,
) -> None:
    try:
        run.store.fail_api_run(
            run.resolution.run["run_id"],
            run.resolution.lease_id,
            error_code=code,
            canceled=canceled,
        )
    except Exception:
        logger.debug("Could not fail Responses run lease", exc_info=True)


def _drive_run(
        run: OpenAIResponsesRun,
        initial_result: Any = None,
) -> None:
    try:
        result = initial_result
        while result is None:
            result = run.runtime_api.wait_for_done(
                run.resolution.session["internal_conversation_id"],
                run.resolution.run["run_id"],
                timeout=_HEARTBEAT_SECONDS,
            )
            if result is None and not run.store.heartbeat_api_run(
                    run.resolution.run["run_id"],
                    run.resolution.lease_id):
                raise OpenAIResponsesRunError(
                    "The agent run lease was lost.",
                    code="run_lost",
                )
        if getattr(result, "error", ""):
            raise OpenAIResponsesRunError(
                "The published agent run failed.",
                code="agent_error",
            )
        output = _public_output(result, run.response_id)
        usage = _usage(result)
        payload = _response_envelope(
            run,
            status="completed",
            output=output,
            usage=usage,
        )
        visible_items = run.turn.visible_items + (
            NormalizedVisibleItem(
                kind="response_output",
                data={"output": output},
            ),
        )
        heads = compute_hash_chain(
            run.turn.namespace, visible_items, run.hash_secret)
        prefixes = eligible_prefixes(visible_items, heads)
        response_record = None
        if run.parsed.store:
            response_record = {
                "previous_response_id": run.turn.previous_response_id or "",
                "visible_items": visible_items,
                "output": output,
                "envelope": payload,
            }
        finalize_api_run_with_checkpoint(
            run.store,
            run.conversation_store,
            str(run.resolution.session["internal_conversation_id"]),
            run.resolution.run["run_id"],
            run.resolution.lease_id,
            visible_head_hash=heads[-1],
            item_count=len(visible_items),
            prefixes=prefixes[-1:],
            response_id=run.response_id,
            response_record=response_record,
            pending_client_tool_calls=tuple(
                getattr(result, "client_tool_calls", None) or ()),
            client_tool_definitions=run.turn.client_tools,
        )
        run.complete(result, payload)
    except OpenAIResponsesRunError as exc:
        _fail_lease(run, exc.code)
        run.fail(exc)
    except Exception:
        logger.exception("OpenAI Responses run finalization failed")
        _fail_lease(run, "internal_error")
        run.fail(OpenAIResponsesRunError(
            "The published agent run failed.",
            code="internal_error",
        ))
    finally:
        with _ACTIVE_RUNS_LOCK:
            if _ACTIVE_RUNS.get(run.resolution.run["run_id"]) is run:
                _ACTIVE_RUNS.pop(run.resolution.run["run_id"], None)


def prepare_openai_response_run(
        store: Any,
        publication: Mapping[str, Any],
        key: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        request_id: str,
        conversation_store: Any = None,
        runtime_api: Any = None,
        hash_secret: Optional[bytes] = None,
) -> OpenAIResponsesAdmission:
    """Validate, resolve, settle, and submit before opening a response."""

    from core.agent_runtime_api import AgentRuntimeAPI, AgentStructuredRequest
    from core._a2a_standard_api import (
        ApiRunQuotaExceeded,
        ApiSessionQuotaExceeded,
    )

    runtime = runtime_api or AgentRuntimeAPI
    secret = hash_secret or standard_api_hash_secret(1)
    parsed = parse_openai_response_request(
        publication,
        key,
        body,
        request_id=request_id,
        hash_secret=secret,
    )
    turn = parsed.turn
    if conversation_store is None:
        from core.conversation_store import ConversationStore
        conversation_store = ConversationStore.instance()
    if turn.previous_response_id:
        parent = store.get_api_response(
            turn.namespace, turn.previous_response_id)
        if parent is None:
            raise OpenAIResponsesError(
                "The requested previous response was not found.",
                status=404,
                code="response_not_found",
                param="previous_response_id",
            )
        parent_items = tuple(parent["visible_items"])
        if (len(parent_items) != int(parent["item_count"])
                or not parent_items):
            raise OpenAIResponsesError(
                "The requested previous response was not found.",
                status=404,
                code="response_not_found",
                param="previous_response_id",
            )
        parent_heads = compute_hash_chain(
            turn.namespace, parent_items, secret)
        if parent_heads[-1] != parent["visible_head_hash"]:
            raise OpenAIResponsesError(
                "The requested previous response was not found.",
                status=404,
                code="response_not_found",
                param="previous_response_id",
            )
        turn = replace(
            turn,
            visible_items=parent_items + turn.visible_items,
            actionable_suffix_start=len(parent_items),
        )

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
            raise OpenAIResponsesError(
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
                raise OpenAIResponsesError(
                    "The active response is unavailable; retry the request.",
                    status=409,
                    code="run_conflict",
                )
            return OpenAIResponsesAdmission(run=active, owner=False)

        run = OpenAIResponsesRun(
            store=store,
            conversation_store=conversation_store,
            publication=dict(publication),
            key=dict(key),
            parsed=parsed,
            turn=turn,
            resolution=resolution,
            runtime_api=runtime,
            hash_secret=secret,
            response_id=_response_id(run_id),
            created=int(float(
                resolution.run.get("started_at") or time.time())),
        )
        _ACTIVE_RUNS[run_id] = run
        try:
            current_results = _current_tool_results(resolution)
            pending = store.get_pending_api_tool_batch(
                resolution.session["session_id"])
            if pending is not None and not current_results:
                raise _invalid(
                    "The request must settle every pending client tool call",
                    param="input",
                )
            if current_results:
                store.settle_api_tool_batch(
                    run_id,
                    resolution.lease_id,
                    results=current_results,
                    client_tool_definitions=turn.client_tools,
                )
            ingress = _ingress_messages(
                resolution, publication, parsed.instructions)
            source = {
                "type": "standard_api",
                "name": "OpenAI API client",
                "target_agent": publication["agent_name"],
                "visibility": "target_only",
                "publication_id": publication["publication_id"],
                "key_id": key["key_id"],
                "api_dialect": "responses",
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
        except OpenAIResponsesError:
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
            raise OpenAIResponsesError(
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
            name=f"openai-responses-{run_id[:16]}",
        ).start()
        return OpenAIResponsesAdmission(run=run, owner=True)


def wait_openai_response_payload(
        run: OpenAIResponsesRun,
) -> Dict[str, Any]:
    run._done.wait()
    if run.error is not None:
        raise run.error
    if run.payload is None:
        raise OpenAIResponsesRunError(
            "The published agent run ended without a response.",
            code="run_lost",
        )
    return run.payload


def _sse(event: Mapping[str, Any]) -> bytes:
    event_type = str(event["type"])
    payload = json.dumps(
        dict(event),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")


def _stream_event(
        event_type: str,
        sequence_number: int,
        **values: Any,
) -> Dict[str, Any]:
    return {
        "type": event_type,
        "sequence_number": sequence_number,
        **values,
    }


def iter_openai_response_sse(
        admission: OpenAIResponsesAdmission,
) -> Iterator[bytes]:
    """Translate one shared run to native OpenAI Responses SSE."""

    run = admission.run
    sequence_number = 0
    offset = 0
    emitted_text = ""
    streamed_ids = set()
    text_started = False
    normal_end = False

    def event(event_type: str, **values: Any) -> bytes:
        nonlocal sequence_number
        frame = _sse(_stream_event(
            event_type, sequence_number, **values))
        sequence_number += 1
        return frame

    empty_response = _response_envelope(
        run,
        status="in_progress",
        output=[],
        usage=None,
    )
    try:
        yield event("response.created", response=empty_response)
        yield event("response.in_progress", response=empty_response)
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
                    if not text_started:
                        item_id = _item_id("msg_", run.response_id, 0)
                        yield event(
                            "response.output_item.added",
                            output_index=0,
                            item={
                                "id": item_id,
                                "type": "message",
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [],
                            },
                        )
                        yield event(
                            "response.content_part.added",
                            item_id=item_id,
                            output_index=0,
                            content_index=0,
                            part={
                                "type": "output_text",
                                "annotations": [],
                                "text": "",
                            },
                        )
                        text_started = True
                    emitted_text += delta
                    yield event(
                        "response.output_text.delta",
                        item_id=_item_id("msg_", run.response_id, 0),
                        output_index=0,
                        content_index=0,
                        delta=delta,
                        logprobs=[],
                    )
                elif event_type == "new_message":
                    if data.get("role") != "assistant":
                        continue
                    msg_id = str(data.get("msg_id") or "")
                    content = str(data.get("content") or "")
                    if not content or (msg_id and msg_id in streamed_ids):
                        continue
                    if not emitted_text:
                        if not text_started:
                            item_id = _item_id("msg_", run.response_id, 0)
                            yield event(
                                "response.output_item.added",
                                output_index=0,
                                item={
                                    "id": item_id,
                                    "type": "message",
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [],
                                },
                            )
                            yield event(
                                "response.content_part.added",
                                item_id=item_id,
                                output_index=0,
                                content_index=0,
                                part={
                                    "type": "output_text",
                                    "annotations": [],
                                    "text": "",
                                },
                            )
                            text_started = True
                        emitted_text = content
                        yield event(
                            "response.output_text.delta",
                            item_id=_item_id("msg_", run.response_id, 0),
                            output_index=0,
                            content_index=0,
                            delta=content,
                            logprobs=[],
                        )
            if done and offset >= len(run._events):
                break

        if run.error is not None:
            failed = _response_envelope(
                run,
                status="failed",
                output=[],
                usage=None,
                error={
                    "code": run.error.code,
                    "message": str(run.error),
                },
            )
            yield event("response.failed", response=failed)
            normal_end = True
            return

        result = run.result
        output = _public_output(result, run.response_id)
        calls = list(getattr(result, "client_tool_calls", None) or ())
        if calls:
            for output_index, item in enumerate(output):
                partial = dict(item)
                partial["status"] = "in_progress"
                partial["arguments"] = ""
                yield event(
                    "response.output_item.added",
                    output_index=output_index,
                    item=partial,
                )
                arguments = str(item["arguments"])
                if arguments:
                    yield event(
                        "response.function_call_arguments.delta",
                        item_id=item["id"],
                        output_index=output_index,
                        delta=arguments,
                    )
                yield event(
                    "response.function_call_arguments.done",
                    item_id=item["id"],
                    output_index=output_index,
                    arguments=arguments,
                )
                yield event(
                    "response.output_item.done",
                    output_index=output_index,
                    item=item,
                )
        else:
            item = output[0]
            item_id = item["id"]
            final_text = item["content"][0]["text"]
            if not text_started:
                yield event(
                    "response.output_item.added",
                    output_index=0,
                    item={
                        "id": item_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                )
                yield event(
                    "response.content_part.added",
                    item_id=item_id,
                    output_index=0,
                    content_index=0,
                    part={
                        "type": "output_text",
                        "annotations": [],
                        "text": "",
                    },
                )
                text_started = True
            if final_text.startswith(emitted_text):
                remainder = final_text[len(emitted_text):]
                if remainder:
                    yield event(
                        "response.output_text.delta",
                        item_id=item_id,
                        output_index=0,
                        content_index=0,
                        delta=remainder,
                        logprobs=[],
                    )
                    emitted_text += remainder
            elif not emitted_text and final_text:
                yield event(
                    "response.output_text.delta",
                    item_id=item_id,
                    output_index=0,
                    content_index=0,
                    delta=final_text,
                    logprobs=[],
                )
                emitted_text = final_text
            part = item["content"][0]
            yield event(
                "response.output_text.done",
                item_id=item_id,
                output_index=0,
                content_index=0,
                text=final_text,
                logprobs=[],
            )
            yield event(
                "response.content_part.done",
                item_id=item_id,
                output_index=0,
                content_index=0,
                part=part,
            )
            yield event(
                "response.output_item.done",
                output_index=0,
                item=item,
            )
        payload = run.payload or _response_envelope(
            run,
            status="completed",
            output=output,
            usage=_usage(result),
        )
        yield event("response.completed", response=payload)
        normal_end = True
    finally:
        if (not normal_end and admission.owner
                and run.publication.get("api_disconnect_policy") == "cancel"):
            run.cancel()


__all__ = [
    "OpenAIResponsesAdmission",
    "OpenAIResponsesError",
    "OpenAIResponsesRun",
    "OpenAIResponsesRunError",
    "ParsedOpenAIResponse",
    "iter_openai_response_sse",
    "parse_openai_response_request",
    "prepare_openai_response_run",
    "wait_openai_response_payload",
]
