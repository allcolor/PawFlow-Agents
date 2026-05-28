"""Claude Code container pool manager.

One container per Claude Code exec (1:1). The pool keeps a small set of
pre-warmed idle containers (`_ready`) so an acquire doesn't pay the
~1-3s docker-run cost on the hot path, and tracks currently-running
ones (`_active`). `release()` destroys the container with `docker rm -f`
— it does NOT return the container to the ready pool, because kernel
isolation is the whole point: sharing a container across execs lets a
kill-cascade take out siblings (seen when a compact / memory_extract
crashed its MCP bridge and main CC died with it, even with per-exec
setsid PGIDs).

Architecture:
  _ready[name] → ContainerInfo   (idle, `sleep infinity`, awaiting exec)
  _active[name] → ContainerInfo  (running a CC exec)

  acquire(): prefer _ready, else spawn fresh; trigger async top-up
  release(): docker rm -f; trigger async top-up
  kill cascade is killed BY the kernel: separate PID namespaces per
    container mean a buggy exec can only tear down its own container.
"""

import logging
import os
import subprocess  # nosec B404
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.docker_utils import docker_cmd, to_host_path, get_host_ip

logger = logging.getLogger(__name__)


@dataclass
class _ContainerInfo:
    """State of a pool container.

    `active_sessions` is either 0 (in `_ready`) or 1 (in `_active`) under
    the 1:1 model — kept as an int for bookkeeping symmetry with the
    readiness check and `last_used` touches.
    """
    name: str
    active_sessions: int = 0
    max_sessions: int = 1
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)

    @property
    def has_capacity(self) -> bool:
        return self.active_sessions < self.max_sessions


class ClaudeCodePool:
    """Singleton pool of one-CC-per-container Docker containers.

    Usage:
        pool = ClaudeCodePool.instance()
        container = pool.acquire()         # new (or pre-warmed) container
        proc = pool.exec_claude(container, session_dir, claude_args)
        # ... stream proc.stdout ...
        pool.release(container)            # docker rm -f (not returned to pool)
    """

    _instance: Optional['ClaudeCodePool'] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> 'ClaudeCodePool':
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance._register_death_handlers()
        return cls._instance

    def _register_death_handlers(self):
        """Kill every spawned container when the Python process dies.

        Pool containers are `docker run -d --rm sleep infinity`. Without
        --rm they leak on crash; with --rm they auto-remove on EXIT, but
        they NEVER exit on their own because of `sleep infinity`. The
        server/CLI shutting down doesn't signal them.

        Register atexit + SIGINT/SIGTERM handlers that issue
        `docker rm -f` on every tracked container. Combined with the
        boot-time _cleanup_orphans reaper, no container can survive the
        parent process.
        """
        import atexit, signal, sys
        def _kill_all(*_args, **_kwargs):
            if getattr(self, "_shutdown_once", False):
                return
            self._shutdown_once = True
            # Kill live CC sessions FIRST so revoke_token has a chance
            # to run before the containers are nuked — once docker rm -f
            # fires, the proc is gone and the MCP bridge with it.
            try:
                from core.cc_live_registry import LiveSessionRegistry
                LiveSessionRegistry.instance().shutdown_all()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            try:
                self.shutdown()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        atexit.register(_kill_all)
        # Only install signal handlers in the main thread — subprocesses /
        # worker threads can't register them and will raise ValueError.
        try:
            import threading as _th
            if _th.current_thread() is _th.main_thread():
                for _sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        _prev = signal.getsignal(_sig)
                        def _chain(signum, frame, _p=_prev):
                            _kill_all()
                            # Chain to previous handler if it was a callable
                            if callable(_p) and _p not in (signal.SIG_DFL, signal.SIG_IGN):
                                _p(signum, frame)
                            else:
                                # Default: re-raise as exit
                                sys.exit(128 + signum)
                        signal.signal(_sig, _chain)
                    except (ValueError, OSError):
                        pass
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def __init__(self):
        # Containers currently running a CC exec. name → info.
        self._active: Dict[str, _ContainerInfo] = {}
        # Pre-warmed idle containers. Drawn by acquire() in FIFO order.
        self._ready: Dict[str, _ContainerInfo] = {}
        self._lock = threading.Lock()
        self._reaper_started = False
        self._shutdown_once = False

        # Config (can be overridden via env vars). Defaults were retuned
        # for the 1:1 model: cap bumped from 10 → 50 so concurrency isn't
        # artificially capped now that each exec gets its own container.
        self.image = os.environ.get(
            "PAWFLOW_CC_IMAGE", "pawflow-claude-code:latest")
        self.max_containers = int(os.environ.get(
            "PAWFLOW_CC_POOL_MAX", "50"))
        # Ready-pool target size. Kept at 0 by default so nobody
        # accidentally burns RAM holding idle Node runtimes — opt-in
        # when spawn latency matters (e.g. cold-start sensitive convs).
        self.prewarm_count = int(os.environ.get(
            "PAWFLOW_CC_POOL_PREWARM", "0"))
        # Ready container TTL: idle pre-warmed containers get reaped
        # this many seconds after their last touch. Covers the case
        # where `prewarm_count` is reduced at runtime and the excess
        # stays around otherwise.
        self.idle_timeout = int(os.environ.get(
            "PAWFLOW_CC_POOL_IDLE", "300"))
        self.cpu_limit = os.environ.get("PAWFLOW_CC_CPU", "2")
        self.memory_limit = os.environ.get("PAWFLOW_CC_MEM", "4g")

        # Kill orphan pool containers from previous PawFlow runs
        self._cleanup_orphans()

        # Sessions volume: host path for CC sessions (must be absolute for Docker -v)
        import core.paths as _paths
        _raw_path = str(_paths.CLAUDE_SESSIONS_DIR.resolve())
        # translate_path converts Windows paths (C:\...) to WSL mount paths (/mnt/c/...)
        from pawflow_relay.utils import translate_path
        self._sessions_host_path = translate_path(to_host_path(_raw_path))

    # ── Public API ─────────────────────────────────────────────────

    def acquire(self, workspace_mount_args: Optional[List[str]] = None) -> str:
        """Acquire a container for a new CC exec. Returns container name.

        Prefers a pre-warmed container from `_ready`. Falls back to a
        fresh spawn (paying the ~1-3s docker-run cost) when the ready
        pool is empty. Triggers an async top-up so the NEXT acquire can
        draw from the ready pool even if this one couldn't.

        Raises RuntimeError if the pool is at its hard cap
        (`PAWFLOW_CC_POOL_MAX`).
        """
        workspace_mount_args = list(workspace_mount_args or [])
        spawn_needed = False
        name = None
        with self._lock:
            self._ensure_reaper()

            if not workspace_mount_args and self._ready:
                # FIFO: take the oldest ready container.
                name, info = next(iter(self._ready.items()))
                del self._ready[name]
                info.active_sessions = 1
                info.last_used = time.monotonic()
                self._active[name] = info
                logger.info(
                    "Pool acquire [ready]: %s (active=%d, ready=%d)",
                    name, len(self._active), len(self._ready))
            else:
                # Check cap BEFORE releasing lock to spawn.
                if len(self._active) + len(self._ready) >= self.max_containers:
                    raise RuntimeError(
                        f"Claude Code pool exhausted: active={len(self._active)} "
                        f"+ ready={len(self._ready)} == max={self.max_containers}")
                spawn_needed = True

        if spawn_needed:
            # Spawn OUTSIDE the lock — docker run takes ~1-3s and must
            # not block concurrent acquires/releases.
            fresh = self._spawn_container(workspace_mount_args=workspace_mount_args)
            with self._lock:
                # Re-check cap: a concurrent acquire may have bumped us.
                if (len(self._active) + len(self._ready)
                        >= self.max_containers):
                    self._kill_container(fresh)
                    raise RuntimeError(
                        f"Claude Code pool exhausted during race: "
                        f"{len(self._active)}/{self.max_containers}")
                info = _ContainerInfo(
                    name=fresh, active_sessions=1, max_sessions=1,
                    last_used=time.monotonic())
                self._active[fresh] = info
                name = fresh
            logger.info(
                "Pool acquire [spawned]: %s (active=%d, ready=%d)",
                name, len(self._active), len(self._ready))

        # Fire-and-forget top-up — keeps `_ready` at `prewarm_count`.
        self._trigger_topup()
        return name

    def release(self, container_name: str):
        """Destroy the container backing a CC exec (`docker rm -f`).

        Under the 1:1 model, the container's lifetime == the CC exec's
        lifetime. Releasing returns it to the kernel, not to the ready
        pool — a fresh container is spawned on demand (or already warm
        via top-up) for the next acquire. This is what kills the
        cross-exec kill-cascade: the destroyed PID namespace cannot
        leak SIGKILL to another CC.
        """
        with self._lock:
            info = self._active.pop(container_name, None)
            if info is None:
                # Already released, not tracked because live registry replaced
                # it, or pool bookkeeping was cleared during shutdown. Still
                # issue docker rm -f: release() is the ownership boundary.
                if container_name in self._ready:
                    del self._ready[container_name]
                    logger.warning(
                        "Pool release: %s was in ready pool, killing anyway",
                        container_name)
                else:
                    if not self._is_container_alive(container_name):
                        logger.warning(
                            "Pool release: unknown container %s; not running",
                            container_name)
                        return
                    logger.warning(
                        "Pool release: unknown container %s; killing defensively",
                        container_name)

        # Kill outside the lock — docker rm -f can take a second.
        self._kill_container(container_name)
        logger.info("Pool release [killed]: %s (active=%d, ready=%d)",
                    container_name, len(self._active), len(self._ready))
        # Top-up to maintain prewarm_count.
        self._trigger_topup()

    def _trigger_topup(self) -> None:
        """Schedule an async top-up of the ready pool to `prewarm_count`.

        Fire-and-forget — callers don't wait. Multiple concurrent
        triggers are safe: each re-checks headroom under the lock before
        spawning, so over-spawn is bounded by `max_containers`.
        """
        if self.prewarm_count <= 0:
            return
        threading.Thread(
            target=self._topup_ready, daemon=True,
            name="cc-pool-topup").start()

    def _topup_ready(self) -> None:
        """Spawn ready containers until `prewarm_count` is met.

        Runs in its own thread. Each iteration re-reads `_ready` /
        `_active` under the lock so a parallel acquire / release is
        observed. Aborts at the `max_containers` cap.
        """
        while True:
            with self._lock:
                need = self.prewarm_count - len(self._ready)
                room = self.max_containers - (
                    len(self._active) + len(self._ready))
                if need <= 0 or room <= 0:
                    return
            # Spawn one at a time, outside the lock.
            try:
                name = self._spawn_container()
            except Exception as e:
                logger.warning("Pool top-up: spawn failed: %s", e)
                return
            with self._lock:
                if (len(self._active) + len(self._ready)
                        >= self.max_containers):
                    # Race: another acquire took the slot we just
                    # created room for. Discard this container.
                    self._kill_container(name)
                    return
                self._ready[name] = _ContainerInfo(
                    name=name, active_sessions=0, max_sessions=1,
                    last_used=time.monotonic())
                logger.info(
                    "Pool top-up [spawned ready]: %s (ready=%d/%d)",
                    name, len(self._ready), self.prewarm_count)

    def exec_claude(self, container_name: str, session_dir: str,
                    claude_args: list, extra_env: dict = None,
                    **popen_kwargs) -> subprocess.Popen:
        """Start a claude process inside a pool container.

        Args:
            container_name: container from acquire()
            session_dir: path INSIDE the container for CLAUDE_CONFIG_DIR
                         (e.g. /cc_sessions/<user>/<conv>/<agent>)
            claude_args: args for the claude CLI (after 'claude')
            extra_env: additional env vars (e.g. ANTHROPIC_API_KEY)
            **popen_kwargs: extra args for subprocess.Popen

        Returns:
            subprocess.Popen with stdin/stdout/stderr
        """
        host_ip = get_host_ip()

        # Per-exec mount namespace via `unshare -m`: each docker exec gets
        # its own private view of the filesystem. We bind the user's slot
        # `/cc_sessions/<user>` over `/cc_sessions` so CC sees its conv
        # tree at `/cc_sessions/<conv>/<agent>` — the SAME canonical path
        # the user's relay exposes via its server-fs FUSE mount. No path
        # translation needed downstream.
        #
        # Cross-user iso: the bind hides every other user's subtree from
        # this exec's namespace. Cross-conv (same user) is intentionally
        # visible — the relay shows the same.
        #
        # The wrapper runs as root (needed for mount + unshare -m with
        # CAP_SYS_ADMIN) then drops privileges to uid 1000 via setpriv
        # before exec'ing claude — Claude Code refuses to run as root.
        #
        # session_dir layout: /cc_sessions/<user>/<conv>/<agent>
        # After the bind:     /cc_sessions/<conv>/<agent>
        _sd_parts = session_dir.lstrip("/").split("/")
        if len(_sd_parts) < 3 or _sd_parts[0] != "cc_sessions":
            raise ValueError(
                f"session_dir must look like /cc_sessions/<user>/<conv>/...; "
                f"got {session_dir!r}")
        _user_slot = "/cc_sessions/" + _sd_parts[1]
        _ns_workdir = "/" + "/".join(_sd_parts[:1] + _sd_parts[2:])
        # _ns_workdir = "/cc_sessions/<conv>/<agent>"
        exec_args = [
            "-i",
            "--user", "root",
            "-e", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "-e", f"HOME={_ns_workdir}",
            "-e", "USER=pawflow",
            "-e", f"CLAUDE_CONFIG_DIR={_ns_workdir}",
            "-e", "NODE_OPTIONS=--max-old-space-size=768",
            "-e", f"PAWFLOW_HOST={host_ip}",
            "-e", "GIT_CONFIG_COUNT=1",
            "-e", "GIT_CONFIG_KEY_0=safe.directory",
            "-e", f"GIT_CONFIG_VALUE_0={_ns_workdir}",
        ]
        # Pass extra env vars (e.g. ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL)
        for k, v in (extra_env or {}).items():
            exec_args.extend(["-e", f"{k}={v}"])
        # Build the in-namespace command:
        #   mount --bind /cc_sessions/<user> /cc_sessions    (private to this ns)
        #   cd /cc_sessions/<conv>/<agent>
        #   printf "__PF_CLAUDE_PID=$$" 1>&2                 (bash PID = claude PID
        #                                                     after chained exec)
        #   exec setpriv ... -- claude <args>
        #
        # Why `exec` + $$ and NOT `setsid ... & + $!` ?
        # `bash -c "<script>"` inside a container (the shape docker exec
        # uses) does NOT populate $! when it backgrounds a command — an
        # inescapable bash quirk (reproducible: `docker exec <c> bash -c
        # 'sleep 1 & echo pid=$!'` prints `pid=`). The old `setsid & $!`
        # pattern therefore always printed an empty PID, `wait $_cc_pid`
        # errored with "not a pid or valid job spec", and the whole
        # claude spawn failed 100% of the time.
        #
        # Why `setsid` at the outer level?
        # docker exec does NOT create a new session per exec — every exec
        # in the same container inherits the container's containerd-shim
        # session (or whatever PGID the shim holds). Without setsid, bash
        # inherits a PGID shared with EVERY OTHER CONCURRENT exec in the
        # container. When `_kill_cc_hard` sends `kill -9 -<PGID>`
        # targeting its own claude, it actually SIGKILLs every sibling
        # exec that shares the PGID — e.g. the main agent's CC when a
        # compact / memory_extract / sub-agent finishes. Repro: user's
        # log showed compact's kill with pgid=1985254 immediately preceded
        # main's exit 137, both streams' stderr buffers showing the same
        # __PF_CLAUDE_PID= value (mirrored from shared self._stderr_buffer,
        # but more importantly they were in the SAME process group).
        #
        # setsid gives bash its own session + PGID. After the exec chain,
        # claude's PID == PGID == SID — isolated from siblings. Negative-
        # PID kill then targets only this exec's process group.
        #
        # Chained `exec`: bash PID == setpriv PID == claude PID (exec
        # replaces the process in place). `kill -9 -<PID>` with a NEGATIVE
        # PID in `_kill_cc_hard` sends SIGKILL to the whole pgroup, reaping
        # claude + every Node worker it forked — the original goal of
        # 0e22927 without the bash-quirk footgun.
        import shlex
        _claude_quoted = " ".join(shlex.quote(str(a)) for a in claude_args)
        _shell_script = (
            f"mount --bind {shlex.quote(_user_slot)} /cc_sessions && "
            f"cd {shlex.quote(_ns_workdir)} && "
            f'printf "__PF_CLAUDE_PID=%s\\n" "$$" 1>&2 && '
            f"exec setpriv --reuid=1000 --regid=1000 --clear-groups "
            f"-- claude {_claude_quoted}"
        )
        exec_args.extend([
            container_name,
            # setsid --wait: new session + PGID per exec so kill -9 -<PGID>
            # only hits THIS exec's process group, never siblings. Without
            # setsid every concurrent docker exec in the same container
            # inherits the container's containerd-shim PGID, and a kill
            # targeting that PGID wipes every sibling exec — repro seen
            # when a compact/memory_extract finishes and its
            # _kill_cc_hard takes out the main agent's CC with it
            # (exit 137). setsid forks once (bash is typically a PGL so
            # can't become a session leader without forking); --wait makes
            # the setsid parent block for the child and propagate its exit
            # status so docker exec still sees the real return code.
            # unshare -m: private mount namespace for the /cc_sessions bind.
            "setsid", "--wait", "unshare", "-m", "--",
            "bash", "-c", _shell_script,
        ])

        cmd = docker_cmd() + ["exec"] + exec_args
        logger.info("Pool exec: %s", container_name)
        # Detailed env dump at DEBUG — re-enable when diagnosing env-
        # propagation bugs (ANTHROPIC_BASE_URL, NODE_TLS_REJECT_UNAUTHORIZED,
        # etc.). Keys are scrubbed so the line stays paste-safe.
        if logger.isEnabledFor(logging.DEBUG):
            _env_preview = []
            _i = 0
            while _i < len(exec_args) - 1:
                if exec_args[_i] == "-e":
                    _kv = exec_args[_i + 1]
                    _k, _, _v = _kv.partition("=")
                    if _k.lower() in ("anthropic_api_key", "api_key"):
                        _v = f"<{len(_v)} chars redacted>"
                    elif len(_v) > 80:
                        _v = _v[:77] + "..."
                    _env_preview.append(f"{_k}={_v}")
                    _i += 2
                else:
                    _i += 1
            logger.debug(
                "Pool exec envs=%s cmd_tail=%s",
                _env_preview, " ".join(cmd[-6:]))

        return subprocess.Popen(cmd, **popen_kwargs)  # nosec B603

    def status(self) -> dict:
        """Return pool status for diagnostics."""
        now = time.monotonic()
        with self._lock:
            active = [
                {
                    "name": info.name,
                    "idle_seconds": int(now - info.last_used),
                }
                for info in self._active.values()
            ]
            ready = [
                {
                    "name": info.name,
                    "idle_seconds": int(now - info.last_used),
                }
                for info in self._ready.values()
            ]
            return {
                "active": active,
                "ready": ready,
                "total": len(self._active) + len(self._ready),
                "max": self.max_containers,
                "prewarm": self.prewarm_count,
                "image": self.image,
            }

    def shutdown(self):
        """Kill all pool containers (called on server shutdown)."""
        with self._lock:
            names = list(self._active.keys()) + list(self._ready.keys())
            self._active.clear()
            self._ready.clear()
        for name in names:
            self._kill_container(name)
        logger.info("Pool shutdown: %d container(s) killed", len(names))

    # ── Internal ───────────────────────────────────────────────────

    def _spawn_container(self, workspace_mount_args: Optional[List[str]] = None) -> str:
        """Spawn a new warm container and return its name.

        Pure spawn: caller owns the bookkeeping (`_active` / `_ready`).
        MUST be called OUTSIDE `self._lock` — docker run takes ~1-3s and
        blocking the lock would stall every other acquire/release.
        """
        import uuid
        name = f"pf-cc-pool-{uuid.uuid4().hex[:8]}"
        workspace_mount_args = list(workspace_mount_args or [])

        # Ensure sessions dir exists on host (Docker -v does NOT create
        # missing host-side dirs — the bind would fail or create owned-by-
        # root dirs). Use the authoritative path from core.paths so we
        # don't accidentally recreate the pre-migration data/claude_sessions
        # location.
        import core.paths as _paths
        _paths.CLAUDE_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        # Dev-mount the MCP bridge + PawFlow SDK straight from the host
        # tree so that changes take effect on the next container spawn
        # without any rebuild. The image itself ships no bridge code —
        # these binds are the only source of /opt/pawflow/*.py.
        from core.docker_utils import translate_path, to_host_path
        _project_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        _bridge_src_files = [
            (os.path.join(_project_root, "tools"), "mcp_bridge.py"),
            (os.path.join(_project_root, "docker", "pawflow_sdk"),
             "pawflow.py"),
        ]
        _bridge_mounts = []
        for _src_dir, _rf in _bridge_src_files:
            _src = os.path.join(_src_dir, _rf)
            if os.path.exists(_src):
                _translated = translate_path(to_host_path(_src))
                _bridge_mounts += [
                    "-v", f"{_translated}:/opt/pawflow/{_rf}:ro"]
        # Mount the pawflow_relay/ package so the bridge can
        # `from pawflow_relay.ws_frame import ...`. Same pattern as
        # pawflow_relay/thread.py:384: live-reload against the host tree,
        # Python resolves the package via __init__.py.
        _pkg_dir = os.path.join(_project_root, "pawflow_relay")
        if os.path.isdir(_pkg_dir):
            _translated_pkg = translate_path(to_host_path(_pkg_dir))
            _bridge_mounts += [
                "-v", f"{_translated_pkg}:/opt/pawflow/pawflow_relay:ro"]

        logger.info(
            "[pool] bridge dev-mounts (%d): %s",
            len(_bridge_mounts) // 2,
            " | ".join(_bridge_mounts[i] + " " + _bridge_mounts[i+1]
                       for i in range(0, len(_bridge_mounts), 2))
            if _bridge_mounts else "NONE")

        # Resolve the HTTPS listener's public hostname so the container
        # can reach it via that name (required when ANTHROPIC_BASE_URL
        # is https://<hostname>:<port>/… — without --add-host the
        # container's resolver would fail to look up the hostname that
        # only lives in the user's /etc/hosts or C:\Windows\System32\
        # drivers\etc\hosts). Maps to the actual LAN IP (not
        # host-gateway) so the container goes directly through the
        # bridged network — host-gateway resolves to a Docker-
        # internal gateway on some platforms (Docker Desktop, WSL2)
        # that may not route back to the listener bound on 0.0.0.0
        # of the LAN interface.
        _host_aliases: List[str] = []
        try:
            from services import http_listener_service as _hl_mod
            _inst = getattr(_hl_mod, "_instances", None) or {}
            for _p, _lst in _inst.items():
                if _p == 19895:
                    continue  # internal listener, not the public one
                _ph = (getattr(_lst, "public_hostname", "") or "").strip()
                if _ph and _ph not in _host_aliases:
                    _host_aliases.append(_ph)
        except Exception:
            logger.debug(
                "[pool] public_hostname lookup failed", exc_info=True)

        _host_ip = get_host_ip()
        _add_host_target = (
            "host-gateway" if _host_ip == "host.docker.internal" else _host_ip)
        _extra_add_hosts: list = []
        for _alias in _host_aliases:
            _extra_add_hosts.extend(["--add-host", f"{_alias}:{_add_host_target}"])
        if _extra_add_hosts:
            logger.info(
                "[pool] container hostname aliases → %s: %s",
                _add_host_target, _host_aliases)

        run_args = [
            "-d",  # detached
            "--rm",  # auto-remove on exit — prevents dead-container pileup
            "--name", name,
            "--cpus", self.cpu_limit,
            "--memory", self.memory_limit,
            *workspace_mount_args,
            # Mount sessions volume (all sessions, shared across all execs)
            "-v", f"{self._sessions_host_path}:/cc_sessions",
            *_bridge_mounts,
            # Network: allow MCP bridge to reach host tool relay
            "--add-host", "host.docker.internal:host-gateway",
            # Extra aliases for the HTTPS listener hostname — without
            # these, https://<cert-CN>:9090/ from inside the container
            # fails DNS (the hostname only exists in the user's host
            # file), bails silently, and CC surfaces "empty or malformed
            # response (HTTP 200)".
            *_extra_add_hosts,
            # Run as non-root (Claude Code requirement)
            "--user", "1000:1000",
            # Force clean HOME/USER — PATH is set in docker exec, not here
            # (setting PATH here breaks the entrypoint command resolution)
            "-e", "HOME=/home/pawflow",
            "-e", "USER=pawflow",
            # Security
            "--shm-size", "512m",
            "--tmpfs", "/tmp:rw,nosuid,size=512m",  # nosec B108 - Docker tmpfs mount target inside ephemeral container.
            "--cap-add", "SYS_ADMIN",  # needed for mount --bind in exec_claude
            # Override entrypoint: keep alive (full path — PATH may be dirty)
            "--entrypoint", "/usr/bin/sleep",
            self.image,
            "infinity",
        ]

        cmd = docker_cmd() + ["run"] + run_args
        logger.info("Pool spawn: %s (image=%s)", name, self.image)

        result = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to spawn pool container {name}: "
                f"{result.stderr.strip()[:300]}")

        # Start chronyd for time sync (avoid drift in long-running containers)
        subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", "root", name, "chronyd"],
            capture_output=True, timeout=5)

        logger.info("Pool container spawned: %s", name)
        return name

    def _kill_container(self, name: str):
        """Kill and remove a container (`docker rm -f`).

        Safe to call with or without `self._lock` held. Under the 1:1
        model, callers typically call this OUTSIDE the lock since
        docker rm -f can take ~1s.
        """
        try:
            subprocess.run(  # nosec B603
                docker_cmd() + ["rm", "-f", name],
                capture_output=True, timeout=10)
            logger.info("Pool container killed: %s", name)
        except Exception as e:
            logger.warning("Failed to kill pool container %s: %s", name, e)

    def _is_container_alive(self, name: str) -> bool:
        """Check if a container is still running."""
        try:
            result = subprocess.run(  # nosec B603
                docker_cmd() + ["inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True, timeout=5)
            return result.stdout.strip() == "true"
        except Exception:
            return False

    # ── Idle reaper ────────────────────────────────────────────────

    def _ensure_reaper(self):
        """Start the idle reaper thread if not already running."""
        if self._reaper_started:
            return
        self._reaper_started = True
        t = threading.Thread(target=self._reaper_loop, daemon=True)
        t.start()

    def _reaper_loop(self):
        """Periodically kill idle containers."""
        while True:
            time.sleep(60)  # check every minute
            try:
                self._reap_idle()
            except Exception as e:
                logger.warning("Pool reaper error: %s", e)

    def _reap_idle(self):
        """Kill stale ready containers and dead active ones.

        - Ready containers idle beyond `idle_timeout` are reaped so a
          lowered `prewarm_count` eventually drains.
        - Active containers whose underlying Docker process died
          unexpectedly are dropped from bookkeeping so a subsequent
          release() doesn't try to kill an already-gone container (and
          so the cap accounting stays honest).
        """
        now = time.monotonic()
        to_kill = []
        to_forget_active = []

        with self._lock:
            for name, info in list(self._ready.items()):
                if (now - info.last_used) > self.idle_timeout:
                    del self._ready[name]
                    to_kill.append((name, "ready_idle"))
            for name, info in list(self._active.items()):
                if not self._is_container_alive(name):
                    logger.warning(
                        "Pool active container %s is dead — "
                        "dropping from pool (caller should release)",
                        name)
                    del self._active[name]
                    to_forget_active.append(name)

        for name, reason in to_kill:
            self._kill_container(name)  # idempotent if already dead
        if to_kill:
            logger.info("Pool reaper: killed %d ready container(s)",
                        len(to_kill))
        if to_forget_active:
            logger.info("Pool reaper: forgot %d dead active container(s): %s",
                        len(to_forget_active), to_forget_active)

    def _cleanup_orphans(self):
        """Kill + remove orphan pf-cc-pool containers from previous runs.

        Without -a the previous version only saw RUNNING containers,
        so Exited pool containers piled up across restarts (seen with
        hundreds after a few days). ps -a includes every state
        (running, exited, created, dead) and rm -f stops+removes.
        """
        try:
            result = subprocess.run(  # nosec B603
                docker_cmd() + ["ps", "-a",
                                "--filter", "name=pf-cc-pool-",
                                "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                orphans = [n.strip() for n in result.stdout.strip().split("\n") if n.strip()]
                for name in orphans:
                    try:
                        subprocess.run(  # nosec B603
                            docker_cmd() + ["rm", "-f", name],
                            capture_output=True, timeout=10)
                        logger.info("Pool: removed orphan container %s", name)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                if orphans:
                    logger.info("Pool: cleaned up %d orphan container(s)", len(orphans))
        except Exception as e:
            logger.warning("Pool: orphan cleanup failed: %s", e)
