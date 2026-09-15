"""Exercise the production physical client and host helpers on disposable Windows."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pawflow_relay.physical_thread import PhysicalRelayThread
from pawflow_relay.utils import docker_cmd, get_host_ip
from pawflow_relay.ws_frame import ws_recv
from tests.physical_relay_probe import MOUNTS, Peer, receive


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def send(peer, payload):
    peer.send({"message_id": str(uuid.uuid4()), "timestamp": time.time(), **payload})


class Handler(BaseHTTPRequestHandler):
    rbufsize = 0

    def log_message(self, *_args):
        pass

    def do_POST(self):
        check(self.path == "/api/ui", "Unexpected fixture API path")
        check(self.headers.get("Authorization") == "Bearer synthetic-session",
              "Missing fixture API authentication")
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        action = body["action"]
        with self.server.changed:
            if action == "service_install":
                name = body["service_name"]
                check(name in self.server.peers, "Only logical services may register")
                config = dict(item.split("=", 1) for item in body["config_str"].split(","))
                check(bool(config["token"]), "Logical registration token is empty")
                self.server.tokens[name] = config["token"]
                self.server.actions.append([action, name])
                result = {"ok": True}
            elif action == "service_uninstall":
                name = body["service_id"]
                check(name in self.server.peers, "Only logical services may unregister")
                self.server.tokens.pop(name, None)
                self.server.actions.append([action, name])
                result = {"ok": True}
            elif action == "relay_list_available":
                result = {"relays": [
                    {"relay_id": name, "connected": any(p in self.server.live for p in peers)}
                    for name, peers in self.server.peers.items()
                ]}
            else:
                raise RuntimeError("Unexpected fixture action: " + action)
        data = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        peer = None
        try:
            check(self.headers.get("Upgrade", "").lower() == "websocket", "Expected upgrade")
            key = self.headers["Sec-WebSocket-Key"]
            digest = hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(),
                usedforsecurity=False).digest()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", base64.b64encode(digest).decode())
            self.end_headers()
            self.wfile.flush()
            registration = receive(self.connection)
            name = registration["relay_id"]
            with self.server.changed:
                check(name in self.server.tokens, "Uninstalled logical relay")
                check(registration.get("type") == "register"
                      and registration.get("token") == self.server.tokens[name],
                      "Logical WebSocket authentication failed")
            check(self.path == "/ws/relay/" + name, "Wrong logical WebSocket endpoint")
            check(registration["info"]["root"] == "/workspace", "Wrong logical workspace")
            peer = Peer(self.connection, name)
            peer.info = registration["info"]
            send(peer, {"type": "fence_snapshot", "highwaters": {}})
            check(receive(self.connection).get("type") == "fence_ack", "Missing fence ACK")
            send(peer, {"type": "registered", "relay_id": name})
            with self.server.changed:
                self.server.live.add(peer)
                self.server.peers[name].append(peer)
                self.server.changed.notify_all()
            while True:
                opcode, payload = ws_recv(self.connection)
                if opcode in (None, 8):
                    break
                check(opcode == 1, "Unexpected WebSocket frame")
                msg = json.loads(payload)
                if msg["type"] == "relay_request":
                    send(peer, {"type": "relay_response", "request_id": msg["request_id"],
                                **peer.fs.handle(msg["method"], msg.get("args", {}))})
                elif msg["type"] == "result":
                    peer.replies.put(msg)
                elif msg["type"] == "ping":
                    send(peer, {"type": "pong"})
        except (ConnectionError, OSError):
            pass
        except Exception as error:  # noqa: BLE001 - report fixture failures to its owner.
            with self.server.changed:
                self.server.errors.append(str(error))
                self.server.changed.notify_all()
        finally:
            if peer is not None:
                with self.server.changed:
                    self.server.live.discard(peer)
                    self.server.changed.notify_all()
            self.close_connection = True


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("0.0.0.0", 0), Handler)
        self.changed = threading.Condition()
        self.tokens = {}
        self.peers = {"alpha": [], "beta": []}
        self.live = set()
        self.errors = []
        self.actions = []

    def wait_peers(self, generation, relay):
        deadline = time.monotonic() + 120
        with self.changed:
            while any(len(peers) < generation for peers in self.peers.values()):
                check(not self.errors, "Fixture failed: " + str(self.errors))
                check(relay._thread.is_alive(), "Physical client loop exited")
                remaining = deadline - time.monotonic()
                check(remaining > 0, "Timed out waiting for native physical client")
                self.changed.wait(min(remaining, 0.5))
            return {name: peers[generation - 1] for name, peers in self.peers.items()}

    def wait_disconnected(self):
        deadline = time.monotonic() + 10
        with self.changed:
            while self.live:
                remaining = deadline - time.monotonic()
                check(remaining > 0, "Logical WebSocket remains after group stop")
                self.changed.wait(remaining)


def docker(*args):
    return subprocess.check_output(docker_cmd() + list(args), text=True, timeout=30).strip()


def snapshot(relay):
    owners = []
    for helper in [relay, *relay.members[1:]]:
        bridge = helper._host_bridge_proc
        thread = helper._host_helper_thread
        check(thread.is_alive() and bridge is not None and bridge.poll() is None,
              "Missing real Windows helper or WSL bridge")
        port = int(bridge.args[bridge.args.index("--target-port") + 1])
        bridge_port = int(bridge.args[bridge.args.index("--listen-port") + 1])
        owners.append((thread, bridge, port, bridge_port))
    check(relay._host_helper_token != relay.members[1]._host_helper_token,
          "Logical helper capabilities are shared")
    return {"owners": owners, "name": relay._docker_container,
            "container": docker("inspect", "--format", "{{.Id}}", relay._docker_container)}


def check_retired(before):
    for thread, bridge, _port, _bridge_port in before["owners"]:
        check(not thread.is_alive(), "Retired host helper thread remains")
        check(bridge.poll() is not None, "Retired WSL bridge process remains")
    check(not docker("ps", "-aq", "--filter", "id=" + before["container"]),
          "Retired group container remains")


def host_request(port, token, action):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        request = {"action": action, "_host_helper_token": token,
                   "message_id": str(uuid.uuid4()), "timestamp": time.time()}
        connection.sendall((json.dumps(request) + chr(10)).encode())
        with connection.makefile("rb") as stream:
            return json.loads(stream.readline())


def exercise(peers, relay, generation):
    before = snapshot(relay)
    evidence = {}
    for name, peer in peers.items():
        workspace = peer.command("hash_file", path="/workspace/sentinel")
        expected = hashlib.sha256(name.encode()).hexdigest()
        check(workspace.get("sha256") == expected, "Wrong private native workspace")
        local = peer.command("hash_file", path="sentinel", local=True)
        check(local.get("sha256") == expected, "Windows host filesystem forwarding failed")
        local_exec = peer.command(
            "exec", path=".", local=True,
            argv=[sys.executable, "-I", "-c", "import sys; print(sys.platform)"], timeout=15)
        if name == "alpha":
            check(local_exec.get("returncode") == 0 and local_exec["stdout"].strip() == "win32",
                  "Host command did not execute on native Windows")
        else:
            check(local_exec == {"ok": False,
                                 "error": "Host action requires allow_local and allow_exec"},
                  "Expected host execution permission denial: " + str(local_exec))
        for tag, mount in MOUNTS.items():
            result = peer.command("hash_file", path=mount + "/sentinel-1")
            expected_fuse = hashlib.sha256((name + ":" + tag + ":sentinel-1").encode()).hexdigest()
            check(result.get("sha256") == expected_fuse and tag + ".read" in peer.fs.calls,
                  "Native client FUSE traffic failed")
        # The filesystem protocol intentionally cannot address private HOME paths.
        # Inspect only this owned container's per-member mounts as the worker UID.
        home = next(export.home_mount for export in relay.plan.exports if export.relay_id == name)
        script = (
            "import hashlib, sys\n"
            "from pathlib import Path\n"
            "profile = Path(sys.argv[1]) / '.chromium-profile/sentinel'\n"
            "if sys.argv[2] == '1':\n"
            "    profile.parent.mkdir(parents=True, exist_ok=True)\n"
            "    profile.write_text(sys.argv[3], encoding='utf-8')\n"
            "print(hashlib.sha256(profile.read_bytes()).hexdigest())\n"
        )
        digest = docker("exec", "--user", "1000:1000", before["container"],
                        "python3", "-I", "-c", script, home, str(generation), "profile-" + name)
        check(digest == hashlib.sha256(("profile-" + name).encode()).hexdigest(),
              "Profile was lost across physical restart")
        evidence[name] = {"root": peer.info["root"], "host_root": peer.info["host_root"],
                          "fuse_methods": sorted(set(peer.fs.calls)),
                          "host_filesystem": True, "host_exec": name == "alpha",
                          "profile": "profile-" + name}
    for index, (_thread, _bridge, port, _bridge_port) in enumerate(before["owners"]):
        helper = relay if index == 0 else relay.members[1]
        sibling = relay.members[1] if index == 0 else relay
        check(host_request(port, helper._host_helper_token, "host_helper_ping")
              == {"type": "result", "data": {"ok": True}}, "Helper authentication failed")
        denied = host_request(port, sibling._host_helper_token, "host_helper_ping")
        check(denied.get("type") == "error", "Sibling helper capability was accepted")
    return evidence, before


def stop(relay, before, server):
    relay.stop()
    relay._thread.join(timeout=15)
    check(not relay._thread.is_alive(), "Physical reconnect loop remains after stop")
    check_retired(before)
    for _thread, _bridge, port, bridge_port in before["owners"]:
        with socket.socket() as connection:
            connection.settimeout(2)
            check(connection.connect_ex(("127.0.0.1", port)) != 0, "Host listener remains")
        # Probe the actual WSL listener, not Windows localhost forwarding.
        code = ("import socket; s=socket.socket(); s.settimeout(2); "
                "raise SystemExit(1 if s.connect_ex(('127.0.0.1', "
                + str(bridge_port) + ")) == 0 else 0)")
        subprocess.run(["wsl", "--exec", "python3", "-c", code], check=True, timeout=10)
    server.wait_disconnected()
    check(not server.tokens, "Logical services remain installed")
    check(not server.errors, "Fixture protocol failed: " + str(server.errors))


def run_acceptance(output, image):
    check(os.name == "nt" and os.environ.get("PAWFLOW_DISPOSABLE_ACCEPTANCE") == "1",
          "Run only on an explicitly disposable Windows runner")
    check(docker_cmd() == ["wsl", "docker"], "Native Windows Docker default required")
    physical = {"physical_id": "native-windows-acceptance", "name": "Native acceptance",
                "workspaces": []}
    for name in ("alpha", "beta"):
        directory = output / ("native " + name + " workspace")
        directory.mkdir()
        (directory / "sentinel").write_text(name, encoding="utf-8")
        physical["workspaces"].append({
            "name": name, "relay_id": name, "path": str(directory),
            "server": "fixture", "docker_image": image, "mode": "rw",
            "allow_local": True, "allow_exec": name == "alpha",
            "allow_remote_desktop": False, "allow_service_tunnels": False,
        })
    server = FixtureServer()
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    credentials = {"url": "http://" + get_host_ip() + ":" + str(server.server_port),
                   "session_token": "synthetic-session", "username": "acceptance"}
    relay = None
    completed = []
    before = None
    try:
        for generation in range(1, 5):
            if generation in (1, 4):
                relay = PhysicalRelayThread(physical, credentials)
                relay.log_file = str(output / "native-client-runtime.log")
                relay.start()
            peers = server.wait_peers(generation, relay)
            evidence, current = exercise(peers, relay, generation)
            if before is not None:
                check_retired(before)
                check(before["container"] != current["container"], "Container was not replaced")
            completed.append({"generation": generation, "container": current["container"],
                              "relays": evidence, "helper_count": len(current["owners"])})
            before = current
            if generation == 1:
                # Stop the exact beta helper owner and let the real retry loop recover.
                relay.members[1]._host_helper_stop_event.set()
                before["owners"][1][0].join(timeout=5)
                check(not before["owners"][1][0].is_alive(), "Injected helper stop failed")
            elif generation == 2:
                # EOF ends the exact tracked WSL bridge; no process-name matching.
                before["owners"][1][1].stdin.close()
                before["owners"][1][1].wait(timeout=10)
            else:
                stop(relay, before, server)
        result = {"status": "passed", "platform": sys.platform, "generations": completed,
                  "helper_failure_recovered": True, "bridge_failure_recovered": True,
                  "explicit_group_restart": True, "helpers_and_containers_removed": True,
                  "actions": server.actions}
    except BaseException as error:
        result = {"status": "failed", "error": str(error), "generations": completed}
        raise
    finally:
        try:
            if relay is not None:
                relay.stop()
                if relay._thread is not None:
                    relay._thread.join(timeout=15)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
        except BaseException as error:
            result = {"status": "failed", "error": "Cleanup failed: " + str(error),
                      "generations": completed}
            raise
        finally:
            (output / "native-client-result.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    run_acceptance(Path(os.environ["PAWFLOW_WSL_OUTPUT"]).resolve(),
                   "pawflow-wsl-acceptance:local")
