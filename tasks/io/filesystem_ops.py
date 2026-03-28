"""FilesystemOps Task — Unified filesystem operations via service.

Performs filesystem operations (list, read, write, delete, search, grep,
find_replace, git) through a configured filesystem service. The action
and path can come from config or FlowFile attributes.

Config:
    service_id: str  — Filesystem service ID (required)
    action: str      — Operation to perform (or from flowfile fs.action attribute)
    path: str        — Target path (or from flowfile fs.path attribute)
    pattern: str     — For search (glob) or find_replace (regex)
    regex: str       — For grep
    replacement: str — For find_replace
    recursive: bool  — For search/grep (default: true)
"""

import json
import posixpath
from dataclasses import asdict
from typing import Any, Dict, List

from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class FilesystemOpsTask(BaseTask):
    """Unified filesystem operations task."""

    TYPE = "filesystemOps"
    VERSION = "1.0.0"
    NAME = "Filesystem Operations"
    DESCRIPTION = "Perform filesystem operations via a filesystem service"
    ICON = "folder"

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        service_id = self.config.get("service_id")
        if not service_id:
            raise TaskError("service_id is required for filesystemOps")

        svc = self.get_service(service_id)
        if svc is None:
            raise TaskError(f"Filesystem service not found: {service_id}")

        # Resolve action and path from config or flowfile attributes
        action = self.resolve_value(
            self.config.get("action", "") or flowfile.get_attribute("fs.action") or "",
            flowfile,
        )
        path = self.resolve_value(
            self.config.get("path", "") or flowfile.get_attribute("fs.path") or ".",
            flowfile,
        )

        if not action:
            raise TaskError("No action specified (config or fs.action attribute)")

        # Dispatch
        if action == "list_dir":
            entries = svc.list_dir(path)
            flowfile.set_content(json.dumps([asdict(e) for e in entries], default=str).encode())

        elif action == "read_file":
            data = svc.read_file(path)
            flowfile.set_content(data)
            flowfile.set_attribute("filename", posixpath.basename(path))
            flowfile.set_attribute("fileSize", str(len(data)))

        elif action == "write_file":
            svc.write_file(path, flowfile.get_content())

        elif action == "delete_file":
            svc.delete_file(path)

        elif action == "mkdir":
            svc.mkdir(path)

        elif action == "stat":
            entry = svc.stat(path)
            flowfile.set_content(json.dumps(asdict(entry), default=str).encode())

        elif action == "exists":
            flowfile.set_attribute("fs.exists", str(svc.exists(path)).lower())

        elif action == "search":
            pattern = self.resolve_value(self.config.get("pattern", "*"), flowfile)
            recursive = self.config.get("recursive", True)
            results = svc.search(path, pattern, recursive)
            flowfile.set_content(json.dumps(results).encode())

        elif action == "grep":
            regex = self.resolve_value(self.config.get("regex", ""), flowfile)
            recursive = self.config.get("recursive", True)
            results = svc.grep(path, regex, recursive)
            flowfile.set_content(json.dumps(results).encode())

        elif action == "find_replace":
            pattern = self.resolve_value(self.config.get("pattern", ""), flowfile)
            replacement = self.resolve_value(self.config.get("replacement", ""), flowfile)
            result = svc.find_replace(path, pattern, replacement)
            flowfile.set_content(json.dumps(result).encode())

        else:
            raise TaskError(f"Unknown filesystem action: {action}")

        flowfile.set_attribute("fs.action", action)
        flowfile.set_attribute("fs.path", path)
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_id": {
                "type": "string", "required": True,
                "description": "Filesystem service ID",
            },
            "action": {
                "type": "select", "required": False,
                "options": [
                    "list_dir", "read_file", "write_file", "delete_file",
                    "mkdir", "stat", "exists", "search", "grep", "find_replace",
                ],
                "description": "Operation (or use fs.action attribute)",
            },
            "path": {
                "type": "string", "required": False,
                "description": "Target path (or use fs.path attribute)",
            },
            "pattern": {
                "type": "string", "required": False,
                "description": "Glob pattern (search) or regex (find_replace)",
            },
            "regex": {
                "type": "string", "required": False,
                "description": "Regex pattern for grep",
            },
            "replacement": {
                "type": "string", "required": False,
                "description": "Replacement text for find_replace",
            },
            "recursive": {
                "type": "boolean", "required": False, "default": True,
                "description": "Recursive search/grep",
            },
        }


TaskFactory.register(FilesystemOpsTask)
