"""Private launch payload and Docker arguments for a server physical relay."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from core._relay_naming import _relay_runtime_host_dir
from core.server_physical_config import scope_directory
from pawflow_relay._physical_runtime import SECCOMP_PROFILE


def runtime_directory(record: dict) -> Path:
    return scope_directory(record["scope"], record["scope_id"]) / (
        hashlib.sha256(record["physical_id"].encode("utf-8")).hexdigest() + "-runtime")


def launch_file(record: dict) -> Path:
    return runtime_directory(record) / "launch.json"


def _write_private(filename: Path, payload: dict) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".launch-", dir=filename.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, filename)
    finally:
        Path(temporary).unlink(missing_ok=True)


def group_command(original: list[str], image: str, record: dict) -> list[str]:
    """Keep the common resource budget and put each credential in its own view."""
    image_index = original.index(image)
    environment = {}
    for index, option in enumerate(original[:image_index]):
        if option == "--env":
            key, _, value = original[index + 1].partition("=")
            environment[key] = value
    workspace = environment["PAWFLOW_RELAY_DIR"]
    command = []
    index = 0
    while index < image_index:
        option = original[index]
        if option in ("--env", "--volume", "--security-opt"):
            value = original[index + 1]
            index += 2
            if option == "--env" and value.startswith("PAWFLOW_"):
                continue
            if option == "--volume":
                parts = value.split(":")
                target = parts[-2] if parts[-1] in ("ro", "rw") else parts[-1]
                if target in (workspace, "/home/pawflow"):
                    continue
            if option == "--security-opt" and value.startswith("apparmor="):
                continue
            command.extend((option, value))
        else:
            command.append(option)
            index += 1
    exports = []
    endpoint = environment["PAWFLOW_RELAY_SERVER"].rsplit("/", 1)[0]
    for member in record["members"]:
        service_id = member["service_id"]
        config = member["config"]
        digest = hashlib.sha256(service_id.encode("utf-8")).hexdigest()
        root_mount = "/run/pawflow-physical/" + digest + "/workspace"
        home_mount = "/run/pawflow-physical/" + digest + "/home"
        mode = "ro" if config.get("mode") == "readonly" else "rw"
        directory = Path(config["server_workspace_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        from core._relay_naming import _chown_for_host_runner
        _chown_for_host_runner(directory)
        command.extend((
            "--volume", config["server_workspace_host_dir"] + ":" + root_mount + ":" + mode,
            "--volume", config["server_home_volume"] + ":" + home_mount,
        ))
        worker = ["python3", "-u", "/opt/pawflow/pawflow_relay_launcher.py", "--allow-automation"]
        if config.get("allow_service_tunnels"):
            worker.append("--allow-service-tunnels")
        exports.append({
            "relay_id": service_id, "root_mount": root_mount, "home_mount": home_mount,
            "mode": mode, "command": worker,
            "environment": {
                **{key: value for key, value in environment.items() if key.startswith("PAWFLOW_")},
                "PAWFLOW_RELAY_SERVER": endpoint + "/" + quote(service_id, safe=""),
                "PAWFLOW_RELAY_TOKEN": config["token"], "PAWFLOW_RELAY_ID": service_id,
                "PAWFLOW_RELAY_DIR": "/workspace",
                "PAWFLOW_RELAY_ALLOW_EXEC": "1" if config.get("allow_exec", True) else "0",
            },
        })
    filename = launch_file(record)
    _write_private(filename, {"exports": exports})
    command.extend((
        "--user", "0:0", "--workdir", "/", "--entrypoint", "/usr/bin/tini",
        "--volume", "/run/pawflow-rootfs", "--cap-add", "NET_ADMIN",
        "--device", "/dev/net/tun", "--security-opt", "apparmor=unconfined",
        "--security-opt", "seccomp=" + str(SECCOMP_PROFILE),
        "--volume", _relay_runtime_host_dir(filename) + ":/run/pawflow-launch.json:ro",
        image, "--", "python3", "-I", "-u",
        "/opt/pawflow/pawflow_relay/_physical_runtime.py",
        "--config-file", "/run/pawflow-launch.json",
    ))
    return command
