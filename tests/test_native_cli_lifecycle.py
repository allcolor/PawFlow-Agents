import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from core import FlowFile, update_manager
from core import native_cli_auth as auth
from tasks.ai.actions import _sf_native_cli as actions

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("key,body,expected", [
    ("cursor", 'DOWNLOAD_URL="https://downloads.cursor.com/lab/2026.09.02-c22c1a3/linux/x64/agent-cli-package.tar.gz"', "2026.09.02-c22c1a3"),
    ("cursor", "installer changed", ""),
    ("grok", "1.0.13\n", "1.0.13"),
    ("grok", "1.0.13; touch /danger", ""),
    ("grok", "<html>failure</html>", ""),
])
def test_official_native_release_resolution(monkeypatch, key, body, expected):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(text=body, raise_for_status=lambda: None)
    monkeypatch.setattr("requests.get", get)
    assert update_manager.latest_native_version(key) == expected
    assert calls[0][1]["timeout"] == update_manager.HTTP_TIMEOUT


def test_native_release_network_failure_is_unknown(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("offline")
    monkeypatch.setattr("requests.get", fail)
    assert update_manager.latest_native_version("cursor") == ""


@pytest.mark.parametrize("current,latest,newer", [
    ("2026.09.01-aaa", "2026.09.02-bbb", True),
    ("2026.09.02-aaa", "2026.09.01-bbb", False),
    ("2026.09.02-aaa", "2026.09.02-bbb", False),
    ("unknown", "2026.09.02-bbb", False),
])
def test_cursor_dates_do_not_compare_hashes(current, latest, newer):
    assert update_manager._component("cursor", "Cursor", current, latest)["update_available"] is newer


def test_native_resolver_failure_prevents_build(monkeypatch):
    monkeypatch.setattr(update_manager, "latest_npm_version", lambda pkg: "1.0.0")
    monkeypatch.setattr(update_manager, "latest_native_version", lambda key: "")
    def unexpected(*args, **kwargs):
        pytest.fail("Docker must not start after release lookup failed")
    monkeypatch.setattr(update_manager, "_stream_command", unexpected)
    result = update_manager.rebuild_cli_image()
    assert not result["ok"]
    assert "Could not resolve latest Cursor" in result["output"]
    assert not update_manager.cli_build_running()


def test_native_build_arguments_and_stamp_are_wired(monkeypatch):
    monkeypatch.setattr(update_manager, "latest_npm_version", lambda pkg: "1.18.28")
    monkeypatch.setattr(update_manager, "latest_native_version",
                        lambda key: "2026.09.02-abc" if key == "cursor" else "1.0.13")
    args = update_manager._resolved_build_args()
    assert "CURSOR_VERSION=2026.09.02-abc" in args
    assert "GROK_BUILD_VERSION=1.0.13" in args
    assert "OPENCODE_VERSION=1.18.28" in args
    dockerfile = (ROOT / "docker/claude-code/Dockerfile").read_text()
    resolver = (ROOT / "docker/claude-code/resolve_native_versions.cjs").read_text()
    stamp = (ROOT / "docker/claude-code/stamp_versions.sh").read_text()
    for cli in update_manager.NATIVE_CLIS:
        assert f"ARG {cli['build_arg']}=" in dockerfile
        assert cli["build_arg"] in resolver
        assert f'"{cli["key"]}"' in stamp
    assert "ln -s /opt/pawflow/cursor/cursor-agent /usr/local/bin/cursor-agent" in dockerfile
    assert "/usr/local/bin/agent" not in dockerfile


def test_stamp_keeps_full_cursor_build_id(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for binary, version in [("cursor-agent", "2026.09.02-c22c1a3"), ("grok", "1.0.13"), ("opencode", "1.18.28")]:
        executable = binaries / binary
        executable.write_text("#!/bin/sh\nprintf '%s\\n' " + version + "\n")
        executable.chmod(0o755)
    result = subprocess.run(["bash", str(ROOT / "docker/claude-code/stamp_versions.sh")],
                            env={**os.environ, "PATH": str(binaries) + ":/usr/bin:/bin"},
                            capture_output=True, text=True, timeout=10)
    data = json.loads(result.stdout)
    assert data["cursor"] == "2026.09.02-c22c1a3"
    assert data["grok"] == "1.0.13"
    assert data["opencode"] == "1.18.28"


def test_service_homes_are_isolated(monkeypatch, tmp_path):
    monkeypatch.setattr("core.paths.RUNTIME_DIR", tmp_path)
    first = auth.native_cli_home("cursor-acp", "user", "service")
    assert first == auth.native_cli_home("cursor-acp", "user", "service")
    assert first != auth.native_cli_home("cursor-acp", "other", "service")
    assert first != auth.native_cli_home("grok-build-acp", "user", "service")
    assert first != auth.native_cli_home("cursor-acp", "user", "other")


@pytest.mark.parametrize("provider", ["cursor-acp", "grok-build-acp", "opencode"])
def test_identity_is_required(provider):
    with pytest.raises(ValueError):
        auth.native_cli_home(provider, "", "service")


def test_merge_keeps_other_profiles_permissions_and_inode(tmp_path):
    target = tmp_path / ".local/share/opencode/auth.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"keep": {"key": "old"}, "replace": {"key": "old"}}))
    inode = target.stat().st_ino
    auth.merge_native_auth(tmp_path, ".local/share/opencode/auth.json", {"replace": {"key": "new"}})
    assert json.loads(target.read_text()) == {"keep": {"key": "old"}, "replace": {"key": "new"}}
    assert target.stat().st_ino == inode
    assert target.stat().st_mode & 0o777 == 0o600


def test_bad_existing_auth_is_not_overwritten(tmp_path):
    target = tmp_path / "auth.json"
    target.write_text("invalid")
    with pytest.raises(ValueError):
        auth.merge_native_auth(tmp_path, "auth.json", {"provider": {}})
    assert target.read_text() == "invalid"


def test_status_never_exposes_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "native_cli_home", lambda *args: tmp_path)
    auth.merge_native_auth(tmp_path, ".grok/auth.json", {"xai": {"key": "secret"}})
    assert auth.native_cli_auth_status("grok-build-acp", "user", "service") == {
        "stored": True, "verified": False}


def test_login_commands_use_explicit_cli_names():
    spec = importlib.util.spec_from_file_location("native_login", ROOT / "docker/claude-code/native_cli_login.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.login_command("cursor-acp", "cursor-agent") == ["cursor-agent", "login"]
    assert module.login_command("grok-build-acp", "grok") == ["grok", "--no-auto-update", "login", "--device-auth"]
    assert module.login_command("opencode", "opencode") == ["opencode", "auth", "login"]


def test_login_session_owner_and_service_are_bound(monkeypatch):
    from services import vnc_proxy
    session = {"native_provider": "cursor-acp", "owner_user_id": "owner", "service_id": "service"}
    monkeypatch.setattr(vnc_proxy, "_sessions", {"session": session})
    assert actions._session_for_request({"session_id": "session"}, "owner") is session
    with pytest.raises(ValueError):
        actions._session_for_request({"session_id": "session"}, "other")
    with pytest.raises(ValueError):
        actions._session_for_request({"session_id": "session", "service_id": "other"}, "owner")


def _action(monkeypatch, action, config, roles=""):
    monkeypatch.setattr(actions, "_resolve_service_definition_for_action",
                        lambda *args: SimpleNamespace(config=config))
    ff = FlowFile(content=b"{}", attributes={"http.auth.roles": roles})
    result = actions._handle_sf_native_cli(None, action, {"service_id": "svc"},
                                          None, "user", ff, None)
    return json.loads(result[0].content)


def test_external_server_cannot_launch_managed_login(monkeypatch):
    def unexpected(*args):
        pytest.fail("External server must not start managed login")
    monkeypatch.setattr(actions, "_start_login", unexpected)
    result = _action(monkeypatch, "native_cli_server_login",
                     {"provider": "opencode", "opencode_mode": "external"})
    assert "External OpenCode" in result["error"]


@pytest.mark.parametrize("provider,field,key", [
    ("cursor-acp", "acp_env", "CURSOR_API_KEY"),
    ("grok-build-acp", "acp_env", "XAI_API_KEY"),
    ("opencode", "opencode_env", "ANTHROPIC_API_KEY"),
])
@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("value,expected", [("", False), ("   ", False), ("test-provider-value", True)])
def test_native_status_reports_provider_environment_without_values(
    monkeypatch, provider, field, key, encoded, value, expected,
):
    monkeypatch.setattr(actions, "native_cli_auth_status", lambda *_: {
        "stored": False, "verified": False})
    env = {key: value}
    result = _action(monkeypatch, "native_cli_status", {
        "provider": provider, field: json.dumps(env) if encoded else env})
    assert result["provider_environment_configured"] is expected
    assert result["verified"] is False
    assert "test-provider-value" not in json.dumps(result)
    assert ("Provider environment variables are configured." in result["message"]) is expected


def test_update_action_requires_admin_and_matching_image(monkeypatch):
    monkeypatch.setattr(actions, "native_cli_image", lambda provider: "pawflow-claude-code:latest")
    monkeypatch.setattr(update_manager, "cli_image_name", lambda: "pawflow-claude-code:latest")
    assert "administrator" in _action(monkeypatch, "native_cli_update", {"provider": "cursor-acp"})["error"]
    assert _action(monkeypatch, "native_cli_update", {"provider": "cursor-acp"}, "admin")["open_updates"]
    monkeypatch.setattr(actions, "native_cli_image", lambda provider: "custom:latest")
    assert "custom image" in _action(monkeypatch, "native_cli_update", {"provider": "cursor-acp"}, "admin")["error"]


def test_version_report_reads_runtime_image(monkeypatch):
    calls = []
    monkeypatch.setattr(actions, "native_cli_image", lambda provider: "custom:tag")
    monkeypatch.setattr(update_manager, "installed_cli_versions",
                        lambda image: calls.append(image) or {"opencode": "1.18.27"})
    monkeypatch.setattr(update_manager, "latest_npm_version", lambda pkg: "1.18.28")
    result = actions._versions("opencode", {})
    assert calls == ["custom:tag"]
    assert result["update_available"] is True


def test_ui_routes_native_login_and_existing_update_dialog():
    source = (ROOT / "tasks/io/chat_ui/resources_service_login.js").read_text()
    assert "'native': 'native_cli_server_login_status'" in source
    assert "'native': 'native_cli_server_login_cleanup'" in source
    assert "flow === 'native_cli_login_server'" in source
    assert "if (resp.open_updates) openUpdatesDialog()" in source


@pytest.mark.parametrize("provider,binary", [("cursor-acp", "cursor-agent"), ("grok-build-acp", "grok"), ("opencode", "opencode")])
def test_login_container_uses_runtime_contract(monkeypatch, tmp_path, provider, binary):
    monkeypatch.setattr(actions, "native_cli_home", lambda *args: tmp_path)
    monkeypatch.setattr(actions, "native_cli_image", lambda *args: "test-image:tag")
    monkeypatch.setattr(actions, "native_cli_binary", lambda *args: binary)
    monkeypatch.setattr(actions, "native_cli_user_spec", lambda: "1234:5678")
    monkeypatch.setattr("core.docker_utils.translate_path", lambda path: path)
    monkeypatch.setattr("core.docker_utils.to_host_path", lambda path: path)
    monkeypatch.setattr("core.docker_utils.pawflow_container_labels", lambda *args: [])
    argv = actions._login_argv(provider, "user", "service", "login-container", 12345)
    assert "PAWFLOW_NATIVE_USER=1234:5678" in argv
    assert f"PAWFLOW_NATIVE_BIN={binary}" in argv
    assert argv[-2:] == ["test-image:tag", "/opt/pawflow/native_cli_login.py"]
    assert argv[argv.index("--pull") + 1] == "never"
    assert (str(tmp_path) + ":/native-home" in argv) is (provider == "cursor-acp")


def test_completed_opencode_login_preserves_existing_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(actions, "native_cli_home", lambda *args: tmp_path)
    auth.merge_native_auth(tmp_path, ".local/share/opencode/auth.json", {"other": {"key": "keep"}})
    monkeypatch.setattr(actions, "_docker", lambda *args: SimpleNamespace(
        returncode=0, stdout=json.dumps({"new": {"key": "added"}})))
    actions._complete_login("opencode", "user", "service", "container")
    saved = json.loads((tmp_path / ".local/share/opencode/auth.json").read_text())
    assert saved == {"other": {"key": "keep"}, "new": {"key": "added"}}


def test_failed_credential_collection_preserves_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(actions, "native_cli_home", lambda *args: tmp_path)
    auth.merge_native_auth(tmp_path, ".grok/auth.json", {"old": {"key": "keep"}})
    monkeypatch.setattr(actions, "_docker", lambda *args: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError):
        actions._complete_login("grok-build-acp", "user", "service", "container")
    assert json.loads((tmp_path / ".grok/auth.json").read_text()) == {"old": {"key": "keep"}}


def test_login_worker_and_cached_status(monkeypatch):
    from services import vnc_proxy
    from core.conversation_event_bus import ConversationEventBus
    sessions = {}
    workers = []
    published = []
    collected = []
    removed = []
    monkeypatch.setattr(vnc_proxy, "_sessions", sessions)
    def register(session_id, port, **kwargs):
        sessions[session_id] = kwargs
        return "capability-token"
    monkeypatch.setattr(vnc_proxy, "register_session", register)
    monkeypatch.setattr(vnc_proxy, "update_session_ready", lambda sid: None)
    monkeypatch.setattr("pawflow_relay.utils.find_free_port", lambda: 12345)
    monkeypatch.setattr(actions, "_docker_published_host", lambda: "127.0.0.1")
    monkeypatch.setattr(actions, "_docker_container_ip", lambda name: "127.0.0.1")
    monkeypatch.setattr(actions, "_wait_for_vnc_login_backend", lambda *args: True)
    monkeypatch.setattr(actions, "_ensure_vnc_routes", lambda *args: None)
    monkeypatch.setattr(actions, "_login_argv", lambda *args: ["run"])
    monkeypatch.setattr(actions, "_docker", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout='{"ok":true}'))
    monkeypatch.setattr(actions, "_complete_login", lambda *args: collected.append(args))
    monkeypatch.setattr(actions, "_remove_container", lambda name: removed.append(name))
    monkeypatch.setattr(ConversationEventBus, "instance", lambda: SimpleNamespace(
        publish_event=lambda *args: published.append(args)))
    class DeferredThread:
        def __init__(self, target, **kwargs):
            workers.append(target)
        def start(self):
            pass
    monkeypatch.setattr(actions.threading, "Thread", DeferredThread)
    ff = FlowFile(content=b"{}", attributes={"auth.session_id": "web-session"})
    response = actions._start_login("opencode", "service", "owner", "conv", ff)
    session_id = json.loads(response[0].content)["session_id"]
    assert sessions[session_id]["native_status"] == {"status": "starting"}
    assert sessions[session_id]["ttl_seconds"] == 360
    assert not collected
    workers[0]()
    assert len(collected) == 1 and removed
    assert published[0][1] == "vnc_login_ready"
    assert published[0][2]["cli"] == "native"
    monkeypatch.setattr(actions, "_docker", lambda *args: pytest.fail("Polling must not call Docker"))
    response = actions._handle_sf_native_cli(None, "native_cli_server_login_status",
        {"session_id": session_id, "service_id": "service"}, None, "owner", ff, None)
    assert json.loads(response[0].content)["ok"] is True
