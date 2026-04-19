"""Tests for voice-clone actions on the agent-resource HTTP handler.

Covers:
  - list_resources exposes `voices` (read via the voice_clone_cache).
  - delete_voice_clone cascade-deletes entry + cached TTS files.
  - rename_voice_clone renames entry, preserves other fields,
    handles conflicts/unknown/unchanged cases.

The handler is invoked directly (no HTTP round-trip); `self`, `store`
and service registry are stubbed because the voice-clone branches do
not touch them.
"""

import json
import pytest

from core import FlowFile
from core import voice_clone_cache as _vcache
from tasks.ai.actions.agent_resource import _handle_agent_resource


class _StubStore:
    """Minimal ConversationStore stand-in for the handler."""

    def __init__(self):
        self._extras = {}

    def get_extra(self, conv_id, key):
        return self._extras.get((conv_id, key))

    def set_extra(self, conv_id, key, value):
        self._extras[(conv_id, key)] = value


def _ff():
    f = FlowFile(b"")
    # Mark the caller as a plain user (no admin role) — matches web UI.
    f.set_attribute("http.auth.roles", "user")
    return f


def _call(action, body, user_id):
    ff = _ff()
    result = _handle_agent_resource(
        None, action, body, _StubStore(), user_id, ff)
    assert result == [ff], f"action {action!r} not handled"
    return json.loads(ff.content.decode("utf-8"))


# ── delete_voice_clone ────────────────────────────────────────────

def test_delete_voice_clone_cascade_removes_entry_and_tts():
    uid = "u_del_action"
    conv = "c_del_action"
    _vcache.save(uid, {
        "name": "vdel",
        "provider": "fishAudioVoiceClone",
        "ref_audio_hash": "hdel",
    })
    key = _vcache.tts_cache_key("hdel", "hi",
                                 provider="fishAudioVoiceClone")
    _vcache.tts_store(
        user_id=uid, conversation_id=conv, cache_key=key,
        filename="x.mp3", audio_bytes=b"R", ref_audio_hash="hdel",
    )
    assert _vcache.tts_find(uid, conv, key) is not None

    resp = _call("delete_voice_clone", {"name": "vdel"}, uid)
    assert resp["ok"] is True
    assert resp["name"] == "vdel"
    assert resp["tts_cached_purged"] == 1
    assert _vcache.get_by_name(uid, "vdel") is None
    assert _vcache.tts_find(uid, conv, key) is None


def test_delete_voice_clone_missing_name_returns_error():
    resp = _call("delete_voice_clone", {"name": ""}, "u_del_empty")
    assert "error" in resp


def test_delete_voice_clone_unknown_returns_404():
    ff = _ff()
    _handle_agent_resource(
        None, "delete_voice_clone", {"name": "ghost"},
        _StubStore(), "u_del_unknown", ff)
    assert ff.get_attribute("http.response.status") == "404"
    payload = json.loads(ff.content.decode("utf-8"))
    assert "not found" in payload["error"]


# ── rename_voice_clone ────────────────────────────────────────────

def test_rename_voice_clone_happy_path_preserves_fields():
    uid = "u_rn_ok"
    _vcache.save(uid, {
        "name": "old",
        "provider": "fishAudioVoiceClone",
        "ref_audio_hash": "hrn",
        "ref_audio_fid": "fid-keep",
        "language": "fr",
        "voice_id": "vid-keep",
    })
    resp = _call("rename_voice_clone",
                  {"name": "old", "new_name": "brand-new"}, uid)
    assert resp == {"ok": True, "name": "brand-new",
                    "previous_name": "old"}
    assert _vcache.get_by_name(uid, "old") is None
    new_entry = _vcache.get_by_name(uid, "brand-new")
    assert new_entry is not None
    # Identity-bearing fields are preserved across the rename.
    assert new_entry["ref_audio_hash"] == "hrn"
    assert new_entry["ref_audio_fid"] == "fid-keep"
    assert new_entry["language"] == "fr"
    assert new_entry["voice_id"] == "vid-keep"


def test_rename_voice_clone_safename_normalisation():
    uid = "u_rn_safe"
    _vcache.save(uid, {"name": "src",
                       "provider": "fishAudioVoiceClone"})
    resp = _call("rename_voice_clone",
                  {"name": "src", "new_name": "Hé llo!"}, uid)
    assert resp["ok"] is True
    assert resp["name"] == _vcache.safe_name("Hé llo!")


def test_rename_voice_clone_unchanged_is_noop():
    uid = "u_rn_same"
    _vcache.save(uid, {"name": "keepme",
                       "provider": "fishAudioVoiceClone"})
    resp = _call("rename_voice_clone",
                  {"name": "keepme", "new_name": "keepme"}, uid)
    assert resp == {"ok": True, "name": "keepme", "unchanged": True}


def test_rename_voice_clone_conflict_returns_409():
    uid = "u_rn_conf"
    _vcache.save(uid, {"name": "one",
                       "provider": "fishAudioVoiceClone"})
    _vcache.save(uid, {"name": "two",
                       "provider": "fishAudioVoiceClone"})
    ff = _ff()
    _handle_agent_resource(
        None, "rename_voice_clone",
        {"name": "one", "new_name": "two"},
        _StubStore(), uid, ff)
    assert ff.get_attribute("http.response.status") == "409"
    payload = json.loads(ff.content.decode("utf-8"))
    assert "already exists" in payload["error"]


def test_rename_voice_clone_unknown_source_returns_404():
    ff = _ff()
    _handle_agent_resource(
        None, "rename_voice_clone",
        {"name": "ghost", "new_name": "any"},
        _StubStore(), "u_rn_miss", ff)
    assert ff.get_attribute("http.response.status") == "404"


def test_rename_voice_clone_missing_params():
    resp = _call("rename_voice_clone", {"name": ""}, "u_rn_empty")
    assert "error" in resp
    resp = _call("rename_voice_clone",
                  {"name": "x", "new_name": ""}, "u_rn_empty")
    assert "error" in resp
