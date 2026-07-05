"""Continuous Flow Executor - NiFi-style execution with queues and backpressure.

The single execution engine for PawFlow:
- Tasks are scheduled in a loop when they have input and are RUNNING
- FlowFiles live in Connection queues between tasks
- Transaction model: FlowFile is only removed from input queue after
  successful processing. On error, it stays in the queue.
- On task error: task goes to ERROR state, stops consuming,
  input queues fill up, backpressure cascades to entry point.
- Supports flow version updates (swap tasks, keep queues).
- Auto-stop: flows without persistent sources stop when all queues are empty.
- Batch mode: run_batch() provides synchronous one-shot execution.
"""

import logging
import threading
import time
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor

from core import Flow, FlowFile, Task, FlowError
from core.task_state import TaskStateManager
from core.connection import Connection, ConnectionManager
from core.parameter_context import ParameterContext
from engine.provenance import ProvenanceRepository
from engine.checkpoint import CheckpointManager

from engine._exec_types import TaskStats, ExecutionResult
from engine._continuous_exec_run import _ContinuousExecRunMixin
from engine._continuous_exec_control import _ContinuousExecControlMixin

logger = logging.getLogger(__name__)

__all__ = ["TaskStats", "ExecutionResult", "ContinuousFlowExecutor"]


class ContinuousFlowExecutor(_ContinuousExecRunMixin, _ContinuousExecControlMixin):
    """NiFi-style continuous executor.

    Lifecycle:
        executor = ContinuousFlowExecutor(flow)
        executor.start()           # all tasks -> RUNNING, scheduler starts
        executor.inject(flowfile)  # inject FlowFile at entry point
        ...                        # FlowFiles flow through the DAG
        executor.stop()            # all tasks -> STOPPED, scheduler stops

    On task error:
        - Task transitions to ERROR
        - FlowFile stays in the input queue (NOT lost)
        - Input queues fill up -> backpressure cascades upstream
        - Fix the problem, then: executor.restart_task("task_id")
        - Task resumes consuming from its queue

    Flow updates:
        executor.update_task("task_id", new_config)  # hot-swap config
        executor.update_flow(new_flow)                # structural update
    """

    def __init__(self, flow: Flow,
                 max_workers: int = 32,
                 max_retries: int = 3,
                 schedule_interval: float = 0.05,
                 provenance: Optional[ProvenanceRepository] = None,
                 checkpoint_interval: float = 30.0,
                 enable_checkpoints: bool = True,
                 parameters: Optional[Dict[str, Any]] = None,
                 runtime_context: Optional[Dict[str, Any]] = None,
                 enabled_one_shot_root_task_ids: Optional[List[str]] = None):
        self._flow = flow
        self._max_workers = max_workers
        self._max_retries = max_retries
        self._schedule_interval = schedule_interval
        self._provenance = provenance

        self._task_states = TaskStateManager()
        self._connections = ConnectionManager()
        self._tasks: Dict[str, Task] = {}
        self._task_retry_counts: Dict[str, int] = {}

        self._pool: Optional[ThreadPoolExecutor] = None
        self._interactive_pool: Optional[ThreadPoolExecutor] = None
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._schedule_wake = threading.Event()
        self._lock = threading.Lock()

        # Track concurrent executions per task (counter, not boolean)
        self._in_flight: Dict[str, int] = {}
        self._max_instances: Dict[str, int] = {}  # populated in _build()

        self._flow_version = 1
        self._version_history: List[Dict[str, Any]] = []

        # Checkpoint system
        self._checkpoint_interval = checkpoint_interval
        self._enable_checkpoints = enable_checkpoints
        self._checkpoint_mgr = CheckpointManager(flow.id) if enable_checkpoints else None
        self._last_checkpoint_time = 0.0

        # Parameter context
        ctx = ParameterContext(flow.parameters)
        if parameters:
            ctx = ctx.with_overrides(parameters)
        self._parameter_context = ctx
        self._runtime_context = dict(runtime_context or {})

        # Debugger (attached externally via FlowDebugger.attach())
        self._debugger = None

        # Data preview (attached externally via DataPreviewManager.attach())
        self._data_preview = None

        self._has_persistent_sources = False
        self._enabled_one_shot_root_task_ids = (
            set(enabled_one_shot_root_task_ids)
            if enabled_one_shot_root_task_ids is not None else None
        )
        self._idle_cycles = 0
        self._auto_stop_threshold = 3  # idle cycles before auto-stop
        self._exit_results: List[FlowFile] = []  # outputs from exit tasks

        self._build(flow)

    # -- Setup --

    def _build(self, flow: Flow):
        """Build internal state from a Flow object."""
        # Register tasks and inject services + parameter context
        for task_id, task in flow.tasks.items():
            self._tasks[task_id] = task
            task_type = task.TYPE if hasattr(task, 'TYPE') else ''
            self._task_states.register_task(task_id, task_type)
            self._task_retry_counts[task_id] = 0
            self._max_instances[task_id] = getattr(task, '_max_instances', 1)
            self._inject_runtime_context(task)
            if hasattr(task, 'set_scheduler_wake'):
                task.set_scheduler_wake(self._wake_scheduler)
            # Inject controller services into task
            if flow.services and hasattr(task, 'set_services'):
                task.set_services(flow.services)
            # Inject parameter context into task
            if hasattr(task, 'set_parameter_context'):
                task.set_parameter_context(self._parameter_context)
            # Inject flow source directory for asset resolution
            if flow.source_dir and hasattr(task, 'set_flow_source_dir'):
                task.set_flow_source_dir(flow.source_dir)

        # Resolve ${*} in service configs before connecting
        self._resolve_service_configs(flow)

        # Initialize services
        for service_id, service in flow.services.items():
            try:
                service.connect()
                logger.info(f"Service '{service_id}' connected")
            except Exception as e:
                logger.error(f"Service '{service_id}' failed to connect: {e}")

        # Initialize tasks (e.g. register HTTP routes) after services are ready
        for task_id, task in self._tasks.items():
            try:
                if hasattr(task, 'initialize'):
                    task.initialize()
                    logger.debug(f"Task '{task_id}' initialized")
            except Exception as e:
                logger.error(f"Task '{task_id}' initialization failed: {e}")

        # Detect persistent sources (listeners, pollers, cron triggers)
        self._has_persistent_sources = any(
            getattr(task, 'is_persistent_source', False)
            for task in self._tasks.values()
        )

        # Build connections from flow relations
        flow_dict = {
            "tasks": {tid: {} for tid in flow.tasks},
            "relations": flow.relations,
        }
        self._connections.build_from_flow(flow_dict)

    def _inject_runtime_context(self, task: Task):
        """Attach deployment runtime context to runtime-aware tasks."""
        user_id = str(self._runtime_context.get("user_id") or "")
        conversation_id = str(self._runtime_context.get("conversation_id") or "")
        scope = str(self._runtime_context.get("scope") or "")
        if not scope:
            scope = "conversation" if conversation_id else "user" if user_id else ""
        agent_name = str(self._runtime_context.get("agent_name") or "")

        if hasattr(task, "set_runtime_context"):
            task.set_runtime_context(
                user_id=user_id,
                conversation_id=conversation_id,
                scope=scope,
                agent_name=agent_name,
            )

        if not getattr(task, "PACKAGE_RUNTIME", None):
            return
        task.config["_user_id"] = user_id
        task.config["_conversation_id"] = conversation_id
        task.config["_scope"] = scope
        task.config["_agent_name"] = agent_name

    def _resolve_service_configs(self, flow: Flow):
        """Resolve expressions in service configs (cascade-safe).

        Services don't receive ParameterContext like tasks do, so we
        resolve expressions in their config dicts using the flow's
        parameter context before they are connected.

        Cascading: if ${x} resolves to "${y}", a second pass resolves
        the inner expression too.
        """
        from core.expression import resolve_expression
        params = self._parameter_context._params if self._parameter_context else {}

        def _resolve_deep(obj):
            """Recursively resolve expressions in nested dicts/lists."""
            if isinstance(obj, str) and '${' in obj:
                resolved = resolve_expression(obj, parameters=params)
                if isinstance(resolved, str) and '${' in resolved:
                    resolved = resolve_expression(resolved, parameters=params)
                return resolved
            elif isinstance(obj, dict):
                return {k: _resolve_deep(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_resolve_deep(v) for v in obj]
            return obj

        for service_id, service in flow.services.items():
            service.config = _resolve_deep(service.config)

    @property
    def task_states(self) -> TaskStateManager:
        return self._task_states

    @property
    def connections(self) -> ConnectionManager:
        return self._connections

    @property
    def flow_version(self) -> int:
        return self._flow_version

    @property
    def flow(self) -> Flow:
        return self._flow

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    # -- Lifecycle --

    def start(self):
        """Start all tasks and the scheduler."""
        _start_t0 = time.monotonic()
        # Try to recover from checkpoint before starting
        if self._checkpoint_mgr:
            _t0 = time.monotonic()
            self._recover_from_checkpoint()
            logger.debug("[startup-timing] executor checkpoint recover: %.1fms",
                        (time.monotonic() - _t0) * 1000)

        # Re-connect services (needed after stop/start cycle, idempotent)
        _t0 = time.monotonic()
        for service_id, service in self._flow.services.items():
            if not (hasattr(service, 'is_connected') and service.is_connected()):
                try:
                    _svc_t0 = time.monotonic()
                    service.connect()
                    logger.info(f"Service '{service_id}' connected")
                    logger.debug("[startup-timing] executor service %s connect: %.1fms",
                                service_id, (time.monotonic() - _svc_t0) * 1000)
                except Exception as e:
                    logger.error(f"Service '{service_id}' failed to connect: {e}")
        logger.debug("[startup-timing] executor service reconnect phase: %.1fms",
                    (time.monotonic() - _t0) * 1000)

        # Re-initialize tasks (e.g. register HTTP routes, idempotent)
        _t0 = time.monotonic()
        for task_id, task in self._tasks.items():
            try:
                if hasattr(task, 'initialize'):
                    _task_t0 = time.monotonic()
                    task.initialize()
                    logger.debug("[startup-timing] executor task %s initialize: %.1fms",
                                task_id, (time.monotonic() - _task_t0) * 1000)
            except Exception as e:
                logger.error(f"Task '{task_id}' initialization failed: {e}")
        logger.debug("[startup-timing] executor task init phase: %.1fms",
                    (time.monotonic() - _t0) * 1000)

        # Transition all tasks to RUNNING
        _t0 = time.monotonic()
        for task_id in self._tasks:
            self._task_states.start(task_id)

        self._stop_event.clear()
        self._schedule_wake.clear()
        self._pool = ThreadPoolExecutor(max_workers=self._max_workers)
        self._interactive_pool = ThreadPoolExecutor(
            max_workers=max(2, min(8, self._max_workers)),
            thread_name_prefix="pawflow-ui",
        )
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="continuous-executor",
            daemon=True,
        )
        self._scheduler_thread.start()
        logger.info("ContinuousFlowExecutor started")
        logger.debug("[startup-timing] executor scheduler/start phase: %.1fms",
                    (time.monotonic() - _t0) * 1000)

        # Safety net: reclaim orphan CLI session dirs accumulated across
        # previous runs. This only checks sessions/<provider>/<user>/<conv>
        # links against conversation dirs; it does not walk live session trees.
        def _cleanup_cli_sessions_async():
            try:
                _t0 = time.monotonic()
                from core.conversation_store import ConversationStore as _CS
                _removed = _CS.instance().cleanup_orphan_cli_sessions()
                if _removed:
                    logger.info("Reclaimed %d orphan CLI session dir(s) on boot",
                                _removed)
                logger.debug("[startup-timing] executor CLI session cleanup async: %.1fms",
                            (time.monotonic() - _t0) * 1000)
            except Exception as _e:
                logger.debug("CLI session cleanup on boot failed: %s", _e)

        threading.Thread(
            target=_cleanup_cli_sessions_async,
            name="cli-session-cleanup",
            daemon=True,
        ).start()
        logger.debug("[startup-timing] executor start total: %.1fms",
                    (time.monotonic() - _start_t0) * 1000)

    def stop(self):
        """Stop the scheduler and all tasks."""
        self._run_shutdown_triggers()

        self._stop_event.set()
        self._schedule_wake.set()

        # Stop scheduler thread first
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=10)
            self._scheduler_thread = None

        # Disconnect services BEFORE pool shutdown — this unblocks pending
        # HTTP requests (503) so worker threads can finish
        for service_id, service in self._flow.services.items():
            try:
                service.disconnect()
                logger.info(f"Service '{service_id}' disconnected")
            except Exception as e:
                logger.warning(f"Service '{service_id}' disconnect error: {e}")

        # Now shut down the thread pool (workers are unblocked)
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
        if self._interactive_pool:
            self._interactive_pool.shutdown(wait=False, cancel_futures=True)
            self._interactive_pool = None

        for task_id in self._tasks:
            self._task_states.stop(task_id)

        # Cleanup tasks (e.g. unregister HTTP routes)
        for task_id, task in self._tasks.items():
            if hasattr(task, 'cleanup'):
                try:
                    task.cleanup()
                except Exception as e:
                    logger.warning(f"Task '{task_id}' cleanup error: {e}")

        # Save final checkpoint on stop
        self._save_checkpoint()

        logger.info("ContinuousFlowExecutor stopped")

    def _run_shutdown_triggers(self):
        """Fire shutdownTrigger roots before services and workers are stopped."""
        if self._stop_event.is_set() or not self._pool:
            return

        triggers = [
            (task_id, task) for task_id, task in self._tasks.items()
            if getattr(task, "TYPE", "") == "shutdownTrigger"
        ]
        if not triggers:
            return

        timeout = 0.0
        trigger_ids = {task_id for task_id, _task in triggers}
        for _task_id, task in triggers:
            try:
                timeout = max(timeout, float((getattr(task, "config", {}) or {}).get("timeout", 10)))
                if hasattr(task, "arm_shutdown"):
                    task.arm_shutdown()
            except Exception as exc:
                logger.warning("shutdownTrigger arm error: %s", exc)

        self._schedule_wake.set()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                any_in_flight = any(v > 0 for v in self._in_flight.values())
            any_pending_trigger = any(
                bool(getattr(task, "has_pending_input", lambda: False)())
                for _task_id, task in triggers
            )
            if (not any_pending_trigger and not any_in_flight and
                    self._downstream_queue_size(trigger_ids) == 0):
                return
            time.sleep(min(self._schedule_interval, 0.1))

        logger.warning("shutdownTrigger cleanup timed out after %.1fs", timeout)

    def _downstream_queue_size(self, source_task_ids):
        """Return queued FlowFiles on connections reachable from source tasks."""
        seen = set(source_task_ids or set())
        frontier = list(seen)
        total = 0
        while frontier:
            source_id = frontier.pop()
            for conn in self._connections.get_outgoing(source_id):
                total += conn.queue_size()
                if conn.target_id not in seen:
                    seen.add(conn.target_id)
                    frontier.append(conn.target_id)
        return total

    def _wake_scheduler(self):
        """Wake the scheduler when new work is available."""
        self._schedule_wake.set()

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    # -- Inject FlowFiles --

    def inject(self, flowfile: FlowFile, entry_task_id: Optional[str] = None):
        """Inject a FlowFile at an entry point.

        If entry_task_id is None, uses the first entry point of the flow.
        The FlowFile is enqueued in the first incoming connection of the
        entry task (or a virtual entry connection is created).
        """
        if entry_task_id is None:
            # Find root tasks (no incoming connections)
            all_targets = set()
            for rel in self._flow.relations:
                all_targets.add(rel.get("to"))
            roots = [tid for tid in self._tasks if tid not in all_targets]
            if not roots:
                raise FlowError("No entry point found")
            entry_task_id = roots[0]

        # Find or create an incoming connection for the entry task
        incoming = self._connections.get_incoming(entry_task_id)
        if incoming:
            conn = incoming[0]
        else:
            # Create a virtual "source" connection for the entry
            conn = Connection(
                source_id="__input__",
                target_id=entry_task_id,
                relationship="input",
            )
            self._connections.add_connection(conn)

        if not conn.enqueue(flowfile):
            logger.warning(
                f"Backpressure at entry point '{entry_task_id}': "
                f"FlowFile queued={conn.queue_size()}, "
                f"cannot inject more data"
            )
            return False

        self._wake_scheduler()
        logger.debug(f"Injected FlowFile into '{entry_task_id}'")
        return True

    # -- Scheduler --

    @staticmethod
    def _is_interactive_http_ff(flowfile: FlowFile) -> bool:
        """True for UI HTTP requests that must not wait behind agent work."""
        if not flowfile or not flowfile.get_attribute("http.request.id"):
            return False
        if flowfile.get_attribute("http.path") == "/api/ui":
            return True
        try:
            return int(flowfile.get_attribute("priority") or "0") >= 10
        except (TypeError, ValueError):
            return False

    def _has_interactive_input(self, task_id: str, incoming: List[Connection]) -> bool:
        task = self._tasks.get(task_id)
        if task and hasattr(task, "has_priority_input"):
            try:
                if task.has_priority_input():
                    return True
            except Exception:
                logger.debug("priority-input check failed for %s", task_id, exc_info=True)
        for conn in incoming or []:
            for ff in conn.peek_all(limit=20):
                if self._is_interactive_http_ff(ff):
                    return True
        return False

