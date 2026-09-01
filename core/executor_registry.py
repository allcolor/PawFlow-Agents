"""Global registry for continuous executors.

Executors run in background threads and must survive Streamlit page
refreshes (which reset session_state). This module provides a
process-level singleton that keeps track of all running executors.

It also persists the list of running flows to disk so they can be
automatically restarted after a server restart.

Hooks into DeploymentRegistry to track instance status (running/stopped).
"""

import copy
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.continuous_executor import ContinuousFlowExecutor

logger = logging.getLogger(__name__)

def _get_deployment_registry():
    """Lazy import to avoid circular imports."""
    try:
        from core.deployment_registry import DeploymentRegistry
        return DeploymentRegistry.get_instance()
    except Exception:
        return None


def _apply_service_bindings(flow, service_overrides=None, service_configs=None):
    """Apply deployment-local service config and service forwarding."""
    service_configs = service_configs or {}
    for service_id, config in service_configs.items():
        svc = flow.services.get(service_id)
        if svc is not None and hasattr(svc, "config") and isinstance(config, dict):
            svc.config.update(config)

    service_overrides = service_overrides or {}
    if not service_overrides:
        return
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    for flow_service_id, ref in service_overrides.items():
        if not ref or ref == "local" or flow_service_id not in flow.services:
            continue
        live = None
        if ref.startswith("user:"):
            parts = ref.split(":", 2)
            if len(parts) == 3:
                _, uid, sid = parts
                live = reg.get_live_instance("user", uid, sid)
        elif ref.startswith("global:"):
            live = reg.get_live_instance("global", "", ref.split(":", 1)[1])
        else:
            live = reg.get_live_instance("global", "", ref)
        if live is not None:
            flow.services[flow_service_id] = live


def _merge_service_configs(flow_config, service_configs=None):
    """Merge deployment-local service config into raw flow JSON before parse."""
    if not service_configs:
        return
    services = flow_config.get("services")
    if not isinstance(services, dict):
        return
    for service_id, config in service_configs.items():
        if not isinstance(config, dict):
            continue
        service_def = services.get(service_id)
        if not isinstance(service_def, dict):
            continue
        params = service_def.setdefault("parameters", {})
        if isinstance(params, dict):
            params.update(config)


def _flow_source_dir(flow_path: str = "", flow_fqn: str = "",
                     flow_scope: str = "global", owner: str = "",
                     conversation_id: str = "") -> str:
    """Resolve the directory that contains a deployed flow definition."""
    if flow_path and Path(flow_path).exists():
        return str(Path(flow_path).resolve().parent)
    if not flow_fqn:
        return ""
    try:
        from core.paths import flow_version_file, parse_flow_fqn
        package, flowname, version = parse_flow_fqn(flow_fqn)
        if not version:
            return ""
        candidates = []
        if flow_scope:
            candidates.append(flow_scope)
        if conversation_id:
            candidates.append("conv")
        if owner:
            candidates.append("user")
        candidates.append("global")
        seen = set()
        for scope in candidates:
            if scope in seen:
                continue
            seen.add(scope)
            path = flow_version_file(
                package, flowname, version, scope,
                owner if scope in {"user", "conv"} else "",
                conversation_id if scope == "conv" else "",
            )
            if path.exists():
                return str(path.resolve().parent)
    except Exception:
        logger.debug("Flow source directory lookup failed", exc_info=True)
    return ""


class ExecutorRegistry:
    """Thread-safe global registry for continuous executors.

    Keys are instance_id (which may equal flow_id for legacy deployments).
    """

    _instance: Optional["ExecutorRegistry"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._executors: Dict[str, ContinuousFlowExecutor] = {}
        self._executor_lock = threading.Lock()
        self._restore_lock = threading.Lock()
        self._restored = False

    @classmethod
    def get_instance(cls) -> "ExecutorRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register(self, instance_id: str, executor: ContinuousFlowExecutor):
        """Register a running executor and persist state.

        If an executor already exists for this instance_id, stop it first
        to prevent duplicate execution.
        """
        with self._executor_lock:
            old = self._executors.get(instance_id)
            if old is not None and old is not executor:
                try:
                    old.stop()
                    logger.info("Stopped previous executor for '%s' before registering new one", instance_id)
                except Exception as e:
                    logger.warning("Failed to stop previous executor for '%s': %s", instance_id, e)
            self._executors[instance_id] = executor
            executor._instance_id = instance_id

        # Update deployment status
        dr = _get_deployment_registry()
        if dr:
            dr.update_status(instance_id, "running")

    def unregister(self, instance_id: str, *, allow_required: bool = False):
        """Remove an executor from the registry and persist state.

        Marks the deployment as stopped (does NOT delete it).
        """
        from core.system_flow_guard import (
            ensure_required_system_flow_action_allowed,
        )
        ensure_required_system_flow_action_allowed(
            instance_id, "stopped", allow_required=allow_required)
        try:
            from core.workflow_agent_invocation import (
                WorkflowParentInvocationStore,
            )
            child_run_ids = (
                WorkflowParentInvocationStore.instance().cancel_instance(
                    instance_id))
            if child_run_ids:
                from core.workflow_agent_runtime import WorkflowAgentRuntime
                runtime = WorkflowAgentRuntime.instance()
                for run_id in child_run_ids:
                    runtime.cancel_run(
                        run_id, "parent flow instance stopped", force=True)
        except Exception:
            logger.exception(
                "Failed to propagate parent flow cancellation for %s",
                instance_id)
        with self._executor_lock:
            self._executors.pop(instance_id, None)

        # Mark deployment as stopped (not deleted)
        dr = _get_deployment_registry()
        if dr:
            dr.update_status(instance_id, "stopped")

    def get(self, instance_id: str) -> Optional[ContinuousFlowExecutor]:
        """Get an executor by instance ID."""
        with self._executor_lock:
            return self._executors.get(instance_id)

    def get_all(self) -> Dict[str, ContinuousFlowExecutor]:
        """Get all registered executors (copy of dict)."""
        with self._executor_lock:
            return dict(self._executors)

    def cleanup_dead(self):
        """Remove executors that have stopped."""
        dead = []
        with self._executor_lock:
            for fid, ex in self._executors.items():
                try:
                    status = ex.get_status()
                    if not status.get("is_running", False):
                        dead.append(fid)
                except Exception:
                    dead.append(fid)
            for fid in dead:
                del self._executors[fid]
        if dead:
                # Update deployment statuses
            dr = _get_deployment_registry()
            if dr:
                for fid in dead:
                    dr.update_status(fid, "stopped")
        return dead

    def count(self) -> int:
        with self._executor_lock:
            return len(self._executors)

    # -- Persistence --

    def restore_from_disk(self, required_instance_id: str = ""):
        """Restore persisted executors, forcing one required instance first.

        Ordinary deployments retain status-driven restoration. The required
        runtime is attempted regardless of its persisted status and a failure
        leaves this registry retryable instead of freezing ``_restored``.
        """
        with self._restore_lock:
            with self._executor_lock:
                if self._restored:
                    return

            _restore_t0 = time.monotonic()
            try:
                from core.service_registry import ServiceRegistry
                _t0 = time.monotonic()
                ServiceRegistry.get_instance().connect_all_enabled("global", "")
                logger.info("Global services connected at startup")
                logger.debug("[startup-timing] global services connect: %.1fms",
                             (time.monotonic() - _t0) * 1000)
            except Exception as e:
                from core.llm_router_migration import LLMRouterMigrationError
                if isinstance(e, LLMRouterMigrationError):
                    raise
                logger.warning("Failed to connect global services: %s", e)

            dr = _get_deployment_registry()
            if dr:
                _t0 = time.monotonic()
                dr._ensure_loaded()
                logger.debug("[startup-timing] deployment registry load: %.1fms",
                             (time.monotonic() - _t0) * 1000)
                deployments = dr.get_all()
                if required_instance_id and required_instance_id not in deployments:
                    raise RuntimeError(
                        f"required deployment is missing: {required_instance_id}")

                ordered = list(deployments.items())
                if required_instance_id:
                    ordered.sort(key=lambda item: item[0] != required_instance_id)

                # Do NOT sync before restore: no executors exist yet in a fresh
                # process, so sync would incorrectly persist them as stopped.
                for iid, inst in ordered:
                    is_required = bool(
                        required_instance_id and iid == required_instance_id)
                    if not is_required and inst.status != "running":
                        continue
                    if self.get(iid) is not None:
                        continue
                    _inst_t0 = time.monotonic()
                    restored = self._restore_instance(
                        iid, inst.flow_path,
                        inst.max_workers, inst.max_retries,
                        flow_fqn=getattr(inst, 'flow_fqn', ''),
                        flow_scope=getattr(inst, 'flow_scope', '') or '',
                        parameters=inst.parameters,
                        service_overrides=inst.service_overrides,
                        service_configs=inst.service_configs,
                        owner=inst.owner or "",
                        conversation_id=inst.conversation_id or "",
                        agent_name=getattr(inst, 'agent_name', '') or "",
                        strict_initialization=is_required,
                        require_http_routes=is_required,
                    )
                    logger.debug("[startup-timing] restore instance %s: %.1fms",
                                 iid, (time.monotonic() - _inst_t0) * 1000)
                    if is_required and not restored:
                        raise RuntimeError(
                            f"required executor failed startup: {iid}")

            elif required_instance_id:
                raise RuntimeError(
                    f"deployment registry unavailable for required flow: "
                    f"{required_instance_id}")

            with self._executor_lock:
                self._restored = True
            logger.debug(
                "[startup-timing] executor registry restore total: %.1fms",
                (time.monotonic() - _restore_t0) * 1000)


    def _restore_instance(self, instance_id: str, flow_path: str,
                          max_workers: int = 4, max_retries: int = 0,
                          flow_fqn: str = "",
                          flow_scope: str = "",
                          parameters: Optional[Dict[str, Any]] = None,
                          service_overrides: Optional[Dict[str, str]] = None,
                          service_configs: Optional[Dict[str, Dict[str, Any]]] = None,
                          owner: str = "",
                          conversation_id: str = "",
                          agent_name: str = "",
                          enabled_one_shot_root_task_ids: Optional[List[str]] = None,
                          strict_initialization: bool = False,
                          require_http_routes: bool = False) -> bool:
        """Restore a single executor from the repository or flow_path."""
        executor = None
        try:
            _restore_t0 = time.monotonic()
            from tasks import register_all_tasks
            _t0 = time.monotonic()
            register_all_tasks()
            logger.debug("[startup-timing] %s register_all_tasks: %.1fms",
                        instance_id, (time.monotonic() - _t0) * 1000)

            raw = None
            # Try repository FQN first
            if flow_fqn:
                try:
                    _t0 = time.monotonic()
                    from core.repository import ScopedRepository
                    repo = ScopedRepository.instance()
                    scopes = []
                    if flow_scope:
                        scopes.append(flow_scope)
                    if conversation_id:
                        scopes.append("conv")
                    if owner:
                        scopes.append("user")
                    scopes.append("global")
                    seen = set()
                    for scope in scopes:
                        if scope in seen:
                            continue
                        seen.add(scope)
                        raw = repo.get_flow(
                            flow_fqn, scope,
                            user_id=owner,
                            conv_id=conversation_id if scope == "conv" else "")
                        if raw:
                            break
                    if raw:
                        logger.info("Restored '%s' from repository (%s)",
                                    instance_id, flow_fqn)
                    logger.debug("[startup-timing] %s repository lookup: %.1fms",
                                instance_id, (time.monotonic() - _t0) * 1000)
                except Exception as e:
                    logger.debug("Repository lookup failed for '%s': %s",
                                 flow_fqn, e)
            # Fallback to flow_path
            if raw is None:
                _t0 = time.monotonic()
                if not flow_path or not Path(flow_path).exists():
                    logger.warning("Cannot restore '%s': not found (fqn=%s, path=%s)",
                                   instance_id, flow_fqn, flow_path)
                    if strict_initialization:
                        raise FileNotFoundError(
                            f"required flow definition not found: {flow_path or flow_fqn}")
                    return False
                with open(flow_path, "r", encoding="utf-8") as ff:
                    raw = json.load(ff)
                logger.debug("[startup-timing] %s flow file load: %.1fms",
                            instance_id, (time.monotonic() - _t0) * 1000)
            clean = {k: copy.deepcopy(v) for k, v in raw.items() if not k.startswith("_")}
            source_dir = _flow_source_dir(
                flow_path, flow_fqn, flow_scope,
                owner, conversation_id)
            if source_dir:
                clean["_source_dir"] = source_dir
            # Expose the unique deploy-time instance_id as a reserved flow
            # parameter so flows can mint per-instance values (e.g. a webhook
            # route /hook/${_instance_id} that never collides across deploys).
            # It is injected last so it always wins over any supplied value.
            clean["parameters"] = {
                **(clean.get("parameters") or {}),
                **(parameters or {}),
                "_instance_id": instance_id,
            }
            _merge_service_configs(clean, service_configs)
            from engine.parser import FlowParser
            _t0 = time.monotonic()
            flow = FlowParser.parse(clean)
            _apply_service_bindings(flow, service_overrides, service_configs)
            logger.debug("[startup-timing] %s parse/bind flow: %.1fms",
                        instance_id, (time.monotonic() - _t0) * 1000)

            # Allow flow parameters to override max_workers
            _eff_workers = max_workers
            _flow_params = clean.get('parameters', {})
            if parameters and 'max_workers' in parameters:
                _eff_workers = int(parameters['max_workers'])
            elif 'max_workers' in _flow_params:
                _eff_workers = int(_flow_params['max_workers'])
            executor = ContinuousFlowExecutor(
                flow,
                max_workers=_eff_workers,
                max_retries=max_retries,
                parameters=parameters if parameters else None,
                runtime_context={
                    "user_id": owner,
                    "conversation_id": conversation_id,
                    "scope": "conversation" if conversation_id else "user" if owner else "global",
                    "agent_name": agent_name,
                },
                enabled_one_shot_root_task_ids=enabled_one_shot_root_task_ids,
                strict_initialization=strict_initialization,
            )
            if flow_fqn:
                executor._flow_fqn = flow_fqn

            # Try to restore checkpoint
            if executor._checkpoint_mgr:
                _t0 = time.monotonic()
                cp = executor._checkpoint_mgr.load_latest_checkpoint()
                if cp:
                    flowfiles = executor._checkpoint_mgr.restore_flowfiles(cp)
                    for conn_key, ffs in flowfiles.items():
                        src_id, tgt_id = conn_key
                        conn = executor._connections.get_connection(src_id, tgt_id)
                        if conn:
                            for ff_item in ffs:
                                conn.enqueue(ff_item)
                    logger.info("Restored checkpoint for '%s'", instance_id)
                logger.debug("[startup-timing] %s checkpoint restore: %.1fms",
                            instance_id, (time.monotonic() - _t0) * 1000)

            _t0 = time.monotonic()
            executor.start()
            if strict_initialization or require_http_routes:
                executor.validate_startup_readiness(
                    require_http_routes=require_http_routes)
            logger.debug("[startup-timing] %s executor.start: %.1fms",
                        instance_id, (time.monotonic() - _t0) * 1000)
            self.register(instance_id, executor)
            if require_http_routes:
                from core.runtime_readiness import mark_required_flow_ready
                mark_required_flow_ready(instance_id)
            logger.info("Restored executor for '%s'", instance_id)
            logger.debug("[startup-timing] %s restore total: %.1fms",
                        instance_id, (time.monotonic() - _restore_t0) * 1000)
            return True
        except Exception as e:
            logger.error("Failed to restore executor for '%s': %s", instance_id, e, exc_info=True)
            if executor is not None:
                try:
                    executor.stop()
                except Exception:
                    logger.warning(
                        "Failed to clean partial executor for '%s'", instance_id,
                        exc_info=True)
            if require_http_routes:
                from core.runtime_readiness import mark_required_flow_failed
                mark_required_flow_failed(instance_id, str(e))
            # Mark as error in deployment registry
            if _get_deployment_registry():
                _get_deployment_registry().update_status(instance_id, "error", str(e))
            return False
