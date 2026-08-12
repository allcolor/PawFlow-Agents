"""Grep + glob filesystem actions, split from fs_actions.py."""
import json
import re
import shutil
import subprocess  # nosec B404
from collections import deque
from pathlib import Path
from typing import Any, Dict, List

from _fs_paths import _expand_glob_braces


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


def _grep_has_glob_magic(path: str) -> bool:
    return any(ch in path for ch in ("*", "?", "["))


def _grep_split_glob_path(path: str) -> tuple[str, str]:
    if not path or not _grep_has_glob_magic(path):
        return path, ""
    p = Path(path)
    if p.exists():
        return path, ""

    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    split_at = None
    for idx, part in enumerate(parts):
        if _grep_has_glob_magic(part):
            split_at = idx
            break
    if split_at is None:
        return path, ""

    base_parts = parts[:split_at]
    glob_parts = parts[split_at:]
    base = "/".join(base_parts)
    if not base:
        base = "/" if normalized.startswith("/") else "."
    return base, "/".join(glob_parts)


def _grep_candidate_paths(path: str) -> list[Path]:
    p = Path(path)
    if p.exists():
        return [p]
    parts = [Path(part) for part in str(path).split() if part]
    if len(parts) > 1 and all(part.exists() for part in parts):
        return parts
    return [p]


def _grep_effective_globs(glob_pattern: Any, recursive: bool) -> list[str]:
    """Normalize include/glob filters.

    Accept comma/space-separated strings and lists. Basename globs are
    recursive by default, matching the MCP grep contract.
    """
    if not glob_pattern:
        return []
    if isinstance(glob_pattern, (list, tuple, set)):
        raw_parts = [str(g).strip() for g in glob_pattern]
    else:
        raw_parts = _split_glob_parts(str(glob_pattern))
    globs = []
    for part in raw_parts:
        if not part:
            continue
        for expanded in _expand_glob_braces(part):
            normalized = expanded.replace("\\", "/")
            if recursive and "/" not in normalized and not normalized.startswith("**/"):
                normalized = f"**/{normalized}"
            globs.append(normalized)
    return globs


def _split_glob_parts(value: str) -> list[str]:
    parts = []
    start = 0
    depth = 0
    for idx, ch in enumerate(value):
        if ch == "{":
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
        elif depth == 0 and (ch == "," or ch.isspace()):
            part = value[start:idx].strip()
            if part:
                parts.append(part)
            start = idx + 1
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _grep_rg_display_path(raw_path: str, root_dir: str, paths: list[Path],
                          multiple_roots: bool) -> tuple[str, Path]:
    """Map an rg result back to the relay's existing display-path contract."""
    matched = Path(raw_path)
    if not matched.is_absolute():
        matched = Path(root_dir) / matched
    matched = matched.resolve()

    for candidate in paths:
        resolved = candidate.resolve()
        if candidate.is_file():
            if matched == resolved:
                display = str(candidate).replace("\\", "/") if multiple_roots else candidate.name
                return display, matched
            continue
        try:
            rel = str(matched.relative_to(resolved)).replace("\\", "/")
        except ValueError:
            continue
        root_label = str(candidate).replace("\\", "/")
        display = f"{root_label}/{rel}" if multiple_roots else rel
        return display, matched

    return raw_path.replace("\\", "/"), matched


def _grep_attach_context(results: list[dict], context_before: int,
                         context_after: int) -> None:
    """Attach bounded context to rg matches using the legacy response shape."""
    if not context_before and not context_after:
        for row in results:
            row.pop("_absolute_path", None)
        return

    cache: dict[str, list[str]] = {}
    for row in results:
        raw_path = row.pop("_absolute_path", "")
        lines = cache.get(raw_path)
        if lines is None:
            try:
                lines = Path(raw_path).read_text(
                    encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            cache[raw_path] = lines

        line_number = int(row["line_number"])
        if context_before:
            start = max(1, line_number - context_before)
            row["before"] = [
                {"line_number": number, "line": lines[number - 1][:500]}
                for number in range(start, line_number)
                if number <= len(lines)
            ]
        if context_after:
            end = min(len(lines), line_number + context_after)
            row["after"] = [
                {"line_number": number, "line": lines[number - 1][:500]}
                for number in range(line_number + 1, end + 1)
            ]


def _grep_with_ripgrep(root_dir: str, paths: list[Path], regex: str,
                        glob_patterns: list[str], recursive: bool, limit: int,
                        context_before: int,
                        context_after: int) -> list[dict] | None:
    """Use bounded rg JSON output, or return None for Python fallback."""
    if limit <= 0:
        return []
    # ripgrep may interleave multiple roots; the legacy contract preserves the
    # caller's root order, so keep that uncommon shape on the Python fallback.
    if len(paths) != 1:
        return None
    binary = shutil.which("rg")
    if not binary:
        return None

    args = [
        binary, "--json", "--ignore-case", "--hidden", "--no-ignore",
        "--color=never", "--no-messages",
    ]
    if not recursive:
        args.extend(["--max-depth", "1"])
    for pattern in glob_patterns:
        args.extend(["--glob", pattern])
    for name in sorted(_GREP_SKIP_DIRS):
        args.extend(["--glob", f"!**/{name}/**"])
    for suffix in sorted(_GREP_SKIP_EXT):
        args.extend(["--glob", f"!**/*{suffix}"])
    args.extend(["--", regex])
    args.extend(str(path) for path in paths)

    try:
        proc = subprocess.Popen(  # nosec B603
            args,
            cwd=root_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None

    results: list[dict] = []
    reached_limit = False
    parse_failed = False
    multiple_roots = len(paths) > 1
    try:
        if proc.stdout is None:
            parse_failed = True
        else:
            for raw_line in proc.stdout:
                try:
                    event = json.loads(raw_line)
                except (TypeError, json.JSONDecodeError):
                    parse_failed = True
                    break
                if event.get("type") != "match":
                    continue
                data = event.get("data") or {}
                raw_path = (data.get("path") or {}).get("text")
                line = (data.get("lines") or {}).get("text")
                line_number = data.get("line_number")
                if not isinstance(raw_path, str) or not isinstance(line, str):
                    continue
                if not isinstance(line_number, int):
                    continue
                display, absolute = _grep_rg_display_path(
                    raw_path, root_dir, paths, multiple_roots)
                results.append({
                    "path": display,
                    "line_number": line_number,
                    "line": line.rstrip("\r\n")[:500],
                    "_absolute_path": str(absolute),
                })
                if len(results) >= limit:
                    reached_limit = True
                    break
    finally:
        if (reached_limit or parse_failed) and proc.poll() is None:
            proc.terminate()
        try:
            returncode = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            returncode = proc.wait()

    if parse_failed or (not reached_limit and returncode not in (0, 1)):
        return None
    _grep_attach_context(results, context_before, context_after)
    return results


def action_grep(root_dir: str, path: str, req: Dict[str, Any]) -> Any:
    regex = req.get("regex", "")
    recursive = req.get("recursive", True)
    glob_pattern = req.get("glob", "") or req.get("include", "")
    limit = int(req.get("limit") or req.get("head_limit") or 200)
    context_both = int(req.get("context", 0) or 0)
    context_before = int(req.get("context_before") or req.get("-B") or context_both or 0)
    context_after = int(req.get("context_after") or req.get("-A") or context_both or 0)
    if not regex:
        raise ValueError("Missing 'regex' parameter")
    if not glob_pattern:
        path, glob_pattern = _grep_split_glob_path(path)
    glob_patterns = _grep_effective_globs(glob_pattern, recursive)
    # Suppress Python 3.12+ FutureWarning for user-supplied regex quirks
    # (e.g. `[[..]]` patterns flagged as possible nested sets). The regex
    # still compiles correctly — we just don't want the warning to pollute
    # the relay log on every grep call.
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("ignore", FutureWarning)
        compiled = re.compile(regex, re.IGNORECASE)
    paths = _grep_candidate_paths(path)
    rg_results = _grep_with_ripgrep(
        root_dir, paths, regex, glob_patterns, recursive, limit,
        context_before, context_after)
    if rg_results is not None:
        return rg_results

    multiple_roots = len(paths) > 1
    results = []

    def _scan_file(fpath: Path, display_name: str) -> bool:
        if fpath.suffix.lower() in _GREP_SKIP_EXT:
            return False
        if _grep_is_binary(fpath):
            return False
        before = deque(maxlen=max(0, context_before))
        pending_after: List[Dict[str, Any]] = []
        try:
            handle = fpath.open("r", encoding="utf-8", errors="replace")
        except Exception:
            return False
        with handle:
            for i, raw_line in enumerate(handle, 1):
                line = raw_line.rstrip("\r\n")
                clipped = line[:500]
                if pending_after:
                    done = []
                    for item in pending_after:
                        item["after"].append({"line_number": i, "line": clipped})
                        item["_remaining_after"] -= 1
                        if item["_remaining_after"] <= 0:
                            done.append(item)
                    for item in done:
                        pending_after.remove(item)
                if len(results) < limit and compiled.search(line):
                    row = {
                        "path": display_name,
                        "line_number": i,
                        "line": clipped,
                    }
                    if context_before:
                        row["before"] = list(before)
                    if context_after:
                        row["after"] = []
                        row["_remaining_after"] = context_after
                        pending_after.append(row)
                    results.append(row)
                if context_before:
                    before.append({"line_number": i, "line": clipped})
                if len(results) >= limit and not pending_after:
                    break
        for item in results:
            item.pop("_remaining_after", None)
        return len(results) >= limit

    scan_patterns = glob_patterns or ["**/*" if recursive else "*"]
    seen = set()
    for p in paths:
        if p.is_file():
            display = str(p).replace("\\", "/") if multiple_roots else p.name
            if _scan_file(p, display):
                break
            continue
        for scan_pattern in scan_patterns:
            for fpath in p.glob(scan_pattern):
                if fpath in seen:
                    continue
                seen.add(fpath)
                if not fpath.is_file():
                    continue
                # Skip any path whose parents include an ignored dir.
                if any(part in _GREP_SKIP_DIRS for part in fpath.parts):
                    continue
                rel = str(fpath.relative_to(p)).replace("\\", "/")
                root_label = str(p).replace("\\", "/")
                display = f"{root_label}/{rel}" if multiple_roots else rel
                if _scan_file(fpath, display):
                    break
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    return results
