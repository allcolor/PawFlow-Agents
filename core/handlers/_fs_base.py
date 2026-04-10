"""BaseFsHandler — shared infrastructure for all filesystem tools.

Provides:
- Service resolution (explicit name → auto-detect → workdir fallback)
- Workdir operations (local server I/O for Claude Code agent workspace)
- FileStore routing
- Checkpoint support
- Path sandboxing
- Binary output capping
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

# Binary/base64 content cap — wastes context tokens
_BINARY_CAP = 2000

_FS_ALIASES = frozenset({"filestore", "store", "server"})
_FS_TYPES = ("relay", "filesystem", "googleDrive", "oneDrive")


def find_fs_service(user_id: str, service_name: str = ""):
    """Standalone service lookup (for non-handler code like HTTP actions).

    Walks conv > user > global scope chain via ServiceRegistry.
    Returns the live service instance or None.
    """
    def _set_uid(svc):
        if hasattr(svc, 'set_user_id') and user_id:
            svc.set_user_id(user_id)
        return svc

    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        if service_name:
            svc = reg.resolve(service_name, user_id=user_id)
            if svc:
                return _set_uid(svc)
        else:
            for fs_type in _FS_TYPES:
                for sdef in reg.resolve_by_type(fs_type, user_id=user_id):
                    svc = reg.resolve(sdef.service_id, user_id=user_id)
                    if svc:
                        return _set_uid(svc)
    except Exception:
        pass

    return None


def get_tool_relay_env() -> Dict[str, str]:
    """Get PawFlow SDK environment variables for scripts running in relay/Docker.

    Returns dict with PAWFLOW_TOOL_RELAY_URL, PAWFLOW_TOOL_RELAY_TOKEN, etc.
    Empty dict if no tool relay is available.
    """
    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        for sid, sdef in reg.get_all("global", "").items():
            if getattr(sdef, "service_type", "") != "toolRelay":
                continue
            svc = reg.get_live_instance("global", "", sid)
            if svc:
                cfg = getattr(sdef, "config", {}) or {}
                port = int(cfg.get("port", 0))
                token = cfg.get("token", "")
                if port and token:
                    return {
                        "PAWFLOW_TOOL_RELAY_URL": f"ws://host.docker.internal:{port}/ws/tools",
                        "PAWFLOW_TOOL_RELAY_TOKEN": token,
                    }
    except Exception:
        pass
    return {}


def cap_binary_output(text: str, cap: int = _BINARY_CAP) -> str:
    """Reduce cap for output that looks like binary or base64 data."""
    if not text or len(text) < cap:
        return text
    _b64_ratio = sum(
        1 for c in text[:2000]
        if c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/='
    ) / min(len(text), 2000)
    if _b64_ratio > 0.85:
        return text[:cap] + f"\n\n... [base64/binary data truncated — {len(text)} chars total]"
    if 'data:' in text[:200] and ';base64,' in text[:200]:
        return text[:cap] + f"\n\n... [data URI truncated — {len(text)} chars total]"
    return text


class BaseFsHandler(ToolHandler):
    """Base class for all filesystem-related tool handlers.

    Subclasses set their own name/description/parameters_schema/execute.
    This base provides service resolution and workdir fallback.
    """

    def __init__(self):
        self._fs_service = None
        self._available_services = []
        self._user_id = ""
        self._conversation_id = ""
        self._checkpoint_id = ""
        self._tool_result_max_chars = 50000
        self._workdir = ""
        self._is_claude_code = False
        self._default_local = None  # None=ask, True=local, False=docker

    # ── Setters (called by tool_relay_service and agent_tool_config) ──

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def set_checkpoint_id(self, checkpoint_id: str):
        self._checkpoint_id = checkpoint_id

    def set_available_services(self, services: List[Dict[str, Any]]):
        self._available_services = services

    def set_fs_service(self, svc):
        self._fs_service = svc

    def set_workdir(self, workdir: str):
        self._workdir = workdir

    def set_is_claude_code(self, val: bool):
        self._is_claude_code = val

    def _resolve_local(self, arguments: dict) -> bool:
        """Resolve the 'local' flag: explicit argument > default > False."""
        if "local" in arguments:
            return bool(arguments["local"])
        if self._default_local is not None:
            return self._default_local
        return False

    # ── Service resolution ──

    def _resolve(self, fs_param: str = "") -> Tuple[Any, Optional[str]]:
        """Resolve filesystem target from the FS parameter.

        Args:
            fs_param: Value of the source/destination/filesystem/relay parameter.
                      Empty string = auto-detect.

        Returns:
            (service, workdir_path):
              - service is set → operate via the service
              - workdir_path is set → operate locally on the agent workdir
              - both None → error (caller should report)
        """
        # fs:// URL → extract service name
        # (path parsing is the caller's responsibility)

        # FileStore aliases
        if fs_param and fs_param.lower() in _FS_ALIASES:
            return ("filestore", None)

        # Explicit service name
        if fs_param:
            svc = self._find_service(fs_param)
            if svc:
                return (svc, None)
            return (None, None)  # explicit name not found → error

        # Auto-detect (no param)
        if self._is_claude_code and self._workdir:
            return (None, self._workdir)

        # LLM API: try first relay/fs service
        svc = self._find_service("")
        if svc:
            return (svc, None)

        # No relay → FileStore fallback (LLM API only)
        return ("filestore", None)

    def _find_service(self, service_name: str = ""):
        """Find a filesystem service by name or auto-detect.

        Search order: injected _fs_service → find_fs_service (registries).
        If _available_services is set (from relay bindings), only allow
        services in that list — reject unlinked relays.
        """
        if self._fs_service:
            if not service_name or service_name == getattr(
                    self._fs_service, '_service_id', ''):
                return self._fs_service

        if service_name and service_name.lower() in ("workspace", "ws", "local"):
            return self._find_service("")

        # Check against linked relays if available
        if service_name and self._available_services:
            allowed_ids = {s.get("id", "") for s in self._available_services}
            if service_name not in allowed_ids:
                return None  # relay not linked to this conversation

        return find_fs_service(self._user_id, service_name)

    def _no_target_error(self, fs_param: str = "") -> str:
        """Error message when no FS target could be resolved."""
        available = self._available_services or []
        if fs_param:
            names = [s.get("id", "?") for s in available]
            if names:
                return (f"Error: service '{fs_param}' not found. "
                        f"Available: {', '.join(names)}")
            return f"Error: service '{fs_param}' not found. No filesystem services available."
        if available:
            names = [s.get("id", "?") for s in available]
            return (f"Error: no filesystem service specified. "
                    f"Available: {', '.join(names)}. "
                    f"Use the source/destination/filesystem parameter.")
        return "Error: no filesystem services available."

    # ── Path helpers ──

    @staticmethod
    def _parse_fs_url(path: str) -> Tuple[str, str]:
        """Parse fs://service/path into (service_name, path). Returns ("", path) if not a fs:// URL."""
        if path.startswith("fs://"):
            parts = path[5:].split("/", 1)
            return (parts[0], parts[1] if len(parts) > 1 else ".")
        return ("", path)

    @staticmethod
    def _sandbox_path(path: str, base: str) -> str:
        """Resolve path relative to base, preventing ../ escape."""
        if os.path.isabs(path):
            return path  # absolute paths pass through (service handles sandboxing)
        resolved = os.path.normpath(os.path.join(base, path))
        if not resolved.startswith(os.path.normpath(base)):
            raise ValueError(f"Path '{path}' escapes sandbox '{base}'")
        return resolved

    # ── Checkpoint ──

    def _checkpoint_before(self, svc, path: str, content_after: bytes = None,
                           is_delete: bool = False, service_name: str = ""):
        """Capture file state for /rewind support."""
        if not self._conversation_id or not self._checkpoint_id:
            return
        try:
            from core.checkpoint import CheckpointManager
            if is_delete:
                CheckpointManager.capture_before_delete(
                    svc, path, self._conversation_id, self._checkpoint_id, service_name)
            else:
                CheckpointManager.capture_before_write(
                    svc, path, content_after or b"",
                    self._conversation_id, self._checkpoint_id, service_name)
        except Exception as e:
            logger.debug(f"[checkpoint] capture failed for {path}: {e}")

    # ── FileStore operations ──

    def _filestore_read(self, path: str, offset: int = 0, limit: int = 0) -> str:
        """Read from server FileStore — same pagination as relay read."""
        from core.file_store import FileStore
        store = FileStore.instance()
        _fid_match = re.search(r'/?(?:files/)?([a-f0-9]{12})(?:/|$)', path)
        file_id = _fid_match.group(1) if _fid_match else path.split("/")[0]
        entry = store.get(file_id)
        if not entry:
            found = store.find_by_name(file_id)
            if found:
                entry = store.get(found)
        if not entry:
            return f"Error: '{file_id}' not found in FileStore"
        fname, data, ct = entry
        if ct and ct.startswith("image/"):
            import base64 as _b64
            b64 = _b64.b64encode(data).decode("ascii")
            url = f"/files/{file_id}/{fname}"
            return f"Image: {url}\n__image_data__:{ct}:{b64}"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"Binary file: {fname} ({len(data):,} bytes, {ct})"
        return self._format_text_read(fname, text, offset, limit)

    def _filestore_list(self) -> str:
        """List all files in server FileStore."""
        from core.file_store import FileStore
        store = FileStore.instance()
        entries = store.list_all() if hasattr(store, 'list_all') else []
        if not entries:
            return "(FileStore is empty)"
        lines = [f"📄 fs://filestore/{e['id']}/{e['name']} ({e.get('size', '?')} bytes)"
                 for e in entries[:50]]
        if len(entries) > 50:
            lines.append(f"... +{len(entries) - 50} more")
        return "\n".join(lines)

    def _filestore_delete(self, path: str = "", file_id: str = "") -> str:
        """Delete from server FileStore."""
        from core.file_store import FileStore
        store = FileStore.instance()
        _id = file_id
        if not _id:
            _fid_match = re.search(r'/?(?:files/)?([a-f0-9]{12})(?:/|$)', path)
            _id = _fid_match.group(1) if _fid_match else path.split("/")[0]
        store.delete(_id)
        return f"Deleted '{_id}' from FileStore"

    def _filestore_exists(self, path: str) -> str:
        from core.file_store import FileStore
        _fid_match = re.search(r'/?(?:files/)?([a-f0-9]{12})(?:/|$)', path)
        file_id = _fid_match.group(1) if _fid_match else path.split("/")[0]
        entry = FileStore.instance().get(file_id)
        return "true" if entry else "false"

    def _filestore_stat(self, path: str) -> str:
        from core.file_store import FileStore
        _fid_match = re.search(r'/?(?:files/)?([a-f0-9]{12})(?:/|$)', path)
        file_id = _fid_match.group(1) if _fid_match else path.split("/")[0]
        entry = FileStore.instance().get(file_id)
        if not entry:
            return f"Error: '{file_id}' not found in FileStore"
        fname, data, ct = entry
        return json.dumps({"name": fname, "size": len(data), "content_type": ct})

    # ── Workdir operations (local server filesystem) ──

    def _workdir_read(self, path: str, offset: int = 0, limit: int = 0) -> str:
        """Read a file from the agent workdir."""
        full = self._sandbox_path(path, self._workdir)
        if not os.path.exists(full):
            return f"Error: '{path}' not found in workspace"
        if os.path.isdir(full):
            return self._workdir_list(path)
        with open(full, "rb") as f:
            data = f.read()
        fname = os.path.basename(full)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"(binary file, {len(data)} bytes)"
        return self._format_text_read(fname, text, offset, limit)

    def _workdir_write(self, path: str, content: str) -> str:
        full = self._sandbox_path(path, self._workdir)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"

    def _workdir_list(self, path: str = ".") -> str:
        full = self._sandbox_path(path, self._workdir)
        if not os.path.isdir(full):
            return f"Error: '{path}' is not a directory"
        entries = sorted(os.listdir(full))
        lines = []
        for e in entries:
            ep = os.path.join(full, e)
            kind = "📁" if os.path.isdir(ep) else "📄"
            size = f" ({os.path.getsize(ep)} bytes)" if os.path.isfile(ep) else ""
            lines.append(f"{kind} {e}{size}")
        return "\n".join(lines) if lines else "(empty directory)"

    def _workdir_exists(self, path: str) -> str:
        full = self._sandbox_path(path, self._workdir)
        return "Exists" if os.path.exists(full) else "Does not exist"

    def _workdir_stat(self, path: str) -> str:
        full = self._sandbox_path(path, self._workdir)
        if not os.path.exists(full):
            return f"Error: '{path}' not found"
        st = os.stat(full)
        return json.dumps({
            "name": os.path.basename(full),
            "size": st.st_size,
            "is_dir": os.path.isdir(full),
            "modified": st.st_mtime,
        })

    def _workdir_delete(self, path: str) -> str:
        import shutil
        full = self._sandbox_path(path, self._workdir)
        if not os.path.exists(full):
            return f"Error: '{path}' not found"
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)
        return f"Deleted: {path}"

    def _workdir_mkdir(self, path: str) -> str:
        full = self._sandbox_path(path, self._workdir)
        os.makedirs(full, exist_ok=True)
        return f"Created directory: {path}"

    def _workdir_glob(self, pattern: str, path: str = ".") -> str:
        import fnmatch
        full = self._sandbox_path(path, self._workdir)
        matches = []
        for root, dirs, files in os.walk(full):
            for f in files:
                if fnmatch.fnmatch(f, pattern):
                    rel = os.path.relpath(os.path.join(root, f), self._workdir)
                    matches.append(rel.replace("\\", "/"))
        return "\n".join(matches) if matches else "(no matches)"

    def _workdir_grep(self, pattern: str, path: str = ".",
                      recursive: bool = True, limit: int = 50) -> str:
        full = self._sandbox_path(path, self._workdir)
        regex = re.compile(pattern)
        results = []
        if os.path.isfile(full):
            walk = [(os.path.dirname(full), [], [os.path.basename(full)])]
        else:
            walk = os.walk(full) if recursive else [(full, [], os.listdir(full))]
        for root, dirs, files in walk:
            for f in files:
                fp = os.path.join(root, f)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, 1):
                            if regex.search(line):
                                rel = os.path.relpath(fp, self._workdir).replace("\\", "/")
                                results.append(f"{rel}:{i}: {line.rstrip()}")
                                if len(results) >= limit:
                                    break
                except (OSError, UnicodeDecodeError):
                    continue
            if len(results) >= limit:
                break
        return "\n".join(results) if results else "(no matches)"

    def _workdir_edit(self, path: str, old_string: str, new_string: str,
                      replace_all: bool = False) -> str:
        full = self._sandbox_path(path, self._workdir)
        if not os.path.exists(full):
            return f"Error: '{path}' not found"
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        if old_string not in content:
            return f"Error: old_string not found in {path}"
        if replace_all:
            new_content = content.replace(old_string, new_string)
            count = content.count(old_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
            count = 1
        with open(full, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Edited {path}: {count} replacement(s)"

    def _workdir_find_replace(self, path: str, pattern: str, replacement: str) -> str:
        full = self._sandbox_path(path, self._workdir)
        if not os.path.exists(full):
            return f"Error: '{path}' not found"
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        new_content, count = re.subn(pattern, replacement, content)
        with open(full, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Replaced {count} occurrences in {path}"

    # ── Shared formatting ──

    def _format_text_read(self, fname: str, text: str,
                          offset: int = 0, limit: int = 0) -> str:
        """Format text file with line numbers and pagination."""
        _max_page = self._tool_result_max_chars
        lines = text.split("\n")
        total_lines = len(lines)
        total_chars = len(text)
        start = max(0, offset - 1) if offset > 0 else 0
        end = start + limit if limit else total_lines
        selected = lines[start:end]

        output_lines = []
        output_chars = 0
        for i, ln in enumerate(selected):
            line_text = f"{start + i + 1:4d}\t{ln}\n"
            if output_chars + len(line_text) > _max_page and output_lines:
                end = start + i
                break
            output_lines.append(line_text)
            output_chars += len(line_text)

        has_more = end < total_lines
        header = f"[{fname}: {total_lines} lines, {total_chars:,} chars"
        if start > 0 or has_more:
            header += f", showing lines {start + 1}-{min(end, total_lines)}"
        if has_more:
            header += (f" — use offset={end + 1} to read next page"
                       f" (MUST paginate, max {_max_page} chars/page)")
        header += "]"
        return header + "\n" + "".join(output_lines)

    # ── Expression resolution ──

    def _resolve_expressions(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve ${expressions} in all argument values."""
        from core.expression import resolve_value
        return resolve_value(arguments, owner=self._user_id)

    # ── JSON unwrapping ──

    @staticmethod
    def _unwrap_json(arguments) -> Dict[str, Any]:
        """Unwrap arguments that arrive as JSON string (MCP bridge double-encoding)."""
        for _ in range(3):
            if not isinstance(arguments, str):
                break
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                return {}
        return arguments if isinstance(arguments, dict) else {}
