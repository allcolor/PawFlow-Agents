"""grep — Regex content search in files (CC-compatible parameters)."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class GrepHandler(BaseFsHandler):

    @property
    def name(self):
        return "grep"

    @property
    def description(self):
        return "Search file contents with a regex pattern. Returns path:line_number:line."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in (default: root)"},
                "glob": {"type": "string", "description": "Glob pattern to filter files (e.g. \"*.js\", \"*.{ts,tsx}\")"},
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
        glob_pattern = arguments.get("glob", "")
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

        # Relay service: pass all params (relay executes via its own rg)
        try:
            results = svc.grep(path, pattern, recursive)
            lines = [f"{r['path']}:{r['line_number']}: {r['line']}" for r in results[:limit]]
            total = len(results)
            if total > limit:
                lines.append(f"... and {total - limit} more matches (use limit to see more)")
            return "\n".join(lines) if lines else "(no matches)"
        except Exception as e:
            return f"Error: {e}"

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
            return self._workdir_grep_fallback(pattern, path, recursive, limit)

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
                args, capture_output=True, text=True, timeout=30,
                cwd=str(self._workdir) if self._workdir else None)
            output = result.stdout
        except FileNotFoundError:
            return self._workdir_grep_fallback(pattern, path, recursive, limit)
        except subprocess.TimeoutExpired:
            return "Error: grep timed out (30s)"

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

    def _workdir_grep_fallback(self, pattern, path, recursive, limit):
        """Python regex fallback when rg is not available."""
        import os, re
        full = self._sandbox_path(path, self._workdir)
        regex = re.compile(pattern)
        results = []
        if os.path.isfile(full):
            walk = [(os.path.dirname(full), [], [os.path.basename(full)])]
        else:
            walk = os.walk(full) if recursive else [(full, [], os.listdir(full))]
        for root, dirs, files in walk:
            for f in files:
                fp = os.path.join(root, f)
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
