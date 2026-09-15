"""Real relay workers and FUSE over a synthetic WebSocket peer in disposable CI."""

from __future__ import annotations

import argparse
import base64
import errno
import hashlib
import json
import os
import queue
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

if __package__:
    from tests import physical_runtime_probe as kernel
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import physical_runtime_probe as kernel

from pawflow_relay.ws_frame import ws_recv

PROBE = "/opt/pawflow/physical_relay_probe.py"
MOUNTS = {"sfs": "/cc_sessions", "ffs": "/filestore", "skfs": "/skills"}


def send(sock, message):
    """The fixture sends unmasked server frames, as required by RFC 6455."""
    data = json.dumps(message).encode()
    length = len(data)
    header = bytes((0x81, length)) if length < 126 else (
        bytes((0x81, 126)) + struct.pack("!H", length))
    sock.sendall(header + data)


def receive(sock):
    opcode, payload = ws_recv(sock)
    kernel.check(opcode == 1, "Expected a JSON WebSocket text frame")
    return json.loads(payload)


class FixtureFs:
    """Only synthetic readonly files; no access to the server or host filesystem."""

    def __init__(self, name):
        self.name = name
        self.handles = {}
        self.next_handle = 1
        self.calls = []

    def handle(self, method, args):
        self.calls.append(method)
        tag, op = method.split(".", 1)
        if tag not in MOUNTS:
            return {"error": "ENOSYS", "errno": errno.ENOSYS}
        path = args.get("path", "")
        if op in ("getattr", "open"):
            if path not in ("/sentinel-1", "/sentinel-2"):
                return {"error": "ENOENT", "errno": errno.ENOENT}
            data = (self.name + ":" + tag + ":" + path[1:]).encode()
            if op == "getattr":
                return {"data": {
                    "st_mode": stat.S_IFREG | 0o444, "st_size": len(data),
                    "st_nlink": 1, "st_uid": 1000, "st_gid": 1000,
                    "st_atime": 1, "st_mtime": 1, "st_ctime": 1,
                }}
            if args["flags"] & (os.O_WRONLY | os.O_RDWR | os.O_TRUNC | os.O_CREAT):
                return {"error": "EROFS", "errno": errno.EROFS}
            handle = self.next_handle
            self.next_handle += 1
            self.handles[(tag, handle)] = data
            return {"data": {"fh": handle}}
        if op in ("read", "release"):
            key = (tag, args["fh"])
            if key not in self.handles:
                return {"error": "EBADF", "errno": errno.EBADF}
            if op == "release":
                del self.handles[key]
                return {"data": {}}
            data = self.handles[key]
            offset = args["offset"]
            return {"data": {"data_b64": base64.b64encode(
                data[offset:offset + args["size"]]).decode()}}
        if op == "readdir":
            return {"data": {"entries": ["sentinel-1", "sentinel-2"]}}
        return {"error": "EROFS", "errno": errno.EROFS}


class Peer:
    def __init__(self, sock, name):
        self.sock = sock
        self.name = name
        self.lock = threading.Lock()
        self.replies = queue.Queue()
        self.fs = FixtureFs(name)

    def send(self, message):
        with self.lock:
            send(self.sock, message)

    def command(self, action, **args):
        request_id = str(uuid.uuid4())
        self.send({"type": "command", "request_id": request_id,
                   "timestamp": time.time(), "action": action, **args})
        try:
            response = self.replies.get(timeout=30)
        except queue.Empty as error:
            raise RuntimeError(self.name + " timed out executing " + action) from error
        kernel.check(response.get("request_id") == request_id, "Mismatched command response")
        return response["data"]

    def disconnect(self):
        with self.lock:
            self.sock.sendall(bytes((0x88, 0)))


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock = self.request
        sock.settimeout(60)
        try:
            header = bytearray()
            while not header.endswith(b"\r\n\r\n"):
                piece = sock.recv(1)
                kernel.check(bool(piece) and len(header) < 16384, "Invalid HTTP upgrade")
                header.extend(piece)
            lines = header.decode("ascii").split("\r\n")
            headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
            key = next(value for name, value in headers.items()
                       if name.lower() == "sec-websocket-key")
            digest = hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(),
                usedforsecurity=False).digest()
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                          "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                          "Sec-WebSocket-Accept: " + base64.b64encode(digest).decode()
                          + "\r\n\r\n").encode())
            registration = receive(sock)
            name = registration.get("relay_id")
            kernel.check(name in ("alpha", "beta"), "Unknown logical identity")
            kernel.check(registration.get("type") == "register"
                         and registration.get("token") == "synthetic-token-" + name,
                         "Invalid synthetic registration")
            kernel.check(lines[0].split()[1] == "/ws/relay/" + name,
                         "Logical identity does not match its endpoint")
            peer = Peer(sock, name)
            peer.send({"type": "fence_snapshot", "highwaters": {}})
            kernel.check(receive(sock).get("type") == "fence_ack", "Missing fence acknowledgement")
            peer.send({"type": "registered", "relay_id": name})
            with self.server.changed:
                self.server.peers[name].append(peer)
                self.server.changed.notify_all()
            while True:
                msg = receive(sock)
                if msg.get("type") == "relay_request":
                    peer.send({"type": "relay_response", "request_id": msg["request_id"],
                               **peer.fs.handle(msg["method"], msg.get("args", {}))})
                elif msg.get("type") == "result":
                    peer.replies.put(msg)
                elif msg.get("type") == "ping":
                    peer.send({"type": "pong"})
        except (ConnectionError, OSError):
            pass
        except Exception as error:  # noqa: BLE001 - surface handler failures to the acceptance owner.
            with self.server.changed:
                self.server.errors.append(str(error))
                self.server.changed.notify_all()


class FixtureServer(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, Handler)
        self.changed = threading.Condition()
        self.peers = {"alpha": [], "beta": []}
        self.errors = []

    def wait_peers(self, generation, process):
        deadline = time.monotonic() + 75
        with self.changed:
            while any(len(peers) < generation for peers in self.peers.values()):
                kernel.check(not self.errors, "Fixture server failed: " + str(self.errors))
                kernel.check(process.poll() is None, "Supervisor exited before worker registration")
                remaining = deadline - time.monotonic()
                kernel.check(remaining > 0, "Timed out waiting for logical registrations")
                self.changed.wait(min(remaining, 0.5))
            return {name: peers[generation - 1] for name, peers in self.peers.items()}


def exports(endpoint):
    result = kernel.exports_for_round(1)
    for export in result:
        name = export["relay_id"]
        export["command"] = [
            sys.executable, "-u", "/opt/pawflow/pawflow_relay_launcher.py",
            "--server", endpoint + "/ws/relay/" + name,
            "--token=synthetic-token-" + name, "--relay-id", name,
            "--dir", "/workspace", "--allow-exec",
            "--server-mount", "/cc_sessions", "--filestore-mount", "/filestore",
            "--skills-mount", "/skills",
        ] + (["--readonly"] if export["mode"] == "ro" else [])
    return result


def inspect_mount():
    lines = Path("/proc/self/mountinfo").read_text().splitlines()
    mounts = [line for line in lines if " /tmp/pf_combined_fs " in line
              and " - fuse" in line]
    kernel.check(len(mounts) == 1, "Expected one real combined FUSE mount")
    return {"mount": mounts[0], "namespace": os.readlink("/proc/self/ns/mnt"),
            "profile": Path("/home/pawflow/.chromium-profile/sentinel").read_text()}


def exercise(peers, generation):
    evidence = {}
    for name, peer in peers.items():
        workspace = peer.command("hash_file", path="/workspace/sentinel")
        kernel.check(workspace.get("sha256") == hashlib.sha256(name.encode()).hexdigest(),
                     "Wrong logical workspace over WebSocket: " + str(workspace))
        for tag, mount in MOUNTS.items():
            path = "/sentinel-" + str(generation)
            result = peer.command("hash_file", path=mount + path)
            expected = (name + ":" + tag + ":" + path[1:]).encode()
            kernel.check(result.get("sha256") == hashlib.sha256(expected).hexdigest(),
                         "Wrong FUSE contents for " + name + ":" + tag + ": " + str(result))
            kernel.check(tag + ".read" in peer.fs.calls,
                         "No real FUSE read crossed the current WebSocket: " + tag)
        denied = peer.command("hash_file", path="/filestore/sibling-only")
        kernel.check(denied.get("ok") is False, "Missing FUSE file did not fail")
        denied = peer.command("hash_file", path="/workspace/sentinel", local=True)
        kernel.check(denied.get("ok") is False, "Unconfigured host access was allowed")
        evidence[name] = {"fuse_methods": sorted(set(peer.fs.calls))}
    readonly = peers["beta"].command("write_file", path="/workspace/forbidden", content="denied")
    kernel.check(readonly.get("ok") is False and "readonly" in readonly.get("error", ""),
                 "Readonly worker accepted a write: " + str(readonly))
    result = peers["alpha"].command(
        "exec", path="/workspace", argv=[sys.executable, "-I", PROBE, "--inspect"], timeout=15)
    kernel.check(result.get("returncode") == 0, "Worker exec failed: " + str(result))
    evidence["alpha"]["inspection"] = json.loads(result["stdout"])
    return evidence


def run_acceptance():
    kernel.require_disposable()
    kernel.check(os.geteuid() == 0, "Acceptance supervisor must start as root")
    kernel.check(not kernel.STAGING.exists(), "Acceptance staging must be fresh")
    kernel.OUTPUT.mkdir(exist_ok=True)
    for name in ("alpha", "beta"):
        root = kernel.source(name)
        (root / "workspace").mkdir(parents=True)
        (root / "workspace/sentinel").write_text(name)
        profile = root / "home/.chromium-profile"
        profile.mkdir(parents=True)
        (profile / "sentinel").write_text("profile-" + name)
    address = socket.gethostbyname(socket.gethostname())
    server = FixtureServer((address, 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    completed = []
    try:
        with (kernel.OUTPUT / "relay-runtime.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-I", kernel.RUNTIME],
                                       stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            try:
                endpoint = "ws://" + address + ":" + str(server.server_address[1])
                process.stdin.write(json.dumps({"exports": exports(endpoint)}).encode())
                process.stdin.close()
                for generation in (1, 2):
                    peers = server.wait_peers(generation, process)
                    completed.append(exercise(peers, generation))
                    if generation == 1:
                        for peer in peers.values():
                            peer.disconnect()
                first = completed[0]["alpha"]["inspection"]
                second = completed[1]["alpha"]["inspection"]
                kernel.check(first == second, "FUSE mount or profile changed across reconnect")
                kernel.check(first["profile"] == "profile-alpha", "Profile sentinel changed")
                owned = kernel.descendants(process.pid, kernel.process_table())
                kernel.check(len(owned) >= 7, "Missing logical worker/helper descendants")
                process.send_signal(signal.SIGTERM)
                kernel.check(process.wait(timeout=30) == 0, "Supervisor stop failed")
                kernel.wait_cleanup(owned)
                kernel.check(not server.errors, "Fixture server failed: " + str(server.errors))
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=90)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
        kernel.publish(kernel.OUTPUT / "relay-result.json",
                       {"status": "passed", "generations": completed,
                        "owned_processes": owned})
    except Exception as error:
        kernel.publish(kernel.OUTPUT / "relay-result.json",
                       {"status": "failed", "error": str(error), "generations": completed})
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    if args.inspect:
        print(json.dumps(inspect_mount()))
    else:
        raise SystemExit(run_acceptance())
