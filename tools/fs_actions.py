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

# Actions that require write access
WRITE_ACTIONS = frozenset({
    "write_file", "delete_file", "mkdir", "find_replace", "edit",
    "git_commit", "git_push", "exec",
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


def action_read_file(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    content = Path(path).read_bytes()
    return {"content": base64.b64encode(content).decode("ascii"), "size": len(content)}


def action_write_file(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    content = req.get("content", "")
    raw = base64.b64decode(content) if req.get("base64") else content.encode("utf-8")
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
    """Exact string replacement (like Claude Code Edit tool)."""
    old_string = req.get("old_string", "")
    new_string = req.get("new_string", "")
    replace_all = req.get("replace_all", False)
    if not old_string:
        raise ValueError("Missing 'old_string' parameter")
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        raise ValueError(f"old_string not found in {p.name}")
    if count > 1 and not replace_all:
        raise ValueError(f"old_string found {count} times (use replace_all=true)")
    if replace_all:
        new_text = text.replace(old_string, new_string)
    else:
        new_text = text.replace(old_string, new_string, 1)
    p.write_text(new_text, encoding="utf-8")
    return {"replacements": count if replace_all else 1, "path": _rel(path, root_dir)}


def action_exec(root_dir: str, path: str, req: Dict[str, Any], *,
                allow_exec: bool = False) -> Any:
    """Execute a shell command in the sandbox directory."""
    if not allow_exec:
        raise PermissionError("Shell execution disabled. Start relay with --allow-exec")
    command = req.get("command", "")
    timeout = min(req.get("timeout", 30), 120)
    if not command:
        raise ValueError("Missing 'command' parameter")
    result = subprocess.run(
        command, shell=True,
        capture_output=True, text=True,
        timeout=timeout,
        cwd=root_dir,
    )
    return {
        "stdout": result.stdout[-10000:],
        "stderr": result.stderr[-5000:],
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
    _git_run(path, ["add", "-A"])
    result = _git_run(path, ["commit", "-m", message])
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


# ── Action registry ──────────────────────────────────────────────

ACTIONS = {
    "list_dir": action_list_dir,
    "read_file": action_read_file,
    "write_file": action_write_file,
    "delete_file": action_delete_file,
    "mkdir": action_mkdir,
    "stat": action_stat,
    "exists": action_exists,
    "search": action_search,
    "grep": action_grep,
    "find_replace": action_find_replace,
    "edit": action_edit,
    "exec": action_exec,
    "git_status": action_git_status,
    "git_log": action_git_log,
    "git_diff": action_git_diff,
    "git_commit": action_git_commit,
    "git_pull": action_git_pull,
    "git_push": action_git_push,
    "git_checkout": action_git_checkout,
}
