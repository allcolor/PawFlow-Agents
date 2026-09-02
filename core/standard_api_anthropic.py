"""Anthropic Messages adapter for published PawFlow agents."""

from __future__ import annotations

import hashlib
import json
import logging
import math
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
from core.standard_api_chat import standard_api_hash_secret
from core.standard_api_contract import ANTHROPIC_MESSAGES_ACCEPTED_FIELDS
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
_MAX_OUTPUT_TOKENS = 1_000_000
_KEEPALIVE_SECONDS = 15.0
_HEARTBEAT_SECONDS = 30.0
_BOUNDARY_KINDS = frozenset({"assistant_message", "client_tool_call_batch"})
_COMPATIBILITY_NOOP_FIELDS = frozenset({
    "temperature",
    "top_p",
    "top_k",
    "stop_sequences",
    "metadata",
})


class AnthropicMessagesError(ValueError):
    """A pre-stream error using Anthropic's public error vocabulary."""

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


class AnthropicMessagesRunError(RuntimeError):
    """A terminal Messages failure that happened after admission."""

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
class ParsedAnthropicMessage:
    """Validated request options kept outside the visible transcript."""

    turn: NormalizedApiTurn
    max_tokens: int
    public_tools: tuple[Dict[str, Any], ...]
    metadata: Dict[str, Any]
    temperature: Optional[float]
    top_p: Optional[float]
    top_k: Optional[int]
    stop_sequences: tuple[str, ...]


def _invalid(
        message: str,
        *,
        param: Optional[str] = None,
) -> AnthropicMessagesError:
    return AnthropicMessagesError(message, param=param)


def _bounded_text(
        value: Any,
        *,
        param: str,
        allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise _invalid("Text content must be a string", param=param)
    if not allow_empty and not value:
        raise _invalid("Text content must not be empty", param=param)
    if len(value) > _MAX_TEXT_CHARS:
        raise _invalid("Text content exceeds its size limit", param=param)
    return value


def _text_content(
        value: Any,
        *,
        param: str,
        allow_empty: bool = False,
) -> str:
    if isinstance(value, str):
        return _bounded_text(value, param=param, allow_empty=allow_empty)
    if not isinstance(value, list) or (not value and not allow_empty):
        raise _invalid("Content must be text or a non-empty array", param=param)
    chunks = []
    total = 0
    for index, part in enumerate(value):
        part_param = f"{param}.{index}"
        if not isinstance(part, Mapping):
            raise _invalid("Content blocks must be objects", param=part_param)
        unknown = sorted(set(part) - {"type", "text"})
        if unknown:
            raise _invalid(
                "Unsupported content block field: " + unknown[0],
                param=f"{part_param}.{unknown[0]}",
            )
        if part.get("type") != "text":
            raise _invalid(
                "Only text content blocks are enabled for this publication",
                param=f"{part_param}.type",
            )
        text = _bounded_text(
            part.get("text"),
            param=f"{part_param}.text",
            allow_empty=True,
        )
        total += len(text)
        if total > _MAX_TEXT_CHARS:
            raise _invalid("Content exceeds its size limit", param=param)
        chunks.append(text)
    result = "".join(chunks)
    if not result and not allow_empty:
        raise _invalid("Content must not be empty", param=param)
    return result


def _append_text_item(
        items: list[NormalizedVisibleItem],
        *,
        kind: str,
        content: str,
) -> None:
    if items and items[-1].kind == kind:
        previous = str(items[-1].data.get("content") or "")
        items[-1] = NormalizedVisibleItem(
            kind=kind,
            data={"content": previous + ("\n" if previous and content else "") + content},
        )
        return
    items.append(NormalizedVisibleItem(kind=kind, data={"content": content}))


def _tool_result_content(value: Any, *, param: str) -> str:
    if isinstance(value, str):
        return _bounded_text(value, param=param, allow_empty=True)
    return _text_content(value, param=param, allow_empty=True)


def _parse_messages(messages: Any) -> tuple[NormalizedVisibleItem, ...]:
    if not isinstance(messages, list) or not messages:
        raise _invalid("messages must be a non-empty array", param="messages")
    if len(messages) > _MAX_MESSAGES:
        raise _invalid("messages exceeds the request limit", param="messages")

    items: list[NormalizedVisibleItem] = []
    pending_call_ids: Optional[set[str]] = None
    previous_role = ""
    for message_index, message in enumerate(messages):
        param = f"messages.{message_index}"
        if not isinstance(message, Mapping):
            raise _invalid("Each message must be an object", param=param)
        unknown = sorted(set(message) - {"role", "content"})
        if unknown:
            raise _invalid(
                "Unsupported message field: " + unknown[0],
                param=f"{param}.{unknown[0]}",
            )
        role = message.get("role")
        if role not in {"user", "assistant"}:
            raise _invalid(
                "Message role must be user or assistant",
                param=f"{param}.role",
            )
        content = message.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content}]
        elif isinstance(content, list) and content:
            blocks = content
        else:
            raise _invalid(
                "Message content must be text or a non-empty array",
                param=f"{param}.content",
            )

        if pending_call_ids is not None and role != "user":
            raise _invalid(
                "Every assistant tool use requires a tool result before "
                "the next assistant message",
                param=param,
            )

        if role == "assistant":
            text_chunks = []
            calls = []
            saw_tool = False
            seen_ids = set()
            for block_index, block in enumerate(blocks):
                block_param = f"{param}.content.{block_index}"
                if not isinstance(block, Mapping):
                    raise _invalid("Content blocks must be objects", param=block_param)
                block_type = block.get("type")
                if block_type == "text":
                    if saw_tool:
                        raise _invalid(
                            "Assistant text blocks must precede tool_use blocks",
                            param=block_param,
                        )
                    unknown_block = sorted(set(block) - {"type", "text"})
                    if unknown_block:
                        raise _invalid(
                            "Unsupported text block field: " + unknown_block[0],
                            param=f"{block_param}.{unknown_block[0]}",
                        )
                    text_chunks.append(_bounded_text(
                        block.get("text"),
                        param=f"{block_param}.text",
                        allow_empty=True,
                    ))
                    continue
                if block_type != "tool_use":
                    raise _invalid(
                        "Assistant content supports only text and tool_use blocks",
                        param=f"{block_param}.type",
                    )
                saw_tool = True
                unknown_block = sorted(
                    set(block) - {"type", "id", "name", "input"})
                if unknown_block:
                    raise _invalid(
                        "Unsupported tool_use field: " + unknown_block[0],
                        param=f"{block_param}.{unknown_block[0]}",
                    )
                call_id = block.get("id")
                name = block.get("name")
                arguments = block.get("input")
                if not isinstance(call_id, str) or not call_id:
                    raise _invalid(
                        "tool_use id is required",
                        param=f"{block_param}.id",
                    )
                if call_id in seen_ids:
                    raise _invalid(
                        "tool_use ids must be unique",
                        param=f"{block_param}.id",
                    )
                if not isinstance(name, str) or not name:
                    raise _invalid(
                        "tool_use name is required",
                        param=f"{block_param}.name",
                    )
                if not isinstance(arguments, Mapping):
                    raise _invalid(
                        "tool_use input must be an object",
                        param=f"{block_param}.input",
                    )
                seen_ids.add(call_id)
                calls.append({
                    "id": call_id,
                    "name": name,
                    "arguments": dict(arguments),
                })
            text = "".join(text_chunks)
            if text:
                _append_text_item(items, kind="assistant_message", content=text)
            if calls:
                items.append(NormalizedVisibleItem(
                    kind="client_tool_call_batch",
                    data={"calls": calls},
                ))
                pending_call_ids = seen_ids
            if not text and not calls:
                raise _invalid(
                    "Assistant content must not be empty",
                    param=f"{param}.content",
                )
            previous_role = role
            continue

        text_chunks = []
        results = []
        result_ids = set()
        saw_text = False
        for block_index, block in enumerate(blocks):
            block_param = f"{param}.content.{block_index}"
            if not isinstance(block, Mapping):
                raise _invalid("Content blocks must be objects", param=block_param)
            block_type = block.get("type")
            if block_type == "text":
                unknown_block = sorted(set(block) - {"type", "text"})
                if unknown_block:
                    raise _invalid(
                        "Unsupported text block field: " + unknown_block[0],
                        param=f"{block_param}.{unknown_block[0]}",
                    )
                saw_text = True
                text_chunks.append(_bounded_text(
                    block.get("text"),
                    param=f"{block_param}.text",
                    allow_empty=True,
                ))
                continue
            if block_type == "image":
                raise _invalid(
                    "Only text content blocks are enabled for this publication",
                    param=f"{block_param}.type",
                )
            if block_type != "tool_result":
                raise _invalid(
                    "User content supports only text and tool_result blocks",
                    param=f"{block_param}.type",
                )
            if saw_text:
                raise _invalid(
                    "tool_result blocks must precede user text blocks",
                    param=block_param,
                )
            if pending_call_ids is None:
                raise _invalid(
                    "tool_result requires a preceding assistant tool_use batch",
                    param=block_param,
                )
            unknown_block = sorted(
                set(block) - {"type", "tool_use_id", "content", "is_error"})
            if unknown_block:
                raise _invalid(
                    "Unsupported tool_result field: " + unknown_block[0],
                    param=f"{block_param}.{unknown_block[0]}",
                )
            call_id = block.get("tool_use_id")
            if not isinstance(call_id, str) or not call_id:
                raise _invalid(
                    "tool_use_id is required",
                    param=f"{block_param}.tool_use_id",
                )
            if call_id in result_ids:
                raise _invalid(
                    "Tool results must be unique",
                    param=f"{block_param}.tool_use_id",
                )
            is_error = block.get("is_error", False)
            if not isinstance(is_error, bool):
                raise _invalid(
                    "is_error must be a boolean",
                    param=f"{block_param}.is_error",
                )
            result_ids.add(call_id)
            results.append({
                "id": call_id,
                "content": _tool_result_content(
                    block.get("content", ""),
                    param=f"{block_param}.content",
                ),
                "error": is_error,
            })
        if pending_call_ids is not None:
            if result_ids != pending_call_ids:
                raise _invalid(
                    "The request must contain exactly one result for every "
                    "assistant tool use",
                    param=param,
                )
            items.append(NormalizedVisibleItem(
                kind="client_tool_result_batch",
                data={"results": results},
            ))
            pending_call_ids = None
        elif results:
            raise _invalid(
                "tool_result requires a preceding assistant tool_use batch",
                param=param,
            )
        text = "".join(text_chunks)
        if text:
            _append_text_item(items, kind="user_message", content=text)
        if not text and not results:
            raise _invalid(
                "User content must not be empty",
                param=f"{param}.content",
            )
        previous_role = role

    if pending_call_ids is not None:
        raise _invalid(
            "Assistant tool uses must be followed by their tool results",
            param="messages",
        )
    if not items or items[-1].kind in _BOUNDARY_KINDS:
        raise _invalid(
            "The final message must carry new client input or tool results",
            param="messages",
        )
    return tuple(items)


def _parse_tools(
        value: Any,
) -> tuple[tuple[Dict[str, Any], ...], tuple[Dict[str, Any], ...]]:
    if value in (None, []):
        return (), ()
    if not isinstance(value, list):
        raise _invalid("tools must be an array", param="tools")
    if len(value) > _MAX_TOOLS:
        raise _invalid("tools exceeds the request limit", param="tools")

    public = []
    runtime = []
    for index, item in enumerate(value):
        param = f"tools.{index}"
        if not isinstance(item, Mapping):
            raise _invalid("Tool definitions must be objects", param=param)
        unknown = sorted(set(item) - {"name", "description", "input_schema"})
        if unknown:
            raise _invalid(
                "Unsupported tool field: " + unknown[0],
                param=f"{param}.{unknown[0]}",
            )
        name = item.get("name")
        description = item.get("description", "")
        schema = item.get("input_schema")
        if not isinstance(name, str) or not name:
            raise _invalid("Tool name is required", param=f"{param}.name")
        if not isinstance(description, str):
            raise _invalid(
                "Tool description must be a string",
                param=f"{param}.description",
            )
        if not isinstance(schema, Mapping):
            raise _invalid(
                "Tool input_schema must be an object",
                param=f"{param}.input_schema",
            )
        public_item = {
            "name": name,
            "description": description,
            "input_schema": dict(schema),
        }
        public.append(public_item)
        runtime.append({
            "name": name,
            "description": description,
            "parameters": dict(schema),
        })

    from core.client_tools import validate_client_tool_definitions
    try:
        validated = validate_client_tool_definitions(runtime)
    except ValueError as exc:
        raise _invalid(str(exc), param="tools") from exc
    return tuple(public), tuple(validated)


def _parse_tool_choice(value: Any) -> str:
    if value is None:
        return "auto"
    if not isinstance(value, Mapping):
        raise _invalid("tool_choice must be an object", param="tool_choice")
    unknown = sorted(set(value) - {"type", "disable_parallel_tool_use"})
    if unknown:
        raise _invalid(
            "Unsupported tool_choice field: " + unknown[0],
            param=f"tool_choice.{unknown[0]}",
        )
    choice = value.get("type")
    if choice not in {"auto", "none"}:
        raise _invalid(
            "Only tool_choice types 'auto' and 'none' are supported",
            param="tool_choice.type",
        )
    disable_parallel = value.get("disable_parallel_tool_use", False)
    if not isinstance(disable_parallel, bool):
        raise _invalid(
            "disable_parallel_tool_use must be a boolean",
            param="tool_choice.disable_parallel_tool_use",
        )
    if disable_parallel:
        raise _invalid(
            "disable_parallel_tool_use=true cannot be enforced by this publication",
            param="tool_choice.disable_parallel_tool_use",
        )
    return str(choice)


def _finite_number(
        value: Any,
        *,
        param: str,
        minimum: float,
        maximum: float,
) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(f"{param} must be a number", param=param)
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise _invalid(
            f"{param} must be between {minimum:g} and {maximum:g}",
            param=param,
        )
    return result


def _positive_int(value: Any, *, param: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _invalid(f"{param} must be a positive integer", param=param)
    if value > maximum:
        raise _invalid(f"{param} exceeds the request limit", param=param)
    return value


def _parse_metadata(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _invalid("metadata must be an object", param="metadata")
    unknown = sorted(set(value) - {"user_id"})
    if unknown:
        raise _invalid(
            "Unsupported metadata field: " + unknown[0],
            param=f"metadata.{unknown[0]}",
        )
    user_id = value.get("user_id")
    if user_id is not None and (
            not isinstance(user_id, str) or len(user_id) > 500):
        raise _invalid(
            "metadata.user_id must be a string of at most 500 characters",
            param="metadata.user_id",
        )
    return dict(value)


def _parse_stop_sequences(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _invalid("stop_sequences must be an array", param="stop_sequences")
    if len(value) > 16:
        raise _invalid(
            "stop_sequences exceeds the request limit",
            param="stop_sequences",
        )
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise _invalid(
                "stop_sequences entries must be non-empty strings",
                param=f"stop_sequences.{index}",
            )
        if len(item) > 1_000:
            raise _invalid(
                "stop_sequences entry exceeds its size limit",
                param=f"stop_sequences.{index}",
            )
        result.append(item)
    return tuple(result)


def _strict_noops(
        publication: Mapping[str, Any],
        body: Mapping[str, Any],
) -> None:
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


def parse_anthropic_message_request(
        publication: Mapping[str, Any],
        key: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        request_id: str,
        hash_secret: Optional[bytes] = None,
) -> ParsedAnthropicMessage:
    """Validate one Anthropic Messages request before session lookup."""

    if not isinstance(body, Mapping):
        raise _invalid("The request body must be a JSON object")
    unknown = sorted(set(body) - ANTHROPIC_MESSAGES_ACCEPTED_FIELDS)
    if unknown:
        raise _invalid(
            "Unsupported request field: " + unknown[0],
            param=unknown[0],
        )
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise _invalid("model is required", param="model")
    if model != publication.get("api_model_id"):
        raise AnthropicMessagesError(
            "The requested model does not exist.",
            status=404,
            error_type="not_found_error",
            code="model_not_found",
            param="model",
        )
    if "max_tokens" not in body or body.get("max_tokens") is None:
        raise _invalid("max_tokens is required", param="max_tokens")
    max_tokens = _positive_int(
        body.get("max_tokens"),
        param="max_tokens",
        maximum=_MAX_OUTPUT_TOKENS,
    )
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise _invalid("stream must be a boolean", param="stream")

    _strict_noops(publication, body)
    temperature = _finite_number(
        body.get("temperature"),
        param="temperature",
        minimum=0,
        maximum=1,
    )
    top_p = _finite_number(
        body.get("top_p"),
        param="top_p",
        minimum=0,
        maximum=1,
    )
    top_k_value = body.get("top_k")
    top_k = None
    if top_k_value is not None:
        top_k = _positive_int(
            top_k_value,
            param="top_k",
            maximum=1_000_000,
        )
    stop_sequences = _parse_stop_sequences(body.get("stop_sequences"))
    metadata = _parse_metadata(body.get("metadata"))

    visible_items = list(_parse_messages(body.get("messages")))
    system = body.get("system")
    if system is not None:
        instruction = _text_content(
            system,
            param="system",
            allow_empty=False,
        )
        visible_items.insert(0, NormalizedVisibleItem(
            kind="client_instruction",
            data={"role": "system", "content": instruction},
        ))

    public_tools, tools = _parse_tools(body.get("tools"))
    tool_choice = _parse_tool_choice(body.get("tool_choice"))
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
        dialect="anthropic_messages",
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
    turn = NormalizedApiTurn(
        namespace=namespace,
        visible_items=tuple(visible_items),
        actionable_suffix_start=actionable_suffix_start,
        request_id=request_id,
        body_fingerprint=fingerprint,
        client_tools=runtime_tools,
        tool_choice=tool_choice,
        stream=stream,
    )
    return ParsedAnthropicMessage(
        turn=turn,
        max_tokens=max_tokens,
        public_tools=public_tools,
        metadata=metadata,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        stop_sequences=stop_sequences,
    )


def _message_id(run_id: str) -> str:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:24]
    return "msg_" + digest


def _tool_input(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _public_content(result: Any) -> list[Dict[str, Any]]:
    calls = list(getattr(result, "client_tool_calls", None) or ())
    if calls:
        return [{
            "type": "tool_use",
            "id": str(call.get("id") or ""),
            "name": str(call.get("name") or ""),
            "input": _tool_input(call.get("arguments", {})),
        } for call in calls]
    return [{
        "type": "text",
        "text": str(getattr(result, "response", "") or ""),
    }]


def _stop_reason(result: Any) -> str:
    if getattr(result, "client_tool_calls", None):
        return "tool_use"
    value = str(getattr(result, "finish_reason", "") or "")
    if value == "length":
        return "max_tokens"
    if value == "stop_sequence":
        return "stop_sequence"
    return "end_turn"


def _usage(result: Any) -> Dict[str, int]:
    return {
        "input_tokens": max(0, int(getattr(result, "tokens_in", 0) or 0)),
        "output_tokens": max(0, int(getattr(result, "tokens_out", 0) or 0)),
    }


def anthropic_message_payload(
        result: Any,
        *,
        message_id: str,
        model: str,
) -> Dict[str, Any]:
    """Translate one terminal runtime result to an Anthropic message."""

    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": _public_content(result),
        "stop_reason": _stop_reason(result),
        "stop_sequence": None,
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


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, (list, tuple)):
        return str(value or "")
    return "".join(
        str(part.get("text") or "")
        for part in value
        if isinstance(part, Mapping) and part.get("type") == "text"
    )


def _ingress_messages(
        resolution: ApiTurnResolution,
        publication: Mapping[str, Any],
) -> tuple[Any, ...]:
    from core.agent_runtime_api import AgentIngressMessage

    source_base = {
        "type": "standard_api",
        "name": "Anthropic API client",
        "target_agent": publication["agent_name"],
        "visibility": "target_only",
        "publication_id": publication["publication_id"],
        "api_dialect": "anthropic_messages",
    }
    ingress = []
    for item in resolution.ingress_items:
        data = item.data
        if item.kind == "client_instruction":
            ingress.append(AgentIngressMessage(
                role="user",
                content=(
                    "[Authenticated API client system instruction; "
                    "lower priority than publisher policy]\n"
                    + _text(data.get("content"))
                ),
                source=dict(source_base, instruction_role="system"),
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
            raise AnthropicMessagesError(
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
            results.extend(
                dict(result) for result in item.data.get("results") or ())
    return tuple(results)


@dataclass
class AnthropicMessagesRun:
    store: Any
    publication: Mapping[str, Any]
    key: Mapping[str, Any]
    parsed: ParsedAnthropicMessage
    turn: NormalizedApiTurn
    resolution: ApiTurnResolution
    runtime_api: Any
    hash_secret: bytes
    message_id: str
    conversation_store: Any = None
    owner: bool = True
    result: Any = None
    payload: Optional[Dict[str, Any]] = None
    error: Optional[AnthropicMessagesRunError] = None
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
                "Could not append Anthropic replay event",
                exc_info=True,
            )
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

    def fail(self, error: AnthropicMessagesRunError) -> None:
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
                    "Could not force-stop disconnected Anthropic run",
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
                    "Could not cancel disconnected Anthropic lease",
                    exc_info=True,
                )
            self.fail(AnthropicMessagesRunError(
                "The client disconnected.",
                status=499,
                code="client_disconnected",
            ))


@dataclass(frozen=True)
class AnthropicMessagesAdmission:
    run: AnthropicMessagesRun
    owner: bool


_ACTIVE_RUNS: Dict[str, AnthropicMessagesRun] = {}
_ACTIVE_RUNS_LOCK = threading.RLock()


def _fail_lease(
        run: AnthropicMessagesRun,
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
        logger.debug(
            "Could not fail Anthropic run lease",
            exc_info=True,
        )


def _drive_run(
        run: AnthropicMessagesRun,
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
            if result is None:
                if not run.store.heartbeat_api_run(
                        run.resolution.run["run_id"],
                        run.resolution.lease_id):
                    raise AnthropicMessagesRunError(
                        "The agent run lease was lost.",
                        code="run_lost",
                    )
        if getattr(result, "error", ""):
            raise AnthropicMessagesRunError(
                "The published agent run failed.",
                code="agent_error",
            )
        visible_items = run.turn.visible_items + (_output_item(result),)
        heads = compute_hash_chain(
            run.turn.namespace,
            visible_items,
            run.hash_secret,
        )
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
        payload = anthropic_message_payload(
            result,
            message_id=run.message_id,
            model=str(run.publication["api_model_id"]),
        )
        run.complete(result, payload)
    except AnthropicMessagesRunError as exc:
        _fail_lease(run, exc.code)
        run.fail(exc)
    except Exception:
        logger.exception("Anthropic Messages run finalization failed")
        _fail_lease(run, "internal_error")
        run.fail(AnthropicMessagesRunError(
            "The published agent run failed.",
            code="internal_error",
        ))
    finally:
        with _ACTIVE_RUNS_LOCK:
            if _ACTIVE_RUNS.get(run.resolution.run["run_id"]) is run:
                _ACTIVE_RUNS.pop(run.resolution.run["run_id"], None)


def prepare_anthropic_message_run(
        store: Any,
        publication: Mapping[str, Any],
        key: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        request_id: str,
        conversation_store: Any = None,
        runtime_api: Any = None,
        hash_secret: Optional[bytes] = None,
) -> AnthropicMessagesAdmission:
    """Validate, resolve, settle, and submit before opening a response."""

    from core.agent_runtime_api import AgentRuntimeAPI, AgentStructuredRequest
    from core._a2a_standard_api import (
        ApiRunQuotaExceeded,
        ApiSessionQuotaExceeded,
    )

    runtime = runtime_api or AgentRuntimeAPI
    secret = hash_secret or standard_api_hash_secret(1)
    parsed = parse_anthropic_message_request(
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
            raise AnthropicMessagesError(
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
                raise AnthropicMessagesError(
                    "The active retry stream is unavailable; retry the request.",
                    status=409,
                    error_type="overloaded_error",
                    code="run_conflict",
                )
            return AnthropicMessagesAdmission(run=active, owner=False)

        run = AnthropicMessagesRun(
            store=store,
            conversation_store=conversation_store,
            publication=dict(publication),
            key=dict(key),
            parsed=parsed,
            turn=turn,
            resolution=resolution,
            runtime_api=runtime,
            hash_secret=secret,
            message_id=_message_id(run_id),
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
                "name": "Anthropic API client",
                "target_agent": publication["agent_name"],
                "visibility": "target_only",
                "publication_id": publication["publication_id"],
                "key_id": key["key_id"],
                "api_dialect": "anthropic_messages",
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
                        source,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
                live_callback=run.publish,
            ))
        except AnthropicMessagesError:
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
            raise AnthropicMessagesError(
                "The published agent could not start the request.",
                status=503,
                error_type="overloaded_error",
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
            name=f"anthropic-messages-{run_id[:16]}",
        ).start()
        return AnthropicMessagesAdmission(run=run, owner=True)


def wait_anthropic_message_payload(
        run: AnthropicMessagesRun,
) -> Dict[str, Any]:
    run._done.wait()
    if run.error is not None:
        raise run.error
    if run.payload is None:
        raise AnthropicMessagesRunError(
            "The published agent run ended without a response.",
            code="run_lost",
        )
    return run.payload


def _sse(event_type: str, value: Mapping[str, Any]) -> bytes:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return (
        "event: " + event_type + "\n"
        + "data: " + payload + "\n\n"
    ).encode("utf-8")


def _message_start(run: AnthropicMessagesRun) -> Dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": run.message_id,
            "type": "message",
            "role": "assistant",
            "model": str(run.publication["api_model_id"]),
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }


def iter_anthropic_message_sse(
        admission: AnthropicMessagesAdmission,
) -> Iterator[bytes]:
    """Translate one shared run to native Anthropic named SSE events."""

    run = admission.run
    offset = 0
    emitted_text = ""
    streamed_ids = set()
    text_started = False
    normal_end = False

    def event(event_type: str, **values: Any) -> bytes:
        return _sse(event_type, {"type": event_type, **values})

    try:
        yield _sse("message_start", _message_start(run))
        while True:
            events, offset, done = run.event_slice(
                offset,
                timeout=_KEEPALIVE_SECONDS,
            )
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
                        yield event(
                            "content_block_start",
                            index=0,
                            content_block={"type": "text", "text": ""},
                        )
                        text_started = True
                    emitted_text += delta
                    yield event(
                        "content_block_delta",
                        index=0,
                        delta={"type": "text_delta", "text": delta},
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
                            yield event(
                                "content_block_start",
                                index=0,
                                content_block={"type": "text", "text": ""},
                            )
                            text_started = True
                        emitted_text = content
                        yield event(
                            "content_block_delta",
                            index=0,
                            delta={"type": "text_delta", "text": content},
                        )
            if done and offset >= len(run._events):
                break

        if run.error is not None:
            yield event(
                "error",
                error={
                    "type": "api_error",
                    "message": "The published agent run failed.",
                },
                request_id=run.turn.request_id,
            )
            normal_end = True
            return

        result = run.result
        calls = list(getattr(result, "client_tool_calls", None) or ())
        if calls:
            for index, block in enumerate(_public_content(result)):
                yield event(
                    "content_block_start",
                    index=index,
                    content_block={
                        "type": "tool_use",
                        "id": block["id"],
                        "name": block["name"],
                        "input": {},
                    },
                )
                partial_json = json.dumps(
                    block["input"],
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if partial_json:
                    yield event(
                        "content_block_delta",
                        index=index,
                        delta={
                            "type": "input_json_delta",
                            "partial_json": partial_json,
                        },
                    )
                yield event("content_block_stop", index=index)
        else:
            final_text = str(getattr(result, "response", "") or "")
            if not text_started:
                yield event(
                    "content_block_start",
                    index=0,
                    content_block={"type": "text", "text": ""},
                )
                text_started = True
            if final_text.startswith(emitted_text):
                remainder = final_text[len(emitted_text):]
                if remainder:
                    yield event(
                        "content_block_delta",
                        index=0,
                        delta={"type": "text_delta", "text": remainder},
                    )
                    emitted_text += remainder
            elif not emitted_text and final_text:
                yield event(
                    "content_block_delta",
                    index=0,
                    delta={"type": "text_delta", "text": final_text},
                )
                emitted_text = final_text
            yield event("content_block_stop", index=0)

        usage = _usage(result)
        yield event(
            "message_delta",
            delta={
                "stop_reason": _stop_reason(result),
                "stop_sequence": None,
            },
            usage={"output_tokens": usage["output_tokens"]},
        )
        yield event("message_stop")
        normal_end = True
    finally:
        if (
                not normal_end
                and admission.owner
                and run.publication.get("api_disconnect_policy") == "cancel"
        ):
            run.cancel()


__all__ = [
    "AnthropicMessagesAdmission",
    "AnthropicMessagesError",
    "AnthropicMessagesRun",
    "AnthropicMessagesRunError",
    "ParsedAnthropicMessage",
    "anthropic_message_payload",
    "iter_anthropic_message_sse",
    "parse_anthropic_message_request",
    "prepare_anthropic_message_run",
    "wait_anthropic_message_payload",
]
