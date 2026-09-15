"""Real static-runtime checks, executed only in an explicitly disposable container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

RUNTIME = "/opt/pawflow/pawflow_relay/_physical_runtime.py"
PROBE = "/opt/pawflow/physical_runtime_probe.py"
STAGING = Path("/run/pawflow-physical")
OUTPUT = Path("/acceptance-output")


def require_disposable() -> None:
    if os.environ.get("PAWFLOW_DISPOSABLE_ACCEPTANCE") != "1" or not Path("/.dockerenv").is_file():
        raise RuntimeError("Run only in an explicitly disposable acceptance container")


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def source(name):
    return STAGING / hashlib.sha256(name.encode()).hexdigest()


def exports_for_round(round_number):
    return [{
        "relay_id": name,
        "root_mount": str(source(name) / "workspace"),
        "home_mount": str(source(name) / "home"),
        "mode": mode,
        "command": [sys.executable, "-I", PROBE, "--worker", name, "--mode", mode,
                    "--round", str(round_number)],
        "environment": {"PAWFLOW_DISPOSABLE_ACCEPTANCE": "1",
                        "PAWFLOW_PROBE_TOKEN": "synthetic-token-" + name},
    } for name, mode in (("alpha", "rw"), ("beta", "ro"))]


def publish(path, result):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
    temporary.replace(path)


def worker_probe(name, mode, round_number):
    home = Path("/home/pawflow")
    peer = "beta" if name == "alpha" else "alpha"
    check(os.getcwd() == "/workspace", "Worker did not start at literal /workspace")
    check(Path("/workspace/sentinel").read_text() == name, "Wrong workspace view")
    check((home / ".chromium-profile/sentinel").read_text() == "profile-" + name,
          "Persistent profile sentinel changed")
    check(not Path("/.physical-old-root").exists(), "Original root remains reachable")
    check(not (source(peer) / "workspace/sentinel").exists(), "Sibling staging is exposed")
    check(not Path("/var/run/docker.sock").exists(), "Docker socket is exposed")
    forbidden = ("synthetic-token-" + peer).encode()
    for path in Path("/proc").glob("[0-9]*/environ"):
        try:
            environment = path.read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        check(forbidden not in environment, "Sibling credential is visible")
    namespaces = {kind: os.readlink("/proc/self/ns/" + kind)
                  for kind in ("mnt", "pid", "user", "net", "ipc")}
    for target in ("/opt/pawflow",) + (("/workspace",) if mode == "ro" else ()):
        result = subprocess.run(
            ["sudo", "-n", "mount", "-o", "remount,bind,rw", target],
            capture_output=True, text=True, timeout=10, check=False)
        check(result.returncode != 0, "Privileged remount succeeded: " + target)
    if mode == "rw":
        path = Path("/workspace/written")
        path.write_text("round-" + str(round_number))
        link = Path("/workspace/link-" + str(round_number))
        link.symlink_to(path.name)
        check(link.read_text() == path.read_text(), "Symlink access failed")
        executable = Path("/workspace/executable")
        executable.write_text("#!/bin/sh\nprintf executable-ok\n")
        executable.chmod(0o755)
        result = subprocess.run([str(executable)], capture_output=True, text=True,
                                timeout=10, check=True)
        check(result.stdout == "executable-ok", "Workspace execution failed")
    else:
        result = subprocess.run(["sudo", "-n", "touch", "/workspace/forbidden"],
                                capture_output=True, timeout=10, check=False)
        check(result.returncode != 0, "Readonly workspace accepted a privileged write")
    check(socket.getaddrinfo("github.com", 443), "Private DNS resolution failed")
    report = {"name": name, "mode": mode, "round": round_number,
              "workspace": str(Path.cwd()), "profile": "profile-" + name,
              "namespaces": namespaces, "uid": os.getuid()}
    publish(home / ("probe-" + str(round_number) + ".json"), report)
    stop = home / ("exit-" + str(round_number))
    while not stop.exists():
        time.sleep(0.1)
    return 19


def process_table():
    result = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            text = path.read_text()
            fields = text[text.rindex(")") + 2:].split()
            result[int(path.parent.name)] = {"parent": int(fields[1]), "start": fields[19]}
        except (FileNotFoundError, ProcessLookupError):
            continue
    return result


def descendants(root_pid, table):
    owned = {root_pid}
    while True:
        children = {pid for pid, info in table.items() if info["parent"] in owned}
        expanded = owned | children
        if expanded == owned:
            return {pid: table[pid]["start"] for pid in owned if pid in table}
        owned = expanded


def wait_reports(process, round_number):
    paths = [source(name) / "home" / ("probe-" + str(round_number) + ".json")
             for name in ("alpha", "beta")]
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        if all(path.exists() for path in paths):
            reports = [json.loads(path.read_text()) for path in paths]
            for kind in ("mnt", "pid", "user", "net", "ipc"):
                check(reports[0]["namespaces"][kind] != reports[1]["namespaces"][kind],
                      "Workers share namespace: " + kind)
            return reports
        check(process.poll() is None, "Supervisor exited before both probes completed")
        time.sleep(0.1)
    raise RuntimeError("Timed out waiting for both real worker probes")


def wait_cleanup(owned):
    deadline = time.monotonic() + 5
    while True:
        current = process_table()
        remaining = [pid for pid, start in owned.items()
                     if pid in current and current[pid]["start"] == start]
        if not remaining:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Owned processes survived group shutdown: " + str(remaining))
        time.sleep(0.05)


def run_acceptance():
    require_disposable()
    check(os.geteuid() == 0, "The disposable supervisor must start as root")
    check(not STAGING.exists(), "Acceptance staging must be fresh")
    OUTPUT.mkdir(exist_ok=True)
    import pwd

    owner = pwd.getpwnam("pawflow")
    for name in ("alpha", "beta"):
        root = source(name)
        workspace, home = root / "workspace", root / "home"
        workspace.mkdir(parents=True)
        home.mkdir()
        (workspace / "sentinel").write_text(name)
        profile = home / ".chromium-profile"
        profile.mkdir()
        (profile / "sentinel").write_text("profile-" + name)
        for path in [home, profile, profile / "sentinel"]:
            os.chown(path, owner.pw_uid, owner.pw_gid)
    completed = []
    try:
        for round_number in (1, 2):
            with (OUTPUT / ("runtime-" + str(round_number) + ".log")).open("wb") as log:
                process = subprocess.Popen(
                    [sys.executable, "-I", RUNTIME], stdin=subprocess.PIPE,
                    stdout=log, stderr=subprocess.STDOUT)
                try:
                    process.stdin.write(json.dumps({"exports": exports_for_round(round_number)}).encode())
                    process.stdin.close()
                    reports = wait_reports(process, round_number)
                    owned = descendants(process.pid, process_table())
                    check(len(owned) >= 7, "Expected worker and helper descendants were not observed")
                    if round_number == 1:
                        (source("alpha") / "home/exit-1").touch()
                    else:
                        process.send_signal(signal.SIGTERM)
                    code = process.wait(timeout=30)
                    check(code != 0 if round_number == 1 else code == 0,
                          "Unexpected supervisor exit status: " + str(code))
                    wait_cleanup(owned)
                    completed.append({"round": round_number, "workers": reports,
                                      "exit_code": code, "owned_processes": owned})
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=90)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
        publish(OUTPUT / "result.json", {"status": "passed", "rounds": completed})
    except Exception as error:
        publish(OUTPUT / "result.json", {"status": "failed", "error": str(error),
                                        "completed_rounds": completed})
        raise
    print(json.dumps({"status": "passed", "rounds": len(completed)}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("alpha", "beta"))
    parser.add_argument("--mode", choices=("ro", "rw"))
    parser.add_argument("--round", type=int)
    args = parser.parse_args()
    if args.worker:
        return worker_probe(args.worker, args.mode, args.round)
    return run_acceptance()


if __name__ == "__main__":
    raise SystemExit(main())
