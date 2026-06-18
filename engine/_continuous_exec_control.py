"""Continuous-executor task control, flow updates, checkpointing and batch run.

_ContinuousExecControlMixin holds task lifecycle controls, flow hot-swap,
checkpoint save/restore and the batch-mode entry points. Split out of
continuous_executor.py for the <=800-line rule; mixed into
ContinuousFlowExecutor (one MRO, shared self state).
"""

import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from core import Flow, FlowFile, TaskFactory, FlowError
from core.task_state import TaskState
from engine.provenance import ProvenanceRepository

from engine._exec_types import ExecutionResult

logger = logging.getLogger(__name__)


class _ContinuousExecControlMixin:
    """Task control, flow updates, checkpointing and batch run for ContinuousFlowExecutor."""

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
        HTTP-originated FlowFiles are discarded when their listener request is
        no longer pending.
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
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
        timeout: Optional[float] = None,
        provenance: Optional[ProvenanceRepository] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
        entry_task_id: Optional[str] = None,
        suppress_one_shot_roots: bool = False,
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
            timeout: Optional max seconds to wait for completion. If omitted,
                wait until the flow completes.
            provenance: Optional provenance repository
            entry_task_id: Optional task id where input FlowFiles are injected
            suppress_one_shot_roots: If true, root tasks that self-trigger via
                has_pending_input() are not scheduled implicitly.

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
            runtime_context=runtime_context,
            enabled_one_shot_root_task_ids=[] if suppress_one_shot_roots else None,
        )

        try:
            executor.start()

            # Inject input FlowFiles at entry points
            if input_flowfiles:
                for ff in input_flowfiles:
                    executor.inject(ff, entry_task_id=entry_task_id)
            elif not executor._has_persistent_sources and not suppress_one_shot_roots:
                # No input and no sources — inject an empty FlowFile to kick off
                if flow.entries:
                    executor.inject(FlowFile(), entry_task_id=entry_task_id)

            # Wait for completion. No implicit deadline: callers that need a
            # bounded batch run must pass timeout explicitly.
            deadline = time.time() + timeout if timeout is not None else None
            while executor.is_running:
                if deadline is not None and time.time() >= deadline:
                    logger.warning(f"Batch execution timed out after {timeout}s")
                    errors.append({"error": f"Timeout after {timeout}s"})
                    break
                time.sleep(0.05)

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
