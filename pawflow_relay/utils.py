"""Shared utilities for the PawFlow relay."""

import hashlib
import json
import os
import socket
from pathlib import Path


def docker_cmd():
    """Return the docker command prefix (handles Windows WSL)."""
    if os.name == "nt":
        return ["wsl", "docker"]
    return ["docker"]


def translate_path(p):
    """Translate a Windows path to WSL/Docker mount path."""
    if os.name != "nt":
        return p
    p = p.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:]}"
    return p


def to_host_path(container_path):
    """Translate container path to host path for DinD volume mounts."""
    host_workdir = os.environ.get("PAWFLOW_HOST_WORKDIR")
    if not host_workdir:
        return container_path
    container_workdir = os.environ.get("PAWFLOW_WORKDIR", "/workspace")
    try:
        rel = os.path.relpath(container_path, container_workdir)
        if rel.startswith(".."):
            return container_path
        if rel == ".":
            return host_workdir
        return os.path.join(host_workdir, rel).replace("\\", "/")
    except ValueError:
        return container_path


def get_host_ip():
    """Get IP reachable from Docker containers."""
    if os.name == "nt":
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            pass
    return "host.docker.internal"


def find_free_port() -> int:
    """Find and return a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def generate_relay_id(username: str, directory: str) -> str:
    """Generate a stable relay ID from username + directory.

    Format: fs_{username}_{sha256(username:normalized_dir)[:8]}
    Consistent across PawCode CLI, VSCode extension, and standalone relay.
    """
    normalized = str(Path(directory).resolve())
    h = hashlib.sha256(f"{username}:{normalized}".encode()).hexdigest()[:8]
    return f"fs_{username}_{h}"


def api_call(server_url, method, path, body=None, session_token="",
             gateway_cookie="", on_token_refresh=None):
    """HTTP request to PawFlow agent API (stdlib only).

    Args:
        on_token_refresh: Optional callback(new_token) for transparent token refresh.
    """
    import http.client
    from urllib.parse import urlparse

    parsed = urlparse(server_url)
    use_ssl = parsed.scheme == "https"
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if use_ssl else 80)

    if use_ssl:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=30)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=30)

    headers = {"Content-Type": "application/json"}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    if gateway_cookie:
        headers["Cookie"] = f"_pf_gw={gateway_cookie}"

    payload = json.dumps(body).encode("utf-8") if body else None
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()

    # Follow 301/302 redirects (HTTP→HTTPS)
    if resp.status == 301:
        resp.read()  # drain
        conn.close()
        location = resp.getheader("Location", "")
        if location:
            from urllib.parse import urlparse as _urlparse
            _rp = _urlparse(location)
            _rssl = _rp.scheme == "https"
            _rhost = _rp.hostname or host
            _rport = _rp.port or (443 if _rssl else 80)
            if _rssl:
                import ssl as _ssl2
                _ctx2 = _ssl2.create_default_context()
                _ctx2.check_hostname = False
                _ctx2.verify_mode = _ssl2.CERT_NONE
                conn = http.client.HTTPSConnection(_rhost, _rport, context=_ctx2, timeout=30)
            else:
                conn = http.client.HTTPConnection(_rhost, _rport, timeout=30)
            conn.request(method, _rp.path or path, body=payload, headers=headers)
            resp = conn.getresponse()

    data = resp.read().decode("utf-8")
    # Transparent token refresh: server sends new token in header
    new_token = resp.getheader("X-Session-Token")
    if new_token and on_token_refresh:
        on_token_refresh(new_token)
    conn.close()

    if resp.status >= 400:
        raise Exception(f"API {method} {path} -> {resp.status}: {data}")
    return json.loads(data) if data else {}
