"""BaseFsHandler — shared infrastructure for all filesystem tools.

Provides:
- Service resolution (explicit name → auto-detect → workdir fallback)
- Workdir operations (local server I/O for Claude Code agent workspace)
- FileStore routing
- Checkpoint support
- Path sandboxing
- Binary output capping
"""

import ast
import json
import logging
import os
import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

# Binary/base64 content cap — wastes context tokens
_BINARY_CAP = 2000

_FS_ALIASES = frozenset({"filestore", "store", "server"})
_FS_TYPES = ("relay", "filesystem", "googleDrive", "oneDrive")


def _expand_glob_braces(pattern: str, max_patterns: int = 256) -> List[str]:
    """Expand shell-style glob braces for Python glob/fnmatch callers."""
    def _split_options(body: str) -> List[str]:
        parts = []
        start = 0
        depth = 0
        for idx, ch in enumerate(body):
            if ch == "{":
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(body[start:idx])
                start = idx + 1
        parts.append(body[start:])
        return parts

    def _expand_one(value: str) -> List[str]:
        start = value.find("{")
        if start < 0:
            return [value]
        depth = 0
        end = -1
        for idx in range(start, len(value)):
            ch = value[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break
        if end < 0:
            return [value]
        prefix = value[:start]
        suffix = value[end + 1:]
        expanded = []
        for option in _split_options(value[start + 1:end]):
            for tail in _expand_one(suffix):
                expanded.append(prefix + option + tail)
                if len(expanded) >= max_patterns:
                    return expanded
        return expanded

    return _expand_one(pattern or "*")[:max_patterns]


def find_fs_service(user_id: str, service_name: str = "", conversation_id: str = ""):
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
        if conversation_id:
            try:
                from core.relay_bindings import get_default, get_linked
                linked = get_linked(conversation_id)
                default_id = get_default(conversation_id) or ""
            except Exception:
                linked = []
                default_id = ""
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if service_name:
                if service_name not in linked:
                    return None
            elif default_id and default_id in linked:
                service_name = default_id
            elif len(linked) == 1:
                service_name = linked[0]
            else:
                return None
        if service_name:
            svc = reg.resolve(service_name, user_id=user_id, conv_id=conversation_id)
            if svc:
                return _set_uid(svc)
        else:
            for fs_type in _FS_TYPES:
                for sdef in reg.resolve_by_type(fs_type, user_id=user_id, conv_id=conversation_id):
                    svc = reg.resolve(sdef.service_id, user_id=user_id, conv_id=conversation_id)
                    if svc:
                        return _set_uid(svc)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class BaseFsHandler(ToolHandler):
    """Base class for all filesystem-related tool handlers.

    Subclasses set their own name/description/parameters_schema/execute.
    This base provides service resolution and workdir fallback.
    """

    def __init__(self):
        self._fs_service = None
        self._available_services = []
        self._default_service_id = ""
        self._filesystem_scope_enforced = False
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""
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

    def set_agent_name(self, agent_name: str):
        # Scope for Read-before-Edit: each agent has its own view of what
        # it has read. Another agent reading the same file in the same
        # conv doesn't grant this agent permission to edit.
        self._agent_name = agent_name or ""

    def set_checkpoint_id(self, checkpoint_id: str):
        self._checkpoint_id = checkpoint_id

    def set_available_services(self, services: List[Dict[str, Any]],
                               default_service_id: str = ""):
        self._available_services = services
        self._default_service_id = default_service_id or ""
        self._filesystem_scope_enforced = True

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

        # No implicit FileStore fallback for filesystem tools. FileStore is
        # valid only when explicitly requested (`source=filestore`,
        # `fs://filestore/...`, etc.); otherwise omitted source means the
        # conversation's default relay/filesystem service.
        return (None, None)

    def _find_service(self, service_name: str = ""):
        """Find a filesystem service by name or auto-detect.

        Search order: injected _fs_service → find_fs_service (registries).
        If _available_services is set (from relay bindings), only allow
        services in that list — reject unlinked relays.
        """
        if service_name and service_name.lower() in ("workspace", "ws", "local"):
            return self._find_service("")

        if self._filesystem_scope_enforced:
            allowed_ids = [s.get("id", "") for s in self._available_services if s.get("id")]
            if service_name:
                if service_name not in allowed_ids:
                    return None
            elif self._default_service_id:
                service_name = self._default_service_id
            elif len(allowed_ids) == 1:
                service_name = allowed_ids[0]
            else:
                return None

        if self._fs_service:
            if not service_name or service_name == getattr(
                    self._fs_service, '_service_id', ''):
                return self._fs_service

        return find_fs_service(self._user_id, service_name, self._conversation_id)

    def _no_target_error(self, fs_param: str = "") -> str:
        """Error message when no FS target could be resolved."""
        available = self._available_services or []
        if fs_param:
            names = [s.get("id", "?") for s in available]
            if names:
                return (f"Error: filesystem not found: '{fs_param}'. "
                        f"Available: {', '.join(names)}")
            return f"Error: filesystem not found: '{fs_param}'"
        if available:
            names = [s.get("id", "?") for s in available]
            return (f"Error: no filesystem service specified. "
                    f"Available: {', '.join(names)}. "
                    f"Use the source/destination/filesystem parameter.")
        return "Error: no filesystem services available."

    # ── Optional RTK helpers ──

    def _rtk_enabled(self, arguments: Dict[str, Any]) -> bool:
        """Return whether filesystem tools should prefer RTK output."""
        secret_env = arguments.get("_secret_env") or {}
        if "PAWFLOW_USE_RTK" in secret_env:
            return _truthy(secret_env.get("PAWFLOW_USE_RTK"))
        if "PAWFLOW_USE_RTK" in os.environ:
            return _truthy(os.environ.get("PAWFLOW_USE_RTK"))
        try:
            from core.expression import resolve_expression
            raw = resolve_expression(
                "$" + "{" + "PAWFLOW_USE_RTK:default(\"\")" + "}",
                owner=self._user_id,
                conversation_id=self._conversation_id,
            )
        except Exception:
            raw = ""
        return _truthy(raw)

    def _relay_exec_rtk(self, svc, path: str, args: List[str],
                        arguments: Dict[str, Any]) -> Optional[str]:
        """Run an RTK command on the relay, returning stdout or None.

        This is best-effort: missing RTK, unsupported flags, or non-zero exit
        all fall back to the native handler path.
        """
        if not self._rtk_enabled(arguments):
            return None
        if not hasattr(svc, "exec"):
            return None
        command = " ".join(shlex.quote(part) for part in args)
        kwargs = {"shell": ""}
        if arguments.get("_secret_env"):
            kwargs["env"] = arguments["_secret_env"]
        try:
            result = svc.exec(
                path,
                command,
                local=bool(arguments.get("local", False)),
                **kwargs,
            )
        except Exception as exc:
            logger.debug("[rtk] command failed; using native path: %s", exc)
            return None
        if result.get("returncode", 0) != 0:
            logger.debug("[rtk] command rc=%s; using native path", result.get("returncode"))
            return None
        output = str(result.get("stdout", ""))
        return output if output.strip() else None

    # ── Path helpers ──

    @staticmethod
    def _parse_fs_url(path: str) -> Tuple[str, str]:
        """Parse a path into (service_name, path).

        Recognized formats:
          fs://service/path                    → (service, path)
          fs://filestore/id/name               → ("filestore", "id/name")
          /files/{id}/{name}                   → ("filestore", "id/name")
          /filestore/{id}/{name}               → ("filestore", "id/name")
          /filestore/{conversation}/{id}/name  → ("filestore", "conversation/id/name")
          (anything else)                      → ("", path)
        """
        if path.startswith("fs://"):
            parts = path[5:].split("/", 1)
            return (parts[0], parts[1] if len(parts) > 1 else ".")
        # Bare FileStore URL/path — auto-route to server FileStore.
        if path.startswith("/files/"):
            return ("filestore", path[len("/files/"):])
        if path.startswith("/filestore/"):
            return ("filestore", path[len("/filestore/"):])
        return ("", path)

    @staticmethod
    def _filestore_id_from_path(path: str) -> str:
        """Extract a FileStore id from fs:// or /filestore paths.

        Web/relay logs may show /filestore/{conversation_id}/{file_id}/name,
        while tool URLs use fs://filestore/{file_id}/name. Prefer the first
        12-hex segment after an optional conversation-id segment.
        """
        cleaned = (path or "").strip()
        if cleaned.startswith("fs://filestore/"):
            cleaned = cleaned[len("fs://filestore/"):]
        elif cleaned.startswith("/filestore/"):
            cleaned = cleaned[len("/filestore/"):]
        elif cleaned.startswith("/files/"):
            cleaned = cleaned[len("/files/"):]
        elif cleaned.startswith("files/"):
            cleaned = cleaned[len("files/"):]
        elif cleaned.startswith("filestore/"):
            cleaned = cleaned[len("filestore/"):]
        segments = [seg for seg in cleaned.split("/") if seg]
        if len(segments) >= 2:
            first, second = segments[0], segments[1]
            if re.fullmatch(r"[a-f0-9]{13,64}", first) and re.fullmatch(r"[a-f0-9]{12}", second):
                return second
        for seg in segments:
            if re.fullmatch(r"[a-f0-9]{12}", seg):
                return seg
        return segments[0] if segments else cleaned

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

    def _filestore_read(self, path: str, offset: int = 0, limit: int = 0,
                        mode: str = "full") -> str:
        """Read from server FileStore — same pagination as relay read."""
        from core.file_store import FileStore
        store = FileStore.instance()
        file_id = self._filestore_id_from_path(path)
        entry = store.get(file_id, user_id=self._user_id)
        if not entry:
            found = store.find_by_name(file_id)
            if found:
                entry = store.get(found, user_id=self._user_id)
        if not entry:
            return f"Error: '{file_id}' not found in FileStore"
        fname, data, ct = entry
        if ct and ct.startswith("image/"):
            import base64 as _b64
            b64 = _b64.b64encode(data).decode("ascii")
            url = f"fs://filestore/{file_id}/{fname}"
            return f"Image: {url}\n__image_data__:{ct}:{b64}"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"Binary file: {fname} ({len(data):,} bytes, {ct})"
        if mode == "outline":
            return self._format_outline_read(fname, text)
        return self._format_text_read(fname, text, offset, limit)

    def _filestore_list(self) -> str:
        """List all files in server FileStore."""
        from core.file_store import FileStore
        store = FileStore.instance()
        entries = store.list_files(
            user_id=self._user_id,
            conversation_id=self._conversation_id or "",
            include_internal=False,
        )
        if not entries:
            return "(FileStore is empty)"
        lines = [f"📄 fs://filestore/{e['file_id']}/{e['filename']} ({e.get('size', '?')} bytes)"
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
            _id = self._filestore_id_from_path(path)
        store.delete(_id)
        return f"Deleted '{_id}' from FileStore"

    def _filestore_exists(self, path: str) -> str:
        from core.file_store import FileStore
        file_id = self._filestore_id_from_path(path)
        entry = FileStore.instance().get(file_id, user_id=self._user_id)
        return "true" if entry else "false"

    def _filestore_stat(self, path: str) -> str:
        from core.file_store import FileStore
        file_id = self._filestore_id_from_path(path)
        entry = FileStore.instance().get(file_id, user_id=self._user_id)
        if not entry:
            return f"Error: '{file_id}' not found in FileStore"
        fname, data, ct = entry
        return json.dumps({"name": fname, "size": len(data), "content_type": ct})

    # ── Workdir operations (local server filesystem) ──

    def _workdir_read(self, path: str, offset: int = 0, limit: int = 0,
                      mode: str = "full") -> str:
        """Read a file from the agent workdir."""
        full = self._sandbox_path(path, self._workdir)
        if not os.path.exists(full):
            return f"Error: '{path}' not found in workspace"
        if os.path.isdir(full):
            return self._workdir_list(path)
        with open(full, "rb") as f:
            data = f.read()
        fname = os.path.basename(full)
        # Track reads so failed edit retries can be cleared after a fresh view.
        from core.handlers._edit_guard import track_read
        track_read(self._user_id, self._conversation_id,
                   self._agent_name, path, data)
        _img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")
        if any(fname.lower().endswith(ext) for ext in _img_exts):
            import base64 as _b64
            import mimetypes as _mimetypes
            mime = _mimetypes.guess_type(fname)[0] or "image/png"
            b64 = _b64.b64encode(data).decode("ascii")
            return f"Image: {fname} ({len(data):,} bytes, {mime})\n__image_data__:{mime}:{b64}"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"(binary file, {len(data)} bytes)"
        if mode == "outline":
            return self._format_outline_read(fname, text)
        return self._format_text_read(fname, text, offset, limit)

    def _workdir_write(self, path: str, content: str) -> str:
        full = self._sandbox_path(path, self._workdir)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"

    def _workdir_list(self, path: str = ".", recursive: bool = False,
                      max_entries: int = 0) -> str:
        full = self._sandbox_path(path, self._workdir)
        if not os.path.isdir(full):
            return f"Error: '{path}' is not a directory"
        lines = []
        if recursive:
            iterator = []
            for root, dirs, files in os.walk(full):
                for name in sorted(dirs + files):
                    iterator.append(os.path.join(root, name))
            entries = sorted(iterator)
        else:
            entries = [os.path.join(full, e) for e in sorted(os.listdir(full))]
        for ep in entries:
            rel = os.path.relpath(ep, full).replace(os.sep, "/") if recursive else os.path.basename(ep)
            kind = "📁" if os.path.isdir(ep) else "📄"
            size = f" ({os.path.getsize(ep)} bytes)" if os.path.isfile(ep) else ""
            lines.append(f"{kind} {rel}{size}")
            if max_entries > 0 and len(lines) >= max_entries:
                break
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

    def _workdir_glob(self, pattern: str, path: str = ".", limit: int = 500) -> str:
        from pathlib import Path
        full = self._sandbox_path(path, self._workdir)
        patterns = _expand_glob_braces(pattern)
        matches = []
        seen = set()
        for pat in patterns:
            for match in Path(full).glob(pat):
                if not match.is_file():
                    continue
                rel = os.path.relpath(str(match), self._workdir).replace("\\", "/")
                if rel in seen:
                    continue
                seen.add(rel)
                matches.append(rel)
                if len(matches) >= limit:
                    return "\n".join(matches)
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
            # Rich diagnostic so the agent stops retrying the same wrong
            # pattern and re-reads the file instead.
            from tools.fs_actions import _diagnose_edit_mismatch
            return "Error: " + _diagnose_edit_mismatch(
                old_string, content, os.path.basename(path))
        if replace_all:
            new_content = content.replace(old_string, new_string)
            count = content.count(old_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
            count = 1
        with open(full, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Edited {path}: {count} replacement(s)"

    def _workdir_find_replace(self, path: str, pattern: str, replacement: str,
                              multiline: bool = False) -> str:
        full = self._sandbox_path(path, self._workdir)
        if not os.path.exists(full):
            return f"Error: '{path}' not found"
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        flags = re.MULTILINE if multiline else 0
        new_content, count = re.subn(pattern, replacement, content, flags=flags)
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

        page_end = min(start + len(output_lines), total_lines)

        def build_header(current_end: int) -> str:
            has_more = current_end < total_lines
            header = f"[{fname}: {total_lines} lines, {total_chars:,} chars"
            if start > 0 or has_more:
                header += f", showing lines {start + 1}-{min(current_end, total_lines)}"
            if has_more:
                header += (f" — use offset={current_end + 1} to read next page"
                           f" (MUST paginate, max {_max_page} chars/page)")
            return header + "]"

        header = build_header(page_end)
        body = "".join(output_lines)
        result = header + "\n" + body

        # The registry applies the same character cap to final tool results.
        # Keep the formatted page, including metadata header, below that cap so
        # paginated reads do not get stored again as a separate tool result.
        while len(result) > _max_page and len(output_lines) > 1:
            output_lines.pop()
            page_end -= 1
            header = build_header(page_end)
            body = "".join(output_lines)
            result = header + "\n" + body
        if len(result) > _max_page and output_lines:
            body_budget = max(0, _max_page - len(header) - 1)
            result = header + "\n" + body[:body_budget]
        return result

    def _format_outline_read(self, fname: str, text: str) -> str:
        """Return a compact source outline with function/class bodies stubbed."""
        lower = fname.lower()
        if lower.endswith(".py"):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                return self._format_text_read(fname, text, 0, 200)
            lines = text.split("\n")
            keep = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    keep.add(node.lineno)
                elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    keep.add(node.lineno)
                    for deco in getattr(node, "decorator_list", []) or []:
                        keep.add(getattr(deco, "lineno", node.lineno))
                    for child in getattr(node, "body", [])[:2]:
                        if isinstance(child, ast.Expr) and isinstance(getattr(child, "value", None), ast.Constant):
                            keep.add(child.lineno)
            output = []
            emitted_stub_for = set()
            for idx, line in enumerate(lines, start=1):
                stripped = line.strip()
                if idx in keep or stripped.startswith(("@", "class ", "def ", "async def ", "import ", "from ")):
                    output.append(f"{idx:4d}\t{line}")
                    if stripped.startswith(("class ", "def ", "async def ")):
                        indent = line[:len(line) - len(line.lstrip())]
                        stub_line = indent + "    ..."
                        if idx not in emitted_stub_for:
                            output.append(f"{idx:4d}\t{stub_line}")
                            emitted_stub_for.add(idx)
            return f"[{fname}: outline, {len(lines)} lines, {len(text):,} chars]\n" + "\n".join(output)

        if lower.endswith((".js", ".jsx", ".ts", ".tsx")):
            sig = re.compile(
                r"^\s*(export\s+)?(async\s+)?(function\s+\w+|class\s+\w+|"
                r"(const|let|var)\s+\w+\s*=\s*(async\s*)?(\([^)]*\)|\w+)\s*=>|"
                r"import\s+|export\s+\{)"
            )
            lines = text.split("\n")
            output = []
            for idx, line in enumerate(lines, start=1):
                if sig.search(line):
                    output.append(f"{idx:4d}\t{line.rstrip()}")
                    if "{" in line and not line.rstrip().endswith(";"):
                        indent = line[:len(line) - len(line.lstrip())]
                        output.append(f"{idx:4d}\t{indent}  ...")
            return f"[{fname}: outline, {len(lines)} lines, {len(text):,} chars]\n" + "\n".join(output)

        return self._format_text_read(fname, text, 0, 200)

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
