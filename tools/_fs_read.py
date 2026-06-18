"""Read-family + chunked + project-init filesystem actions, split from
fs_actions.py (list/read/pdf/notebook/stat/exists/search/write/chunked).
"""
import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from _fs_paths import MAX_FILE_SIZE, _expand_glob_braces, _rel


def action_list_dir(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    p = Path(path)
    recursive = bool(req.get("recursive", False))
    try:
        max_entries = int(req.get("max_entries") or 0)
    except (TypeError, ValueError):
        max_entries = 0

    entries = []
    iterator = p.rglob("*") if recursive else p.iterdir()
    for entry in sorted(iterator):
        st = entry.stat()
        name = entry.name
        if recursive:
            name = str(entry.relative_to(p)).replace("\\", "/")
        entries.append({
            "name": name,
            "kind": "directory" if entry.is_dir() else "file",
            "size": st.st_size if entry.is_file() else 0,
            "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        })
        if max_entries > 0 and len(entries) >= max_entries:
            break
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
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
    if "package.json" in names:
        types.append("Node.js")
    if "pyproject.toml" in names or "setup.py" in names or "requirements.txt" in names:
        types.append("Python")
    if "Cargo.toml" in names:
        types.append("Rust")
    if "go.mod" in names:
        types.append("Go")
    if "pom.xml" in names or "build.gradle" in names:
        types.append("Java")
    if "Makefile" in names:
        types.append("Make")
    if "Dockerfile" in names:
        types.append("Docker")
    context["project_types"] = types

    # Git info
    git_dir = root / ".git"
    if git_dir.is_dir():
        context["git"] = True
        try:
            import subprocess  # nosec B404
            br = subprocess.run(["git", "branch", "--show-current"],  # nosec B603, B607
                                cwd=root_dir, capture_output=True, text=True, timeout=10)
            context["git_branch"] = br.stdout.strip()
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
    try:
        limit = int(req.get("limit", 500) or 500)
    except (TypeError, ValueError):
        limit = 500
    if limit <= 0:
        limit = 500
    limit = min(limit, 5000)
    p = Path(path)
    patterns = _expand_glob_braces(pattern)
    matches = []
    seen = set()
    if recursive:
        iterator = (m for pat in patterns for m in p.rglob(pat))
    else:
        iterator = (m for pat in patterns for m in p.glob(pat))
    for m in iterator:
        rel = str(m.relative_to(p)).replace("\\", "/")
        if rel in seen:
            continue
        seen.add(rel)
        matches.append(rel)
        if len(matches) >= limit:
            break
    return matches[:limit]



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
