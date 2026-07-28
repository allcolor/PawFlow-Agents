"""Server self-update: compose detection, preflight, and the detached updater."""

import json
import subprocess

import pytest

from core import compose_deployment, update_manager
from core import FlowFile

WORKDIR = "/srv/pawflow"


@pytest.fixture(autouse=True)
def _clean_cache():
    compose_deployment.reset_cache()
    yield
    compose_deployment.reset_cache()


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


def test_preflight_refuses_outside_a_compose_deployment(monkeypatch):
    _patch_compose(monkeypatch, info={})
    monkeypatch.setattr(update_manager.subprocess, "run",
                        lambda *a, **k: pytest.fail("nothing may run"))

    check = update_manager.server_update_preflight()

    assert check["ok"] is False
    assert "Compose" in check["reason"]


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
    assert lines[-1] == "docker compose up -d --build"
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
    assert script.count("|| true") >= 1


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


# -- admin actions -----------------------------------------------------


def test_server_update_actions_require_admin():
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    for action in ("admin_server_update_check", "admin_update_server"):
        ff = FlowFile(content=b"{}", attributes={"http.auth.roles": "user"})
        result = _handle_admin_settings(None, action, {}, None, "bob", ff)
        assert result[0].get_attribute("http.response.status") == "403"


def test_update_action_does_not_refuse_because_agents_are_running(monkeypatch):
    # A restart kills them, exactly as a command-line update would. The UI says
    # so; the server does not decide for the operator.
    from tasks.ai.actions import admin_settings

    monkeypatch.setattr(update_manager, "running_agent_count", lambda: 7)
    monkeypatch.setattr(update_manager, "update_server",
                        lambda pull_source=False: {"ok": True, "started": True,
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
                        lambda pull_source=False: {"ok": False,
                                                   "reason": "not a compose deployment"})

    result = admin_settings._handle_admin_settings(
        None, "admin_update_server", {}, None, "admin", _admin_flowfile())

    assert result[0].get_attribute("http.response.status") == "409"


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
    assert "/health" in js
    assert "location.reload()" in js
