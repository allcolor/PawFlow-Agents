"""The shell image installer replaces a running server before probing its port."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", ["running", "stopped", "doctor_failure", "no_start"])
def test_pull_images_handles_existing_server(tmp_path, case):
    tools = tmp_path / "bin"
    tools.mkdir()
    state = tmp_path / "running"
    state.write_text("0" if case == "stopped" else "1")
    log = tmp_path / "docker.jsonl"
    docker = tools / "docker"
    docker.write_text(f"#!{sys.executable}\n" + r'''
import json
import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
state = Path(os.environ["FAKE_RUNNING"])
with open(os.environ["FAKE_DOCKER_LOG"], "a") as out:
    out.write(json.dumps(args) + "\n")
cmd = args[0]
if cmd == "inspect":
    print("true" if state.read_text() == "1" else "false")
elif cmd == "ps":
    if "-a" in args:
        print("selected-pawflow")
elif cmd == "create":
    print("extract-image")
elif cmd == "cp":
    relative = args[1].split(":/app/", 1)[1]
    target = Path(args[2])
    source = Path(os.environ["FAKE_REPO"]) / relative
    if relative == "docker/claude-code":
        target.mkdir(parents=True)
        (target / "build.sh").write_text("#!/bin/sh\nexit 0\n")
    elif source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
elif cmd == "stop":
    assert args[1] == "selected-pawflow", args
    state.write_text("0")
elif cmd == "start":
    assert args[1] == "selected-pawflow", args
    state.write_text("1")
elif cmd == "run":
    if args[1] == "-d":
        if state.read_text() == "1":
            print("bind: address already in use", file=sys.stderr)
            sys.exit(125)
        state.write_text("1")
        print("new-server")
    elif "command -v docker && docker --version" in args:
        print("/usr/bin/docker\nDocker version 27.0.0")
sys.exit(0)
''')
    docker.chmod(0o755)
    nc = tools / "nc"
    nc.write_text(f"#!{sys.executable}\n" + r'''
import os
from pathlib import Path
import sys
busy = Path(os.environ["FAKE_RUNNING"]).read_text() == "1"
sys.exit(0 if busy or os.environ.get("FAKE_DOCTOR_FAILURE") == "1" else 1)
''')
    nc.chmod(0o755)
    env = {
        "HOME": os.environ["HOME"],
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "FAKE_RUNNING": str(state),
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_REPO": str(ROOT),
        "FAKE_DOCTOR_FAILURE": "1" if case == "doctor_failure" else "0",
        "PAWFLOW_CONTAINER": "selected-pawflow",
        "PAWFLOW_CLEAN_OLD_IMAGES": "0",
        "PAWFLOW_STARTUP_HEALTH_RETRIES": "1",
        "PAWFLOW_STARTUP_HEALTH_INTERVAL": "0",
    }
    command = [
        "bash", str(ROOT / "scripts/install-pawflow.sh"),
        "--pull-images", "--version", "1.0.0-beta.269", "--port", "19990",
        "--home", str(tmp_path / "home"),
        "--dir", str(tmp_path / "checkout"),
        "--runtime-dir", str(tmp_path / "runtime"),
        "--skip-apparmor",
    ]
    if case == "no_start":
        command += ["--no-start", "--skip-doctor"]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=20)
    assert log.exists(), result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    stops = [i for i, args in enumerate(calls) if args[0] == "stop"]
    starts = [args for args in calls if args[0] == "start"]
    runs = [i for i, args in enumerate(calls) if args[:2] == ["run", "-d"]]
    if case == "doctor_failure":
        assert result.returncode != 0
        assert stops
        assert starts == [["start", "selected-pawflow"]]
        assert not runs
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        if case == "no_start":
            assert not stops
            assert not runs
        else:
            assert len(runs) == 1
            assert stops and max(stops) < runs[0]
            # Complete all image pulls before taking down the old server.
            pulls = [i for i, args in enumerate(calls) if args[0] == "pull"]
            assert max(pulls) < min(stops)
    assert state.read_text() == "1"
