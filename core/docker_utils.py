"""Docker utilities — detect and invoke Docker correctly.

On Windows: Docker runs in WSL, invoke via 'wsl docker ...'
On Linux: Docker runs natively, invoke via 'docker ...'

All code that runs Docker should use docker_cmd() to get the correct
command prefix, and translate_path() for volume mounts.

Execution modes:
  local   — no Docker, subprocess only
  docker  — spawn containers via docker.sock (host or DinD)
  sidecar — pre-deployed containers, communicate via network
"""

import os
import platform
import subprocess


def is_windows() -> bool:
    return os.name == "nt"


# ── Execution mode detection ─────────────────────────────────────

_exec_mode_cache = None


def detect_exec_mode() -> str:
    """Auto-detect execution mode: local, docker, or sidecar.

    Override with PAWFLOW_EXEC_MODE env var.
    """
    global _exec_mode_cache
    if _exec_mode_cache is not None:
        return _exec_mode_cache

    explicit = os.environ.get("PAWFLOW_EXEC_MODE", "").strip().lower()
    if explicit in ("local", "docker", "sidecar"):
        _exec_mode_cache = explicit
        return explicit

    # K8s pod
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        _exec_mode_cache = "sidecar"
        return "sidecar"
    # AWS ECS
    if os.environ.get("ECS_CONTAINER_METADATA_URI") or os.environ.get("ECS_CONTAINER_METADATA_URI_V4"):
        _exec_mode_cache = "sidecar"
        return "sidecar"
    # In a container
    in_container = os.path.exists("/.dockerenv") or bool(os.environ.get("PAWFLOW_DOCKER_IMAGE"))
    if in_container:
        if os.path.exists("/var/run/docker.sock"):
            _exec_mode_cache = "docker"  # DinD with socket
            return "docker"
        _exec_mode_cache = "sidecar"  # container without docker.sock
        return "sidecar"
    # Host with Docker
    if docker_available():
        _exec_mode_cache = "docker"
        return "docker"
    _exec_mode_cache = "local"
    return "local"


def to_host_path(container_path: str) -> str:
    """Convert a container path to the equivalent host path for volume mounts.

    In Docker-in-Docker, PawFlow sees /workspace but the Docker daemon
    needs the original host path. Set PAWFLOW_HOST_WORKDIR to enable.

    Example:
      PAWFLOW_HOST_WORKDIR=/home/user/project
      PAWFLOW_WORKDIR=/workspace  (default)
      to_host_path("/workspace/sub") → "/home/user/project/sub"
    """
    host_workdir = os.environ.get("PAWFLOW_HOST_WORKDIR")
    if not host_workdir:
        return container_path  # not in DinD, path is already host
    container_workdir = os.environ.get("PAWFLOW_WORKDIR", "/workspace")
    try:
        rel = os.path.relpath(container_path, container_workdir)
        if rel.startswith(".."):
            return container_path  # outside workspace, can't translate
        if rel == ".":
            return host_workdir
        return os.path.join(host_workdir, rel).replace("\\", "/")
    except ValueError:
        return container_path


def docker_cmd() -> list:
    """Return the command prefix to invoke Docker.

    Windows: ["wsl", "docker"]
    Linux:   ["docker"]
    """
    if is_windows():
        return ["wsl", "docker"]
    return ["docker"]


def translate_path(host_path: str) -> str:
    """Translate a host path to a Docker-compatible path.

    Windows: C:\\Projets\\fssandbox → /mnt/c/Projets/fssandbox
    Linux:   /home/user/project → /home/user/project (unchanged)
    """
    if not is_windows():
        return host_path

    # Convert Windows path to WSL path
    # C:\Projets\foo → /mnt/c/Projets/foo
    path = host_path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        path = f"/mnt/{drive}{path[2:]}"
    return path


def _translate_volume_mount(mount: str) -> str:
    """Translate a -v mount argument from Windows to WSL Docker format.

    Input:  C:\\Projets\\foo:/workspace
    Output: /mnt/c/Projets/foo:/workspace

    Handles Windows drive letters (C:) which conflict with the
    host:container separator (:).
    """
    if not is_windows():
        return mount

    # Detect Windows absolute path (C:\... or C:/...)
    # The mount format is host_path:container_path[:options]
    # Windows path has a drive letter like C:\ which contains ':'
    host_path = mount
    container_path = ""

    # Check for drive letter pattern: X:\... or X:/...
    if len(mount) >= 3 and mount[1] == ":" and mount[2] in ("\\/"):
        # Windows absolute path — find the NEXT ':' after the drive
        next_colon = mount.find(":", 2)
        if next_colon > 0:
            host_path = mount[:next_colon]
            container_path = mount[next_colon + 1:]
        # else: no container path, just a host path
    elif ":" in mount:
        # Unix-style or relative path — split on first ':'
        idx = mount.index(":")
        host_path = mount[:idx]
        container_path = mount[idx + 1:]

    host_path = translate_path(host_path)
    if container_path:
        return f"{host_path}:{container_path}"
    return host_path


def get_host_ip() -> str:
    """Get the host IP address that Docker containers can reach.

    On Windows (Docker WSL): returns the LAN IP (host.docker.internal doesn't work)
    On Linux: returns 'host.docker.internal' (Docker Desktop) or gateway IP
    """
    if is_windows():
        # Docker WSL can't reach host.docker.internal — use LAN IP
        import socket as _sock
        try:
            s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "host.docker.internal"
    return "host.docker.internal"


def docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        cmd = docker_cmd() + ["info"]
        r = subprocess.run(cmd, capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def docker_run(args: list, **kwargs) -> subprocess.CompletedProcess:
    """Run 'docker run' with correct prefix. Translates -v paths on Windows."""
    cmd = docker_cmd() + ["run"]

    # Translate volume mount paths on Windows
    if is_windows():
        translated = []
        i = 0
        while i < len(args):
            if args[i] == "-v" and i + 1 < len(args):
                translated.append("-v")
                translated.append(_translate_volume_mount(args[i + 1]))
                i += 2
            else:
                translated.append(args[i])
                i += 1
        args = translated

    cmd.extend(args)
    return subprocess.run(cmd, **kwargs)


def docker_popen(args: list, **kwargs) -> subprocess.Popen:
    """Popen 'docker run' with correct prefix. Translates -v paths on Windows."""
    cmd = docker_cmd() + ["run"]

    # On Windows: create new process group so Ctrl-C goes to Python, not wsl
    if is_windows() and "creationflags" not in kwargs:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    if is_windows():
        translated = []
        i = 0
        while i < len(args):
            if args[i] == "-v" and i + 1 < len(args):
                translated.append("-v")
                mount = args[i + 1]
                translated.append(_translate_volume_mount(mount))
                i += 2
            else:
                translated.append(args[i])
                i += 1
        args = translated

    cmd.extend(args)
    return subprocess.Popen(cmd, **kwargs)


def docker_exec(container: str, cmd_args: list, **kwargs) -> subprocess.CompletedProcess:
    """Run 'docker exec' with correct prefix."""
    cmd = docker_cmd() + ["exec"] + cmd_args
    return subprocess.run(cmd, **kwargs)


def docker_rm(container: str, force: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Remove a Docker container."""
    cmd = docker_cmd() + ["rm"]
    if force:
        cmd.append("-f")
    cmd.append(container)
    return subprocess.run(cmd, capture_output=True, timeout=10, **kwargs)
