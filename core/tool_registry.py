"""Tool Registry — dispatch system for agent tool execution.

Provides a registry of executable tool handlers that agents can invoke.
Each handler declares its name, description, JSON schema, and execute method.

Builtin handlers:
- execute_script: Run a Python snippet and return the result
- read_file: Read a local file's content
- fetch: Fetch a web page (text extraction or raw HTTP)

Agent tool types:
- builtin: Reference to a builtin handler
- http: Call an external HTTP endpoint
- task: Execute a PawFlow task inline
- mcp: Call a tool on an MCP server (HTTP transport)
"""

import json
import logging
import http.client
import ssl
import threading
import time
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

# ToolHandler base class — in separate module to avoid circular imports
from core.tool_handler import ToolHandler  # noqa: F401

logger = logging.getLogger(__name__)

# Handler classes live in per-feature submodules under core/handlers/.
# Imported here so callers can resolve them off the registry module.
from core.handlers.agent_tools import (  # noqa: F401
    BrowserActionHandler,
    ConfigurableToolHandler,
    HTTPToolHandler,
    LinkIdentityHandler,
    MCPToolHandler,
    TaskToolHandler,
)
from core.handlers.capabilities import (  # noqa: F401
    CloneVoiceHandler,
    DeleteVoiceHandler,
    DescribeImageHandler,
    Generate3DHandler,
    LipsyncHandler,
    RemixImageHandler,
    RemoveBackgroundHandler,
    SpeakHandler,
    SpeechToVideoHandler,
    TrainImageModelHandler,
    TryOnHandler,
    UpscaleImageHandler,
    UpscaleVideoHandler,
)
from core.handlers.devops import (  # noqa: F401
    ReadParentContextHandler,
    RunTestsHandler,
    SecurityScanHandler,
)
from core.handlers.dynamic_tool import (  # noqa: F401
    CreateToolHandler,
    DeleteToolHandler,
)
from core.handlers.file_ops import (  # noqa: F401
    CreateFileHandler,
    ScheduleContinuationHandler,
    ScheduleWakeupHandler,
)
from core.handlers.flow_management import FlowManagerHandler  # noqa: F401
from core.handlers.help_secrets import (  # noqa: F401
    ListSecretsHandler,
    PawFlowHelpHandler,
    StoreSecretHandler,
)
from core.handlers.media import (  # noqa: F401
    AudioGenerationHandler,
    EditImageHandler,
    ImageGenerationHandler,
    ImageModelInfoHandler,
    VideoGenerationHandler,
)
from core.handlers.memory import (  # noqa: F401
    ForgetHandler,
    RecallHandler,
    RememberHandler,
    SemanticRecallHandler,
)
from core.handlers.meta_tools import (  # noqa: F401
    GetToolSchemaHandler,
    UseToolHandler,
)
from core.handlers.plan_handlers import (  # noqa: F401
    ApprovePlanHandler,
    AssignPlanHandler,
    CancelPlanHandler,
    CreatePlanHandler,
    DeletePlanHandler,
    UpdatePlanHandler,
    VerifyPlanStepHandler,
)
from core.handlers.resource_agent import (  # noqa: F401
    FlashAgentHandler,
    ManageResourceHandler,
    ShowFileHandler,
    SpawnAgentsHandler,
)
from core.handlers.task_management import (  # noqa: F401
    AssignTaskHandler,
    CompleteTaskHandler,
    LinkTaskHandler,
    VerifyTaskHandler,
)
from core.handlers.user_interaction import (  # noqa: F401
    AskUserHandler,
    NotifyUserHandler,
)
from core.handlers.web_fetch import (  # noqa: F401
    ExecuteScriptHandler,
    ScraplingFetchHandler,
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
    _metrics_lock = threading.Lock()
    _metrics: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        self._handlers: Dict[str, ToolHandler] = {}
        self._hooks: Dict[str, List] = {}  # "pre:tool_name" or "post:tool_name" or "pre:*" / "post:*"

    @classmethod
    def reset_metrics(cls):
        with cls._metrics_lock:
            cls._metrics.clear()

    @classmethod
    def _record_metric(cls, name: str, ok: bool, duration_ms: float,
                       error: str = ""):
        tool_name = name or "(missing)"
        now = time.time()
        with cls._metrics_lock:
            m = cls._metrics.setdefault(tool_name, {
                "calls": 0, "successes": 0, "errors": 0,
                "total_duration_ms": 0.0, "avg_duration_ms": 0.0,
                "min_duration_ms": 0.0, "max_duration_ms": 0.0,
                "last_duration_ms": 0.0, "last_at": 0.0,
                "last_ok": False, "last_error": "",
            })
            m["calls"] += 1
            m["successes" if ok else "errors"] += 1
            m["total_duration_ms"] += duration_ms
            m["avg_duration_ms"] = m["total_duration_ms"] / max(1, m["calls"])
            m["min_duration_ms"] = (
                duration_ms if m["calls"] == 1
                else min(m["min_duration_ms"], duration_ms)
            )
            m["max_duration_ms"] = max(m["max_duration_ms"], duration_ms)
            m["last_duration_ms"] = duration_ms
            m["last_at"] = now
            m["last_ok"] = ok
            m["last_error"] = "" if ok else str(error or "")[:500]

    @classmethod
    def get_metrics(cls) -> Dict[str, Dict[str, Any]]:
        with cls._metrics_lock:
            return {name: dict(stats) for name, stats in cls._metrics.items()}

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
        from core.handlers.meta_tools import _schema_with_local
        return [
            {
                "name": h.name,
                "description": h.description,
                "parameters": _schema_with_local(h),
            }
            for h in self._handlers.values()
        ]

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool by name. Returns result text or error."""
        start = time.time()
        ok = False
        metric_error = ""

        def _fail(message: str) -> str:
            nonlocal metric_error
            metric_error = message
            return message

        try:
            handler = self._handlers.get(name)
            if not handler:
                return _fail(f"Error: unknown tool '{name}'")
            # Run pre-hooks (specific then wildcard)
            args = arguments
            for hook in self._hooks.get(f"pre:{name}", []) + self._hooks.get("pre:*", []):
                result = hook(name, args)
                if result is None:
                    return _fail(f"Error: tool '{name}' blocked by pre-hook")
                args = result
            # Unwrap JSON string (MCP bridge double-encoding)
            if isinstance(args, str):
                from core.tool_json import parse_tool_arguments
                args = parse_tool_arguments(args, tool_name=name,
                                            provider="tool_registry",
                                            log=logger)
            from core.tool_json import tool_argument_parse_error
            _parse_error = tool_argument_parse_error(args)
            if _parse_error:
                return _fail(_parse_error)
            # Normalize CC-native argument names to PawFlow names
            # (drop-in compat with Claude Code built-in tool signatures)
            if isinstance(args, dict):
                _CC_ALIASES = {
                    "file_path": "path",      # Read, Write, Edit
                    "include": "glob",        # Grep
                    "notebook_path": "path",  # NotebookEdit
                    "edit_mode": "operation",  # NotebookEdit
                    "filesystem": "source",   # Read, Grep, Glob → "source" param
                }
                # Only apply alias if the handler's schema does NOT already
                # have the CC name as a known property (e.g. edit has "filesystem"
                # natively — don't rename it to "source")
                _schema_props = set()
                if hasattr(handler, 'parameters_schema'):
                    _schema_props = set((handler.parameters_schema.get("properties") or {}).keys())
                for _cc_name, _pf_name in _CC_ALIASES.items():
                    if _cc_name in args and _pf_name not in args:
                        if _cc_name not in _schema_props:
                            args[_pf_name] = args.pop(_cc_name)
                try:
                    from core.handlers.meta_tools import _normalize_tool_args, _schema_with_local
                    args = _normalize_tool_args(name, args, _schema_with_local(handler))
                except Exception:
                    pass
            if name == "use_tool" and isinstance(args, dict) and "arguments" in args and "arguments_json" not in args:
                args = dict(args)
                raw_use_tool_args = args.pop("arguments")
                args["arguments_json"] = (
                    raw_use_tool_args if isinstance(raw_use_tool_args, str)
                    else json.dumps(raw_use_tool_args)
                )
            # Validate: reject unknown arguments so the LLM learns
            if isinstance(args, dict) and hasattr(handler, 'parameters_schema'):
                try:
                    from core.handlers.meta_tools import _schema_with_local
                    _schema = _schema_with_local(handler)
                except Exception:
                    _schema = handler.parameters_schema
                from core.tool_json import missing_required_arguments
                _known = set((_schema.get("properties") or {}).keys())
                if _known:
                    _unknown = [k for k in args if k not in _known and not k.startswith("_")]
                    if _unknown:
                        return _fail(
                            f"Error: unknown argument(s) {_unknown} for tool '{name}'. "
                            f"Valid arguments: {sorted(_known)}")
                _missing = missing_required_arguments(_schema, args)
                if _missing:
                    return _fail(
                        f"Error: missing required argument(s) {_missing} for tool '{name}'. "
                        f"Valid arguments: {sorted(_known)}")
            # Execute
            result = handler.execute(args)
            # Run post-hooks (specific then wildcard)
            for hook in self._hooks.get(f"post:{name}", []) + self._hooks.get("post:*", []):
                result = hook(name, args, result)
            # Safety cap: prevent oversized results from crashing any caller.
            # Skip cap only for handlers that declare _returns_images=True AND
            # actually emit a marker — otherwise a grep match on the literal
            # "__image_data__:" string would wrongly bypass the cap.
            _max = getattr(handler, '_tool_result_max_chars', 50000)
            _has_image = (getattr(handler, '_returns_images', False)
                          and isinstance(result, str)
                          and "__image_data__:" in result)
            if isinstance(result, str) and len(result) > _max and not _has_image:
                try:
                    from core.file_store import FileStore
                    _uid = getattr(handler, '_user_id', '') or ''
                    _cid = getattr(handler, '_conversation_id', '') or ''
                    _fid = FileStore.instance().store(
                        f"tool_result_{name}.txt",
                        result.encode("utf-8"), "text/plain",
                        category="tool_result",
                        user_id=_uid,
                        conversation_id=_cid,
                        ttl=4 * 3600,  # 4h safety net, cleaned at compaction
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
            if isinstance(result, str) and result.startswith("Error:"):
                metric_error = result
            else:
                ok = True
            return result
        except Exception as e:
            metric_error = f"Error executing tool '{name}': {e}"
            logger.error(f"Tool '{name}' execution failed: {e}")
            return metric_error
        finally:
            self._record_metric(
                name, ok, (time.time() - start) * 1000,
                error=metric_error,
            )



# ── Builtin handlers ──────────────────────────────────────────────────



def create_default_registry() -> ToolRegistry:
    """Create a ToolRegistry with all builtin handlers registered."""
    registry = ToolRegistry()
    registry.register(ExecuteScriptHandler())
    registry.register(WebSearchHandler())
    registry.register(ScraplingFetchHandler())
    registry.register(CreateFileHandler())
    registry.register(ScheduleContinuationHandler())
    registry.register(ScheduleWakeupHandler())
    from core.handlers.push_notification import PushNotificationHandler
    registry.register(PushNotificationHandler())
    registry.register(ImageGenerationHandler())
    registry.register(EditImageHandler())
    registry.register(ImageModelInfoHandler())
    registry.register(VideoGenerationHandler())
    registry.register(AudioGenerationHandler())
    registry.register(Generate3DHandler())
    registry.register(UpscaleImageHandler())
    registry.register(UpscaleVideoHandler())
    registry.register(DescribeImageHandler())
    registry.register(RemixImageHandler())
    registry.register(RemoveBackgroundHandler())
    registry.register(TryOnHandler())
    registry.register(LipsyncHandler())
    registry.register(TrainImageModelHandler())
    registry.register(SpeechToVideoHandler())
    registry.register(CloneVoiceHandler())
    registry.register(SpeakHandler())
    registry.register(DeleteVoiceHandler())
    registry.register(RememberHandler())
    registry.register(RecallHandler())
    registry.register(SemanticRecallHandler())
    registry.register(LinkTaskHandler())
    registry.register(AssignTaskHandler())
    registry.register(CompleteTaskHandler())
    registry.register(VerifyTaskHandler())
    registry.register(ForgetHandler())
    from core.handlers.memory import CheckDuplicateHandler
    registry.register(CheckDuplicateHandler())
    from core.handlers.diary import DiaryWriteHandler, DiaryReadHandler
    registry.register(DiaryWriteHandler())
    registry.register(DiaryReadHandler())
    from core.handlers.learn import LearnHandler
    registry.register(LearnHandler())
    from core.handlers.project_graph import ProjectGraphHandler
    registry.register(ProjectGraphHandler())

    # Knowledge Graph handlers
    from core.handlers.knowledge_graph import (
        KgAddHandler, KgQueryHandler, KgInvalidateHandler,
        KgTimelineHandler, KgStatsHandler,
        QueryGraphHandler, KgGodNodesHandler,
    )
    for _kg_cls in (KgAddHandler, KgQueryHandler, KgInvalidateHandler,
                    KgTimelineHandler, KgStatsHandler,
                    QueryGraphHandler, KgGodNodesHandler):
        registry.register(_kg_cls())

    # Memory navigation

    registry.register(CreatePlanHandler())
    registry.register(UpdatePlanHandler())
    registry.register(ApprovePlanHandler())
    registry.register(AssignPlanHandler())
    registry.register(CancelPlanHandler())
    registry.register(DeletePlanHandler())
    registry.register(VerifyPlanStepHandler())
    from core.handlers.plan_mode import EnterPlanModeHandler, ExitPlanModeHandler
    registry.register(EnterPlanModeHandler())
    registry.register(ExitPlanModeHandler())
    registry.register(NotifyUserHandler())
    registry.register(AskUserHandler())
    registry.register(CreateToolHandler())
    registry.register(FlowManagerHandler())
    registry.register(PawFlowHelpHandler())
    registry.register(StoreSecretHandler())
    registry.register(ListSecretsHandler())
    registry.register(ManageResourceHandler())
    registry.register(SpawnAgentsHandler())
    registry.register(FlashAgentHandler())
    registry.register(ShowFileHandler())
    registry.register(ReadParentContextHandler())

    # History browsing (read old messages outside current context)
    from core.handlers.history import ReadHistoryHandler
    registry.register(ReadHistoryHandler())

    # Compact result (receives summary from Claude Code during compaction)
    from core.handlers.compact_result import CompactResultHandler
    registry.register(CompactResultHandler())

    # Monitor — pawflow replacement for the CC built-in. Runs a relay
    # bash command and returns early on regex match / line cap / timeout.
    from core.handlers.monitor import MonitorHandler
    registry.register(MonitorHandler())

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
    from core.handlers.search import SearchHandler
    from core.handlers.bash import BashHandler
    from core.handlers.notebook import NotebookEditHandler
    from core.handlers.copy import CopyHandler
    from core.handlers.see import SeeHandler
    for _h_cls in (ReadHandler, WriteHandler, EditHandler, BatchEditHandler,
                   ApplyPatchHandler, FindReplaceHandler, DeleteHandler,
                   MkdirHandler, StatHandler, ExistsHandler, ListDirHandler,
                   GlobHandler, GrepHandler, SearchHandler, BashHandler, NotebookEditHandler,
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



