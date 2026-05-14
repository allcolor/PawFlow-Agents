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
        schema = dict(parameters.get("properties", {}))
    elif isinstance(parameters, dict):
        schema = dict(parameters)
    else:
        schema = {}
    schema["relay"] = {
        "type": "string",
        "required": True,
        "description": "Filesystem relay service id used to execute this package task.",
    }

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

        def set_parameter_context(self, ctx) -> None:
            runtime_context = {
                key: self.config.get(key)
                for key in ("_user_id", "_conversation_id", "_scope", "_agent_name")
                if isinstance(self.config, dict) and key in self.config
            }
            if ctx:
                self.config = ctx.resolve_config(
                    getattr(self, "_original_config", self.config))
                self.config.update(runtime_context)

        def execute(self, flowfile: FlowFile) -> List[FlowFile]:
            from core import pfp_runtime
            try:
                relay_id = str(self.config.get("relay") or "").strip()
                if not relay_id:
                    raise TaskError("PFP flow task requires relay parameter")
                user_id = str(self.config.get("_user_id") or "")
                conversation_id = str(self.config.get("_conversation_id") or "")
                if not user_id:
                    raise TaskError("PFP flow task requires user_id runtime context")
                scope = str(self.config.get("_scope") or ("conversation" if conversation_id else "user"))
                if scope in {"conversation", "conv"} and not conversation_id:
                    raise TaskError("PFP conversation-scoped flow task requires conversation_id runtime context")
                agent_name = str(self.config.get("_agent_name") or "")
                resolved = pfp_runtime.resolve_flow_task_runtime(
                    task_type, user_id=user_id,
                    conversation_id=conversation_id, scope=scope)
                task_config = dict(self.config)
                task_config.pop("relay", None)
                task_config.pop("_user_id", None)
                task_config.pop("_conversation_id", None)
                task_config.pop("_scope", None)
                task_config.pop("_agent_name", None)
                return pfp_runtime.invoke_task(
                    resolved["package_runtime"], resolved["installed_from"], task_config, flowfile, {
                        "relay_id": relay_id,
                        "user_id": user_id,
                        "conversation_id": conversation_id,
                        "scope": scope,
                        "agent_name": agent_name,
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
