"""Unified Filesystem Service — WS route on the main HTTPListenerService.

On connect(), registers /ws/relay/<service_id> on the shared main listener;
the relay client then reverse-connects to that URL and streams filesystem
commands over the pool.

Config:
    token: str      — Shared token (relay must match to connect)
    mode: str       — "readwrite" | "readonly" (informational)

Relay usage:
    python tools/pawflow_relay.py --server wss://<host>:<main_port>/ws/relay/<service_id>
        --relay-id <service_id> --token <token> --dir /path
"""

import logging
import threading
from typing import Any, Dict, List, Optional

from core import ServiceFactory
from core.base_service import BaseService

from services._relay_ws import (  # noqa: F401  (re-exported public surface)
    _invalidate_tool_relay_registry_cache,
    _short_args,
    _is_relay_disconnect_error,
    _get_relay_scripts_bundle,
    _sync_relay_scripts,
    _attach_sync_sock_to_loop,
    _ws_recv_frame,
    _ws_close_info,
    _ws_send_frame,
)
from services._relay_conn import _RelayConnMixin
from services._filesystem_ops import _RelayFsOpsMixin

logger = logging.getLogger(__name__)


class RelayService(_RelayConnMixin, _RelayFsOpsMixin, BaseService):
    """Filesystem service backed by a reverse WebSocket relay."""

    TYPE = "relay"
    VERSION = "2.0.0"
    NAME = "Relay"
    DESCRIPTION = "Managed server relay or standalone WebSocket relay client"
    PARAMETERS = {
        "relay_id": {
            "type": "string", "required": False, "default": "",
            "description": "Existing linked/user relay id to reference from a flow instead of creating a new standalone relay",
        },
        "name": {
            "type": "string", "required": False, "default": "",
            "description": "Alias for relay_id",
        },
        "token": {
            "type": "string", "required": False, "sensitive": True, "default": "",
            "description": "Authentication token for a standalone external relay client. Managed server relays generate this token server-side.",
        },
        "mode": {
            "type": "select", "required": False, "default": "readwrite",
            "options": ["readwrite", "readonly"],
            "description": "Access mode for file operations",
        },
    }
    SERVICE_API = [
        "exec(path, command, timeout=None, shell=True, env=None, local=False)",
        "read_file(path, encoding='utf-8')",
        "write_file(path, content, encoding='utf-8')",
        "exists(path), list_dir(path), mkdir(path), delete(path), stat(path), grep(path, pattern)",
    ]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._service_id = self.config.get("_service_id", "")

        self._project_context: Optional[Dict] = None  # auto-fetched on relay connect
        self._relay_shells: List[str] = []  # available shells on the relay system
        self._relay_info: Dict[str, Any] = {}  # full registration info (platform, containerized, etc.)

        # Relay connection pool. Each entry is one WS to the same
        # service_id. The pool typically holds exactly one entry —
        # multi-relay setups link several RelayServices to a conversation
        # (core/relay_bindings) with one designated default; routing
        # between different relays happens at the agent-tool-config
        # level via that default + explicit `relay=` param, NOT inside
        # this pool. The pool grows beyond 1 only during reconnect
        # overlap: a dying WS that the server hasn't detected as dead
        # yet, plus the freshly reconnected one. We always send to the
        # most-recently-connected WS first and fall back to older ones
        # only if it fails. Round-robin would split traffic across the
        # dying and live WS unpredictably.
        self._relay_pool: List[Dict] = []  # [{"reader", "writer", "loop"}]
        self._relay_pool_lock = threading.Lock()
        self._managed_container_started = False

        # Pending requests: {request_id: (Event, result_holder)}
        self._pending: Dict[str, tuple] = {}
        self._pending_lock = threading.Lock()

        # Inverse-direction handler: relay-initiated FS ops scoped to the
        # owner's CLAUDE_SESSIONS_DIR slot. We seed `_user_id` from the
        # registry-supplied `_scope_id` if this is a user-scoped relay,
        # so the FUSE bridge serves requests even before any tool
        # handler has called set_user_id(). Without this seed, the
        # first FUSE callbacks (e.g. `ls /cc_sessions/` from a bare
        # relay terminal) would arrive with `_user_id == ""` and the
        # dispatcher would either return EACCES or block depending on
        # the path — exact symptom user saw.
        try:
            from core.service_registry import SCOPE_USER
            if self.config.get("_scope", "") == SCOPE_USER:
                self._user_id = str(self.config.get("_scope_id", "") or "")
            else:
                self._user_id = ""
        except Exception:
            self._user_id = ""
        self._server_fs = None
        self._server_fs_lock = threading.Lock()
        # Second inverse-direction handler: virtualized FUSE view of the
        # FileStore. Methods come in with the `ffs.` prefix and dispatch
        # to RelayFileStoreFs instead of RelayServerFs.
        self._filestore_fs = None
        self._filestore_fs_lock = threading.Lock()
        # Third inverse-direction handler: virtualized FUSE view of the
        # Agent Skills repository. Methods come in with the `skfs.`
        # prefix and dispatch to RelaySkillsFs.
        self._skills_fs = None
        self._skills_fs_lock = threading.Lock()
        self._ctx_sync_lock = threading.Lock()
        self._ctx_sync_active = False

    def get_parameter_schema(self) -> Dict[str, Any]:
        return self.PARAMETERS

    @property
    def service_id(self) -> str:
        return self._service_id

    def get_project_prompt(self) -> str:
        """Build a system prompt supplement from the auto-scanned project context."""
        ctx = self._project_context
        if not ctx:
            return ""
        lines = [f"\n\n## Filesystem: {self._service_id}"]
        if ctx.get("project_types"):
            lines.append(f"Project type: {', '.join(ctx['project_types'])}")
        if ctx.get("git"):
            lines.append(f"Git repo (branch: {ctx.get('git_branch', '?')})")
        # .pawflow.md or CLAUDE.md — project instructions
        for key in (".pawflow.md", "CLAUDE.md"):
            if key in ctx.get("config_files", {}):
                lines.append(f"\n### {key}\n{ctx['config_files'][key]}")
        # README summary (first 2000 chars)
        for key in ("README.md", "readme.md"):
            if key in ctx.get("config_files", {}):
                readme = ctx["config_files"][key][:2000]
                lines.append(f"\n### {key} (excerpt)\n{readme}")
                break
        # File tree
        if ctx.get("tree"):
            tree = ctx["tree"][:3000]
            lines.append(f"\n### Project structure\n```\n{tree}\n```")
        return "\n".join(lines)



# Register with ServiceFactory
ServiceFactory.register(RelayService)
