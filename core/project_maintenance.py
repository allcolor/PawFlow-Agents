"""Coalesced background maintenance for relay-scoped project knowledge.

Each active relay is one project. Context preparation schedules a cheap lazy
refresh; filesystem writes reschedule it with a short debounce. The worker
refreshes the AST graph and source-hash manifest at most once per lazy
interval (or immediately after a write), then lets an ephemeral LLM process one
bounded wiki batch. A wiki backlog that is ready for processing schedules
process-only runs in between: they skip the graph rebuild and the tree hashing
scan, which would otherwise be repeated on every turn for as long as the
backlog lasts. Blocked or deferred sources do not count as ready. Nothing runs
on the UI or HTTP worker thread.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)

_LAZY_REFRESH_SECONDS = 60.0
_WRITE_DEBOUNCE_SECONDS = 2.0
# Minimum spacing between process-only runs (wiki batch without a fresh scan)
# while a ready backlog persists.
_BACKLOG_PROCESS_SECONDS = 10.0
_WIKI_FLOW_FQN = "pawflow.agents.wiki:1.0.0"
_WIKI_WORKFLOW_CUTOVER_ENV = "PAWFLOW_WIKI_WORKFLOW_CUTOVER"


@dataclass
class _MaintenanceJob:
    user_id: str
    relay_id: str
    service: Any
    conversation_id: str = ""
    agent_name: str = ""
    root: str = "."
    local: bool = False
    timer: Optional[threading.Timer] = None
    running: bool = False
    rerun: bool = False
    last_run_at: float = 0.0
    last_scan_at: float = 0.0
    changed_paths: set[str] = field(default_factory=set)
    # Whether the next started run must refresh the graph and rescan sources.
    scan_pending: bool = False
    # Whether the current run refreshes the graph and rescans sources.
    scan: bool = True
    runs: int = 0
    scans: int = 0


class ProjectMaintenanceScheduler:
    """Process-local coalescer keyed by `(user_id, relay_id)`."""

    _instance: Optional["ProjectMaintenanceScheduler"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ProjectMaintenanceScheduler":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: Dict[str, _MaintenanceJob] = {}

    @staticmethod
    def _key(user_id: str, relay_id: str) -> str:
        return f"{user_id}::{relay_id}"

    def schedule(self, *, user_id: str, relay_id: str, service,
                 conversation_id: str = "", agent_name: str = "",
                 root: str = ".", local: bool = False,
                 changed_path: str = "", force: bool = False) -> bool:
        if not user_id or not relay_id or service is None:
            return False
        key = self._key(user_id, relay_id)
        now = time.time()
        with self._lock:
            job = self._jobs.get(key)
            if job is None:
                job = _MaintenanceJob(user_id=user_id, relay_id=relay_id,
                                      service=service)
                self._jobs[key] = job
            job.service = service
            job.conversation_id = conversation_id or job.conversation_id
            job.agent_name = agent_name or job.agent_name
            job.root = str(root or ".")
            job.local = bool(local)
            if changed_path:
                job.changed_paths.add(str(changed_path).replace("\\", "/"))
            if job.running:
                job.rerun = job.rerun or force or bool(changed_path)
                return False
            scan_due = bool(
                force or changed_path
                or now - job.last_scan_at >= _LAZY_REFRESH_SECONDS)
            if not scan_due:
                # Scan cadence is separate from backlog processing: a ready
                # wiki backlog earns a process-only run, never an early scan.
                if now - job.last_run_at < _BACKLOG_PROCESS_SECONDS:
                    return False
                if not self._wiki_backlog_ready(user_id, relay_id):
                    return False
            if job.timer is not None:
                job.timer.cancel()
            # A pending timer that already owes a scan keeps owing it: a
            # later backlog-only request must not downgrade the run.
            job.scan_pending = job.scan_pending or scan_due
            delay = _WRITE_DEBOUNCE_SECONDS if (force or changed_path) else 0.05
            job.timer = threading.Timer(delay, self._start, args=(key,))
            job.timer.daemon = True
            job.timer.name = f"project-maint-{relay_id[:20]}"
            job.timer.start()
            return True

    @staticmethod
    def _wiki_backlog_ready(user_id: str, relay_id: str) -> bool:
        """Whether the wiki holds dirty sources a run could process right now.

        Blocked sources never become ready by waiting and deferred sources
        are waiting on their retry time; neither justifies a run.
        """
        try:
            from core.project_wiki import ProjectWiki
            status = ProjectWiki.for_relay(user_id, relay_id).status()
        except Exception:
            logger.debug("Failed to inspect pending project wiki", exc_info=True)
            return False
        ready = (int(status.get("dirty_sources") or 0)
                 - int(status.get("blocked_sources") or 0)
                 - int(status.get("deferred_sources") or 0))
        return ready > 0

    def _start(self, key: str) -> None:
        with self._lock:
            job = self._jobs.get(key)
            if job is None or job.running:
                return
            job.running = True
            job.timer = None
            job.rerun = False
            job.scan = job.scan_pending
            job.scan_pending = False
        try:
            self._run(job)
        except Exception:
            logger.warning("Project maintenance failed for relay=%s",
                           job.relay_id, exc_info=True)
        finally:
            with self._lock:
                job.running = False
                job.last_run_at = time.time()
                job.runs += 1
                if job.scan:
                    job.last_scan_at = job.last_run_at
                    job.scans += 1
                rerun = job.rerun
                job.rerun = False
                job.changed_paths.clear()
            if rerun:
                self.schedule(
                    user_id=job.user_id, relay_id=job.relay_id,
                    service=job.service, conversation_id=job.conversation_id,
                    agent_name=job.agent_name,
                    root=job.root, local=job.local, force=True)

    @staticmethod
    def _graph_seed_paths(graph) -> list[str]:
        counts = Counter(
            str(node.get("source_file") or "")
            for node in graph.nodes if node.get("source_file"))
        return [path for path, _count in counts.most_common(40)]

    @staticmethod
    def _resolve_wiki_service_id(user_id: str, conversation_id: str) -> str:
        """Resolve a wiki override, then the historical summarizer LLM."""
        from core.linked_service_bindings import resolve_llm_override
        client, _definition, service_id, _explicit = resolve_llm_override(
            "project_wiki", user_id, conversation_id)
        if client is not None:
            return service_id
        from core.summarizer_bindings import resolve_service
        summarizer, _definition, _explicit = resolve_service(
            user_id, conversation_id)
        if summarizer is None or not hasattr(summarizer, "resolve_llm_service"):
            return ""
        _client, _context_size, service_id = summarizer.resolve_llm_service(
            user_id, conversation_id)
        return str(service_id or "")

    def _submit_wiki_workflow(
            self, job: _MaintenanceJob, *,
            write_mode: str = "live") -> Dict[str, Any]:
        """Queue the exact Wiki Agent flow under the active user authority."""
        if write_mode not in {"live", "shadow"}:
            raise ValueError("Wiki workflow write_mode must be live or shadow")
        if not job.conversation_id or not job.agent_name:
            return {"status": "skipped", "reason": "missing conversation agent"}
        from core.authorization_context import active_authority_ref
        authorization = active_authority_ref(
            job.conversation_id, job.agent_name)
        if authorization is None or not authorization.root_turn_id:
            return {"status": "skipped", "reason": "missing authorization"}
        from core.relay_bindings import get_default
        if get_default(
                job.conversation_id, agent=job.agent_name) != job.relay_id:
            return {"status": "skipped", "reason": "relay binding changed"}
        from core.workflow_agent_contracts import WorkflowInstanceConfig
        from core.linked_service_bindings import resolve_agent_override
        target_agent, target_config, _explicit = resolve_agent_override(
            "project_wiki", job.user_id, job.conversation_id)
        if target_agent:
            workflow = dict(target_config.get("workflow") or {})
            parameters = dict(workflow.get("parameters") or {})
            parameters.update({
                "project_root": job.root,
                "write_mode": write_mode,
            })
            workflow["parameters"] = parameters
            binding = WorkflowInstanceConfig.from_dict(workflow)
        else:
            service_id = self._resolve_wiki_service_id(
                job.user_id, job.conversation_id)
            if not service_id:
                return {
                    "status": "skipped", "reason": "missing summarizer LLM"}
            from core.workflow_agent_resources import bind_agent_workflow
            binding = WorkflowInstanceConfig.from_dict(bind_agent_workflow({
                "flow_fqn": _WIKI_FLOW_FQN,
                "preempt_policy": "checkpoint",
                "parameters": {
                    "project_root": job.root,
                    "extractor_llm": service_id,
                    "writer_llm": service_id,
                    "batch_files": 8,
                    "max_files": 10000,
                    "write_mode": write_mode,
                },
            }, job.user_id, job.conversation_id))
        from core.workflow_agent_runtime import (
            WorkflowAgentRuntime,
            prepare_workflow_turn,
        )
        runtime = WorkflowAgentRuntime.instance()
        request = prepare_workflow_turn(
            conversation_id=job.conversation_id,
            agent_name=target_agent or job.agent_name,
            user_id=job.user_id,
            message="Refresh the project wiki from pending relay changes.",
            attachments=[],
            message_id=authorization.root_turn_id,
            channel="maintenance",
            permission_mode="default",
            source={
                "type": "user",
                "name": job.user_id,
                "authorization": authorization.to_dict(),
            },
            runtime=runtime,
        )
        return runtime.submit_bound(
            request, binding, invocation_mode="silent_maintenance")

    @staticmethod
    def _workflow_cutover_enabled() -> bool:
        return os.environ.get(_WIKI_WORKFLOW_CUTOVER_ENV, "").strip() == "1"

    def _run(self, job: _MaintenanceJob) -> None:
        if job.conversation_id and job.agent_name:
            try:
                from core.scratchdir_manager import ScratchDirManager
                cleaned = ScratchDirManager(job.service).cleanup_expired(
                    job.user_id, job.conversation_id,
                    job.agent_name, job.relay_id)
                if cleaned:
                    logger.info(
                        "Expired ScratchDir cleared relay=%s conversation=%s agent=%s",
                        job.relay_id, job.conversation_id[:8], job.agent_name)
            except Exception:
                logger.warning(
                    "ScratchDir expiry cleanup failed relay=%s conversation=%s agent=%s",
                    job.relay_id, job.conversation_id[:8], job.agent_name,
                    exc_info=True)
        from core.project_graph import ProjectGraph
        from core.project_wiki import ProjectWiki

        wiki = ProjectWiki.for_relay(job.user_id, job.relay_id)
        if job.scan:
            graph = ProjectGraph.for_relay(job.user_id, job.relay_id)
            # Project knowledge is relay-scoped. A server-local filesystem
            # mutation may schedule this job with local=True, but indexing
            # that surface would scan the deployed /app tree instead of the
            # relay project.
            graph_result = graph.build_from_relay(
                job.service, job.root, local=False)
            if graph_result.get("status") == "error":
                logger.warning(
                    "Automatic project graph refresh failed relay=%s: %s",
                    job.relay_id, graph_result.get("reason", ""))
            # The wiki always scans the relay container, whatever surface
            # the graph build used: local=true would index the server/host tree.
            wiki_result = wiki.scan_from_relay(
                job.service, job.root, local=False,
                initial_paths=self._graph_seed_paths(graph))
        else:
            graph_result = {"status": "skipped", "reason": "scan not due"}
            wiki_result = {"status": "skipped", "reason": "scan not due"}
        from core.linked_service_bindings import resolve_agent_override
        wiki_agent, _config, _explicit = resolve_agent_override(
            "project_wiki", job.user_id, job.conversation_id)
        if self._workflow_cutover_enabled() or wiki_agent:
            update_result = self._submit_wiki_workflow(job)
        else:
            from core.linked_service_bindings import resolve_llm_override
            llm_client, _definition, _service_id, _explicit = (
                resolve_llm_override(
                    "project_wiki", job.user_id, job.conversation_id))
            if llm_client is None:
                from core.summarizer_bindings import resolve_service
                summarizer, _definition, _explicit = resolve_service(
                    job.user_id, job.conversation_id)
                if summarizer is not None and hasattr(
                        summarizer, "resolve_llm_service"):
                    llm_client, _context_size, _service_id = (
                        summarizer.resolve_llm_service(
                            job.user_id, job.conversation_id))
            update_result = wiki.auto_update(
                job.service, llm_client, local=False)
        logger.info(
            "Project maintenance relay=%s graph=%s wiki=%s update=%s",
            job.relay_id, graph_result.get("status"),
            wiki_result.get("status"), update_result.get("status"))

    def snapshot(self, user_id: str, relay_id: str) -> Dict[str, Any]:
        key = self._key(user_id, relay_id)
        with self._lock:
            job = self._jobs.get(key)
            if job is None:
                return {"scheduled": False, "running": False}
            return {
                "scheduled": job.timer is not None,
                "running": job.running,
                "last_run_at": job.last_run_at,
                "last_scan_at": job.last_scan_at,
                "runs": job.runs,
                "scans": job.scans,
                "scan_pending": job.scan_pending,
                "changed_paths": sorted(job.changed_paths),
            }


def schedule_project_maintenance(**kwargs) -> bool:
    """Schedule a non-blocking project refresh; never raise into a caller."""
    try:
        return ProjectMaintenanceScheduler.instance().schedule(**kwargs)
    except Exception:
        logger.debug("Failed to schedule project maintenance", exc_info=True)
        return False
