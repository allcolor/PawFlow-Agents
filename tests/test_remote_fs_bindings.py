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


def test_resource_sidebar_renders_rclone_filesystem_bindings():
    src = Path("tasks/io/chat_ui/resources.js").read_text(encoding="utf-8")

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
    assert "url" in schema
    assert "account" in schema
    assert "service_account_file" in schema

    by_type = {rule["when"]["rclone_type"]: rule["set"] for rule in rules}
    assert by_type["sftp"]["host"]["required"] is True
    assert by_type["sftp"]["provider"]["visible"] is False
    assert by_type["s3"]["provider"]["visible"] is True
    assert by_type["s3"]["host"]["visible"] is False
    assert by_type["webdav"]["url"]["required"] is True
    assert by_type["drive"]["rclone_config"]["required"] is True


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

    assert _rclone_config_for("user1", sdef) == {
        "type": "s3",
        "provider": "AWS",
        "access_key_id": "AKIA...",
    }


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


def test_relay_image_installs_rclone():
    src = Path("docker/relay-dev/Dockerfile").read_text(encoding="utf-8")

    assert " rclone " in src
