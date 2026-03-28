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
    def description(self):
        return (
            "Edit a file by exact string replacement (old_string → new_string) or "
            "line-based replacement (start_line/end_line + new_string). "
            "Use filesystem parameter to specify the service."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_string": {"type": "string", "description": "Exact string to find and replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: first only)"},
                "start_line": {"type": "integer", "description": "Start line for line-based edit (1-based)"},
                "end_line": {"type": "integer", "description": "End line for line-based edit"},
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
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

        # Workdir
        if workdir:
            if start_line > 0 and end_line > 0:
                return self._workdir_line_edit(path, start_line, end_line, new_string)
            return self._workdir_edit(path, old_string, new_string, replace_all)

        if svc is None or svc == "filestore":
            return self._no_target_error(fs) if svc is None else "Error: cannot edit FileStore files"

        # Service
        try:
            service_name = fs or getattr(svc, '_service_id', '')
            self._checkpoint_before(svc, path, service_name=service_name)

            if start_line > 0 and end_line > 0:
                result = svc._request("edit", path,
                                      start_line=start_line, end_line=end_line,
                                      new_string=new_string)
                return (f"Edited {result.get('path', path)}: "
                        f"replaced lines {start_line}-{end_line} "
                        f"({result.get('lines_removed', 0)} removed, "
                        f"{result.get('lines_inserted', 0)} inserted)")
            else:
                result = svc.edit(path, old_string, new_string, replace_all)
                diff = result.get("diff", [])
                if diff:
                    diff_text = (f"Edited {result.get('path', path)} "
                                 f"(line {result.get('line', '?')}), "
                                 f"{result.get('replacements', 0)} replacement(s):\n")
                    for d in diff:
                        prefix = "- " if d["type"] == "remove" else "+ " if d["type"] == "add" else "  "
                        diff_text += f"{d['line']:4d} {prefix}{d['text']}\n"
                    return diff_text
                return f"Edited {result.get('path', path)}: {result.get('replacements', 0)} replacement(s)"
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
