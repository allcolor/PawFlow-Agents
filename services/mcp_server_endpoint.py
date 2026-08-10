"""Inbound MCP Streamable HTTP endpoint for published conversations."""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

from core.mcp_server_store import MCPServerStore


logger = logging.getLogger(__name__)
_ROUTE_OWNER = "_published_mcp_server"
_PROTOCOL_VERSION = "2025-03-26"
_SUPPORTED_PROTOCOLS = {_PROTOCOL_VERSION}
_MAX_REQUEST_BYTES = 2_000_000
_SESSION_TTL_SECONDS = 8 * 3600

_sessions: Dict[str, Dict[str, Any]] = {}
_sessions_lock = threading.RLock()
_runtime_lock = threading.Lock()
_tool_runtime = None
_lease_sweeper_lock = threading.Lock()
_lease_sweeper_started = False


_MCP_TOOLS = [
    {
        "name": "get_tool_schema",
        "description": (
            "Get the full parameter schema for a PawFlow tool, or list all "
            "available tools. Call with tool_name to get the schema."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Tool name to inspect; omit to list tools",
                }
            },
        },
    },
    {
        "name": "use_tool",
        "description": (
            "Execute a PawFlow tool. Call get_tool_schema first. arguments_json "
            "must be a JSON object encoded as a string; use '{}' for no arguments."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "arguments_json": {
                    "type": "string",
                    "description": "JSON object string containing the tool arguments",
                },
            },
            "required": ["tool_name", "arguments_json"],
        },
    },
]


def _json_response(req, status: int, payload: Any,
                   headers: Optional[Dict[str, str]] = None) -> None:
    body = b"" if payload is None else json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    response_headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "Content-Length": str(len(body)),
    }
    response_headers.update(headers or {})
    req.complete(status, response_headers, body)


def _rpc_result(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str,
               data: Any = None) -> Dict[str, Any]:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _bearer(headers: Dict[str, str]) -> str:
    value = ""
    for key, candidate in (headers or {}).items():
        if str(key).lower() == "authorization":
            value = str(candidate or "")
            break
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


def _authenticate(req, server_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not _origin_allowed(req):
        _json_response(req, 403, {"error": "Origin is not allowed"})
        return None, None
    store = MCPServerStore.instance()
    key = store.validate_key(server_id, _bearer(req.headers))
    server = store.get(server_id) if key else None
    if not key or not server:
        _json_response(
            req, 401, {"error": "Unauthorized"},
            {"WWW-Authenticate": "Bearer"},
        )
        return None, None
    try:
        from core.conversation_store import ConversationStore
        owner = ConversationStore.instance().resolve_owner(server["conversation_id"])
    except Exception:
        owner = ""
    if not owner or owner != server["owner_user_id"]:
        _json_response(req, 403, {"error": "Published conversation is unavailable"})
        return None, None
    try:
        from core.conv_agent_config import get_all_agent_configs
        configs = get_all_agent_configs(server["conversation_id"]) or {}
        needle = server["agent_name"].lower()
        canonical = next(
            (name for name in configs if isinstance(name, str) and name.lower() == needle),
            "",
        )
    except Exception:
        canonical = ""
    if not canonical:
        _json_response(req, 403, {"error": "Published agent is no longer attached"})
        return None, None
    server = dict(server)
    server["agent_name"] = canonical
    return server, key


def _header(req, name: str) -> str:
    needle = name.lower()
    for key, value in (req.headers or {}).items():
        if str(key).lower() == needle:
            return str(value or "")
    return ""


def _origin_allowed(req) -> bool:
    """Reject cross-authority browser Origins to prevent DNS rebinding."""
    origin = _header(req, "Origin").strip()
    if not origin:
        return True
    try:
        parsed_origin = urlsplit(origin)
        if (parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None):
            return False
        forwarded_proto = _header(req, "X-Forwarded-Proto").split(",", 1)[0].strip()
        expected_scheme = forwarded_proto or parsed_origin.scheme
        host = (_header(req, "X-Forwarded-Host") or _header(req, "Host")).split(",", 1)[0].strip()
        parsed_host = urlsplit("//" + host)
        if not parsed_host.hostname or parsed_origin.scheme != expected_scheme:
            return False
        default_port = 443 if expected_scheme == "https" else 80
        return (
            parsed_origin.hostname.lower() == parsed_host.hostname.lower()
            and (parsed_origin.port or default_port) == (parsed_host.port or default_port)
        )
    except ValueError:
        return False


def _new_session(server: Dict[str, Any], key: Dict[str, Any], protocol: str) -> str:
    session_id = secrets.token_urlsafe(24)
    now = time.time()
    with _sessions_lock:
        expired = [sid for sid, value in _sessions.items()
                   if float(value.get("last_seen") or 0) < now - _SESSION_TTL_SECONDS]
        for sid in expired:
            _sessions.pop(sid, None)
        _sessions[session_id] = {
            "server_id": server["server_id"],
            "key_id": key["key_id"],
            "protocol": protocol,
            "initialized": False,
            "created_at": now,
            "last_seen": now,
        }
    return session_id


def _require_session(req, server: Dict[str, Any], key: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    session_id = _header(req, "Mcp-Session-Id")
    if not session_id:
        _json_response(req, 400, {"error": "Mcp-Session-Id header is required"})
        return None
    now = time.time()
    with _sessions_lock:
        session = _sessions.get(session_id) if session_id else None
        if (not session or session.get("server_id") != server["server_id"]
                or session.get("key_id") != key["key_id"]
                or float(session.get("last_seen") or 0) < now - _SESSION_TTL_SECONDS):
            if session_id:
                _sessions.pop(session_id, None)
            _json_response(req, 404, {"error": "MCP session not found"})
            return None
        session["last_seen"] = now
        return dict(session, session_id=session_id)


def _runtime():
    global _tool_runtime
    if _tool_runtime is None:
        with _runtime_lock:
            if _tool_runtime is None:
                from services.tool_relay_service import ToolRelayService
                _tool_runtime = ToolRelayService({"_service_id": "_published_mcp"})
    return _tool_runtime


def _registry(server: Dict[str, Any]):
    registry = _runtime()._get_registry(
        server["owner_user_id"], server["conversation_id"], server["agent_name"])
    return registry.fork()


def _available_tool_rows(registry) -> list:
    return [{
        "name": definition.get("name", ""),
        "description": definition.get("description", ""),
    } for definition in registry.get_tool_definitions()]


def _tool_schema(registry, tool_name: str) -> Any:
    definitions = registry.get_tool_definitions()
    if not tool_name:
        return _available_tool_rows(registry)
    for definition in definitions:
        if definition.get("name") == tool_name:
            return definition
    return f"Error: unknown tool '{tool_name}'"


def _image_marker_text(registry, tool_name: str, arguments: dict,
                       result: Any) -> Optional[str]:
    """Return a marker-form image result only for an image-producing tool."""
    from core.handlers.meta_tools import resolve_result_shape_handler

    handler = resolve_result_shape_handler(registry, tool_name, arguments)
    if not handler or not getattr(handler, "_returns_images", False):
        return None
    if isinstance(result, str):
        return result if "__image_data__:" in result else None
    if not isinstance(result, list):
        return None
    lines = []
    has_image = False
    for block in result:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            lines.append(str(block.get("text") or ""))
        elif block_type == "image" and block.get("data"):
            mime = str(block.get("mimeType") or "image/png")
            lines.append(f"__image_data__:{mime}:{block['data']}")
            has_image = True
    return "\n".join(lines) if has_image else None


def _apply_published_image_output(server: Dict[str, Any], registry,
                                  tool_name: str, arguments: dict,
                                  result: Any) -> Any:
    if str(server.get("image_output") or "native") != "describe":
        return result
    marker_text = _image_marker_text(registry, tool_name, arguments, result)
    if marker_text is None:
        return result
    from core.conv_agent_config import get_agent_config
    from core.vision_describe import describe_tool_result_images

    agent_svc = str(get_agent_config(
        server["conversation_id"], server["agent_name"]
    ).get("llm_service") or "")
    described = describe_tool_result_images(
        marker_text, agent_svc=agent_svc,
        user_id=server["owner_user_id"],
        conversation_id=server["conversation_id"],
        agent_name=server["agent_name"], force=True,
    )
    if described is None:
        return (
            "Error: MCP image output is set to describe, but the published "
            "agent has no usable vision service. Configure its LLM or "
            "vision_llm_service, or select native image output."
        )
    return described


def _compact_content_for_transcript(blocks: list) -> str:
    lines = []
    for block in blocks:
        if not isinstance(block, dict):
            lines.append(str(block))
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text = str(block.get("text") or "")
            if text:
                lines.append(text)
        elif block_type == "image":
            mime = str(block.get("mimeType") or "image")
            lines.append(f"[Image returned to MCP client: {mime}]")
        elif block_type:
            lines.append(f"[{block_type} content returned to MCP client]")
    return "\n".join(lines) or "(no output)"


def _encode_tool_content(registry, tool_name: str, arguments: dict,
                         result: Any) -> Tuple[list, bool, str]:
    if isinstance(result, list):
        compact = _compact_content_for_transcript(result)
        return result, compact.startswith("Error:"), compact
    text = str(result if result is not None and result != "" else "(no output)")
    handler = registry.get(tool_name)
    if (handler is not None and getattr(handler, "_returns_images", False)
            and "__image_data__:" in text):
        blocks = []
        for line in text.splitlines():
            if line.startswith("__image_data__:"):
                parts = line.split(":", 2)
                if len(parts) == 3:
                    blocks.append({"type": "image", "mimeType": parts[1], "data": parts[2]})
            elif line.strip():
                blocks.append({"type": "text", "text": line})
        if blocks:
            compact = _compact_content_for_transcript(blocks)
            return blocks, compact.startswith("Error:"), compact
    return [{"type": "text", "text": text}], text.startswith("Error:"), text


def _persist_tool_call(server: Dict[str, Any], key: Dict[str, Any],
                       tool_name: str, arguments: dict, result_text: str,
                       tool_call_id: str) -> None:
    from core.conversation_writer import ConversationWriter
    from core.llm_client import stamp_message

    conversation_id = server["conversation_id"]
    source = {
        "type": "mcp_client",
        "name": key.get("label") or key.get("prefix") or "MCP client",
        "key_id": key.get("key_id", ""),
        "server_id": server["server_id"],
    }
    writer = ConversationWriter.for_conversation(conversation_id)
    call_message = stamp_message({
        "role": "assistant", "content": "", "source": source,
        "tool_calls": [{
            "id": tool_call_id, "name": tool_name, "arguments": arguments,
        }],
    }, conversation_id)
    writer.enqueue_message(
        call_message, agent_name=server["agent_name"],
        user_id=server["owner_user_id"],
        sse_events=[{"type": "tool_call", "data": {
            "tool": tool_name, "arguments": arguments,
            "agent_name": server["agent_name"], "llm_service": "mcp",
            "source": "mcp", "ts": time.time(),
        }}], wait=True,
    )
    result_message = stamp_message({
        "role": "tool", "content": result_text,
        "tool_call_id": tool_call_id, "source": source,
    }, conversation_id)
    writer.enqueue_message(
        result_message, agent_name=server["agent_name"],
        user_id=server["owner_user_id"],
        sse_events=[{"type": "tool_result", "data": {
            "tool": tool_name, "result": result_text[:2000],
            "agent_name": server["agent_name"], "llm_service": "mcp",
            "source": "mcp",
        }}], wait=True,
    )


def _call_tool(server: Dict[str, Any], key: Dict[str, Any],
               name: str, arguments: Any) -> Dict[str, Any]:
    registry = _registry(server)
    args = arguments if isinstance(arguments, dict) else {}
    executed_name = name
    executed_args = args
    if name == "get_tool_schema":
        result = _tool_schema(registry, str(args.get("tool_name") or ""))
    elif name == "use_tool":
        tool_name = str(args.get("tool_name") or "").strip()
        if tool_name in {"use_tool", "mcp__pawflow__use_tool", "mcp_pawflow_use_tool"}:
            result = "Error: nested use_tool calls are not allowed"
            tool_name = "use_tool"
            tool_args = {}
        else:
            from core.tool_json import parse_tool_arguments, tool_argument_parse_error
            raw = args.get("arguments_json")
            if raw is None:
                raw = args.get("arguments", {})
            tool_args = parse_tool_arguments(raw, tool_name=tool_name,
                                             provider="published_mcp")
            parse_error = tool_argument_parse_error(tool_args)
            if not tool_name:
                result = "Error: missing required parameter 'tool_name'"
            elif parse_error:
                result = parse_error
            elif not isinstance(tool_args, dict):
                result = f"Error: arguments for {tool_name} must be a JSON object"
            else:
                tool_call_id = uuid.uuid4().hex[:12]
                execution = _runtime()._do_execute(
                    tool_call_id, tool_name, tool_args,
                    server["owner_user_id"], server["conversation_id"],
                    server["agent_name"],
                )
                result = execution.get("data") if isinstance(execution, dict) else execution
        executed_name = tool_name or "use_tool"
        executed_args = tool_args if isinstance(tool_args, dict) else {}
    else:
        result = f"Error: unknown tool '{name}'"

    if name == "use_tool" and executed_name != "use_tool":
        result = _apply_published_image_output(
            server, registry, executed_name, executed_args, result)

    content, is_error, result_text = _encode_tool_content(
        registry, executed_name, executed_args, result)
    # Schema discovery is read-only protocol chatter; only real executions
    # belong in the ordinary conversation transcript.
    if name == "use_tool" and executed_name != "use_tool":
        try:
            _persist_tool_call(
                server, key, executed_name, executed_args, result_text,
                locals().get("tool_call_id") or uuid.uuid4().hex[:12],
            )
        except Exception:
            logger.error("Could not persist published MCP tool call", exc_info=True)
    return {"content": content, "isError": bool(is_error)}


def _dispatch_session_message(server: Dict[str, Any], key: Dict[str, Any],
                              message: Any, session_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        request_id = message.get("id") if isinstance(message, dict) else None
        return _rpc_error(request_id, -32600, "Invalid Request")
    method = str(message.get("method") or "")
    request_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if not method and ("result" in message or "error" in message):
        return None
    if method == "notifications/initialized":
        with _sessions_lock:
            if session_id in _sessions:
                _sessions[session_id]["initialized"] = True
        return None
    if method == "ping":
        return _rpc_result(request_id, {}) if request_id is not None else None
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": _MCP_TOOLS}) if request_id is not None else None
    if method == "tools/call":
        name = str(params.get("name") or "")
        if not name:
            return _rpc_error(request_id, -32602, "Tool name is required")
        result = _call_tool(server, key, name, params.get("arguments") or {})
        return _rpc_result(request_id, result) if request_id is not None else None
    return (
        _rpc_error(request_id, -32601, f"Method not found: {method}")
        if request_id is not None else None
    )


def handle_mcp_post(req) -> None:
    server_id = str((req.path_params or {}).get("server_id") or "")
    server, key = _authenticate(req, server_id)
    if not server:
        return
    if len(req.body or b"") > _MAX_REQUEST_BYTES:
        _json_response(req, 413, {"error": "Request body too large"})
        return
    try:
        message = json.loads((req.body or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _json_response(req, 400, _rpc_error(None, -32700, "Parse error"))
        return
    is_batch = isinstance(message, list)
    if is_batch and not message:
        _json_response(req, 400, _rpc_error(None, -32600, "Invalid Request"))
        return
    messages = message if is_batch else [message]
    if not is_batch and (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"):
        _json_response(req, 400, _rpc_error(
            message.get("id") if isinstance(message, dict) else None,
            -32600, "Invalid Request"))
        return
    initializers = [item for item in messages
                    if isinstance(item, dict) and item.get("method") == "initialize"]
    if initializers:
        if len(messages) != 1 or initializers[0].get("id") is None:
            _json_response(req, 400, _rpc_error(None, -32600,
                                                 "Initialize must be one request"))
            return
        initialize = initializers[0]
        params = initialize.get("params") if isinstance(initialize.get("params"), dict) else {}
        requested = str(params.get("protocolVersion") or _PROTOCOL_VERSION)
        protocol = requested if requested in _SUPPORTED_PROTOCOLS else _PROTOCOL_VERSION
        session_id = _new_session(server, key, protocol)
        response = _rpc_result(initialize.get("id"), {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "PawFlow", "version": "1.0"},
        })
        _json_response(req, 200, [response] if is_batch else response,
                       {"Mcp-Session-Id": session_id})
        return

    session = _require_session(req, server, key)
    if not session:
        return
    session_headers = {"Mcp-Session-Id": session["session_id"]}
    responses = [
        response for response in (
            _dispatch_session_message(server, key, item, session["session_id"])
            for item in messages
        ) if response is not None
    ]
    if not responses:
        _json_response(req, 202, None, session_headers)
    else:
        payload = responses if is_batch else responses[0]
        _json_response(req, 200, payload, session_headers)


def handle_mcp_delete(req) -> None:
    server_id = str((req.path_params or {}).get("server_id") or "")
    server, key = _authenticate(req, server_id)
    if not server:
        return
    session = _require_session(req, server, key)
    if not session:
        return
    with _sessions_lock:
        _sessions.pop(session["session_id"], None)
    _json_response(req, 204, None)


def handle_mcp_get(req) -> None:
    server_id = str((req.path_params or {}).get("server_id") or "")
    server, _key = _authenticate(req, server_id)
    if server:
        _json_response(req, 405, {"error": "Server-initiated SSE is not supported"},
                       {"Allow": "POST, DELETE"})


def _relay_id(server_id: str) -> str:
    return "mcp_cli_" + "".join(ch for ch in server_id if ch.isalnum())[:24]


def _request_json(req) -> Dict[str, Any]:
    try:
        value = json.loads((req.body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _relay_status(server: Dict[str, Any]) -> Dict[str, Any]:
    relay_id = server.get("active_relay_id") or _relay_id(server["server_id"])
    connected = False
    try:
        from core.service_registry import ServiceRegistry
        service = ServiceRegistry.get_instance().resolve(
            relay_id, user_id=server["owner_user_id"],
            conv_id=server["conversation_id"])
        connected = bool(service and service.is_connected())
    except Exception:
        logger.debug("Published MCP relay status failed", exc_info=True)
    return {
        "connected": connected,
        "client_active": bool(server.get("client_active")),
        "client_id": server.get("active_client_id", ""),
        "client_name": server.get("active_client_name", ""),
        "relay_id": relay_id,
        "last_heartbeat": server.get("client_heartbeat_at", 0),
    }


def remove_mcp_relay(server: Dict[str, Any]) -> None:
    """Remove one published CLI relay service and its agent binding."""
    if not server:
        return
    relay_id = str(server.get("active_relay_id") or _relay_id(server["server_id"]))
    try:
        from core.relay_bindings import unlink_relay
        unlink_relay(
            server["conversation_id"], relay_id,
            agent=server["agent_name"],
        )
    except Exception:
        logger.warning("Could not unlink MCP CLI relay %s", relay_id,
                       exc_info=True)
    try:
        from core.service_registry import ServiceRegistry
        ServiceRegistry.get_instance().uninstall(
            "conv", server["conversation_id"], relay_id)
    except Exception:
        logger.warning("Could not uninstall MCP CLI relay %s", relay_id,
                       exc_info=True)


def sweep_expired_mcp_relays() -> int:
    """Remove services and bindings left by crashed MCP CLI processes."""
    expired = MCPServerStore.instance().expire_stale_clients()
    for server in expired:
        remove_mcp_relay(server)
    return len(expired)


def _start_lease_sweeper() -> None:
    global _lease_sweeper_started
    with _lease_sweeper_lock:
        if _lease_sweeper_started:
            return
        _lease_sweeper_started = True

    def _sweep() -> None:
        while True:
            time.sleep(30)
            try:
                sweep_expired_mcp_relays()
            except Exception:
                logger.warning("Published MCP relay lease sweep failed",
                               exc_info=True)

    threading.Thread(
        target=_sweep, daemon=True, name="pawflow-mcp-lease-sweeper",
    ).start()


def handle_relay_connect(req) -> None:
    server_id = str((req.path_params or {}).get("server_id") or "")
    server, _key = _authenticate(req, server_id)
    if not server:
        return
    body = _request_json(req)
    client_id = str(body.get("client_id") or "").strip()
    client_name = str(body.get("client_name") or "CLI").strip()[:80]
    if not client_id or len(client_id) > 160:
        _json_response(req, 400, {"error": "client_id is required"})
        return
    relay_id = _relay_id(server_id)
    token = secrets.token_urlsafe(32)
    store = MCPServerStore.instance()
    try:
        server = store.claim_client(server_id, client_id, client_name, relay_id)
    except RuntimeError as exc:
        _json_response(req, 409, {
            "error": "mcp_server_already_in_use", "message": str(exc),
            "active": _relay_status(store.get(server_id) or server),
        })
        return
    try:
        from core.service_registry import ServiceRegistry
        ServiceRegistry.get_instance().install(
            scope="conv", scope_id=server["conversation_id"],
            service_id=relay_id, service_type="relay",
            config={
                "token": token,
                "mode": "readonly" if body.get("readonly") else "readwrite",
                "mcp_external": True,
                "mcp_server_id": server_id,
                "mcp_client_name": client_name,
                "_service_id": relay_id,
            },
            description=f"MCP CLI workspace ({client_name})",
        )
        from core.relay_bindings import link_relay
        link_relay(
            server["conversation_id"], relay_id,
            agent=server["agent_name"], user_id=server["owner_user_id"],
            auto_default=False,
        )
    except Exception as exc:
        remove_mcp_relay(server)
        store.release_client(server_id, client_id)
        logger.error("Could not register MCP CLI relay", exc_info=True)
        _json_response(req, 500, {"error": "relay_registration_failed", "message": str(exc)})
        return
    proto = _header(req, "X-Forwarded-Proto") or "http"
    scheme = "wss" if proto.lower() == "https" else "ws"
    host = _header(req, "X-Forwarded-Host") or _header(req, "Host") or "localhost"
    _json_response(req, 200, {
        "ok": True, "relay_id": relay_id,
        "ws_url": f"{scheme}://{host}/ws/relay/{relay_id}",
        "relay_token": token,
        "conversation_id": server["conversation_id"],
        "agent_name": server["agent_name"],
        "auto_default": False,
    })


def handle_relay_status(req) -> None:
    server_id = str((req.path_params or {}).get("server_id") or "")
    server, _key = _authenticate(req, server_id)
    if not server:
        return
    body = _request_json(req)
    client_id = str(body.get("client_id") or "")
    if client_id:
        MCPServerStore.instance().heartbeat_client(server_id, client_id)
        server = MCPServerStore.instance().get(server_id) or server
    _json_response(req, 200, _relay_status(server))


def handle_relay_disconnect(req) -> None:
    server_id = str((req.path_params or {}).get("server_id") or "")
    server, _key = _authenticate(req, server_id)
    if not server:
        return
    body = _request_json(req)
    client_id = str(body.get("client_id") or "")
    if not client_id or client_id != str(server.get("active_client_id") or ""):
        _json_response(req, 409, {"error": "mcp_client_lease_mismatch"})
        return
    remove_mcp_relay(server)
    release_client = bool(body.get("release_client"))
    released = (
        MCPServerStore.instance().release_client(server_id, client_id)
        if release_client else False
    )
    _json_response(req, 200, {
        "ok": True,
        "released": released,
        "client_active": not release_client,
        "relay_id": _relay_id(server_id),
    })


def register_mcp_routes(listener) -> None:
    """Register idempotent public-token routes on an HTTP listener.

    ``public=True`` means the generic browser/session gate is skipped; every
    callback performs its own scoped Bearer authentication.  The listener's
    Private Gateway still runs first and remains mandatory when configured.
    """
    _start_lease_sweeper()
    existing = {row.get("pattern", "") for row in listener.get_routes()}
    routes = [
        ("POST", "/mcp/{server_id}", handle_mcp_post),
        ("DELETE", "/mcp/{server_id}", handle_mcp_delete),
        ("GET", "/mcp/{server_id}", handle_mcp_get),
        ("POST", "/mcp/{server_id}/relay/connect", handle_relay_connect),
        ("POST", "/mcp/{server_id}/relay/status", handle_relay_status),
        ("POST", "/mcp/{server_id}/relay/disconnect", handle_relay_disconnect),
    ]
    for method, pattern, callback in routes:
        if pattern not in existing:
            listener.register_route(
                method, pattern, _ROUTE_OWNER,
                callback=callback, public=True,
            )


def ensure_mcp_routes() -> None:
    """Install inbound MCP routes on the running main HTTP listener."""
    from services.http_listener_service import HTTPListenerService
    listener = next(iter(HTTPListenerService.all_instances().values()), None)
    if listener is None:
        raise RuntimeError("No HTTP listener is available for the MCP endpoint")
    register_mcp_routes(listener)


__all__ = [
    "register_mcp_routes",
    "ensure_mcp_routes",
    "remove_mcp_relay",
    "sweep_expired_mcp_relays",
]
