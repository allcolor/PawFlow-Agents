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


def test_link_rejects_global_filesystem(monkeypatch):
    from core import remote_fs_bindings as rfb

    store = _FakeStore()
    monkeypatch.setattr("core.conversation_store.ConversationStore", _FakeConversationStore)
    _FakeConversationStore._instance = store

    monkeypatch.setattr(rfb, "_resolve_service_definition", lambda *_args, **_kwargs: SimpleNamespace(
        service_id="gdrive", service_type="googleDrive", scope="global", config={}, description=""))

    with pytest.raises(ValueError, match="Global filesystem services"):
        rfb.link_filesystem("conv1", "user1", "gdrive")


def test_link_rejects_native_api_filesystem(monkeypatch):
    from core import remote_fs_bindings as rfb

    store = _FakeStore()
    monkeypatch.setattr("core.conversation_store.ConversationStore", _FakeConversationStore)
    _FakeConversationStore._instance = store

    monkeypatch.setattr(rfb, "_resolve_service_definition", lambda *_args, **_kwargs: SimpleNamespace(
        service_id="gdrive", service_type="googleDrive", scope="user", config={}, description=""))

    with pytest.raises(ValueError, match="not rclone-mount compatible"):
        rfb.link_filesystem("conv1", "user1", "gdrive")


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
