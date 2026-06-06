"""Resolve declared agent runtime ports to running AgentLoopTask instances."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def resolve_agent_runtime_task(runtime_port: str):
    """Return the task behind a declared agent runtime port.

    ``runtime_port`` accepts ``flow.port`` or ``instance_id.port``. The flow part
    is matched against deployed instance id, template id, template name, and FQN.
    Only running deployments with a matching ``ports`` declaration are accepted.
    """
    flow_ref, port_id = _split_runtime_port(runtime_port)
    if not flow_ref or not port_id:
        raise RuntimeError(
            "agent_runtime_port must be '<flow_or_instance>.<port>'")

    from core.deployment_registry import DeploymentRegistry
    from core.executor_registry import ExecutorRegistry

    deploy_reg = DeploymentRegistry.get_instance()
    exec_reg = ExecutorRegistry.get_instance()
    matches = []
    for instance_id, inst in deploy_reg.get_all().items():
        if inst.status != "running":
            continue
        if not _matches_flow_ref(inst, flow_ref):
            continue
        executor = exec_reg.get(instance_id)
        if executor is None:
            continue
        port = _runtime_port_definition(executor, inst.flow_path, port_id)
        if not port:
            continue
        if port.get("type") != "agentRuntime":
            continue
        task_id = str(port.get("task") or "")
        task = executor.get_task(task_id) if task_id else None
        if task is None:
            continue
        matches.append((instance_id, task))

    if not matches:
        raise RuntimeError(f"No running agent runtime port found: {runtime_port}")
    if len(matches) > 1:
        ids = ", ".join(instance_id for instance_id, _ in matches)
        raise RuntimeError(
            f"Agent runtime port '{runtime_port}' is ambiguous: {ids}")
    return matches[0][1]


def _split_runtime_port(runtime_port: str) -> tuple[str, str]:
    value = str(runtime_port or "").strip()
    if "." not in value:
        return value, ""
    flow_ref, port_id = value.rsplit(".", 1)
    return flow_ref.strip(), port_id.strip()


def _matches_flow_ref(inst: Any, flow_ref: str) -> bool:
    return flow_ref in {
        str(getattr(inst, "instance_id", "") or ""),
        str(getattr(inst, "flow_id", "") or ""),
        str(getattr(inst, "flow_name", "") or ""),
        str(getattr(inst, "flow_fqn", "") or ""),
    }


def _runtime_port_definition(executor: Any, flow_path: str, port_id: str) -> Optional[Dict[str, Any]]:
    flow = getattr(executor, "flow", None)
    ports = getattr(flow, "ports", {}) if flow is not None else {}
    port = ports.get(port_id) if isinstance(ports, dict) else None
    if isinstance(port, dict):
        return port

    if not flow_path:
        return None
    try:
        raw = json.loads(Path(flow_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    port = (raw.get("ports") or {}).get(port_id)
    return port if isinstance(port, dict) else None
