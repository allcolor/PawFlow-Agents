"""Tool Registry — dispatch system for agent tool execution.

Provides a registry of executable tool handlers that agents can invoke.
Each handler declares its name, description, JSON schema, and execute method.

Builtin handlers:
- execute_script: Run a Python snippet and return the result
- read_file: Read a local file's content
- scrape_url: Fetch a web page using Scrapling

Agent tool types (flow-level agent_tools section):
- builtin: Reference to a builtin handler
- http: Call an external HTTP endpoint
- task: Execute a PawFlow task inline
- mcp: Call a tool on an MCP server (HTTP transport)
"""

import json
import logging
import http.client
import ssl
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

# ToolHandler base class — in separate module to avoid circular imports
from core.tool_handler import ToolHandler  # noqa: F401

logger = logging.getLogger(__name__)

# Handler classes moved to core/handlers/ — re-exported for compatibility
from core.handlers import (  # noqa: F401
    ApprovePlanHandler,
    AskAgentHandler,
    AskUserHandler,
    AssignPlanHandler,
    AssignTaskHandler,
    BrowserActionHandler,
    CancelPlanHandler,
    CompleteTaskHandler,
    ConfigurableToolHandler,
    CreateFileHandler,
    CreatePlanHandler,
    CreateToolHandler,
    DeletePlanHandler,
    ExecuteScriptHandler,
    FilesystemToolHandler,
    FlowManagerHandler,
    ForgetHandler,
    GetAgentResultsHandler,
    GitHubHandler,
    HTTPToolHandler,
    ImageGenerationHandler,
    ImageModelInfoHandler,
    LinkIdentityHandler,
    ListSecretsHandler,
    LocalFilesHandler,
    MCPToolHandler,
    ManageResourceHandler,
    NotifyUserHandler,
    PawFlowHelpHandler,
    ReadFileHandler,
    ReadParentContextHandler,
    RecallHandler,
    RememberHandler,
    RemoteExecutorHandler,
    RunTestsHandler,
    ScheduleContinuationHandler,
    ScheduleRecheckHandler,
    ScraplingFetchHandler,
    SecurityScanHandler,
    SemanticRecallHandler,
    ShowFileHandler,
    SpawnAgentsHandler,
    StoreSecretHandler,
    TaskToolHandler,
    UpdatePlanHandler,
    UseSkillHandler,
    VerifyTaskHandler,
    VideoGenerationHandler,
    WebFetchHandler,
    WebSearchHandler,
)


def _append_task_log(conversation_id: str, task_id: str, entry: dict):
    """Append an entry to the persistent task timeline log (standalone helper)."""
    import time
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    key = f"task_log:{task_id}"
    log = store.get_extra(conversation_id, key) or []
    entry["ts"] = time.time()
    log.append(entry)
    if len(log) > 500:
        log = log[-500:]
    store.set_extra(conversation_id, key, log)



class ToolRegistry:
    """Registry of available tool handlers."""

    def __init__(self):
        self._handlers: Dict[str, ToolHandler] = {}
        self._hooks: Dict[str, List] = {}  # "pre:tool_name" or "post:tool_name" or "pre:*" / "post:*"

    def register_hook(self, event: str, callback):
        """Register a pre/post hook. Event format: 'pre:tool_name', 'post:tool_name', 'pre:*', 'post:*'."""
        self._hooks.setdefault(event, []).append(callback)

    def unregister_hook(self, event: str, callback):
        """Remove a hook callback."""
        if event in self._hooks:
            try:
                self._hooks[event].remove(callback)
            except ValueError:
                pass

    def register(self, handler: ToolHandler):
        """Register a tool handler."""
        self._handlers[handler.name] = handler

    def unregister(self, name: str):
        """Remove a tool handler."""
        self._handlers.pop(name, None)

    def get(self, name: str) -> Optional[ToolHandler]:
        """Get a handler by name."""
        return self._handlers.get(name)

    def list_tools(self) -> List[ToolHandler]:
        """List all registered handlers."""
        return list(self._handlers.values())

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions in a format suitable for LLMToolDefinition."""
        return [
            {
                "name": h.name,
                "description": h.description,
                "parameters": h.parameters_schema,
            }
            for h in self._handlers.values()
        ]

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool by name. Returns result text or error."""
        handler = self._handlers.get(name)
        if not handler:
            return f"Error: unknown tool '{name}'"
        try:
            # Run pre-hooks (specific then wildcard)
            args = arguments
            for hook in self._hooks.get(f"pre:{name}", []) + self._hooks.get("pre:*", []):
                result = hook(name, args)
                if result is None:
                    return f"Error: tool '{name}' blocked by pre-hook"
                args = result
            # Execute
            result = handler.execute(args)
            # Run post-hooks (specific then wildcard)
            for hook in self._hooks.get(f"post:{name}", []) + self._hooks.get("post:*", []):
                result = hook(name, args, result)
            return result
        except Exception as e:
            logger.error(f"Tool '{name}' execution failed: {e}")
            return f"Error executing tool '{name}': {e}"



class GetToolSchemaHandler(ToolHandler):
    """Return the full JSON schema of a tool so the LLM can call it via use_tool."""

    def __init__(self, registry: "ToolRegistry"):
        self._registry = registry

    @property
    def name(self) -> str:
        return "get_tool_schema"

    @property
    def description(self) -> str:
        return "Get the full parameter schema for a tool. Call this BEFORE use_tool to know the required arguments."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool to inspect"},
            },
            "required": ["tool_name"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        name = arguments.get("tool_name", "")
        handler = self._registry.get(name)
        if not handler:
            available = [h.name for h in self._registry.list_tools()
                         if h.name not in ("get_tool_schema", "use_tool")]
            return json.dumps({"error": f"Unknown tool '{name}'",
                               "available": available})
        return json.dumps({
            "name": handler.name,
            "description": handler.description,
            "parameters": handler.parameters_schema,
        }, indent=2)



class UseToolHandler(ToolHandler):
    """Execute any tool by name. The LLM should call get_tool_schema first."""

    def __init__(self, registry: "ToolRegistry"):
        self._registry = registry

    @property
    def name(self) -> str:
        return "use_tool"

    @property
    def description(self) -> str:
        return "Execute a tool by name with the given arguments. Call get_tool_schema first to know the parameters."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "Name of the tool to execute"},
                "arguments": {"type": "object", "description": "Arguments to pass to the tool"},
            },
            "required": ["tool_name", "arguments"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        tool_name = arguments.get("tool_name", "")
        tool_args = arguments.get("arguments", {})
        # LLM sometimes sends arguments as JSON string instead of dict
        # (can be double-encoded — keep parsing until we get a dict)
        for _ in range(3):  # max 3 levels of JSON encoding
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except (json.JSONDecodeError, TypeError):
                    return f"Error: invalid arguments format for '{tool_name}' — expected JSON object, got string: {tool_args[:200]}"
            else:
                break
        if not isinstance(tool_args, dict):
            return f"Error: arguments for '{tool_name}' must be a JSON object, got {type(tool_args).__name__}"
        if tool_name in ("get_tool_schema", "use_tool"):
            return (f"Error: '{tool_name}' is a meta-tool — call it directly "
                    f"as a top-level tool call, not via use_tool.")
        # Validate arguments against tool schema
        handler = self._registry.get(tool_name)
        if handler:
            schema = handler.parameters_schema or {}
            props = schema.get("properties", {})
            if props and isinstance(tool_args, dict):
                unknown = [k for k in tool_args if k not in props]
                if unknown:
                    valid = list(props.keys())
                    return (f"Error: unknown argument(s) {unknown} for tool '{tool_name}'. "
                            f"Valid arguments: {valid}. "
                            f"Use get_tool_schema(tool_name='{tool_name}') to see full schema.")
        return self._registry.execute(tool_name, tool_args)


# ── Builtin handlers ──────────────────────────────────────────────────



def create_default_registry() -> ToolRegistry:
    """Create a ToolRegistry with all builtin handlers registered."""
    registry = ToolRegistry()
    registry.register(ExecuteScriptHandler())
    registry.register(WebSearchHandler())
    registry.register(WebFetchHandler())
    registry.register(ScraplingFetchHandler())
    registry.register(ReadFileHandler())
    registry.register(CreateFileHandler())
    registry.register(ScheduleContinuationHandler())
    registry.register(ScheduleRecheckHandler())
    registry.register(LocalFilesHandler())
    registry.register(RemoteExecutorHandler())
    registry.register(ImageGenerationHandler())
    registry.register(ImageModelInfoHandler())
    registry.register(VideoGenerationHandler())
    registry.register(RememberHandler())
    registry.register(RecallHandler())
    registry.register(SemanticRecallHandler())
    registry.register(AssignTaskHandler())
    registry.register(CompleteTaskHandler())
    registry.register(VerifyTaskHandler())
    registry.register(ForgetHandler())
    registry.register(CreatePlanHandler())
    registry.register(UpdatePlanHandler())
    registry.register(ApprovePlanHandler())
    registry.register(AssignPlanHandler())
    registry.register(CancelPlanHandler())
    registry.register(DeletePlanHandler())
    registry.register(NotifyUserHandler())
    registry.register(AskUserHandler())
    registry.register(CreateToolHandler())
    registry.register(AskAgentHandler())
    registry.register(FlowManagerHandler())
    registry.register(PawFlowHelpHandler())
    registry.register(StoreSecretHandler())
    registry.register(ListSecretsHandler())
    registry.register(ManageResourceHandler())
    registry.register(SpawnAgentsHandler())
    registry.register(GetAgentResultsHandler())
    registry.register(UseSkillHandler())
    registry.register(ShowFileHandler())
    registry.register(ReadParentContextHandler())

    # History browsing (read old messages outside current context)
    from core.handlers.history import ReadHistoryHandler
    registry.register(ReadHistoryHandler())

    # Screen interaction (screenshots, clicks — requires relay with exec)
    from core.handlers.screen import ScreenHandler
    registry.register(ScreenHandler())

    # Browser automation (conditional — requires playwright)
    try:
        from services.browser_service import BrowserService  # noqa: F401
        registry.register(BrowserActionHandler())
    except ImportError:
        pass

    # Identity linking
    registry.register(LinkIdentityHandler())

    # Filesystem
    registry.register(FilesystemToolHandler())

    # Test runner
    registry.register(RunTestsHandler())

    # GitHub CLI
    registry.register(GitHubHandler())

    # Security scanning
    registry.register(SecurityScanHandler())

    return registry



def discover_mcp_tools(server_url: str,
                       headers: Optional[Dict[str, str]] = None,
                       timeout: int = 10) -> List[Dict[str, Any]]:
    """Discover available tools from an MCP server via tools/list.

    Returns a list of dicts: [{"name": ..., "description": ..., "inputSchema": ...}]
    """
    import uuid as _uuid
    parsed = urlparse(server_url)
    host = parsed.hostname
    port = parsed.port
    scheme = parsed.scheme or "https"

    rpc_body = json.dumps({
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": str(_uuid.uuid4()),
    }).encode("utf-8")

    try:
        if scheme == "https":
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(
                host, port, timeout=timeout)

        req_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Content-Length": str(len(rpc_body)),
        }
        if headers:
            req_headers.update(headers)

        path = parsed.path or "/"
        conn.request("POST", path, body=rpc_body, headers=req_headers)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        conn.close()

        if response.status != 200:
            logger.error(f"MCP tools/list failed (HTTP {response.status}): {body}")
            return []

        rpc_response = json.loads(body)
        if "error" in rpc_response:
            logger.error(f"MCP tools/list error: {rpc_response['error']}")
            return []

        return rpc_response.get("result", {}).get("tools", [])

    except Exception as e:
        logger.error(f"MCP discovery failed for {server_url}: {e}")
        return []


# ── Agent tools loader ───────────────────────────────────────────────



def load_agent_tools(agent_tools_config: Dict[str, Any]) -> ToolRegistry:
    """Build a ToolRegistry from a flow-level agent_tools configuration.

    Supports four tool types:
    - builtin: Reference to a builtin handler (execute_script, read_file, scrape_url)
    - http: Call an external HTTP endpoint
    - task: Execute a PawFlow task inline
    - mcp: Call a tool on an MCP server (single tool)

    Plus a special "mcp_server" entry that auto-discovers all tools::

        "agent_tools": {
            "_mcp_server": {
                "type": "mcp_server",
                "server_url": "http://localhost:3001/mcp",
                "headers": {}
            },
            "calculator": {"type": "builtin", "handler": "execute_script"},
            "search_api": {"type": "http", "endpoint": "...", ...}
        }
    """
    registry = ToolRegistry()
    default_builtins = None  # lazy

    for tool_name, tool_def in agent_tools_config.items():
        tool_type = tool_def.get("type", "http")
        handler = None

        if tool_type == "builtin":
            # Reference to a builtin handler
            handler_name = tool_def.get("handler", tool_name)
            if default_builtins is None:
                default_builtins = create_default_registry()
            builtin = default_builtins.get(handler_name)
            if builtin:
                handler = builtin
            else:
                logger.warning(f"agent_tools: unknown builtin '{handler_name}'")

        elif tool_type == "http":
            endpoint = tool_def.get("endpoint", "")
            if not endpoint:
                logger.warning(f"agent_tools: '{tool_name}' has no endpoint")
                continue
            handler = HTTPToolHandler(
                tool_name=tool_name,
                tool_description=tool_def.get("description", f"HTTP tool: {tool_name}"),
                tool_parameters=tool_def.get("parameters", {
                    "type": "object", "properties": {},
                }),
                endpoint=endpoint,
                method=tool_def.get("method", "POST"),
                headers=tool_def.get("headers"),
                timeout=int(tool_def.get("timeout", 30)),
            )

        elif tool_type == "task":
            task_type = tool_def.get("task_type", "")
            if not task_type:
                logger.warning(f"agent_tools: '{tool_name}' has no task_type")
                continue
            handler = TaskToolHandler(
                tool_name=tool_name,
                tool_description=tool_def.get("description", f"PawFlow task: {task_type}"),
                tool_parameters=tool_def.get("parameters", {
                    "type": "object", "properties": {},
                }),
                task_type=task_type,
                task_config=tool_def.get("config", {}),
                parameter_mapping=tool_def.get("parameter_mapping", {}),
            )

        elif tool_type == "mcp":
            server_url = tool_def.get("server_url", "")
            if not server_url:
                logger.warning(f"agent_tools: '{tool_name}' has no server_url")
                continue
            handler = MCPToolHandler(
                tool_name=tool_name,
                tool_description=tool_def.get("description", f"MCP tool: {tool_name}"),
                tool_parameters=tool_def.get("parameters", {
                    "type": "object", "properties": {},
                }),
                server_url=server_url,
                mcp_tool_name=tool_def.get("tool_name", tool_name),
                headers=tool_def.get("headers"),
                timeout=int(tool_def.get("timeout", 30)),
            )

        elif tool_type == "mcp_server":
            # Auto-discover all tools from an MCP server
            server_url = tool_def.get("server_url", "")
            if not server_url:
                logger.warning(f"agent_tools: '{tool_name}' has no server_url")
                continue
            mcp_headers = tool_def.get("headers", {})
            mcp_timeout = int(tool_def.get("timeout", 30))
            discovered = discover_mcp_tools(
                server_url, headers=mcp_headers, timeout=10)
            for mcp_tool in discovered:
                mcp_name = mcp_tool.get("name", "")
                if not mcp_name:
                    continue
                h = MCPToolHandler(
                    tool_name=mcp_name,
                    tool_description=mcp_tool.get("description", ""),
                    tool_parameters=mcp_tool.get("inputSchema", {
                        "type": "object", "properties": {},
                    }),
                    server_url=server_url,
                    mcp_tool_name=mcp_name,
                    headers=mcp_headers,
                    timeout=mcp_timeout,
                )
                registry.register(h)
                logger.info(f"agent_tools: discovered MCP tool '{mcp_name}' "
                           f"from {server_url}")
            continue  # skip the register below

        elif tool_type == "mcp_stdio":
            # MCP server via stdio (relay proxy)
            # Config: {type: "mcp_stdio", command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem"],
            #          env: {"KEY": "val"}, server_id: "fs-mcp"}
            mcp_command = tool_def.get("command", "")
            mcp_args = tool_def.get("args", [])
            mcp_env = tool_def.get("env", {})
            mcp_server_id = tool_def.get("server_id", tool_name)
            if not mcp_command:
                logger.warning(f"agent_tools: '{tool_name}' has no command for mcp_stdio")
                continue
            # The relay_service will be injected later by _configure_tool_handlers
            # Store config for deferred initialization
            handler = MCPToolHandler(
                tool_name=f"mcp_stdio_{mcp_server_id}",
                tool_description=f"MCP stdio server '{mcp_server_id}' (pending discovery)",
                tool_parameters={"type": "object", "properties": {}},
                transport="stdio",
                server_id=mcp_server_id,
            )
            handler._mcp_stdio_config = {
                "command": mcp_command, "args": mcp_args,
                "env": mcp_env, "server_id": mcp_server_id,
            }
            # Don't register this placeholder — discovery happens at runtime
            continue

        else:
            logger.warning(f"agent_tools: unknown type '{tool_type}' "
                          f"for tool '{tool_name}'")
            continue

        if handler:
            # Attach allowed_roles for per-user tool filtering
            allowed_roles = tool_def.get("allowed_roles")
            if allowed_roles is not None:
                handler.allowed_roles = allowed_roles
            registry.register(handler)
            logger.info(f"agent_tools: loaded {tool_type} tool '{tool_name}'")

    return registry

