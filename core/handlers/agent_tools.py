"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import http.client
import re
import ssl
import threading
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class BrowserActionHandler(ToolHandler):
    """Interactive browser control via Playwright."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Interactive browser. Actions: navigate (go to URL), click (click element), "
            "fill (fill input field), extract (get text content), screenshot (capture page — "
            "useful for visual debugging and verifying UI changes), "
            "scroll (scroll up/down), wait (wait for element), close (close browser). "
            "Tips: use screenshot to verify web pages visually; use extract with 'body' selector "
            "to get full page text; combine with filesystem(action=exec) to run local dev servers "
            "or build scripts before navigating."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["navigate", "click", "fill", "extract", "screenshot",
                             "scroll", "wait", "close"],
                    "description": "Browser action to perform",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for navigate action)",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector (for click/fill/extract/wait)",
                },
                "value": {
                    "type": "string",
                    "description": "Value to fill (for fill action)",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (default: down)",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Timeout in ms for wait action (default: 5000)",
                },
            },
            "required": ["action"],
        }

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        action = arguments.get("action", "")
        if not action:
            return "Error: action is required"

        conv_id = self._conversation_id or "default"

        try:
            from services.browser_service import BrowserService
            svc = BrowserService.instance()

            if action == "navigate":
                url = arguments.get("url", "")
                if not url:
                    return "Error: url is required for navigate"
                return svc.navigate(conv_id, url)

            elif action == "click":
                selector = arguments.get("selector", "")
                if not selector:
                    return "Error: selector is required for click"
                return svc.click(conv_id, selector)

            elif action == "fill":
                selector = arguments.get("selector", "")
                value = arguments.get("value", "")
                if not selector:
                    return "Error: selector is required for fill"
                return svc.fill(conv_id, selector, value)

            elif action == "extract":
                selector = arguments.get("selector", "")
                if not selector:
                    return "Error: selector is required for extract"
                return svc.extract(conv_id, selector)

            elif action == "screenshot":
                return svc.screenshot(conv_id)

            elif action == "scroll":
                direction = arguments.get("direction", "down")
                return svc.scroll(conv_id, direction)

            elif action == "wait":
                selector = arguments.get("selector", "")
                if not selector:
                    return "Error: selector is required for wait"
                timeout_ms = int(arguments.get("timeout_ms", 5000))
                return svc.wait_for(conv_id, selector, timeout_ms)

            elif action == "close":
                svc.close_session(conv_id)
                return "Browser session closed."

            else:
                return f"Error: unknown action '{action}'"

        except ImportError:
            return "Error: Playwright not installed. Install with: pip install playwright"
        except Exception as e:
            return f"Browser error: {e}"


class LinkIdentityHandler(ToolHandler):
    """Generate a code to link identity across channels."""

    _pending_codes: Dict[str, Dict[str, str]] = {}  # code -> {user_id, channel, channel_id, expires}
    _codes_lock = threading.Lock()

    def __init__(self):
        self._user_id = ""
        self._channel = ""
        self._channel_id = ""

    @property
    def name(self) -> str:
        return "link_identity"

    @property
    def description(self) -> str:
        return (
            "Link your identity across channels (web, Telegram, Discord, Slack, WhatsApp). "
            "Generates a verification code. Send /link CODE on the other channel to complete."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["generate", "verify"],
                    "description": "generate = create link code, verify = verify a received code",
                },
                "code": {
                    "type": "string",
                    "description": "6-digit code to verify (for verify action)",
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_channel_info(self, channel: str, channel_id: str):
        self._channel = channel
        self._channel_id = channel_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        import random
        import time as _time

        action = arguments.get("action", "generate")

        if action == "generate":
            if not self._user_id:
                return "Error: You must be authenticated to generate a link code."

            code = str(random.randint(100000, 999999))
            with self._codes_lock:
                # Clean expired codes
                now = _time.time()
                expired = [c for c, v in self._pending_codes.items()
                           if float(v.get("expires", 0)) < now]
                for c in expired:
                    del self._pending_codes[c]

                self._pending_codes[code] = {
                    "user_id": self._user_id,
                    "channel": self._channel,
                    "channel_id": self._channel_id,
                    "expires": str(_time.time() + 300),  # 5 min expiry
                }

            return (
                f"Link code: {code}\n"
                f"Send '/link {code}' on the other channel within 5 minutes to link your accounts."
            )

        elif action == "verify":
            code = arguments.get("code", "")
            if not code:
                return "Error: code is required for verify"

            with self._codes_lock:
                entry = self._pending_codes.pop(code, None)

            if not entry:
                return "Invalid or expired link code."

            if float(entry.get("expires", 0)) < _time.time():
                return "Link code has expired."

            # Link the identity
            try:
                from core.identity_service import IdentityService
                ids = IdentityService.instance()

                original_user = entry["user_id"]
                # Link current channel to the original user
                if self._channel and self._channel_id:
                    ok = ids.link(original_user, self._channel, self._channel_id)
                    if not ok:
                        return "This channel ID is already linked to another user."
                    return f"Identity linked! User '{original_user}' is now connected on {self._channel}."
                else:
                    return "Error: No channel information available for linking."
            except Exception as e:
                return f"Error linking identity: {e}"

        return f"Unknown action: {action}"


# ── Configurable handlers (for agent_tools) ──────────────────────────


class ConfigurableToolHandler(ToolHandler):
    """Base for tools configured via agent_tools dict (not hardcoded)."""

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any]):
        self._name = tool_name
        self._description = tool_description
        self._parameters = tool_parameters or {
            "type": "object", "properties": {},
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return self._parameters


class HTTPToolHandler(ConfigurableToolHandler):
    """Tool that calls an external HTTP endpoint.

    Config example::

        {
            "type": "http",
            "endpoint": "http://localhost:8080/api/search",
            "method": "POST",
            "headers": {"Authorization": "Bearer xxx"},
            "timeout": 30,
            "description": "Search the web",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}
        }

    The tool POSTs arguments as JSON body and returns the response text.
    For GET, arguments are sent as query parameters.
    """

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any], endpoint: str,
                 method: str = "POST", headers: Optional[Dict[str, str]] = None,
                 timeout: int = 30):
        super().__init__(tool_name, tool_description, tool_parameters)
        self._endpoint = endpoint
        self._method = method.upper()
        self._headers = headers or {}
        self._timeout = timeout

    def execute(self, arguments: Dict[str, Any]) -> str:
        parsed = urlparse(self._endpoint)
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme or "https"

        try:
            if scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(
                    host, port, timeout=self._timeout, context=ctx)
            else:
                conn = http.client.HTTPConnection(
                    host, port, timeout=self._timeout)

            headers = {"User-Agent": "PawFlow-Agent/1.0",
                       "Content-Type": "application/json"}
            headers.update(self._headers)

            path = parsed.path or "/"

            if self._method == "GET":
                # Encode arguments as query params
                from urllib.parse import urlencode
                qs = urlencode(arguments)
                if qs:
                    sep = "&" if "?" in path else "?"
                    path = f"{path}{sep}{qs}"
                conn.request("GET", path, headers=headers)
            else:
                body = json.dumps(arguments).encode("utf-8")
                headers["Content-Length"] = str(len(body))
                conn.request(self._method, path, body=body, headers=headers)

            response = conn.getresponse()
            response_body = response.read().decode("utf-8", errors="replace")
            conn.close()

            if len(response_body) > 10000:
                response_body = response_body[:10000] + "\n... (truncated)"

            return f"HTTP {response.status}\n{response_body}"
        except Exception as e:
            return f"Error calling {self._endpoint}: {e}"


class TaskToolHandler(ConfigurableToolHandler):
    """Tool that executes a PawFlow task inline.

    Config example::

        {
            "type": "task",
            "task_type": "executeSql",
            "config": {"connection_id": "my_db"},
            "parameter_mapping": {"sql": "sql_query"},
            "description": "Run a SQL query",
            "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}}
        }

    parameter_mapping maps tool argument names → task config keys.
    The tool creates a FlowFile with arguments as JSON content,
    sets mapped config values, executes the task, and returns the output.
    """

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any], task_type: str,
                 task_config: Optional[Dict[str, Any]] = None,
                 parameter_mapping: Optional[Dict[str, str]] = None):
        super().__init__(tool_name, tool_description, tool_parameters)
        self._task_type = task_type
        self._task_config = task_config or {}
        self._parameter_mapping = parameter_mapping or {}

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core import TaskFactory, FlowFile

        try:
            task_class = TaskFactory.get(self._task_type)
        except Exception as e:
            return f"Error: unknown task type '{self._task_type}': {e}"

        # Build config: base config + mapped arguments
        config = dict(self._task_config)
        for arg_key, config_key in self._parameter_mapping.items():
            if arg_key in arguments:
                config[config_key] = arguments[arg_key]

        # If no mapping, pass all arguments as config keys
        if not self._parameter_mapping:
            config.update(arguments)

        try:
            task = task_class(config)
            ff = FlowFile(content=json.dumps(arguments).encode("utf-8"))
            results = task.execute(ff)
            if results:
                return results[0].get_content().decode("utf-8", errors="replace")
            return "Task executed (no output)"
        except Exception as e:
            return f"Error executing task '{self._task_type}': {e}"


class MCPToolHandler(ConfigurableToolHandler):
    """Tool that calls a tool on an MCP server (HTTP transport).

    Config example::

        {
            "type": "mcp",
            "server_url": "http://localhost:3001/mcp",
            "tool_name": "web_search",
            "headers": {"Authorization": "Bearer xxx"},
            "timeout": 30,
            "description": "Search the web via MCP",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}
        }

    Uses JSON-RPC over HTTP (MCP Streamable HTTP transport).
    Sends tools/call to the server and returns the text result.
    """

    def __init__(self, tool_name: str, tool_description: str,
                 tool_parameters: Dict[str, Any], server_url: str,
                 mcp_tool_name: Optional[str] = None,
                 headers: Optional[Dict[str, str]] = None,
                 timeout: int = 30):
        super().__init__(tool_name, tool_description, tool_parameters)
        self._server_url = server_url
        self._mcp_tool_name = mcp_tool_name or tool_name
        self._headers = headers or {}
        self._timeout = timeout

    def execute(self, arguments: Dict[str, Any]) -> str:
        import uuid as _uuid
        parsed = urlparse(self._server_url)
        host = parsed.hostname
        port = parsed.port
        scheme = parsed.scheme or "https"

        rpc_body = json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": self._mcp_tool_name,
                "arguments": arguments,
            },
            "id": str(_uuid.uuid4()),
        }).encode("utf-8")

        try:
            if scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(
                    host, port, timeout=self._timeout, context=ctx)
            else:
                conn = http.client.HTTPConnection(
                    host, port, timeout=self._timeout)

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Length": str(len(rpc_body)),
            }
            headers.update(self._headers)

            path = parsed.path or "/"
            conn.request("POST", path, body=rpc_body, headers=headers)
            response = conn.getresponse()
            body = response.read().decode("utf-8", errors="replace")
            conn.close()

            if response.status != 200:
                return f"MCP error (HTTP {response.status}): {body}"

            rpc_response = json.loads(body)
            if "error" in rpc_response:
                err = rpc_response["error"]
                return f"MCP error: {err.get('message', err)}"

            result = rpc_response.get("result", {})
            # MCP tools/call result has "content" array
            content_parts = result.get("content", [])
            texts = []
            for part in content_parts:
                if isinstance(part, dict):
                    texts.append(part.get("text", json.dumps(part)))
                else:
                    texts.append(str(part))
            return "\n".join(texts) if texts else json.dumps(result)

        except json.JSONDecodeError:
            return f"MCP error: invalid JSON response from {self._server_url}"
        except Exception as e:
            return f"Error calling MCP server {self._server_url}: {e}"


# ── MCP server discovery ─────────────────────────────────────────────
