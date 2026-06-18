from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeStore:
    def __init__(self):
        self.data = {}

    def get_extra_cached(self, _cid, _key, default=None):
        return self.data or default

    def set_extra(self, _cid, _key, value):
        self.data = value


class _FakeConversationStore:
    _instance = None

    @classmethod
    def instance(cls):
        return cls._instance


def test_mount_dir_is_derived_from_service_id():
    from core.remote_fs_bindings import mount_path_for, sanitize_mount_dir

    assert sanitize_mount_dir("gdrive-main") == "gdrive-main"
    assert sanitize_mount_dir("gdrive/main") == "gdrive_main"
    assert mount_path_for("sftp:prod") == "/remote/sftp_prod"


def test_mount_dir_rejects_empty_after_sanitize():
    from core.remote_fs_bindings import sanitize_mount_dir

    with pytest.raises(ValueError):
        sanitize_mount_dir("///")


def test_link_accepts_global_native_filesystem_as_tool_binding(monkeypatch):
    from core import remote_fs_bindings as rfb

    store = _FakeStore()
    monkeypatch.setattr("core.conversation_store.ConversationStore", _FakeConversationStore)
    _FakeConversationStore._instance = store

    monkeypatch.setattr(rfb, "_resolve_service_definition", lambda *_args, **_kwargs: SimpleNamespace(
        service_id="gdrive", service_type="googleDrive", scope="global", config={}, description=""))

    monkeypatch.setattr(rfb, "notify_linked_relays", lambda *_args, **_kwargs: None)

    linked = rfb.link_filesystem("conv1", "user1", "gdrive")

    assert linked["service_type"] == "googleDrive"
    assert linked["scope"] == "global"
    assert linked["backend"] == "service"
    assert linked["access"] == "tools"
    assert linked["mount_path"] == ""
    assert rfb.list_tool_filesystems("user1", "conv1") == [{
        "id": "gdrive",
        "type": "googleDrive",
        "scope": "global",
        "access": "tools",
    }]
    assert rfb.build_manifest_for_conversation("user1", "conv1") == {
        "conversation_id": "conv1",
        "mounts": [],
    }


def test_link_accepts_native_api_filesystem_as_tool_binding(monkeypatch):
    from core import remote_fs_bindings as rfb

    store = _FakeStore()
    monkeypatch.setattr("core.conversation_store.ConversationStore", _FakeConversationStore)
    _FakeConversationStore._instance = store

    monkeypatch.setattr(rfb, "_resolve_service_definition", lambda *_args, **_kwargs: SimpleNamespace(
        service_id="gdrive", service_type="googleDrive", scope="user", config={}, description=""))

    monkeypatch.setattr(rfb, "notify_linked_relays", lambda *_args, **_kwargs: None)

    linked = rfb.link_filesystem("conv1", "user1", "gdrive")

    assert linked["service_type"] == "googleDrive"
    assert linked["backend"] == "service"
    assert linked["access"] == "tools"
    assert linked["mount_path"] == ""


def test_link_rejects_sanitized_mount_collision(monkeypatch):
    from core import remote_fs_bindings as rfb

    store = _FakeStore()
    store.data = {"linked": [{"service_id": "gdrive/main", "scope": "user"}]}
    monkeypatch.setattr("core.conversation_store.ConversationStore", _FakeConversationStore)
    _FakeConversationStore._instance = store

    monkeypatch.setattr(rfb, "_resolve_service_definition", lambda *_args, **_kwargs: SimpleNamespace(
        service_id="gdrive:main", service_type="rcloneFilesystem", scope="user", config={}, description=""))

    with pytest.raises(ValueError, match="already used"):
        rfb.link_filesystem("conv1", "user1", "gdrive:main")


def test_relay_worker_handles_remote_mount_manifest():
    src = Path("pawflow_relay/worker.py").read_text(encoding="utf-8")

    assert "remote_mount_manifest" in src
    assert "_remote_mount_mgr.reconcile" in src
    assert "remote-mount-reconcile" in src


def test_remote_mount_manager_serializes_reconcile():
    src = Path("pawflow_relay/remote_mounts.py").read_text(encoding="utf-8")

    assert "self._lock = threading.Lock()" in src
    assert "with self._lock:" in src
    assert "def _ensure_mountpoint" in src
    assert '"chown", f"{uid}:{gid}", str(target)' in src
    assert "returned success but" in src


def test_resource_sidebar_renders_rclone_filesystem_bindings():
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(Path("tasks/io/chat_ui").glob("resources*.js")))

    assert "_remote_fs" in src
    assert "remote_filesystems" in src
    assert "_showRemoteFsLinkDialog" in src
    assert "s.service_type === 'rcloneFilesystem'" in src
    assert "pdef.label || pname" in src
    assert "wrapper.style.display === 'none'" in src


def test_rclone_service_schema_is_backend_dependent():
    from services.rclone_filesystem_service import RcloneFilesystemService

    svc = RcloneFilesystemService({})
    schema = svc.get_parameter_schema()
    rules = svc.get_parameter_rules()

    assert schema["rclone_type"]["label"] == "Backend type"
    assert schema["credential_service_id"]["type"] == "service_ref"
    assert schema["credential_service_id"]["service_type"] == "rcloneOAuthCredentials"
    assert "url" in schema
    assert "account" in schema
    assert "service_account_file" in schema

    by_type = {rule["when"]["rclone_type"]: rule["set"] for rule in rules}
    assert by_type["sftp"]["host"]["required"] is True
    assert by_type["sftp"]["rclone_config"]["visible"] is True
    assert by_type["sftp"]["provider"]["visible"] is False
    assert by_type["s3"]["provider"]["visible"] is True
    assert by_type["s3"]["host"]["visible"] is False
    assert by_type["webdav"]["url"]["required"] is True
    assert by_type["drive"]["credential_service_id"]["required"] is True
    assert by_type["drive"]["rclone_config"]["visible"] is False
    assert by_type["onedrive"]["credential_service_id"]["required"] is True
    assert by_type["onedrive"]["rclone_config"]["visible"] is False
    assert RcloneFilesystemService({}).get_service_actions() == []


def test_rclone_config_omits_empty_guided_fields():
    from core.remote_fs_bindings import _rclone_config_for

    sdef = SimpleNamespace(
        service_id="s3_docs",
        service_type="rcloneFilesystem",
        config={
            "rclone_type": "s3",
            "provider": "AWS",
            "access_key_id": "AKIA...",
            "secret_access_key": "",
            "endpoint": "",
            "mode": "readwrite",
        },
    )

    assert _rclone_config_for("user1", "conv1", sdef) == {
        "type": "s3",
        "provider": "AWS",
        "access_key_id": "AKIA...",
    }


def test_rclone_oauth_config_comes_from_credential_service(monkeypatch):
    from core.remote_fs_bindings import _rclone_config_for

    marker = "$" + "{rclone_body}"
    sdef = SimpleNamespace(
        service_id="gdrive",
        service_type="rcloneFilesystem",
        config={"rclone_type": "drive", "credential_service_id": "gdrive_creds"},
    )
    cred = SimpleNamespace(
        service_id="gdrive_creds",
        service_type="rcloneOAuthCredentials",
        config={"provider": "drive", "rclone_config": marker},
    )

    def fake_resolve(value, **kwargs):
        assert kwargs["owner"] == "user1"
        assert kwargs["conversation_id"] == "conv1"
        return "type = drive\ntoken = {...}"

    monkeypatch.setattr("core.remote_fs_bindings._resolve_rclone_credential_definition", lambda *_args: cred)
    monkeypatch.setattr("core.expression.resolve_expression", fake_resolve)

    assert _rclone_config_for("user1", "conv1", sdef) == {
        "_raw": "type = drive\ntoken = {...}",
    }


def test_rclone_oauth_credentials_expose_login_action_and_sensitive_config():
    from services.rclone_oauth_credentials import RcloneOAuthCredentialsService

    svc = RcloneOAuthCredentialsService({"provider": "drive"})
    schema = svc.get_parameter_schema()
    actions = svc.get_service_actions()

    assert schema["rclone_config"]["sensitive"] is True
    login = next(a for a in actions if a["id"] == "rclone_server_login")
    assert login["server_action"] == "rclone_server_login"
    assert login["flow"] == "rclone_login_server"
    assert login["when"] == {"provider": ["drive", "onedrive"]}


def test_chat_ui_routes_rclone_oauth_through_vnc_dialog():
    src = "".join(p.read_text(encoding="utf-8") for p in sorted(Path("tasks/io/chat_ui").glob("resources*.js")))
    sse = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")

    assert "svc-install-login-btn" in src
    assert "_submitServiceInstall(true)" in src
    assert "typeEl.value === 'rcloneOAuthCredentials'" in src
    assert "fireAction('rclone_server_login', { service_id: name, scope });" in src
    assert "'rclone': 'rclone_server_login_status'" in src
    assert "'rclone': 'rclone_server_login_cleanup'" in src
    assert "flow === 'rclone_login_server'" in src
    assert "if (scope) payload.scope = scope;" in src
    assert "data.scope || ''" in sse


def test_service_flow_saves_rclone_login_result_into_sensitive_service_config():
    src = Path("tasks/ai/actions/service_flow.py").read_text(encoding="utf-8")

    assert 'action == "rclone_server_login"' in src
    assert '"cli": "rclone"' in src
    assert 'sdef.service_type != "rcloneOAuthCredentials"' in src
    assert "PAWFLOW_RCLONE_TYPE" in src
    assert 'action == "rclone_server_login_status"' in src
    assert 'result_dir = "/tmp/pawflow-rclone-login"' in src
    assert '"rclone_config": rclone_config' in src
    assert "notify_linked_relays" in src


def test_service_flow_notifies_relays_after_rclone_service_update():
    src = Path("tasks/ai/actions/service_flow.py").read_text(encoding="utf-8")

    assert "def _notify_remote_mounts_after_service_change" in src
    assert '"rcloneFilesystem", "rcloneOAuthCredentials"' in src
    assert "sdef = registry.get_definition(scope, scope_id, sid)" in src
    assert "_notify_remote_mounts_after_service_change(sdef, conv_id, user_id)" in src


def test_manifest_skips_stale_non_rclone_bindings(monkeypatch):
    from core import remote_fs_bindings as rfb

    store = _FakeStore()
    store.data = {"linked": [{
        "service_id": "gdrive",
        "service_type": "googleDrive",
        "scope": "user",
        "enabled": True,
    }]}
    monkeypatch.setattr("core.conversation_store.ConversationStore", _FakeConversationStore)
    _FakeConversationStore._instance = store

    manifest = rfb.build_manifest_for_conversation("user1", "conv1")

    assert manifest["mounts"] == []


def test_relay_worker_reconciles_remote_mount_manifest_in_background_thread():
    src = Path("pawflow_relay/worker.py").read_text(encoding="utf-8")
    runtime = Path("pawflow-relay-desktop/runtime/pawflow_relay/worker.py").read_text(encoding="utf-8")

    for text in (src, runtime):
        assert '_mtype == "remote_mount_manifest"' in text
        assert "target=_reconcile_remote_mounts" in text
        assert 'name="remote-mount-reconcile"' in text


def test_remote_mount_manager_does_not_mark_false_daemon_success_active(monkeypatch, tmp_path):
    from pawflow_relay.remote_mounts import RemoteMountManager

    manager = RemoteMountManager(
        remote_root=str(tmp_path / "remote"),
        state_dir=str(tmp_path / "state"),
    )
    monkeypatch.setattr("pawflow_relay.remote_mounts.shutil.which", lambda name: "/usr/bin/rclone")
    monkeypatch.setattr(manager, "_ensure_root", lambda: True)
    monkeypatch.setattr(manager, "_ensure_mountpoint", lambda target: True)
    monkeypatch.setattr(manager, "_run", lambda argv, what: True)
    monkeypatch.setattr(manager, "_is_mounted", lambda name: False)

    manager.reconcile({"mounts": [{
        "remote_name": "MyGDrive",
        "mode": "readwrite",
        "rclone_config": {"_raw": "[MyGDrive]\ntype = drive\ntoken = {}"},
    }]})

    assert manager._active == {}


def test_relay_image_installs_rclone():
    src = Path("docker/relay-dev/Dockerfile").read_text(encoding="utf-8")

    assert "downloads.rclone.org/rclone-${DOWNLOAD_RCLONE_VERSION}-linux-amd64.zip" in src
    assert "install -m 0755 /tmp/rclone-dist/*/rclone /usr/local/bin/rclone" in src
    assert 'ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/init.sh"]' in src
    assert "ARG RCLONE_" not in src
    assert " zip rclone " not in src


def test_agent_login_image_installs_rclone_and_copies_login_script():
    src = Path("docker/claude-code/Dockerfile").read_text(encoding="utf-8")
    script = Path("docker/claude-code/rclone_auth_login.sh").read_text(encoding="utf-8")

    assert "downloads.rclone.org/rclone-${DOWNLOAD_RCLONE_VERSION}-linux-amd64.zip" in src
    assert "install -m 0755 /tmp/rclone-dist/*/rclone /usr/local/bin/rclone" in src
    assert 'ENTRYPOINT ["/usr/bin/tini", "--"]' in src
    assert 'CMD ["claude"]' in src
    assert "ARG RCLONE_" not in src
    assert "PAWFLOW_RCLONE_TYPE" in script
    assert " chrony rclone " not in src
    assert "rclone_auth_login.sh" in src
    assert "rclone config create" in script
    assert "/workspace/rclone" not in script
    assert "/tmp/pawflow-rclone-login" in script
    assert "rclone_config_body.txt" in script


def test_agent_login_image_installs_antigravity_cli():
    src = Path("docker/claude-code/Dockerfile").read_text(encoding="utf-8")

    assert "https://antigravity.google/cli/install.sh" in src
    assert "bash -s -- --dir /usr/local/bin" in src


def test_agent_cli_image_installs_bubblewrap_for_codex_sandbox():
    src = Path("docker/claude-code/Dockerfile").read_text(encoding="utf-8")

    assert " bubblewrap " in src
    assert " tini " in src


def test_agent_pool_containers_keep_tini_as_pid_one():
    for path in (
        "core/claude_code_pool.py",
        "core/codex_pool.py",
        "core/gemini_pool.py",
        # interactive pool's container run-args live in the split-out spawn module
        "core/_cci_pool_spawn.py",
        "core/antigravity_observer_pool.py",
    ):
        if path.endswith("thread.py"):
            src = "".join(q.read_text(encoding="utf-8") for q in sorted(Path(path).parent.glob("*thread*.py")))
        else:
            src = Path(path).read_text(encoding="utf-8")
        assert '"--init"' in src
        assert '"--entrypoint", "/usr/bin/sleep"' in src
        assert '"/usr/bin/sleep"' in src
        assert '"infinity"' in src


def test_container_launchers_request_docker_init():
    for path in (
        "core/server_relay_manager.py",
        "pawflow_relay/cli.py",
        "pawflow_relay/thread.py",
        "pawflow_relay/worker.py",
        "tools/fs_exec.py",
        "pawflow-relay-desktop/runtime/tools/fs_exec.py",
        "pawflow-relay-desktop/runtime/pawflow_relay/cli.py",
        "pawflow-relay-desktop/runtime/pawflow_relay/thread.py",
        "pawflow-relay-desktop/runtime/pawflow_relay/worker.py",
    ):
        if path.endswith("thread.py"):
            src = "".join(q.read_text(encoding="utf-8") for q in sorted(Path(path).parent.glob("*thread*.py")))
        else:
            src = Path(path).read_text(encoding="utf-8")
        assert '"--init"' in src

    docker_utils = Path("core/docker_utils.py").read_text(encoding="utf-8")
    assert "def _with_docker_init" in docker_utils
    assert 'return ["--init", *args]' in docker_utils
