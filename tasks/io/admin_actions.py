"""AdminActionTask — Handle admin API requests.

Receives POST /admin/api with JSON body {action: "...", ...params}.
Routes to handler functions that use ExecutorRegistry, DeploymentRegistry,
ServiceRegistry (global), and TemplateService.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import FlowFile, TaskFactory, ServiceFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


def _get_registries():
    """Lazy import registries to avoid circular imports."""
    from core.executor_registry import ExecutorRegistry
    from core.deployment_registry import DeploymentRegistry
    from core.service_registry import ServiceRegistry
    from core.template_service import TemplateService
    return (
        ExecutorRegistry.get_instance(),
        DeploymentRegistry.get_instance(),
        ServiceRegistry.get_instance(),
        TemplateService(),
    )


def _apply_service_forwards(flow, service_overrides: Dict[str, str]):
    """Replace flow services with global/user service instances."""
    if not service_overrides:
        return
    from core.service_registry import ServiceRegistry
    gsvc_reg = ServiceRegistry.get_instance()
    usvc_reg = ServiceRegistry.get_instance()

    for flow_svc_id, ref in service_overrides.items():
        live = None
        if ref.startswith("user:"):
            parts = ref.split(":", 2)
            if len(parts) == 3:
                _, uid, sid = parts
                live = usvc_reg.get_live_instance("user", uid, sid)
        elif ref.startswith("global:"):
            sid = ref.split(":", 1)[1]
            live = gsvc_reg.get_live_instance("global", "", sid)
        else:
            live = gsvc_reg.get_live_instance("global", "", ref)

        if live is not None and flow_svc_id in flow.services:
            flow.services[flow_svc_id] = live


def _json_response(flowfile: FlowFile, data: Any, status: str = "200") -> List[FlowFile]:
    """Set JSON response on a FlowFile."""
    flowfile.set_content(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))
    flowfile.set_attribute("http.response.status", status)
    flowfile.set_attribute("http.response.header.Content-Type", "application/json")
    return [flowfile]


def _error(flowfile: FlowFile, msg: str, status: str = "400") -> List[FlowFile]:
    return _json_response(flowfile, {"error": msg}, status)


# ── Action handlers ──

def _admin_list_flows(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """List all deployed flows with executor status."""
    deploy_reg.sync_with_executors()
    instances = deploy_reg.get_all()
    result = []
    for iid, inst in instances.items():
        executor = exec_reg.get(iid)
        status_info = executor.get_status() if executor else None
        result.append({
            "instance_id": iid,
            "flow_id": inst.flow_id,
            "flow_name": inst.flow_name,
            "owner": inst.owner,
            "status": inst.status,
            "source": inst.source,
            "created_at": inst.created_at,
            "last_started": inst.last_started,
            "last_stopped": inst.last_stopped,
            "error_message": inst.error_message,
            "tasks_total": status_info["tasks_total"] if status_info else 0,
            "tasks_running": status_info["tasks_running"] if status_info else 0,
            "tasks_errored": status_info["tasks_errored"] if status_info else 0,
            "total_queued": status_info["total_queued_flowfiles"] if status_info else 0,
        })
    return result


def _admin_get_flow(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Get detailed flow info."""
    iid = body.get("instance_id", "")
    inst = deploy_reg.get(iid)
    if not inst:
        return {"error": f"Instance '{iid}' not found"}

    executor = exec_reg.get(iid)
    status_info = executor.get_status() if executor else None
    task_states = executor.get_all_task_states() if executor else {}
    queue_stats = executor.get_queue_stats() if executor else []

    # Load flow template for task/service definitions
    tasks_def = {}
    services_def = {}
    relations = []
    if inst.flow_path and Path(inst.flow_path).exists():
        try:
            raw = json.loads(Path(inst.flow_path).read_text(encoding="utf-8"))
            tasks_def = raw.get("tasks", {})
            services_def = raw.get("services", {})
            relations = raw.get("relations", [])
        except Exception:
            pass

    return {
        "instance_id": iid,
        "flow_id": inst.flow_id,
        "flow_name": inst.flow_name,
        "flow_path": inst.flow_path,
        "owner": inst.owner,
        "status": inst.status,
        "source": inst.source,
        "created_at": inst.created_at,
        "last_started": inst.last_started,
        "last_stopped": inst.last_stopped,
        "error_message": inst.error_message,
        "parameters": inst.parameters,
        "service_overrides": inst.service_overrides,
        "service_configs": inst.service_configs,
        "max_workers": inst.max_workers,
        "max_retries": inst.max_retries,
        "tasks": tasks_def,
        "services": services_def,
        "relations": relations,
        "task_states": task_states,
        "queue_stats": queue_stats,
        "executor_status": status_info,
        "layout": inst.layout,
    }


def _admin_deploy_flow(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Deploy a flow from a template path."""
    template_path = body.get("template_path", "")
    owner = body.get("owner")
    parameters = body.get("parameters", {})
    max_workers = body.get("max_workers", 4)
    max_retries = body.get("max_retries", 3)
    service_overrides = body.get("service_overrides", {})
    service_configs = body.get("service_configs", {})
    auto_start = body.get("auto_start", True)

    if not template_path or not Path(template_path).exists():
        return {"error": f"Template not found: {template_path}"}

    instance_id = deploy_reg.deploy(
        template_path=template_path,
        owner=owner if owner != "__global__" else None,
        parameters=parameters,
        max_workers=max_workers,
        max_retries=max_retries,
        source="admin",
        service_overrides=service_overrides,
        service_configs=service_configs,
    )

    if auto_start:
        result = _start_executor(instance_id, deploy_reg, exec_reg)
        if "error" in result:
            return {"instance_id": instance_id, "warning": result["error"]}

    return {"instance_id": instance_id, "status": "deployed"}


def _start_executor(instance_id: str, deploy_reg, exec_reg) -> dict:
    """Parse flow, apply overrides, create executor, start it."""
    inst = deploy_reg.get(instance_id)
    if not inst:
        return {"error": f"Instance '{instance_id}' not found"}
    if not inst.flow_path or not Path(inst.flow_path).exists():
        return {"error": f"Flow file not found: {inst.flow_path}"}

    try:
        from tasks import register_all_tasks
        register_all_tasks()

        raw = json.loads(Path(inst.flow_path).read_text(encoding="utf-8"))
        clean = {k: v for k, v in raw.items() if not k.startswith("_")}
        from engine.parser import FlowParser
        flow = FlowParser.parse(clean)

        # Apply service configs
        if inst.service_configs:
            for svc_id, cfg in inst.service_configs.items():
                if svc_id in flow.services:
                    svc = flow.services[svc_id]
                    if hasattr(svc, 'config'):
                        svc.config.update(cfg)

        # Apply service forwards
        _apply_service_forwards(flow, inst.service_overrides)

        from engine.continuous_executor import ContinuousFlowExecutor
        executor = ContinuousFlowExecutor(
            flow,
            max_workers=inst.max_workers,
            max_retries=inst.max_retries,
            parameters=inst.parameters if inst.parameters else None,
        )
        executor.start()
        exec_reg.register(instance_id, executor)
        return {"status": "running"}
    except Exception as e:
        logger.error("Failed to start '%s': %s", instance_id, e)
        deploy_reg.update_status(instance_id, "error", str(e))
        return {"error": str(e)}


def _admin_undeploy_flow(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    iid = body.get("instance_id", "")
    deploy_reg.undeploy(iid)
    return {"status": "undeployed"}


def _admin_start_flow(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    iid = body.get("instance_id", "")
    result = _start_executor(iid, deploy_reg, exec_reg)
    return result


def _admin_stop_flow(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    iid = body.get("instance_id", "")
    executor = exec_reg.get(iid)
    if executor:
        executor.stop()
        exec_reg.unregister(iid)
    return {"status": "stopped"}


def _admin_restart_flow(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    iid = body.get("instance_id", "")
    executor = exec_reg.get(iid)
    if executor:
        executor.stop()
        exec_reg.unregister(iid)
    result = _start_executor(iid, deploy_reg, exec_reg)
    return result


def _admin_hot_reload(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    iid = body.get("instance_id", "")
    executor = exec_reg.get(iid)
    if not executor:
        return {"error": f"No running executor for '{iid}'"}
    inst = deploy_reg.get(iid)
    if not inst or not inst.flow_path:
        return {"error": "Flow path not found"}
    try:
        raw = json.loads(Path(inst.flow_path).read_text(encoding="utf-8"))
        clean = {k: v for k, v in raw.items() if not k.startswith("_")}
        from engine.parser import FlowParser
        flow = FlowParser.parse(clean)
        executor.hot_update(flow)
        return {"status": "reloaded", "version": executor._flow_version}
    except Exception as e:
        return {"error": str(e)}


def _admin_get_queue_stats(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    iid = body.get("instance_id", "")
    executor = exec_reg.get(iid)
    if not executor:
        return {"error": f"No running executor for '{iid}'"}
    return {"queue_stats": executor.get_queue_stats()}


def _admin_get_kpis(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    iid = body.get("instance_id", "")
    executor = exec_reg.get(iid)
    if not executor:
        return {"error": f"No running executor for '{iid}'"}
    status = executor.get_status()
    task_states = executor.get_all_task_states()
    total_ff_in = sum(s.get("flowfiles_in", 0) for s in task_states.values())
    total_ff_out = sum(s.get("flowfiles_out", 0) for s in task_states.values())
    total_errors = sum(s.get("error_count", 0) for s in task_states.values())
    return {
        "tasks_total": status["tasks_total"],
        "tasks_running": status["tasks_running"],
        "tasks_errored": status["tasks_errored"],
        "total_queued": status["total_queued_flowfiles"],
        "total_ff_in": total_ff_in,
        "total_ff_out": total_ff_out,
        "total_errors": total_errors,
        "is_running": status["is_running"],
        "flow_version": status["flow_version"],
    }


def _admin_update_service(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Update service override or config on a deployment."""
    iid = body.get("instance_id", "")
    svc_id = body.get("service_id", "")
    mode = body.get("mode", "local")  # "local" | "global:{id}" | "user:{uid}:{id}"
    config = body.get("config", {})

    inst = deploy_reg.get(iid)
    if not inst:
        return {"error": f"Instance '{iid}' not found"}

    if mode == "local":
        inst.service_configs[svc_id] = config
        inst.service_overrides.pop(svc_id, None)
    else:
        inst.service_overrides[svc_id] = mode
        inst.service_configs.pop(svc_id, None)

    deploy_reg._save_instance(inst)
    return {"status": "updated"}


def _admin_update_parameter(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Update a flow parameter on a deployment."""
    iid = body.get("instance_id", "")
    key = body.get("key", "")
    value = body.get("value", "")

    inst = deploy_reg.get(iid)
    if not inst:
        return {"error": f"Instance '{iid}' not found"}

    inst.parameters[key] = value
    deploy_reg._save_instance(inst)
    return {"status": "updated"}


def _admin_list_templates(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """List available flow templates."""
    # Templates from TemplateService
    templates = tmpl_svc.list_templates()
    # Also list flow files in flows/ directory
    flows_dir = Path("flows")
    if flows_dir.exists():
        for fp in sorted(flows_dir.glob("*.json")):
            try:
                raw = json.loads(fp.read_text(encoding="utf-8"))
                templates.append({
                    "id": raw.get("id", fp.stem),
                    "name": raw.get("name", fp.stem),
                    "description": raw.get("description", ""),
                    "category": "Flow",
                    "path": str(fp),
                    "builtin": False,
                    "tasks_count": len(raw.get("tasks", {})),
                    "services_count": len(raw.get("services", {})),
                })
            except Exception:
                pass
    return templates


def _admin_get_template(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc):
    """Get template detail."""
    template_id = body.get("template_id", "")
    template_path = body.get("template_path", "")

    if template_path and Path(template_path).exists():
        raw = json.loads(Path(template_path).read_text(encoding="utf-8"))
        return raw

    tmpl = tmpl_svc.get_template(template_id)
    if tmpl:
        return tmpl
    return {"error": f"Template '{template_id}' not found"}


# ── Editor actions (imported from admin_editor_actions.py) ──

from tasks.io.admin_editor_actions import (
    _admin_list_task_types, _admin_get_task_schema,
    _admin_list_service_types, _admin_get_service_schema,
    _admin_save_flow_json, _admin_validate_flow, _admin_auto_layout,
)


# ── Action dispatch ──

_ACTIONS = {
    "admin_list_flows": _admin_list_flows,
    "admin_get_flow": _admin_get_flow,
    "admin_deploy_flow": _admin_deploy_flow,
    "admin_undeploy_flow": _admin_undeploy_flow,
    "admin_start_flow": _admin_start_flow,
    "admin_stop_flow": _admin_stop_flow,
    "admin_restart_flow": _admin_restart_flow,
    "admin_hot_reload": _admin_hot_reload,
    "admin_get_queue_stats": _admin_get_queue_stats,
    "admin_get_kpis": _admin_get_kpis,
    "admin_update_service": _admin_update_service,
    "admin_update_parameter": _admin_update_parameter,
    "admin_list_templates": _admin_list_templates,
    "admin_get_template": _admin_get_template,
    "admin_list_task_types": _admin_list_task_types,
    "admin_get_task_schema": _admin_get_task_schema,
    "admin_list_service_types": _admin_list_service_types,
    "admin_get_service_schema": _admin_get_service_schema,
    "admin_save_flow_json": _admin_save_flow_json,
    "admin_validate_flow": _admin_validate_flow,
    "admin_auto_layout": _admin_auto_layout,
}


class AdminActionTask(BaseTask):
    """Route admin API requests to handler functions."""

    TYPE = "adminAction"
    VERSION = "1.0.0"
    NAME = "Admin Action"
    DESCRIPTION = "Handle PawFlow admin API requests"
    ICON = "admin"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {}

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        try:
            raw_body = flowfile.get_content()
            if isinstance(raw_body, bytes):
                raw_body = raw_body.decode("utf-8")
            body = json.loads(raw_body) if raw_body else {}
        except Exception:
            return _error(flowfile, "Invalid JSON body")

        action = body.get("action", "")
        if not action:
            return _error(flowfile, "Missing 'action' field")

        handler = _ACTIONS.get(action)
        if not handler:
            return _error(flowfile, f"Unknown action: {action}")

        try:
            exec_reg, deploy_reg, gsvc_reg, tmpl_svc = _get_registries()
            result = handler(body, exec_reg, deploy_reg, gsvc_reg, tmpl_svc)
            return _json_response(flowfile, result)
        except Exception as e:
            logger.error("Admin action '%s' failed: %s", action, e, exc_info=True)
            return _error(flowfile, str(e), "500")


TaskFactory.register(AdminActionTask)
