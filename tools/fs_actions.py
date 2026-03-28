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


def _docker_cmd():
    if os.name == "nt":
        return ["wsl", "docker"]
    return ["docker"]


def _translate_path(p):
    if os.name != "nt":
        return p
    p = p.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:]}"
    return p


def _to_host_path(container_path):
    """Translate container path to host path for DinD volume mounts."""
    host_workdir = os.environ.get("PAWFLOW_HOST_WORKDIR")
    if not host_workdir:
        return container_path
    container_workdir = os.environ.get("PAWFLOW_WORKDIR", "/workspace")
    try:
        rel = os.path.relpath(container_path, container_workdir)
        if rel.startswith(".."):
            return container_path
        if rel == ".":
            return host_workdir
        return os.path.join(host_workdir, rel).replace("\\", "/")
    except ValueError:
        return container_path


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
    # Docker-based shells (isolated execution)
    try:
        _dr = subprocess.run(_docker_cmd() + ["info"], capture_output=True, timeout=10)
        if _dr.returncode == 0:
            _docker_bin = _docker_cmd()[0]
            shells["docker-python"] = _docker_bin
            shells["docker-node"] = _docker_bin
            shells["docker-bash"] = _docker_bin
    except Exception:
        pass
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
    action_screen_type, action_screen_key, action_screen_move,
    action_screen_scroll, action_screen_mouse_position,
)
from fs_mcp import (
    action_mcp_start, action_mcp_discover, action_mcp_call,
    action_mcp_stop, action_mcp_list,
)

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
    "read_file_chunked": action_read_file_chunked,
    "read_chunk": action_read_chunk,
    "write_file_chunked": action_write_file_chunked,
    "project_init": action_project_init,
    "edit_notebook": action_edit_notebook,
    "screen_screenshot": action_screen_screenshot,
    "screen_click": action_screen_click,
    "screen_double_click": action_screen_double_click,
    "screen_type": action_screen_type,
    "screen_key": action_screen_key,
    "screen_move": action_screen_move,
    "screen_scroll": action_screen_scroll,
    "screen_mouse_position": action_screen_mouse_position,
    "mcp_start": action_mcp_start,
    "mcp_call": action_mcp_call,
    "mcp_discover": action_mcp_discover,
    "mcp_stop": action_mcp_stop,
    "mcp_list": action_mcp_list,
}
