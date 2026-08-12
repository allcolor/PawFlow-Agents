"""Local service catalogue and FRP client lifecycle for PawFlow relays."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404
import threading
from pathlib import Path
from typing import Any, Dict, List

from pawflow_relay.manager import relay_home


_CATALOG_FILE = "service_catalogs.json"
_VALID_ID = re.compile(r"^[a-zA-Z0-9_.-]{1,96}$")
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_LOCK = threading.RLock()
_PROCESSES: Dict[str, Dict[str, Any]] = {}


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _identifier(value: Any, field: str) -> str:
    text = _required(value, field)
    if not _VALID_ID.fullmatch(text):
        raise ValueError(f"{field} contains unsupported characters")
    return text


def _port(value: Any, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a TCP port") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{field} must be between 1 and 65535")
    return port


def _catalog_path() -> Path:
    return relay_home() / _CATALOG_FILE


def _load_catalogs() -> Dict[str, Dict[str, Dict[str, Any]]]:
    path = _catalog_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid service catalogue file: {path}")
    return data


def _save_catalogs(data: Dict[str, Any]) -> None:
    path = _catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def save_service(relay_id: str, service: Dict[str, Any]) -> Dict[str, Any]:
    """Create or replace one locally approved TCP service."""
    relay_id = _identifier(relay_id, "relay_id")
    service_id = _identifier(service.get("service_id"), "service_id")
    protocol = str(service.get("protocol") or "tcp").strip().lower()
    if protocol != "tcp":
        raise ValueError("Only TCP services are supported")
    entry = {
        "service_id": service_id,
        "name": _required(service.get("name"), "name"),
        "protocol": protocol,
        "target_host": _required(service.get("target_host"), "target_host"),
        "target_port": _port(service.get("target_port"), "target_port"),
    }
    with _LOCK:
        catalogs = _load_catalogs()
        catalogs.setdefault(relay_id, {})[service_id] = entry
        _save_catalogs(catalogs)
    return dict(entry)


def list_services(relay_id: str) -> List[Dict[str, Any]]:
    relay_id = _identifier(relay_id, "relay_id")
    with _LOCK:
        entries = _load_catalogs().get(relay_id, {})
    return [dict(entries[key]) for key in sorted(entries)]


def get_service(relay_id: str, service_id: str) -> Dict[str, Any]:
    relay_id = _identifier(relay_id, "relay_id")
    service_id = _identifier(service_id, "service_id")
    with _LOCK:
        entry = _load_catalogs().get(relay_id, {}).get(service_id)
    if entry is None:
        raise KeyError(f"Service '{service_id}' is not approved on relay '{relay_id}'")
    return dict(entry)


def delete_service(relay_id: str, service_id: str) -> bool:
    relay_id = _identifier(relay_id, "relay_id")
    service_id = _identifier(service_id, "service_id")
    with _LOCK:
        catalogs = _load_catalogs()
        relay_catalog = catalogs.get(relay_id, {})
        existed = relay_catalog.pop(service_id, None) is not None
        if not relay_catalog:
            catalogs.pop(relay_id, None)
        if existed:
            _save_catalogs(catalogs)
    return existed


def _toml_string(value: Any) -> str:
    return json.dumps(_required(value, "TOML value"), ensure_ascii=False)


def build_frpc_config(message: Dict[str, Any]) -> str:
    """Build a minimal, secret STCP service or visitor configuration."""
    role = str(message.get("role") or "").strip()
    if role not in {"service", "access"}:
        raise ValueError("role must be 'service' or 'access'")
    tunnel_id = _identifier(message.get("tunnel_id"), "tunnel_id")
    relay_id = _identifier(message.get("relay_id"), "relay_id")
    server_name = _identifier(message.get("server_name"), "server_name")
    server_addr = _required(message.get("frps_server"), "frps_server")
    server_port = _port(message.get("frps_port"), "frps_port")
    auth_token = _required(message.get("frps_token"), "frps_token")
    grant = _required(message.get("grant"), "grant")
    secret_key = _required(message.get("secret_key"), "secret_key")
    transport = str(message.get("transport") or "quic").strip().lower()
    if transport not in {"tcp", "quic"}:
        raise ValueError("transport must be 'tcp' or 'quic'")

    lines = [
        f"clientID = {_toml_string('pft_' + tunnel_id + '_' + role)}",
        f"serverAddr = {_toml_string(server_addr)}",
        f"serverPort = {server_port}",
        "loginFailExit = false",
        'auth.method = "token"',
        f"auth.token = {_toml_string(auth_token)}",
        f"transport.protocol = {_toml_string(transport)}",
        "transport.tls.enable = true",
        f"metadatas.pawflow_grant = {_toml_string(grant)}",
        f"metadatas.pawflow_relay_id = {_toml_string(relay_id)}",
        "",
    ]
    if role == "service":
        service = get_service(relay_id, message.get("service_id"))
        lines.extend([
            "[[proxies]]",
            f"name = {_toml_string(server_name)}",
            'type = "stcp"',
            f"localIP = {_toml_string(service['target_host'])}",
            f"localPort = {service['target_port']}",
            f"secretKey = {_toml_string(secret_key)}",
            f"metadatas.pawflow_grant = {_toml_string(grant)}",
        ])
    else:
        bind_host = str(message.get("bind_host") or "127.0.0.1").strip().lower()
        if bind_host not in _LOOPBACK:
            raise ValueError("bind_host must be loopback-only")
        lines.extend([
            "[[visitors]]",
            f"name = {_toml_string(server_name + '_visitor')}",
            'type = "stcp"',
            f"serverName = {_toml_string(server_name)}",
            f"secretKey = {_toml_string(secret_key)}",
            f"bindAddr = {_toml_string(bind_host)}",
            f"bindPort = {_port(message.get('bind_port'), 'bind_port')}",
        ])
    return "\n".join(lines) + "\n"


def _frpc_binary() -> str:
    configured = os.environ.get("PAWFLOW_FRPC_BIN", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        raise FileNotFoundError(f"PAWFLOW_FRPC_BIN does not exist: {path}")
    executable = "frpc.exe" if os.name == "nt" else "frpc"
    packaged = Path(__file__).resolve().parent.parent / "bin" / executable
    if packaged.is_file():
        return str(packaged)
    found = shutil.which("frpc")
    if found:
        return found
    raise FileNotFoundError("frpc is not installed in the PawFlow Relay runtime")


def _runtime_dir() -> Path:
    path = relay_home() / "service-tunnels"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stop_locked(tunnel_id: str, role: str) -> bool:
    key = f"{tunnel_id}:{role}"
    state = _PROCESSES.pop(key, None)
    if not state:
        return False
    process = state["process"]
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return True


def apply_tunnel(message: Dict[str, Any]) -> Dict[str, Any]:
    config = build_frpc_config(message)
    tunnel_id = _identifier(message.get("tunnel_id"), "tunnel_id")
    role = str(message.get("role") or "")
    key = f"{tunnel_id}:{role}"
    digest = hashlib.sha256(config.encode("utf-8")).hexdigest()
    with _LOCK:
        current = _PROCESSES.get(key)
        if current and current["process"].poll() is None and current["digest"] == digest:
            return {"running": True, "already_running": True, "role": role}
        if current:
            _stop_locked(tunnel_id, role)
        runtime = _runtime_dir()
        config_path = runtime / f"{tunnel_id}-{role}.toml"
        log_path = runtime / f"{tunnel_id}-{role}.log"
        config_path.write_text(config, encoding="utf-8")
        try:
            config_path.chmod(0o600)
        except OSError:
            pass
        log_handle = open(log_path, "ab", buffering=0)  # noqa: SIM115
        try:
            process = subprocess.Popen(  # nosec B603
                [_frpc_binary(), "-c", str(config_path)],
                stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT)
        except Exception:
            log_handle.close()
            raise
        _PROCESSES[key] = {
            "process": process, "digest": digest, "config_path": str(config_path),
            "log_path": str(log_path), "log_handle": log_handle,
        }
    return {"running": True, "already_running": False, "role": role}


def stop_tunnel(tunnel_id: str, role: str) -> Dict[str, Any]:
    tunnel_id = _identifier(tunnel_id, "tunnel_id")
    if role not in {"service", "access"}:
        raise ValueError("role must be 'service' or 'access'")
    with _LOCK:
        stopped = _stop_locked(tunnel_id, role)
    return {"running": False, "stopped": stopped, "role": role}


def tunnel_status(tunnel_id: str, role: str) -> Dict[str, Any]:
    tunnel_id = _identifier(tunnel_id, "tunnel_id")
    if role not in {"service", "access"}:
        raise ValueError("role must be 'service' or 'access'")
    with _LOCK:
        state = _PROCESSES.get(f"{tunnel_id}:{role}")
        running = bool(state and state["process"].poll() is None)
    return {"running": running, "role": role}


def handle_action(action: str, message: Dict[str, Any]) -> Dict[str, Any]:
    if action == "service_tunnel_catalog":
        return {"services": list_services(message.get("relay_id"))}
    if action == "service_tunnel_catalog_save":
        return {"service": save_service(message.get("relay_id"), message.get("service") or {})}
    if action == "service_tunnel_catalog_delete":
        return {"deleted": delete_service(
            message.get("relay_id"), message.get("service_id"))}
    if action == "service_tunnel_apply":
        return apply_tunnel(message)
    if action == "service_tunnel_stop":
        return stop_tunnel(message.get("tunnel_id"), message.get("role"))
    if action == "service_tunnel_status":
        return tunnel_status(message.get("tunnel_id"), message.get("role"))
    raise ValueError(f"Unknown service tunnel action: {action}")
