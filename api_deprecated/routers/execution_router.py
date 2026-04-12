"""Execution router — run flows (batch & continuous), inject FlowFiles."""

import base64
import logging
import threading
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from api.auth import require_permission
from core.flow_service import FlowService
from engine.continuous_executor import ContinuousFlowExecutor
from engine.provenance import get_provenance_repository
from engine.debugger import FlowDebugger
from engine.flow_state import FlowStateManager
from core import FlowFile

logger = logging.getLogger(__name__)

router = APIRouter()

# Store active continuous executors
_continuous_executors: Dict[str, ContinuousFlowExecutor] = {}
_executors_lock = threading.Lock()

_flow_service = FlowService()
_flow_state = FlowStateManager()


def get_flow_service() -> FlowService:
    _flow_service.initialize()
    return _flow_service


# -- Models --

class BatchExecuteRequest(BaseModel):
    flow_id: str
    input_content: Optional[str] = None  # base64 encoded
    input_attributes: Optional[Dict[str, str]] = None
    parameters: Optional[Dict[str, Any]] = None  # override flow.parameters
    max_workers: int = 4
    max_retries: int = 3


class ContinuousStartRequest(BaseModel):
    flow_id: str
    parameters: Optional[Dict[str, Any]] = None  # override flow.parameters
    max_workers: int = 8
    max_retries: int = 3
    enable_checkpoints: bool = True
    checkpoint_interval: float = 30.0


class InjectRequest(BaseModel):
    content: Optional[str] = None  # base64 encoded
    content_text: Optional[str] = None  # plain text alternative
    attributes: Optional[Dict[str, str]] = None
    entry_task_id: Optional[str] = None


class TaskActionRequest(BaseModel):
    action: str  # start, stop, restart, disable


class UpdateTaskRequest(BaseModel):
    config: Dict[str, Any]
    new_type: Optional[str] = None


# -- Batch Execution --

@router.post("/batch")
def execute_batch(
    req: BatchExecuteRequest,
    _=Depends(require_permission("flow.execute")),
    svc: FlowService = Depends(get_flow_service),
):
    """Execute a flow in batch mode (synchronous)."""
    # Find and parse the flow
    flow = _find_flow(req.flow_id, svc)

    # Create input FlowFile if provided
    input_ffs = []
    if req.input_content:
        content = base64.b64decode(req.input_content)
        ff = FlowFile(content=content, attributes=req.input_attributes or {})
        input_ffs.append(ff)
    elif req.input_attributes:
        ff = FlowFile(content=b"", attributes=req.input_attributes)
        input_ffs.append(ff)

    result = ContinuousFlowExecutor.run_batch(
        flow,
        input_flowfiles=input_ffs if input_ffs else None,
        parameters=req.parameters,
        max_workers=req.max_workers,
        max_retries=req.max_retries,
    )

    return {
        "success": result.success,
        "flow_id": result.flow_id,
        "duration_ms": result.duration_ms,
        "errors": result.errors,
        "statistics": result.statistics,
        "outputs": [
            {
                "process_id": ff.process_id,
                "size": ff.size(),
                "attributes": ff.get_attributes(),
                "content_b64": base64.b64encode(ff.get_content()).decode()
                if ff.size() < 1024 * 1024 else None,  # only inline < 1MB
            }
            for ff in (result.output_flowfiles or [])
        ],
    }


# -- Continuous Execution --

@router.post("/continuous/start")
def start_continuous(
    req: ContinuousStartRequest,
    _=Depends(require_permission("flow.execute")),
    svc: FlowService = Depends(get_flow_service),
):
    """Start a continuous flow executor."""
    with _executors_lock:
        if req.flow_id in _continuous_executors:
            ex = _continuous_executors[req.flow_id]
            if ex.is_running:
                raise HTTPException(409, f"Flow '{req.flow_id}' is already running")

    flow = _find_flow(req.flow_id, svc)
    provenance = get_provenance_repository()

    # Save flow config version before starting
    flow_config = svc.flow_to_dict(flow) if hasattr(svc, 'flow_to_dict') else {}
    if flow_config:
        pass  # flow versioning removed

    executor = ContinuousFlowExecutor(
        flow,
        max_workers=req.max_workers,
        max_retries=req.max_retries,
        provenance=provenance,
        enable_checkpoints=req.enable_checkpoints,
        checkpoint_interval=req.checkpoint_interval,
        parameters=req.parameters,
    )
    executor.start()

    with _executors_lock:
        _continuous_executors[req.flow_id] = executor

    # Persist running state for crash recovery
    _flow_state.register_flow(
        flow_id=req.flow_id,
        parameters=req.parameters,
        max_workers=req.max_workers,
        max_retries=req.max_retries,
        enable_checkpoints=req.enable_checkpoints,
        checkpoint_interval=req.checkpoint_interval,
    )

    return {"status": "started", "flow_id": req.flow_id}


@router.post("/continuous/{flow_id}/stop")
def stop_continuous(
    flow_id: str,
    _=Depends(require_permission("flow.execute")),
):
    """Stop a continuous flow executor."""
    executor = _get_executor(flow_id)
    executor.stop()
    _flow_state.unregister_flow(flow_id)
    return {"status": "stopped", "flow_id": flow_id}


@router.delete("/continuous/{flow_id}")
def destroy_continuous(
    flow_id: str,
    _=Depends(require_permission("flow.execute")),
):
    """Stop and remove a continuous flow executor."""
    executor = _get_executor(flow_id)
    if executor.is_running:
        executor.stop()
    with _executors_lock:
        _continuous_executors.pop(flow_id, None)
    _flow_state.unregister_flow(flow_id)
    return {"status": "destroyed", "flow_id": flow_id}


@router.get("/continuous")
def list_continuous(
    _=Depends(require_permission("monitor.view")),
):
    """List all continuous executors."""
    with _executors_lock:
        return {
            flow_id: ex.get_status()
            for flow_id, ex in _continuous_executors.items()
        }


@router.get("/continuous/{flow_id}/status")
def get_continuous_status(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
):
    """Get status of a continuous executor."""
    executor = _get_executor(flow_id)
    return executor.get_status()


@router.get("/continuous/{flow_id}/tasks")
def get_continuous_tasks(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
):
    """Get all task states for a continuous executor."""
    executor = _get_executor(flow_id)
    return executor.get_all_task_states()


@router.get("/continuous/{flow_id}/queues")
def get_continuous_queues(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
):
    """Get queue stats for a continuous executor."""
    executor = _get_executor(flow_id)
    return executor.get_queue_stats()


@router.get("/continuous/{flow_id}/versions")
def get_version_history(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
):
    """Get flow version history."""
    executor = _get_executor(flow_id)
    return executor.get_version_history()


# -- Inject FlowFiles --

@router.post("/continuous/{flow_id}/inject")
def inject_flowfile(
    flow_id: str,
    req: InjectRequest,
    _=Depends(require_permission("flow.execute")),
):
    """Inject a FlowFile into a running continuous executor."""
    executor = _get_executor(flow_id)
    if not executor.is_running:
        raise HTTPException(409, "Executor is not running")

    if req.content:
        content = base64.b64decode(req.content)
    elif req.content_text:
        content = req.content_text.encode("utf-8")
    else:
        content = b""

    ff = FlowFile(content=content, attributes=req.attributes or {})
    success = executor.inject(ff, entry_task_id=req.entry_task_id)

    return {"injected": success, "process_id": ff.process_id}


# -- Task management within continuous executor --

@router.post("/continuous/{flow_id}/tasks/{task_id}")
def task_action(
    flow_id: str,
    task_id: str,
    req: TaskActionRequest,
    _=Depends(require_permission("flow.execute")),
):
    """Perform an action on a task (start, stop, restart, disable)."""
    executor = _get_executor(flow_id)
    actions = {
        "start": executor.start_task,
        "stop": executor.stop_task,
        "restart": executor.restart_task,
        "disable": executor.disable_task,
    }
    fn = actions.get(req.action)
    if not fn:
        raise HTTPException(400, f"Invalid action: {req.action}. Valid: {list(actions.keys())}")

    success = fn(task_id)
    return {"action": req.action, "task_id": task_id, "success": success}


@router.put("/continuous/{flow_id}/tasks/{task_id}")
def update_task(
    flow_id: str,
    task_id: str,
    req: UpdateTaskRequest,
    _=Depends(require_permission("flow.edit")),
):
    """Hot-swap a task's configuration."""
    executor = _get_executor(flow_id)
    success = executor.update_task(task_id, req.config, new_type=req.new_type)
    return {"updated": success, "task_id": task_id}


@router.post("/continuous/{flow_id}/checkpoint")
def save_checkpoint(
    flow_id: str,
    _=Depends(require_permission("flow.execute")),
):
    """Manually trigger a checkpoint."""
    executor = _get_executor(flow_id)
    path = executor.save_checkpoint_now()
    return {"checkpoint_path": path}


# -- Data Preview Endpoints --

def _get_preview_manager(executor: ContinuousFlowExecutor):
    """Get or create a DataPreviewManager for an executor."""
    if not hasattr(executor, '_data_preview') or executor._data_preview is None:
        from engine.data_preview import DataPreviewManager
        preview = DataPreviewManager()
        preview.attach(executor)
    return executor._data_preview


@router.post("/continuous/{flow_id}/preview/enable")
def enable_preview(
    flow_id: str,
    source_id: str = "",
    target_id: str = "",
    all: bool = False,
    _=Depends(require_permission("flow.execute")),
):
    """Enable data preview on a connection."""
    executor = _get_executor(flow_id)
    preview = _get_preview_manager(executor)
    if all:
        preview.enable_all()
        return {"status": "enabled_all"}
    elif source_id and target_id:
        preview.enable_connection(source_id, target_id)
        return {"status": "enabled", "connection": f"{source_id} -> {target_id}"}
    else:
        raise HTTPException(400, "Provide source_id and target_id, or set all=true")


@router.post("/continuous/{flow_id}/preview/disable")
def disable_preview(
    flow_id: str,
    source_id: str = "",
    target_id: str = "",
    all: bool = False,
    _=Depends(require_permission("flow.execute")),
):
    """Disable data preview on a connection."""
    executor = _get_executor(flow_id)
    preview = _get_preview_manager(executor)
    if all:
        preview.disable_all()
        return {"status": "disabled_all"}
    elif source_id and target_id:
        preview.disable_connection(source_id, target_id)
        return {"status": "disabled", "connection": f"{source_id} -> {target_id}"}
    else:
        raise HTTPException(400, "Provide source_id and target_id, or set all=true")


@router.get("/continuous/{flow_id}/preview/samples")
def get_preview_samples(
    flow_id: str,
    source_id: str = None,
    target_id: str = None,
    limit: int = 10,
    _=Depends(require_permission("monitor.view")),
):
    """Get data preview samples."""
    executor = _get_executor(flow_id)
    preview = _get_preview_manager(executor)
    return preview.get_samples(source_id=source_id, target_id=target_id, limit=limit)


@router.get("/continuous/{flow_id}/preview/connections")
def preview_connections(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
):
    """List connections with captured data."""
    executor = _get_executor(flow_id)
    preview = _get_preview_manager(executor)
    return preview.get_connections_with_data()


@router.delete("/continuous/{flow_id}/preview")
def clear_preview(
    flow_id: str,
    source_id: str = None,
    target_id: str = None,
    _=Depends(require_permission("flow.execute")),
):
    """Clear captured preview data."""
    executor = _get_executor(flow_id)
    preview = _get_preview_manager(executor)
    preview.clear(source_id=source_id, target_id=target_id)
    return {"status": "cleared"}


# -- Debug Endpoints --

def _get_debugger(executor: ContinuousFlowExecutor) -> FlowDebugger:
    """Get or create a debugger for an executor."""
    if not hasattr(executor, '_debugger') or executor._debugger is None:
        debugger = FlowDebugger()
        debugger.attach(executor)
    return executor._debugger


@router.post("/continuous/{flow_id}/debug/breakpoint/{task_id}")
def add_breakpoint(
    flow_id: str,
    task_id: str,
    condition: str = "",
    log_message: str = "",
    _=Depends(require_permission("flow.execute")),
):
    """Add a breakpoint on a task."""
    executor = _get_executor(flow_id)
    debugger = _get_debugger(executor)
    bp = debugger.add_breakpoint(task_id, condition=condition, log_message=log_message)
    return {"task_id": bp.task_id, "condition": bp.condition, "log_message": bp.log_message}


@router.delete("/continuous/{flow_id}/debug/breakpoint/{task_id}")
def remove_breakpoint(
    flow_id: str,
    task_id: str,
    _=Depends(require_permission("flow.execute")),
):
    """Remove a breakpoint from a task."""
    executor = _get_executor(flow_id)
    debugger = _get_debugger(executor)
    removed = debugger.remove_breakpoint(task_id)
    return {"removed": removed, "task_id": task_id}


@router.get("/continuous/{flow_id}/debug/status")
def debug_status(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
):
    """Get debugger status."""
    executor = _get_executor(flow_id)
    debugger = _get_debugger(executor)
    return debugger.get_status()


@router.post("/continuous/{flow_id}/debug/continue")
def debug_continue(
    flow_id: str,
    _=Depends(require_permission("flow.execute")),
):
    """Resume execution until next breakpoint."""
    executor = _get_executor(flow_id)
    debugger = _get_debugger(executor)
    debugger.continue_execution()
    return {"action": "continue"}


@router.post("/continuous/{flow_id}/debug/step")
def debug_step(
    flow_id: str,
    _=Depends(require_permission("flow.execute")),
):
    """Step one task then pause."""
    executor = _get_executor(flow_id)
    debugger = _get_debugger(executor)
    debugger.step()
    return {"action": "step"}


@router.post("/continuous/{flow_id}/debug/stop")
def debug_stop(
    flow_id: str,
    _=Depends(require_permission("flow.execute")),
):
    """Stop debugging and resume normal execution."""
    executor = _get_executor(flow_id)
    debugger = _get_debugger(executor)
    debugger.stop_debugging()
    return {"action": "stop"}


@router.get("/continuous/{flow_id}/debug/snapshots")
def debug_snapshots(
    flow_id: str,
    task_id: str = None,
    limit: int = 50,
    _=Depends(require_permission("monitor.view")),
):
    """Get FlowFile debug snapshots."""
    executor = _get_executor(flow_id)
    debugger = _get_debugger(executor)
    return debugger.get_snapshots(task_id=task_id, limit=limit)


# -- Helpers --

def _find_flow(flow_id: str, svc: FlowService):
    """Find and parse a flow by ID."""
    flow_files = svc.list_flows()
    for fp in flow_files:
        try:
            flow = svc.parse_from_file(fp)
            if flow.id == flow_id:
                return flow
        except Exception:
            continue
    raise HTTPException(404, f"Flow '{flow_id}' not found")


def _get_executor(flow_id: str) -> ContinuousFlowExecutor:
    """Get an active continuous executor."""
    with _executors_lock:
        executor = _continuous_executors.get(flow_id)
    if not executor:
        raise HTTPException(404, f"No continuous executor for flow '{flow_id}'")
    return executor


# -- Crash Recovery --

def recover_flows_on_startup():
    """Called at server startup: restart flows that were running before crash.

    Checks two sources:
    1. FlowStateManager (data/config/running_flows.json) — flows started via API
    2. DeploymentRegistry (data/deployments/) — flows started via GUI

    For each flow that was 'running', attempts to recreate executor and start it.
    """
    recovered = 0
    failed = 0

    # --- Source 1: FlowStateManager (API-started flows) ---
    _flow_state.load()
    to_recover = _flow_state.get_flows_to_recover()

    if to_recover:
        logger.info(f"FlowState recovery: {len(to_recover)} flow(s) to restore")
        svc = FlowService()
        svc.initialize()
        provenance = get_provenance_repository()

        for entry in to_recover:
            flow_id = entry.flow_id
            try:
                flow = _find_flow_silent(flow_id, svc)
                if flow is None:
                    _flow_state.mark_recovery_failed(flow_id, f"Flow config '{flow_id}' not found")
                    failed += 1
                    continue

                executor = ContinuousFlowExecutor(
                    flow,
                    max_workers=entry.max_workers,
                    max_retries=entry.max_retries,
                    provenance=provenance,
                    enable_checkpoints=entry.enable_checkpoints,
                    checkpoint_interval=entry.checkpoint_interval,
                    parameters=entry.parameters,
                )
                executor.start()

                with _executors_lock:
                    _continuous_executors[flow_id] = executor

                _flow_state.mark_recovered(flow_id)
                recovered += 1
                logger.info(f"Flow '{flow_id}' recovered (FlowState)")

            except Exception as e:
                error_msg = f"Recovery failed: {e}"
                _flow_state.mark_recovery_failed(flow_id, error_msg)
                failed += 1
                logger.error(f"Flow '{flow_id}' {error_msg}")

    # --- Source 2: DeploymentRegistry (GUI-started flows) ---
    try:
        from core.executor_registry import ExecutorRegistry
        reg = ExecutorRegistry.get_instance()
        reg.restore_from_disk()
        dr_count = reg.count()
        if dr_count:
            recovered += dr_count
            logger.info(f"DeploymentRegistry recovery: {dr_count} flow(s) restored")
    except Exception as e:
        logger.warning(f"DeploymentRegistry recovery skipped: {e}")

    if recovered or failed:
        logger.info(f"Crash recovery complete: {recovered} recovered, {failed} failed")


def _find_flow_silent(flow_id: str, svc: FlowService):
    """Find a flow without raising HTTPException."""
    flow_files = svc.list_flows()
    for fp in flow_files:
        try:
            flow = svc.parse_from_file(fp)
            if flow.id == flow_id:
                return flow
        except Exception:
            continue
    return None


# -- Recovery Status Endpoints --

@router.get("/recovery/status")
def get_recovery_status(
    _=Depends(require_permission("monitor.view")),
):
    """Get crash recovery status for all flows."""
    _flow_state.load()
    entries = _flow_state.get_all_entries()
    return {
        "flows": [e.to_dict() for e in entries],
        "total": len(entries),
        "running": sum(1 for e in entries if e.status == "running"),
        "crashed": sum(1 for e in entries if e.status == "crashed"),
        "recovery_failed": sum(1 for e in entries if e.status == "recovery_failed"),
    }


@router.post("/recovery/{flow_id}/retry")
def retry_recovery(
    flow_id: str,
    _=Depends(require_permission("flow.execute")),
    svc: FlowService = Depends(get_flow_service),
):
    """Retry recovery of a failed flow."""
    entry = _flow_state.get_entry(flow_id)
    if not entry:
        raise HTTPException(404, f"No state entry for flow '{flow_id}'")
    if entry.status not in ("recovery_failed", "crashed"):
        raise HTTPException(400, f"Flow '{flow_id}' is not in a recoverable state (status: {entry.status})")

    try:
        flow = _find_flow(flow_id, svc)
        provenance = get_provenance_repository()
        executor = ContinuousFlowExecutor(
            flow,
            max_workers=entry.max_workers,
            max_retries=entry.max_retries,
            provenance=provenance,
            enable_checkpoints=entry.enable_checkpoints,
            checkpoint_interval=entry.checkpoint_interval,
            parameters=entry.parameters,
        )
        executor.start()
        with _executors_lock:
            _continuous_executors[flow_id] = executor
        _flow_state.mark_recovered(flow_id)
        return {"status": "recovered", "flow_id": flow_id}
    except Exception as e:
        _flow_state.mark_recovery_failed(flow_id, str(e))
        raise HTTPException(500, f"Recovery failed: {e}")


@router.delete("/recovery/{flow_id}")
def dismiss_recovery(
    flow_id: str,
    _=Depends(require_permission("flow.execute")),
):
    """Dismiss a crashed/failed flow from recovery list."""
    _flow_state.unregister_flow(flow_id)
    return {"status": "dismissed", "flow_id": flow_id}


# -- Flow Version / Downgrade Endpoints --

@router.get("/continuous/{flow_id}/config-versions")
def list_config_versions(
    flow_id: str,
    _=Depends(require_permission("monitor.view")),
):
    """List saved config versions for a flow."""
    return []  # flow versioning removed


@router.get("/continuous/{flow_id}/config-versions/{version}")
def get_config_version(
    flow_id: str,
    version: int,
    _=Depends(require_permission("monitor.view")),
):
    """Get a specific config version."""
    config = None  # flow versioning removed
    if config is None:
        raise HTTPException(404, f"Version {version} not found for flow '{flow_id}'")
    return config


@router.post("/continuous/{flow_id}/downgrade/{version}")
def downgrade_flow(
    flow_id: str,
    version: int,
    _=Depends(require_permission("flow.edit")),
    svc: FlowService = Depends(get_flow_service),
):
    """Downgrade a running flow to a previous config version.

    1. Loads the old config version
    2. Saves the current config as a new version (backup)
    3. Writes the old config to the flow file
    4. If flow is running: hot-updates with update_flow()
    """
    old_config = None  # flow versioning removed
    if old_config is None:
        raise HTTPException(404, f"Version {version} not found for flow '{flow_id}'")

    # Save current as backup
    try:
        current_flow = _find_flow(flow_id, svc)
        current_config = svc.flow_to_dict(current_flow) if hasattr(svc, 'flow_to_dict') else {}
        if current_config:
            pass  # flow versioning removed
    except Exception:
        pass  # Flow might not exist yet

    # Write old config to flow file
    flow_path = f"flows/{flow_id}.json"
    try:
        import json
        with open(flow_path, "w") as f:
            json.dump(old_config, f, indent=2)
    except OSError as e:
        raise HTTPException(500, f"Failed to write flow config: {e}")

    # If flow is running, hot-update
    result = {"status": "downgraded", "flow_id": flow_id, "version": version, "hot_updated": False}

    with _executors_lock:
        executor = _continuous_executors.get(flow_id)

    if executor and executor.is_running:
        try:
            new_flow = svc.parse_from_file(flow_path)
            success = executor.update_flow(new_flow)
            result["hot_updated"] = success
            if success:
                pass  # flow versioning removed
        except Exception as e:
            result["hot_update_error"] = str(e)

    return result
