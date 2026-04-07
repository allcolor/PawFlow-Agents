"""Tool Registry — dispatch system for agent tool execution.

Provides a registry of executable tool handlers that agents can invoke.
Each handler declares its name, description, JSON schema, and execute method.

Builtin handlers:
- execute_script: Run a Python snippet and return the result
- read_file: Read a local file's content
- fetch: Fetch a web page (text extraction or raw HTTP)

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
    DeleteToolHandler,
    DeletePlanHandler,
    ExecuteScriptHandler,
    FlowManagerHandler,
    ForgetHandler,
    GetAgentResultsHandler,
    GetToolSchemaHandler,
    HTTPToolHandler,
    ImageGenerationHandler,
    ImageModelInfoHandler,
    LinkIdentityHandler,
    ListSecretsHandler,
    MCPToolHandler,
    ManageResourceHandler,
    NotifyUserHandler,
    PawFlowHelpHandler,
    ReadParentContextHandler,
    RecallHandler,
    RememberHandler,
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
    UseToolHandler,
    VerifyPlanStepHandler,
    VerifyTaskHandler,
    VideoGenerationHandler,
    AudioGenerationHandler,
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

    _live_registry: Optional["ToolRegistry"] = None  # for dynamic tool access

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
            # Unwrap JSON string (MCP bridge double-encoding)
            if isinstance(args, str):
                try:
                    import json as _json_uw
                    args = _json_uw.loads(args)
                except (ValueError, TypeError):
                    pass
            # Normalize CC-native argument names to PawFlow names
            # (drop-in compat with Claude Code built-in tool signatures)
            if isinstance(args, dict):
                _CC_ALIASES = {
                    "file_path": "path",
                    "head_limit": "limit",
                    "include": "glob",
                }
                for _cc_name, _pf_name in _CC_ALIASES.items():
                    if _cc_name in args and _pf_name not in args:
                        args[_pf_name] = args.pop(_cc_name)
            # Validate: reject unknown arguments so the LLM learns
            if isinstance(args, dict) and hasattr(handler, 'parameters_schema'):
                _schema = handler.parameters_schema
                _known = set((_schema.get("properties") or {}).keys())
                if _known:
                    _unknown = [k for k in args if k not in _known and not k.startswith("_")]
                    if _unknown:
                        return (f"Error: unknown argument(s) {_unknown} for tool '{name}'. "
                                f"Valid arguments: {sorted(_known)}")
            # Execute
            result = handler.execute(args)
            # Run post-hooks (specific then wildcard)
            for hook in self._hooks.get(f"post:{name}", []) + self._hooks.get("post:*", []):
                result = hook(name, args, result)
            # Safety cap: prevent oversized results from crashing any caller
            # Skip cap for multimodal image markers — they are converted to
            # image content blocks by the agent loop, not sent as text.
            _max = getattr(handler, '_tool_result_max_chars', 50000)
            _has_image = isinstance(result, str) and "__image_data__:" in result
            if isinstance(result, str) and len(result) > _max and not _has_image:
                try:
                    from core.file_store import FileStore
                    _fid = FileStore.instance().store(
                        f"tool_result_{name}.txt",
                        result.encode("utf-8"), "text/plain",
                        category="tool_result",
                    )
                    _first = result.split("\n", 1)[0][:200]
                    result = (
                        result[:_max]
                        + f"\n\n{_first}\n"
                        f"[Result cleared — {len(result):,} chars. "
                        f"Full output: read(path=\"{_fid}\", source=\"filestore\")]"
                    )
                except Exception:
                    result = result[:_max] + f"\n\n[... truncated — {len(result):,} chars total]"
            return result
        except Exception as e:
            logger.error(f"Tool '{name}' execution failed: {e}")
            return f"Error executing tool '{name}': {e}"



# ── Builtin handlers ──────────────────────────────────────────────────



def create_default_registry() -> ToolRegistry:
    """Create a ToolRegistry with all builtin handlers registered."""
    registry = ToolRegistry()
    registry.register(ExecuteScriptHandler())
    registry.register(WebSearchHandler())
    registry.register(ScraplingFetchHandler())
    registry.register(CreateFileHandler())
    registry.register(ScheduleContinuationHandler())
    registry.register(ScheduleRecheckHandler())
    registry.register(ImageGenerationHandler())
    registry.register(ImageModelInfoHandler())
    registry.register(VideoGenerationHandler())
    registry.register(AudioGenerationHandler())
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
    registry.register(VerifyPlanStepHandler())
    registry.register(NotifyUserHandler())
    registry.register(AskUserHandler())
    registry.register(CreateToolHandler())
    registry.register(FlowManagerHandler())
    registry.register(PawFlowHelpHandler())
    registry.register(StoreSecretHandler())
    registry.register(ListSecretsHandler())
    registry.register(ManageResourceHandler())
    registry.register(SpawnAgentsHandler())
    registry.register(GetAgentResultsHandler())
    registry.register(ShowFileHandler())
    registry.register(ReadParentContextHandler())

    # History browsing (read old messages outside current context)
    from core.handlers.history import ReadHistoryHandler
    registry.register(ReadHistoryHandler())

    # Compact result (receives summary from Claude Code during compaction)
    from core.handlers.compact_result import CompactResultHandler
    registry.register(CompactResultHandler())

    # Screen interaction (screenshots, clicks — requires relay with exec)
    from core.handlers.screen import ScreenHandler
    registry.register(ScreenHandler())

    # Browser automation (conditional — requires playwright)
    try:
        from services.browser_service import BrowserService  # noqa: F401
        registry.register(BrowserActionHandler())
    except ImportError:
        pass

    # Dynamic tools (create/delete at runtime)
    registry.register(DeleteToolHandler())

    # Set live registry for dynamic tool access
    ToolRegistry._live_registry = registry

    # Identity linking
    registry.register(LinkIdentityHandler())

    # Filesystem tools
    from core.handlers.read import ReadHandler
    from core.handlers.write import WriteHandler
    from core.handlers.edit_handler import EditHandler
    from core.handlers.batch_edit import BatchEditHandler
    from core.handlers.apply_patch import ApplyPatchHandler
    from core.handlers.find_replace import FindReplaceHandler
    from core.handlers.delete import DeleteHandler
    from core.handlers.mkdir import MkdirHandler
    from core.handlers.stat import StatHandler
    from core.handlers.exists import ExistsHandler
    from core.handlers.list_dir import ListDirHandler
    from core.handlers.glob_handler import GlobHandler
    from core.handlers.grep_handler import GrepHandler
    from core.handlers.bash import BashHandler
    from core.handlers.notebook import NotebookEditHandler
    from core.handlers.copy import CopyHandler
    from core.handlers.see import SeeHandler
    for _h_cls in (ReadHandler, WriteHandler, EditHandler, BatchEditHandler,
                   ApplyPatchHandler, FindReplaceHandler, DeleteHandler,
                   MkdirHandler, StatHandler, ExistsHandler, ListDirHandler,
                   GlobHandler, GrepHandler, BashHandler, NotebookEditHandler,
                   CopyHandler, SeeHandler):
        registry.register(_h_cls())

    # Test runner
    registry.register(RunTestsHandler())

    # Security scanning
    registry.register(SecurityScanHandler())

    # Meta-tools (lazy tool loading for API providers)
    registry.register(GetToolSchemaHandler(registry))
    registry.register(UseToolHandler(registry))

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
    - builtin: Reference to a builtin handler (execute_script, read_file, fetch)
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

