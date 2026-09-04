#!/usr/bin/env python3
"""Drive one ``authenticate`` round trip against the Antigravity ACP server.

Runs inside the ``pawflow-claude-code`` login container (see
``agy_acp_auth_login.sh``): it spawns Google's ``agy_acp_server`` over stdio,
sends ``initialize``, checks that the requested authentication method is
advertised, sends ``authenticate`` and waits for its response. The server
itself opens the browser (``BROWSER`` / ``DISPLAY`` come from the shell script)
and completes the OAuth redirect on its loopback listener; on success it
stores the token under ``GEMINI_HOME/antigravity-acp/``.

The outcome is written atomically to ``AGY_ACP_LOGIN_RESULT`` so the PawFlow
server can poll it with one ``docker exec cat``. Only the standard library is
used: the image has no ACP SDK.

Environment:
  AGY_ACP_BIN            server binary (default /opt/pawflow/antigravity-acp/agy_acp_server.par)
  AGY_ACP_AUTH_METHOD    advertised method id to use (default oauth-personal)
  AGY_ACP_LOGIN_RESULT   result JSON path (default /tmp/agy-acp-login.result.json)
  AGY_ACP_LOGIN_STDERR   server stderr log (default /tmp/agy-acp-login.stderr.log)
  AGY_ACP_LOGIN_TIMEOUT  seconds to wait for the authenticate response (default 280)
"""

from __future__ import annotations

import json
import os
import queue
import subprocess  # nosec B404 - spawning the ACP server is this script's job.
import sys
import threading
import time

SERVER_BIN = os.environ.get("AGY_ACP_BIN") or "/opt/pawflow/antigravity-acp/agy_acp_server.par"
SERVER_ARGS = ("--uid=",)
AUTH_METHOD = os.environ.get("AGY_ACP_AUTH_METHOD") or "oauth-personal"
RESULT_PATH = os.environ.get("AGY_ACP_LOGIN_RESULT") or "/tmp/agy-acp-login.result.json"  # nosec B108
STDERR_PATH = os.environ.get("AGY_ACP_LOGIN_STDERR") or "/tmp/agy-acp-login.stderr.log"  # nosec B108
TIMEOUT_SECONDS = float(os.environ.get("AGY_ACP_LOGIN_TIMEOUT") or "280")
INIT_TIMEOUT_SECONDS = 60.0


def write_result(payload: dict) -> None:
    tmp = RESULT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(tmp, RESULT_PATH)


def _reader(stream, lines: "queue.Queue[str | None]") -> None:
    try:
        for raw in stream:
            lines.put(raw)
    finally:
        lines.put(None)


class Session:
    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=_reader, args=(process.stdout, self.lines), daemon=True).start()

    def send(self, message: dict) -> None:
        if self.process.stdin is None:
            raise RuntimeError("the ACP server process has no stdin")
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def wait_response(self, request_id: int, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no response to request {request_id} within {timeout:.0f}s")
            try:
                raw = self.lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if raw is None:
                raise EOFError("the ACP server closed its stdout")
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == request_id and ("result" in message or "error" in message):
                return message
            if "method" in message and "id" in message:
                # A client request from the server: nothing is served here.
                self.send({
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                })


def main() -> int:
    stderr = open(STDERR_PATH, "ab", buffering=0)  # noqa: SIM115
    try:
        process = subprocess.Popen(  # nosec B603 - fixed argv, no shell
            [SERVER_BIN, *SERVER_ARGS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
    except OSError as exc:
        write_result({"ok": False, "error": f"cannot start {SERVER_BIN}: {exc}"})
        print(f"cannot start the ACP server: {exc}", file=sys.stderr)
        return 1
    session = Session(process)
    try:
        session.send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
                "clientInfo": {"name": "pawflow-antigravity-acp-login", "version": "1"},
            },
        })
        initialized = session.wait_response(1, INIT_TIMEOUT_SECONDS)
        if "error" in initialized:
            message = str(initialized["error"].get("message", initialized["error"]))
            write_result({"ok": False, "error": f"initialize failed: {message}"})
            print(f"initialize failed: {message}", file=sys.stderr)
            return 1
        advertised = [
            str(method.get("id", ""))
            for method in (initialized.get("result") or {}).get("authMethods") or []
            if isinstance(method, dict)
        ]
        if AUTH_METHOD not in advertised:
            error = (
                f"auth method {AUTH_METHOD!r} is not advertised by the server; "
                f"advertised: {', '.join(advertised) or 'none'}"
            )
            write_result({"ok": False, "error": error, "advertised": advertised})
            print(error, file=sys.stderr)
            return 2
        print(f"Authenticating with {AUTH_METHOD}: complete the sign-in in the browser window.")
        session.send({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "authenticate",
            "params": {"methodId": AUTH_METHOD},
        })
        authenticated = session.wait_response(2, TIMEOUT_SECONDS)
        if "error" in authenticated:
            message = str(authenticated["error"].get("message", authenticated["error"]))
            write_result({"ok": False, "error": f"authenticate failed: {message}"})
            print(f"authenticate failed: {message}", file=sys.stderr)
            return 3
        write_result({"ok": True, "method": AUTH_METHOD})
        print("Authenticated. You can close this window.")
        return 0
    except (EOFError, TimeoutError, OSError) as exc:
        write_result({"ok": False, "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 4
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        stderr.close()


if __name__ == "__main__":
    sys.exit(main())
