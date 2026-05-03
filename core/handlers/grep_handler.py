"""grep — Regex content search in files (CC-compatible parameters)."""

import os
from typing import Any, Dict, Tuple
from core.handlers._fs_base import BaseFsHandler


class GrepHandler(BaseFsHandler):

    @property
    def name(self):
        return "grep"

    @property
    def description(self):
        return (
            "A powerful content search tool built on ripgrep.\n\n"
            "ALWAYS use this grep tool for searching file contents. NEVER invoke grep or rg "
            "as a bash command — this tool is optimized for correct access and permissions.\n\n"
            "Usage:\n"
            " - Supports full regex syntax (e.g. 'log.*Error', 'function\\s+\\w+').\n"
            " - Filter files with the glob parameter (e.g. '*.js', '**/*.tsx') or the "
            "type parameter (e.g. 'js', 'py', 'rust').\n"
            " - Output modes: 'content' shows matching lines with context, "
            "'files_with_matches' shows only file paths (default), "
            "'count' shows match counts per file.\n\n"
            "Pattern syntax:\n"
            " - Uses ripgrep regex (not grep). Literal braces need escaping "
            "(use 'interface\\{\\}' to find 'interface{}' in Go code).\n"
            " - For cross-line patterns, use multiline: true "
            "(e.g. 'struct \\{[\\s\\S]*?field').\n\n"
            "Parameters:\n"
            " - head_limit: Limit output to first N entries (default 250). "
            "Pass 0 for unlimited (use sparingly).\n"
            " - offset: Skip first N entries before applying head_limit.\n"
            " - -B/-A/-C/context: Lines of context before/after/both for content mode.\n"
            " - -i: Case insensitive search.\n"
            " - -n: Show line numbers (default true for content mode)."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in (default: root)"},
                "glob": {"type": "string", "description": "Glob pattern to filter files (e.g. \"*.js\", \"*.{ts,tsx}\")"},
                "include": {"type": "string", "description": "Alias for glob. Basename patterns like '*.py' are recursive by default."},
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": "Output mode: 'content' shows matching lines, 'files_with_matches' shows file paths (default), 'count' shows match counts.",
                },
                "-B": {"type": "number", "description": "Lines to show before each match (context)."},
                "-A": {"type": "number", "description": "Lines to show after each match (context)."},
                "-C": {"type": "number", "description": "Lines to show before and after each match (context)."},
                "context": {"type": "number", "description": "Alias for -C."},
                "-n": {"type": "boolean", "description": "Show line numbers (default: true for content mode)."},
                "-i": {"type": "boolean", "description": "Case insensitive search."},
                "type": {"type": "string", "description": "File type filter (e.g. 'js', 'py', 'rust')."},
                "head_limit": {"type": "number", "description": "Limit output to first N entries. Defaults to 250."},
                "limit": {"type": "integer", "description": "Alias for head_limit."},
                "offset": {"type": "number", "description": "Skip first N entries before applying limit."},
                "multiline": {"type": "boolean", "description": "Enable multiline mode (pattern spans lines)."},
                "recursive": {"type": "boolean", "description": "Search recursively (default: true)"},
                "source": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["pattern"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        pattern = arguments.get("pattern", "")
        if not pattern:
            return "Error: 'pattern' is required"
        path = arguments.get("path", ".")
        source = arguments.get("source", "")

        # Normalize params
        limit = int(arguments.get("head_limit") or arguments.get("limit") or 250)
        offset = int(arguments.get("offset", 0) or 0)
        output_mode = arguments.get("output_mode", "files_with_matches")
        glob_pattern = arguments.get("glob", "") or arguments.get("include", "")
        case_insensitive = arguments.get("-i", False)
        context_before = arguments.get("-B", 0)
        context_after = arguments.get("-A", 0)
        context_both = arguments.get("-C") or arguments.get("context", 0)
        show_line_numbers = arguments.get("-n", True)
        file_type = arguments.get("type", "")
        multiline = arguments.get("multiline", False)
        recursive = arguments.get("recursive", True)

        if context_both:
            context_before = context_before or context_both
            context_after = context_after or context_both

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            source = _svc_name

        svc, workdir = self._resolve(source)

        if not glob_pattern:
            path, glob_pattern = self._split_glob_from_path(path, workdir or "")

        if svc == "filestore":
            return "Error: grep is not supported on FileStore"

        if workdir:
            return self._workdir_rg(
                pattern, path, limit=limit, offset=offset,
                output_mode=output_mode, glob_pattern=glob_pattern,
                case_insensitive=case_insensitive,
                context_before=context_before, context_after=context_after,
                show_line_numbers=show_line_numbers, file_type=file_type,
                multiline=multiline, recursive=recursive)

        if svc is None:
            return self._no_target_error(source)

        # Relay service: pass all CC params through. Keep grep native even
        # when RTK is enabled: `rtk grep` emits abbreviated paths and output
        # that does not preserve PawFlow's file:line/mode semantics reliably.
        try:
            _grep_kwargs = {}
            if glob_pattern:
                _grep_kwargs["glob"] = glob_pattern
            if output_mode != "files_with_matches":
                _grep_kwargs["output_mode"] = output_mode
            if case_insensitive:
                _grep_kwargs["case_insensitive"] = True
            if context_before:
                _grep_kwargs["context_before"] = context_before
            if context_after:
                _grep_kwargs["context_after"] = context_after
            if not show_line_numbers:
                _grep_kwargs["show_line_numbers"] = False
            if file_type:
                _grep_kwargs["file_type"] = file_type
            if multiline:
                _grep_kwargs["multiline"] = True
            if limit != 250:
                _grep_kwargs["limit"] = limit
            if offset:
                _grep_kwargs["offset"] = offset
            if arguments.get("local", False):
                _grep_kwargs["local"] = True
            results = svc.grep(path, pattern, recursive, **_grep_kwargs)
            if isinstance(results, str):
                return results  # relay returned formatted text
            lines = [f"{r['path']}:{r['line_number']}: {r['line']}" for r in results[:limit]]
            total = len(results)
            if total > limit:
                lines.append(f"... and {total - limit} more matches (use limit to see more)")
            return "\n".join(lines) if lines else "(no matches)"
        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def _has_glob_magic(path: str) -> bool:
        return any(ch in path for ch in ("*", "?", "["))

    def _split_glob_from_path(self, path: str, workdir: str = "") -> Tuple[str, str]:
        """Treat accidental grep path globs as a directory plus glob filter."""
        if not path or not self._has_glob_magic(path):
            return path, ""

        if workdir:
            try:
                if os.path.exists(self._sandbox_path(path, workdir)):
                    return path, ""
            except Exception:
                pass
        elif os.path.isabs(path) and os.path.exists(path):
            return path, ""

        normalized = path.replace("\\", "/")
        parts = normalized.split("/")
        split_at = None
        for idx, part in enumerate(parts):
            if self._has_glob_magic(part):
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

    def _workdir_rg(self, pattern: str, path: str = ".",
                    limit: int = 250, offset: int = 0,
                    output_mode: str = "files_with_matches",
                    glob_pattern: str = "", case_insensitive: bool = False,
                    context_before: int = 0, context_after: int = 0,
                    show_line_numbers: bool = True, file_type: str = "",
                    multiline: bool = False, recursive: bool = True) -> str:
        """Execute ripgrep (rg) in workdir. Falls back to Python regex if rg not available."""
        import subprocess, os, shutil

        full = self._sandbox_path(path, self._workdir)
        rg_path = shutil.which("rg")

        if not rg_path:
            # Fallback to Python regex
            return self._workdir_grep_fallback(
                pattern, path, recursive, limit, glob_pattern)

        args = [rg_path]
        if multiline:
            args.extend(["-U", "--multiline-dotall"])
        if case_insensitive:
            args.append("-i")
        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")
        if output_mode == "content" and show_line_numbers:
            args.append("-n")
        if output_mode == "content":
            if context_before:
                args.extend(["-B", str(context_before)])
            if context_after:
                args.extend(["-A", str(context_after)])
        if not recursive:
            args.append("--max-depth=1")
        if file_type:
            args.extend(["--type", file_type])
        if glob_pattern:
            for g in glob_pattern.replace(",", " ").split():
                if g.strip():
                    args.extend(["--glob", g.strip()])
        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)
        args.append(str(full))

        try:
            result = subprocess.run(
                args, capture_output=True, text=True,
                cwd=str(self._workdir) if self._workdir else None)
            output = result.stdout
        except FileNotFoundError:
            return self._workdir_grep_fallback(
                pattern, path, recursive, limit, glob_pattern)

        if not output.strip():
            return "(no matches)"

        lines = output.strip().split("\n")

        # Make paths relative
        wd = str(self._workdir).replace("\\", "/")
        rel_lines = []
        for line in lines:
            line = line.replace("\\", "/")
            if line.startswith(wd + "/"):
                line = line[len(wd) + 1:]
            rel_lines.append(line)

        # Apply offset + limit
        if offset:
            rel_lines = rel_lines[offset:]
        if limit and len(rel_lines) > limit:
            rel_lines = rel_lines[:limit]

        return "\n".join(rel_lines)

    def _workdir_grep_fallback(self, pattern, path, recursive, limit,
                               glob_pattern=""):
        """Python regex fallback when rg is not available."""
        import fnmatch
        import os
        import re
        full = self._sandbox_path(path, self._workdir)
        regex = re.compile(pattern)
        results = []
        globs = [g.strip() for g in glob_pattern.replace(",", " ").split()
                 if g.strip()]

        def _matches_glob(fp: str) -> bool:
            if not globs:
                return True
            rel = os.path.relpath(fp, self._workdir).replace("\\", "/")
            name = os.path.basename(fp)
            return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g)
                       for g in globs)

        if os.path.isfile(full):
            walk = [(os.path.dirname(full), [], [os.path.basename(full)])]
        else:
            walk = os.walk(full) if recursive else [(full, [], os.listdir(full))]
        for root, dirs, files in walk:
            for f in files:
                fp = os.path.join(root, f)
                if not _matches_glob(fp):
                    continue
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        for i, line in enumerate(fh, 1):
                            if regex.search(line):
                                rel = os.path.relpath(fp, self._workdir).replace("\\", "/")
                                results.append(f"{rel}:{i}: {line.rstrip()}")
                                if len(results) >= limit:
                                    break
                except (OSError, UnicodeDecodeError):
                    continue
            if len(results) >= limit:
                break
        return "\n".join(results) if results else "(no matches)"
