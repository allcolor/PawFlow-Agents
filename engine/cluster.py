"""Cluster mode — multi-instance coordination for OpenPaw."""

import json
import os
import time
import threading
import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum


class InstanceRole(Enum):
    COORDINATOR = "coordinator"
    WORKER = "worker"
    STANDBY = "standby"


@dataclass
class InstanceInfo:
    instance_id: str
    role: InstanceRole
    host: str
    port: int
    api_port: int = 8000
    started_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_alive(self, timeout: float = 30.0) -> bool:
        return (time.time() - self.last_heartbeat) < timeout


class ClusterState:
    """Shared cluster state backed by filesystem (or Redis in future)."""

    def __init__(self, state_dir: str = "cluster_state"):
        self._state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self._lock = threading.Lock()

    def register_instance(self, info: InstanceInfo):
        """Register this instance in shared state."""
        path = os.path.join(self._state_dir, f"instance_{info.instance_id}.json")
        data = {
            "instance_id": info.instance_id,
            "role": info.role.value,
            "host": info.host,
            "port": info.port,
            "api_port": info.api_port,
            "started_at": info.started_at,
            "last_heartbeat": info.last_heartbeat,
            "metadata": info.metadata,
        }
        with self._lock:
            with open(path, "w") as f:
                json.dump(data, f)

    def update_heartbeat(self, instance_id: str):
        """Update heartbeat timestamp."""
        path = os.path.join(self._state_dir, f"instance_{instance_id}.json")
        if not os.path.exists(path):
            return
        with self._lock:
            with open(path) as f:
                data = json.load(f)
            data["last_heartbeat"] = time.time()
            with open(path, "w") as f:
                json.dump(data, f)

    def get_instances(self, timeout: float = 30.0) -> List[InstanceInfo]:
        """Get all registered instances, filtering out dead ones."""
        instances = []
        if not os.path.exists(self._state_dir):
            return instances
        for fname in os.listdir(self._state_dir):
            if not fname.startswith("instance_") or not fname.endswith(".json"):
                continue
            path = os.path.join(self._state_dir, fname)
            try:
                with open(path) as f:
                    data = json.load(f)
                info = InstanceInfo(
                    instance_id=data["instance_id"],
                    role=InstanceRole(data["role"]),
                    host=data["host"],
                    port=data["port"],
                    api_port=data.get("api_port", 8000),
                    started_at=data["started_at"],
                    last_heartbeat=data["last_heartbeat"],
                    metadata=data.get("metadata", {}),
                )
                instances.append(info)
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return instances

    def get_alive_instances(self, timeout: float = 30.0) -> List[InstanceInfo]:
        """Get only alive instances."""
        return [i for i in self.get_instances() if i.is_alive(timeout)]

    def remove_instance(self, instance_id: str):
        """Remove instance from shared state."""
        path = os.path.join(self._state_dir, f"instance_{instance_id}.json")
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                # On Windows, file may be locked; mark as dead instead
                try:
                    with self._lock:
                        with open(path) as f:
                            data = json.load(f)
                        data["last_heartbeat"] = 0  # epoch — effectively dead
                        with open(path, "w") as f:
                            json.dump(data, f)
                except OSError:
                    pass

    def get_coordinator(self, timeout: float = 30.0) -> Optional[InstanceInfo]:
        """Get the current coordinator if alive."""
        for inst in self.get_alive_instances(timeout):
            if inst.role == InstanceRole.COORDINATOR:
                return inst
        return None

    def claim_coordinator(self, instance_id: str, timeout: float = 30.0) -> bool:
        """Try to become coordinator. Returns True if successful."""
        lock_path = os.path.join(self._state_dir, "coordinator.lock")
        with self._lock:
            # Check if there's already a live coordinator
            current = self._get_coordinator_unlocked(timeout)
            if current and current.instance_id != instance_id:
                return False
            # Write/overwrite lock file (stale locks are simply overwritten)
            with open(lock_path, "w") as f:
                json.dump({"instance_id": instance_id, "claimed_at": time.time()}, f)
            # Update role
            path = os.path.join(self._state_dir, f"instance_{instance_id}.json")
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                data["role"] = InstanceRole.COORDINATOR.value
                with open(path, "w") as f:
                    json.dump(data, f)
            return True

    def _get_coordinator_unlocked(self, timeout: float = 30.0) -> Optional[InstanceInfo]:
        """Get coordinator without acquiring lock (for use when lock is already held)."""
        for inst in self._get_alive_unlocked(timeout):
            if inst.role == InstanceRole.COORDINATOR:
                return inst
        return None

    def _get_alive_unlocked(self, timeout: float = 30.0) -> List[InstanceInfo]:
        """Get alive instances without acquiring lock."""
        return [i for i in self._get_instances_unlocked() if i.is_alive(timeout)]

    def _get_instances_unlocked(self) -> List[InstanceInfo]:
        """Read all instance files without acquiring lock."""
        instances = []
        if not os.path.exists(self._state_dir):
            return instances
        for fname in os.listdir(self._state_dir):
            if not fname.startswith("instance_") or not fname.endswith(".json"):
                continue
            path = os.path.join(self._state_dir, fname)
            try:
                with open(path) as f:
                    data = json.load(f)
                info = InstanceInfo(
                    instance_id=data["instance_id"],
                    role=InstanceRole(data["role"]),
                    host=data["host"],
                    port=data["port"],
                    api_port=data.get("api_port", 8000),
                    started_at=data["started_at"],
                    last_heartbeat=data["last_heartbeat"],
                    metadata=data.get("metadata", {}),
                )
                instances.append(info)
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return instances

    def release_coordinator(self, instance_id: str):
        """Release coordinator role."""
        lock_path = os.path.join(self._state_dir, "coordinator.lock")
        if os.path.exists(lock_path):
            try:
                with open(lock_path) as f:
                    data = json.load(f)
                if data.get("instance_id") == instance_id:
                    os.remove(lock_path)
            except (json.JSONDecodeError, OSError):
                pass


class ClusterCoordinator:
    """Multi-instance cluster coordinator.

    Uses filesystem-based shared state for instance registration,
    heartbeat, and coordinator election. Future: Redis backend.

    Usage:
        cluster = ClusterCoordinator(host="0.0.0.0", port=8081)
        cluster.start()
        # ... cluster is running ...
        cluster.stop()
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8081,
        api_port: int = 8000,
        state_dir: str = "cluster_state",
        heartbeat_interval: float = 10.0,
        heartbeat_timeout: float = 30.0,
        auto_promote: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.instance_id = uuid.uuid4().hex[:12]
        self.host = host
        self.port = port
        self.api_port = api_port
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.auto_promote = auto_promote

        self._state = ClusterState(state_dir)
        self._info = InstanceInfo(
            instance_id=self.instance_id,
            role=InstanceRole.STANDBY,
            host=host,
            port=port,
            api_port=api_port,
            metadata=metadata or {},
        )

        self._running = False
        self._stepped_down = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._election_thread: Optional[threading.Thread] = None
        self._callbacks: Dict[str, list] = {
            "promoted": [],
            "demoted": [],
            "instance_joined": [],
            "instance_left": [],
        }

    @property
    def role(self) -> InstanceRole:
        return self._info.role

    @property
    def is_coordinator(self) -> bool:
        return self._info.role == InstanceRole.COORDINATOR

    def on(self, event: str, callback):
        """Register event callback. Events: promoted, demoted, instance_joined, instance_left."""
        if event in self._callbacks:
            self._callbacks[event].append(callback)

    def _emit(self, event: str, **kwargs):
        for cb in self._callbacks.get(event, []):
            try:
                cb(**kwargs)
            except Exception:
                pass

    def start(self):
        """Start cluster participation."""
        self._running = True
        self._state.register_instance(self._info)

        # Try to become coordinator if no one else is
        if self.auto_promote:
            current = self._state.get_coordinator(self.heartbeat_timeout)
            if not current:
                if self._state.claim_coordinator(self.instance_id, self.heartbeat_timeout):
                    self._info.role = InstanceRole.COORDINATOR
                    # Re-register so heartbeat thread sees correct role
                    self._state.register_instance(self._info)
                    self._emit("promoted", instance_id=self.instance_id)

        # Start heartbeat (after promotion so it reads correct role)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="cluster-heartbeat"
        )
        self._heartbeat_thread.start()

        # Start election monitor
        if self.auto_promote:
            self._election_thread = threading.Thread(
                target=self._election_loop, daemon=True, name="cluster-election"
            )
            self._election_thread.start()

    def stop(self):
        """Stop cluster participation."""
        self._running = False
        # Wait for threads to finish first (avoids file locks on Windows)
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
        if self._election_thread:
            self._election_thread.join(timeout=5)
        if self.is_coordinator:
            self._state.release_coordinator(self.instance_id)
        self._state.remove_instance(self.instance_id)

    def _heartbeat_loop(self):
        """Periodically update heartbeat."""
        while self._running:
            try:
                self._state.update_heartbeat(self.instance_id)
            except Exception:
                pass
            # Sleep in small increments for responsive shutdown
            for _ in range(int(self.heartbeat_interval * 10)):
                if not self._running:
                    return
                time.sleep(0.1)

    def _election_loop(self):
        """Monitor coordinator health, promote self if needed."""
        known_instances = set()
        while self._running:
            try:
                alive = self._state.get_alive_instances(self.heartbeat_timeout)
                alive_ids = {i.instance_id for i in alive}

                # Detect joins/leaves
                for inst in alive:
                    if inst.instance_id not in known_instances:
                        known_instances.add(inst.instance_id)
                        if inst.instance_id != self.instance_id:
                            self._emit("instance_joined", instance=inst)
                for old_id in list(known_instances):
                    if old_id not in alive_ids and old_id != self.instance_id:
                        known_instances.discard(old_id)
                        self._emit("instance_left", instance_id=old_id)

                # Check coordinator
                coordinator = self._state.get_coordinator(self.heartbeat_timeout)
                if not coordinator and not self.is_coordinator and not self._stepped_down:
                    # No coordinator alive — try to claim
                    if self._state.claim_coordinator(self.instance_id, self.heartbeat_timeout):
                        self._info.role = InstanceRole.COORDINATOR
                        self._emit("promoted", instance_id=self.instance_id)

            except Exception:
                pass

            for _ in range(int(self.heartbeat_interval * 10)):
                if not self._running:
                    return
                time.sleep(0.1)

    def get_instances(self) -> List[Dict[str, Any]]:
        """Get all alive cluster instances."""
        instances = self._state.get_alive_instances(self.heartbeat_timeout)
        result = []
        for inst in instances:
            result.append({
                "instance_id": inst.instance_id,
                "role": inst.role.value,
                "host": inst.host,
                "port": inst.port,
                "api_port": inst.api_port,
                "alive": inst.is_alive(self.heartbeat_timeout),
                "started_at": inst.started_at,
                "last_heartbeat": inst.last_heartbeat,
                "metadata": inst.metadata,
                "is_self": inst.instance_id == self.instance_id,
            })
        return result

    def get_status(self) -> Dict[str, Any]:
        """Get cluster status summary."""
        instances = self._state.get_alive_instances(self.heartbeat_timeout)
        coordinator = self._state.get_coordinator(self.heartbeat_timeout)
        return {
            "instance_id": self.instance_id,
            "role": self._info.role.value,
            "total_instances": len(instances),
            "coordinator": coordinator.instance_id if coordinator else None,
            "coordinator_host": f"{coordinator.host}:{coordinator.api_port}" if coordinator else None,
            "instances": [
                {"id": i.instance_id, "role": i.role.value, "host": f"{i.host}:{i.api_port}"}
                for i in instances
            ],
        }

    def promote_to_coordinator(self) -> bool:
        """Manually promote this instance to coordinator."""
        if self._state.claim_coordinator(self.instance_id, self.heartbeat_timeout):
            self._stepped_down = False
            self._info.role = InstanceRole.COORDINATOR
            self._state.register_instance(self._info)
            self._emit("promoted", instance_id=self.instance_id)
            return True
        return False

    def step_down(self):
        """Step down from coordinator role. Prevents auto-re-election."""
        if self.is_coordinator:
            self._stepped_down = True
            self._state.release_coordinator(self.instance_id)
            old_role = self._info.role
            self._info.role = InstanceRole.STANDBY
            self._state.register_instance(self._info)
            if old_role == InstanceRole.COORDINATOR:
                self._emit("demoted", instance_id=self.instance_id)
