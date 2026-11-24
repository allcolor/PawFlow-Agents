"""run-pawflow-docker.sh applies and preserves the server memory limit."""

import os
import subprocess
from pathlib import Path


def _fake_docker(tmp_path, previous_memory=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_LOG\"\n"
        "if [[ \"$1\" == \"ps\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"inspect\" && \"$*\" == *HostConfig.Memory* ]]; then\n"
        "  printf '%s\\n' \"$PREVIOUS_MEMORY\"; exit 0\n"
        "fi\n"
        "if [[ \"$1\" == \"run\" ]]; then\n"
        "  args=\"$*\"\n"
        "  if [[ \"$args\" == *\"command -v docker && docker --version\"* ]]; then\n"
        "    printf '/usr/bin/docker\\nDocker version 27.0.0\\n'; exit 0\n"
        "  fi\n"
        "  if [[ \"$args\" == *\"docker version >/dev/null\"* ]]; then exit 0; fi\n"
        "  printf 'container-id\\n'; exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    log = tmp_path / "docker.log"
    env = {
        "DOCKER_LOG": str(log),
        "PREVIOUS_MEMORY": previous_memory,
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "PAWFLOW_HOME": str(tmp_path / "pawflow-home"),
        "PAWFLOW_HOST": "0.0.0.0",
        "PAWFLOW_IMAGE": "test-image",
        "PAWFLOW_PORT": "12345",
        "PAWFLOW_NETWORK_MODE": "bridge",
    }
    return env, log


def _server_run_line(env, log):
    result = subprocess.run(
        ["bash", "scripts/run-pawflow-docker.sh"], cwd=Path.cwd(), env=env,
        text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    lines = [line for line in log.read_text(encoding="utf-8").splitlines()
             if line.startswith("run -d")]
    assert len(lines) == 1
    return lines[0]


def test_an_explicit_limit_is_applied_with_swap_included(tmp_path):
    env, log = _fake_docker(tmp_path, previous_memory="0")
    env["PAWFLOW_MEMORY"] = "24g"
    line = _server_run_line(env, log)
    assert "--restart unless-stopped" in line
    assert "--memory 24g --memory-swap 24g" in line


def test_an_update_keeps_the_limit_of_the_replaced_container(tmp_path):
    env, log = _fake_docker(tmp_path, previous_memory="25769803776")
    line = _server_run_line(env, log)
    assert "--memory 25769803776b --memory-swap 25769803776b" in line


def test_no_limit_without_value_or_previous_limit(tmp_path):
    env, log = _fake_docker(tmp_path, previous_memory="0")
    assert "--memory" not in _server_run_line(env, log)


def test_both_installers_expose_the_option():
    install_sh = Path("scripts/install-pawflow.sh").read_text(encoding="utf-8")
    install_ps1 = Path("scripts/install-pawflow.ps1").read_text(encoding="utf-8")
    assert '--memory) MEMORY_LIMIT="$2"' in install_sh
    assert 'PAWFLOW_MEMORY="$MEMORY_LIMIT"' in install_sh
    assert "[string]$Memory = $env:PAWFLOW_MEMORY" in install_ps1
    assert '"--memory", $memoryLimit, "--memory-swap", $memoryLimit' in install_ps1
