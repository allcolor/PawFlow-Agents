"""edit — Exact string replacement or line-based edit in a file."""

import logging
from typing import Any, Dict

from core.handlers._fs_base import BaseFsHandler

logger = logging.getLogger(__name__)


class EditHandler(BaseFsHandler):

    @property
    def name(self):
        return "edit"

    @property
    def display_name(self):
        return "Update"

    @property
    def description(self):
        return (
            "Performs exact string replacements in a file (old_string -> new_string), "
            "or line-based replacement (start_line/end_line + new_string).\n\n"
            "Usage:\n"
            " - Exact unique replacements are attempted directly. If old_string does "
            "not match, the tool returns diagnostics so you can re-read the relevant "
            "range instead of retrying blindly.\n"
            " - When editing text from read output, preserve the exact indentation "
            "(tabs/spaces) as it appears in the file. Do not include line number prefixes "
            "in old_string or new_string.\n"
            " - ALWAYS prefer editing existing files over creating new files.\n"
            " - Only use emojis if the user explicitly requests it.\n\n"
            "Important:\n"
            " - The edit will FAIL if old_string is not unique in the file. Provide a "
            "larger string with more surrounding context to make it unique, or use "
            "replace_all to change every occurrence.\n"
            " - Whitespace-only drift (line endings, trailing whitespace, tab/space "
            "indentation) is tolerated when it resolves to a unique match. Set "
            "fuzzy=true to allow high-confidence fuzzy matching for one occurrence.\n"
            " - Use replace_all for renaming variables or strings across the entire file.\n"
            " - Use the filesystem parameter to specify a non-default filesystem service."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_string": {"type": "string", "description": "Exact string to find and replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "old": {"type": "string", "description": "Alias for old_string"},
                "new": {"type": "string", "description": "Alias for new_string"},
                "old_str": {"type": "string", "description": "Alias for old_string"},
                "new_str": {"type": "string", "description": "Alias for new_string"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: first only)"},
                "fuzzy": {"type": "boolean", "description": "Allow one high-confidence fuzzy match when exact/whitespace matching fails"},
                "fuzzy_threshold": {"type": "number", "description": "Minimum fuzzy similarity score (default: 0.92)"},
                "start_line": {"type": "integer", "description": "Start line for line-based edit (1-based)"},
                "end_line": {"type": "integer", "description": "End line for line-based edit"},
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        if "old_string" not in arguments and "old" in arguments:
            arguments["old_string"] = arguments.get("old", "")
        if "new_string" not in arguments and "new" in arguments:
            arguments["new_string"] = arguments.get("new", "")
        if "old_string" not in arguments and "old_str" in arguments:
            arguments["old_string"] = arguments.get("old_str", "")
        if "new_string" not in arguments and "new_str" in arguments:
            arguments["new_string"] = arguments.get("new_str", "")
        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' is required"
        fs = arguments.get("filesystem", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            fs = _svc_name

        svc, workdir = self._resolve(fs)

        old_string = arguments.get("old_string", "")
        new_string = arguments.get("new_string", "")
        replace_all = arguments.get("replace_all", False)
        start_line = int(arguments.get("start_line", 0) or 0)
        end_line = int(arguments.get("end_line", 0) or 0)

        # Pre-flight duplicate guard. Read-before-edit is intentionally not
        # enforced here: an exact unique old_string is already proof that the
        # edit target matches the caller's view. If it does not match, the
        # relay returns diagnostics and the duplicate guard stops blind retries.
        from core.handlers._edit_guard import (
            check_duplicate_failure, record_edit_failure, track_write,
        )
        _uid = self._user_id
        _cid = self._conversation_id
        _agent = self._agent_name
        if not (start_line > 0 and end_line > 0):
            _dup = check_duplicate_failure(_uid, _cid, _agent, path, old_string)
            if _dup:
                return f"Error: {_dup}"

        # Workdir
        if workdir:
            if start_line > 0 and end_line > 0:
                return self._workdir_line_edit(path, start_line, end_line, new_string)
            _result = self._workdir_edit(path, old_string, new_string, replace_all)
            if _result.startswith("Error:"):
                record_edit_failure(_uid, _cid, _agent, path, old_string)
            else:
                # Successful edit — update tracking so next edit doesn't
                # require a re-read of our own output.
                try:
                    with open(self._sandbox_path(path, self._workdir), "rb") as _f:
                        track_write(_uid, _cid, _agent, path, _f.read())
                except Exception:
                    pass
            return _result

        if svc is None or svc == "filestore":
            return self._no_target_error(fs) if svc is None else "Error: cannot edit FileStore files"

        # Service
        try:
            service_name = fs or getattr(svc, '_service_id', '')
            self._checkpoint_before(svc, path, service_name=service_name)

            if start_line > 0 and end_line > 0:
                result = svc._request("edit", path,
                                      start_line=start_line, end_line=end_line,
                                      new_string=new_string,
                                      local=bool(arguments.get("local", False)))
                return (f"Edited {result.get('path', path)}: "
                        f"replaced lines {start_line}-{end_line} "
                        f"({result.get('lines_removed', 0)} removed, "
                        f"{result.get('lines_inserted', 0)} inserted)")
            else:
                try:
                    result = svc.edit(
                        path, old_string, new_string, replace_all,
                        local=bool(arguments.get("local", False)),
                        fuzzy=bool(arguments.get("fuzzy", False)),
                        fuzzy_threshold=arguments.get("fuzzy_threshold"))
                except Exception as e:
                    # Record failure so the next identical old_string attempt
                    # gets refused by check_duplicate_failure.
                    record_edit_failure(_uid, _cid, _agent, path, old_string)
                    return f"Error editing '{path}': {e}"
                diff = result.get("diff", [])
                if diff:
                    match_type = result.get("match_type")
                    match_suffix = f", match={match_type}" if match_type and match_type != "exact" else ""
                    diff_text = (f"Edited {result.get('path', path)} "
                                 f"(line {result.get('line', '?')}), "
                                 f"{result.get('replacements', 0)} replacement(s)"
                                 f"{match_suffix}:\n")
                    for d in diff:
                        prefix = "- " if d["type"] == "remove" else "+ " if d["type"] == "add" else "  "
                        diff_text += f"{d['line']:4d} {prefix}{d['text']}\n"
                    return diff_text
                match_type = result.get("match_type")
                match_suffix = f" ({match_type} match)" if match_type and match_type != "exact" else ""
                return f"Edited {result.get('path', path)}: {result.get('replacements', 0)} replacement(s){match_suffix}"
        except Exception as e:
            return f"Error editing '{path}': {e}"

    def _workdir_line_edit(self, path: str, start: int, end: int, new_string: str) -> str:
        import os
        full = self._sandbox_path(path, self._workdir)
        if not os.path.exists(full):
            return f"Error: '{path}' not found"
        with open(full, "r", encoding="utf-8") as f:
            lines = f.readlines()
        removed = end - start + 1
        new_lines = new_string.split("\n")
        lines[start - 1:end] = [ln + "\n" for ln in new_lines]
        with open(full, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"Edited {path}: replaced lines {start}-{end} ({removed} removed, {len(new_lines)} inserted)"
