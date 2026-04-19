"""Shared filesystem actions for HTTP and WS relays.

All actions take (root_dir, abs_path, req) and return a dict result.
The relay is responsible for path resolution and access control.
"""

import base64
import json
import os
import re
import subprocess
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# Ensure tools/ is on sys.path so bare imports work from project root (tests)
_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir not in _sys.path:
    _sys.path.insert(0, _tools_dir)

# Size limits (prevent memory explosion)
MAX_FILE_SIZE = 50 * 1024 * 1024    # 50 MB for read/write

# Shared utilities (extracted to break circular import with fs_exec)
from fs_common import (
    MAX_EXEC_OUTPUT, _docker_cmd, _translate_path, _to_host_path,
    detect_available_shells, _resolve_shell,
)

# Actions that require write access
WRITE_ACTIONS = frozenset({
    "write_file", "delete_file", "mkdir", "find_replace", "edit",
    "batch_edit", "apply_patch",
    "exec", "exec_stream",
    "edit_notebook",
    "project_init",
    "screen_click", "screen_double_click", "screen_type",
    "screen_key", "screen_move", "screen_scroll",
})

# All available actions — populated at bottom of file after imports
# (referenced here for documentation; actual dict defined below)


def _rel(abs_path: str, root: str) -> str:
    """Convert absolute path back to relative for responses."""
    try:
        return str(Path(abs_path).relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return abs_path


def action_list_dir(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    p = Path(path)
    entries = []
    for entry in sorted(p.iterdir()):
        st = entry.stat()
        entries.append({
            "name": entry.name,
            "kind": "directory" if entry.is_dir() else "file",
            "size": st.st_size if entry.is_file() else 0,
            "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        })
    return entries


def action_project_context(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Auto-scan project root and build a context summary."""
    root = Path(root_dir)
    context = {"root": root_dir, "files": [], "config_files": {}, "tree": ""}

    # List top-level entries (max 100)
    try:
        entries = sorted(root.iterdir())[:100]
        context["files"] = [
            {"name": e.name, "kind": "dir" if e.is_dir() else "file",
             "size": e.stat().st_size if e.is_file() else 0}
            for e in entries
        ]
    except Exception:
        pass

    # Read key config/context files
    _KEY_FILES = [
        ".pawflow.md", "CLAUDE.md", "README.md", "readme.md",
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
        "Makefile", "Dockerfile", "docker-compose.yml",
        ".gitignore", "requirements.txt", "setup.py", "setup.cfg",
        "tsconfig.json", "pom.xml", "build.gradle",
    ]
    for fname in _KEY_FILES:
        fpath = root / fname
        if fpath.is_file():
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
                # Cap at 5KB per file for the summary
                context["config_files"][fname] = text[:5000]
            except Exception:
                pass

    # Build a tree (2 levels deep, max 200 entries)
    tree_lines = []
    count = 0
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") and entry.name not in (".gitignore", ".pawflow.md"):
            continue
        if count >= 200:
            tree_lines.append("... (more files)")
            break
        prefix = "📁 " if entry.is_dir() else "📄 "
        tree_lines.append(f"{prefix}{entry.name}")
        count += 1
        if entry.is_dir():
            try:
                for sub in sorted(entry.iterdir())[:20]:
                    sub_prefix = "  📁 " if sub.is_dir() else "  📄 "
                    tree_lines.append(f"{sub_prefix}{sub.name}")
                    count += 1
                    if count >= 200:
                        break
                if len(list(entry.iterdir())) > 20:
                    tree_lines.append(f"  ... (+{len(list(entry.iterdir())) - 20} more)")
            except PermissionError:
                pass
    context["tree"] = "\n".join(tree_lines)

    # Detect project type
    types = []
    names = {e.name for e in root.iterdir() if e.is_file()}
    if "package.json" in names: types.append("Node.js")
    if "pyproject.toml" in names or "setup.py" in names or "requirements.txt" in names: types.append("Python")
    if "Cargo.toml" in names: types.append("Rust")
    if "go.mod" in names: types.append("Go")
    if "pom.xml" in names or "build.gradle" in names: types.append("Java")
    if "Makefile" in names: types.append("Make")
    if "Dockerfile" in names: types.append("Docker")
    context["project_types"] = types

    # Git info
    git_dir = root / ".git"
    if git_dir.is_dir():
        context["git"] = True
        try:
            import subprocess
            br = subprocess.run(["git", "branch", "--show-current"],
                                cwd=root_dir, capture_output=True, text=True, timeout=10)
            context["git_branch"] = br.stdout.strip()
        except Exception:
            pass

    return context


def action_read_file(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    max_size = req.get("max_size", MAX_FILE_SIZE)
    size = Path(path).stat().st_size
    if size > max_size:
        raise ValueError(f"File too large ({size} bytes, max {max_size}). Use read_file_chunked.")
    content = Path(path).read_bytes()
    return {"content": base64.b64encode(content).decode("ascii"), "size": len(content)}


def action_read_pdf(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Extract text from a PDF file. Requires pypdf or PyPDF2."""
    max_pages = req.get("max_pages", 50)
    p = Path(path)
    if not p.suffix.lower() == ".pdf":
        raise ValueError(f"Not a PDF file: {p.name}")

    # Try pypdf (modern), then PyPDF2 (legacy), then raw fallback
    text_pages = []
    try:
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        reader = PdfReader(str(p))
        total = len(reader.pages)
        for i, page in enumerate(reader.pages[:max_pages]):
            page_text = page.extract_text() or ""
            text_pages.append({"page": i + 1, "text": page_text})
        return {
            "pages": text_pages,
            "total_pages": total,
            "extracted_pages": min(total, max_pages),
        }
    except ImportError:
        # No PDF library — return base64 raw content
        content = p.read_bytes()
        return {
            "error": "No PDF library installed (pip install pypdf). Returning raw base64.",
            "content": base64.b64encode(content).decode("ascii"),
            "size": len(content),
        }


def action_read_notebook(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Extract content from a Jupyter notebook (.ipynb)."""
    p = Path(path)
    if not p.suffix.lower() == ".ipynb":
        raise ValueError(f"Not a notebook: {p.name}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    cells = raw.get("cells", [])
    metadata = raw.get("metadata", {})
    kernel = metadata.get("kernelspec", {}).get("display_name", "")
    result_cells = []
    for i, cell in enumerate(cells):
        cell_type = cell.get("cell_type", "")
        source = "".join(cell.get("source", []))
        outputs_text = ""
        for out in cell.get("outputs", []):
            if "text" in out:
                outputs_text += "".join(out["text"])
            elif "data" in out:
                # Prefer text/plain, then text/html
                data = out["data"]
                if "text/plain" in data:
                    outputs_text += "".join(data["text/plain"])
                elif "text/html" in data:
                    outputs_text += "[HTML output]"
                elif "image/png" in data:
                    outputs_text += "[Image output]"
            if out.get("ename"):
                outputs_text += f"Error: {out['ename']}: {out.get('evalue', '')}"
        result_cells.append({
            "index": i,
            "type": cell_type,
            "source": source,
            "output": outputs_text,
        })
    return {
        "kernel": kernel,
        "total_cells": len(cells),
        "cells": result_cells,
    }


def action_edit_notebook(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Edit, insert, or delete a cell in a Jupyter notebook."""
    p = Path(path)
    if not p.suffix.lower() == ".ipynb":
        raise ValueError(f"Not a notebook: {p.name}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    cells = raw.get("cells", [])
    operation = req.get("operation", "edit")
    cell_index = req.get("cell_index")
    if cell_index is None:
        raise ValueError("Missing 'cell_index' parameter")

    if operation == "delete":
        if cell_index < 0 or cell_index >= len(cells):
            raise ValueError(f"cell_index {cell_index} out of range (0-{len(cells)-1})")
        deleted = cells.pop(cell_index)
        raw["cells"] = cells
        p.write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")
        return {"operation": "delete", "cell_index": cell_index, "deleted_type": deleted.get("cell_type", "")}

    new_source = req.get("new_source", "")
    cell_type = req.get("cell_type", "code")

    if operation == "insert":
        if cell_index < 0 or cell_index > len(cells):
            raise ValueError(f"cell_index {cell_index} out of range for insert (0-{len(cells)})")
        new_cell = {
            "cell_type": cell_type,
            "metadata": {},
            "source": new_source.splitlines(True),
            "outputs": [] if cell_type == "code" else [],
        }
        if cell_type == "code":
            new_cell["execution_count"] = None
        cells.insert(cell_index, new_cell)
    elif operation == "edit":
        if cell_index < 0 or cell_index >= len(cells):
            raise ValueError(f"cell_index {cell_index} out of range (0-{len(cells)-1})")
        cells[cell_index]["source"] = new_source.splitlines(True)
        if cell_type:
            cells[cell_index]["cell_type"] = cell_type
    else:
        raise ValueError(f"Unknown operation: {operation}. Use 'edit', 'insert', or 'delete'.")

    raw["cells"] = cells
    p.write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"operation": operation, "cell_index": cell_index, "total_cells": len(cells)}


def action_write_file(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    content = req.get("content", "")
    raw = base64.b64decode(content) if req.get("base64") else content.encode("utf-8")
    if len(raw) > MAX_FILE_SIZE:
        raise ValueError(f"Content too large ({len(raw)} bytes, max {MAX_FILE_SIZE}). Use write_file_chunked.")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    return {"written": len(raw), "path": _rel(path, root_dir)}


def action_delete_file(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    p = Path(path)
    if p.is_dir():
        import shutil
        shutil.rmtree(p)
    else:
        p.unlink()
    return {"deleted": _rel(path, root_dir)}


def action_mkdir(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    Path(path).mkdir(parents=True, exist_ok=True)
    return {"created": _rel(path, root_dir)}


def action_stat(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    p = Path(path)
    st = p.stat()
    return {
        "name": p.name,
        "kind": "directory" if p.is_dir() else "file",
        "size": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "created": datetime.fromtimestamp(st.st_ctime, tz=timezone.utc).isoformat(),
    }


def action_exists(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    return {"exists": Path(path).exists()}


def action_search(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    pattern = req.get("pattern", "*")
    recursive = req.get("recursive", True)
    p = Path(path)
    if recursive:
        matches = [str(m.relative_to(p)).replace("\\", "/") for m in p.rglob(pattern)]
    else:
        matches = [str(m.relative_to(p)).replace("\\", "/") for m in p.glob(pattern)]
    return matches[:500]


_GREP_SKIP_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", "node_modules",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "dist", "build", ".next", ".cache",
}
_GREP_SKIP_EXT = {
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".exe",
    ".bin", ".o", ".a", ".class", ".jar", ".war",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".ogg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".coverage", ".lock",
}


def _grep_is_binary(fpath: Path) -> bool:
    """Sniff the first 4KB for NUL bytes — ripgrep's binary detection rule."""
    try:
        with open(fpath, "rb") as f:
            chunk = f.read(4096)
        return b"\x00" in chunk
    except OSError:
        return True


def action_grep(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    regex = req.get("regex", "")
    recursive = req.get("recursive", True)
    if not regex:
        raise ValueError("Missing 'regex' parameter")
    # Suppress Python 3.12+ FutureWarning for user-supplied regex quirks
    # (e.g. `[[..]]` patterns flagged as possible nested sets). The regex
    # still compiles correctly — we just don't want the warning to pollute
    # the relay log on every grep call.
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore", FutureWarning)
        compiled = re.compile(regex, re.IGNORECASE)
    p = Path(path)
    results = []

    def _scan_file(fpath: Path, display_name: str):
        if fpath.suffix.lower() in _GREP_SKIP_EXT:
            return
        if _grep_is_binary(fpath):
            return
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        for i, line in enumerate(text.splitlines(), 1):
            if compiled.search(line):
                results.append({
                    "path": display_name,
                    "line_number": i,
                    "line": line[:500],
                })
                if len(results) >= 200:
                    return

    if p.is_file():
        _scan_file(p, p.name)
        return results

    glob_pattern = "**/*" if recursive else "*"
    for fpath in p.glob(glob_pattern):
        if not fpath.is_file():
            continue
        # Skip any path whose parents include an ignored dir.
        if any(part in _GREP_SKIP_DIRS for part in fpath.parts):
            continue
        _scan_file(fpath, str(fpath.relative_to(p)).replace("\\", "/"))
        if len(results) >= 200:
            break
    return results


def action_find_replace(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    pattern = req.get("pattern", "")
    replacement = req.get("replacement", "")
    if not pattern:
        raise ValueError("Missing 'pattern' parameter")
    compiled = re.compile(pattern)
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    new_text, count = compiled.subn(replacement, text)
    if count > 0:
        p.write_text(new_text, encoding="utf-8")
    return {"replacements": count, "path": _rel(path, root_dir)}


def _diagnose_edit_mismatch(old_string: str, text: str, filename: str) -> str:
    """Build an actionable error message explaining WHY old_string doesn't match.

    Emits several hints that cover the common causes agents hit repeatedly:
    CRLF vs LF, trailing whitespace, tab/space indentation, and a best-effort
    longest-prefix match pointing at the exact divergence position. The goal
    is to replace 5 useless retries with a single corrective read.
    """
    hints = []

    # CRLF vs LF mismatch
    if '\r\n' in text and '\r\n' not in old_string:
        if old_string.replace('\n', '\r\n') in text:
            hints.append(
                "File uses CRLF line endings; your old_string uses LF. "
                "Re-send old_string with \\r\\n between lines.")
    elif '\r\n' in old_string and '\r\n' not in text:
        if old_string.replace('\r\n', '\n') in text:
            hints.append(
                "File uses LF line endings; your old_string has CRLF. "
                "Strip the \\r from line endings in old_string.")

    # Trailing whitespace mismatch (either direction) — only emit if no
    # more specific hint already covers it. CRLF and tab/space mismatches
    # also make rstripped content match, but their hints are more actionable.
    _specific_hint = bool(hints)
    old_rstripped = '\n'.join(l.rstrip() for l in old_string.split('\n'))
    text_rstripped = '\n'.join(l.rstrip() for l in text.split('\n'))
    if not _specific_hint and old_rstripped in text_rstripped:
        hints.append(
            "Content matches after rstripping each line — trailing whitespace "
            "differs between your old_string and the file. Re-read the target "
            "lines and copy them verbatim (cat -A or repr() to see exact bytes).")

    # Tabs vs spaces
    if '\t' in text and '\t' not in old_string:
        # Guess the indent width that turns spaces into tabs
        for _w in (4, 2, 8):
            _swapped = old_string.replace(' ' * _w, '\t')
            if _swapped in text:
                hints.append(
                    f"File uses tabs for indentation; your old_string uses "
                    f"{_w}-space indent. Convert runs of {_w} spaces to tabs.")
                break
    elif '\t' in old_string and '\t' not in text:
        for _w in (4, 2, 8):
            _swapped = old_string.replace('\t', ' ' * _w)
            if _swapped in text:
                hints.append(
                    f"File uses spaces for indentation ({_w}-wide); your "
                    f"old_string has tabs. Replace each \\t with {_w} spaces.")
                break

    # Longest-prefix match — where does old_string start diverging?
    _first_line = old_string.split('\n', 1)[0]
    if len(_first_line) >= 8:
        best_prefix = 0
        best_pos = -1
        _pos = 0
        while True:
            _pos = text.find(_first_line, _pos)
            if _pos < 0:
                break
            _mlen = 0
            _stop = min(len(old_string), len(text) - _pos)
            while _mlen < _stop and text[_pos + _mlen] == old_string[_mlen]:
                _mlen += 1
            if _mlen > best_prefix:
                best_prefix = _mlen
                best_pos = _pos
            _pos += 1
        if best_pos >= 0 and best_prefix >= len(_first_line):
            _line_num = text[:best_pos].count('\n') + 1
            _diverge_line = old_string[:best_prefix].count('\n') + 1
            _old_tail = old_string[best_prefix:best_prefix + 60].replace('\n', '\\n')
            _file_tail = text[best_pos + best_prefix:best_pos + best_prefix + 60].replace('\n', '\\n')
            hints.append(
                f"Partial match starts at file line {_line_num}, "
                f"diverges on line {_diverge_line} of old_string "
                f"(after {best_prefix} chars). "
                f"You sent: {_old_tail!r} | File has: {_file_tail!r}")

    if not hints:
        hints.append(
            "No similar content found anywhere in the file. "
            "Re-read the exact lines you want to edit before retrying.")

    return (f"old_string not found in {filename}.\n  - "
            + "\n  - ".join(hints)
            + "\n\nDo NOT retry with the same old_string. "
            "Read the file at the expected line range and copy the exact bytes.")


def action_edit(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Edit a file, either by exact string replacement or by line range.

    Two mutually-exclusive modes (matches EditHandler's JSON schema):
      - string-based: req has `old_string` + `new_string` (+ `replace_all`)
      - line-based:   req has `start_line` + `end_line` + `new_string`
                      (1-based, inclusive end)

    Previously only string-based was implemented; a line-based request
    (no `old_string` in the payload) crashed with
      "Missing 'old_string' parameter"
    even though EditHandler advertises the line-based API and routes to
    it. The line-based branch originally lived in the pawflow_relay.py
    dispatcher copy, which had become dead code and was removed — but the
    fs_actions copy (the one actually reached by the relay) never got it.
    """
    new_string = req.get("new_string", "")
    start_line = int(req.get("start_line", 0) or 0)
    end_line = int(req.get("end_line", 0) or 0)
    p = Path(path)

    if start_line > 0 and end_line > 0:
        text = p.read_text(encoding="utf-8")
        lines = text.split("\n")
        if start_line > len(lines) or end_line < start_line:
            raise ValueError(
                f"Invalid line range {start_line}-{end_line} for file "
                f"{p.name} ({len(lines)} lines)")
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)
        removed = lines[s:e]
        new_lines = new_string.split("\n")
        lines[s:e] = new_lines
        p.write_text("\n".join(lines), encoding="utf-8")
        return {
            "lines_replaced": f"{start_line}-{end_line}",
            "lines_removed": len(removed),
            "lines_inserted": len(new_lines),
            "path": _rel(path, root_dir),
        }

    old_string = req.get("old_string", "")
    replace_all = req.get("replace_all", False)
    if not old_string:
        raise ValueError(
            "Missing 'old_string' parameter (or provide start_line/end_line "
            "for a line-based edit)")
    text = p.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        raise ValueError(_diagnose_edit_mismatch(old_string, text, p.name))
    if count > 1 and not replace_all:
        raise ValueError(f"old_string found {count} times (use replace_all=true)")

    # Build diff context (±3 lines around the first replacement)
    lines = text.splitlines(True)
    diff_lines = []
    old_lines = old_string.splitlines(True)
    new_lines = new_string.splitlines(True)
    # Find line number of first occurrence
    pos = text.find(old_string)
    line_num = text[:pos].count("\n") + 1 if pos >= 0 else 0
    ctx_start = max(0, line_num - 4)
    ctx_end = min(len(lines), line_num + len(old_lines) + 3)
    for i in range(ctx_start, min(ctx_end, len(lines))):
        in_old = line_num - 1 <= i < line_num - 1 + len(old_lines)
        diff_lines.append({"line": i + 1, "text": lines[i].rstrip("\n\r"),
                           "type": "remove" if in_old else "context"})
    for j, nl in enumerate(new_lines):
        diff_lines.append({"line": line_num + j, "text": nl.rstrip("\n\r"),
                           "type": "add"})

    # Apply replacement
    if replace_all:
        new_text = text.replace(old_string, new_string)
    else:
        new_text = text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    return {
        "replacements": count if replace_all else 1,
        "path": _rel(path, root_dir),
        "diff": diff_lines,
        "line": line_num,
    }


def action_batch_edit(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Apply multiple edits atomically across files."""
    edits = req.get("edits", [])
    if not edits:
        raise ValueError("Missing 'edits' parameter (list of {path, old_string, new_string})")

    # Phase 1: Read all files and validate
    file_contents = {}
    for i, edit in enumerate(edits):
        fpath = edit.get("path", "")
        if not fpath:
            raise ValueError(f"Edit {i}: missing 'path'")
        abs_path = str(Path(root_dir).resolve() / fpath)
        old_string = edit.get("old_string", "")
        if not old_string:
            raise ValueError(f"Edit {i}: missing 'old_string'")
        if abs_path not in file_contents:
            p = Path(abs_path)
            if not p.is_file():
                raise ValueError(f"Edit {i}: file not found: {fpath}")
            file_contents[abs_path] = p.read_text(encoding="utf-8")
        text = file_contents[abs_path]
        count = text.count(old_string)
        if count == 0:
            raise ValueError(f"Edit {i}: old_string not found in {fpath}")
        if count > 1:
            raise ValueError(f"Edit {i}: old_string found {count} times in {fpath} (must be unique)")

    # Phase 2: Apply all edits in memory
    for edit in edits:
        abs_path = str(Path(root_dir).resolve() / edit["path"])
        file_contents[abs_path] = file_contents[abs_path].replace(
            edit["old_string"], edit.get("new_string", ""), 1)

    # Phase 3: Write all files
    for abs_path, content in file_contents.items():
        Path(abs_path).write_text(content, encoding="utf-8")

    modified = list(set(str(Path(ap).relative_to(Path(root_dir).resolve())).replace("\\", "/")
                        for ap in file_contents))
    return {"edits_applied": len(edits), "files_modified": modified}


def action_apply_patch(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Apply a unified diff patch."""
    patch = req.get("patch", "")
    if not patch:
        raise ValueError("Missing 'patch' parameter")

    # Try git apply first
    try:
        result = subprocess.run(
            ["git", "apply", "--stat", "-"],
            input=patch, cwd=root_dir,
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            stat_output = result.stdout
            # Actually apply
            result = subprocess.run(
                ["git", "apply", "-"],
                input=patch, cwd=root_dir,
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return {"method": "git_apply", "stats": stat_output.strip(), "applied": True}
            else:
                raise ValueError(f"git apply failed: {result.stderr}")
    except FileNotFoundError:
        pass  # git not available, fall through to manual
    except subprocess.TimeoutExpired:
        raise ValueError("Patch application timed out")

    # Manual fallback: parse unified diff
    files_modified = []
    current_file = None
    current_content = None
    hunks_applied = 0

    lines = patch.splitlines(True)
    i = 0
    while i < len(lines):
        line = lines[i]
        # New file header
        if line.startswith("+++ b/") or line.startswith("+++ "):
            if current_file and current_content is not None:
                Path(current_file).write_text(current_content, encoding="utf-8")
            fname = line[6:].strip() if line.startswith("+++ b/") else line[4:].strip()
            current_file = str(Path(root_dir) / fname)
            p = Path(current_file)
            if p.is_file():
                current_content = p.read_text(encoding="utf-8")
            else:
                current_content = ""
            files_modified.append(fname)
            i += 1
            continue
        if line.startswith("--- "):
            i += 1
            continue
        # Hunk header
        if line.startswith("@@") and current_content is not None:
            import re as _re
            m = _re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if not m:
                i += 1
                continue
            orig_start = int(m.group(1)) - 1
            content_lines = current_content.splitlines(True)
            # Ensure content_lines has enough entries
            while len(content_lines) <= orig_start:
                content_lines.append("")
            j = orig_start
            i += 1
            while i < len(lines):
                dl = lines[i]
                if dl.startswith("@@") or dl.startswith("diff ") or dl.startswith("--- ") or dl.startswith("+++ "):
                    break
                if dl.startswith("-"):
                    if j < len(content_lines):
                        content_lines.pop(j)
                elif dl.startswith("+"):
                    content_lines.insert(j, dl[1:])
                    j += 1
                else:  # context line
                    j += 1
                i += 1
            current_content = "".join(content_lines)
            hunks_applied += 1
            continue
        i += 1

    # Write last file
    if current_file and current_content is not None:
        Path(current_file).write_text(current_content, encoding="utf-8")

    return {"method": "manual", "files_modified": files_modified, "hunks_applied": hunks_applied, "applied": True}





def action_project_init(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Generate a .pawflow.md project description from auto-scan."""
    target = Path(root_dir) / ".pawflow.md"
    if target.exists() and not req.get("force", False):
        raise ValueError(".pawflow.md already exists. Use force=true to overwrite.")

    # Use project_context to gather info
    ctx = action_project_context(root_dir, root_dir, {})

    lines = ["# Project Context\n"]

    if ctx.get("project_types"):
        lines.append(f"**Type:** {', '.join(ctx['project_types'])}\n")

    if ctx.get("git_branch"):
        lines.append(f"**Git branch:** {ctx['git_branch']}\n")

    lines.append("\n## Structure\n")
    if ctx.get("tree"):
        lines.append(f"```\n{ctx['tree'][:3000]}\n```\n")

    # Include key config snippets
    for fname in ("README.md", "readme.md"):
        if fname in ctx.get("config_files", {}):
            excerpt = ctx["config_files"][fname][:1500]
            lines.append(f"\n## README (excerpt)\n\n{excerpt}\n")
            break

    lines.append("\n## Instructions\n\n")
    lines.append("<!-- Add project-specific instructions for the AI agent here -->\n")
    lines.append("<!-- Example: coding conventions, architecture notes, test commands -->\n")

    content = "\n".join(lines)
    target.write_text(content, encoding="utf-8")
    return {"path": ".pawflow.md", "size": len(content)}


# ── Chunked read/write (for large files) ─────────────────────────

CHUNK_SIZE = 1024 * 1024  # 1 MB per chunk


def action_read_file_chunked(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Read a file in chunks. Returns first chunk + metadata.

    The caller must handle {"type": "chunked", "total_size": N, "chunk_size": M,
    "total_chunks": K, "chunk_index": 0, "data": base64, "path": relpath}.
    Subsequent chunks are requested with action=read_chunk, index=N.
    """
    chunk_size = req.get("chunk_size", CHUNK_SIZE)
    p = Path(path)
    total_size = p.stat().st_size
    total_chunks = (total_size + chunk_size - 1) // chunk_size
    # Read first chunk
    with open(path, "rb") as f:
        data = f.read(chunk_size)
    return {
        "type": "chunked",
        "total_size": total_size,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "chunk_index": 0,
        "data": base64.b64encode(data).decode("ascii"),
        "path": _rel(path, root_dir),
    }


def action_read_chunk(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Read a specific chunk of a file by index."""
    chunk_size = req.get("chunk_size", CHUNK_SIZE)
    index = req.get("index", 0)
    offset = index * chunk_size
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(chunk_size)
    return {
        "chunk_index": index,
        "data": base64.b64encode(data).decode("ascii"),
        "size": len(data),
        "done": len(data) < chunk_size,
    }


def action_write_file_chunked(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Write a chunk to a file. First chunk creates/truncates, subsequent append.

    req: {"chunk_index": 0, "data": base64, "done": false}
    Last chunk has "done": true.
    """
    chunk_data = base64.b64decode(req.get("data", ""))
    chunk_index = req.get("index", 0)
    done = req.get("done", False)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if chunk_index == 0 else "ab"
    with open(path, mode) as f:
        f.write(chunk_data)
    result = {"chunk_index": chunk_index, "written": len(chunk_data)}
    if done:
        result["total_written"] = p.stat().st_size
        result["path"] = _rel(path, root_dir)
    return result


# ── Action registry ──────────────────────────────────────────────
# Import split-out modules
from fs_exec import action_exec, action_exec_stream
from fs_screen import (
    action_screen_screenshot, action_screen_click, action_screen_double_click,
    action_screen_triple_click, action_screen_right_click,
    action_screen_type, action_screen_key, action_screen_move,
    action_screen_scroll, action_screen_mouse_position, action_screen_drag,
    action_screen_screenshot_region, action_screen_size, action_screen_wait,
    action_screen_open_app,
    action_screen_clipboard_read, action_screen_clipboard_write,
    action_screen_window_list, action_screen_window_focus,
    action_screen_window_close, action_screen_window_resize,
    action_screen_window_minimize, action_screen_window_maximize,
    action_screen_ocr, action_screen_locate,
)
from fs_mcp import (
    action_mcp_start, action_mcp_discover, action_mcp_call,
    action_mcp_stop, action_mcp_list,
)
from fs_http import action_http_fetch

ACTIONS = {
    "list_dir": action_list_dir,
    "project_context": action_project_context,
    "read_file": action_read_file,
    "read_pdf": action_read_pdf,
    "read_notebook": action_read_notebook,
    "write_file": action_write_file,
    "delete_file": action_delete_file,
    "mkdir": action_mkdir,
    "stat": action_stat,
    "exists": action_exists,
    "search": action_search,
    "grep": action_grep,
    "find_replace": action_find_replace,
    "edit": action_edit,
    "batch_edit": action_batch_edit,
    "apply_patch": action_apply_patch,
    "exec": action_exec,
    "exec_stream": action_exec_stream,
    "http_fetch": action_http_fetch,
    "read_file_chunked": action_read_file_chunked,
    "read_chunk": action_read_chunk,
    "write_file_chunked": action_write_file_chunked,
    "project_init": action_project_init,
    "edit_notebook": action_edit_notebook,
    "screen_screenshot": action_screen_screenshot,
    "screen_screenshot_region": action_screen_screenshot_region,
    "screen_click": action_screen_click,
    "screen_double_click": action_screen_double_click,
    "screen_triple_click": action_screen_triple_click,
    "screen_right_click": action_screen_right_click,
    "screen_type": action_screen_type,
    "screen_key": action_screen_key,
    "screen_move": action_screen_move,
    "screen_drag": action_screen_drag,
    "screen_scroll": action_screen_scroll,
    "screen_cursor_position": action_screen_mouse_position,
    "screen_size": action_screen_size,
    "screen_wait": action_screen_wait,
    "screen_open_app": action_screen_open_app,
    "screen_clipboard_read": action_screen_clipboard_read,
    "screen_clipboard_write": action_screen_clipboard_write,
    "screen_window_list": action_screen_window_list,
    "screen_window_focus": action_screen_window_focus,
    "screen_window_close": action_screen_window_close,
    "screen_window_resize": action_screen_window_resize,
    "screen_window_minimize": action_screen_window_minimize,
    "screen_window_maximize": action_screen_window_maximize,
    "screen_ocr": action_screen_ocr,
    "screen_locate": action_screen_locate,
    "mcp_start": action_mcp_start,
    "mcp_call": action_mcp_call,
    "mcp_discover": action_mcp_discover,
    "mcp_stop": action_mcp_stop,
    "mcp_list": action_mcp_list,
}
