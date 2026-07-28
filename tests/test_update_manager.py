import json
import re
import subprocess
from pathlib import Path

from core import FlowFile, update_manager

ROOT = Path(__file__).resolve().parents[1]


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
           "@google/gemini-cli": "3.0.0"}
    monkeypatch.setattr(update_manager, "server_version", lambda: "1.0.0b35")
    monkeypatch.setattr(update_manager, "latest_server_release", lambda: "1.0.0-beta.36")
    monkeypatch.setattr(update_manager, "local_image_tags", lambda repo: ["2026.07.16"])
    monkeypatch.setattr(update_manager, "catalog_relay_version", lambda: "2026.07.16")
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


# -- admin actions -----------------------------------------------------


def test_update_actions_require_admin():
    from tasks.ai.actions.admin_settings import _handle_admin_settings

    for action in ("admin_check_updates", "admin_rebuild_cli_image",
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
        lambda force, conv, user: started.update(force=force, conv=conv, user=user))
    monkeypatch.setattr(update_manager, "cli_build_running", lambda: False)

    ff = _admin_flowfile()
    result = admin_settings._handle_admin_settings(
        None, "admin_rebuild_cli_image",
        {"force": True, "conversation_id": "conv-1"}, None, "admin", ff)
    payload = json.loads(result[0].get_content().decode("utf-8"))

    assert payload["started"] is True
    assert payload["forced"] is True
    assert started == {"force": True, "conv": "conv-1", "user": "admin"}


def test_admin_rebuild_refuses_while_a_build_runs(monkeypatch):
    from tasks.ai.actions import admin_settings

    monkeypatch.setattr(update_manager, "cli_build_running", lambda: True)
    called = []
    monkeypatch.setattr(admin_settings, "_start_cli_image_rebuild",
                        lambda *a: called.append(a))

    ff = _admin_flowfile()
    result = admin_settings._handle_admin_settings(
        None, "admin_rebuild_cli_image", {}, None, "admin", ff)

    assert result[0].get_attribute("http.response.status") == "409"
    assert called == []


def test_admin_rebuild_relay_image_starts_a_background_build(monkeypatch):
    from tasks.ai.actions import admin_settings

    started = {}
    monkeypatch.setattr(
        admin_settings, "_start_relay_image_rebuild",
        lambda key, force, conv, user: started.update(
            key=key, force=force, conv=conv, user=user))
    monkeypatch.setattr(update_manager, "relay_build_running", lambda: False)
    monkeypatch.setattr(update_manager, "relay_image_name", lambda key: "img:" + key)

    result = admin_settings._handle_admin_settings(
        None, "admin_rebuild_relay_image",
        {"image": "relay-dev", "force": True, "conversation_id": "conv-1"},
        None, "admin", _admin_flowfile())
    payload = json.loads(result[0].get_content().decode("utf-8"))

    assert payload["started"] is True
    assert payload["image"] == "img:relay-dev"
    assert started == {"key": "relay-dev", "force": True, "conv": "conv-1", "user": "admin"}


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


# -- UI wiring ---------------------------------------------------------


def test_updates_dialog_is_wired_into_the_gear_menu_and_sse():
    ui = ROOT / "tasks" / "io" / "chat_ui"
    template = (ui / "template.html").read_text(encoding="utf-8")
    admin_js = (ui / "admin_settings.js").read_text(encoding="utf-8")
    sse_js = (ui / "sse_handlers_b.js").read_text(encoding="utf-8")

    assert "openUpdatesDialog()" in template
    assert re.search(r"function openUpdatesDialog\(", admin_js)
    assert "admin_check_updates" in admin_js
    assert "admin_rebuild_cli_image" in admin_js
    assert "cli_image_build" in sse_js
    assert "adminBuildProgress" in sse_js
    assert "admin_rebuild_relay_image" in admin_js
    assert "admin_restart_relays" in admin_js
    assert re.search(r"function adminRelayBuildProgress\(", admin_js)
    assert re.search(r"function adminRelayRestartProgress\(", admin_js)
    assert "relay_image_build" in sse_js
    assert "relay_restart" in sse_js
