"""Private stdio transport for an isolated OpenCode SDK v2 HTTP server.

Runs inside the provider container using only the Python standard library.
Wire frames are newline-delimited JSON: requests carry id/method/path/body,
responses carry id/status/body, and unsolicited frames carry event or ready.
The HTTP endpoints and SSE data are the public OpenCode v2 contract:
https://opencode.ai/docs/server/ and the v1.14.19 SDK generated types.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - manage the isolated native CLI process.
import sys
import threading
import time
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:4096"
_output_lock = threading.Lock()
# Provider proxy variables may be present for upstream model traffic. The
# private loopback protocol must never inherit those proxy settings.
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def emit(frame):
    with _output_lock:
        print(json.dumps(frame, ensure_ascii=False), flush=True)


def sse_events(lines):
    """Decode SSE records, including multiline data and CRLF framing."""
    data = []
    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if data:
                yield json.loads("\n".join(data))
                data.clear()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        raise RuntimeError("OpenCode event stream ended within a record")


def request(method, path, body=None, *, timeout=30):
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Expected an absolute API path")
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path, data=payload, method=method,
        headers={"Content-Type": "application/json"})
    try:
        response = _http.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # Never echo arbitrary error bodies, which may include credentials.
        return exc.code, None
    with response:
        data = response.read()
        return response.status, json.loads(data) if data else None


def lock_scope(path):
    """Prevent two containers from opening the same scoped session database."""
    import fcntl  # The bridge runs in the Linux provider image.
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(descriptor, "a")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise RuntimeError("OpenCode scope is already running") from None
    return handle


def main():
    binary = os.environ.get("PAWFLOW_OPENCODE_BIN", "opencode")
    server = None
    scope_lock = None
    connected = threading.Event()
    broken = threading.Event()
    try:
        scope_lock = lock_scope("/opencode-runtime/server.lock")
        server = subprocess.Popen(  # nosec B603 - operator-selected binary, fixed argv, no shell.
            [binary, "serve", "--hostname", "127.0.0.1", "--port", "4096"],
            stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr)
        deadline = time.monotonic() + 30
        while True:
            if server.poll() is not None:
                raise RuntimeError("OpenCode server exited during startup")
            try:
                status, health = request("GET", "/global/health", timeout=1)
                if status == 200 and health.get("healthy"):
                    break
            except (OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("OpenCode health check timed out")
            time.sleep(0.1)

        def events():
            try:
                with _http.open(BASE_URL + "/event", timeout=60) as stream:
                    for event in sse_events(stream):
                        if event.get("type") == "server.connected":
                            connected.set()
                        emit({"event": event})
                raise RuntimeError("OpenCode event stream closed")
            except Exception:
                broken.set()
                connected.set()
                emit({"fatal": "OpenCode event stream disconnected"})

        threading.Thread(target=events, daemon=True).start()
        if not connected.wait(15) or broken.is_set():
            raise RuntimeError("OpenCode event subscription failed")
        emit({"ready": True, "version": health.get("version", "")})
        for line in sys.stdin:
            frame = json.loads(line)
            try:
                status, body = request(
                    frame["method"], frame["path"], frame.get("body"))
                emit({"id": frame["id"], "status": status, "body": body})
            except Exception:
                emit({"id": frame["id"], "status": 502, "body": None})
    except Exception as exc:
        emit({"fatal": str(exc)})
        return 1
    finally:
        if server is not None:
            server.kill()
            server.wait()
        if scope_lock is not None:
            scope_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
