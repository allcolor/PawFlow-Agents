"""Coalesced background maintenance for relay-scoped project knowledge.

Each active relay is one project. Context preparation schedules a cheap lazy
refresh; filesystem writes reschedule it with a short debounce. The worker
refreshes the AST graph and source-hash manifest, then lets an ephemeral LLM
process one bounded wiki batch. Nothing runs on the UI or HTTP worker thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)

_LAZY_REFRESH_SECONDS = 60.0
_WRITE_DEBOUNCE_SECONDS = 2.0


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
    changed_paths: set[str] = field(default_factory=set)


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
            pending_wiki = False
            try:
                from core.project_wiki import ProjectWiki
                pending_wiki = bool(ProjectWiki.for_relay(
                    user_id, relay_id).status()["dirty_sources"])
            except Exception:
                logger.debug("Failed to inspect pending project wiki", exc_info=True)
            if (not force and not changed_path and not pending_wiki
                    and now - job.last_run_at < _LAZY_REFRESH_SECONDS):
                return False
            if job.timer is not None:
                job.timer.cancel()
            delay = _WRITE_DEBOUNCE_SECONDS if (force or changed_path) else 0.05
            job.timer = threading.Timer(delay, self._start, args=(key,))
            job.timer.daemon = True
            job.timer.name = f"project-maint-{relay_id[:20]}"
            job.timer.start()
            return True

    def _start(self, key: str) -> None:
        with self._lock:
            job = self._jobs.get(key)
            if job is None or job.running:
                return
            job.running = True
            job.timer = None
            job.rerun = False
        try:
            self._run(job)
        except Exception:
            logger.warning("Project maintenance failed for relay=%s",
                           job.relay_id, exc_info=True)
        finally:
            with self._lock:
                job.running = False
                job.last_run_at = time.time()
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
    def _resolve_wiki_client(user_id: str, conversation_id: str):
        """Resolve only the LLM configured by the effective summarizer service."""
        from core.summarizer_bindings import resolve_service
        summarizer, _definition, _explicit = resolve_service(
            user_id, conversation_id)
        if summarizer is None or not hasattr(summarizer, "resolve_llm_service"):
            return None
        client, _context_size, _service_id = summarizer.resolve_llm_service(
            user_id, conversation_id)
        return client

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

        graph = ProjectGraph.for_relay(job.user_id, job.relay_id)
        graph_result = graph.build_from_relay(
            job.service, job.root, local=job.local)
        if graph_result.get("status") == "error":
            logger.warning("Automatic project graph refresh failed relay=%s: %s",
                           job.relay_id, graph_result.get("reason", ""))
        wiki = ProjectWiki.for_relay(job.user_id, job.relay_id)
        # The wiki always scans the relay container, whatever surface the
        # graph build used: local=true would index the server/host tree.
        wiki_result = wiki.scan_from_relay(
            job.service, job.root, local=False,
            initial_paths=self._graph_seed_paths(graph))
        llm_client = self._resolve_wiki_client(
            job.user_id, job.conversation_id)
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
                "changed_paths": sorted(job.changed_paths),
            }


def schedule_project_maintenance(**kwargs) -> bool:
    """Schedule a non-blocking project refresh; never raise into a caller."""
    try:
        return ProjectMaintenanceScheduler.instance().schedule(**kwargs)
    except Exception:
        logger.debug("Failed to schedule project maintenance", exc_info=True)
        return False
