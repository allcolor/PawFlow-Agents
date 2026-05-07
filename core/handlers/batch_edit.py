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
            "Each edit specifies a path, an old_string to find, a new_string to\n"
            "replace it with, and optional replace_all. The top-level replace_all\n"
            "acts as the default for edits that do not specify it.\n"
            "All edits are applied sequentially within one tool invocation, which\n"
            "reduces round-trips when you need to change several files at once.\n\n"
            "Parameters:\n"
            "  edits      -- array of {path, old_string, new_string, replace_all?} objects.\n"
            "  replace_all -- default replace_all value for edits that omit it.\n"
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
                            "replace_all": {"type": "boolean", "description": "Replace every occurrence for this edit"},
                            "fuzzy": {"type": "boolean", "description": "Allow one high-confidence fuzzy match for this edit"},
                            "fuzzy_threshold": {"type": "number", "description": "Minimum fuzzy similarity score"},
                        },
                        "required": ["path", "old_string", "new_string"],
                    },
                },
                "replace_all": {"type": "boolean", "description": "Default replace_all value for edits that omit it"},
                "fuzzy": {"type": "boolean", "description": "Default fuzzy value for edits that omit it"},
                "fuzzy_threshold": {"type": "number", "description": "Default fuzzy threshold (default: 0.92)"},
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["edits"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        from core.handlers._arg_normalize import validate_object_list
        edits, _err = validate_object_list(
            arguments.get("edits"),
            param_name="edits",
            required_keys=["path", "old_string", "new_string"],
            example=('edits=[{"path": "...", "old_string": "...", '
                     '"new_string": "..."}, ...]'),
        )
        if _err:
            return f"Error: {_err}"
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
                    bool(edit.get("replace_all", arguments.get("replace_all", False))),
                )
                results.append(r)
            return "\n".join(results)

        if svc is None:
            return self._no_target_error(fs)

        try:
            service_name = fs or getattr(svc, '_service_id', '')
            for edit in edits:
                path = edit.get("path", "")
                self._checkpoint_before(svc, path, service_name=service_name)
            result = svc.batch_edit(
                edits,
                replace_all=bool(arguments.get("replace_all", False)),
                local=bool(arguments.get("local", False)),
                fuzzy=bool(arguments.get("fuzzy", False)),
                fuzzy_threshold=arguments.get("fuzzy_threshold"),
            )
            if isinstance(result, dict):
                files = result.get("files_modified") or []
                total = int(result.get("total_replacements", 0) or 0)
                lines = [
                    f"Batch edited {result.get('files_modified_count', len(files))} file(s), "
                    f"{result.get('edits_applied', len(edits))} edit(s), "
                    f"{total} replacement(s)."
                ]
                for detail in result.get("details", [])[:20]:
                    match = detail.get("match_type") or "exact"
                    suffix = f", {match}" if match != "exact" else ""
                    lines.append(
                        f"- {detail.get('path')}: {detail.get('replacements', 0)} "
                        f"replacement(s) at line {detail.get('line', '?')}{suffix}")
                if len(result.get("details", [])) > 20:
                    lines.append(f"... {len(result.get('details', [])) - 20} more edit(s)")
                return "\n".join(lines)
            return str(result)
        except Exception as e:
            return f"Error: {e}"
