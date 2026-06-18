"""Continuous-executor scheduling + per-task execution engine.

_ContinuousExecRunMixin holds the scheduler loop, task execution, commit and
rollback. Split out of continuous_executor.py for the <=800-line rule; mixed
into ContinuousFlowExecutor (one MRO, shared self state).
"""

import time
import logging
from typing import List, Optional

from core import FlowFile
from core.connection import Connection
from core.bulletin import BulletinBoard
from engine.provenance import ProvenanceEventType

logger = logging.getLogger(__name__)


class _ContinuousExecRunMixin:
    """Scheduler loop + task execution/commit/rollback for ContinuousFlowExecutor."""

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
                    if (task and hasattr(task, 'has_pending_input') and
                            task.has_pending_input() and
                            (self._enabled_one_shot_root_task_ids is None or
                             task_id in self._enabled_one_shot_root_task_ids)):
                        pass  # fall through to schedule
                    else:
                        continue
                elif not has_input:
                    continue

                # Schedule up to (max_inst - current) instances this cycle,
                # capped by the number of queued FlowFiles — otherwise a
                # single FF would cause `max_inst` spurious submits (workers
                # race to dequeue, 1 wins, 999 no-op and return).
                slots = max_inst - current
                if incoming:
                    _pending = sum(c.queue_size() for c in incoming)
                    slots = min(slots, _pending)
                for _ in range(slots):
                    # Re-check input availability for each slot
                    if incoming and not any(not c.is_empty() for c in incoming):
                        break
                    with self._lock:
                        self._in_flight[task_id] = self._in_flight.get(task_id, 0) + 1
                    try:
                        _pool = (self._interactive_pool
                                 if self._has_interactive_input(task_id, incoming)
                                 else self._pool)
                        if _pool is None:
                            raise RuntimeError("executor pool is not running")
                        _pool.submit(self._execute_task, task_id)
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
                self._schedule_wake.wait(timeout=self._schedule_interval)
                self._schedule_wake.clear()

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
                if (hasattr(task, 'has_pending_input') and task.has_pending_input() and
                        (self._enabled_one_shot_root_task_ids is None or
                         task_id in self._enabled_one_shot_root_task_ids)):
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

            # Discard stale HTTP FlowFiles (request already responded or listener stopped)
            if dequeued_ff and dequeued_ff.get_attribute("http.request.id"):
                _req_id = dequeued_ff.get_attribute("http.request.id")
                _svc_id = dequeued_ff.get_attribute("http.listener.service_id") or ""
                _still_valid = False
                try:
                    from services.http_listener_service import _instances
                    for _port, _svc in _instances.items():
                        if _svc._server and _req_id in _svc._server._pending_requests:
                            _still_valid = True
                            break
                except Exception:
                    _still_valid = True  # can't check → assume valid
                if not _still_valid:
                    # Request is no longer pending — silently discard (already dequeued above)
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
                        self._wake_scheduler()
                    else:
                        self._wake_scheduler()
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

