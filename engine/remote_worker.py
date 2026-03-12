"""Remote Worker - distributed task execution.

Supports executing tasks on remote workers via HTTP or in-process.
Workers register with the coordinator, receive tasks, execute them,
and return results.
"""

import json
import threading
import uuid
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class WorkerStatus(Enum):
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass
class WorkerInfo:
    """Information about a registered worker."""
    worker_id: str
    name: str
    host: str = "localhost"
    port: int = 0
    status: WorkerStatus = WorkerStatus.IDLE
    capabilities: List[str] = field(default_factory=list)
    labels: Dict[str, str] = field(default_factory=dict)
    max_concurrent: int = 4
    current_tasks: int = 0
    registered_at: datetime = field(default_factory=datetime.now)
    last_heartbeat: datetime = field(default_factory=datetime.now)
    total_executed: int = 0
    total_errors: int = 0

    def is_available(self) -> bool:
        return (self.status in (WorkerStatus.IDLE, WorkerStatus.BUSY) and
                self.current_tasks < self.max_concurrent)

    def matches_affinity(self, affinity: Optional[Dict[str, str]] = None) -> bool:
        """Check if worker matches affinity requirements.
        Affinity is a dict of label key-value pairs that must all match."""
        if not affinity:
            return True
        return all(self.labels.get(k) == v for k, v in affinity.items())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "status": self.status.value,
            "capabilities": self.capabilities,
            "labels": self.labels,
            "max_concurrent": self.max_concurrent,
            "current_tasks": self.current_tasks,
            "registered_at": self.registered_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "total_executed": self.total_executed,
            "total_errors": self.total_errors,
        }


@dataclass
class TaskAssignment:
    """A task assigned to a worker."""
    assignment_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    worker_id: str = ""
    task_id: str = ""
    task_type: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    flowfile_content: bytes = b""
    flowfile_attributes: Dict[str, str] = field(default_factory=dict)
    status: str = "pending"  # pending, running, completed, failed
    result_content: Optional[bytes] = None
    result_attributes: Optional[Dict[str, str]] = None
    error: Optional[str] = None
    submitted_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


class WorkerCoordinator:
    """Coordinates task distribution across workers.

    In local mode, executes tasks in thread pool.
    Can be extended for HTTP-based remote execution.

    Health checks:
    - Workers must send heartbeats within heartbeat_timeout_seconds
    - Workers exceeding the timeout are marked OFFLINE
    - Circuit breaker: after max_consecutive_failures, worker is marked OFFLINE
    - OFFLINE workers are skipped during selection
    """

    def __init__(self, heartbeat_timeout_seconds: float = 60.0,
                 max_consecutive_failures: int = 5,
                 health_check_interval: float = 15.0):
        self._workers: Dict[str, WorkerInfo] = {}
        self._assignments: Dict[str, TaskAssignment] = {}
        self._lock = threading.Lock()
        self._local_worker = self._create_local_worker()

        # Health check config
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._max_consecutive_failures = max_consecutive_failures
        self._health_check_interval = health_check_interval
        # Track consecutive failures per worker
        self._consecutive_failures: Dict[str, int] = {}

        # Health check thread
        self._stop_event = threading.Event()
        self._health_thread: Optional[threading.Thread] = None

    def _create_local_worker(self) -> WorkerInfo:
        """Create and register the local worker."""
        worker = WorkerInfo(
            worker_id="local",
            name="Local Worker",
            host="localhost",
            max_concurrent=8,
        )
        self._workers["local"] = worker
        return worker

    def register_worker(self, name: str, host: str = "localhost",
                        port: int = 0, capabilities: Optional[List[str]] = None,
                        labels: Optional[Dict[str, str]] = None,
                        max_concurrent: int = 4) -> WorkerInfo:
        """Register a new remote worker."""
        worker_id = str(uuid.uuid4())[:8]
        worker = WorkerInfo(
            worker_id=worker_id,
            name=name,
            host=host,
            port=port,
            capabilities=capabilities or [],
            labels=labels or {},
            max_concurrent=max_concurrent,
        )
        with self._lock:
            self._workers[worker_id] = worker
        logger.info(f"Worker registered: {name} ({worker_id}) at {host}:{port}")
        return worker

    def unregister_worker(self, worker_id: str):
        """Remove a worker."""
        with self._lock:
            self._workers.pop(worker_id, None)

    def heartbeat(self, worker_id: str):
        """Update worker heartbeat."""
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker:
                worker.last_heartbeat = datetime.now()
                if worker.status == WorkerStatus.OFFLINE:
                    worker.status = WorkerStatus.IDLE

    def select_worker(self, task_type: str = "",
                      affinity: Optional[Dict[str, str]] = None) -> Optional[WorkerInfo]:
        """Select the best available worker for a task.

        Strategy:
        1. Filter by availability
        2. Filter by capabilities (task type support)
        3. Filter by affinity labels (all must match)
        4. Pick least-loaded among candidates
        Falls back to local worker if no affinity specified.
        """
        with self._lock:
            candidates = []
            for worker in self._workers.values():
                if not worker.is_available():
                    continue
                if task_type and worker.capabilities and task_type not in worker.capabilities:
                    continue
                if not worker.matches_affinity(affinity):
                    continue
                candidates.append(worker)

            if not candidates:
                # Fallback to local if no affinity constraint
                if not affinity and self._local_worker.is_available():
                    return self._local_worker
                return None

            # Least loaded
            return min(candidates, key=lambda w: w.current_tasks)

    def submit_task(self, task_id: str, task_type: str, config: Dict[str, Any],
                    flowfile_content: bytes, flowfile_attributes: Dict[str, str],
                    worker_id: Optional[str] = None,
                    affinity: Optional[Dict[str, str]] = None) -> TaskAssignment:
        """Submit a task for execution on a worker.

        Args:
            affinity: Dict of label key-value pairs for worker selection.
                      E.g. {"zone": "eu", "gpu": "true"} will only dispatch
                      to workers whose labels match all pairs.
        """
        if worker_id is None:
            worker = self.select_worker(task_type, affinity=affinity)
            if not worker:
                raise RuntimeError(
                    f"No available worker matching affinity={affinity}, "
                    f"task_type={task_type}"
                )
            worker_id = worker.worker_id

        assignment = TaskAssignment(
            worker_id=worker_id,
            task_id=task_id,
            task_type=task_type,
            config=config,
            flowfile_content=flowfile_content,
            flowfile_attributes=flowfile_attributes,
            status="pending",
        )

        with self._lock:
            self._assignments[assignment.assignment_id] = assignment
            worker = self._workers.get(worker_id)
            if worker:
                worker.current_tasks += 1

        return assignment

    def complete_task(self, assignment_id: str, result_content: bytes,
                      result_attributes: Dict[str, str]):
        """Mark a task as completed."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if not assignment:
                return
            assignment.status = "completed"
            assignment.result_content = result_content
            assignment.result_attributes = result_attributes
            assignment.completed_at = datetime.now()

            worker = self._workers.get(assignment.worker_id)
            if worker:
                worker.current_tasks = max(0, worker.current_tasks - 1)
                worker.total_executed += 1

    def fail_task(self, assignment_id: str, error: str):
        """Mark a task as failed."""
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if not assignment:
                return
            assignment.status = "failed"
            assignment.error = error
            assignment.completed_at = datetime.now()

            worker = self._workers.get(assignment.worker_id)
            if worker:
                worker.current_tasks = max(0, worker.current_tasks - 1)
                worker.total_errors += 1

    def get_assignment(self, assignment_id: str) -> Optional[TaskAssignment]:
        """Get an assignment by ID."""
        with self._lock:
            return self._assignments.get(assignment_id)

    def get_workers(self) -> List[Dict[str, Any]]:
        """Get all registered workers."""
        with self._lock:
            return [w.to_dict() for w in self._workers.values()]

    def get_pending_assignments(self, worker_id: str) -> List[TaskAssignment]:
        """Get pending assignments for a worker."""
        with self._lock:
            return [a for a in self._assignments.values()
                    if a.worker_id == worker_id and a.status == "pending"]

    def execute_local(self, assignment: TaskAssignment) -> TaskAssignment:
        """Execute a task locally (in-process)."""
        from core import TaskFactory, FlowFile

        try:
            assignment.status = "running"
            task_class = TaskFactory.get(assignment.task_type)
            task = task_class(assignment.config)

            ff = FlowFile(
                content=assignment.flowfile_content,
                attributes=assignment.flowfile_attributes,
            )

            results = task.execute(ff)

            if results:
                result_ff = results[0]
                self.complete_task(
                    assignment.assignment_id,
                    result_content=result_ff.get_content(),
                    result_attributes=result_ff.get_attributes(),
                )
            else:
                self.complete_task(assignment.assignment_id, b"", {})

        except Exception as e:
            self.fail_task(assignment.assignment_id, str(e))

        return assignment

    def execute_remote(self, assignment: TaskAssignment) -> TaskAssignment:
        """Execute a task on a remote worker via HTTP streaming.

        Uses WorkerClient to send the FlowFile to the assigned worker
        and receive the results back, all via streaming protocol.
        """
        from core import FlowFile
        from engine.worker_client import WorkerClient

        worker = self._workers.get(assignment.worker_id)
        if not worker or worker.worker_id == "local":
            return self.execute_local(assignment)

        try:
            assignment.status = "running"

            ff = FlowFile(
                content=assignment.flowfile_content,
                attributes=assignment.flowfile_attributes,
            )

            client = WorkerClient(worker.host, worker.port)
            results, metadata = client.execute_task(
                ff,
                task_id=assignment.task_id,
                task_type=assignment.task_type,
                config=assignment.config,
            )

            if metadata.get("status") == "failed":
                self.fail_task(
                    assignment.assignment_id,
                    metadata.get("error", "Remote execution failed"),
                )
            elif results:
                result_ff = results[0]
                self.complete_task(
                    assignment.assignment_id,
                    result_content=result_ff.get_content(),
                    result_attributes=result_ff.get_attributes(),
                )
            else:
                self.complete_task(assignment.assignment_id, b"", {})

        except Exception as e:
            self.fail_task(assignment.assignment_id, str(e))

        return assignment

    def execute(self, assignment: TaskAssignment) -> TaskAssignment:
        """Execute a task on the appropriate worker (local or remote)."""
        worker = self._workers.get(assignment.worker_id)
        if worker and worker.worker_id != "local" and worker.port > 0:
            result = self.execute_remote(assignment)
        else:
            result = self.execute_local(assignment)

        # Update circuit breaker
        if result.status == "failed":
            self._record_failure(result.worker_id)
        elif result.status == "completed":
            self._record_success(result.worker_id)

        return result

    # -- Health checks & circuit breaker --

    def start_health_checks(self):
        """Start the background health check thread."""
        if self._health_thread and self._health_thread.is_alive():
            return
        self._stop_event.clear()
        self._health_thread = threading.Thread(
            target=self._health_check_loop,
            name="worker-health-check",
            daemon=True,
        )
        self._health_thread.start()

    def stop_health_checks(self):
        """Stop the background health check thread."""
        self._stop_event.set()
        if self._health_thread:
            self._health_thread.join(timeout=5)
            self._health_thread = None

    def _health_check_loop(self):
        """Periodically check worker health."""
        while not self._stop_event.is_set():
            self._check_worker_health()
            self._stop_event.wait(timeout=self._health_check_interval)

    def _check_worker_health(self):
        """Mark workers as OFFLINE if heartbeat expired."""
        now = datetime.now()
        with self._lock:
            for worker in self._workers.values():
                if worker.worker_id == "local":
                    continue
                if worker.status == WorkerStatus.OFFLINE:
                    continue
                elapsed = (now - worker.last_heartbeat).total_seconds()
                if elapsed > self._heartbeat_timeout:
                    worker.status = WorkerStatus.OFFLINE
                    logger.warning(
                        f"Worker '{worker.name}' ({worker.worker_id}) "
                        f"marked OFFLINE: no heartbeat for {elapsed:.0f}s"
                    )

    def _record_failure(self, worker_id: str):
        """Record a failure for circuit breaker."""
        with self._lock:
            count = self._consecutive_failures.get(worker_id, 0) + 1
            self._consecutive_failures[worker_id] = count
            if count >= self._max_consecutive_failures:
                worker = self._workers.get(worker_id)
                if worker and worker.worker_id != "local":
                    worker.status = WorkerStatus.OFFLINE
                    logger.warning(
                        f"Circuit breaker tripped for worker '{worker.name}' "
                        f"({worker_id}): {count} consecutive failures"
                    )

    def _record_success(self, worker_id: str):
        """Reset failure counter on success."""
        with self._lock:
            self._consecutive_failures.pop(worker_id, None)

    def reset_worker(self, worker_id: str) -> bool:
        """Manually reset an OFFLINE worker back to IDLE."""
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker:
                return False
            worker.status = WorkerStatus.IDLE
            worker.last_heartbeat = datetime.now()
            self._consecutive_failures.pop(worker_id, None)
            logger.info(f"Worker '{worker.name}' ({worker_id}) manually reset to IDLE")
            return True

    def get_health_summary(self) -> Dict[str, Any]:
        """Get a summary of worker health."""
        with self._lock:
            total = len(self._workers)
            online = sum(1 for w in self._workers.values()
                         if w.status != WorkerStatus.OFFLINE)
            offline = total - online
            return {
                "total_workers": total,
                "online": online,
                "offline": offline,
                "workers": [
                    {
                        **w.to_dict(),
                        "consecutive_failures": self._consecutive_failures.get(
                            w.worker_id, 0
                        ),
                    }
                    for w in self._workers.values()
                ],
            }
