"""Exercise real grouped Linux workers through native Windows and WSL2 Docker."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pawflow_relay.utils import docker_cmd, translate_path


def main():
    if os.name != "nt" or os.environ.get("PAWFLOW_DISPOSABLE_ACCEPTANCE") != "1":
        raise RuntimeError("Run only on an explicitly disposable Windows runner")
    output = Path(os.environ["PAWFLOW_WSL_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    prefix = docker_cmd()
    if prefix != ["wsl", "docker"]:
        raise RuntimeError("Acceptance requires the production Windows Docker default")
    kernel = subprocess.check_output(["wsl", "--exec", "uname", "-r"], text=True, timeout=30).strip()
    if "wsl2" not in kernel.lower():
        raise RuntimeError("The imported distribution must run under WSL2")
    info = json.loads(subprocess.check_output(
        prefix + ["info", "--format", "{{json .}}"], text=True, timeout=30))
    if info["OSType"] != "linux":
        raise RuntimeError("Acceptance requires a Linux Docker engine")
    context = output / "image context with spaces"
    image = "pawflow-wsl-acceptance:local"
    subprocess.run([
        sys.executable, str(root / "scripts/generate-relay-image.py"),
        "--profile", "client-minimal", "--out", str(context), "--image", image,
    ], check=True, timeout=120)
    for probe in ("physical_runtime_probe.py", "physical_relay_probe.py"):
        shutil.copy2(root / "tests" / probe, context / "runtime" / probe)
    subprocess.run(prefix + ["build", "-t", image, translate_path(str(context))],
                   check=True, timeout=1200)
    native = output / "native mount with spaces"
    native.mkdir()
    (native / "sentinel").write_text("native Windows mount", encoding="utf-8")
    reports = []
    for probe, report in (("physical_runtime_probe", "result.json"),
                          ("physical_relay_probe", "relay-result.json")):
        name = "pawflow-wsl-" + probe.replace("_", "-")
        command = prefix + [
            "run", "--rm", "--name", name,
            "--cpus", "2", "--memory", "2g", "--memory-swap", "2g", "--pids-limit", "512",
            "--cap-add", "SYS_ADMIN", "--cap-add", "NET_ADMIN",
            "--device", "/dev/fuse", "--device", "/dev/net/tun",
            "--security-opt", "apparmor=unconfined",
            "--security-opt", "seccomp=" + translate_path(str(root / "pawflow_relay/physical-seccomp.json")),
            "--volume", "/run/pawflow-rootfs",
            "--mount", "type=bind,source=" + translate_path(str(context / "runtime")) + ",target=/opt/pawflow,readonly",
            "--mount", "type=bind,source=" + translate_path(str(output)) + ",target=/acceptance-output",
            "--mount", "type=bind,source=" + translate_path(str(native)) + ",target=/native-input,readonly",
            "--env", "PAWFLOW_DISPOSABLE_ACCEPTANCE=1",
            "--entrypoint", "/usr/bin/tini", image, "--", "python3", "-I", "-c",
            "from pathlib import Path; import runpy; "
            "assert Path('/native-input/sentinel').read_text() == 'native Windows mount'; "
            "runpy.run_path('/opt/pawflow/" + probe + ".py', run_name='__main__')",
        ]
        try:
            subprocess.run(command, check=True, timeout=420)
            result = json.loads((output / report).read_text(encoding="utf-8"))
            if result["status"] != "passed":
                raise RuntimeError("The grouped probe did not pass: " + report)
            reports.append(report)
        finally:
            subprocess.run(prefix + ["rm", "-f", name], timeout=30, check=False)
            remaining = subprocess.check_output(
                prefix + ["ps", "-aq", "--filter", "name=^/" + name + "$"],
                text=True, timeout=30).strip()
            if remaining:
                raise RuntimeError("The owned acceptance container remains: " + name)
    (output / "windows-wsl-result.json").write_text(json.dumps({
        "status": "passed", "platform": sys.platform, "kernel": kernel,
        "docker_server_version": info["ServerVersion"], "docker_os": info["OSType"],
        "docker_prefix": prefix, "reports": reports,
        "native_mount_with_spaces": True, "containers_removed": True,
        "scope": "Native Windows Docker dispatch, grouped kernel and relay/FUSE probes",
        "native_physical_host_helpers_exercised": False,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
