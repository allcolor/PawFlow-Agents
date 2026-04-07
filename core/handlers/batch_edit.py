"""batch_edit — Atomic multi-file string replacements."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class BatchEditHandler(BaseFsHandler):

    @property
    def name(self):
        return "batch_edit"

    @property
    def description(self):
        return (
            "Apply multiple string replacements across one or more files in a\n"
            "single atomic call.\n\n"
            "Each edit specifies a path, an old_string to find, and a new_string to\n"
            "replace it with -- identical semantics to the single-file edit tool.\n"
            "All edits are applied sequentially within one tool invocation, which\n"
            "reduces round-trips when you need to change several files at once.\n\n"
            "Parameters:\n"
            "  edits      -- array of {path, old_string, new_string} objects.\n"
            "  filesystem -- filesystem service name; omit for the default service.\n\n"
            "When to use:\n"
            "  - Renaming a symbol across multiple files.\n"
            "  - Applying a coordinated set of changes that must land together.\n"
            "  - Any time you have 3+ edits queued -- batch_edit saves tool calls.\n\n"
            "Prefer the single edit tool when you have only one or two changes, as\n"
            "it gives clearer per-file error messages. batch_edit is not supported\n"
            "on FileStore -- use it only with filesystem services or workdir."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "description": "List of edits to apply",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                        },
                        "required": ["path", "old_string", "new_string"],
                    },
                },
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["edits"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        edits = arguments.get("edits", [])
        if not edits:
            return "Error: 'edits' array is required"
        fs = arguments.get("filesystem", "")

        svc, workdir = self._resolve(fs)

        if svc == "filestore":
            return "Error: batch_edit not supported on FileStore"

        if workdir:
            results = []
            for edit in edits:
                r = self._workdir_edit(
                    edit.get("path", ""),
                    edit.get("old_string", ""),
                    edit.get("new_string", ""),
                )
                results.append(r)
            return "\n".join(results)

        if svc is None:
            return self._no_target_error(fs)

        try:
            service_name = fs or getattr(svc, '_service_id', '')
            results = []
            for edit in edits:
                path = edit.get("path", "")
                self._checkpoint_before(svc, path, service_name=service_name)
                result = svc.edit(path, edit.get("old_string", ""),
                                  edit.get("new_string", ""), False)
                results.append(f"{path}: {result.get('replacements', 0)} replacement(s)")
            return "\n".join(results)
        except Exception as e:
            return f"Error: {e}"
