"""Registry for deployed flow instances.

Manages the inventory of ALL deployed instances (running + stopped).
Uses filesystem-based persistence: data/deployments/{owner}/*.json.

A flow is versioned in the repository (FQN like default.pawflow_agent:1.0.0).
A deployment is a running instance pinned to a specific flow version.
"""

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

import core.paths as _paths
GLOBAL_OWNER = "__global__"


@dataclass
class DeployedInstance:
    """A single deployed flow instance.

    flow_fqn is the fully qualified name in the repository:
        package.flow_name:version  (e.g. default.pawflow_agent:1.0.0)
    """

    instance_id: str
    flow_id: str                          # flow name (e.g. pawflow_agent)
    flow_name: str                        # display name
    flow_fqn: str = ""                    # repository FQN (package.name:version)
    flow_scope: str = ""                  # repository scope: conv | user | global
    flow_path: str = ""                   # legacy — fallback for unversioned flows
    owner: Optional[str] = None           # None = global
    status: str = "stopped"               # running | stopped | error
    source: str = "gui"                   # gui | agent
    parameters: Dict[str, Any] = field(default_factory=dict)
    conversation_id: Optional[str] = None
    agent_name: str = ""
    created_at: float = field(default_factory=time.time)
    last_started: Optional[float] = None
    last_stopped: Optional[float] = None
    error_message: Optional[str] = None
    max_workers: int = 4
    max_retries: int = 3
    service_overrides: Dict[str, str] = field(default_factory=dict)
    service_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    layout: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Remove None values for cleaner JSON
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "DeployedInstance":
        # Accept all known fields, ignore unknown
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class DeploymentRegistry:
    """Thread-safe singleton registry for deployed flow instances.

    Persistence is filesystem-based:
        data/deployments/global/*.json       → owner=None
        data/deployments/{username}/*.json   → owner=username
    """

    _instance: Optional["DeploymentRegistry"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._instances: Dict[str, DeployedInstance] = {}
        self._data_lock = threading.Lock()
        self._counter = 0
        self._loaded = False

    @classmethod
    def get_instance(cls) -> "DeploymentRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def _ensure_loaded(self):
        """Lazy-load from disk on first access."""
        if not self._loaded:
            with self._data_lock:
                if not self._loaded:
                    self._scan_disk()
                    self._loaded = True

    def reload(self) -> None:
        """Force re-scan disk for new/removed deployments."""
        with self._data_lock:
            self._scan_disk()
            self._loaded = True

    # ---- CRUD ----

    def deploy(
        self,
        template_path: str,
        owner: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        max_workers: int = 4,
        max_retries: int = 3,
        source: str = "gui",
        conversation_id: Optional[str] = None,
        agent_name: str = "",
        instance_id: Optional[str] = None,
        service_overrides: Optional[Dict[str, str]] = None,
        service_configs: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        """Create a new deployed instance from a template. Returns instance_id."""
        self._ensure_loaded()

        # Load template to get flow_id and name
        tpath = Path(template_path)
        if not tpath.exists():
            raise FileNotFoundError(f"Template not found: {template_path}")

        raw = json.loads(tpath.read_text(encoding="utf-8"))
        flow_id = raw.get("id", tpath.stem)
        flow_name = raw.get("name", flow_id)

        # Generate unique instance_id
        if not instance_id:
            self._counter += 1
            short = uuid.uuid4().hex[:6]
            instance_id = f"{flow_id}__{short}"

        # Copy layout from flow template if available
        flow_layout = raw.get("layout", {})

        inst = DeployedInstance(
            instance_id=instance_id,
            flow_id=flow_id,
            flow_name=flow_name,
            flow_path=str(tpath),
            owner=owner,
            status="stopped",
            source=source,
            parameters=parameters or {},
            conversation_id=conversation_id,
            agent_name=agent_name or "",
            created_at=time.time(),
            max_workers=max_workers,
            max_retries=max_retries,
            service_overrides=service_overrides or {},
            service_configs=service_configs or {},
            layout=flow_layout,
        )

        with self._data_lock:
            self._instances[instance_id] = inst
        self._save_instance(inst)
        logger.info("Deployed instance '%s' from template '%s'", instance_id, flow_id)
        return instance_id


    def save_layout(self, instance_id: str, layout: Dict[str, Any]) -> None:
        """Save layout positions for a deployed instance."""
        self._ensure_loaded()
        with self._data_lock:
            inst = self._instances.get(instance_id)
            if inst is None:
                return
            inst.layout = layout
        self._save_instance(inst)

    def get_layout(self, instance_id: str) -> Dict[str, Any]:
        """Get layout positions for a deployed instance."""
        self._ensure_loaded()
        with self._data_lock:
            inst = self._instances.get(instance_id)
            if inst is None:
                return {}
            return dict(inst.layout)

    def undeploy(self, instance_id: str) -> None:
        """Remove a deployed instance (stop if running + delete file)."""
        self._ensure_loaded()

        with self._data_lock:
            inst = self._instances.pop(instance_id, None)
        if inst is None:
            logger.warning("Cannot undeploy '%s': not found", instance_id)
            return

        # Stop executor if running
        if inst.status == "running":
            try:
                from core.executor_registry import ExecutorRegistry
                reg = ExecutorRegistry.get_instance()
                ex = reg.get(instance_id)
                if ex:
                    ex.stop()
                    reg.unregister(instance_id)
            except Exception as e:
                logger.warning("Error stopping executor for '%s': %s", instance_id, e)

        self._delete_instance_file(instance_id, inst.owner)
        logger.info("Undeployed instance '%s'", instance_id)

    def update_status(
        self, instance_id: str, status: str, error: Optional[str] = None
    ) -> None:
        """Update the status of a deployed instance."""
        self._ensure_loaded()

        with self._data_lock:
            inst = self._instances.get(instance_id)
            if inst is None:
                logger.debug("update_status: instance '%s' not found", instance_id)
                return
            inst.status = status
            inst.error_message = error
            if status == "running":
                inst.last_started = time.time()
            elif status == "stopped":
                inst.last_stopped = time.time()

        self._save_instance(inst)

    def set_owner(self, instance_id: str, new_owner: Optional[str]) -> None:
        """Change the owner of an instance (moves file on disk)."""
        self._ensure_loaded()

        with self._data_lock:
            inst = self._instances.get(instance_id)
            if inst is None:
                return
            old_owner = inst.owner
            inst.owner = new_owner

        # Delete old file, save new
        self._delete_instance_file(instance_id, old_owner)
        self._save_instance(inst)

    # ---- Queries ----

    def get(self, instance_id: str) -> Optional[DeployedInstance]:
        self._ensure_loaded()
        with self._data_lock:
            return self._instances.get(instance_id)

    def get_all(self) -> Dict[str, DeployedInstance]:
        self._ensure_loaded()
        with self._data_lock:
            return dict(self._instances)

    def get_grouped(self) -> Dict[str, List[DeployedInstance]]:
        """Return instances grouped by owner.

        Key GLOBAL_OWNER for global instances, username for user instances.
        """
        self._ensure_loaded()
        groups: Dict[str, List[DeployedInstance]] = {}
        with self._data_lock:
            for inst in self._instances.values():
                key = inst.owner or GLOBAL_OWNER
                groups.setdefault(key, []).append(inst)
        # Sort each group by created_at
        for key in groups:
            groups[key].sort(key=lambda x: x.created_at)
        return groups

    def get_by_owner(self, owner: Optional[str]) -> List[DeployedInstance]:
        """Get all instances for a specific owner."""
        self._ensure_loaded()
        with self._data_lock:
            return [
                inst for inst in self._instances.values()
                if inst.owner == owner
            ]

    def get_by_conversation(
        self, conversation_id: str, owner: Optional[str] = None
    ) -> List[DeployedInstance]:
        """Get instances for a conversation, optionally filtered by owner."""
        self._ensure_loaded()
        with self._data_lock:
            results = []
            for inst in self._instances.values():
                if inst.conversation_id != conversation_id:
                    continue
                if owner is not None and inst.owner != owner:
                    continue
                results.append(inst)
            return results

    # ---- Sync ----

    def sync_with_executors(self) -> None:
        """Cross-reference with ExecutorRegistry to update statuses.

        - Running instances whose executor died → mark stopped
        - Unknown executors → create global instance
        """
        self._ensure_loaded()

        try:
            from core.executor_registry import ExecutorRegistry
            reg = ExecutorRegistry.get_instance()
        except Exception:
            return

        executors = reg.get_all()
        executor_ids = set(executors.keys())

        with self._data_lock:
            for iid, inst in self._instances.items():
                if inst.status == "running" and iid not in executor_ids:
                    # Running instance whose executor died → mark stopped
                    inst.status = "stopped"
                    inst.last_stopped = time.time()
                    self._save_instance(inst)
                    logger.info("Instance '%s' executor died, marked stopped", iid)
                elif inst.status != "running" and iid in executor_ids:
                    # Stopped/error instance with a live executor → mark running
                    inst.status = "running"
                    inst.last_started = time.time()
                    inst.error_message = None
                    self._save_instance(inst)
                    logger.info("Instance '%s' has live executor, marked running", iid)

            # Check for executors not tracked as instances
            known_ids = set(self._instances.keys())
            for eid in executor_ids:
                if eid not in known_ids:
                    # Create a global instance for this unknown executor
                    ex = executors[eid]
                    flow = getattr(ex, '_flow', None)
                    if flow is None:
                        continue
                    flow_id = getattr(flow, 'id', eid)
                    flow_name = getattr(flow, 'name', flow_id)
                    inst = DeployedInstance(
                        instance_id=eid,
                        flow_id=flow_id,
                        flow_name=flow_name,
                        flow_path=self._find_flow_path(flow_id) or "",
                        owner=None,
                        status="running",
                        source="gui",
                        created_at=time.time(),
                        last_started=time.time(),
                        max_workers=getattr(ex, '_max_workers', 4),
                        max_retries=getattr(ex, '_max_retries', 3),
                    )
                    self._instances[eid] = inst
                    self._save_instance(inst)
                    logger.info("Created instance for unknown executor '%s'", eid)

    # ---- Persistence ----

    def _owner_dir(self, owner: Optional[str]) -> Path:
        """Get the directory for a given owner."""
        if owner is None:
            return _paths.DEPLOYMENTS_DIR / "global"
        return _paths.DEPLOYMENTS_DIR / owner

    def _save_instance(self, inst: DeployedInstance) -> None:
        """Save an instance to its JSON file on disk."""
        owner_dir = self._owner_dir(inst.owner)
        owner_dir.mkdir(parents=True, exist_ok=True)
        path = owner_dir / f"{inst.instance_id}.json"
        try:
            path.write_text(
                json.dumps(inst.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Failed to save instance '%s': %s", inst.instance_id, e)

    def _delete_instance_file(
        self, instance_id: str, owner: Optional[str] = None
    ) -> None:
        """Delete the JSON file for an instance."""
        path = self._owner_dir(owner) / f"{instance_id}.json"
        try:
            if path.exists():
                path.unlink()
            # Clean up empty owner dir (but not "global")
            parent = path.parent
            if parent.name != "global" and parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except Exception as e:
            logger.debug("Failed to delete instance file '%s': %s", instance_id, e)

    def _scan_disk(self) -> None:
        """Scan data/deployments/ and load all instances."""
        if not _paths.DEPLOYMENTS_DIR.exists():
            return

        for owner_dir in _paths.DEPLOYMENTS_DIR.iterdir():
            if not owner_dir.is_dir():
                continue
            owner = None if owner_dir.name == "global" else owner_dir.name
            for jf in owner_dir.glob("*.json"):
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    # Ensure owner matches directory
                    data["owner"] = owner
                    inst = DeployedInstance.from_dict(data)
                    self._instances[inst.instance_id] = inst
                except Exception as e:
                    logger.warning("Failed to load deployment '%s': %s", jf, e)

        logger.info("Loaded %d deployment(s) from disk", len(self._instances))

    @staticmethod
    def _find_flow_path(flow_id: str) -> Optional[str]:
        """Find the template JSON file for a flow ID."""
        for dir_name in ("flows",):
            flows_dir = Path(dir_name)
            if flows_dir.exists():
                for p in flows_dir.glob("*.json"):
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                        if data.get("id") == flow_id:
                            return str(p)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

