import json

import core.paths as paths
from core import FlowFile
from core.appearance_store import (
    clear_conversation_preferences,
    normalize_preferences,
    resolve_preferences,
    save_preferences,
)
from core.file_store import FileStore
from tasks.ai.actions.appearance import _handle_appearance


def test_preferences_are_clamped_and_persisted_per_user(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp_path / "users")

    prefs = normalize_preferences({
        "scale": 999, "dim": -5, "blur": 90, "saturation": 3,
        "panel": 20, "source": "none",
    })
    assert prefs["scale"] == 150
    assert prefs["dim"] == 0
    assert prefs["blur"] == 24
    assert prefs["saturation"] == 50
    assert prefs["panel"] == 55

    save_preferences("alice", "global", prefs)
    assert resolve_preferences("alice")["resolved"]["scale"] == 150
    assert resolve_preferences("bob")["exists"] is False


def test_conversation_override_inherits_then_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp_path / "users")
    save_preferences("alice", "global", {"source": "none", "scale": 110})
    save_preferences(
        "alice", "conversation", {"source": "none", "scale": 125},
        conversation_id="conv-7",
    )

    resolved = resolve_preferences("alice", "conv-7")
    assert resolved["scope"] == "conversation"
    assert resolved["resolved"]["scale"] == 125

    cleared = clear_conversation_preferences("alice", "conv-7")
    assert cleared["scope"] == "global"
    assert cleared["resolved"]["scale"] == 110


def test_appearance_action_validates_private_upload_and_removes_replaced_file(
        tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp_path / "users")
    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(
        FileStore, "instance", classmethod(lambda _cls: file_store))
    file_id = file_store.store(
        "wall paper.jpg", b"image", content_type="image/jpeg",
        conversation_id="_appearance", user_id="alice",
        ttl=0, category="appearance",
    )

    flowfile = FlowFile(content=b"{}")
    result = _handle_appearance(
        None, "appearance_save",
        {
            "scope": "global",
            "prefs": {"source": "upload", "file_id": file_id},
        },
        None, "alice", flowfile,
    )
    assert result == [flowfile]
    payload = json.loads(flowfile.get_content())
    assert payload["resolved"]["file_id"] == file_id
    assert payload["resolved"]["url"].endswith("/wall%20paper.jpg")
    assert file_store.exists(file_id)

    _handle_appearance(
        None, "appearance_save",
        {"scope": "global", "prefs": {"source": "none"}},
        None, "alice", flowfile,
    )
    assert not file_store.exists(file_id)


def test_appearance_action_rejects_another_users_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp_path / "users")
    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(
        FileStore, "instance", classmethod(lambda _cls: file_store))
    file_id = file_store.store(
        "private.png", b"image", content_type="image/png",
        conversation_id="_appearance", user_id="bob",
        ttl=0, category="appearance",
    )
    flowfile = FlowFile(content=b"{}")

    _handle_appearance(
        None, "appearance_save",
        {
            "scope": "global",
            "prefs": {"source": "upload", "file_id": file_id},
        },
        None, "alice", flowfile,
    )

    assert "another user" in json.loads(flowfile.get_content())["error"]
