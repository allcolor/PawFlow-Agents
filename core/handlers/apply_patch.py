"""apply_patch — Apply a unified diff patch to a file."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class ApplyPatchHandler(BaseFsHandler):

    @property
    def name(self):
        return "apply_patch"

    @property
    def description(self):
        return "Apply a unified diff patch to a file."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to patch"},
                "patch": {"type": "string", "description": "Unified diff string"},
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["path", "patch"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", "")
        patch = arguments.get("patch", "")
        if not path or not patch:
            return "Error: 'path' and 'patch' are required"
        fs = arguments.get("filesystem", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            fs = _svc_name

        svc, workdir = self._resolve(fs)

        if svc == "filestore":
            return "Error: apply_patch not supported on FileStore"

        if workdir:
            return "Error: apply_patch not supported on workspace (use edit instead)"

        if svc is None:
            return self._no_target_error(fs)

        try:
            service_name = fs or getattr(svc, '_service_id', '')
            self._checkpoint_before(svc, path, service_name=service_name)
            result = svc._request("apply_patch", path, patch=patch)
            if isinstance(result, dict):
                if result.get("applied") is False:
                    return f"Error: patch was not applied to {path}"
                files = result.get("files_modified") or []
                hunks = int(result.get("hunks_applied", 0) or 0)
                stats = result.get("stats", "") or ""
                output = result.get("output", "") or ""
                if files:
                    return (f"Patch applied ({result.get('method', 'apply_patch')}, "
                            f"{hunks} hunk(s)): " + ", ".join(files))
                if stats:
                    return f"Patch applied ({result.get('method', 'apply_patch')}): {stats}"
                if output:
                    return output
                return "Error: patch reported success but no files or hunks were modified"
            return str(result)
        except Exception as e:
            return f"Error: {e}"
