"""Proxy registration for PawFlow Package flow tasks."""

from __future__ import annotations

from typing import Any, Dict, List

from core import FlowFile, Task, TaskError, TaskFactory


def register_package_task_proxy(task_type: str, metadata: Dict[str, Any]) -> type:
    """Register a relay runtime TaskFactory proxy for a PFP flow task."""
    task_type = str(task_type or "").strip()
    if not task_type:
        raise ValueError("task_type is required")
    parameters = metadata.get("parameters") or {}
    if parameters.get("type") == "object" and isinstance(parameters.get("properties", {}), dict):
        schema = parameters.get("properties", {})
    elif isinstance(parameters, dict):
        schema = parameters
    else:
        schema = {}

    class PackageTaskProxy(Task):
        TYPE = task_type
        VERSION = str(metadata.get("version") or "1.0.0")
        NAME = str(metadata.get("name") or task_type)
        DESCRIPTION = str(metadata.get("description") or "PFP package flow task proxy")
        ICON = str(metadata.get("icon") or "package")
        PACKAGE_RUNTIME = dict(metadata.get("package_runtime") or {})
        INSTALLED_FROM = dict(metadata.get("installed_from") or {})

        def get_parameter_schema(self) -> Dict[str, Any]:
            return dict(schema)

        def execute(self, flowfile: FlowFile) -> List[FlowFile]:
            from core import pfp_runtime
            try:
                return pfp_runtime.invoke_task(
                    self.PACKAGE_RUNTIME, self.INSTALLED_FROM, self.config, flowfile, {
                        "user_id": self.config.get("_user_id", ""),
                        "conversation_id": self.config.get("_conversation_id", ""),
                        "scope": self.config.get("_scope", ""),
                    })
            except pfp_runtime.PackageRuntimeError as exc:
                raise TaskError(str(exc)) from exc

    PackageTaskProxy.__name__ = _class_name_for(task_type)
    TaskFactory.register(PackageTaskProxy)
    return PackageTaskProxy


def _class_name_for(task_type: str) -> str:
    clean = "".join(ch if ch.isalnum() else "_" for ch in task_type).strip("_")
    parts = [part for part in clean.split("_") if part]
    name = "".join(part[:1].upper() + part[1:] for part in parts) or "Package"
    return f"{name}PackageTaskProxy"
