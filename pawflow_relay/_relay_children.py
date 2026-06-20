"""Child-relay lifecycle for the relay worker (spawn_relay / stop_relay).

The server can ask a relay to spawn a child relay rooted at a different
directory (optionally in its own Docker container). Extracted from
_ws_connect's message loop. The connection-scoped values it needs are passed
in: a ChildRelayConfig (the parent's url + credentials + --allow-* flags, all
stable across reconnects), a DockerEnv (resolved by the worker so the
globals()/args reads stay in worker scope), and a ``send_locked(bytes)``
callback that writes a frame to the live socket under the send lock.

``_ws_connect`` is imported lazily inside the child thread to avoid a circular
import with worker.py.
"""
import json
import logging
import os
import subprocess  # nosec B404
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass
class ChildRelayConfig:
    """Parent-relay settings inherited by spawned children."""
    url: str
    token: str
    secret: str
    readonly: bool
    allow_exec: bool
    allow_automation: bool
    allow_local_screen: bool
    allow_local: bool


@dataclass
class DockerEnv:
    """Docker context resolved by the worker (keeps globals()/args in scope)."""
    parent_docker: bool
    cpus: str
    memory: str


class ChildRelayManager:
    """Tracks child-relay threads for one WS connection.

    Recreated per (re)connection, mirroring the old per-connection
    ``_child_relays = {}`` reset in the worker's reconnect loop.
    """

    def __init__(self):
        self.children = {}  # relay_id -> thread

    def handle_spawn(self, msg, cfg, docker, send_locked):
        """Spawn a child relay for a different root. Replies via send_locked."""
        _sr_root = msg.get("root", "")
        _sr_id = msg.get("relay_id", "")
        _sr_token = msg.get("token", cfg.token)
        _sr_secret = msg.get("secret", cfg.secret)
        _sr_rid = msg.get("request_id", "")
        if not _sr_root or not os.path.isdir(_sr_root):
            send_locked(json.dumps({"type": "result", "request_id": _sr_rid,
                "data": {"ok": False, "error": f"Directory not found: {_sr_root}"}}).encode("utf-8"))
            return
        sys.stderr.write(f"[FSRelay] Spawning child relay: {_sr_id} -> {_sr_root}\n")
        _child_docker_image = msg.get("docker_image", "")
        _child_thread = threading.Thread(
            target=self._child_relay,
            args=(cfg, docker, _sr_id, _sr_token, _sr_secret, _sr_root,
                  _child_docker_image),
            daemon=True, name=f"relay-child-{_sr_id}")
        _child_thread.start()
        self.children[_sr_id] = _child_thread
        send_locked(json.dumps({"type": "result", "request_id": _sr_rid,
            "data": {"ok": True, "relay_id": _sr_id, "root": _sr_root}}).encode("utf-8"))

    def _child_relay(self, cfg, docker, sr_id, sr_token, sr_secret, sr_root,
                     docker_img=""):
        _child_container = None
        try:
            # Start child Docker container if parent uses Docker
            if docker_img or docker.parent_docker:
                import uuid as _uuid_child
                from fs_common import _docker_cmd, _translate_path, _to_host_path
                _img = docker_img or "pawflow-relay-dev:latest"
                _child_container = f"pawflow-relay-child-{_uuid_child.uuid4().hex[:8]}"
                _dr = subprocess.run(_docker_cmd() + [  # nosec B603
                    "run", "-d",
                    "--name", _child_container,
                    "--init",
                    "-v", f"{_translate_path(_to_host_path(sr_root))}:/workspace",
                    "-w", "/workspace",
                    "--cpus", docker.cpus, "--memory", docker.memory,
                    "--security-opt", "no-new-privileges",
                    _img, "tail", "-f", "/dev/null",
                ], capture_output=True, text=True)
                if _dr.returncode == 0:
                    # Register container for this root dir
                    import fs_actions as _fsa
                    if not hasattr(_fsa, '_DOCKER_CONTAINERS'):
                        _fsa._DOCKER_CONTAINERS = {}
                    _fsa._DOCKER_CONTAINERS[str(Path(sr_root).resolve())] = _child_container
                    sys.stderr.write(f"[FSRelay] Child container: {_child_container}\n")
                else:
                    _child_container = None
            from pawflow_relay.worker import _ws_connect
            _ws_connect(cfg.url, sr_token, sr_secret, sr_id, sr_root,
                        readonly=cfg.readonly, allow_exec=cfg.allow_exec,
                        allow_automation=cfg.allow_automation,
                        allow_local_screen=cfg.allow_local_screen,
                        allow_local=cfg.allow_local)
        except Exception as _ce:
            sys.stderr.write(f"[FSRelay] Child {sr_id} died: {_ce}\n")
        finally:
            if _child_container:
                # Unregister from dict
                try:
                    import fs_actions as _fsa2
                    _fsa2._DOCKER_CONTAINERS.pop(str(Path(sr_root).resolve()), None)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                try:
                    from fs_common import _docker_cmd
                    subprocess.run(_docker_cmd() + ["rm", "-f", _child_container],  # nosec B603
                                   capture_output=True, timeout=10)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def handle_stop(self, msg, send_locked):
        """Stop tracking a child relay. Replies via send_locked.

        Child relays run _ws_connect which reconnects forever; we signal them
        to stop by removing them from tracking. The child dies on its next
        reconnect failure or is cleaned up by the OS (daemon thread).
        """
        _stop_id = msg.get("relay_id", "")
        _stop_rid = msg.get("request_id", "")
        self.children.pop(_stop_id, None)
        sys.stderr.write(f"[FSRelay] Stopping child relay: {_stop_id}\n")
        send_locked(json.dumps({"type": "result", "request_id": _stop_rid,
            "data": {"ok": True, "stopped": _stop_id}}).encode("utf-8"))
