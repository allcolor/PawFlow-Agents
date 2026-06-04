"""Standalone PawFlow relay client configuration and launcher.

PawCode, VS Code and other frontends are PawFlow clients only. Client-side relay
lifecycle is owned by the standalone relay client, which stores server profiles
and local workspace shares, then starts relay processes on demand.
"""

from __future__ import annotations
import logging

from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import json
import os
import re
import signal

from pawflow_relay.utils import api_call, generate_relay_id


_SERVERS_FILE = "servers.json"
_WORKSPACES_FILE = "workspaces.json"
_VALID_MODES = {"rw", "ro"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def relay_home() -> Path:
    """Return the local config directory for the standalone relay client."""
    override = os.environ.get("PAWFLOW_RELAY_HOME")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "PawFlow" / "relay"
    return Path.home() / ".pawflow" / "relay"


def _runtime_dir() -> Path:
    return relay_home() / "runtime"


def _workspace_runtime_lock_path(relay_id: str) -> Path:
    safe_id = _slug(relay_id or "relay")
    return _runtime_dir() / f"{safe_id}.lock"


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return ctypes.get_last_error() == 5
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_runtime_lock(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _remove_workspace_runtime_lock(relay_id: str, *, only_stale: bool = True) -> bool:
    path = _workspace_runtime_lock_path(relay_id)
    if not path.exists():
        return False
    data = _read_runtime_lock(path)
    pid = int(data.get("pid") or 0)
    if only_stale and pid and _process_is_running(pid):
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def _terminate_workspace_runtime_lock(relay_id: str) -> bool:
    """Terminate the process recorded in a workspace runtime lock.

    Desktop Stop is an explicit user stop, not a passive stale-lock cleanup.
    The launcher process may outlive Electron's child handle when it runs via
    wsl.exe, so cleanup must target the PID recorded by the relay manager.
    """
    path = _workspace_runtime_lock_path(relay_id)
    data = _read_runtime_lock(path)
    pid = int(data.get("pid") or 0)
    if not pid or not _process_is_running(pid):
        return False
    kill_sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    for sig in (signal.SIGTERM, kill_sig):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OSError:
            return False
        if sig == signal.SIGTERM:
            import time
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not _process_is_running(pid):
                    return True
                time.sleep(0.1)
    return not _process_is_running(pid)


@contextmanager
def _workspace_runtime_lock(name: str, relay_id: str):
    path = _workspace_runtime_lock_path(relay_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "workspace": name,
        "relay_id": relay_id,
        "created_at": _now(),
    }
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            data = _read_runtime_lock(path)
            pid = int(data.get("pid") or 0)
            if pid and _process_is_running(pid):
                raise RuntimeError(
                    f"Workspace relay '{name}' is already running "
                    f"for relay_id '{relay_id}' (pid {pid})"
                )
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
            fh.write("\n")
        yield
    finally:
        _remove_workspace_runtime_lock(relay_id, only_stale=False)


def _load_json(filename: str) -> Dict[str, Any]:
    path = relay_home() / filename
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid relay config file: {path}")
    return data


def _save_json(filename: str, data: Dict[str, Any]) -> None:
    home = relay_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / filename
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-._")
    return slug or "relay"


@dataclass
class ServerProfile:
    name: str
    url: str
    gateway_key: str = ""
    gateway_cookie: str = ""
    session_token: str = ""
    username: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkspaceShare:
    name: str
    server: str
    path: str
    mode: str = "rw"
    docker_image: str = ""
    allow_exec: bool = True
    allow_remote_desktop: bool = True
    allow_local: bool = False
    relay_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def list_servers() -> List[Dict[str, Any]]:
    return list(_load_json(_SERVERS_FILE).values())


def get_server(name: str) -> Dict[str, Any]:
    servers = _load_json(_SERVERS_FILE)
    if name not in servers:
        raise ValueError(f"Unknown relay server '{name}'")
    return servers[name]


def add_server(name: str, url: str, gateway_key: str = "") -> Dict[str, Any]:
    if not name:
        raise ValueError("Server name is required")
    if not url:
        raise ValueError("Server URL is required")
    servers = _load_json(_SERVERS_FILE)
    now = _now()
    previous = servers.get(name, {})
    profile = ServerProfile(
        name=name,
        url=url.rstrip("/"),
        gateway_key=gateway_key or previous.get("gateway_key", ""),
        gateway_cookie=previous.get("gateway_cookie", ""),
        session_token=previous.get("session_token", ""),
        username=previous.get("username", ""),
        created_at=previous.get("created_at", now),
        updated_at=now,
    ).to_dict()
    servers[name] = profile
    _save_json(_SERVERS_FILE, servers)
    return profile


def update_server_auth(name: str, *, gateway_cookie: str = "",
                       session_token: str = "", username: str = "") -> Dict[str, Any]:
    servers = _load_json(_SERVERS_FILE)
    if name not in servers:
        raise ValueError(f"Unknown relay server '{name}'")
    profile = dict(servers[name])
    if gateway_cookie:
        profile["gateway_cookie"] = gateway_cookie
    if session_token:
        profile["session_token"] = session_token
    if username:
        profile["username"] = username
    profile["updated_at"] = _now()
    servers[name] = profile
    _save_json(_SERVERS_FILE, servers)
    return profile


def delete_server(name: str) -> Dict[str, Any]:
    """Delete a server profile and all workspace shares attached to it."""
    servers = _load_json(_SERVERS_FILE)
    if name not in servers:
        raise ValueError(f"Unknown relay server '{name}'")
    removed = servers.pop(name)
    _save_json(_SERVERS_FILE, servers)

    workspaces = _load_json(_WORKSPACES_FILE)
    removed_workspaces = [
        wname for wname, share in workspaces.items()
        if share.get("server") == name
    ]
    for wname in removed_workspaces:
        workspaces.pop(wname, None)
    _save_json(_WORKSPACES_FILE, workspaces)
    return {"server": removed, "workspaces": removed_workspaces}


def list_workspaces() -> List[Dict[str, Any]]:
    return list(_load_json(_WORKSPACES_FILE).values())


def get_workspace(name: str) -> Dict[str, Any]:
    workspaces = _load_json(_WORKSPACES_FILE)
    if name not in workspaces:
        raise ValueError(f"Unknown relay workspace '{name}'")
    return workspaces[name]


def add_workspace(name: str, server: str, path: str, mode: str = "rw",
                  docker_image: str = "", allow_local: bool = False,
                  allow_exec: bool = True,
                  allow_remote_desktop: bool = True) -> Dict[str, Any]:
    if not name:
        raise ValueError("Workspace name is required")
    get_server(server)
    if mode not in _VALID_MODES:
        raise ValueError("Workspace mode must be 'rw' or 'ro'")
    resolved = str(Path(path).expanduser().resolve())
    workspaces = _load_json(_WORKSPACES_FILE)
    now = _now()
    previous = workspaces.get(name, {})
    username = get_server(server).get("username") or "client"
    relay_id = previous.get("relay_id") or generate_relay_id(username, resolved)
    share = WorkspaceShare(
        name=name,
        server=server,
        path=resolved,
        mode=mode,
        docker_image=docker_image or previous.get("docker_image", ""),
        allow_exec=bool(allow_exec if allow_exec is not None else previous.get("allow_exec", True)),
        allow_remote_desktop=bool(
            allow_remote_desktop if allow_remote_desktop is not None
            else previous.get("allow_remote_desktop", True)),
        allow_local=bool(allow_local),
        relay_id=relay_id,
        created_at=previous.get("created_at", now),
        updated_at=now,
    ).to_dict()
    workspaces[name] = share
    _save_json(_WORKSPACES_FILE, workspaces)
    return share


def delete_workspace(name: str) -> Dict[str, Any]:
    workspaces = _load_json(_WORKSPACES_FILE)
    if name not in workspaces:
        raise ValueError(f"Unknown relay workspace '{name}'")
    removed = workspaces.pop(name)
    _save_json(_WORKSPACES_FILE, workspaces)
    return removed


def stop_workspace_runtime(name: str) -> Dict[str, Any]:
    """Best-effort cleanup for a workspace relay runtime.

    This is used by the desktop app after stopping its launcher process. On
    Windows, Electron can terminate the child process without letting Python run
    `RelayThread.stop()`, so Docker containers must be cleaned independently.
    """
    share = get_workspace(name)
    server = get_server(share["server"])
    relay_id = share.get("relay_id") or generate_relay_id(
        server.get("username") or "client", share["path"])
    service_uninstalled = False
    if server.get("session_token"):
        try:
            api_call(
                server["url"], "POST", "/api/ui",
                body={"action": "service_uninstall", "service_id": relay_id},
                session_token=server.get("session_token", ""),
                gateway_cookie=server.get("gateway_cookie", ""),
            )
            service_uninstalled = True
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    from pawflow_relay.thread import cleanup_relay_containers
    runtime_process_terminated = _terminate_workspace_runtime_lock(relay_id)
    containers_removed = cleanup_relay_containers(relay_id)
    runtime_lock_removed = _remove_workspace_runtime_lock(relay_id, only_stale=False)
    return {
        "workspace": name,
        "relay_id": relay_id,
        "service_uninstalled": service_uninstalled,
        "runtime_process_terminated": runtime_process_terminated,
        "containers_removed": containers_removed,
        "runtime_lock_removed": runtime_lock_removed,
    }


def start_workspace(name: str):
    """Start a configured workspace relay and block until interrupted."""
    from pawflow_relay.thread import RelayThread

    share = get_workspace(name)
    server = get_server(share["server"])
    token = server.get("session_token", "")
    username = server.get("username", "")
    if not token or not username:
        raise ValueError(
            f"Server '{share['server']}' is not logged in. Run: "
            f"pawflow-relay server login {share['server']}"
        )
    relay = RelayThread(
        server["url"], token, username, share["path"],
        relay_id=share.get("relay_id", ""),
        docker_image=share.get("docker_image", "") or "pawflow-relay-dev:latest",
        gateway_cookie=server.get("gateway_cookie", ""),
        gateway_key=server.get("gateway_key", ""),
        allow_exec=bool(share.get("allow_exec", True)),
        allow_remote_desktop=bool(share.get("allow_remote_desktop", True)),
        allow_local=bool(share.get("allow_local", False)),
        read_only=(share.get("mode") == "ro"),
    )
    with _workspace_runtime_lock(name, relay.relay_id):
        previous_handlers = {}

        def _request_stop(_sig, _frame):
            raise KeyboardInterrupt

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, _request_stop)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            relay.start()
            relay.wait()
        except KeyboardInterrupt:
            pass
        finally:
            relay.stop()
            for sig, handler in previous_handlers.items():
                try:
                    signal.signal(sig, handler)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return relay
