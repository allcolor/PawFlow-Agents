"""Shared filesystem actions for HTTP and WS relays.

All actions take (root_dir, abs_path, req) and return a dict result.
The relay is responsible for path resolution and access control.
"""

import base64
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# Size limits (prevent memory explosion)
MAX_FILE_SIZE = 50 * 1024 * 1024    # 50 MB for read/write
MAX_EXEC_OUTPUT = 10 * 1024 * 1024  # 10 MB for stdout/stderr


# ── Shell detection ──────────────────────────────────────────────────

import shutil as _shutil

def detect_available_shells() -> Dict[str, str]:
    """Detect available shells on this system. Returns {name: path}."""
    shells: Dict[str, str] = {}
    if os.name == "nt":
        # Windows shells
        _cmd = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
        if os.path.isfile(_cmd):
            shells["cmd"] = _cmd
        for _ps in ("pwsh", "powershell"):
            _p = _shutil.which(_ps)
            if _p:
                shells["powershell"] = _p
                break
        # Git Bash: lives in Git\bin\bash.exe, NOT in PATH by default
        _git_bash = None
        _git = _shutil.which("git")
        if _git:
            _git_bin = str(Path(_git).resolve().parent.parent / "bin" / "bash.exe")
            if os.path.isfile(_git_bin):
                _git_bash = _git_bin
        if not _git_bash:
            # Fallback: common install locations
            for _gb in (r"C:\Program Files\Git\bin\bash.exe",
                        r"C:\Program Files (x86)\Git\bin\bash.exe"):
                if os.path.isfile(_gb):
                    _git_bash = _gb
                    break
        if _git_bash:
            shells["bash"] = _git_bash
        # WSL bash: system32\bash.exe
        _wsl_bash = _shutil.which("bash")
        if _wsl_bash:
            _wbl = _wsl_bash.lower().replace("\\", "/")
            if "system32" in _wbl or "wsl" in _wbl:
                shells["wsl"] = _wsl_bash
            elif not _git_bash:
                # Unknown bash — register as generic
                shells["bash"] = _wsl_bash
    else:
        # Unix shells
        for _sh in ("bash", "sh", "zsh", "fish"):
            _p = _shutil.which(_sh)
            if _p:
                shells[_sh] = _p
    # Interpreters (cross-platform)
    for _interp in ("python", "python3", "node"):
        _p = _shutil.which(_interp)
        if _p:
            shells[_interp] = _p
    return shells


def _resolve_shell(name: str) -> str:
    """Resolve a shell name to its executable path. Returns '' if not found."""
    shells = detect_available_shells()
    # Exact match
    if name in shells:
        return shells[name]
    # Fuzzy match (powershell = pwsh, py = python, etc.)
    _aliases = {"ps": "powershell", "pwsh": "powershell", "py": "python",
                "python3": "python", "js": "node"}
    canonical = _aliases.get(name.lower(), name.lower())
    return shells.get(canonical, "")

# Actions that require write access
WRITE_ACTIONS = frozenset({
    "write_file", "delete_file", "mkdir", "find_replace", "edit",
    "batch_edit", "apply_patch",
    "git_commit", "git_push", "exec",
    "edit_notebook", "git_worktree_add", "git_worktree_remove",
    "git_add", "git_reset", "git_stash", "git_branch",
    "git_merge", "git_rebase", "git_cherry_pick", "git_tag",
    "project_init",
    "screen_click", "screen_double_click", "screen_type",
    "screen_key", "screen_move", "screen_scroll",
})

# All available actions
ACTIONS = {}


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
                                cwd=root_dir, capture_output=True, text=True, timeout=5)
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


def action_grep(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    regex = req.get("regex", "")
    recursive = req.get("recursive", True)
    if not regex:
        raise ValueError("Missing 'regex' parameter")
    compiled = re.compile(regex, re.IGNORECASE)
    p = Path(path)
    results = []
    glob_pattern = "**/*" if recursive else "*"
    for fpath in p.glob(glob_pattern):
        if not fpath.is_file():
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    results.append({
                        "path": str(fpath.relative_to(p)).replace("\\", "/"),
                        "line_number": i,
                        "line": line[:500],
                    })
                    if len(results) >= 200:
                        return results
        except Exception:
            continue
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


def action_edit(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Exact string replacement with diff context."""
    old_string = req.get("old_string", "")
    new_string = req.get("new_string", "")
    replace_all = req.get("replace_all", False)
    if not old_string:
        raise ValueError("Missing 'old_string' parameter")
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        # Fuzzy fallback: try to find the closest match (whitespace-tolerant)
        import difflib
        old_lines = old_string.splitlines()
        text_lines = text.splitlines()
        # Try to find the best matching block
        matcher = difflib.SequenceMatcher(None,
            [l.strip() for l in old_lines],
            [l.strip() for l in text_lines])
        best = matcher.find_longest_match(0, len(old_lines), 0, len(text_lines))
        if best.size >= max(1, len(old_lines) * 0.6):
            # Found a fuzzy match — use the actual text from the file
            matched_lines = text_lines[best.b:best.b + best.size]
            actual_old = "\n".join(matched_lines)
            if actual_old in text:
                # Replace with the actual matched text
                count = 1
                old_string = actual_old
            else:
                raise ValueError(f"old_string not found in {p.name} (fuzzy match also failed)")
        else:
            raise ValueError(f"old_string not found in {p.name}")
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
                capture_output=True, text=True, timeout=30,
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


def action_exec(root_dir: str, path: str, req: Dict[str, Any], *,
                allow_exec: bool = False) -> Any:
    """Execute a shell command in the sandbox directory."""
    if not allow_exec:
        raise PermissionError("Shell execution disabled. Start relay with --allow-exec")
    command = req.get("command", "")
    timeout = min(req.get("timeout", 30), 120)
    shell_name = req.get("shell", "")  # optional: powershell, bash, python, node, cmd
    if not command:
        raise ValueError("Missing 'command' parameter")
    # Resolve fs:// URLs in the command to real local paths
    root_abs = str(Path(root_dir).resolve())
    _fs_url_pattern = re.compile(r'fs://[^/\s]+/(\S+)')
    command = _fs_url_pattern.sub(
        lambda m: str(Path(root_abs) / m.group(1)).replace("\\", "/"), command)
    # Force UTF-8 output from child process (Windows defaults to cp850/cp1252)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PAWFLOW_FS_ROOT"] = root_abs
    # Resolve shell executable
    executable = None
    if shell_name:
        executable = _resolve_shell(shell_name)
        if not executable:
            raise ValueError(f"Shell '{shell_name}' not found. "
                             f"Available: {', '.join(detect_available_shells().keys())}")
    if not executable and os.name == "nt":
        # Default: cmd.exe with UTF-8 codepage
        command = f"chcp 65001 >nul 2>&1 & {command}"
    result = subprocess.run(
        command, shell=True,
        executable=executable,
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=timeout,
        cwd=root_dir,
        env=env,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if len(stdout) > MAX_EXEC_OUTPUT:
        stdout = stdout[:MAX_EXEC_OUTPUT] + f"\n... (truncated, {len(result.stdout)} bytes total)"
    if len(stderr) > MAX_EXEC_OUTPUT:
        stderr = stderr[:MAX_EXEC_OUTPUT] + f"\n... (truncated, {len(result.stderr)} bytes total)"
    return {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": result.returncode,
    }


# ── Git actions ───────────────────────────────────────────────────

def _git_run(cwd, args, timeout=30):
    return subprocess.run(
        ["git"] + args, cwd=cwd,
        capture_output=True, text=True, timeout=timeout,
    )


def action_git_status(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    br = _git_run(path, ["branch", "--show-current"])
    branch = br.stdout.strip() or "HEAD"
    st = _git_run(path, ["status", "--porcelain"])
    staged, modified, untracked = [], [], []
    for line in st.stdout.splitlines():
        if len(line) < 3:
            continue
        x, y, fname = line[0], line[1], line[3:]
        if x in ("A", "M", "D", "R"):
            staged.append(fname)
        if y in ("M", "D"):
            modified.append(fname)
        if x == "?" and y == "?":
            untracked.append(fname)
    return {"branch": branch, "staged": staged, "modified": modified, "untracked": untracked}


def action_git_log(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    count = req.get("count", 10)
    result = _git_run(path, ["log", f"-{count}", "--format=%H|%ai|%s"])
    entries = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            entries.append({"hash": parts[0], "date": parts[1], "message": parts[2]})
    return entries


def action_git_diff(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    ref = req.get("ref", "")
    args = ["diff"]
    if ref:
        args.append(ref)
    result = _git_run(path, args)
    return {"diff": result.stdout[:50000]}


def action_git_commit(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    message = req.get("message", "")
    if not message:
        raise ValueError("Missing 'message' parameter")
    files = req.get("files", [])
    amend = req.get("amend", False)
    if files:
        _git_run(path, ["add", "--"] + files)
    else:
        _git_run(path, ["add", "-A"])
    args = ["commit", "-m", message]
    if amend:
        args = ["commit", "--amend", "-m", message]
    result = _git_run(path, args)
    return {"output": result.stdout, "hash": result.stdout.split()[1] if result.returncode == 0 else ""}


def action_git_pull(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    result = _git_run(path, ["pull"])
    return {"output": result.stdout, "error": result.stderr}


def action_git_push(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    result = _git_run(path, ["push"])
    return {"output": result.stdout, "error": result.stderr}


def action_git_checkout(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    ref = req.get("ref", "")
    if not ref:
        raise ValueError("Missing 'ref' parameter")
    result = _git_run(path, ["checkout", ref])
    return {"output": result.stdout, "branch": ref}


def action_git_add(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Stage specific files."""
    files = req.get("files", [])
    if not files:
        raise ValueError("Missing 'files' parameter (list of file paths)")
    result = _git_run(path, ["add", "--"] + files)
    if result.returncode != 0:
        raise ValueError(f"git add failed: {result.stderr}")
    return {"staged": files}


def action_git_reset(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Unstage files or reset to a ref."""
    files = req.get("files", [])
    ref = req.get("ref", "")
    if files:
        result = _git_run(path, ["reset", "HEAD", "--"] + files)
    elif ref:
        mode = req.get("mode", "mixed")  # mixed, soft, hard
        result = _git_run(path, ["reset", f"--{mode}", ref])
    else:
        result = _git_run(path, ["reset", "HEAD"])
    if result.returncode != 0:
        raise ValueError(f"git reset failed: {result.stderr}")
    return {"output": result.stdout.strip()}


def action_git_stash(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Git stash operations: push, pop, list, drop."""
    operation = req.get("operation", "push")
    if operation == "push":
        message = req.get("message", "")
        args = ["stash", "push"]
        if message:
            args += ["-m", message]
        result = _git_run(path, args)
    elif operation == "pop":
        result = _git_run(path, ["stash", "pop"])
    elif operation == "list":
        result = _git_run(path, ["stash", "list"])
    elif operation == "drop":
        index = req.get("index", 0)
        result = _git_run(path, ["stash", "drop", f"stash@{{{index}}}"])
    else:
        raise ValueError(f"Unknown stash operation: {operation}")
    if result.returncode != 0 and operation != "list":
        raise ValueError(f"git stash {operation} failed: {result.stderr}")
    return {"output": (result.stdout + result.stderr).strip()}


def action_git_branch(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """List, create, or delete branches."""
    operation = req.get("operation", "list")
    branch_name = req.get("branch", "")
    if operation == "list":
        result = _git_run(path, ["branch", "-a", "--format=%(refname:short) %(objectname:short) %(upstream:short)"])
        branches = []
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if parts:
                branches.append({"name": parts[0], "hash": parts[1] if len(parts) > 1 else "", "upstream": parts[2] if len(parts) > 2 else ""})
        return branches
    elif operation == "create":
        if not branch_name:
            raise ValueError("Missing 'branch' parameter")
        base = req.get("base", "")
        args = ["branch", branch_name]
        if base:
            args.append(base)
        result = _git_run(path, args)
    elif operation == "delete":
        if not branch_name:
            raise ValueError("Missing 'branch' parameter")
        force = req.get("force", False)
        flag = "-D" if force else "-d"
        result = _git_run(path, ["branch", flag, branch_name])
    else:
        raise ValueError(f"Unknown branch operation: {operation}")
    if result.returncode != 0:
        raise ValueError(f"git branch {operation} failed: {result.stderr}")
    return {"output": (result.stdout + result.stderr).strip()}


def action_git_merge(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Merge a branch."""
    branch = req.get("branch", "")
    if not branch:
        raise ValueError("Missing 'branch' parameter")
    no_ff = req.get("no_ff", False)
    args = ["merge"]
    if no_ff:
        args.append("--no-ff")
    args.append(branch)
    result = _git_run(path, args, timeout=60)
    if result.returncode != 0:
        return {"conflict": True, "output": (result.stdout + result.stderr).strip()}
    return {"conflict": False, "output": (result.stdout + result.stderr).strip()}


def action_git_rebase(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Rebase onto a branch."""
    onto = req.get("onto", "")
    if not onto:
        raise ValueError("Missing 'onto' parameter")
    operation = req.get("operation", "start")
    if operation == "start":
        result = _git_run(path, ["rebase", onto], timeout=60)
    elif operation == "continue":
        result = _git_run(path, ["rebase", "--continue"], timeout=60)
    elif operation == "abort":
        result = _git_run(path, ["rebase", "--abort"], timeout=30)
    else:
        raise ValueError(f"Unknown rebase operation: {operation}")
    if result.returncode != 0:
        return {"conflict": True, "output": (result.stdout + result.stderr).strip()}
    return {"conflict": False, "output": (result.stdout + result.stderr).strip()}


def action_git_cherry_pick(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Cherry-pick commits."""
    commits = req.get("commits", [])
    if not commits:
        raise ValueError("Missing 'commits' parameter (list of commit hashes)")
    result = _git_run(path, ["cherry-pick"] + commits, timeout=60)
    if result.returncode != 0:
        return {"conflict": True, "output": (result.stdout + result.stderr).strip()}
    return {"conflict": False, "output": (result.stdout + result.stderr).strip()}


def action_git_tag(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """List, create, or delete tags."""
    operation = req.get("operation", "list")
    tag_name = req.get("tag", "")
    if operation == "list":
        result = _git_run(path, ["tag", "-l", "--format=%(refname:short) %(objectname:short)"])
        tags = []
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if parts:
                tags.append({"name": parts[0], "hash": parts[1] if len(parts) > 1 else ""})
        return tags
    elif operation == "create":
        if not tag_name:
            raise ValueError("Missing 'tag' parameter")
        message = req.get("message", "")
        if message:
            result = _git_run(path, ["tag", "-a", tag_name, "-m", message])
        else:
            result = _git_run(path, ["tag", tag_name])
    elif operation == "delete":
        if not tag_name:
            raise ValueError("Missing 'tag' parameter")
        result = _git_run(path, ["tag", "-d", tag_name])
    else:
        raise ValueError(f"Unknown tag operation: {operation}")
    if result.returncode != 0:
        raise ValueError(f"git tag {operation} failed: {result.stderr}")
    return {"output": (result.stdout + result.stderr).strip()}


def action_git_blame(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Show line-by-line authorship."""
    file_path = req.get("file", "")
    if not file_path:
        file_path = path  # Use the main path argument
    start_line = req.get("start_line", 0)
    end_line = req.get("end_line", 0)
    args = ["blame", "--porcelain"]
    if start_line and end_line:
        args += [f"-L{start_line},{end_line}"]
    args.append(file_path)
    result = _git_run(path, args, timeout=30)
    if result.returncode != 0:
        raise ValueError(f"git blame failed: {result.stderr}")
    # Parse porcelain output into structured data
    blame_entries = []
    current = {}
    for line in result.stdout.splitlines():
        if line.startswith("\t"):
            current["content"] = line[1:]
            blame_entries.append(current)
            current = {}
        elif line.startswith("author "):
            current["author"] = line[7:]
        elif line.startswith("author-time "):
            current["time"] = line[12:]
        elif line.startswith("summary "):
            current["summary"] = line[8:]
        elif len(line) >= 40 and line[:40].replace(" ", "").isalnum() and "hash" not in current:
            parts = line.split()
            if len(parts) >= 3:
                current["hash"] = parts[0][:8]
                current["line"] = parts[2]
    return blame_entries[:200]  # Cap at 200 lines


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


# ── Git worktree actions ──────────────────────────────────────────

def action_git_worktree_list(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """List git worktrees."""
    result = _git_run(path, ["worktree", "list", "--porcelain"])
    if result.returncode != 0:
        raise ValueError(f"git worktree list failed: {result.stderr}")
    worktrees = []
    current = {}
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[9:]}
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line == "":
            if current:
                worktrees.append(current)
                current = {}
    if current:
        worktrees.append(current)
    return worktrees


def action_git_worktree_add(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Create a new git worktree."""
    branch = req.get("branch", "")
    worktree_path = req.get("worktree_path", "")
    if not branch:
        raise ValueError("Missing 'branch' parameter")
    if not worktree_path:
        # Auto-generate under .worktrees/
        worktree_path = str(Path(root_dir) / ".worktrees" / branch.replace("/", "_"))
    args = ["worktree", "add", worktree_path, branch]
    create_new = req.get("create_new_branch", False)
    if create_new:
        args = ["worktree", "add", "-b", branch, worktree_path]
    result = _git_run(path, args, timeout=30)
    if result.returncode != 0:
        raise ValueError(f"git worktree add failed: {result.stderr}")
    return {"worktree_path": worktree_path, "branch": branch, "output": result.stdout + result.stderr}


def action_git_worktree_remove(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    """Remove a git worktree."""
    worktree_path = req.get("worktree_path", "")
    if not worktree_path:
        raise ValueError("Missing 'worktree_path' parameter")
    result = _git_run(path, ["worktree", "remove", worktree_path], timeout=30)
    if result.returncode != 0:
        # Try force removal
        result = _git_run(path, ["worktree", "remove", "--force", worktree_path], timeout=30)
        if result.returncode != 0:
            raise ValueError(f"git worktree remove failed: {result.stderr}")
    return {"removed": worktree_path, "output": result.stdout + result.stderr}


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


# ── Screen automation (optional: pyautogui + mss) ────────────────

def _get_screen_libs():
    """Lazy import screen automation libs. Returns (pyautogui, mss) or raises."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = True  # move mouse to corner to abort
        return pyautogui
    except ImportError:
        raise RuntimeError(
            "pyautogui not installed. Run: pip install pyautogui mss")

def action_screen_screenshot(root_dir, abs_path, req):
    import base64
    try:
        import mss
        with mss.mss() as sct:
            img = sct.grab(sct.monitors[0])
            # Convert to PNG bytes
            from mss.tools import to_png
            png = to_png(img.rgb, img.size)
        return base64.b64encode(png).decode("ascii")
    except ImportError:
        pag = _get_screen_libs()
        import io
        screenshot = pag.screenshot()
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")

def action_screen_click(root_dir, abs_path, req):
    pag = _get_screen_libs()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    button = req.get("button", "left")
    pag.click(x, y, button=button)
    return {"clicked": True, "x": x, "y": y}

def action_screen_double_click(root_dir, abs_path, req):
    pag = _get_screen_libs()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    pag.doubleClick(x, y)
    return {"double_clicked": True, "x": x, "y": y}

def action_screen_type(root_dir, abs_path, req):
    pag = _get_screen_libs()
    text = req.get("text", "")
    pag.write(text, interval=0.02)
    return {"typed": len(text)}

def action_screen_key(root_dir, abs_path, req):
    pag = _get_screen_libs()
    key = req.get("key", "")
    # Support combos like "ctrl+c", "alt+tab"
    if "+" in key:
        keys = [k.strip() for k in key.split("+")]
        pag.hotkey(*keys)
    else:
        pag.press(key)
    return {"pressed": key}

def action_screen_move(root_dir, abs_path, req):
    pag = _get_screen_libs()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    pag.moveTo(x, y, duration=0.2)
    return {"moved": True, "x": x, "y": y}

def action_screen_scroll(root_dir, abs_path, req):
    pag = _get_screen_libs()
    x, y = int(req.get("x", 0)), int(req.get("y", 0))
    amount = int(req.get("amount", 3))
    pag.scroll(amount, x=x, y=y)
    return {"scrolled": amount}

def action_screen_mouse_position(root_dir, abs_path, req):
    pag = _get_screen_libs()
    pos = pag.position()
    return {"x": pos.x, "y": pos.y}


# ── Action registry ──────────────────────────────────────────────

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
    "read_file_chunked": action_read_file_chunked,
    "read_chunk": action_read_chunk,
    "write_file_chunked": action_write_file_chunked,
    "git_status": action_git_status,
    "git_log": action_git_log,
    "git_diff": action_git_diff,
    "git_commit": action_git_commit,
    "git_pull": action_git_pull,
    "git_push": action_git_push,
    "git_checkout": action_git_checkout,
    "git_add": action_git_add,
    "git_reset": action_git_reset,
    "git_stash": action_git_stash,
    "git_branch": action_git_branch,
    "git_merge": action_git_merge,
    "git_rebase": action_git_rebase,
    "git_cherry_pick": action_git_cherry_pick,
    "git_tag": action_git_tag,
    "git_blame": action_git_blame,
    "project_init": action_project_init,
    "edit_notebook": action_edit_notebook,
    "git_worktree_list": action_git_worktree_list,
    "git_worktree_add": action_git_worktree_add,
    "git_worktree_remove": action_git_worktree_remove,
    "screen_screenshot": action_screen_screenshot,
    "screen_click": action_screen_click,
    "screen_double_click": action_screen_double_click,
    "screen_type": action_screen_type,
    "screen_key": action_screen_key,
    "screen_move": action_screen_move,
    "screen_scroll": action_screen_scroll,
    "screen_mouse_position": action_screen_mouse_position,
    # MCP stdio proxy — registered after definition (see below)
}


# ── MCP stdio proxy ──────────────────────────────────────────────────
# Manages MCP servers as local subprocesses, proxies JSON-RPC calls.

import threading
import uuid as _uuid

# Active MCP server processes: {server_id: {"process", "stdin_lock", "pending"}}
_mcp_servers: Dict[str, Any] = {}
_mcp_lock = threading.Lock()


def _mcp_send_rpc(server_id: str, method: str, params: dict = None, timeout: int = 30) -> dict:
    """Send a JSON-RPC 2.0 request to an MCP stdio server and wait for response."""
    with _mcp_lock:
        srv = _mcp_servers.get(server_id)
    if not srv or srv["process"].poll() is not None:
        raise RuntimeError(f"MCP server '{server_id}' not running")

    request_id = _uuid.uuid4().hex[:12]
    rpc = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        rpc["params"] = params

    proc = srv["process"]
    line = json.dumps(rpc) + "\n"

    with srv["stdin_lock"]:
        proc.stdin.write(line.encode("utf-8"))
        proc.stdin.flush()

    # Read response lines until we get our request_id
    # MCP servers send one JSON-RPC response per line on stdout
    import select as _sel
    import time as _t
    deadline = _t.time() + timeout
    stdout_fd = proc.stdout.fileno()
    while _t.time() < deadline:
        if proc.poll() is not None:
            # Read any remaining output before reporting death
            remaining = proc.stdout.read()
            stderr_out = proc.stderr.read() if proc.stderr else b""
            raise RuntimeError(
                f"MCP server '{server_id}' exited (code={proc.returncode}). "
                f"stderr: {stderr_out[:500]}")
        # Wait for data with timeout (cross-platform: use thread for Windows)
        try:
            ready, _, _ = _sel.select([stdout_fd], [], [], 1.0)
        except (ValueError, OSError):
            # On Windows, select doesn't work on pipes — use blocking read in thread
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(1) as pool:
                future = pool.submit(proc.stdout.readline)
                try:
                    resp_line = future.result(timeout=1.0)
                except _cf.TimeoutError:
                    continue
            if resp_line:
                resp_line = resp_line.strip()
                if resp_line:
                    try:
                        resp = json.loads(resp_line)
                        if resp.get("id") == request_id:
                            if "error" in resp:
                                err = resp["error"]
                                raise RuntimeError(f"MCP error: {err.get('message', err)}")
                            return resp.get("result", {})
                    except json.JSONDecodeError:
                        pass
            continue
        if not ready:
            continue
        resp_line = proc.stdout.readline()
        if not resp_line:
            continue
        resp_line = resp_line.strip()
        if not resp_line:
            continue
        try:
            resp = json.loads(resp_line)
        except json.JSONDecodeError:
            continue
        if resp.get("id") == request_id:
            if "error" in resp:
                err = resp["error"]
                raise RuntimeError(f"MCP error: {err.get('message', err)}")
            return resp.get("result", {})
        # Not our response — could be a notification, skip
    raise TimeoutError(f"MCP server '{server_id}' did not respond within {timeout}s")


def action_mcp_start(root_dir, abs_path, req, **kwargs):
    """Start an MCP stdio server subprocess.

    req: {server_id, command, args?, env?}
    """
    server_id = req.get("server_id", "")
    command = req.get("command", "")
    args = req.get("args", [])
    env_extra = req.get("env", {})
    if not server_id or not command:
        raise ValueError("server_id and command are required")

    with _mcp_lock:
        if server_id in _mcp_servers:
            p = _mcp_servers[server_id]["process"]
            if p.poll() is None:
                return {"status": "already_running", "server_id": server_id}
            # Dead — clean up
            _mcp_servers.pop(server_id, None)

    # Build environment
    env = os.environ.copy()
    env.update(env_extra)

    # Launch subprocess
    cmd = [command] + (args if isinstance(args, list) else [args])
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=root_dir,
    )

    srv = {
        "process": proc,
        "stdin_lock": threading.Lock(),
        "command": command,
        "args": args,
    }
    with _mcp_lock:
        _mcp_servers[server_id] = srv

    # Initialize: send initialize request
    try:
        init_result = _mcp_send_rpc(server_id, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pawflow-relay", "version": "1.0"},
        }, timeout=10)

        # Send initialized notification (no response expected)
        notif = json.dumps({
            "jsonrpc": "2.0", "method": "notifications/initialized"
        }) + "\n"
        with srv["stdin_lock"]:
            proc.stdin.write(notif.encode("utf-8"))
            proc.stdin.flush()

        return {
            "status": "started",
            "server_id": server_id,
            "server_info": init_result.get("serverInfo", {}),
            "capabilities": init_result.get("capabilities", {}),
        }
    except Exception as e:
        # Startup failed — kill
        proc.kill()
        with _mcp_lock:
            _mcp_servers.pop(server_id, None)
        raise RuntimeError(f"MCP server init failed: {e}")


def action_mcp_discover(root_dir, abs_path, req, **kwargs):
    """Discover tools from a running MCP stdio server.

    req: {server_id}
    Returns: list of tools with name, description, inputSchema
    """
    server_id = req.get("server_id", "")
    if not server_id:
        raise ValueError("server_id is required")

    result = _mcp_send_rpc(server_id, "tools/list", {})
    tools = result.get("tools", [])
    return {
        "tools": [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema", {}),
            }
            for t in tools
        ],
        "server_id": server_id,
    }


def action_mcp_call(root_dir, abs_path, req, **kwargs):
    """Call a tool on a running MCP stdio server.

    req: {server_id, tool_name, arguments}
    """
    server_id = req.get("server_id", "")
    tool_name = req.get("tool_name", "")
    arguments = req.get("arguments", {})
    if not server_id or not tool_name:
        raise ValueError("server_id and tool_name are required")

    result = _mcp_send_rpc(server_id, "tools/call", {
        "name": tool_name,
        "arguments": arguments,
    })
    # MCP returns content array
    content = result.get("content", [])
    # Flatten text content
    text_parts = []
    for item in content:
        if item.get("type") == "text":
            text_parts.append(item.get("text", ""))
        elif item.get("type") == "image":
            text_parts.append(f"[image: {item.get('mimeType', 'image/*')}]")
        else:
            text_parts.append(json.dumps(item))
    return {
        "result": "\n".join(text_parts),
        "content": content,
        "isError": result.get("isError", False),
    }


def action_mcp_stop(root_dir, abs_path, req, **kwargs):
    """Stop a running MCP stdio server.

    req: {server_id}
    """
    server_id = req.get("server_id", "")
    if not server_id:
        raise ValueError("server_id is required")

    with _mcp_lock:
        srv = _mcp_servers.pop(server_id, None)
    if not srv:
        return {"status": "not_running", "server_id": server_id}

    proc = srv["process"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return {"status": "stopped", "server_id": server_id}


def action_mcp_list(root_dir, abs_path, req, **kwargs):
    """List all running MCP stdio servers."""
    with _mcp_lock:
        result = []
        dead = []
        for sid, srv in _mcp_servers.items():
            alive = srv["process"].poll() is None
            if not alive:
                dead.append(sid)
            result.append({
                "server_id": sid,
                "command": srv.get("command", ""),
                "alive": alive,
            })
        for sid in dead:
            _mcp_servers.pop(sid, None)
    return {"servers": result}


# Register MCP actions (defined after ACTIONS dict — avoids NameError)
ACTIONS["mcp_start"] = action_mcp_start
ACTIONS["mcp_call"] = action_mcp_call
ACTIONS["mcp_discover"] = action_mcp_discover
ACTIONS["mcp_stop"] = action_mcp_stop
ACTIONS["mcp_list"] = action_mcp_list
