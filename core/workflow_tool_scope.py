"""Ephemeral server-side tool scopes for isolated workflow LLM calls."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import re
import threading
import uuid
from typing import Any, Callable, Iterator

from core.tool_json import parse_tool_arguments, tool_argument_parse_error


ToolGuard = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class WorkflowToolScope:
    conversation_id: str
    allowed_tools: frozenset[str]
    guard: ToolGuard
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempts: Counter[str] = field(default_factory=Counter)
    calls: Counter[str] = field(default_factory=Counter)


_lock = threading.RLock()
_scopes: dict[str, list[WorkflowToolScope]] = {}
_PROVIDER_EPHEMERAL_SUFFIX = re.compile(r"__ephemeral_[0-9a-f]{32}$")


def _scope_key(conversation_id: str) -> str:
    """Map a provider-owned ephemeral session back to its workflow scope."""

    value = str(conversation_id or "")
    return _PROVIDER_EPHEMERAL_SUFFIX.sub("", value)


def _current(conversation_id: str) -> WorkflowToolScope | None:
    with _lock:
        stack = _scopes.get(_scope_key(conversation_id))
        return stack[-1] if stack else None


@contextmanager
def workflow_tool_scope(
    conversation_id: str,
    allowed_tools: set[str] | frozenset[str] | tuple[str, ...],
    guard: ToolGuard,
) -> Iterator[WorkflowToolScope]:
    """Restrict MCP calls for one ephemeral workflow conversation."""

    conversation_id = str(conversation_id or "")
    if "::workflow::" not in conversation_id:
        raise ValueError("workflow tool scopes require an ephemeral workflow conversation")
    scope = WorkflowToolScope(
        conversation_id=conversation_id,
        allowed_tools=frozenset(str(name) for name in allowed_tools),
        guard=guard,
    )
    with _lock:
        _scopes.setdefault(conversation_id, []).append(scope)
    try:
        yield scope
    finally:
        with _lock:
            stack = _scopes.get(conversation_id, [])
            _scopes[conversation_id] = [
                item for item in stack if item.token != scope.token
            ]
            if not _scopes[conversation_id]:
                _scopes.pop(conversation_id, None)


def workflow_tool_visible_names(conversation_id: str) -> frozenset[str] | None:
    """Return the names visible to a scoped MCP client, or None if unscoped."""

    scope = _current(conversation_id)
    if scope is None:
        return None
    return scope.allowed_tools | frozenset({"get_tool_schema", "use_tool"})


def _wrapped_target(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    target = str(
        arguments.get("tool_name")
        or arguments.get("name")
        or arguments.get("tool")
        or ""
    )
    raw = arguments.get("arguments_json")
    if raw is None:
        raw = arguments.get("arguments")
    if raw is None:
        raw = arguments.get("params")
    if raw is None:
        raw = arguments.get("input")
    if raw is None:
        raw = {}
    parsed = parse_tool_arguments(
        raw, tool_name=target, provider="workflow_tool_scope",
    )
    error = tool_argument_parse_error(parsed)
    if error:
        raise ValueError(error)
    if not target or not isinstance(parsed, dict):
        raise ValueError("scoped use_tool requires a tool_name and JSON object arguments")
    return target, parsed


def authorize_workflow_tool(
    conversation_id: str,
    tool_name: str,
    arguments: dict[str, Any] | Any,
) -> dict[str, Any] | None:
    """Authorize and rewrite one MCP call; None means no workflow scope exists."""

    scope = _current(conversation_id)
    if scope is None:
        return None
    name = str(tool_name or "")
    args = dict(arguments) if isinstance(arguments, dict) else {}

    if name == "get_tool_schema":
        target = str(args.get("tool_name") or "")
        if not target:
            raise ValueError(
                "scoped get_tool_schema requires one allowed tool_name"
            )
        if target not in scope.allowed_tools:
            raise PermissionError(
                f"tool '{target}' is not allowed in this workflow phase"
            )
        return {"tool_name": name, "arguments": args}

    if name == "use_tool":
        target, target_args = _wrapped_target(args)
        if target not in scope.allowed_tools:
            raise PermissionError(
                f"tool '{target}' is not allowed in this workflow phase"
            )
        confined = scope.guard(target, target_args)
        scope.attempts[target] += 1
        rewritten = {
            "tool_name": target,
            "arguments_json": json.dumps(
                confined, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ),
        }
        return {
            "tool_name": name,
            "arguments": rewritten,
            "target_tool_name": target,
        }

    if name not in scope.allowed_tools:
        raise PermissionError(
            f"tool '{name}' is not allowed in this workflow phase"
        )
    confined = scope.guard(name, args)
    scope.attempts[name] += 1
    return {"tool_name": name, "arguments": confined}


def record_workflow_tool_result(
    conversation_id: str,
    tool_name: str,
    result: Any,
) -> None:
    """Count one scoped call only when its returned result is successful."""

    scope = _current(conversation_id)
    name = str(tool_name or "")
    if scope is None or name not in scope.allowed_tools:
        return
    if not isinstance(result, dict) or result.get("type") == "error":
        return
    data = result.get("data")
    texts = [data] if isinstance(data, str) else [
        str(item.get("text") or "")
        for item in data or []
        if isinstance(item, dict) and item.get("type") == "text"
    ] if isinstance(data, list) else []
    failure_prefixes = (
        "Error:", "Blocked by hook:", "[Interrupted by user",
        "[Running in background",
    )
    if any(text.startswith(failure_prefixes) for text in texts):
        return
    scope.calls[name] += 1
