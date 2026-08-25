"""First-party declarative block catalog derived from TaskFactory."""

from __future__ import annotations

import copy
from typing import Any, Optional

from core.flow_definition_validator import (
    static_task_relationships,
    static_task_schema,
)

_ALIASES = {
    "inferLLM": ("llm_call", "LLM Call", "Agents and LLM"),
    "agentLLMCall": ("agent_llm_call", "Agent LLM Call", "Agents and LLM"),
    "invokeWorkflowAgent": (
        "workflow_agent", "Workflow Agent", "Agents and LLM"),
    "executeFlow": ("subflow", "Subflow", "Subflows and Groups"),
    "fetchHTTP": ("http_request", "HTTP Request", "Files and Network"),
    "getFile": ("read_file", "Read File", "Files and Network"),
    "putFile": ("write_file", "Write File", "Files and Network"),
    "transformJSON": ("transform", "Transform", "Data and Transform"),
    "updateAttribute": ("update_value", "Update Value", "Data and Transform"),
    "routeOnAttribute": ("route", "Route", "Decisions"),
    "splitContent": ("split", "Split", "Data and Transform"),
    "splitJSON": ("split_json", "Split JSON", "Data and Transform"),
    "mergeContent": ("merge", "Merge", "Parallel and Join"),
    "publishMessage": ("publish_message", "Publish Message", "Messaging"),
    "notify": ("notify_user", "Notify User", "User Interaction"),
    "notifyUser": ("notify_user", "Notify User", "User Interaction"),
    "requestUserInput": (
        "request_user_input", "Request User Input", "User Interaction"),
    "requestConfirmation": (
        "request_confirmation", "Request Confirmation", "User Interaction"),
    "durableWait": ("durable_wait", "Durable Wait", "Time and Events"),
    "durableTimer": ("durable_timer", "Durable Timer", "Time and Events"),
    "repeatUntil": ("repeat_until", "Repeat Until", "Loops"),
    "durableNotify": ("notify_event", "Notify Event", "Time and Events"),
    "inputPort": ("input", "Input", "Steps"),
    "outputPort": ("output", "Output", "Completion and Errors"),
    "fail": ("failure", "Failure", "Completion and Errors"),
}

_DIRECT_EXECUTOR_TASKS = frozenset({"inferLLM", "agentLLMCall"})


class DeclarativeBlockRegistry:
    """Pure authoring registry; descriptors never execute package code."""

    @staticmethod
    def _task_factory():
        from core import TaskFactory
        return TaskFactory

    @classmethod
    def descriptor_for_task(
        cls, task_type: str, parameters: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        factory = cls._task_factory()
        task_class = factory.get(task_type)
        alias = _ALIASES.get(task_type)
        block_type, label, category = (
            alias if alias else (
                f"processor:{task_type}", task_type, "Advanced Processors"))
        schema = static_task_schema(task_class, parameters or {})
        outputs = static_task_relationships(task_class, parameters or {})
        return {
            "type": block_type,
            "version": 1,
            "label": label,
            "category": category,
            "shape": "atomic",
            "task_type": task_type,
            "config_schema": copy.deepcopy(schema),
            "inputs": ["input"],
            "outputs": list(outputs),
            "lowering_version": 1,
            "recognizer_version": 1,
            "requires_explicit_executor": task_type in _DIRECT_EXECUTOR_TASKS,
            "generic": alias is None,
        }

    @classmethod
    def catalog(cls) -> list[dict[str, Any]]:
        factory = cls._task_factory()
        rows = [
            cls.descriptor_for_task(task_type)
            for task_type in sorted(factory.list_types())
        ]
        return sorted(
            rows,
            key=lambda row: (
                row["category"].lower(), row["label"].lower(), row["type"]),
        )

    @classmethod
    def by_block_type(cls, block_type: str) -> dict[str, Any]:
        if block_type.startswith("processor:"):
            return cls.descriptor_for_task(block_type.split(":", 1)[1])
        for task_type, alias in _ALIASES.items():
            if alias[0] == block_type:
                return cls.descriptor_for_task(task_type)
        raise KeyError(block_type)

    @staticmethod
    def effective_executor(task: dict[str, Any]) -> dict[str, Any]:
        execution = task.get("execution")
        if isinstance(execution, dict):
            return copy.deepcopy(execution)
        if task.get("type") in _DIRECT_EXECUTOR_TASKS:
            return {
                "strategy": "single", "roles": {},
                "missing_binding": True,
            }
        if task.get("type") == "invokeWorkflowAgent":
            return {
                "strategy": "single",
                "roles": {"primary": {
                    "kind": "workflow_agent",
                    "agent_ref": copy.deepcopy(
                        (task.get("parameters") or {}).get("agent_ref")),
                }},
            }
        return {
            "strategy": "single",
            "roles": {"primary": {"kind": "pawflow"}},
        }


__all__ = ["DeclarativeBlockRegistry"]
