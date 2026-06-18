"""Shared path-resolution and glob helpers for the fs_* action modules.

Extracted from fs_actions.py so the read/grep/edit action modules can share
them without importing each other (avoids circular imports).
"""
from pathlib import Path
from typing import List

# Size limits (prevent memory explosion)
MAX_FILE_SIZE = 50 * 1024 * 1024    # 50 MB for read/write


def _is_windows_drive_absolute_path(path: str) -> bool:
    raw = str(path or "").replace("\\", "/")
    return len(raw) >= 3 and raw[1] == ":" and raw[2] == "/"


def _is_host_absolute_path(path: str) -> bool:
    raw = str(path or "").replace("\\", "/")
    return raw.startswith("/") or raw.startswith("//") or _is_windows_drive_absolute_path(raw)


def _resolve_tool_path(root_dir: str, raw_path: str, *, allow_host_absolute: bool = False) -> Path:
    root = Path(root_dir).resolve()
    raw = str(raw_path or ".")
    if raw.startswith("/workspace/"):
        raw = raw[len("/workspace/"):]
    elif raw == "/workspace":
        return root
    if _is_host_absolute_path(raw):
        if allow_host_absolute:
            return Path(raw).resolve()
        raise ValueError(f"Path escapes workspace: {raw_path}")
    target = (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {raw_path}") from exc
    return target


def _rel(abs_path: str, root: str) -> str:
    """Convert absolute path back to relative for responses."""
    try:
        return str(Path(abs_path).relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return abs_path


def _expand_glob_braces(pattern: str, max_patterns: int = 256) -> List[str]:
    """Expand shell-style glob braces without invoking a shell.

    pathlib glob/rglob supports `**` and character classes but not `{a,b}`.
    PawFlow tools accept patterns like `{core,services}/**/*.py`, so expand
    braces before passing each concrete pattern to pathlib.
    """
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
