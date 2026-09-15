"""Production HTTP/relay/storage acceptance in a fresh disposable process."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from pawflow_relay.ws_frame import ws_recv, ws_send


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


class WireClient:
    """Small real WebSocket client for inverse filesystem acceptance."""

    def __init__(self, server, name, session):
        self.sock = socket.create_connection(server.address, timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /ws/relay/{name} HTTP/1.1\r\nHost: localhost\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Cookie: pawflow_token={session}\r\n\r\n"
        )
        try:
            self.sock.sendall(request.encode())
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                chunk = self.sock.recv(1)
                check(bool(chunk) and len(header) < 16384, "Incomplete HTTP upgrade")
                header.extend(chunk)
            self.status = int(header.split(b" ", 2)[1])
        except BaseException:
            self.close()
            raise

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()

    def send(self, message):
        message = {"message_id": str(uuid.uuid4()), "timestamp": time.time(), **message}
        ws_send(self.sock, json.dumps(message).encode())

    def receive(self):
        for _ in range(50):
            opcode, payload = ws_recv(self.sock)
            check(opcode == 1, "Expected a server JSON frame")
            message = json.loads(payload)
            kind = message.get("type")
            if kind == "command":
                # The socket-only worker has an empty synthetic project.
                check(message["action"] in ("project_context", "key_pubkey_get"),
                      "Unexpected command in socket-only fixture: " + message["action"])
                data = {} if message["action"] == "project_context" else {
                    "ok": False, "error": "Synthetic socket worker has no key",
                }
                self.send({"type": "result", "request_id": message["request_id"],
                           "data": {"ok": True, "data": data}})
            elif kind in ("remote_mount_manifest", "pong"):
                continue
            else:
                return message
        raise RuntimeError("Too many unsolicited frames")

    def register(self, name, token):
        check(self.status == 101, "Authenticated HTTP upgrade failed")
        self.send({"type": "register", "relay_id": name, "token": token})
        response = self.receive()
        if response.get("type") == "error":
            return response
        check(response.get("type") == "fence_snapshot", "Missing production fence")
        self.send({"type": "fence_ack"})
        return self.receive()

    def request(self, method, **args):
        request_id = str(uuid.uuid4())
        self.send({"type": "relay_request", "request_id": request_id,
                   "method": method, "args": args})
        response = self.receive()
        check(response.get("type") == "relay_response"
              and response.get("request_id") == request_id, "Crossed inverse response")
        return response

    def read(self, tag, path):
        opened = self.request(tag + ".open", path=path, flags=os.O_RDONLY)
        check("data" in opened, "Owned file open refused: " + tag + ":" + path)
        handle = opened["data"]["fh"]
        try:
            response = self.request(tag + ".read", fh=handle, offset=0, size=4096)
            check("data" in response, "Owned file read refused")
            return base64.b64decode(response["data"]["data_b64"])
        finally:
            check("data" in self.request(tag + ".release", fh=handle),
                  "File handle release refused")


class ProductionServer:
    """Real listener, session manager and per-user RelayServices; no handler mocks."""

    def __init__(self, host):
        from core import paths
        from core.file_store import FileStore
        from core.security import SecurityManager
        from services.filesystem_service import RelayService
        from services.http_listener_service import HTTPListenerService

        self.services = {}
        self.sessions = {}
        self.files = {}
        self.security = SecurityManager.get_instance()
        self.store = FileStore.instance()
        for name in ("alpha", "beta"):
            self.security.create_user(name, "synthetic-password-" + name)
            session = self.security.authenticate(name, "synthetic-password-" + name)
            check(session is not None, "Synthetic login failed")
            self.sessions[name] = session.session_id
            self.files[name] = {}
            for generation in (1, 2):
                content = f"{name}:generation:{generation}".encode()
                session_path = paths.CLAUDE_SESSIONS_DIR / name / "shared/agent"
                session_path.mkdir(parents=True, exist_ok=True)
                (session_path / f"sentinel-{generation}").write_bytes(content)
                private = paths.CLAUDE_SESSIONS_DIR / name / ("private-" + name)
                private.mkdir(exist_ok=True)
                (private / "sentinel").write_bytes(content)
                skill = paths.REPOSITORY_DIR / "skills/users" / name / "probe"
                skill.mkdir(parents=True, exist_ok=True)
                (skill / f"sentinel-{generation}").write_bytes(content)
                file_id = self.store.store(
                    f"sentinel-{generation}", content, user_id=name,
                    conversation_id="conv-" + name)
                self.files[name][generation] = (
                    f"/conv-{name}/{file_id}/sentinel-{generation}")
            self.services[name] = RelayService({
                "_service_id": name, "_scope": "user", "_scope_id": name,
                "token": "synthetic-token-" + name,
            })
        self.listener = HTTPListenerService({"host": host, "port": 0})
        self.listener.connect()
        self.address = self.listener._server.server_address
        for service in self.services.values():
            service.connect()

    def paths(self, name, generation):
        return {
            "sfs": f"/shared/agent/sentinel-{generation}",
            "ffs": self.files[name][generation],
            "skfs": f"/users/{name}/probe/sentinel-{generation}",
        }

    def forbidden(self, name, generation):
        other = "beta" if name == "alpha" else "alpha"
        return {
            "sfs": "/private-" + other + "/sentinel",
            "ffs": self.files[other][generation],
            "skfs": f"/users/{other}/probe/sentinel-{generation}",
        }

    def close(self):
        for service in self.services.values():
            with service._relay_pool_lock:
                connections = list(service._relay_pool)
            for connection in connections:
                connection["loop"].call_soon_threadsafe(connection["writer"].close)
        for service in self.services.values():
            service.disconnect()
        self.listener.disconnect()

    def check_authentication(self):
        for session in ("", "invalid-session"):
            client = WireClient(self, "alpha", session)
            try:
                check(client.status == 401, "Missing/invalid session was accepted")
            finally:
                client.close()
        for identity, token, reason in (
            ("alpha", "synthetic-token-beta", "Token mismatch"),
            ("beta", "synthetic-token-alpha", "Relay identity mismatch"),
            ("", "synthetic-token-alpha", "Relay identity mismatch"),
        ):
            client = WireClient(self, "alpha", self.sessions["alpha"])
            try:
                response = client.register(identity, token)
                check(response == {"type": "error", "message": reason},
                      "Invalid relay registration was accepted")
            finally:
                client.close()
        return {"session_denials": 2, "registration_denials": 3}

    def socket_acceptance(self):
        authentication = self.check_authentication()
        reports = []
        for generation in (1, 2):
            for name in ("alpha", "beta"):
                client = WireClient(self, name, self.sessions[name])
                try:
                    check(client.register(name, "synthetic-token-" + name)
                          == {"type": "registered", "relay_id": name},
                          "Valid logical identity was not registered")
                    expected = f"{name}:generation:{generation}".encode()
                    other = "beta" if name == "alpha" else "alpha"
                    for tag, path in self.paths(name, generation).items():
                        check(client.read(tag, path) == expected, "Wrong owner's storage bytes")
                    for tag, path in self.forbidden(name, generation).items():
                        response = client.request(tag + ".getattr", path=path, user_id=other)
                        check(response.get("errno") in (2, 13),
                              "Foreign storage was accessible: " + tag)
                    for path in (f"../{other}/private-{other}/sentinel", "/../../etc/passwd"):
                        check(client.request("sfs.getattr", path=path).get("errno") == 13,
                              "Server-session traversal was accepted")
                    reports.append({"name": name, "generation": generation,
                                    "reads": 3, "foreign_denials": 3, "traversal_denials": 2})
                finally:
                    client.close()
        return {"status": "passed", "authentication": authentication, "rounds": reports}

    def wait_workers(self, process, previous):
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            check(process.poll() is None, "Grouped supervisor exited before registration")
            ready = True
            for name, service in self.services.items():
                with service._relay_pool_lock:
                    pool = list(service._relay_pool)
                if not pool or pool[-1]["connected_at"] <= previous.get(name, 0):
                    ready = False
                    break
            if ready:
                return {name: service._relay_pool[-1]["connected_at"]
                        for name, service in self.services.items()}
            time.sleep(0.05)
        raise RuntimeError("Production server did not register both logical workers")


def mounted_acceptance(server):
    from tests import physical_relay_probe as relay
    from tests import physical_runtime_probe as kernel

    kernel.require_disposable()
    check(os.geteuid() == 0 and not kernel.STAGING.exists(), "Fresh root fixture required")
    for name in ("alpha", "beta"):
        root = kernel.source(name)
        (root / "workspace").mkdir(parents=True)
        (root / "workspace/sentinel").write_text(name)
        profile = root / "home/.chromium-profile"
        profile.mkdir(parents=True)
        (profile / "sentinel").write_text("profile-" + name)
    endpoint = "ws://" + server.address[0] + ":" + str(server.address[1])
    exports = relay.exports(endpoint)
    for export in exports:
        export["command"].extend(["--session-token", server.sessions[export["relay_id"]]])
    reports = []
    with (kernel.OUTPUT / "server-runtime.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-I", kernel.RUNTIME],
            stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
        try:
            process.stdin.write(json.dumps({"exports": exports}).encode())
            process.stdin.close()
            previous = {name: time.monotonic() for name in server.services}
            for generation in (1, 2):
                previous = server.wait_workers(process, previous)
                report = {}
                for name, service in server.services.items():
                    expected = f"{name}:generation:{generation}".encode()
                    for tag, path in server.paths(name, generation).items():
                        result = service._request(
                            "hash_file", path=relay.MOUNTS[tag] + path,
                            _request_timeout=15, _retry_on_disconnect=False)
                        check(result.get("sha256") == hashlib.sha256(expected).hexdigest(),
                              "Production mounted storage bytes mismatch")
                    for tag, path in server.forbidden(name, generation).items():
                        try:
                            service._request(
                                "hash_file", path=relay.MOUNTS[tag] + path,
                                _request_timeout=15, _retry_on_disconnect=False)
                        except Exception as error:  # noqa: BLE001 - validate the actual denial.
                            check("not found" in str(error).lower()
                                  or "no such" in str(error).lower()
                                  or "permission" in str(error).lower(),
                                  "Unexpected foreign-read failure: " + str(error))
                        else:
                            raise RuntimeError("Foreign mounted storage was accessible")
                    result = service._request(
                        "exec", path="/workspace", argv=[
                            sys.executable, "-c",
                            ("from pathlib import Path; "
                             "assert not Path('/run/pawflow-server-data').exists()")],
                        timeout=10, _request_timeout=15, _retry_on_disconnect=False)
                    check(result.get("returncode") == 0, "Raw server data exposed to worker")
                    report[name] = {"reads": 3, "foreign_denials": 3,
                                    "owner": service._user_id}
                reports.append(report)
                if generation == 1:
                    for service in server.services.values():
                        with service._relay_pool_lock:
                            connections = list(service._relay_pool)
                        for connection in connections:
                            connection["loop"].call_soon_threadsafe(connection["writer"].close)
            owned = kernel.descendants(process.pid, kernel.process_table())
            check(len(owned) >= 7, "Missing owned workers")
            process.send_signal(signal.SIGTERM)
            check(process.wait(timeout=30) == 0, "Group shutdown failed")
            kernel.wait_cleanup(owned)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    return {"status": "passed", "generations": reports, "owned_processes": owned}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--mounted", action="store_true")
    args = parser.parse_args()
    root = Path(args.state_dir).resolve()
    check(not root.exists(), "Server acceptance requires a fresh state directory")
    root.mkdir(parents=True)
    os.environ["PAWFLOW_DATA_DIR"] = str(root)
    os.environ.pop("PAWFLOW_INTERNAL_TOKEN", None)
    host = socket.gethostbyname(socket.gethostname()) if args.mounted else "127.0.0.1"
    server = ProductionServer(host)
    try:
        report = server.socket_acceptance()
        if args.mounted:
            report["mounted"] = mounted_acceptance(server)
        print(json.dumps(report, sort_keys=True))
        if args.mounted:
            from tests import physical_runtime_probe as kernel
            kernel.publish(kernel.OUTPUT / "server-result.json", report)
    finally:
        server.close()


if __name__ == "__main__":
    main()
