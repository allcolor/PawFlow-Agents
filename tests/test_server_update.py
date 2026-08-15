"""Server self-update: deployment detection, preflight, and the detached updater."""

import json
import subprocess

import pytest

from core import compose_deployment, installer_deployment, update_manager
from core import FlowFile

WORKDIR = "/srv/pawflow"
APP_DIR = "/home/pawflow/install"


@pytest.fixture(autouse=True)
def _clean_cache():
    compose_deployment.reset_cache()
    installer_deployment.reset_cache()
    yield
    compose_deployment.reset_cache()
    installer_deployment.reset_cache()


def _admin_flowfile():
    return FlowFile(content=b"{}", attributes={"http.auth.roles": "admin"})


def _inspect_payload(labels):
    return json.dumps([{"Name": "/pawflow-pawflow-1", "Config": {"Labels": labels}}])


COMPOSE_LABELS = {
    compose_deployment.LABEL_PROJECT: "pawflow",
    compose_deployment.LABEL_SERVICE: "pawflow",
    compose_deployment.LABEL_WORKING_DIR: WORKDIR,
    compose_deployment.LABEL_CONFIG_FILES: f"{WORKDIR}/docker-compose.yml",
}


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


# -- finding ourselves -------------------------------------------------


def test_container_id_comes_from_mountinfo(monkeypatch, tmp_path):
    cid = "a" * 64
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        f"1234 25 0:52 / /etc/hosts rw - ext4 /var/lib/docker/containers/{cid}/hosts rw\n",
        encoding="utf-8")
    monkeypatch.setattr(compose_deployment, "Path",
                        lambda p: mountinfo if "mountinfo" in p else tmp_path / "missing")

    assert compose_deployment.self_container_id() == cid


def test_hostname_is_only_trusted_when_it_looks_like_an_id(monkeypatch, tmp_path):
    monkeypatch.setattr(compose_deployment, "Path", lambda p: tmp_path / "missing")

    # A compose file with `hostname: pawflow` must not be mistaken for an id.
    monkeypatch.setattr(compose_deployment.socket, "gethostname", lambda: "pawflow")
    assert compose_deployment.self_container_id() == ""

    monkeypatch.setattr(compose_deployment.socket, "gethostname", lambda: "3f2b19c0d4e5")
    assert compose_deployment.self_container_id() == "3f2b19c0d4e5"


def test_compose_info_reads_the_labels_compose_wrote(monkeypatch):
    monkeypatch.setattr(compose_deployment, "self_container_id", lambda: "c" * 64)
    monkeypatch.setattr(compose_deployment.subprocess, "run",
                        lambda *a, **k: _completed(_inspect_payload(COMPOSE_LABELS)))

    info = compose_deployment.compose_info()

    assert info["project"] == "pawflow"
    assert info["working_dir"] == WORKDIR
    assert info["service"] == "pawflow"
    assert info["config_files"] == [f"{WORKDIR}/docker-compose.yml"]
    assert info["container_name"] == "pawflow-pawflow-1"


def test_a_container_without_compose_labels_is_not_a_compose_deployment(monkeypatch):
    monkeypatch.setattr(compose_deployment, "self_container_id", lambda: "c" * 64)
    monkeypatch.setattr(compose_deployment.subprocess, "run",
                        lambda *a, **k: _completed(_inspect_payload({"foo": "bar"})))

    assert compose_deployment.compose_info() == {}


def test_compose_info_is_resolved_once(monkeypatch):
    calls = []
    monkeypatch.setattr(compose_deployment, "self_container_id", lambda: "c" * 64)

    def fake_run(*a, **k):
        calls.append(1)
        return _completed(_inspect_payload(COMPOSE_LABELS))

    monkeypatch.setattr(compose_deployment.subprocess, "run", fake_run)

    compose_deployment.compose_info()
    compose_deployment.compose_info()

    assert len(calls) == 1


# -- preflight ---------------------------------------------------------


def _patch_compose(monkeypatch, info=None):
    monkeypatch.setattr("core.compose_deployment.compose_info",
                        lambda refresh=False: dict(info if info is not None else {
                            "project": "pawflow", "service": "pawflow",
                            "working_dir": WORKDIR, "container_id": "c" * 64,
                            "container_name": "pawflow-pawflow-1",
                            "config_files": []}))


def _patch_installer(monkeypatch, info=None):
    monkeypatch.setattr("core.installer_deployment.installer_info",
                        lambda refresh=False: dict(info or {}))


def test_preflight_refuses_when_neither_deployment_is_recognised(monkeypatch):
    _patch_compose(monkeypatch, info={})
    _patch_installer(monkeypatch)
    monkeypatch.setattr(update_manager.subprocess, "run",
                        lambda *a, **k: pytest.fail("nothing may run"))

    check = update_manager.server_update_preflight()

    assert check["ok"] is False
    assert "Compose" in check["reason"]
    assert "PAWFLOW_HOST_APP_DIR" in check["reason"]


def test_preflight_mounts_the_project_at_its_own_host_path(monkeypatch):
    # compose resolves ./data and `build: .` against the project directory and
    # hands the result to the daemon as *host* paths. Mounting it anywhere else
    # would silently produce wrong bind mounts.
    captured = {}
    _patch_compose(monkeypatch)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _completed("Docker Compose version v2.29.0\nPAWFLOW_GIT=1\n")

    monkeypatch.setattr(update_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(update_manager, "running_agent_count", lambda: 3)

    check = update_manager.server_update_preflight()

    assert check["ok"] is True
    assert f"{WORKDIR}:{WORKDIR}" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--workdir") + 1] == WORKDIR
    assert check["is_git_checkout"] is True
    assert check["running_agents"] == 3
    assert check["compose_version"].startswith("Docker Compose")


def test_preflight_reports_a_broken_updater_image(monkeypatch):
    _patch_compose(monkeypatch)
    monkeypatch.setattr(update_manager.subprocess, "run",
                        lambda *a, **k: _completed("", returncode=127,
                                                   stderr="docker: 'compose' is not a command"))

    check = update_manager.server_update_preflight()

    assert check["ok"] is False
    assert "not a command" in check["reason"]


def test_a_non_git_project_reports_no_checkout(monkeypatch):
    _patch_compose(monkeypatch)
    monkeypatch.setattr(update_manager.subprocess, "run",
                        lambda *a, **k: _completed("Docker Compose version v2.29.0\n"))

    assert update_manager.server_update_preflight()["is_git_checkout"] is False


# -- the updater script ------------------------------------------------


def test_script_checks_compose_before_touching_anything():
    script = update_manager._updater_script(WORKDIR, pull_source=False)
    lines = script.splitlines()

    # Everything that can fail harmlessly runs before anything that stops the
    # server: a missing compose must abort while the server is still alive.
    assert lines[0] == "set -eu"
    assert lines.index("docker compose version") < len(lines) - 1
    # Bringing the stack up is the last step that can affect the server. The
    # only thing after it is the image cleanup, which reclaims disk once the
    # new version is already running and tolerates its own failures.
    restart = lines.index("docker compose up -d --build")
    assert lines[restart + 1].startswith(
        "echo 'Cleaning older PawFlow image tags"), lines[restart + 1:]
    assert "git pull" not in script


def test_script_pulls_source_only_when_asked_and_only_fast_forward():
    script = update_manager._updater_script(WORKDIR, pull_source=True)

    assert "git pull --ff-only" in script
    # A dirty or diverged tree must stop the update, not be merged behind the
    # operator's back.
    assert "git pull\n" not in script
    assert script.index("git pull") < script.index("docker compose up")


def test_script_survives_a_deployment_that_has_nothing_to_pull():
    script = update_manager._updater_script(WORKDIR, pull_source=False)

    # `build: .` services have no image to pull; that must not abort the run.
    assert "--ignore-buildable" in script


def test_a_failed_pull_is_not_reported_as_an_update():
    """`pull --ignore-buildable || pull || true` turned two failures into one
    success.

    An unreachable registry, an expired login, a rate limit -- all swallowed,
    and `up` then cleanly restarted the image already on the host while the UI
    said the update was done. The flag is probed instead, so where compose can
    tell "nothing to pull" from "pull failed", a failure stops the update.
    """
    script = update_manager._updater_script(WORKDIR, pull_source=False)

    # Scoped to the part that decides whether the update succeeded. The image
    # cleanup that runs afterwards uses `|| true` on purpose: it costs disk,
    # never correctness, and must not fail an update that already worked.
    update_part = script.split("docker compose up -d --build")[0]
    assert "|| true" not in update_part
    # The supported path has no tolerance at all: `set -eu` ends the run.
    assert "  docker compose pull --ignore-buildable\n" in script + "\n"
    # Legacy Compose classifies each service from normalized config instead of
    # swallowing every pull error.
    assert "for _pf_service in $(docker compose config --services)" in script
    assert "ERROR docker compose pull failed for image-only service" in script


def _run_updater(tmp_path, pull_rc):
    """Run the script for real against a `docker` that fails the pull."""
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        'if [[ "$2" == pull && "$3" == --help ]]; then\n'
        "  echo '  --ignore-buildable   Ignore buildable images'; exit 0\n"
        "fi\n"
        'if [[ "$2" == pull ]]; then exit ' + str(pull_rc) + "; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    script = update_manager._updater_script(str(proj), pull_source=False)
    return subprocess.run(
        ["bash", "-c", script], text=True, capture_output=True, timeout=30,
        env={"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
             "DOCKER_LOG": str(tmp_path / "docker.log")})


def test_a_failing_pull_stops_before_the_server_is_recreated(tmp_path):
    result = _run_updater(tmp_path, pull_rc=1)

    assert result.returncode != 0
    log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "up -d --build" not in log, (
        "the server was recreated on the image it already had, and the update "
        "would have been reported as successful")


def test_a_pull_that_works_still_recreates_the_server(tmp_path):
    result = _run_updater(tmp_path, pull_rc=0)

    assert result.returncode == 0, result.stderr
    log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "up -d --build" in log


def _run_legacy_updater(tmp_path, compose_config, services, failing):
    import os
    import subprocess

    bin_dir = tmp_path / "legacy-bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        'if [[ "$2" == pull && "$3" == --help ]]; then exit 0; fi\n'
        'if [[ "$2" == config && "$3" == --services ]]; then printf "%s\\n" $SERVICES; exit 0; fi\n'
        'if [[ "$2" == config ]]; then printf "%s" "$COMPOSE_CONFIG"; exit 0; fi\n'
        'if [[ "$2" == pull && " $FAILING " == *" $3 "* ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8")
    docker.chmod(0o755)
    proj = tmp_path / "legacy-proj"
    proj.mkdir()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "DOCKER_LOG": str(tmp_path / "legacy-docker.log"),
        "COMPOSE_CONFIG": compose_config,
        "SERVICES": " ".join(services),
        "FAILING": " ".join(failing),
    }
    result = subprocess.run(
        ["sh", "-c", update_manager._updater_script(str(proj), False)],
        text=True, capture_output=True, timeout=30, env=env)
    return result, (tmp_path / "legacy-docker.log").read_text(encoding="utf-8")


def test_legacy_compose_propagates_image_only_pull_failure(tmp_path):
    result, log = _run_legacy_updater(
        tmp_path, "services:\n  web:\n    image: example/web:latest\n",
        ["web"], ["web"])

    assert result.returncode != 0
    assert "image-only service web" in result.stderr
    assert "up -d --build" not in log


def test_legacy_compose_tolerates_only_a_service_with_build_config(tmp_path):
    result, log = _run_legacy_updater(
        tmp_path, "services:\n  web:\n    build:\n      context: .\n",
        ["web"], ["web"])

    assert result.returncode == 0, result.stderr
    assert "buildable service web" in result.stderr
    assert "up -d --build" in log


def test_script_quotes_the_project_directory():
    script = update_manager._updater_script("/srv/paw flow", pull_source=False)
    assert "cd '/srv/paw flow'" in script


# -- launching ---------------------------------------------------------


def test_update_server_launches_a_detached_updater(monkeypatch):
    calls = []
    monkeypatch.setattr(update_manager, "server_update_preflight", lambda: {
        "ok": True, "working_dir": WORKDIR, "updater_image": "docker:cli",
        "compose": {"project": "pawflow", "service": "pawflow"}})

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _completed("deadbeef\n")

    monkeypatch.setattr(update_manager.subprocess, "run", fake_run)

    result = update_manager.update_server()

    assert result["ok"] is True and result["started"] is True
    # The old updater is removed first so its name is free.
    assert calls[0][-3:] == ["rm", "-f", update_manager.UPDATER_CONTAINER]
    run_cmd = calls[1]
    assert "--detach" in run_cmd
    # It must outlive the server it is about to kill, and must not restart.
    assert run_cmd[run_cmd.index("--restart") + 1] == "no"
    assert "/var/run/docker.sock:/var/run/docker.sock" in run_cmd
    assert run_cmd[-1].startswith("set -eu")


def test_update_server_refuses_when_the_preflight_fails(monkeypatch):
    monkeypatch.setattr(update_manager, "server_update_preflight",
                        lambda: {"ok": False, "reason": "not compose"})
    monkeypatch.setattr(update_manager.subprocess, "run",
                        lambda *a, **k: pytest.fail("nothing may be launched"))

    result = update_manager.update_server()

    assert result == {"ok": False, "started": False, "reason": "not compose"}


def test_a_refused_launch_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(update_manager, "server_update_preflight", lambda: {
        "ok": True, "working_dir": WORKDIR, "updater_image": "docker:cli",
        "compose": {}})
    monkeypatch.setattr(update_manager.subprocess, "run",
                        lambda cmd, **k: _completed("", returncode=125,
                                                    stderr="name already in use"))

    result = update_manager.update_server()

    assert result["ok"] is False
    assert "name already in use" in result["reason"]


def test_updater_image_is_configurable(monkeypatch):
    monkeypatch.setenv("PAWFLOW_SERVER_UPDATE_IMAGE", "registry.local/docker:cli")
    assert update_manager.updater_image() == "registry.local/docker:cli"

    monkeypatch.delenv("PAWFLOW_SERVER_UPDATE_IMAGE", raising=False)
    monkeypatch.setattr("core.expression._load_global_parameters",
                        lambda: {"server_update_image": "mirror/docker:27-cli"})
    assert update_manager.updater_image() == "mirror/docker:27-cli"

    monkeypatch.setattr("core.expression._load_global_parameters", lambda: {})
    assert update_manager.updater_image() == update_manager.DEFAULT_UPDATER_IMAGE


# -- installer deployments ---------------------------------------------
#
# `install-pawflow.sh` ends on `run-pawflow-docker.sh`, a plain `docker run`.
# Compose stamps its project path on every container it creates; a `docker run`
# stamps nothing, so every installer deployment used to be refused outright.

INSTALLER_ENV = [
    "PATH=/usr/bin",
    "PAWFLOW_HOST_APP_DIR=" + APP_DIR,
    "PAWFLOW_HOST_DATA_DIR=/home/pawflow/.pawflow/data",
    "PAWFLOW_APP_DIR=/app",
    "PAWFLOW_DATA_DIR=/app/data",
    "PAWFLOW_BOOTSTRAP_GATEWAY_KEY=not-roy-batty",
    "PAWFLOW_BOOTSTRAP_RESET=1",
    "PAWFLOW_RUN_UID=1001",
    "PAWFLOW_SERVER_RELAY_IMAGE=ghcr.io/allcolor/pawflow-relay-dev:2026.07.16",
]

INSTALLER_CMD = ["python", "cli.py", "start", "--host", "0.0.0.0",
                 "--port", "19990", "--verbose"]


def _patch_inspect(monkeypatch, labels=None, env=None, cmd=None, mounts=None,
                   image="ghcr.io/allcolor/pawflow:1.0.0-beta.40"):
    raw = {
        "Name": "/pawflow-server",
        "Config": {"Labels": labels or {}, "Env": env if env is not None else INSTALLER_ENV,
                   "Cmd": cmd if cmd is not None else INSTALLER_CMD, "Image": image},
        "HostConfig": {"NetworkMode": "host"},
        "Mounts": mounts if mounts is not None else [
            {"Destination": "/app/data", "Source": "/home/pawflow/.pawflow/data"}],
    }
    monkeypatch.setattr(installer_deployment, "self_container_id", lambda: "d" * 64)
    monkeypatch.setattr(installer_deployment, "inspect_container", lambda c: raw)


def test_a_container_started_before_the_labels_is_still_recognised(monkeypatch):
    # The whole point: an install running *right now* must be updatable, not
    # only the next one. PAWFLOW_HOST_APP_DIR has been injected for far longer
    # than the labels have existed.
    _patch_inspect(monkeypatch, labels={})

    info = installer_deployment.installer_info()

    assert info["host_app_dir"] == APP_DIR
    assert info["labelled"] is False
    assert info["pawflow_home"] == "/home/pawflow/.pawflow"
    assert info["container_name"] == "pawflow-server"


def test_labels_win_over_the_environment(monkeypatch):
    _patch_inspect(monkeypatch, labels={
        installer_deployment.LABEL_DEPLOYMENT: "installer",
        installer_deployment.LABEL_APP_DIR: "/opt/pawflow",
        installer_deployment.LABEL_HOME: "/opt/home",
        installer_deployment.LABEL_PORT: "8443",
    })

    info = installer_deployment.installer_info()

    assert info["host_app_dir"] == "/opt/pawflow"
    assert info["pawflow_home"] == "/opt/home"
    assert info["port"] == "8443"
    assert info["labelled"] is True


def test_the_port_and_extra_args_come_from_the_command_line(monkeypatch):
    # They are arguments to `cli.py start`, never environment variables, so an
    # env-only reading would silently move the server to the default port.
    _patch_inspect(monkeypatch)

    info = installer_deployment.installer_info()

    assert info["port"] == "19990"
    assert info["container_host"] == "0.0.0.0"
    assert info["extra_args"] == "--verbose"


def test_a_container_with_no_installer_marker_is_not_one(monkeypatch):
    _patch_inspect(monkeypatch, labels={}, env=["PATH=/usr/bin"], mounts=[])

    assert installer_deployment.installer_info() == {}


def test_the_start_environment_replays_the_running_container(monkeypatch):
    _patch_inspect(monkeypatch)
    info = installer_deployment.installer_info()

    env = installer_deployment.start_script_env(info, "ghcr.io/x/pawflow:new")

    # Replayed: the deployment keeps its identity across an update.
    assert env["PAWFLOW_BOOTSTRAP_GATEWAY_KEY"] == "not-roy-batty"
    assert env["PAWFLOW_RUN_UID"] == "1001"
    assert env["PAWFLOW_SERVER_RELAY_IMAGE"].endswith("2026.07.16")
    # Decided by the update.
    assert env["PAWFLOW_IMAGE"] == "ghcr.io/x/pawflow:new"
    assert env["PAWFLOW_CONTAINER"] == "pawflow-server"
    assert env["PAWFLOW_PORT"] == "19990"
    assert env["PAWFLOW_NETWORK_MODE"] == "host"
    assert env["PAWFLOW_RECREATE_CONTAINER"] == "1"
    # Paths inside the old container say nothing about the new one.
    assert "PAWFLOW_APP_DIR" not in env and "PAWFLOW_HOST_DATA_DIR" not in env


def test_the_first_run_flags_are_never_replayed(monkeypatch):
    # A fresh install may have been started with the bootstrap reset on.
    # Replaying it on every update would wipe a working server's installer
    # state, which is the opposite of an update.
    _patch_inspect(monkeypatch)
    info = installer_deployment.installer_info()

    env = installer_deployment.start_script_env(info, "img")

    assert env["PAWFLOW_BOOTSTRAP_RESET"] == ""


def test_compose_is_preferred_when_both_are_present(monkeypatch):
    _patch_compose(monkeypatch)
    _patch_installer(monkeypatch, {"host_app_dir": APP_DIR})
    monkeypatch.setattr(update_manager, "_probe",
                        lambda *a, **k: _completed("Docker Compose version v2\n"))

    assert update_manager.server_update_preflight()["deployment"] == "compose"


def test_installer_preflight_requires_the_start_script(monkeypatch):
    _patch_compose(monkeypatch, info={})
    _patch_installer(monkeypatch, {"host_app_dir": APP_DIR,
                                   "container_name": "pawflow-server",
                                   "pawflow_home": "/home/pawflow/.pawflow",
                                   "port": "19990",
                                   "image": "ghcr.io/allcolor/pawflow:1.0.0b40"})
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "1.0.0-beta.41")
    monkeypatch.setattr(update_manager, "_probe",
                        lambda *a, **k: _completed("", returncode=1))

    check = update_manager.server_update_preflight()

    assert check["ok"] is False
    assert "run-pawflow-docker.sh" in check["reason"]


def test_installer_preflight_reports_the_image_it_would_move_to(monkeypatch):
    _patch_compose(monkeypatch, info={})
    _patch_installer(monkeypatch, {"host_app_dir": APP_DIR,
                                   "container_name": "pawflow-server",
                                   "pawflow_home": "/home/pawflow/.pawflow",
                                   "port": "19990",
                                   "image": "ghcr.io/allcolor/pawflow:1.0.0-beta.40"})
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "1.0.0-beta.41")
    probed = {}

    def fake_probe(image, host_dir, script):
        probed.update(image=image, host_dir=host_dir, script=script)
        return _completed("")

    monkeypatch.setattr(update_manager, "_probe", fake_probe)
    monkeypatch.setattr(update_manager, "running_agent_count", lambda: 2)

    check = update_manager.server_update_preflight()

    assert check["ok"] is True
    assert check["deployment"] == "installer"
    assert check["target_image"] == "ghcr.io/allcolor/pawflow:1.0.0-beta.41"
    assert check["updater_image"] == "ghcr.io/allcolor/pawflow:1.0.0-beta.40"
    assert check["working_dir"] == APP_DIR
    assert check["running_agents"] == 2
    assert probed["image"] == "ghcr.io/allcolor/pawflow:1.0.0-beta.40"
    assert "command -v bash" in probed["script"]
    assert "docker version" in probed["script"]


def test_an_unresolvable_published_image_stops_the_update(monkeypatch):
    _patch_compose(monkeypatch, info={})
    _patch_installer(monkeypatch, {"host_app_dir": APP_DIR, "image": "pawflow:x"})
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "")
    monkeypatch.setattr(update_manager, "_probe",
                        lambda *a, **k: pytest.fail("nothing may run"))

    assert update_manager.server_update_preflight()["ok"] is False


def test_an_unknown_data_directory_stops_the_update(monkeypatch):
    # run-pawflow-docker.sh defaults PAWFLOW_HOME to $HOME/pawflow, which inside
    # the updater container is an empty directory: the server would come back on
    # blank data with the old container already gone. Refused, never defaulted.
    _patch_compose(monkeypatch, info={})
    _patch_installer(monkeypatch, {"host_app_dir": APP_DIR,
                                   "container_name": "pawflow-server",
                                   "pawflow_home": "", "port": "19990",
                                   "image": "ghcr.io/allcolor/pawflow:1.0.0-beta.40"})
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "1.0.0-beta.41")
    monkeypatch.setattr(update_manager, "_probe",
                        lambda *a, **k: pytest.fail("nothing may run"))

    check = update_manager.server_update_preflight()

    assert check["ok"] is False
    assert "data directory" in check["reason"]


def test_published_server_image_keeps_the_repository(monkeypatch):
    # The release lookup is a live GitHub API call. Unpatched, a rate-limited
    # or offline runner returns no tag, every result here is empty, and the
    # failure says nothing about the repository handling under test -- which
    # is the only thing this test is about. Pin the tag and assert the whole
    # string rather than a prefix, so an empty tag cannot pass either.
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "1.0.0-beta.48")
    published = update_manager.published_server_image

    assert published("ghcr.io/allcolor/pawflow:1.0.0b40") == \
        "ghcr.io/allcolor/pawflow:1.0.0-beta.48"
    # A digest pin or a registry with a port must not lose its repository.
    assert published("ghcr.io/a/b@sha256:" + "0" * 64) == "ghcr.io/a/b:1.0.0-beta.48"
    assert published("registry.local:5000/pawflow") == \
        "registry.local:5000/pawflow:1.0.0-beta.48"


def test_no_published_release_yields_no_image(monkeypatch):
    # The empty tag the CI runner actually hit: it must resolve to "no image",
    # never to a repository with a dangling colon that a pull would then fail on.
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "")

    assert update_manager.published_server_image("ghcr.io/allcolor/pawflow:1.0.0b40") == ""


def test_the_installer_script_hands_over_to_the_installer(monkeypatch):
    _patch_inspect(monkeypatch)
    info = installer_deployment.installer_info()

    script = update_manager._installer_updater_script(
        info, "ghcr.io/allcolor/pawflow:1.0.0-beta.41", pull_source=False)

    lines = script.splitlines()
    assert lines[0] == "set -eu"
    # The installer updater runs in the already-local PawFlow image, which
    # carries both Bash and the Docker CLI. Bootstrap must not depend on Alpine
    # package repositories before it can even report an update failure.
    assert "apk" not in script
    # The whole update is the installer's own sequence — the same command an
    # operator runs on the host — not a re-implementation of a subset of it.
    assert "bash scripts/install-pawflow.sh --port 19990 --pull-images" in script
    assert "run-pawflow-docker.sh" not in script
    assert f"cd {APP_DIR}" in script
    # The deployment's identity rides along, quoted.
    assert "PAWFLOW_BOOTSTRAP_GATEWAY_KEY=not-roy-batty" in script
    assert "PAWFLOW_BOOTSTRAP_RESET=''" in script
    assert "git pull" not in script


def test_the_installer_script_pulls_source_only_when_asked(monkeypatch):
    _patch_inspect(monkeypatch)
    info = installer_deployment.installer_info()

    script = update_manager._installer_updater_script(info, "img", pull_source=True)

    assert "git pull --ff-only" in script
    assert script.index("git pull") < script.index("install-pawflow.sh")


def test_update_server_runs_the_installer_script_for_an_installer_deployment(monkeypatch):
    _patch_inspect(monkeypatch)
    info = installer_deployment.installer_info()
    calls = []
    monkeypatch.setattr(update_manager, "server_update_preflight", lambda: {
        "ok": True, "deployment": "installer", "installer": info,
        "working_dir": APP_DIR, "updater_image": "docker:cli",
        "target_image": "ghcr.io/allcolor/pawflow:1.0.0-beta.41"})

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _completed("deadbeef\n")

    monkeypatch.setattr(update_manager.subprocess, "run", fake_run)

    result = update_manager.update_server()

    assert result["ok"] is True
    assert result["deployment"] == "installer"
    assert result["target_image"].endswith("1.0.0-beta.41")
    run_cmd = calls[1]
    assert "install-pawflow.sh" in run_cmd[-1]
    assert "docker compose" not in run_cmd[-1]
    # The install directory is mounted at its own host path: the start script
    # resolves $PAWFLOW_HOME against it and hands the daemon host paths.
    assert f"{APP_DIR}:{APP_DIR}" in run_cmd


def test_updater_status_reports_an_early_failure_with_bounded_logs(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "inspect" in cmd:
            return _completed('{"Status":"exited","ExitCode":2}\n')
        return _completed("first line\n" + ("x" * 9000) + "\napk failed\n")

    monkeypatch.setattr(update_manager.subprocess, "run", fake_run)

    status = update_manager.server_updater_status()

    assert status["ok"] is True
    assert status["finished"] is True
    assert status["failed"] is True
    assert status["exit_code"] == 2
    assert status["logs"].endswith("apk failed")
    assert len(status["logs"]) <= 8000
    assert calls[0][-1] == update_manager.UPDATER_CONTAINER
    assert calls[1][-1] == update_manager.UPDATER_CONTAINER


def test_updater_status_does_not_fetch_logs_while_running(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _completed('{"Status":"running","ExitCode":0}\n')

    monkeypatch.setattr(update_manager.subprocess, "run", fake_run)

    status = update_manager.server_updater_status()

    assert status["running"] is True
    assert status["finished"] is False
    assert "logs" not in status
    assert len(calls) == 1


# -- admin actions -----------------------------------------------------


def test_server_update_actions_require_admin():
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    for action in ("admin_server_update_check", "admin_server_update_status",
                   "admin_update_server"):
        ff = FlowFile(content=b"{}", attributes={"http.auth.roles": "user"})
        result = _handle_admin_settings(None, action, {}, None, "bob", ff)
        assert result[0].get_attribute("http.response.status") == "403"


def test_update_action_does_not_refuse_because_agents_are_running(monkeypatch):
    # A restart kills them, exactly as a command-line update would. The UI says
    # so; the server does not decide for the operator.
    from tasks.ai.actions import admin_settings

    monkeypatch.setattr(update_manager, "running_agent_count", lambda: 7)
    monkeypatch.setattr(update_manager, "update_server",
                        lambda pull_source=False: {
                            "ok": True, "started": True,
                            "container": "pawflow-updater",
                            "pull_source": pull_source})

    result = admin_settings._handle_admin_settings(
        None, "admin_update_server", {"pull_source": True}, None, "admin",
        _admin_flowfile())
    payload = json.loads(result[0].get_content().decode("utf-8"))

    assert payload["started"] is True
    assert payload["pull_source"] is True


def test_update_action_reports_a_refusal_as_409(monkeypatch):
    from tasks.ai.actions import admin_settings

    monkeypatch.setattr(update_manager, "update_server",
                        lambda pull_source=False: {
                            "ok": False,
                            "reason": "not a compose deployment"})

    result = admin_settings._handle_admin_settings(
        None, "admin_update_server", {}, None, "admin", _admin_flowfile())

    assert result[0].get_attribute("http.response.status") == "409"


def test_update_status_action_returns_the_fixed_updater_status(monkeypatch):
    from tasks.ai.actions import admin_settings

    monkeypatch.setattr(update_manager, "server_updater_status", lambda: {
        "ok": True, "finished": True, "failed": True, "exit_code": 2,
        "logs": "bootstrap failed",
    })

    result = admin_settings._handle_admin_settings(
        None, "admin_server_update_status", {"container": "not-allowed"},
        None, "admin", _admin_flowfile())
    payload = json.loads(result[0].get_content().decode("utf-8"))

    assert payload["failed"] is True
    assert payload["logs"] == "bootstrap failed"


# -- UI wiring ---------------------------------------------------------


def test_update_dialog_confirms_then_waits_for_health():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "tasks" / "io" / "chat_ui"
          / "admin_settings.js").read_text(encoding="utf-8")

    assert "admin_server_update_check" in js
    assert "admin_update_server" in js
    # The check must come before the destructive call: the operator sees the
    # project, the directory and the running-agent count first.
    assert js.index("admin_server_update_check") < js.index("'admin_update_server'")
    assert "running_agents" in js
    assert "admin_server_update_status" in js
    assert "_admUpdateFailed" in js
    assert "/health" in js
    assert "location.reload()" in js


def test_the_dialog_names_what_an_installer_update_will_actually_do():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "tasks" / "io" / "chat_ui"
          / "admin_settings.js").read_text(encoding="utf-8")

    # It used to promise a compose project to every operator, including the
    # majority who never ran compose.
    assert "deployment === 'installer'" in js
    assert "install-pawflow.sh --pull-images" in js
    assert "target_image" in js


def test_the_start_script_stamps_what_the_detector_reads():
    """A `docker run` records nothing by itself, so the script must.

    Without these labels the only trace of how the server was started is its
    environment, which is why the detector keeps that fallback — but a
    container created from now on says so outright.
    """
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "scripts"
              / "run-pawflow-docker.sh").read_text(encoding="utf-8")

    for label in (installer_deployment.LABEL_DEPLOYMENT,
                  installer_deployment.LABEL_APP_DIR,
                  installer_deployment.LABEL_HOME,
                  installer_deployment.LABEL_PORT,
                  installer_deployment.LABEL_NETWORK):
        assert f"--label {label}=" in script, f"{label} is not stamped"
    assert f"--label {installer_deployment.LABEL_DEPLOYMENT}=" \
           f"{installer_deployment.DEPLOYMENT_INSTALLER}" in script
