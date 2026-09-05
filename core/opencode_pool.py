"""Managed OpenCode SDK v2 runtime and private Docker stdio transport.

Only the auth home is shared by a user/service. Each identity/config revision
has its own mounted XDG data, config, state, cache and working directory.
No server ports are published and no other session directories are mounted.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess  # nosec B404 - Docker process control.
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core._llm_types import LLMClientError

CONTAINER_HOME = "/opencode-home"
CONTAINER_RUNTIME = "/opencode-runtime"
CONTAINER_BRIDGE = "/opt/pawflow/opencode_bridge.py"
CONTAINER_PYTHON = "/usr/bin/python3"
MINIMUM_OPENCODE_VERSION = (1, 14, 19)


class OpenCodeHTTPError(LLMClientError):
    def __init__(self, method: str, path: str, status: int):
        self.status = status
        super().__init__(f"OpenCode {method} {path} failed (HTTP {status})")


class OpenCodeCancelled(Exception):
    """Internal cancellation signal; never reported as a provider error."""


def scope_digest(values) -> str:
    return hashlib.sha256(json.dumps(
        list(values), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class OpenCodePool:
    """Factory for isolated runtimes; callers own and close their handles."""

    @staticmethod
    def base_dir() -> Path:
        from core.paths import RUNTIME_DIR
        return RUNTIME_DIR / "sessions" / "opencode"

    @classmethod
    def home_dir(cls, user_id: str, service_id: str) -> Path:
        if not user_id or not service_id:
            raise ValueError("OpenCode requires user_id and service_id")
        path = cls.base_dir() / "homes" / scope_digest((user_id, service_id))
        auth = path / ".local" / "share" / "opencode"
        auth.mkdir(parents=True, exist_ok=True)
        try:
            with (auth / "auth.json").open("x", encoding="utf-8") as handle:
                handle.write("{}")
            (auth / "auth.json").chmod(0o600)
        except FileExistsError:
            pass
        return path

    @classmethod
    def runtime_dir(cls, scope: tuple[str, str, str, str], revision: str) -> Path:
        if any(not value for value in scope) or not revision:
            raise ValueError("OpenCode requires user, conversation, agent and service identity")
        return cls.base_dir() / "runtimes" / scope_digest((*scope, revision))

    @staticmethod
    def image() -> str:
        return os.environ.get("PAWFLOW_OPENCODE_IMAGE") or "pawflow-claude-code:latest"

    @staticmethod
    def binary() -> str:
        return os.environ.get("PAWFLOW_OPENCODE_BIN") or "opencode"

    @staticmethod
    def user_spec() -> str:
        uid = os.environ.get("PAWFLOW_RUN_UID", "1000")
        gid = os.environ.get("PAWFLOW_RUN_GID", "1000")
        if not uid.isdigit() or not gid.isdigit():
            raise ValueError("PAWFLOW_RUN_UID/GID must be numeric")
        return f"{uid}:{gid}"


class OpenCodeRuntime:
    """Request multiplexing and event delivery for one container.

    Construct and assign this object before calling start so force stop can
    interrupt startup as well as a prompt. Event loss is fatal, never silent.
    """

    def __init__(self, scope, revision, env, *, ephemeral=False):
        self.scope = scope
        self.revision = revision
        self.env = dict(env)
        self.ephemeral = ephemeral
        self.directory = OpenCodePool.runtime_dir(scope, revision)
        self.home = OpenCodePool.home_dir(scope[0], scope[3])
        self.name = ""
        self.process = None
        self.events = queue.Queue(maxsize=8192)
        self._pending = {}
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._fatal = ""
        self.version = ""
        self._stderr = None

    @property
    def is_running(self):
        return (not self._closed.is_set() and not self._fatal
                and self.process is not None and self.process.poll() is None)

    def _check(self):
        if self._closed.is_set():
            raise OpenCodeCancelled()
        if self._fatal:
            raise LLMClientError(self._fatal)

    def start(self):
        from core.antigravity_observer_pool import AntigravityObserverPool
        from core.apparmor import apparmor_security_opts
        from core.docker_utils import (
            docker_cmd, get_server_id, pawflow_container_labels,
            to_host_path, translate_path)

        self._check()
        for part in ("work", "data", "data/opencode", "config", "state", "cache", "logs"):
            directory = self.directory / part
            if directory.is_symlink():
                raise LLMClientError("OpenCode runtime directories must not be symlinks")
            directory.mkdir(parents=True, exist_ok=True)
        auth_link = self.directory / "data" / "opencode" / "auth.json"
        auth_target = CONTAINER_HOME + "/.local/share/opencode/auth.json"
        if auth_link.is_symlink() and str(auth_link.readlink()) != auth_target:
            raise LLMClientError("OpenCode scoped auth symlink has an unexpected target")
        if not auth_link.is_symlink():
            if auth_link.exists():
                raise LLMClientError("OpenCode scoped auth path must be a symlink")
            auth_link.symlink_to(auth_target)
        image = OpenCodePool.image()
        self.name = f"pf-{get_server_id()[:12]}-opencode-{uuid.uuid4().hex[:10]}"
        mount = lambda path: translate_path(to_host_path(str(path.resolve())))
        argv = docker_cmd() + [
            "run", "-d", "--rm", "--init", "--name", self.name,
            *pawflow_container_labels("opencode"),
            "-v", f"{mount(self.home)}:{CONTAINER_HOME}",
            "-v", f"{mount(self.directory)}:{CONTAINER_RUNTIME}",
            "--add-host", "host.docker.internal:host-gateway",
            *apparmor_security_opts(image),
            "--tmpfs", "/tmp:rw,nosuid,size=512m",  # nosec B108 - container tmpfs.
            "--user", "root", "--entrypoint", "/usr/bin/sleep", image, "infinity"]
        try:
            result = subprocess.run(argv, capture_output=True, timeout=30)  # nosec B603
            if result.returncode:
                raise LLMClientError("Failed to start OpenCode Docker container")
            self._check()
            root = Path(__file__).resolve().parents[1]
            AntigravityObserverPool._copy_runtime_files(self.name, [
                (root / "tools/opencode_bridge.py", CONTAINER_BRIDGE),
                (root / "tools/mcp_bridge.py", "/opt/pawflow/mcp_bridge.py"),
                (root / "core/tool_json.py", "/opt/pawflow/tool_json.py"),
                (root / "docker/pawflow_sdk/pawflow.py", "/opt/pawflow/pawflow.py"),
            ], root / "pawflow_relay")
            self._check()
            self._stderr = (self.directory / "logs" / "server.log").open("ab")
            env = {**os.environ, **self.env,
                   "PAWFLOW_OPENCODE_BIN": OpenCodePool.binary()}
            exec_args = docker_cmd() + [
                "exec", "-i", "--user", OpenCodePool.user_spec(),
                "-w", CONTAINER_RUNTIME + "/work",
                "-e", "HOME=" + CONTAINER_HOME,
                "-e", "XDG_DATA_HOME=" + CONTAINER_RUNTIME + "/data",
                "-e", "XDG_CONFIG_HOME=" + CONTAINER_RUNTIME + "/config",
                "-e", "XDG_STATE_HOME=" + CONTAINER_RUNTIME + "/state",
                "-e", "XDG_CACHE_HOME=" + CONTAINER_RUNTIME + "/cache",
                "-e", "USER=pawflow"]
            for name in sorted(set(self.env) | {"PAWFLOW_OPENCODE_BIN"}):
                exec_args += ["-e", name]
            exec_args += [self.name, CONTAINER_PYTHON, "-u", CONTAINER_BRIDGE]
            # Do not hold the state lock across Docker operations.
            self.process = subprocess.Popen(  # nosec B603
                exec_args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._stderr, text=True, encoding="utf-8", env=env)
            self._check()
            threading.Thread(target=self._read_frames, daemon=True).start()
            deadline = time.monotonic() + 50
            while not self._ready.wait(0.1):
                self._check()
                if time.monotonic() >= deadline:
                    raise LLMClientError("OpenCode runtime startup timed out")
            self._check()
            try:
                version = tuple(int(x) for x in self.version.split(".")[:3])
            except ValueError:
                version = ()
            if version < MINIMUM_OPENCODE_VERSION:
                raise LLMClientError("OpenCode requires version 1.14.19 or newer")
        except BaseException:
            self.close()
            raise

    def _fail(self, message):
        with self._lock:
            self._fatal = message
            for pending in self._pending.values():
                pending.put({"fatal": message})
            self._ready.set()

    def _read_frames(self):
        try:
            for line in self.process.stdout:
                frame = json.loads(line)
                if "event" in frame:
                    self.events.put_nowait(frame["event"])
                elif "id" in frame:
                    with self._lock:
                        pending = self._pending.get(frame["id"])
                        if pending is not None:
                            pending.put(frame)
                elif frame.get("ready"):
                    self.version = str(frame.get("version", ""))
                    self._ready.set()
                elif "fatal" in frame:
                    self._fail(str(frame["fatal"]))
                    return
        except (ValueError, OSError, queue.Full):
            self._fail("OpenCode event transport failed or overflowed")
        finally:
            if not self._closed.is_set():
                self._fail(self._fatal or "OpenCode runtime exited")

    def request(self, method, path, body=None, *, timeout=35):
        self._check()
        ident = uuid.uuid4().hex
        pending = queue.Queue()
        with self._lock:
            self._pending[ident] = pending
        try:
            payload = json.dumps({"id": ident, "method": method, "path": path, "body": body})
            with self._write_lock:
                self._check()
                self.process.stdin.write(payload + "\n")
                self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while True:
                self._check()
                try:
                    frame = pending.get(timeout=0.1)
                    break
                except queue.Empty:
                    if time.monotonic() >= deadline:
                        raise LLMClientError("OpenCode HTTP request timed out")
            if "fatal" in frame:
                raise LLMClientError(frame["fatal"])
            status = int(frame["status"])
            if not 200 <= status < 300:
                raise OpenCodeHTTPError(method, path, status)
            return frame.get("body")
        finally:
            with self._lock:
                self._pending.pop(ident, None)

    def next_event(self, timeout=0.2):
        self._check()
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            self._check()
            return None

    def drain_events(self):
        self._check()
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return

    def close(self):
        from core.docker_utils import docker_cmd
        self._closed.set()
        # Killing docker exec alone leaves the server and MCP children alive.
        # The container is the exact scope-owned kill boundary.
        if self.name:
            try:
                subprocess.run(  # nosec B603
                    docker_cmd() + ["rm", "-f", self.name],
                    capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
        process = self.process
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if process.stdin is not None:
                process.stdin.close()
        if self._stderr is not None:
            self._stderr.close()
        if self.ephemeral:
            shutil.rmtree(self.directory, ignore_errors=True)
