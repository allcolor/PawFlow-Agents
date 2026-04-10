"""DynamicToolStore — Manage user-uploaded Python tool handlers.

Users can upload .py files containing ToolHandler subclasses via the chat UI
or Telegram. These are validated (AST + sandbox), stored to disk, and
registered in the agent's ToolRegistry.

Security:
- AST static analysis rejects forbidden imports, os/subprocess access, eval/exec
- Runtime sandbox uses the same restricted builtins as ExecuteScriptHandler
- Per-user isolation: users can only manage their own tools
- Admin can manage all tools
"""

import ast
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.tool_registry import ToolHandler

logger = logging.getLogger(__name__)

from core.paths import DYNAMIC_TOOLS_DIR; _DEFAULT_DIR = str(DYNAMIC_TOOLS_DIR)

# Modules allowed in dynamic tools (same as ExecuteScriptHandler)
_SAFE_MODULES = frozenset({
    "datetime", "math", "re", "json", "time",
    "collections", "itertools", "functools", "statistics",
    "decimal", "fractions", "random", "string", "textwrap",
    "urllib", "urllib.parse", "urllib.request", "urllib.error",
    "http", "http.client", "http.cookiejar",
    "hashlib", "base64", "uuid",
    "numpy", "csv", "io", "operator", "copy",
    "struct", "html", "xml", "xml.etree", "xml.etree.ElementTree",
    "requests",
})

_SAFE_PREFIXES = (
    "requests.", "urllib.", "http.", "xml.", "collections.",
    "html.", "numpy.",
)

# AST node names that are forbidden
_FORBIDDEN_NAMES = frozenset({
    "exec", "eval", "compile", "__import__", "open",
    "globals", "locals", "breakpoint", "exit", "quit",
})

# Forbidden attribute accesses
_FORBIDDEN_ATTRS = frozenset({
    "__subclasses__", "__globals__", "__builtins__", "__code__",
    "__class__", "__bases__", "__mro__",
})


class DynamicToolStore:
    """Singleton store for user-uploaded tool handlers."""

    _instance: Optional["DynamicToolStore"] = None
    _lock = threading.Lock()

    def __init__(self, store_dir: str = ""):
        self._store_dir = Path(store_dir or _DEFAULT_DIR)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._handlers: Dict[str, ToolHandler] = {}  # tool_name -> handler
        self._metadata: Dict[str, Dict[str, Any]] = {}  # tool_name -> meta
        self._store_lock = threading.Lock()
        self._loaded = False

    @classmethod
    def instance(cls) -> "DynamicToolStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        self._load_from_disk()

    # ── Public API ────────────────────────────────────────────────

    def install(self, user_id: str, filename: str, source: str,
                conversation_id: str = "") -> Dict[str, Any]:
        """Validate, sandbox-load, and persist a tool handler.

        Returns dict with tool_name and status, or raises ValueError on error.
        """
        # Step 1: Validate source
        violations = self.validate_source(source)
        if violations:
            raise ValueError(
                f"Security validation failed:\n" +
                "\n".join(f"- {v}" for v in violations)
            )

        # Step 2: Parse and find ToolHandler subclass
        handler, tool_name = self._sandbox_load(source)

        # Step 3: Check for name collision
        with self._store_lock:
            self._ensure_loaded()
            existing = self._metadata.get(tool_name)
            if existing and existing["user_id"] != user_id:
                raise ValueError(
                    f"Tool '{tool_name}' already exists (owned by {existing['user_id']})"
                )

            # Step 4: Persist to disk
            user_dir = self._store_dir / self._safe_user_id(user_id)
            user_dir.mkdir(parents=True, exist_ok=True)
            tool_path = user_dir / f"{tool_name}.py"
            tool_path.write_text(source, encoding="utf-8")

            # Step 5: Register
            self._handlers[tool_name] = handler
            meta = {
                "user_id": user_id,
                "filename": filename,
                "tool_name": tool_name,
                "installed_at": time.time(),
                "hash": hashlib.sha256(source.encode()).hexdigest()[:16],
                "description": handler.description,
            }
            if conversation_id:
                meta["conversation_id"] = conversation_id
            self._metadata[tool_name] = meta
            self._save_index()

        logger.info(f"Dynamic tool '{tool_name}' installed by {user_id}")
        return {"tool_name": tool_name, "description": handler.description}

    def uninstall(self, user_id: str, tool_name: str,
                  is_admin: bool = False) -> bool:
        """Remove a tool. Users can only remove their own; admins can remove any."""
        with self._store_lock:
            self._ensure_loaded()
            meta = self._metadata.get(tool_name)
            if not meta:
                return False
            if not is_admin and meta["user_id"] != user_id:
                raise PermissionError(
                    f"Tool '{tool_name}' is owned by {meta['user_id']}"
                )

            # Remove from disk
            user_dir = self._store_dir / self._safe_user_id(meta["user_id"])
            tool_path = user_dir / f"{tool_name}.py"
            tool_path.unlink(missing_ok=True)

            # Remove from memory
            self._handlers.pop(tool_name, None)
            self._metadata.pop(tool_name, None)
            self._save_index()

        logger.info(f"Dynamic tool '{tool_name}' uninstalled by {user_id}")
        return True

    def list_tools(self, user_id: str = "",
                   is_admin: bool = False) -> List[Dict[str, Any]]:
        """List installed dynamic tools. Filtered by user unless admin."""
        with self._store_lock:
            self._ensure_loaded()
            result = []
            for name, meta in self._metadata.items():
                if not is_admin and user_id and meta["user_id"] != user_id:
                    continue
                result.append({
                    "tool_name": name,
                    "description": meta.get("description", ""),
                    "owner": meta["user_id"],
                    "installed_at": meta.get("installed_at", 0),
                    "source": "dynamic",
                })
            return result

    def get_all_handlers(self) -> Dict[str, ToolHandler]:
        """Return all loaded dynamic tool handlers."""
        with self._store_lock:
            self._ensure_loaded()
            return dict(self._handlers)

    def get_handler(self, tool_name: str) -> Optional[ToolHandler]:
        """Get a single handler by name."""
        with self._store_lock:
            self._ensure_loaded()
            return self._handlers.get(tool_name)

    # ── Validation ────────────────────────────────────────────────

    @staticmethod
    def validate_source(source: str) -> List[str]:
        """Static analysis of Python source. Returns list of violations."""
        violations = []

        # Parse
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return [f"Syntax error: {e}"]

        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    full = alias.name
                    if (full not in _SAFE_MODULES
                            and mod not in _SAFE_MODULES
                            and not any(full.startswith(p) for p in _SAFE_PREFIXES)):
                        violations.append(f"Forbidden import: {alias.name}")

            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                full = node.module or ""
                if (full not in _SAFE_MODULES
                        and mod not in _SAFE_MODULES
                        and not any(full.startswith(p) for p in _SAFE_PREFIXES)):
                    violations.append(f"Forbidden import: from {node.module}")

            # Check forbidden names (exec, eval, open, etc.)
            elif isinstance(node, ast.Name):
                if node.id in _FORBIDDEN_NAMES:
                    violations.append(f"Forbidden name: {node.id}")

            # Check forbidden attribute access
            elif isinstance(node, ast.Attribute):
                if node.attr in _FORBIDDEN_ATTRS:
                    violations.append(f"Forbidden attribute: {node.attr}")

        return violations

    # ── Sandbox loading ───────────────────────────────────────────

    def _sandbox_load(self, source: str) -> tuple:
        """Load source in a sandboxed exec and extract the ToolHandler subclass.

        Returns (handler_instance, tool_name).
        """
        import datetime as _dt
        import math as _math

        def _safe_import(name, *args, **kwargs):
            if (name not in _SAFE_MODULES
                    and not any(name.startswith(p) for p in _SAFE_PREFIXES)):
                raise ImportError(f"Module '{name}' is not allowed")
            return __import__(name, *args, **kwargs)

        safe_builtins = {
            "abs": abs, "all": all, "any": any, "bool": bool,
            "dict": dict, "enumerate": enumerate, "float": float,
            "int": int, "len": len, "list": list, "max": max,
            "min": min, "range": range, "round": round, "set": set,
            "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
            "type": type, "zip": zip, "True": True, "False": False,
            "None": None, "isinstance": isinstance, "map": map,
            "filter": filter, "reversed": reversed,
            "__import__": _safe_import,
            "__build_class__": __build_class__, "__name__": "__dynamic_tool__",
            "print": print,
            "hasattr": hasattr, "getattr": getattr, "setattr": setattr,
            "property": property, "staticmethod": staticmethod,
            "classmethod": classmethod, "super": super,
            "issubclass": issubclass, "callable": callable,
            "iter": iter, "next": next, "repr": repr, "hash": hash,
            "id": id, "frozenset": frozenset, "bytes": bytes,
            "bytearray": bytearray, "complex": complex,
            "chr": chr, "ord": ord, "hex": hex, "oct": oct, "bin": bin,
            "format": format,
            "ValueError": ValueError, "TypeError": TypeError,
            "KeyError": KeyError, "IndexError": IndexError,
            "AttributeError": AttributeError, "RuntimeError": RuntimeError,
            "StopIteration": StopIteration, "Exception": Exception,
            "NotImplementedError": NotImplementedError,
            "datetime": _dt, "math": _math,
        }

        # Inject ToolHandler base class so user code can subclass it
        safe_builtins["ToolHandler"] = ToolHandler

        namespace: Dict[str, Any] = {}
        try:
            exec(source, {"__builtins__": safe_builtins}, namespace)
        except Exception as e:
            raise ValueError(f"Failed to execute tool source: {e}")

        # Find the ToolHandler subclass
        handler_class = None
        for obj in namespace.values():
            if (isinstance(obj, type)
                    and issubclass(obj, ToolHandler)
                    and obj is not ToolHandler):
                handler_class = obj
                break

        if handler_class is None:
            raise ValueError(
                "No ToolHandler subclass found. Your file must define a class "
                "that inherits from ToolHandler with name, description, "
                "parameters_schema properties and an execute() method."
            )

        # Validate the handler
        try:
            handler = handler_class()
            name = handler.name
            desc = handler.description
            schema = handler.parameters_schema
            if not name or not isinstance(name, str):
                raise ValueError("Tool name must be a non-empty string")
            if not desc:
                raise ValueError("Tool description must be non-empty")
            if not isinstance(schema, dict):
                raise ValueError("parameters_schema must be a dict")
        except Exception as e:
            raise ValueError(f"Invalid ToolHandler: {e}")

        return handler, name

    # ── Disk persistence ──────────────────────────────────────────

    def _index_path(self) -> Path:
        return self._store_dir / "_index.json"

    def _save_index(self):
        """Save metadata index to disk."""
        try:
            data = list(self._metadata.values())
            tmp = self._index_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._index_path())
        except Exception as e:
            logger.error(f"Failed to save dynamic tools index: {e}")

    def _load_from_disk(self):
        """Load all dynamic tools from disk."""
        index_path = self._index_path()
        if not index_path.exists():
            return

        try:
            entries = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load dynamic tools index: {e}")
            return

        loaded = 0
        failed = 0
        for entry in entries:
            tool_name = entry.get("tool_name", "")
            user_id = entry.get("user_id", "")
            if not tool_name or not user_id:
                continue

            user_dir = self._store_dir / self._safe_user_id(user_id)
            tool_path = user_dir / f"{tool_name}.py"
            if not tool_path.exists():
                logger.warning(f"Dynamic tool file missing: {tool_path}")
                failed += 1
                continue

            try:
                source = tool_path.read_text(encoding="utf-8")
                handler, name = self._sandbox_load(source)
                self._handlers[name] = handler
                self._metadata[name] = entry
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load dynamic tool '{tool_name}': {e}")
                failed += 1

        if loaded or failed:
            logger.info(f"Dynamic tools: loaded {loaded}, failed {failed}")

    def cleanup_conversation(self, conversation_id: str) -> int:
        """Remove all tools belonging to a conversation. Returns count deleted."""
        with self._store_lock:
            self._ensure_loaded()
            to_remove = [
                name for name, meta in self._metadata.items()
                if meta.get("conversation_id") == conversation_id
            ]
            for name in to_remove:
                meta = self._metadata[name]
                user_dir = self._store_dir / self._safe_user_id(meta["user_id"])
                tool_path = user_dir / f"{name}.py"
                tool_path.unlink(missing_ok=True)
                self._handlers.pop(name, None)
                self._metadata.pop(name, None)
            if to_remove:
                self._save_index()
                logger.info(
                    f"[cleanup] deleted {len(to_remove)} dynamic tools "
                    f"for conversation {conversation_id}"
                )
            return len(to_remove)

    @staticmethod
    def _safe_user_id(user_id: str) -> str:
        """Sanitize user_id for filesystem use."""
        return "".join(c for c in user_id if c.isalnum() or c in "-_@.")
