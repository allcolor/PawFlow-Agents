"""Versioned canonicalization and HMAC chains for standard API continuity."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from core.standard_api_types import (
    NormalizedVisibleItem,
    StandardApiNamespace,
)


_ITEM_FIELDS = {
    "client_instruction": frozenset({"role", "content", "name"}),
    "user_message": frozenset({"content", "name"}),
    "assistant_message": frozenset({
        "content", "name", "tool_calls", "refusal", "annotations", "citations"}),
    "client_tool_call_batch": frozenset({"calls"}),
    "client_tool_result_batch": frozenset({"results"}),
    "response_output": frozenset({"output"}),
}

_BOUNDARY_KINDS = frozenset({
    "assistant_message",
    "client_tool_call_batch",
    "response_output",
})


def _assert_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Canonical JSON numbers must be finite")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Canonical JSON object keys must be strings")
            _assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite(item)


def canonical_json(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON for an already normalized value."""

    _assert_finite(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Value is not finite canonical JSON") from exc
    return encoded.encode("utf-8")


def _canonical_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"encoding": "raw", "value": value}
        _assert_finite(parsed)
        return {"encoding": "json", "value": parsed}
    _assert_finite(value)
    return {"encoding": "json", "value": value}


def _canonical_content(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if not isinstance(value, (list, tuple)):
        raise ValueError("Normalized content must be text, null, or an array")

    parts: list[dict[str, Any]] = []
    for part in value:
        if not isinstance(part, Mapping):
            raise ValueError("Normalized content parts must be objects")
        part_type = str(part.get("type") or "")
        if part_type == "text":
            text = part.get("text")
            if not isinstance(text, str):
                raise ValueError("A text content part requires text")
            parts.append({"type": "text", "text": text})
            continue
        if part_type not in {"image_url", "image_data"}:
            raise ValueError(
                f"Unsupported normalized content part type: {part_type}")
        media_type = part.get("media_type")
        reference_key = "url" if part_type == "image_url" else "data"
        reference = part.get(reference_key)
        if not isinstance(media_type, str) or not media_type:
            raise ValueError("Image content requires media_type")
        if not isinstance(reference, str) or not reference:
            raise ValueError(f"{part_type} content requires {reference_key}")
        parts.append({
            "type": part_type,
            "media_type": media_type,
            reference_key: reference,
        })
    return parts


def _canonical_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Normalized client tool calls must be an array")
    result = []
    for call in value:
        if not isinstance(call, Mapping):
            raise ValueError("Normalized client tool calls must be objects")
        call_id = call.get("id")
        name = call.get("name")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("A normalized client tool call requires id")
        if not isinstance(name, str) or not name:
            raise ValueError("A normalized client tool call requires name")
        result.append({
            "id": call_id,
            "name": name,
            "arguments": _canonical_tool_arguments(call.get("arguments", {})),
        })
    return result


def _canonical_results(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("Normalized client tool results must be an array")
    result = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("Normalized client tool results must be objects")
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id:
            raise ValueError("A normalized client tool result requires id")
        result.append({
            "id": call_id,
            "content": _canonical_content(item.get("content")),
            "error": bool(item.get("error", False)),
        })
    return result


def canonical_visible_item(item: NormalizedVisibleItem) -> dict[str, Any]:
    """Project one item onto its semantic allowlist."""

    allowed = _ITEM_FIELDS[item.kind]
    unknown = sorted(set(item.data) - allowed)
    if unknown:
        raise ValueError(
            f"Unsupported {item.kind} fields: " + ", ".join(unknown))

    result: dict[str, Any] = {"kind": item.kind}
    if item.kind in {
            "client_instruction", "user_message", "assistant_message"}:
        result["content"] = _canonical_content(item.data.get("content"))
        name = item.data.get("name")
        if name not in (None, ""):
            if not isinstance(name, str):
                raise ValueError("Normalized message name must be a string")
            result["name"] = name
        if item.kind == "client_instruction":
            role = item.data.get("role", "system")
            if role not in {"system", "developer"}:
                raise ValueError(
                    "Client instruction role must be system or developer")
            result["role"] = role
        if item.kind == "assistant_message" and item.data.get("tool_calls"):
            result["tool_calls"] = _canonical_calls(
                item.data["tool_calls"])
        return result
    if item.kind == "client_tool_call_batch":
        result["calls"] = _canonical_calls(item.data.get("calls"))
        return result
    if item.kind == "client_tool_result_batch":
        result["results"] = _canonical_results(item.data.get("results"))
        return result

    output = item.data.get("output")
    _assert_finite(output)
    result["output"] = output
    return result


def compute_hash_chain(
        namespace: StandardApiNamespace,
        items: Sequence[NormalizedVisibleItem],
        secret: bytes,
) -> tuple[str, ...]:
    """Compute every HMAC chain head in one pass."""

    if not isinstance(secret, bytes) or not secret:
        raise ValueError("A non-empty hash secret is required")
    namespace_bytes = canonical_json(namespace.as_dict())
    previous = hmac.new(secret, namespace_bytes, hashlib.sha256).digest()
    heads = []
    for item in items:
        item_bytes = canonical_json(canonical_visible_item(item))
        framed = previous + len(item_bytes).to_bytes(8, "big") + item_bytes
        previous = hmac.new(secret, framed, hashlib.sha256).digest()
        heads.append(previous.hex())
    return tuple(heads)


def eligible_prefixes(
        items: Sequence[NormalizedVisibleItem],
        hashes: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    """Return only completed externally visible server boundaries."""

    if len(items) != len(hashes):
        raise ValueError("items and hashes must have the same length")
    return tuple(
        {
            "prefix_hash": hashes[index],
            "item_count": index + 1,
            "boundary_kind": item.kind,
        }
        for index, item in enumerate(items)
        if item.kind in _BOUNDARY_KINDS
    )


def canonical_request_fingerprint(
        value: Mapping[str, Any],
        secret: bytes,
        *,
        excluded_fields: Iterable[str] = (),
) -> str:
    """Fingerprint a validated request projection for active-run coalescing."""

    if not isinstance(secret, bytes) or not secret:
        raise ValueError("A non-empty hash secret is required")
    excluded = set(excluded_fields)
    projected = {
        key: item for key, item in value.items()
        if key not in excluded
    }
    payload = canonical_json(projected)
    return hmac.new(
        secret,
        b"pawflow-standard-api-request-v1\x00" + payload,
        hashlib.sha256,
    ).hexdigest()
