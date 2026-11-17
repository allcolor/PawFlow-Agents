"""Persistent Python kernel behind ``execute_script(persistent=true)``.

One file, two roles:

- ``client`` -- what ``execute_script`` runs for each call, through the
  relay's normal ``exec`` (container, relay host or server-local surface).
  It finds the kernel of its key (relay surface + conversation + agent),
  starts one if there is none, sends it the code with the environment of
  this one execution, prints what the code printed and exits.
- ``serve`` -- the kernel itself: a detached process that keeps one Python
  namespace alive between calls, runs one call at a time and exits after
  an idle timeout.

Why a client per call instead of a long-lived connection: the server mints a
tool capability per execution and revokes it when the call ends, and a
``local=true`` command only has its loopback tool bridge for the length of
the command. The client hands both to the kernel for the duration of the
call; the kernel removes them as soon as the call ends, so code left running
in a background thread cannot call tools afterwards.

State lives in ``<tempdir>/pawflow-kernels/<key>/`` (mode 0700):
``state.json`` (pid, port, secret, SDK hash; mode 0600) and ``sdk/`` (the
copy of ``pawflow.py`` and this file the kernel imports from, since the run
directory of the call that started it is deleted).

Wire format: one JSON line each way over a loopback TCP connection; every
request carries the secret from ``state.json``.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import contextlib
import hashlib
import hmac
import io
import json
import os
import queue
import secrets
import shutil
import signal
import socket
import subprocess  # nosec B404 - starts the kernel with this interpreter
import sys
import tempfile
import threading
import time
import traceback

DEFAULT_IDLE_SECONDS = 30 * 60
MIN_IDLE_SECONDS = 60
MAX_IDLE_SECONDS = 8 * 3600
START_TIMEOUT_SECONDS = 20.0
LOCK_STALE_SECONDS = 30.0
BUSY_WAIT_SECONDS = 10.0
SDK_FILES = ("pawflow.py", "pawflow_kernel.py")
# Set for the length of one call only, from the client's environment.
CALL_ENV = (
    "PAWFLOW_TOOL_CAP", "PAWFLOW_TOOL_ENDPOINT", "PAWFLOW_USER_ID",
    "PAWFLOW_CONVERSATION_ID", "PAWFLOW_AGENT_NAME", "PAWFLOW_DATA_DIR",
)


def clamp_idle(value) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_IDLE_SECONDS
    return max(MIN_IDLE_SECONDS, min(MAX_IDLE_SECONDS, seconds))


def valid_key(key: str) -> bool:
    return 8 <= len(key) <= 64 and key.isalnum()


def state_dir(key: str) -> str:
    return os.path.join(tempfile.gettempdir(), "pawflow-kernels", key)


def sdk_hash(directory: str) -> str:
    digest = hashlib.sha256()
    for name in SDK_FILES:
        with open(os.path.join(directory, name), "rb") as fh:
            digest.update(fh.read())
    return digest.hexdigest()[:16]


def _send(conn, payload: dict) -> None:
    conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


def _recv_line(conn) -> dict | None:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
    if not buf.strip():
        return None
    return json.loads(buf.decode("utf-8"))


# ── kernel ────────────────────────────────────────────────────────────


class _Call:
    def __init__(self, request: dict):
        self.request = request
        self.done = threading.Event()
        self.reply: dict = {}
        self.abandoned = False
        self.running = False


class Kernel:
    """The serving side; ``run_forever`` runs on the main thread."""

    def __init__(self, key: str, idle_seconds: int, sdk: str):
        self.key = key
        self.dir = state_dir(key)
        self.idle_seconds = clamp_idle(idle_seconds)
        self.sdk_hash = sdk
        self.secret = secrets.token_urlsafe(32)
        self.calls: "queue.Queue[_Call]" = queue.Queue()
        self.namespace = {"__name__": "__main__", "__builtins__": builtins}
        self.count = 0
        self.last_active = time.monotonic()
        self.current: _Call | None = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]

    # state file ------------------------------------------------------

    def write_state(self) -> None:
        path = os.path.join(self.dir, "state.json")
        tmp = f"{path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "port": self.port,
                       "secret": self.secret, "sdk_hash": self.sdk_hash}, fh)
        os.replace(tmp, path)

    def remove_state(self) -> None:
        path = os.path.join(self.dir, "state.json")
        try:
            with open(path, encoding="utf-8") as fh:
                if json.load(fh).get("pid") != os.getpid():
                    return  # a successor already owns the key
            os.remove(path)
        except (OSError, ValueError):
            pass

    def exit(self) -> None:
        self.remove_state()
        os._exit(0)

    # connections -----------------------------------------------------

    def accept_forever(self) -> None:
        while True:
            conn, _ = self._sock.accept()
            threading.Thread(target=self._serve_conn, args=(conn,),
                             daemon=True).start()

    def _serve_conn(self, conn) -> None:
        with conn:
            try:
                request = _recv_line(conn)
            except (OSError, ValueError):
                return
            if not isinstance(request, dict) or not hmac.compare_digest(
                    str(request.get("secret") or ""), self.secret):
                return
            op = request.get("op")
            if op == "ping":
                current = self.current
                _send(conn, {"ok": True, "pid": os.getpid(),
                             "sdk_hash": self.sdk_hash, "calls": self.count,
                             "busy": current is not None,
                             "abandoned": bool(current and current.abandoned)})
            elif op == "shutdown":
                with contextlib.suppress(OSError):
                    _send(conn, {"ok": True})
                self.exit()
            elif op == "run":
                self._serve_run(conn, request)

    def _serve_run(self, conn, request: dict) -> None:
        call = _Call(request)
        self.calls.put(call)
        # The client sends nothing more: EOF means its command was killed
        # (force stop, relay timeout). Interrupt the call it is waiting for.
        conn.settimeout(0.5)
        while not call.done.is_set():
            try:
                if conn.recv(1) == b"":
                    call.abandoned = True
                    if call.running:
                        _interrupt_main()
                    return
            except socket.timeout:
                continue
            except OSError:
                call.abandoned = True
                return
        conn.settimeout(None)
        with contextlib.suppress(OSError):
            _send(conn, call.reply)

    # execution -------------------------------------------------------

    def run_forever(self) -> None:
        while True:
            try:
                remaining = self.idle_seconds - (time.monotonic() - self.last_active)
                if remaining <= 0 and self.calls.empty():
                    self.exit()
                try:
                    call = self.calls.get(timeout=max(0.1, min(remaining, 5.0)))
                except queue.Empty:
                    continue
                if call.abandoned:
                    continue
                self._run(call)
                self.last_active = time.monotonic()
            except KeyboardInterrupt:
                # An interrupt that landed just after its call ended.
                continue

    def _run(self, call: _Call) -> None:
        request = call.request
        self.idle_seconds = clamp_idle(request.get("idle", self.idle_seconds))
        env = request.get("env") if isinstance(request.get("env"), dict) else {}
        for name in CALL_ENV:
            if env.get(name):
                os.environ[name] = str(env[name])
            else:
                os.environ.pop(name, None)
        self.count += 1
        out, err = io.StringIO(), io.StringIO()
        error = ""
        self.current = call
        call.running = True
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self._exec(str(request.get("code") or ""), self.count)
        except KeyboardInterrupt:
            error = "KeyboardInterrupt: the call was interrupted\n"
        except SystemExit as exc:
            if exc.code not in (None, 0):
                error = f"SystemExit: {exc.code}\n"
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            error = _format_error(exc)
        finally:
            call.running = False
            self.current = None
            for name in CALL_ENV:
                os.environ.pop(name, None)
        call.reply = {"ok": not error, "stdout": out.getvalue(),
                      "stderr": err.getvalue() + error, "call": self.count}
        call.done.set()

    def _exec(self, code: str, number: int) -> None:
        filename = f"<call {number}>"
        tree = ast.parse(code, filename=filename, mode="exec")
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        exec(compile(tree, filename, "exec"), self.namespace)  # nosec B102
        if last is not None:
            value = eval(compile(last, filename, "eval"), self.namespace)  # nosec B307
            if value is not None:
                print(repr(value))


def _interrupt_main() -> None:
    """Raise KeyboardInterrupt in the call running on the main thread.

    A real SIGINT aimed at the main thread also breaks a blocking system
    call (sleep, select, a pipe read); ``interrupt_main`` alone only sets a
    flag, checked once that call returns.
    """
    if hasattr(signal, "pthread_kill"):
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)
    else:
        import _thread
        _thread.interrupt_main()


def _format_error(exc: BaseException) -> str:
    """The traceback of the caller's code only, without kernel frames."""
    tb = exc.__traceback__
    while tb is not None and not tb.tb_frame.f_code.co_filename.startswith("<call "):
        tb = tb.tb_next
    if isinstance(exc, SyntaxError) or tb is None:
        return "".join(traceback.format_exception_only(type(exc), exc))
    return "".join(traceback.format_exception(type(exc), exc, tb))


def serve(key: str, idle_seconds: int, sdk: str) -> None:
    # A detached process may inherit SIGINT ignored, which would make
    # interrupt_main() (a killed client) a no-op.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    kernel = Kernel(key, idle_seconds, sdk)
    sys.path.insert(0, os.path.join(kernel.dir, "sdk"))
    kernel.write_state()
    threading.Thread(target=kernel.accept_forever, daemon=True).start()
    kernel.run_forever()


# ── client ────────────────────────────────────────────────────────────


def _read_state(directory: str) -> dict | None:
    try:
        with open(os.path.join(directory, "state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None


def _request(state: dict, payload: dict, timeout: float | None):
    conn = socket.create_connection(("127.0.0.1", int(state["port"])), timeout=5)
    conn.settimeout(timeout)
    with conn:
        _send(conn, {"secret": state.get("secret", ""), **payload})
        return _recv_line(conn)


def _ping(state: dict | None) -> dict | None:
    if not state:
        return None
    try:
        reply = _request(state, {"op": "ping"}, timeout=5)
    except (OSError, ValueError, KeyError):
        return None
    return reply if isinstance(reply, dict) and reply.get("ok") else None


def _shutdown(directory: str, state: dict) -> None:
    with contextlib.suppress(OSError, ValueError, KeyError):
        _request(state, {"op": "shutdown"}, timeout=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _ping(state):
        time.sleep(0.1)
    current = _read_state(directory)
    if current and current.get("pid") == state.get("pid"):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(directory, "state.json"))


@contextlib.contextmanager
def _start_lock(directory: str):
    path = os.path.join(directory, "start.lock")
    deadline = time.monotonic() + START_TIMEOUT_SECONDS + LOCK_STALE_SECONDS
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(path) > LOCK_STALE_SECONDS:
                    os.remove(path)
                    continue
            if time.monotonic() > deadline:
                raise RuntimeError("timed out waiting for another kernel start")
            time.sleep(0.1)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def _spawn(directory: str, key: str, idle_seconds: int, digest: str) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    sdk_dir = os.path.join(directory, "sdk")
    os.makedirs(sdk_dir, exist_ok=True)
    for name in SDK_FILES:
        shutil.copyfile(os.path.join(here, name), os.path.join(sdk_dir, name))
    env = {k: v for k, v in os.environ.items() if k not in CALL_ENV}
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True,
              "cwd": os.getcwd(), "env": env}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(  # nosec B603 - this interpreter, this file
        [sys.executable, os.path.join(sdk_dir, "pawflow_kernel.py"), "serve",
         "--key", key, "--idle", str(idle_seconds), "--sdk-hash", digest],
        **kwargs)


def ensure_kernel(key: str, idle_seconds: int, restart: bool):
    """Return ``(state, notice)`` for a live kernel of ``key``."""
    directory = state_dir(key)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    digest = sdk_hash(os.path.dirname(os.path.abspath(__file__)))
    with _start_lock(directory):
        state = _read_state(directory)
        alive = _ping(state)
        notice = ""
        if alive and restart:
            _shutdown(directory, state)
            alive, notice = None, "restarted on request"
        elif alive and alive.get("sdk_hash") != digest:
            _shutdown(directory, state)
            alive, notice = None, "restarted: the PawFlow SDK was updated"
        if alive:
            return state, ""
        if state and not notice:
            notice = "the previous kernel had stopped (idle timeout or restart)"
        _spawn(directory, key, idle_seconds, digest)
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            fresh = _read_state(directory)
            if fresh and fresh != state and _ping(fresh):
                return fresh, notice or "new kernel"
            time.sleep(0.05)
    raise RuntimeError("the kernel did not start")


def wait_until_free(state: dict) -> str:
    """Empty when the kernel can take a call now, else why it cannot."""
    deadline = time.monotonic() + BUSY_WAIT_SECONDS
    while True:
        alive = _ping(state)
        if not alive or not alive.get("busy"):
            return ""
        if time.monotonic() > deadline:
            if alive.get("abandoned"):
                return ("the kernel is still running an interrupted call that "
                        "did not stop; use restart=true")
            return ("the kernel is busy with another call; retry when it "
                    "ends, or use restart=true")
        time.sleep(0.2)


def client(key: str, idle_seconds: int, restart: bool, script: str) -> int:
    with open(script, encoding="utf-8") as fh:
        code = fh.read()
    try:
        state, notice = ensure_kernel(key, idle_seconds, restart)
    except (OSError, RuntimeError) as exc:
        print(f"[pawflow kernel] could not start: {exc}", file=sys.stderr)
        return 2
    if notice:
        print(f"[pawflow kernel] {notice}: earlier variables are gone.")
    busy = wait_until_free(state)
    if busy:
        print(f"[pawflow kernel] {busy}", file=sys.stderr)
        return 4
    env = {name: os.environ[name] for name in CALL_ENV if os.environ.get(name)}
    try:
        reply = _request(state, {"op": "run", "code": code, "env": env,
                                 "idle": idle_seconds}, timeout=None)
    except (OSError, ValueError) as exc:
        reply, error = None, exc
    else:
        error = None
    if not isinstance(reply, dict):
        print("[pawflow kernel] the kernel died during this call; its state "
              f"is lost ({error or 'no reply'})", file=sys.stderr)
        return 3
    sys.stdout.write(reply.get("stdout") or "")
    sys.stderr.write(reply.get("stderr") or "")
    return 0 if reply.get("ok") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pawflow_kernel")
    sub = parser.add_subparsers(dest="role", required=True)
    run = sub.add_parser("client")
    run.add_argument("--key", required=True)
    run.add_argument("--idle", type=int, default=DEFAULT_IDLE_SECONDS)
    run.add_argument("--restart", action="store_true")
    run.add_argument("script")
    srv = sub.add_parser("serve")
    srv.add_argument("--key", required=True)
    srv.add_argument("--idle", type=int, default=DEFAULT_IDLE_SECONDS)
    srv.add_argument("--sdk-hash", required=True)
    args = parser.parse_args(argv)
    if not valid_key(args.key):
        print("[pawflow kernel] invalid key", file=sys.stderr)
        return 2
    if args.role == "serve":
        serve(args.key, args.idle, args.sdk_hash)
        return 0
    return client(args.key, clamp_idle(args.idle), args.restart, args.script)


if __name__ == "__main__":
    sys.exit(main())
