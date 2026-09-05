import json

from chat_ui_testing import rendered_chat_html
import re
import subprocess
import pytest
from pathlib import Path

from core import FlowFile, update_manager

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _native_release_metadata(monkeypatch):
    monkeypatch.setattr(update_manager, "latest_native_version",
                        lambda key: "2026.09.02-c22c1a3" if key == "cursor" else "1.0.13")


def _admin_flowfile():
    return FlowFile(content=b"{}", attributes={"http.auth.roles": "admin"})


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="")


# -- version comparison ------------------------------------------------


def test_release_tag_and_packaged_version_compare_equal():
    # The git tag is semver (1.0.0-beta.35), pyproject is PEP 440 (1.0.0b35).
    # Reporting those as an available update would flag every install forever.
    assert update_manager._is_newer("1.0.0-beta.35", "1.0.0b35") is False
    assert update_manager._is_newer("1.0.0-beta.36", "1.0.0b35") is True
    assert update_manager._is_newer("1.0.0b35", "1.0.0-beta.36") is False


def test_missing_version_is_never_an_update():
    assert update_manager._is_newer("", "1.0.0") is False
    assert update_manager._is_newer("1.0.0", "") is False


# -- build-arg parity with build.sh ------------------------------------


def test_cli_packages_match_build_script_and_dockerfile():
    # build.sh (dev entry point) and update_manager (server entry point) build
    # the same image; a package pinned by one and not the other silently ships
    # a different image depending on who triggered the build.
    build_sh = (ROOT / "docker" / "claude-code" / "build.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "docker" / "claude-code" / "Dockerfile").read_text(encoding="utf-8")
    for cli in update_manager.CLI_PACKAGES:
        assert "{} {}".format(cli["package"], cli["build_arg"]) in build_sh
        assert "ARG {}=".format(cli["build_arg"]) in dockerfile


def test_dockerfile_stamps_the_versions_file_update_manager_reads():
    dockerfile = (ROOT / "docker" / "claude-code" / "Dockerfile").read_text(encoding="utf-8")
    assert update_manager.CLI_VERSIONS_PATH in dockerfile
    stamp = (ROOT / "docker" / "claude-code" / "stamp_versions.sh").read_text(encoding="utf-8")
    keys = [cli["key"] for cli in update_manager.CLI_PACKAGES]
    keys.append(update_manager.UNPINNED_CLI_KEY)
    for key in keys:
        assert '"{}"'.format(key) in stamp


# -- inventory ---------------------------------------------------------


def test_installed_cli_versions_reads_the_image_stamp(monkeypatch):
    calls = []

    def fake_run(args, timeout=30):
        calls.append(args)
        return _completed(json.dumps({"claude": "2.1.0", "antigravity": "0.9.3"}))

    monkeypatch.setattr(update_manager, "_run", fake_run)
    versions = update_manager.installed_cli_versions()

    assert versions == {"claude": "2.1.0", "antigravity": "0.9.3"}
    assert calls[0][:2] == ["image", "inspect"]
    assert calls[1][:4] == ["run", "--rm", "--entrypoint", "cat"]
    assert calls[1][-1] == update_manager.CLI_VERSIONS_PATH


def test_missing_image_is_never_pulled(monkeypatch):
    # `docker run` on an absent image would try to pull it, and this image is
    # never published anywhere: the presence check must short-circuit.
    calls = []

    def fake_run(args, timeout=30):
        calls.append(args)
        return _completed("", returncode=1)

    monkeypatch.setattr(update_manager, "_run", fake_run)

    assert update_manager.installed_cli_versions() == {}
    assert [c[0] for c in calls] == ["image"]


def test_installed_cli_versions_empty_when_stamp_unreadable(monkeypatch):
    def fake_run(args, timeout=30):
        # Image present, but the stamp predates this feature.
        return _completed("", returncode=0 if args[0] == "image" else 1)

    monkeypatch.setattr(update_manager, "_run", fake_run)
    assert update_manager.installed_cli_versions() == {}


def test_check_updates_reports_every_component_and_counts_updates(monkeypatch):
    npm = {"@anthropic-ai/claude-code": "2.1.0",
           "@openai/codex": "1.1.0",
           "@google/gemini-cli": "3.0.0", "opencode-ai": "1.18.28"}
    monkeypatch.setattr(update_manager, "server_version", lambda: "1.0.0b35")
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "1.0.0-beta.36")
    monkeypatch.setattr(update_manager, "local_image_tags", lambda repo: ["2026.07.16"])
    monkeypatch.setattr(update_manager, "catalog_relay_version", lambda: "2026.07.16")
    monkeypatch.setattr(update_manager, "latest_published_relay_tag",
                        lambda repo: "2026.07.16")
    monkeypatch.setattr(update_manager, "installed_cli_versions",
                        lambda: {"claude": "2.1.0", "codex": "1.0.0",
                                 "gemini": "3.0.0", "antigravity": "0.9.3"})
    monkeypatch.setattr(update_manager, "latest_npm_version", lambda pkg: npm[pkg])

    report = update_manager.check_updates()
    by_key = {c["key"]: c for c in report["components"]}

    assert by_key["server"]["update_available"] is True
    assert by_key["relay-dev"]["update_available"] is False
    assert by_key["codex"]["update_available"] is True
    assert by_key["claude"]["update_available"] is False
    # Antigravity has no published version: it can never be reported stale,
    # only refreshed by a forced rebuild.
    assert by_key["antigravity"]["unpinned"] is True
    assert by_key["antigravity"]["update_available"] is False
    assert report["update_count"] == 2


def test_relay_components_name_the_image_the_server_actually_spawns(monkeypatch):
    # The published repository and the tag this server runs are two different
    # names; reporting only the first made a locally rebuilt relay look absent.
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "")
    monkeypatch.setattr(update_manager, "latest_npm_version", lambda pkg: "")
    monkeypatch.setattr(update_manager, "installed_cli_versions", lambda: {})
    monkeypatch.setattr(update_manager, "catalog_relay_version", lambda: "2026.07.16")
    monkeypatch.setattr(update_manager, "latest_published_relay_tag", lambda repo: "")
    monkeypatch.setattr(update_manager, "relay_image_name",
                        lambda key: f"pawflow-{key}:latest")
    monkeypatch.setattr(update_manager, "local_image_tags",
                        lambda repo: ["latest"] if repo.startswith("pawflow-") else [])

    by_key = {c["key"]: c for c in update_manager.check_updates()["components"]}

    assert by_key["relay-dev"]["configured_image"] == "pawflow-relay-dev:latest"
    assert "pawflow-relay-dev:latest" in by_key["relay-dev"]["local_tags"]


def test_check_updates_survives_unreachable_network(monkeypatch):
    monkeypatch.setattr(update_manager, "_fetch_json", lambda url: None)
    monkeypatch.setattr(update_manager, "local_image_tags", lambda repo: [])
    monkeypatch.setattr(update_manager, "installed_cli_versions", lambda: {})

    report = update_manager.check_updates()

    assert report["update_count"] == 0
    assert report["components"]


# -- what "published" means for a relay image ---------------------------


def test_the_published_relay_version_comes_from_the_registry(monkeypatch):
    # It used to be read from the shipped catalog, which answers a different
    # question — what this server *expects* — and reported "unknown" whenever
    # that catalog was stale.
    calls = []

    def fake_fetch(url, headers=None):
        calls.append((url, headers))
        if "/token" in url:
            return {"token": "t0ken"}
        return {"tags": ["2026.06.06", "latest", "2026.07.16", "2026.06.13"]}

    monkeypatch.setattr(update_manager, "_fetch_json", fake_fetch)

    tag = update_manager.latest_published_relay_tag(
        "ghcr.io/allcolor/pawflow-relay-dev")

    assert tag == "2026.07.16"
    # A public GHCR pull still needs a bearer token, so it is two calls, and
    # the repository is asked for without its registry host.
    assert "allcolor/pawflow-relay-dev" in calls[0][0] and "ghcr.io/all" not in calls[1][0]
    assert calls[1][1]["Authorization"] == "Bearer t0ken"


def test_a_moving_tag_is_not_a_version(monkeypatch):
    monkeypatch.setattr(update_manager, "_fetch_json", lambda url, headers=None:
                        {"token": "t"} if "/token" in url else {"tags": ["latest", "main"]})

    assert update_manager.latest_published_relay_tag("ghcr.io/x/y") == ""


def test_an_unreachable_registry_falls_back_to_the_catalog(monkeypatch):
    monkeypatch.setattr(update_manager, "_fetch_json", lambda url, headers=None: None)
    monkeypatch.setattr(update_manager, "local_image_tags", lambda repo: ["2026.07.16"])
    monkeypatch.setattr(update_manager, "installed_cli_versions", lambda: {})
    monkeypatch.setattr(update_manager, "catalog_relay_version", lambda: "2026.07.16")

    by_key = {c["key"]: c for c in update_manager.check_updates()["components"]}

    assert by_key["relay-dev"]["available"] == "2026.07.16"
    assert by_key["relay-dev"]["expected"] == "2026.07.16"


def test_the_catalog_is_read_from_the_image_not_the_bind_mount(monkeypatch, tmp_path):
    """/app/config is a host mount seeded no-clobber by the entrypoint.

    An operator who installed before ``relay_image_version`` existed keeps a
    catalog without it forever, and the whole relay row went "unknown". The
    pristine copy baked into the image is the one to trust.
    """
    shipped = tmp_path / "default-config"
    shipped.mkdir()
    (shipped / "relay_image_catalog.json").write_text(
        json.dumps({"relay_image_version": "2026.07.16"}), encoding="utf-8")
    stale = tmp_path / "config"
    stale.mkdir()
    (stale / "relay_image_catalog.json").write_text(
        json.dumps({"version": 1}), encoding="utf-8")

    monkeypatch.setattr(update_manager, "DEFAULT_CONFIG_DIR", shipped)
    monkeypatch.setattr(update_manager, "APP_ROOT", tmp_path)
    assert update_manager.catalog_relay_version() == "2026.07.16"

    # No image copy (a source checkout): the repository's own config is used.
    monkeypatch.setattr(update_manager, "DEFAULT_CONFIG_DIR", tmp_path / "absent")
    assert update_manager.catalog_relay_version() == ""


# -- rebuild -----------------------------------------------------------


class _FakeProc:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._returncode = returncode

    def wait(self):
        return self._returncode


def _capture_build(monkeypatch, returncode=0, lines=None):
    captured = {}
    out = list(lines if lines is not None else ["#1 building\n", "done\n"])

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(out, returncode)

    monkeypatch.setattr(update_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(update_manager, "latest_npm_version", lambda pkg: "9.9.9")
    return captured


def test_rebuild_pins_resolved_versions_and_omits_no_cache_by_default(monkeypatch):
    captured = _capture_build(monkeypatch)

    result = update_manager.rebuild_cli_image(force=False)

    assert result["ok"] is True
    assert "--no-cache" not in captured["cmd"]
    for cli in update_manager.CLI_PACKAGES:
        assert "{}=9.9.9".format(cli["build_arg"]) in captured["cmd"]
    assert captured["cmd"][-1] == str(update_manager.CLI_BUILD_CONTEXT)


def test_forced_rebuild_passes_no_cache(monkeypatch):
    captured = _capture_build(monkeypatch)

    result = update_manager.rebuild_cli_image(force=True)

    assert result["forced"] is True
    assert "--no-cache" in captured["cmd"]


def test_rebuild_streams_output_and_reports_failure(monkeypatch):
    _capture_build(monkeypatch, returncode=1, lines=["step 1\n", "boom\n"])
    seen = []

    result = update_manager.rebuild_cli_image(force=False, on_output=seen.append)

    assert seen == ["step 1", "boom"]
    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert "boom" in result["output"]


def test_unresolvable_npm_version_falls_back_to_latest(monkeypatch):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc([])

    monkeypatch.setattr(update_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(update_manager, "latest_npm_version", lambda pkg: "")

    update_manager.rebuild_cli_image(force=False)

    for cli in update_manager.CLI_PACKAGES:
        assert "{}=latest".format(cli["build_arg"]) in captured["cmd"]


def test_concurrent_rebuild_is_refused_not_duplicated(monkeypatch):
    monkeypatch.setattr(update_manager, "latest_npm_version", lambda pkg: "9.9.9")
    inner = {}

    def fake_popen(cmd, **kwargs):
        # Re-enter while the lock is held: the second call must not build.
        inner["result"] = update_manager.rebuild_cli_image(force=False)
        return _FakeProc([])

    monkeypatch.setattr(update_manager.subprocess, "Popen", fake_popen)

    assert update_manager.cli_build_running() is False
    outer = update_manager.rebuild_cli_image(force=False)

    assert outer["ok"] is True
    assert inner["result"]["busy"] is True
    assert inner["result"]["ok"] is False
    assert update_manager.cli_build_running() is False


# -- relay images ------------------------------------------------------


def _relay_tree(tmp_path):
    """Minimal server-image layout the relay builds read from."""
    (tmp_path / "docker" / "relay-dev").mkdir(parents=True)
    (tmp_path / "docker" / "relay-dev" / "Dockerfile").write_text("FROM x", encoding="utf-8")
    (tmp_path / "docker" / "relay-generated" / "server-minimal").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "generate-relay-image.py").write_text("#", encoding="utf-8")
    return tmp_path


def _capture_relay(monkeypatch, tmp_path, codes=None):
    """Record every spawned command; ``codes`` gives their exit codes in order."""
    monkeypatch.setattr(update_manager, "APP_ROOT", _relay_tree(tmp_path))
    monkeypatch.setattr(update_manager, "relay_image_name", lambda key: "img:" + key)
    cmds = []
    exits = list(codes or [])

    def fake_popen(cmd, **kwargs):
        cmds.append(cmd)
        return _FakeProc(["step\n"], exits.pop(0) if exits else 0)

    monkeypatch.setattr(update_manager.subprocess, "Popen", fake_popen)
    return cmds


def test_relay_image_tag_is_the_one_the_relay_manager_spawns():
    # A rebuild that lands on a tag nothing runs is invisible: the build target
    # must name the same global parameter the relay manager reads.
    from core import _relay_naming

    for target in update_manager.RELAY_BUILD_TARGETS:
        assert target["param"] in _relay_naming._DEFAULTS
    assert update_manager.relay_image_name("relay-dev") == _relay_naming._cfg("server_relay_image")


def test_repo_root_context_excludes_the_mounted_runtime_dirs():
    # The workspace relay builds with the repository root as its context, and on
    # a deployed server that root holds the mounted data dirs — relay workspaces
    # included. Without these exclusions every rebuild would ship gigabytes of
    # user data to the Docker daemon.
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()

    assert "data/runtime" in ignored
    assert "data/system" in ignored


def test_unknown_relay_image_is_refused():
    try:
        update_manager.relay_build_target("relay-nope")
    except ValueError as exc:
        assert "relay-nope" in str(exc)
    else:
        raise AssertionError("unknown relay image should raise")


def test_relay_dev_build_uses_the_repo_root_context_and_its_dockerfile(monkeypatch, tmp_path):
    cmds = _capture_relay(monkeypatch, tmp_path)

    result = update_manager.rebuild_relay_image("relay-dev")

    assert result["ok"] is True
    assert len(cmds) == 1  # no generator step for this one
    cmd = cmds[0]
    # Same context/dockerfile split as .github/workflows/docker-publish.yml.
    assert "--no-cache" not in cmd
    assert cmd[cmd.index("-f") + 1] == str(tmp_path / "docker" / "relay-dev" / "Dockerfile")
    assert cmd[cmd.index("-t") + 1] == "img:relay-dev"
    assert cmd[-1] == str(tmp_path)


def test_relay_minimal_build_generates_its_context_first(monkeypatch, tmp_path):
    cmds = _capture_relay(monkeypatch, tmp_path)

    result = update_manager.rebuild_relay_image("relay-minimal", force=True)

    assert result["ok"] is True
    generate, build = cmds
    assert str(tmp_path / "scripts" / "generate-relay-image.py") in generate
    assert generate[generate.index("--profile") + 1] == "server-minimal"
    assert generate[generate.index("--image") + 1] == "img:relay-minimal"
    assert "--no-cache" in build
    assert build[-1] == str(tmp_path / "docker" / "relay-generated" / "server-minimal")
    assert "-f" not in build  # the generated context carries its own Dockerfile


def test_failed_generation_stops_before_the_build(monkeypatch, tmp_path):
    cmds = _capture_relay(monkeypatch, tmp_path, codes=[1])

    result = update_manager.rebuild_relay_image("relay-minimal")

    assert result["ok"] is False
    assert len(cmds) == 1
    assert "generation failed" in result["output"]


def test_relay_builds_do_not_run_concurrently(monkeypatch, tmp_path):
    monkeypatch.setattr(update_manager, "APP_ROOT", _relay_tree(tmp_path))
    monkeypatch.setattr(update_manager, "relay_image_name", lambda key: "img:" + key)
    inner = {}

    def fake_popen(cmd, **kwargs):
        # Re-enter with the *other* image: one relay build at a time, both ways.
        inner["result"] = update_manager.rebuild_relay_image("relay-minimal")
        return _FakeProc([])

    monkeypatch.setattr(update_manager.subprocess, "Popen", fake_popen)

    outer = update_manager.rebuild_relay_image("relay-dev")

    assert outer["ok"] is True
    assert inner["result"]["busy"] is True
    assert update_manager.relay_build_running() is False


class _FakeManager:
    def __init__(self, entries, failing=()):
        self.entries = entries
        self.failing = set(failing)
        self.calls = []

    def list_all(self):
        return self.entries

    def recreate(self, conv_id, *, kind="workspace"):
        self.calls.append((conv_id, kind))
        if conv_id in self.failing:
            raise RuntimeError("boom")
        return {"relay_id": "srv_" + conv_id}


def _patch_manager(monkeypatch, manager):
    import core.server_relay_manager as srm
    monkeypatch.setattr(srm.ServerRelayManager, "get_instance",
                        classmethod(lambda cls: manager))


def test_restart_recreates_every_relay_sequentially_and_reports_progress(monkeypatch):
    manager = _FakeManager([
        {"conv_id": "c1", "kind": "workspace"},
        {"conv_id": "c2", "kind": "minimal"},
        {"conv_id": "c3"},  # legacy metadata without a kind
    ])
    _patch_manager(monkeypatch, manager)
    seen = []

    result = update_manager.restart_server_relays(on_progress=seen.append)

    assert manager.calls == [("c1", "workspace"), ("c2", "minimal"), ("c3", "workspace")]
    assert result == {"ok": True, "total": 3, "restarted": 3, "failed": []}
    assert [p["index"] for p in seen] == [1, 2, 3]
    assert all(p["total"] == 3 and p["ok"] for p in seen)


def test_one_failed_relay_does_not_stop_the_sweep(monkeypatch):
    manager = _FakeManager(
        [{"conv_id": "c1", "kind": "workspace"}, {"conv_id": "c2", "kind": "workspace"}],
        failing=["c1"])
    _patch_manager(monkeypatch, manager)

    result = update_manager.restart_server_relays()

    assert manager.calls == [("c1", "workspace"), ("c2", "workspace")]
    assert result["restarted"] == 1
    assert result["ok"] is False
    assert result["failed"] == [{"conv_id": "c1", "kind": "workspace", "error": "boom"}]


def test_restart_is_refused_while_one_runs(monkeypatch):
    inner = {}

    class _Reentrant(_FakeManager):
        def recreate(self, conv_id, *, kind="workspace"):
            inner["result"] = update_manager.restart_server_relays()
            return super().recreate(conv_id, kind=kind)

    manager = _Reentrant([{"conv_id": "c1", "kind": "workspace"}])
    _patch_manager(monkeypatch, manager)

    update_manager.restart_server_relays()

    assert inner["result"]["busy"] is True
    assert inner["result"]["restarted"] == 0
    assert update_manager.relay_restart_running() is False


# -- restart-only helper ------------------------------------------------


def test_server_restart_preflight_resolves_the_current_compose_container(monkeypatch):
    from core import compose_deployment, installer_deployment

    monkeypatch.setattr(compose_deployment, "compose_info",
                        lambda: {"container_name": "pawflow-server"})
    monkeypatch.setattr(installer_deployment, "installer_info", lambda: {})
    monkeypatch.setattr(update_manager, "running_agent_count", lambda: 3)
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Docker version", stderr="")

    monkeypatch.setattr(update_manager.subprocess, "run", fake_run)

    result = update_manager.server_restart_preflight()

    assert result["ok"] is True
    assert result["container"] == "pawflow-server"
    assert result["running_agents"] == 3
    assert "--entrypoint" in commands[0]
    assert "docker" in commands[0]


def test_restart_server_launches_a_detached_restart_only_helper(monkeypatch):
    monkeypatch.setattr(update_manager, "server_restart_preflight", lambda: {
        "ok": True, "container": "pawflow-server", "updater_image": "docker:cli",
        "deployment": "installer",
    })
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="helper-id", stderr="")

    monkeypatch.setattr(update_manager.subprocess, "run", fake_run)

    result = update_manager.restart_server()

    assert result["ok"] is True
    assert result["container"] == update_manager.RESTARTER_CONTAINER
    launch = commands[-1]
    assert "--detach" in launch
    assert "/var/run/docker.sock:/var/run/docker.sock" in launch
    assert "sleep 5; docker restart --time 30 pawflow-server" in launch[-1]


def test_restart_server_refuses_without_a_docker_identity(monkeypatch):
    monkeypatch.setattr(update_manager, "server_restart_preflight",
                        lambda: {"ok": False, "reason": "no container"})

    assert update_manager.restart_server() == {
        "ok": False, "started": False, "reason": "no container"}


# -- admin actions -----------------------------------------------------


def test_update_actions_require_admin():
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    for action in ("admin_check_updates", "admin_rebuild_cli_image",
                   "admin_server_restart_check",
                   "admin_rebuild_relay_image", "admin_restart_relays"):
        ff = FlowFile(content=b"{}", attributes={"http.auth.roles": "user"})
        result = _handle_admin_settings(None, action, {}, None, "bob", ff)
        assert result[0].get_attribute("http.response.status") == "403"


def test_admin_check_updates_returns_the_report(monkeypatch):
    from tasks.ai.actions import admin_settings

    monkeypatch.setattr(update_manager, "check_updates",
                        lambda: {"components": [], "cli_image": "img", "update_count": 0})
    monkeypatch.setattr(update_manager, "cli_build_running", lambda: True)

    ff = _admin_flowfile()
    result = admin_settings._handle_admin_settings(
        None, "admin_check_updates", {}, None, "admin", ff)
    payload = json.loads(result[0].get_content().decode("utf-8"))

    assert payload["cli_image"] == "img"
    assert payload["build_running"] is True


def test_admin_rebuild_starts_a_background_build_and_forwards_force(monkeypatch):
    from tasks.ai.actions import admin_settings

    started = {}
    monkeypatch.setattr(
        admin_settings, "_start_cli_image_rebuild",
        lambda force, conv, user, workflow_claimed=False: started.update(
            force=force, conv=conv, user=user, claimed=workflow_claimed))
    monkeypatch.setattr(update_manager, "server_restart_preflight", lambda: {"ok": True})

    ff = _admin_flowfile()
    result = admin_settings._handle_admin_settings(
        None, "admin_rebuild_cli_image",
        {"force": True, "conversation_id": "conv-1"}, None, "admin", ff)
    payload = json.loads(result[0].get_content().decode("utf-8"))
    if admin_settings._IMAGE_UPDATE_LOCK.locked():
        admin_settings._IMAGE_UPDATE_LOCK.release()

    assert payload["started"] is True
    assert payload["forced"] is True
    assert started == {"force": True, "conv": "conv-1", "user": "admin", "claimed": True}


def test_admin_rebuild_refuses_while_an_image_workflow_runs(monkeypatch):
    from tasks.ai.actions import admin_settings

    called = []
    monkeypatch.setattr(admin_settings, "_start_cli_image_rebuild",
                        lambda *a: called.append(a))

    admin_settings._IMAGE_UPDATE_LOCK.acquire()
    try:
        ff = _admin_flowfile()
        result = admin_settings._handle_admin_settings(
            None, "admin_rebuild_cli_image", {}, None, "admin", ff)
    finally:
        admin_settings._IMAGE_UPDATE_LOCK.release()

    assert result[0].get_attribute("http.response.status") == "409"
    assert called == []


def test_failed_restart_preflight_releases_the_image_workflow_lock(monkeypatch):
    from tasks.ai.actions import admin_settings

    monkeypatch.setattr(update_manager, "server_restart_preflight",
                        lambda: {"ok": False, "reason": "no restarter"})

    result = admin_settings._handle_admin_settings(
        None, "admin_rebuild_cli_image", {}, None, "admin", _admin_flowfile())

    assert result[0].get_attribute("http.response.status") == "409"
    assert admin_settings._IMAGE_UPDATE_LOCK.locked() is False


def test_admin_rebuild_relay_image_starts_a_background_build(monkeypatch):
    from tasks.ai.actions import admin_settings

    started = {}
    monkeypatch.setattr(
        admin_settings, "_start_relay_image_rebuild",
        lambda key, force, conv, user, workflow_claimed=False: started.update(
            key=key, force=force, conv=conv, user=user, claimed=workflow_claimed))
    monkeypatch.setattr(update_manager, "relay_image_name", lambda key: "img:" + key)
    monkeypatch.setattr(update_manager, "server_restart_preflight", lambda: {"ok": True})

    result = admin_settings._handle_admin_settings(
        None, "admin_rebuild_relay_image",
        {"image": "relay-dev", "force": True, "conversation_id": "conv-1"},
        None, "admin", _admin_flowfile())
    payload = json.loads(result[0].get_content().decode("utf-8"))
    if admin_settings._IMAGE_UPDATE_LOCK.locked():
        admin_settings._IMAGE_UPDATE_LOCK.release()

    assert payload["started"] is True
    assert payload["image"] == "img:relay-dev"
    assert started == {"key": "relay-dev", "force": True, "conv": "conv-1",
                       "user": "admin", "claimed": True}


def test_admin_rebuild_relay_image_rejects_an_unknown_image(monkeypatch):
    from tasks.ai.actions import admin_settings

    called = []
    monkeypatch.setattr(admin_settings, "_start_relay_image_rebuild",
                        lambda *a: called.append(a))

    result = admin_settings._handle_admin_settings(
        None, "admin_rebuild_relay_image", {"image": "pawflow"},
        None, "admin", _admin_flowfile())

    assert result[0].get_attribute("http.response.status") == "400"
    assert called == []


def test_admin_relay_actions_refuse_while_one_runs(monkeypatch):
    from tasks.ai.actions import admin_settings

    monkeypatch.setattr(update_manager, "relay_build_running", lambda: True)
    monkeypatch.setattr(update_manager, "relay_restart_running", lambda: True)
    called = []
    monkeypatch.setattr(admin_settings, "_start_relay_image_rebuild",
                        lambda *a: called.append(a))
    monkeypatch.setattr(admin_settings, "_start_relay_restart", lambda *a: called.append(a))

    for action, body in (("admin_rebuild_relay_image", {"image": "relay-dev"}),
                         ("admin_restart_relays", {})):
        result = admin_settings._handle_admin_settings(
            None, action, body, None, "admin", _admin_flowfile())
        assert result[0].get_attribute("http.response.status") == "409"
    assert called == []


def test_admin_restart_relays_starts_the_sweep(monkeypatch):
    from tasks.ai.actions import admin_settings

    started = {}
    monkeypatch.setattr(admin_settings, "_start_relay_restart",
                        lambda conv, user: started.update(conv=conv, user=user))
    monkeypatch.setattr(update_manager, "relay_restart_running", lambda: False)

    result = admin_settings._handle_admin_settings(
        None, "admin_restart_relays", {"conversation_id": "conv-1"},
        None, "admin", _admin_flowfile())
    payload = json.loads(result[0].get_content().decode("utf-8"))

    assert payload["started"] is True
    assert started == {"conv": "conv-1", "user": "admin"}


def test_cli_rebuild_worker_continues_through_server_restart(monkeypatch):
    import threading
    from tasks.ai.actions import admin_settings

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    events = []
    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(admin_settings, "_publish_build_event",
                        lambda conv, payload: events.append(payload))
    monkeypatch.setattr(update_manager, "rebuild_cli_image", lambda **kwargs: {
        "ok": True, "image": "cli:new", "exit_code": 0, "output": "built"})
    monkeypatch.setattr(update_manager, "restart_server", lambda: {
        "ok": True, "started": True, "container": "pawflow-restarter"})

    admin_settings._start_cli_image_rebuild(False, "conv-1", "admin")

    assert [event["status"] for event in events] == ["started", "built", "restarting"]


def test_image_workflow_lock_is_released_when_the_worker_finishes(monkeypatch):
    import threading
    from tasks.ai.actions import admin_settings

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(admin_settings, "_publish_build_event", lambda *args: None)
    monkeypatch.setattr(update_manager, "rebuild_cli_image", lambda **kwargs: {
        "ok": True, "image": "cli:new", "exit_code": 0, "output": "built"})
    monkeypatch.setattr(update_manager, "restart_server",
                        lambda: {"ok": True, "started": True})
    assert admin_settings._IMAGE_UPDATE_LOCK.acquire(blocking=False)

    admin_settings._start_cli_image_rebuild(
        False, "conv-1", "admin", workflow_claimed=True)

    assert admin_settings._IMAGE_UPDATE_LOCK.locked() is False


def test_failed_cli_rebuild_never_restarts_the_server(monkeypatch):
    import threading
    from tasks.ai.actions import admin_settings

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    restarted = []
    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(admin_settings, "_publish_build_event", lambda *args: None)
    monkeypatch.setattr(update_manager, "rebuild_cli_image", lambda **kwargs: {
        "ok": False, "image": "cli:new", "exit_code": 1, "output": "boom"})
    monkeypatch.setattr(update_manager, "restart_server",
                        lambda: restarted.append(True) or {"ok": True})

    admin_settings._start_cli_image_rebuild(False, "conv-1", "admin")

    assert restarted == []


def test_relay_rebuild_worker_restarts_relays_then_pawflow(monkeypatch):
    import threading
    from tasks.ai.actions import admin_settings

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    events = []
    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(admin_settings, "_publish_conv_event",
                        lambda conv, event, payload: events.append(payload))
    monkeypatch.setattr(update_manager, "rebuild_relay_image", lambda *args, **kwargs: {
        "ok": True, "image": "relay:new", "exit_code": 0, "output": "built"})

    def restart_relays(on_progress):
        on_progress({"index": 1, "total": 1, "conv_id": "c1",
                     "kind": "workspace", "ok": True, "error": ""})
        return {"ok": True, "total": 1, "restarted": 1, "failed": []}

    monkeypatch.setattr(update_manager, "restart_server_relays", restart_relays)
    monkeypatch.setattr(update_manager, "restart_server", lambda: {
        "ok": True, "started": True, "container": "pawflow-restarter"})

    admin_settings._start_relay_image_rebuild(
        "relay-dev", False, "conv-1", "admin")

    assert [event["status"] for event in events] == [
        "started", "built", "relay_restart_started", "relay_restart_progress",
        "relays_restarted", "restarting"]


# -- UI wiring ---------------------------------------------------------


def test_updates_dialog_is_wired_into_the_gear_menu_and_sse():
    ui = ROOT / "tasks" / "io" / "chat_ui"
    template = rendered_chat_html()
    admin_js = (ui / "admin_settings.js").read_text(encoding="utf-8")
    sse_js = (ui / "sse_handlers_b.js").read_text(encoding="utf-8")

    assert "openUpdatesDialog()" in template
    assert re.search(r"function openUpdatesDialog\(", admin_js)
    assert "admin_check_updates" in admin_js
    assert "admin_rebuild_cli_image" in admin_js
    assert "admin_server_restart_check" in admin_js
    assert "Rebuild and restart" in admin_js
    assert "cli_image_build" in sse_js
    assert "adminBuildProgress" in sse_js
    assert "admin_rebuild_relay_image" in admin_js
    assert "admin_restart_relays" in admin_js
    assert re.search(r"function adminRelayBuildProgress\(", admin_js)
    assert re.search(r"function adminRelayRestartProgress\(", admin_js)
    assert "relay_image_build" in sse_js
    assert "relay_restart" in sse_js
