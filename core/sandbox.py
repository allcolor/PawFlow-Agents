"""Unified script sandbox for PyFi2.

Provides a secure execution environment used by both:
- ExecuteScriptTask (flow task)
- ExecuteScriptHandler (agent tool)

Features:
- Safe builtins whitelist (no eval, exec, compile, __import__ direct)
- Module whitelist (math, json, re, csv, datetime, io, requests, etc.)
- Sandboxed open() backed by FileStore (virtual filesystem)
- Print capture
- Pre-injected common modules (io, datetime, math, json, re)
"""

import io
import builtins
from typing import Any, Callable, Dict, List, Optional


# ── Safe modules whitelist ───────────────────────────────────────────

SAFE_MODULES = frozenset({
    "datetime", "math", "re", "json", "time",
    "collections", "itertools", "functools", "statistics",
    "decimal", "fractions", "random", "string", "textwrap",
    "urllib", "urllib.parse", "urllib.request", "urllib.error",
    "http", "http.client", "http.cookiejar",
    "hashlib", "base64", "uuid",
    "numpy", "csv", "io", "operator", "copy",
    "struct", "html", "xml", "xml.etree", "xml.etree.ElementTree",
    "requests",
    "zipfile", "gzip", "bz2", "lzma", "tarfile",
    "pathlib", "fnmatch", "glob", "difflib",
    "pprint", "enum", "dataclasses", "typing",
})

# Sub-imports allowed from these prefixes
SAFE_PREFIXES = (
    "requests.", "urllib.", "http.", "xml.", "collections.",
    "html.", "numpy.",
)


# ── SandboxFile ──────────────────────────────────────────────────────

class SandboxFile:
    """File-like object backed by in-memory buffer.

    On close(), written data is persisted to FileStore and a download
    URL is recorded.
    """

    def __init__(self, name: str, mode: str, initial: bytes,
                 store: Any, base_url: str,
                 created_files: List[str]):
        self._name = name
        self._mode = mode
        self._store = store
        self._base_url = base_url
        self._created_files = created_files
        self._closed = False

        is_binary = "b" in mode
        if "r" in mode:
            self._buf = (io.BytesIO(initial) if is_binary
                         else io.StringIO(initial.decode("utf-8", errors="replace")))
        elif "a" in mode:
            self._buf = (io.BytesIO(initial) if is_binary
                         else io.StringIO(initial.decode("utf-8", errors="replace")))
            self._buf.seek(0, 2)
        else:
            self._buf = io.BytesIO() if is_binary else io.StringIO()

    # Delegate standard file methods
    def read(self, *a): return self._buf.read(*a)
    def readline(self, *a): return self._buf.readline(*a)
    def readlines(self, *a): return self._buf.readlines(*a)
    def write(self, data): return self._buf.write(data)
    def writelines(self, lines): return self._buf.writelines(lines)
    def seek(self, *a): return self._buf.seek(*a)
    def tell(self): return self._buf.tell()
    def readable(self): return self._buf.readable()
    def writable(self): return self._buf.writable()
    def seekable(self): return self._buf.seekable()
    def __iter__(self): return iter(self._buf)
    def __next__(self): return next(self._buf)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if any(c in self._mode for c in "wa+"):
            self._buf.seek(0)
            raw = self._buf.read()
            content = raw if isinstance(raw, bytes) else raw.encode("utf-8")
            if content:
                from core.file_store import FileStore as _FS
                ct = _FS._guess_content_type(self._name)
                file_id = self._store.store(self._name, content, ct)
                url = f"{self._base_url}/files/{file_id}/{self._name}"
                self._created_files.append(url)
        self._buf.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        if hasattr(self, "_buf") and not self._closed:
            self.close()


# ── Sandbox builder ──────────────────────────────────────────────────

def make_sandbox_open(
    base_url: str = "http://localhost:9090",
    created_files: Optional[List[str]] = None,
    vfs: Optional[Dict[str, bytes]] = None,
) -> Callable:
    """Create a sandboxed open() backed by FileStore.

    Args:
        base_url: Base URL for download links.
        created_files: List to append created file URLs to.
        vfs: In-memory virtual filesystem (filename -> bytes).
    """
    from core.file_store import FileStore

    store = FileStore.instance()
    if created_files is None:
        created_files = []
    if vfs is None:
        vfs = {}

    def sandbox_open(name, mode="r", **kwargs):
        import os as _os
        safe_name = _os.path.basename(name) or "file"

        initial = b""
        if any(c in mode for c in "ra+"):
            if safe_name in vfs:
                initial = vfs[safe_name]
            else:
                for f in store.list_files():
                    if f["filename"] == safe_name:
                        result = store.get(f["file_id"])
                        if result:
                            initial = result[1]
                        break
                if not initial and "r" in mode and "+" not in mode:
                    raise FileNotFoundError(
                        f"No such file in sandbox: '{safe_name}'"
                    )

        sf = SandboxFile(
            safe_name, mode, initial,
            store, base_url, created_files,
        )

        # Capture written content into VFS on close
        _orig_close = sf.close

        def _vfs_close():
            if not sf._closed and any(c in mode for c in "wa+"):
                sf._buf.seek(0)
                raw = sf._buf.read()
                content = raw if isinstance(raw, bytes) else raw.encode("utf-8")
                vfs[safe_name] = content
                sf._buf.seek(0)
            _orig_close()

        sf.close = _vfs_close
        return sf

    return sandbox_open


def make_safe_import() -> Callable:
    """Create a restricted __import__ using the safe modules whitelist."""

    def _safe_import(name, *args, **kwargs):
        if (name not in SAFE_MODULES
                and not any(name.startswith(p) for p in SAFE_PREFIXES)):
            raise ImportError(f"Module '{name}' is not allowed")
        return builtins.__import__(name, *args, **kwargs)

    return _safe_import


def build_sandbox_globals(
    sandbox_open: Optional[Callable] = None,
    extra_vars: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Build sandboxed globals and a print-capture buffer.

    Returns:
        (globals_dict, print_buffer_list)
    """
    import datetime as _dt
    import math as _math
    import json as _json
    import re as _re

    print_buf: List[str] = []

    def _sandbox_print(*args, **kwargs):
        out = kwargs.get("sep", " ").join(str(a) for a in args)
        end = kwargs.get("end", "\n")
        print_buf.append(out + end)

    _safe_import = make_safe_import()

    safe_builtins = {
        # Core types
        "abs": abs, "all": all, "any": any, "bool": bool,
        "dict": dict, "enumerate": enumerate, "float": float,
        "int": int, "len": len, "list": list, "max": max,
        "min": min, "range": range, "round": round, "set": set,
        "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
        "type": type, "zip": zip, "True": True, "False": False,
        "None": None, "isinstance": isinstance, "map": map,
        "filter": filter, "reversed": reversed,
        # Object model
        "hasattr": hasattr, "getattr": getattr, "setattr": setattr,
        "property": property, "staticmethod": staticmethod,
        "classmethod": classmethod, "super": super,
        "issubclass": issubclass, "callable": callable,
        "iter": iter, "next": next, "repr": repr, "hash": hash,
        "id": id, "frozenset": frozenset, "bytes": bytes,
        "bytearray": bytearray, "memoryview": memoryview,
        "complex": complex, "divmod": divmod, "pow": pow,
        "chr": chr, "ord": ord, "hex": hex, "oct": oct, "bin": bin,
        "format": format, "vars": vars, "ascii": ascii, "slice": slice,
        # Class building
        "__import__": _safe_import,
        "__build_class__": __build_class__,
        "__name__": "__script__",
        # I/O
        "print": _sandbox_print,
        "open": sandbox_open or (lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("open() not available in this context"))),
        # Exceptions
        "ValueError": ValueError, "TypeError": TypeError,
        "KeyError": KeyError, "IndexError": IndexError,
        "AttributeError": AttributeError, "RuntimeError": RuntimeError,
        "StopIteration": StopIteration, "Exception": Exception,
        "FileNotFoundError": FileNotFoundError, "ImportError": ImportError,
        "OSError": OSError, "IOError": IOError,
        "ZeroDivisionError": ZeroDivisionError,
        "NotImplementedError": NotImplementedError,
        # Pre-injected safe modules
        "io": io, "datetime": _dt, "math": _math,
        "json": _json, "re": _re,
    }

    # Secret and variable accessors (read-only, never expose values in logs)
    def _get_secret(key: str, user_id: str = "anonymous") -> str:
        """Get a decrypted secret by name. Usage: get_secret('my_api_key')"""
        from pathlib import Path
        import json as _j
        secrets_path = Path("config/agent_secrets.json")
        if not secrets_path.exists():
            raise KeyError(f"Secret '{key}' not found")
        data = _j.loads(secrets_path.read_text(encoding="utf-8"))
        namespaced = f"{user_id}.{key}"
        entry = data.get(namespaced)
        if entry is None:
            raise KeyError(f"Secret '{key}' not found for user '{user_id}'")
        encrypted = entry.get("value", "") if isinstance(entry, dict) else entry
        from core.secrets import get_secrets_manager
        return get_secrets_manager().decrypt(encrypted)

    def _get_variable(key: str, user_id: str = "anonymous") -> str:
        """Get a stored variable by name. Usage: get_variable('my_var')"""
        from pathlib import Path
        import json as _j
        var_path = Path("config/agent_variables.json")
        if not var_path.exists():
            raise KeyError(f"Variable '{key}' not found")
        data = _j.loads(var_path.read_text(encoding="utf-8"))
        namespaced = f"{user_id}.{key}"
        entry = data.get(namespaced)
        if entry is None:
            raise KeyError(f"Variable '{key}' not found for user '{user_id}'")
        return entry.get("value", "") if isinstance(entry, dict) else str(entry)

    safe_builtins["get_secret"] = _get_secret
    safe_builtins["get_variable"] = _get_variable

    globals_dict = {"__builtins__": safe_builtins}

    if extra_vars:
        globals_dict.update(extra_vars)

    return globals_dict, print_buf


def execute_sandboxed(
    code: str,
    local_vars: Optional[Dict[str, Any]] = None,
    base_url: str = "http://localhost:9090",
    vfs: Optional[Dict[str, bytes]] = None,
) -> tuple:
    """Execute code in the sandbox.

    Args:
        code: Python code to execute.
        local_vars: Variables to inject into the namespace (e.g. flowfile).
        base_url: Base URL for file download links.
        vfs: Shared in-memory virtual filesystem.

    Returns:
        (output_str, created_files_list, namespace_dict)

    Raises:
        Exception on execution error (caller should catch).
    """
    if vfs is None:
        vfs = {}
    created_files: List[str] = []

    sandbox_open = make_sandbox_open(
        base_url=base_url,
        created_files=created_files, vfs=vfs,
    )

    globals_dict, print_buf = build_sandbox_globals(
        sandbox_open=sandbox_open,
    )

    namespace = dict(local_vars or {})

    # Try eval first (expression), fallback to exec (statements)
    try:
        result = eval(code, globals_dict, namespace)
        output = str(result)
    except SyntaxError:
        exec(code, globals_dict, namespace)
        if "result" in namespace:
            output = str(namespace["result"])
        elif print_buf:
            output = "".join(print_buf).rstrip()
        else:
            output = ""

    return output, created_files, namespace
