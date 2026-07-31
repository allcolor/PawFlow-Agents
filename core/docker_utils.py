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
import logging

import os
import subprocess  # nosec B404

logger = logging.getLogger(__name__)


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

    In Docker-in-Docker, PawFlow sees container paths but the Docker daemon
    needs original host paths. Set PAWFLOW_HOST_APP_DIR / PAWFLOW_APP_DIR for
    PawFlow's own application tree and PAWFLOW_HOST_WORKDIR for user workspaces.
    Runtime data mounted at PAWFLOW_DATA_DIR is translated with
    PAWFLOW_HOST_DATA_DIR so child CLI containers see the same session files
    the PawFlow server just wrote.

    Example:
      PAWFLOW_HOST_WORKDIR=/home/user/project
      PAWFLOW_WORKDIR=/workspace  (default)
      to_host_path("/workspace/sub") → "/home/user/project/sub"
    """
    def _translate_with_root(container_root: str, host_root: str) -> str:
        try:
            rel = os.path.relpath(container_path, container_root)
            if rel.startswith(".."):
                return ""
            if rel == ".":
                return host_root
            return os.path.join(host_root, rel).replace("\\", "/")
        except ValueError:
            return ""

    host_data_dir = os.environ.get("PAWFLOW_HOST_DATA_DIR", "").strip()
    if host_data_dir:
        data_dir = os.environ.get("PAWFLOW_DATA_DIR", "/app/data")
        translated = _translate_with_root(data_dir, host_data_dir)
        if translated:
            return translated

    host_app_dir = os.environ.get("PAWFLOW_HOST_APP_DIR", "").strip()
    if host_app_dir:
        app_dir = os.environ.get("PAWFLOW_APP_DIR", "/app")
        translated = _translate_with_root(app_dir, host_app_dir)
        if translated:
            return translated

    host_workdir = os.environ.get("PAWFLOW_HOST_WORKDIR")
    if not host_workdir:
        return container_path  # not in DinD, path is already host
    container_workdir = os.environ.get("PAWFLOW_WORKDIR", "/workspace")
    return _translate_with_root(container_workdir, host_workdir) or container_path


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

    Handles:
      - Drive letters:   C:\\foo\\bar           → /mnt/c/foo/bar
      - WSL UNC paths:   \\\\wsl$\\<distro>\\x  → /x
                         \\\\wsl.localhost\\<distro>\\x → /x

    PawFlow runs Docker via `wsl docker`, so the daemon sees Linux-native
    paths only. Passing `//wsl$/Ubuntu-24.04/…` as a -v source makes
    Docker silently bind-mount an empty directory (that literal path
    doesn't exist inside the WSL distro), which breaks dev-mounts of
    the MCP bridge / relay scripts — the container ends up with no
    /opt/pawflow/mcp_bridge.py and Claude replies tool-less.
    """
    if not is_windows():
        return host_path

    # Convert Windows path to WSL path
    path = host_path.replace("\\", "/")
    lower = path.lower()
    for prefix in ("//wsl$/", "//wsl.localhost/"):
        if lower.startswith(prefix):
            rest = path[len(prefix):]
            _, _, rel = rest.partition("/")
            return "/" + rel
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
        r = subprocess.run(cmd, capture_output=True, timeout=10)  # nosec B603
        return r.returncode == 0
    except Exception:
        return False


def docker_run(args: list, **kwargs) -> subprocess.CompletedProcess:
    """Run 'docker run' with correct prefix. Translates -v paths on Windows."""
    cmd = docker_cmd() + ["run"]
    args = _with_docker_init(args)

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
    return subprocess.run(cmd, **kwargs)  # nosec B603


def docker_popen(args: list, **kwargs) -> subprocess.Popen:
    """Popen 'docker run' with correct prefix. Translates -v paths on Windows."""
    cmd = docker_cmd() + ["run"]
    args = _with_docker_init(args)

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
    return subprocess.Popen(cmd, **kwargs)  # nosec B603


def _with_docker_init(args: list) -> list:
    """Ensure Docker reaps orphaned grandchildren inside launched containers."""
    if "--init" in args:
        return list(args)
    return ["--init", *args]


PAWFLOW_SPAWNED_LABEL = "org.pawflow.spawned"
PAWFLOW_SERVER_LABEL = "org.pawflow.server-id"


def pawflow_container_labels(kind: str = "") -> list:
    """`--label` argv marking a container as spawned by THIS server.

    Nothing PawFlow starts may outlive it, and the shutdown reaper used to
    match container NAMES: every new family of container had to remember to
    add its prefix there, and two of them (interactive Claude Code, the
    Antigravity observer) never did -- they survived `docker stop` and had to
    be cleaned up by hand. A label is declared once, at the only place that
    can know: the spawn.

    The server id keeps it precise when several PawFlow servers share a Docker
    host: a shutdown reaps its own containers and nobody else's.
    """
    labels = ["--label", f"{PAWFLOW_SPAWNED_LABEL}=1",
              "--label", f"{PAWFLOW_SERVER_LABEL}={get_server_id()}"]
    if kind:
        labels += ["--label", f"org.pawflow.kind={kind}"]
    return labels


def docker_exec(container: str, cmd_args: list, **kwargs) -> subprocess.CompletedProcess:
    """Run 'docker exec' with correct prefix."""
    cmd = docker_cmd() + ["exec"] + cmd_args
    return subprocess.run(cmd, **kwargs)  # nosec B603


def _confirmed_removals(requested: list, result) -> list:
    """The subset of ``requested`` that ``docker rm`` actually removed.

    A refusal is a non-zero exit and a line on stderr, NOT an exception, so
    the caller cannot learn anything from the absence of a raise. Counting the
    request as the result is worse than a miscount: the reaper then reports a
    clean sweep while the container is still up, still holding a credential
    slot and still writing to a workdir the new process believes it owns.

    ``docker rm`` echoes what it removed, one identifier per line, so the
    output is the receipt. It echoes back whatever form it was given, and a
    partial failure still removes the others -- hence a set intersection
    rather than a returncode test.
    """
    echoed = {line.strip() for line in (getattr(result, "stdout", "") or "").splitlines()
             if line.strip()}
    return [cid for cid in requested if cid in echoed]


def reap_spawned_containers(log=None) -> int:
    """Remove every container this server spawned. Returns the count.

    Called at BOTH ends of the process, and it has to be the same code at
    both. On shutdown it is what keeps the promise that nothing PawFlow
    starts outlives it. On startup it is what makes that promise true even
    when the shutdown never ran -- a SIGKILL, a crash, an update whose stop
    grace expired. A container still up at boot is a zombie by definition:
    the pools track sessions in memory only, so nothing will ever adopt it,
    sweep it or reap it, while it holds a credential slot and keeps writing
    to a session workdir the new process believes it owns.

    The label is the ONLY thing that decides, and it is stamped at the single
    place that can know -- the spawn, via `pawflow_container_labels`. Matching
    names instead was tried and removed: most families are named `pf-cc-pool-*`
    or `pawflow-relay-srv-*`, carrying no server id at all, so on a shared
    Docker daemon a name match also selects ANOTHER live PawFlow server's
    pools, relays and logins. An unlabelled container is not evidence of an
    older build to clean up -- it is evidence of something this server did not
    start, and killing it breaks the one invariant that matters here: a server
    reaps its own containers and nobody else's.
    """
    _log = log or logger.info
    own = get_server_id()
    try:
        result = subprocess.run(  # nosec B603
            docker_cmd() + ["ps", "-a", "-q", "--filter",
                            f"label={PAWFLOW_SERVER_LABEL}={own}"],
            capture_output=True, text=True, timeout=5)
        ids = [i for i in (result.stdout or "").split() if i]
        if not ids:
            return 0
        rm = subprocess.run(docker_cmd() + ["rm", "-f"] + ids,  # nosec B603
                            capture_output=True, text=True, timeout=20)
        removed = _confirmed_removals(ids, rm)
        if len(removed) != len(ids):
            logger.warning(
                "Reaper could not remove %d of %d container(s): %s",
                len(ids) - len(removed), len(ids),
                (rm.stderr or "").strip() or "docker gave no reason")
        if removed:
            _log("Reaped %d container(s) spawned by this server", len(removed))
        return len(removed)
    except Exception:
        # Docker unreachable must never stop a boot.
        logger.debug("container reap failed", exc_info=True)
        return 0


def docker_rm(container: str, force: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Remove a Docker container."""
    cmd = docker_cmd() + ["rm"]
    if force:
        cmd.append("-f")
    cmd.append(container)
    return subprocess.run(cmd, capture_output=True, timeout=10, **kwargs)  # nosec B603


# ── Server/Relay identity for container naming ────────────────────

_server_id_cache = None


def get_server_id() -> str:
    """Get persistent server ID (generated once, stored in data/server_id)."""
    global _server_id_cache
    if _server_id_cache:
        return _server_id_cache
    import hashlib, uuid
    from pathlib import Path
    from core.paths import SERVER_ID_FILE; path = SERVER_ID_FILE
    if path.exists():
        _server_id_cache = path.read_text().strip()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _server_id_cache = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:12]
        path.write_text(_server_id_cache)
    return _server_id_cache


def make_container_name(owner_id: str, purpose: str) -> str:
    """Generate a Docker container name with owner prefix.

    owner_id: server_id (for server containers) or relay_id (for client containers)
    purpose:  'cc' (claude code), 'relay', 'child', etc.

    Format: pf-{owner_id[:12]}-{purpose}-{uuid[:8]}
    """
    import uuid as _uuid
    safe_owner = owner_id[:12].replace(".", "-").replace("_", "-")
    return f"pf-{safe_owner}-{purpose}-{_uuid.uuid4().hex[:8]}"


def list_containers(owner_id: str = "") -> list:
    """List Docker containers matching owner prefix.

    Returns list of {"id": str, "name": str, "status": str, "image": str}.
    If owner_id is empty, lists ALL pf-* containers.
    """
    prefix = f"pf-{owner_id[:12].replace('.', '-').replace('_', '-')}" if owner_id else "pf-"
    try:
        result = subprocess.run(  # nosec B603
            docker_cmd() + ["ps", "-a", "--filter", f"name={prefix}",
                            "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}"],
            capture_output=True, text=True, timeout=10)
        containers = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                containers.append({
                    "id": parts[0], "name": parts[1],
                    "status": parts[2], "image": parts[3],
                })
        return containers
    except Exception:
        return []


def kill_containers(owner_id: str) -> int:
    """Kill all Docker containers matching owner prefix. Returns count killed."""
    containers = list_containers(owner_id)
    killed = 0
    for c in containers:
        try:
            subprocess.run(docker_cmd() + ["rm", "-f", c["id"]],  # nosec B603
                           capture_output=True, timeout=10)
            killed += 1
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return killed
