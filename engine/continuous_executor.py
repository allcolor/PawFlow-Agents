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
from dataclasses import dataclass, field
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from core import Flow, FlowFile, TaskFactory, Task, FlowError
from core.task_state import TaskState, TaskStateManager
from core.connection import Connection, ConnectionManager
from core.bulletin import BulletinBoard
from core.parameter_context import ParameterContext
from engine.provenance import ProvenanceEventType, ProvenanceRepository
from engine.checkpoint import CheckpointManager

logger = logging.getLogger(__name__)


@dataclass
class TaskStats:
    """Per-task execution statistics."""
    task_id: str
    task_type: str
    invocations: int = 0
    success_count: int = 0
    error_count: int = 0
    flowfiles_in: int = 0
    flowfiles_out: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    total_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0


@dataclass
class ExecutionResult:
    """Result of a flow execution (batch mode)."""
    flow_id: str
    success: bool
    output_flowfiles: List[FlowFile] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    task_statistics: Dict[str, TaskStats] = field(default_factory=dict)


class ContinuousFlowExecutor:
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
                 parameters: Optional[Dict[str, Any]] = None):
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
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
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

        # Debugger (attached externally via FlowDebugger.attach())
        self._debugger = None

        # Data preview (attached externally via DataPreviewManager.attach())
        self._data_preview = None

        self._has_persistent_sources = False
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
        # Sanity-log the connections involving agent_actions so we can
        # spot stale deployments that forgot to reload the new topology.
        for task_id in ("agent_actions", "agent", "route_after_auth"):
            if task_id in self._tasks:
                _out = self._connections.get_outgoing(task_id)
                _in = self._connections.get_incoming(task_id)
                logger.info(
                    "[executor] task=%s in=%s out=%s",
                    task_id,
                    [(c.source_id, c.relationship) for c in _in],
                    [(c.target_id, c.relationship) for c in _out])

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

    # -- Lifecycle --

    def start(self):
        """Start all tasks and the scheduler."""
        # Try to recover from checkpoint before starting
        if self._checkpoint_mgr:
            self._recover_from_checkpoint()

        # Re-connect services (needed after stop/start cycle, idempotent)
        for service_id, service in self._flow.services.items():
            if not (hasattr(service, 'is_connected') and service.is_connected()):
                try:
                    service.connect()
                    logger.info(f"Service '{service_id}' connected")
                except Exception as e:
                    logger.error(f"Service '{service_id}' failed to connect: {e}")

        # Re-initialize tasks (e.g. register HTTP routes, idempotent)
        for task_id, task in self._tasks.items():
            try:
                if hasattr(task, 'initialize'):
                    task.initialize()
            except Exception as e:
                logger.error(f"Task '{task_id}' initialization failed: {e}")

        # Transition all tasks to RUNNING
        for task_id in self._tasks:
            self._task_states.start(task_id)

        self._stop_event.clear()
        self._pool = ThreadPoolExecutor(max_workers=self._max_workers)
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name="continuous-executor",
            daemon=True,
        )
        self._scheduler_thread.start()
        logger.info("ContinuousFlowExecutor started")

    def stop(self):
        """Stop the scheduler and all tasks."""
        self._stop_event.set()

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

        logger.debug(f"Injected FlowFile into '{entry_task_id}'")
        return True

    # -- Scheduler --

    def _scheduler_loop(self):
        """Main scheduling loop.

        Continuously checks each RUNNING task for available input.
        If input exists and output is not backpressured, schedules execution.
        """
        while not self._stop_event.is_set():
            scheduled_any = False

            for task_id in list(self._tasks.keys()):
                if self._stop_event.is_set():
                    break

                # Skip non-runnable tasks
                if not self._task_states.is_runnable(task_id):
                    continue

                # Skip if at max concurrent instances
                max_inst = self._max_instances.get(task_id, 1)
                with self._lock:
                    current = self._in_flight.get(task_id, 0)
                if current >= max_inst:
                    continue

                # Check output backpressure — don't consume if downstream is full
                if self._connections.any_backpressured(task_id):
                    continue

                # Check if there's input available
                incoming = self._connections.get_incoming(task_id)
                has_input = any(not c.is_empty() for c in incoming)

                # Root tasks (no incoming connections):
                # - Self-triggering tasks (has_pending_input) get scheduled
                # - Others need inject() to provide data
                if not incoming:
                    task = self._tasks.get(task_id)
                    if task and hasattr(task, 'has_pending_input') and task.has_pending_input():
                        pass  # fall through to schedule
                    else:
                        continue
                elif not has_input:
                    continue

                # Schedule up to (max_inst - current) instances this cycle
                slots = max_inst - current
                for _ in range(slots):
                    # Re-check input availability for each slot
                    if incoming and not any(not c.is_empty() for c in incoming):
                        break
                    with self._lock:
                        self._in_flight[task_id] = self._in_flight.get(task_id, 0) + 1
                    try:
                        self._pool.submit(self._execute_task, task_id)
                    except RuntimeError:
                        # Pool was shutdown between stop_event check and submit
                        with self._lock:
                            self._in_flight[task_id] = max(0, self._in_flight.get(task_id, 1) - 1)
                        break
                    scheduled_any = True
                    # Self-triggering tasks: only 1 at a time
                    if not incoming:
                        break

            if not scheduled_any:
                self._stop_event.wait(timeout=self._schedule_interval)

                # Auto-stop for one-shot flows (no persistent sources)
                if not self._has_persistent_sources:
                    with self._lock:
                        any_in_flight = any(v > 0 for v in self._in_flight.values())
                    if not any_in_flight:
                        all_empty = self._connections.all_empty()
                        if all_empty:
                            self._idle_cycles += 1
                            if self._idle_cycles >= self._auto_stop_threshold:
                                logger.info(
                                    f"Auto-stopping flow '{self._flow.id}': "
                                    "no persistent sources and all queues empty"
                                )
                                self._stop_event.set()
                                break
                        else:
                            self._idle_cycles = 0
                    else:
                        self._idle_cycles = 0
                else:
                    self._idle_cycles = 0
            else:
                self._idle_cycles = 0

            # Periodic checkpoint
            if (self._checkpoint_mgr and
                    time.time() - self._last_checkpoint_time > self._checkpoint_interval):
                self._save_checkpoint()

        # After the scheduling loop exits:
        # If we auto-stopped with empty queues, clear stale checkpoints
        # to prevent restoring outdated FlowFiles on next start.
        if self._checkpoint_mgr and not self._has_persistent_sources:
            if self._connections.all_empty():
                self._checkpoint_mgr.clear()

    # -- Task Execution (transactional) --

    def _execute_task(self, task_id: str):
        """Execute a task with transactional semantics.

        1. Atomically dequeue a FlowFile from the input connection
        2. Execute the task
        3. On success: enqueue results to output -> COMMIT
        4. On error after retries: route to failure or discard -> ROLLBACK
        """
        try:
            task = self._tasks.get(task_id)
            if not task:
                return

            # Atomically dequeue input FlowFile
            incoming = self._connections.get_incoming(task_id)
            source_conn = None
            dequeued_ff = None
            is_self_triggering = False
            use_selective_dequeue = False

            # Queue-aware scheduling: if task implements select_processable,
            # let it choose which FlowFile to process (skip saturated services)
            if incoming and hasattr(task, 'select_processable'):
                result = task.select_processable(incoming)
                if result is not None:
                    _sel_ff, source_conn = result
                    # Atomically remove the selected FlowFile (peek_all doesn't dequeue)
                    if source_conn.remove(_sel_ff):
                        dequeued_ff = _sel_ff
                        use_selective_dequeue = True
                    # else: another thread got it first, skip
                # else: nothing processable right now
            elif incoming:
                for conn in incoming:
                    ff = conn.dequeue()  # atomic, thread-safe
                    if ff is not None:
                        source_conn = conn
                        dequeued_ff = ff
                        break

            # Self-triggering tasks (e.g. httpReceiver) don't need incoming
            if dequeued_ff is None and not incoming:
                if hasattr(task, 'has_pending_input') and task.has_pending_input():
                    is_self_triggering = True
                    # Will call execute(None) — task generates its own FlowFile
                else:
                    return

            if dequeued_ff is None and not is_self_triggering:
                return

            # Debugger: check if we should pause before execution
            if self._debugger and self._debugger.should_pause(task_id, dequeued_ff):
                self._debugger.pause_at(task_id, dequeued_ff)

            # Check if debugging was stopped
            if self._debugger:
                from engine.debugger import DebugAction
                if self._debugger._action == DebugAction.STOP:
                    self._debugger = None

            # Discard stale HTTP FlowFiles (request already timed out / responded)
            if dequeued_ff and dequeued_ff.get_attribute("http.request.id"):
                _req_id = dequeued_ff.get_attribute("http.request.id")
                _svc_id = dequeued_ff.get_attribute("http.listener.service_id") or ""
                _still_valid = False
                try:
                    from services.http_listener_service import HTTPListenerService, _instances
                    for _port, _svc in _instances.items():
                        if _svc._server and _req_id in _svc._server._pending_requests:
                            _still_valid = True
                            break
                except Exception:
                    _still_valid = True  # can't check → assume valid
                if not _still_valid:
                    # Request expired — silently discard (already dequeued above)
                    self._task_states.record_run(task_id)
                    return

            # Attempt execution with retries
            task_type = task.TYPE if hasattr(task, 'TYPE') else 'unknown'
            last_error = None
            result = None

            # Expose queue drain callback so long-running tasks (like agentLoop)
            # can pull pending user messages mid-execution
            if hasattr(task, '_drain_pending'):
                _incoming = self._connections.get_incoming(task_id)
                def _drain_fn():
                    """Dequeue all pending FlowFiles from input queue (atomic)."""
                    drained = []
                    for conn in (_incoming or []):
                        while True:
                            ff = conn.dequeue()
                            if ff is None:
                                break
                            drained.append(ff)
                    return drained
                def _requeue_fn(flowfiles):
                    """Re-enqueue FlowFiles back to the input queue."""
                    if _incoming:
                        for ff in flowfiles:
                            _incoming[0].requeue(ff)
                task._drain_pending = _drain_fn
                task._requeue_flowfiles = _requeue_fn

            for attempt in range(1, self._max_retries + 1):
                try:
                    start = time.time()
                    result = task.execute(dequeued_ff)
                    duration_ms = (time.time() - start) * 1000

                    if result is None:
                        result = []

                    # Debugger: capture output snapshots
                    if self._debugger:
                        self._debugger.capture_output(task_id, result)

                    # SUCCESS — commit the transaction
                    self._commit(task_id, task_type, source_conn,
                                 dequeued_ff, result, duration_ms,
                                 selective=use_selective_dequeue)
                    self._task_retry_counts[task_id] = 0

                    # Check for explicit stop request (from stopFlow task)
                    for ff in (result or []):
                        if ff.get_attribute("flow.stop_requested") == "true":
                            reason = ff.get_attribute("flow.stop_reason") or "stop requested"
                            logger.info(f"Flow stop requested by task '{task_id}': {reason}")
                            self._stop_event.set()
                    return

                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"Task '{task_id}' attempt {attempt}/{self._max_retries}: {e}"
                    )
                    if attempt < self._max_retries:
                        time.sleep(min(attempt * 0.3, 3))

            # All retries exhausted — ROLLBACK (FlowFile stays in queue)
            self._rollback(task_id, task_type, dequeued_ff, last_error)

        except Exception as e:
            logger.error(f"Unexpected error in task '{task_id}': {e}")
            self._task_states.set_error(task_id, str(e))

        finally:
            with self._lock:
                self._in_flight[task_id] = max(0, self._in_flight.get(task_id, 1) - 1)

    def _commit(self, task_id: str, task_type: str,
                source_conn: Optional[Connection], input_ff: Optional[FlowFile],
                results: List[FlowFile], duration_ms: float,
                selective: bool = False):
        """Commit a successful task execution.

        - Dequeue the input FlowFile (it's been processed)
        - Enqueue results to output connections based on relationship type
        - Update stats

        Routing rules:
        - FlowFiles with attribute "route.relationship" go to matching connections
        - FlowFiles without that attribute go to "success" connections
        - If no matching connection, fall back to all outgoing connections
        """
        # Input FlowFile was already dequeued atomically in _execute_task.
        # For selective dequeue (select_processable), the task handled removal.
        # Nothing to dequeue here.

        # Enqueue results to output connections with relationship routing
        outgoing = self._connections.get_outgoing(task_id)
        if not outgoing:
            # Exit task — capture results for batch mode
            self._exit_results.extend(results)
        if outgoing:
            for result_ff in results:
                # Determine target relationship
                ff_rel = result_ff.get_attribute("route.relationship") or "success"

                # Find matching connections
                matching = [c for c in outgoing if c.relationship == ff_rel]
                if not matching:
                    # Fallback: send to all outgoing
                    matching = outgoing
                    if task_id == "route_after_auth":
                        logger.warning(
                            "[executor] route_after_auth: no connection "
                            "matches relationship=%r — falling back to ALL "
                            "outgoing %s. Connections declared: %s",
                            ff_rel,
                            [c.target_id for c in outgoing],
                            [(c.target_id, c.relationship) for c in outgoing])

                # Fan-out: tag all copies with the same fragment.identifier
                # so downstream mergeContent can correlate them
                if len(matching) > 1 and not result_ff.get_attribute("fragment.identifier"):
                    from uuid import uuid4
                    result_ff.set_attribute("fragment.identifier", str(uuid4()))

                for i, out_conn in enumerate(matching):
                    if i == 0:
                        ff_to_send = result_ff
                    else:
                        ff_to_send = result_ff.clone()
                    # Let target task set priority (for priority-attribute queues)
                    target_task = self._tasks.get(out_conn.target_id)
                    if target_task and hasattr(target_task, 'prioritize'):
                        prio = target_task.prioritize(ff_to_send)
                        if prio != 0:
                            ff_to_send.set_attribute("priority", str(prio))
                    logger.debug(
                        "_commit: %s → %s [%d/%d], fragment.id=%s, %d bytes",
                        task_id, out_conn.target_id, i + 1, len(matching),
                        ff_to_send.get_attribute("fragment.identifier"),
                        len(ff_to_send.get_content()),
                    )
                    if not out_conn.enqueue(ff_to_send):
                        logger.warning(
                            f"Backpressure on {out_conn}: "
                            f"FlowFile from '{task_id}' could not be enqueued"
                        )
                        # Put back in a "penalty" — re-enqueue to input
                        # This prevents FlowFile loss even under backpressure
                        source_conn.enqueue(ff_to_send)
                    else:
                        # Data preview capture (non-blocking)
                        if self._data_preview and self._data_preview.is_enabled(task_id, out_conn.target_id):
                            self._data_preview.capture(task_id, out_conn.target_id, ff_to_send)

        # Update stats
        self._task_states.record_run(
            task_id,
            ff_in=1 if input_ff else 0,
            ff_out=len(results),
            bytes_in=input_ff.size() if input_ff else 0,
            bytes_out=sum(ff.size() for ff in results),
        )

        # Provenance
        if self._provenance:
            from engine.provenance import ProvenanceEvent
            for out_ff in results:
                self._provenance.record(ProvenanceEvent(
                    event_type=ProvenanceEventType.SEND,
                    flowfile_id=out_ff.process_id,
                    task_id=task_id,
                    task_type=task_type,
                    flow_id=self._flow.id,
                    content_size=out_ff.size(),
                    attributes=out_ff.get_attributes(),
                    duration_ms=duration_ms,
                ))

        logger.debug(
            f"Task '{task_id}' committed: 1 in -> {len(results)} out "
            f"({duration_ms:.1f}ms)"
        )

    def _rollback(self, task_id: str, task_type: str,
                  input_ff: FlowFile, error: Exception):
        """Rollback a failed task execution.

        If a "failure" connection exists from this task, the FlowFile is
        dequeued and routed there (penalty box pattern).
        Otherwise, the FlowFile stays in the input queue and the task
        goes to ERROR state, causing backpressure cascade.
        """
        error_msg = str(error)

        # Check for failure connections
        outgoing = self._connections.get_outgoing(task_id)
        failure_conns = [c for c in outgoing if c.relationship == "failure"]

        # FlowFile was already dequeued atomically in _execute_task.
        if failure_conns:
            # Route to failure connection
            input_ff.set_attribute("error.message", error_msg)
            input_ff.set_attribute("error.task", task_id)
            for i, fc in enumerate(failure_conns):
                ff_to_send = input_ff if i == 0 else input_ff.clone()
                fc.enqueue(ff_to_send)

            logger.info(
                f"Task '{task_id}' routed failed FlowFile to failure connection"
            )
            # Task stays RUNNING (failure was handled)
            return

        # If this is an HTTP request, send error response so client doesn't hang
        req_id = input_ff.get_attribute("http.request.id") if input_ff else None
        if req_id:
            input_ff.set_attribute("http.response.status", "500")
            input_ff.set_attribute("http.response.header.Content-Type", "application/json")
            import json as _json
            input_ff.set_content(_json.dumps({"error": error_msg}).encode("utf-8"))
            # Route to send_response if it exists
            outgoing = self._connections.get_outgoing(task_id)
            response_conns = [c for c in outgoing if c.relationship == "success"]
            for rc in response_conns:
                rc.enqueue(input_ff)
                break

        self._task_retry_counts.setdefault(task_id, 0)
        self._task_retry_counts[task_id] += 1
        consecutive = self._task_retry_counts[task_id]

        BulletinBoard.get_instance().post(
            "ERROR", task_id,
            f"FlowFile discarded after {self._max_retries} retries: {error_msg}"
        )

        if consecutive >= 5:
            # 5 consecutive failures → systemic problem, stop the task
            self._task_states.set_error(task_id, error_msg)
            logger.error(
                f"Task '{task_id}' ERROR: {consecutive} consecutive failures. "
                f"Last error: {error_msg}. "
                f"Fix the problem then call restart_task('{task_id}')"
            )
        else:
            logger.warning(
                f"Task '{task_id}' discarded failed FlowFile ({consecutive}/5 consecutive). "
                f"Error: {error_msg}. Task continues processing."
            )

    # -- Task management --

    def restart_task(self, task_id: str) -> bool:
        """Restart a task that's in ERROR state.

        The task transitions back to RUNNING and resumes
        consuming from its input queue.
        """
        return self._task_states.start(task_id)

    def stop_task(self, task_id: str) -> bool:
        """Stop a specific task."""
        return self._task_states.stop(task_id)

    def start_task(self, task_id: str) -> bool:
        """Start a specific task."""
        return self._task_states.start(task_id)

    def disable_task(self, task_id: str) -> bool:
        """Disable a task."""
        return self._task_states.disable(task_id)

    def get_task_state(self, task_id: str) -> Optional[TaskState]:
        return self._task_states.get_state(task_id)

    # -- Queue Management --

    def clear_task_queue(self, source_id: str, target_id: str):
        """Clear a specific connection queue and reset the target task's state."""
        conn = self._connections.get_connection(source_id, target_id)
        if conn:
            conn.clear()
        task = self._tasks.get(target_id)
        if task and hasattr(task, 'reset'):
            task.reset()

    def clear_all_queues(self):
        """Clear all queues, reset task buffers and counters."""
        self._connections.clear_all()
        for task in self._tasks.values():
            if hasattr(task, 'reset'):
                task.reset()
        self._task_states.reset_all_counters()

    # -- Flow Version Updates --

    def update_task(self, task_id: str, new_config: Dict[str, Any],
                    new_type: Optional[str] = None) -> bool:
        """Hot-swap a task's configuration without losing queued FlowFiles.

        1. Stop the task (wait for in-flight to finish)
        2. Replace the task instance with new config (and optionally new type)
        3. Restart the task
        4. Queues are untouched — FlowFiles are preserved
        """
        if task_id not in self._tasks:
            logger.error(f"Task '{task_id}' not found")
            return False

        old_task = self._tasks[task_id]
        task_type = new_type or (old_task.TYPE if hasattr(old_task, 'TYPE') else '')

        # Stop the task
        self._task_states.stop(task_id)

        # Wait for in-flight execution to finish
        for _ in range(100):  # max 10s
            with self._lock:
                if not self._in_flight.get(task_id, False):
                    break
            time.sleep(0.1)

        try:
            # Create new task instance with updated config
            task_class = TaskFactory.get(task_type)
            new_task = task_class(new_config)
            self._tasks[task_id] = new_task

            # Record version change
            self._flow_version += 1
            self._version_history.append({
                "version": self._flow_version,
                "timestamp": datetime.now().isoformat(),
                "action": "update_task",
                "task_id": task_id,
                "old_config": old_task.config,
                "new_config": new_config,
            })

            # Restart the task
            self._task_states.start(task_id)

            logger.info(
                f"Task '{task_id}' updated to v{self._flow_version}. "
                f"Queued FlowFiles preserved."
            )
            return True

        except Exception as e:
            logger.error(f"Failed to update task '{task_id}': {e}")
            # Restore old task
            self._tasks[task_id] = old_task
            self._task_states.set_error(task_id, f"Update failed: {e}")
            return False

    def _flow_fingerprint(self, flow: Flow) -> str:
        """Generate a fingerprint of a flow's structure for change detection."""
        tasks_sig = sorted(
            (tid, t.TYPE if hasattr(t, 'TYPE') else type(t).__name__,
             sorted(t.config.items()) if hasattr(t, 'config') else [])
            for tid, t in flow.tasks.items()
        )
        rels_sig = sorted(
            (r.source_id, r.target_id, r.relation_type)
            for r in flow.relations
        )
        svcs_sig = sorted(
            (sid, s.TYPE if hasattr(s, 'TYPE') else type(s).__name__,
             sorted(s.config.items()) if hasattr(s, 'config') else [])
            for sid, s in flow.services.items()
        )
        return f"{tasks_sig}|{rels_sig}|{svcs_sig}"

    def update_flow(self, new_flow: Flow) -> bool:
        """Update the entire flow structure while preserving queued FlowFiles.

        Returns False without changes if the new flow is identical to current.

        Strategy:
        1. Stop all tasks
        2. For connections that exist in both old and new flow: keep queues
        3. For new connections: create empty
        4. For removed connections: drain FlowFiles to a "lost+found" list
        5. Update/add/remove tasks
        6. Restart
        """
        # Skip update if flow structure hasn't changed
        old_fp = self._flow_fingerprint(self._flow)
        new_fp = self._flow_fingerprint(new_flow)
        if old_fp == new_fp:
            logger.info("Flow unchanged, skipping update.")
            return None  # None = no change (distinct from False = error)

        logger.info(f"Updating flow from v{self._flow_version}...")

        # Preserve existing service instances — they may hold live connections
        # (e.g. HTTP server on a port). Only replace if service type changed.
        old_services = dict(self._flow.services) if self._flow else {}
        for svc_id, new_svc in new_flow.services.items():
            old_svc = old_services.get(svc_id)
            if old_svc is not None:
                old_type = old_svc.TYPE if hasattr(old_svc, 'TYPE') else type(old_svc).__name__
                new_type = new_svc.TYPE if hasattr(new_svc, 'TYPE') else type(new_svc).__name__
                if old_type == new_type:
                    # Reuse the live service instance, update its config
                    for k, v in new_svc.config.items():
                        old_svc.config[k] = v
                    new_flow.services[svc_id] = old_svc
                    logger.info(f"Reusing live service '{svc_id}' ({old_type})")

        # Stop scheduler and pool, but DON'T disconnect reused services
        was_running = self.is_running
        if was_running:
            self._stop_event.set()
            if self._scheduler_thread:
                self._scheduler_thread.join(timeout=10)
                self._scheduler_thread = None
            # Disconnect only services that are NOT reused
            for svc_id, svc in old_services.items():
                if svc is not new_flow.services.get(svc_id):
                    try:
                        svc.disconnect()
                        logger.info(f"Service '{svc_id}' disconnected (replaced)")
                    except Exception as e:
                        logger.warning(f"Service '{svc_id}' disconnect error: {e}")
            if self._pool:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None
            for task_id in self._tasks:
                self._task_states.stop(task_id)
            # Cleanup tasks (unregister routes etc.)
            for task_id, task in self._tasks.items():
                if hasattr(task, 'cleanup'):
                    try:
                        task.cleanup()
                    except Exception as e:
                        logger.warning(f"Task '{task_id}' cleanup error: {e}")

        try:
            # Save current queue contents indexed by (source, target)
            saved_queues: Dict[tuple, List[FlowFile]] = {}
            for conn_stats in self._connections.get_all_stats():
                key = (conn_stats["source"], conn_stats["target"])
                saved_queues[key] = []

            # Drain all connections
            for conn in self._connections._connections:
                key = (conn.source_id, conn.target_id)
                while not conn.is_empty():
                    ff = conn.dequeue()
                    if ff:
                        saved_queues.setdefault(key, []).append(ff)

            # Rebuild with new flow (reused services are already connected)
            old_tasks = dict(self._tasks)
            self._tasks.clear()
            self._task_states.clear()
            self._task_retry_counts.clear()
            self._in_flight.clear()
            self._flow = new_flow
            self._build(new_flow)

            # Restore queues for connections that still exist
            restored = 0
            orphaned = 0
            for (src, tgt), flowfiles in saved_queues.items():
                outgoing = self._connections.get_outgoing(src)
                target_conn = None
                for conn in outgoing:
                    if conn.target_id == tgt:
                        target_conn = conn
                        break

                if target_conn:
                    for ff in flowfiles:
                        target_conn.enqueue(ff)
                        restored += 1
                else:
                    # Connection no longer exists — try to re-inject at target
                    incoming = self._connections.get_incoming(tgt)
                    if incoming:
                        for ff in flowfiles:
                            incoming[0].enqueue(ff)
                            restored += 1
                    else:
                        orphaned += len(flowfiles)
                        logger.warning(
                            f"Orphaned {len(flowfiles)} FlowFiles from "
                            f"removed connection {src} -> {tgt}"
                        )

            self._flow_version += 1
            self._version_history.append({
                "version": self._flow_version,
                "timestamp": datetime.now().isoformat(),
                "action": "update_flow",
                "restored_flowfiles": restored,
                "orphaned_flowfiles": orphaned,
            })

            logger.info(
                f"Flow updated to v{self._flow_version}. "
                f"Restored {restored} FlowFiles, {orphaned} orphaned."
            )

            if was_running:
                self.start()

            # Log registered routes for debugging
            for svc_id, svc in self._flow.services.items():
                if hasattr(svc, 'get_routes'):
                    routes = svc.get_routes()
                    logger.info(f"Service '{svc_id}' routes after update: {routes}")

            return True

        except Exception as e:
            logger.error(f"Flow update failed: {e}")
            return False

    def get_version_history(self) -> List[Dict[str, Any]]:
        """Get the flow version change history."""
        return list(self._version_history)

    # -- Monitoring --

    def get_queue_stats(self) -> List[Dict[str, Any]]:
        """Get stats for all connection queues."""
        return self._connections.get_all_stats()

    def get_all_task_states(self) -> Dict[str, dict]:
        """Get all task states, enriched with in_flight count."""
        states = self._task_states.get_all_states()
        for tid, s in states.items():
            s["in_flight"] = self._in_flight.get(tid, 0)
        return states

    def get_status(self) -> Dict[str, Any]:
        """Get overall executor status."""
        states = self._task_states.get_all_states()
        running = sum(1 for s in states.values() if s["state"] == "running")
        errored = sum(1 for s in states.values() if s["state"] == "error")
        total_queued = sum(
            s["queue_size"] for s in self._connections.get_all_stats()
        )

        return {
            "flow_version": self._flow_version,
            "is_running": self.is_running,
            "tasks_total": len(self._tasks),
            "tasks_running": running,
            "tasks_errored": errored,
            "total_queued_flowfiles": total_queued,
            "queue_stats": self.get_queue_stats(),
        }

    # -- Checkpoint / Recovery --

    def _save_checkpoint(self):
        """Save a checkpoint of current queue state."""
        if not self._checkpoint_mgr:
            return
        try:
            self._checkpoint_mgr.save_checkpoint(
                self._connections,
                self._task_states.get_all_states(),
                self._flow_version,
            )
            self._last_checkpoint_time = time.time()
        except Exception as e:
            logger.error(f"Checkpoint failed: {e}")

    def _recover_from_checkpoint(self):
        """Recover queued FlowFiles from the latest checkpoint.

        Never blocks startup — corrupted checkpoints are skipped and deleted.
        HTTP-originated FlowFiles are discarded (requests already timed out).
        """
        if not self._checkpoint_mgr:
            return
        try:
            data = self._checkpoint_mgr.load_latest_checkpoint()
        except Exception as e:
            logger.error(f"Checkpoint load failed, skipping recovery: {e}")
            return
        if not data:
            return

        try:
            restored_queues = self._checkpoint_mgr.restore_flowfiles(data)
        except Exception as e:
            logger.error(f"Checkpoint restore failed, skipping: {e}")
            return

        total_restored = 0
        total_skipped = 0

        for (src, tgt), flowfiles in restored_queues.items():
            # Skip HTTP-originated FlowFiles — the requests are long gone
            safe_flowfiles = []
            for ff in flowfiles:
                req_id = ff.get_attribute("http.request.id") if hasattr(ff, 'get_attribute') else None
                if req_id:
                    total_skipped += 1
                    continue
                safe_flowfiles.append(ff)

            if not safe_flowfiles:
                continue

            # Find matching connection
            outgoing = self._connections.get_outgoing(src)
            target_conn = None
            for conn in outgoing:
                if conn.target_id == tgt:
                    target_conn = conn
                    break

            if target_conn and target_conn.is_empty():
                for ff in safe_flowfiles:
                    target_conn.enqueue(ff)
                    total_restored += 1
            elif safe_flowfiles:
                total_skipped += len(safe_flowfiles)
                logger.warning(
                    f"Cannot restore {len(safe_flowfiles)} FlowFiles "
                    f"for {src}->{tgt}: connection not found or not empty"
                )

        if total_restored:
            logger.info(f"Recovered {total_restored} FlowFiles from checkpoint")
        if total_skipped:
            logger.info(f"Skipped {total_skipped} stale FlowFiles from checkpoint")

        # Clear checkpoint after recovery to prevent re-restoring stale FlowFiles
        # on next startup if the server crashes during processing
        try:
            self._checkpoint_mgr.clear()
        except Exception:
            pass

    def save_checkpoint_now(self) -> Optional[str]:
        """Manually trigger a checkpoint. Returns checkpoint path."""
        if not self._checkpoint_mgr:
            return None
        return self._checkpoint_mgr.save_checkpoint(
            self._connections,
            self._task_states.get_all_states(),
            self._flow_version,
        )

    # -- Run Once (debug) --

    def run_once(self, task_id: str) -> List[FlowFile]:
        """Execute a single task once, regardless of its state.

        Useful for debugging: forces one execution cycle of the task.
        Takes the first available FlowFile from input, or None for sources.
        Returns the output FlowFiles.
        """
        task = self._tasks.get(task_id)
        if not task:
            raise FlowError(f"Task '{task_id}' not found")

        # Find input
        incoming = self._connections.get_incoming(task_id)
        input_ff = None
        source_conn = None
        for conn in incoming:
            ff = conn.peek()
            if ff is not None:
                input_ff = ff
                source_conn = conn
                break

        # Execute
        start = time.time()
        results = task.execute(input_ff) or []
        duration_ms = (time.time() - start) * 1000

        # Commit (dequeue input, route output)
        task_type = task.TYPE if hasattr(task, 'TYPE') else 'unknown'
        self._commit(task_id, task_type, source_conn, input_ff,
                     results, duration_ms)

        logger.info(
            f"run_once '{task_id}': {len(results)} output(s) in {duration_ms:.1f}ms"
        )
        return results

    # -- Batch Mode --

    @classmethod
    def run_batch(
        cls,
        flow: Flow,
        input_flowfiles: Optional[List[FlowFile]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        max_workers: int = 4,
        max_retries: int = 3,
        timeout: float = 300.0,
        provenance: Optional[ProvenanceRepository] = None,
    ) -> ExecutionResult:
        """Run a flow synchronously (batch mode).

        Creates a ContinuousFlowExecutor, injects input FlowFiles,
        waits for auto-stop (no persistent sources needed), and
        returns the collected exit task outputs.

        Args:
            flow: The Flow to execute
            input_flowfiles: Optional input data
            parameters: Optional parameter overrides
            max_workers: Thread pool size
            max_retries: Retries per task
            timeout: Max seconds to wait for completion
            provenance: Optional provenance repository

        Returns:
            ExecutionResult with outputs, stats, and errors.
        """
        start_time = time.time()
        errors: List[Dict[str, Any]] = []

        executor = cls(
            flow,
            max_workers=max_workers,
            max_retries=max_retries,
            provenance=provenance,
            enable_checkpoints=False,
            parameters=parameters,
        )

        try:
            executor.start()

            # Inject input FlowFiles at entry points
            if input_flowfiles:
                for ff in input_flowfiles:
                    executor.inject(ff)
            elif not executor._has_persistent_sources:
                # No input and no sources — inject an empty FlowFile to kick off
                if flow.entries:
                    executor.inject(FlowFile())

            # Wait for completion (auto-stop or timeout)
            deadline = time.time() + timeout
            while executor.is_running and time.time() < deadline:
                time.sleep(0.05)

            if executor.is_running:
                logger.warning(f"Batch execution timed out after {timeout}s")
                errors.append({"error": f"Timeout after {timeout}s"})

        except Exception as e:
            errors.append({"error": str(e)})
            logger.error(f"Batch execution error: {e}")
        finally:
            if executor.is_running:
                executor.stop()

        duration_ms = (time.time() - start_time) * 1000

        # Collect task errors from state
        for task_id, state in executor.get_all_task_states().items():
            if state.get("state") == "error":
                errors.append({
                    "task_id": task_id,
                    "error": state.get("error", "unknown"),
                })

        stats = {
            "tasks": len(executor._tasks),
            "queue_stats": executor.get_queue_stats(),
            "input_flowfiles": len(input_flowfiles) if input_flowfiles else 0,
            "output_flowfiles": len(executor._exit_results),
            "bytes_processed": sum(ff.size() for ff in executor._exit_results),
        }
        if provenance:
            stats["provenance"] = provenance.to_dict()

        return ExecutionResult(
            flow_id=flow.id,
            success=len(errors) == 0,
            output_flowfiles=list(executor._exit_results),
            statistics=stats,
            errors=errors,
            duration_ms=duration_ms,
        )
