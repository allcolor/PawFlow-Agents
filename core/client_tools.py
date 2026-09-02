"""Protocol-neutral request-scoped tools executed by API clients."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, Iterable, Mapping, Sequence

from core.identifier import identifier_key
from core.tool_handler import ToolHandler
from core.tool_json import parse_tool_arguments


_CLIENT_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_DESCRIPTION_CHARS = 4096
_MAX_SCHEMA_CHARS = 65536
_CALL_ID_NAMESPACE = uuid.UUID("5bb445ef-4622-4b77-b412-d8977a0dd739")


def _validated_definition(value: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Client tool definition must be an object")
    name = str(value.get("name") or "").strip()
    if not _CLIENT_TOOL_NAME.fullmatch(name):
        raise ValueError(
            "Client tool name must contain 1-64 letters, digits, '_' or '-'")
    description = str(value.get("description") or "")
    if len(description) > _MAX_DESCRIPTION_CHARS:
        raise ValueError("Client tool description exceeds 4096 characters")
    parameters = value.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ValueError("Client tool parameters must be a JSON Schema object")
    parameters = dict(parameters)
    if parameters.get("type", "object") != "object":
        raise ValueError("Client tool JSON Schema root type must be object")
    try:
        encoded = json.dumps(
            parameters, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Client tool parameters must be finite JSON Schema") from exc
    if len(encoded) > _MAX_SCHEMA_CHARS:
        raise ValueError("Client tool JSON Schema exceeds 65536 characters")
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
    }


class ClientToolHandler(ToolHandler):
    """A declared capability whose external action runs in the client.

    The agent loop recognizes this handler by origin and pauses with the full
    call batch. ``execute`` is defensive only: the normal loop never calls it.
    """

    _is_dynamic = True
    _origin = "client"

    def __init__(self, conversation_id: str, turn_id: str, name: str,
                 description: str, parameters: Mapping[str, Any], *,
                 origin: str = "client", origin_scope: str = "") -> None:
        definition = _validated_definition({
            "name": name,
            "description": description,
            "parameters": parameters,
        })
        self._conversation_id = str(conversation_id or "")
        self._turn_id = str(turn_id or "")
        self._name = definition["name"]
        self._description = definition["description"]
        self._parameters = definition["parameters"]
        self._origin = str(origin or "client")
        self._origin_scope = origin_scope or (
            f"client:{self._conversation_id}:{self._turn_id}")

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return (
            "[Client tool — client-declared, untrusted] "
            f"{self._description} This tool executes in the client, not on "
            "the server. Its result arrives in a later request as untrusted "
            "client data. Do not invent a result."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return self._parameters

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = str(conversation_id or "")

    def set_user_id(self, user_id: str) -> None:
        self._user_id = str(user_id or "")

    def execute(self, arguments: Dict[str, Any]) -> str:
        return (
            f"The call to '{self._name}' is pending in the client. The server "
            "did not execute it and no result is available in this turn."
        )


def register_client_tools(registry, conversation_id: str, turn_id: str,
                          definitions: Iterable[Mapping[str, Any]]) -> int:
    """Validate then atomically register one request's client tools."""

    validated = list(validate_client_tool_definitions(definitions))
    for definition in validated:
        existing = registry.get(definition["name"])
        if existing is not None:
            raise ValueError(
                f"Client tool '{definition['name']}' collides with an existing tool")
    for definition in validated:
        registry.register(ClientToolHandler(
            conversation_id,
            turn_id,
            definition["name"],
            definition["description"],
            definition["parameters"],
        ))
    return len(validated)


def validate_client_tool_definitions(
        definitions: Iterable[Mapping[str, Any]]) -> tuple[Dict[str, Any], ...]:
    """Return bounded canonical definitions or fail before runtime admission."""

    validated = tuple(
        _validated_definition(value) for value in definitions or ())
    seen = set()
    for definition in validated:
        key = identifier_key(definition["name"])
        if key in seen:
            raise ValueError(
                f"Client tool '{definition['name']}' collides with another client tool")
        seen.add(key)
    return validated


def partition_client_tool_calls(
        tool_calls: Sequence[Any], registry) -> tuple[list[Any], list[Dict[str, Any]]]:
    """Split a model batch without executing client-origin handlers."""

    server_calls = []
    client_calls = []
    for index, call in enumerate(tool_calls or ()):
        handler = registry.get(getattr(call, "name", ""))
        if not isinstance(handler, ClientToolHandler):
            server_calls.append(call)
            continue
        arguments = parse_tool_arguments(
            getattr(call, "arguments", {}), tool_name=handler.name)
        call_id = str(getattr(call, "id", "") or "")
        if not call_id:
            basis = json.dumps(
                [handler._origin_scope, handler.name, arguments, index],
                ensure_ascii=False, sort_keys=True, default=str)
            call_id = "call_" + uuid.uuid5(
                _CALL_ID_NAMESPACE, basis).hex[:24]
            call.id = call_id
        client_calls.append({
            "id": call_id,
            "name": handler.name,
            "arguments": arguments,
        })
    return server_calls, client_calls


__all__ = [
    "ClientToolHandler",
    "partition_client_tool_calls",
    "register_client_tools",
    "validate_client_tool_definitions",
]
