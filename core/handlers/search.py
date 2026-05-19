"""search - Combined file filtering, regex search, and ranked snippets."""
import logging

import fnmatch
import os
import re
from collections import defaultdict
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler, _expand_glob_braces


class SearchHandler(BaseFsHandler):
    @property
    def name(self):
        return "search"

    @property
    def display_name(self):
        return "Search"

    @property
    def description(self):
        return (
            "Search files with one call: combines a path, optional glob filter, "
            "regex pattern, and contextual snippets ranked by file match count. "
            "Use this when you would otherwise call glob + grep + read."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "query": {"type": "string", "description": "Alias for pattern"},
                "path": {"type": "string", "description": "Directory or file to search in (default: .)"},
                "glob": {"type": "string", "description": "Glob filter, e.g. '**/*.py' or '*.js'"},
                "include": {"type": "string", "description": "Alias for glob"},
                "context": {"type": "integer", "description": "Context lines around each match (default: 2)"},
                "limit": {"type": "integer", "description": "Maximum match entries (default: 80)"},
                "case_insensitive": {"type": "boolean", "description": "Case-insensitive regex search"},
                "-i": {"type": "boolean", "description": "Alias for case_insensitive"},
                "multiline": {"type": "boolean", "description": "Enable multiline regex mode"},
                "source": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": [],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        pattern = arguments.get("pattern") or arguments.get("query") or ""
        if not pattern:
            return "Error: 'pattern' is required"
        path = arguments.get("path", ".") or "."
        source = arguments.get("source", "")
        glob_pattern = arguments.get("glob", "") or arguments.get("include", "")
        context = int(arguments.get("context", 2) or 0)
        limit = int(arguments.get("limit", 80) or 80)
        case_insensitive = bool(arguments.get("case_insensitive", arguments.get("-i", False)))
        multiline = bool(arguments.get("multiline", False))

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name
        svc, workdir = self._resolve(source)
        if svc == "filestore":
            return "Error: search is not supported on FileStore"
        if svc is None and not workdir:
            return self._no_target_error(source)

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            compiled = re.compile(pattern, flags)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        if workdir:
            try:
                results, reader = self._search_workdir(path, compiled, glob_pattern, limit)
            except Exception as e:
                return f"Error: {e}"
            return self._format_search_results(pattern, results, reader, context, limit)

        kwargs = {
            "output_mode": "content",
            "context_before": context,
            "context_after": context,
            "limit": limit,
        }
        if glob_pattern:
            kwargs["glob"] = glob_pattern
        if case_insensitive:
            kwargs["case_insensitive"] = True
        if multiline:
            kwargs["multiline"] = True
        if arguments.get("local", False):
            kwargs["local"] = True
        local = bool(arguments.get("local", False))
        if local:
            kwargs["local"] = True
        try:
            raw_results = svc.grep(path, pattern, True, **kwargs)
        except Exception as e:
            return f"Error: {e}"

        results = self._normalize_grep_results(raw_results, limit)
        if not results:
            return "(no matches)"

        def reader(rel_path: str) -> str:
            target = self._remote_result_path(path, rel_path)
            data = svc.read_file(target, local=local)
            return data.decode("utf-8", errors="replace")

        return self._format_search_results(pattern, results, reader, context, limit)

    def _normalize_grep_results(self, raw_results: Any, limit: int) -> list[dict]:
        if isinstance(raw_results, list):
            normalized = []
            for item in raw_results[:limit]:
                if not isinstance(item, dict):
                    continue
                normalized.append({
                    "path": str(item.get("path", "?")),
                    "line_number": int(item.get("line_number", 0) or 0),
                    "line": str(item.get("line", "")),
                })
            return normalized
        if not isinstance(raw_results, str):
            return []
        parsed = []
        for line in raw_results.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            parsed.append({
                "path": parts[0],
                "line_number": int(parts[1]),
                "line": parts[2],
            })
            if len(parsed) >= limit:
                break
        return parsed

    def _remote_result_path(self, base_path: str, rel_path: str) -> str:
        if rel_path.startswith("/"):
            return rel_path
        base = base_path.rstrip("/") or "."
        if os.path.basename(base) == rel_path:
            return base
        return f"{base}/{rel_path}"

    def _search_workdir(self, path: str, compiled, glob_pattern: str,
                        limit: int) -> tuple[list[dict], Any]:
        base = self._sandbox_path(path, self._workdir)
        if not os.path.exists(base):
            raise FileNotFoundError(f"'{path}' not found in workspace")
        root = base if os.path.isdir(base) else os.path.dirname(base)
        candidates = self._workdir_candidates(base, glob_pattern)
        results = []
        text_cache = {}
        for full_path, rel_path in candidates:
            try:
                text = self._read_workdir_text(full_path)
            except UnicodeDecodeError:
                continue
            text_cache[rel_path] = text
            for i, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    results.append({"path": rel_path, "line_number": i, "line": line[:500]})
                    if len(results) >= limit:
                        return results, lambda p: text_cache.get(p, self._read_workdir_text(os.path.join(root, p)))
        return results, lambda p: text_cache.get(p, self._read_workdir_text(os.path.join(root, p)))

    def _workdir_candidates(self, base: str, glob_pattern: str) -> list[tuple[str, str]]:
        if os.path.isfile(base):
            return [(base, os.path.basename(base))]
        patterns = self._split_globs(glob_pattern) or ["**/*"]
        out = []
        seen = set()
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".pytest_cache"}]
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                if rel in seen or not self._matches_any_glob(rel, patterns):
                    continue
                seen.add(rel)
                out.append((full, rel))
        return out

    def _split_globs(self, glob_pattern: str) -> list[str]:
        if not glob_pattern:
            return []
        globs = []
        for part in self._split_glob_parts(str(glob_pattern)):
            globs.extend(_expand_glob_braces(part))
        return globs

    def _split_glob_parts(self, value: str) -> list[str]:
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

    def _matches_any_glob(self, rel_path: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            normalized = pattern.replace("\\", "/")
            if fnmatch.fnmatch(rel_path, normalized):
                return True
            if "/**/" in normalized and fnmatch.fnmatch(rel_path, normalized.replace("/**/", "/")):
                return True
            if "/" not in normalized and fnmatch.fnmatch(os.path.basename(rel_path), normalized):
                return True
        return False

    def _read_workdir_text(self, full_path: str) -> str:
        with open(full_path, "rb") as f:
            data = f.read()
        if b"\x00" in data[:4096]:
            raise UnicodeDecodeError("utf-8", data, 0, 1, "binary file")
        return data.decode("utf-8", errors="replace")

    def _format_search_results(self, pattern: str, results: list[dict],
                               reader, context: int, limit: int) -> str:
        grouped = defaultdict(list)
        for item in results[:limit]:
            grouped[item.get("path", "?")].append(item)
        ranked = sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True)
        out = [f"Search results for {pattern!r}: {sum(len(v) for _, v in ranked)} match(es) in {len(ranked)} file(s)"]
        for fpath, items in ranked:
            out.append(f"\n## {fpath} ({len(items)} match(es))")
            lines = []
            try:
                lines = reader(fpath).splitlines()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            for item in items[:8]:
                line_no = item.get("line_number", "?")
                if context > 0 and isinstance(line_no, int) and lines:
                    start = max(1, line_no - context)
                    end = min(len(lines), line_no + context)
                    for n in range(start, end + 1):
                        marker = ">" if n == line_no else " "
                        out.append(f"{marker} {n}: {lines[n - 1][:500]}")
                else:
                    line = item.get("line", "")
                    out.append(f"> {line_no}: {line}")
            if len(items) > 8:
                out.append(f"... {len(items) - 8} more match(es) in this file")
        return "\n".join(out)
