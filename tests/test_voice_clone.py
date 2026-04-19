"""Tests for the voice-clone capability.

Covers:
  - BaseVoiceCloneService contract
  - FishAudioVoiceCloneService.clone_speak (mocked HTTPS)
  - voice_clone_cache (hashing, repo save/lookup, tts cache)
  - CloneVoiceHandler + SpeakHandler (end-to-end, cache hit/miss)
"""

import io
import json
from types import SimpleNamespace

import pytest

from core import voice_clone_cache as _cache
from core.file_store import FileStore
from services.base_voice_clone import BaseVoiceCloneService
from services.fish_audio_voice_clone_service import FishAudioVoiceCloneService


# ── Fish Audio service ───────────────────────────────────────────────


def _fish(api_key: str = "k", **kwargs) -> FishAudioVoiceCloneService:
    cfg = {"api_key": api_key}
    cfg.update(kwargs)
    return FishAudioVoiceCloneService(cfg)


def test_base_voice_clone_is_abstract():
    # Cannot instantiate BaseVoiceCloneService directly — abstract method.
    with pytest.raises(TypeError):
        BaseVoiceCloneService({})


def test_fish_schema_contains_sensitive_api_key():
    schema = _fish().get_parameter_schema()
    assert schema["api_key"]["required"] is True
    assert schema["api_key"]["sensitive"] is True
    assert schema["format"]["default"] == "mp3"
    assert "mp3" in schema["format"]["options"]


def test_fish_requires_api_key_on_connect():
    from core import ServiceError
    s = _fish("")
    with pytest.raises(ServiceError, match="api_key"):
        s._create_connection()


def test_fish_clone_speak_requires_text():
    from core import ServiceError
    s = _fish()
    with pytest.raises(ServiceError, match="text"):
        s.clone_speak(text="", reference_audio_bytes=b"x")


def test_fish_clone_speak_requires_reference():
    from core import ServiceError
    s = _fish()
    with pytest.raises(ServiceError, match="reference"):
        s.clone_speak(text="hello")


def test_fish_clone_speak_happy_path(monkeypatch):
    """clone_speak buffers the HTTPS response body and returns bytes + CT."""
    captured = {}

    class _FakeResp:
        status = 200
        headers = {"Content-Type": "audio/mpeg"}

        def read(self):
            return b"MP3BYTES"

    class _FakeConn:
        def __init__(self, host, timeout=None, context=None):
            captured["host"] = host

        def request(self, method, path, body=None, headers=None):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = body
            captured["headers"] = headers

        def getresponse(self):
            return _FakeResp()

        def close(self):
            captured["closed"] = True

    import http.client as _hc
    monkeypatch.setattr(_hc, "HTTPSConnection", _FakeConn)

    s = _fish()
    out = s.clone_speak(
        text="Hello world",
        reference_audio_bytes=b"WAV-BYTES",
        reference_text="sample transcription",
        language="en",
    )

    assert out["audio_bytes"] == b"MP3BYTES"
    assert out["content_type"].startswith("audio/")
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/tts"
    assert captured["headers"]["Authorization"] == "Bearer k"
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["text"] == "Hello world"
    assert body["references"][0]["text"] == "sample transcription"
    assert body["language"] == "en"
    # reference audio must be base64-encoded
    import base64
    assert base64.b64decode(body["references"][0]["audio"]) == b"WAV-BYTES"


def test_fish_clone_speak_api_error(monkeypatch):
    from core import ServiceError

    class _FakeResp:
        status = 401
        headers = {"Content-Type": "application/json"}

        def read(self):
            return b'{"error": "bad key"}'

    class _FakeConn:
        def __init__(self, host, timeout=None, context=None):
            pass

        def request(self, method, path, body=None, headers=None):
            pass

        def getresponse(self):
            return _FakeResp()

        def close(self):
            pass

    import http.client as _hc
    monkeypatch.setattr(_hc, "HTTPSConnection", _FakeConn)

    with pytest.raises(ServiceError, match="401"):
        _fish().clone_speak(text="hi", reference_audio_bytes=b"x")


# ── voice_clone_cache ───────────────────────────────────────────────


def test_cache_hash_audio_is_deterministic():
    assert _cache.hash_audio(b"abc") == _cache.hash_audio(b"abc")
    assert _cache.hash_audio(b"abc") != _cache.hash_audio(b"abcd")


def test_cache_safe_name_normalises():
    assert _cache.safe_name("Héllo World!") == "H_llo_World"
    assert _cache.safe_name("  a b c  ") == "a_b_c"
    assert _cache.safe_name("") == "voice"


def test_cache_save_and_find_by_hash():
    uid = "u_cache1"
    entry = {
        "name": "testvoice",
        "provider": "fishAudioVoiceClone",
        "provider_version": "1",
        "voice_id": "",
        "ref_audio_hash": "h123",
        "ref_audio_fid": "fid_x",
    }
    saved = _cache.save(uid, entry)
    assert saved["name"] == "testvoice"
    assert saved["created_at"] > 0

    # find_by_hash returns the saved entry
    found = _cache.find_by_hash(uid, "fishAudioVoiceClone", "h123")
    assert found is not None
    assert found["name"] == "testvoice"

    # Scoped by provider
    assert _cache.find_by_hash(uid, "otherProvider", "h123") is None
    # Scoped by user
    assert _cache.find_by_hash("u_other", "fishAudioVoiceClone", "h123") is None


def test_cache_delete_round_trip():
    uid = "u_cache2"
    _cache.save(uid, {"name": "v1", "provider": "fishAudioVoiceClone",
                      "ref_audio_hash": "h_del"})
    assert _cache.get_by_name(uid, "v1") is not None
    assert _cache.delete(uid, "v1") is True
    assert _cache.get_by_name(uid, "v1") is None
    # idempotent
    assert _cache.delete(uid, "v1") is False


def test_tts_cache_key_is_stable():
    k1 = _cache.tts_cache_key("hh", "Hello", language="en", provider="p")
    k2 = _cache.tts_cache_key("hh", "Hello", language="en", provider="p")
    k3 = _cache.tts_cache_key("hh", "Hello", language="fr", provider="p")
    assert k1 == k2
    assert k1 != k3


def test_tts_store_and_find_round_trip():
    uid = "u_tts_rt"
    conv = "c_tts_rt"
    key = _cache.tts_cache_key("hh", "Hello", provider="fishAudioVoiceClone")

    fid = _cache.tts_store(
        user_id=uid, conversation_id=conv,
        cache_key=key,
        filename="hello.mp3", audio_bytes=b"RENDERED",
        content_type="audio/mpeg",
    )
    assert fid

    # Same key, same user → cache hit returns our fid.
    got = _cache.tts_find(uid, conv, key)
    assert got == fid

    # Different user → miss.
    assert _cache.tts_find("u_other", conv, key) is None

    # Different cache key → miss.
    other_key = _cache.tts_cache_key("hh", "Different",
                                     provider="fishAudioVoiceClone")
    assert _cache.tts_find(uid, conv, other_key) is None


# ── Handlers ─────────────────────────────────────────────────────────────


class _FakeUrlOpen:
    """Context manager that fakes urllib.request.urlopen for the handler.

    The handler downloads the reference audio via urllib to hash it.
    We stub the call to return a fixed bytes payload.
    """

    def __init__(self, payload: bytes = b"REFAUDIO",
                 content_type: str = "audio/mpeg"):
        self.payload = payload
        self.content_type = content_type

    def __call__(self, req, timeout=None):
        return self  # act as context manager

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.payload

    @property
    def headers(self):
        return {"Content-Type": self.content_type}


def _wire_handler(handler, svc, user_id="u_h", conv="c_h"):
    handler.set_base_url("http://localhost:9090")
    handler.set_user_id(user_id)
    handler.set_conversation_id(conv)
    handler.set_service_resolver(lambda: (svc, None))


def test_clone_voice_handler_registers_new_voice(monkeypatch):
    from core.handlers.capabilities import CloneVoiceHandler

    svc = _fish()
    # Handler will NOT call the service for registration — only downloads
    # and hashes the ref audio. clone_speak is called by SpeakHandler.

    h = CloneVoiceHandler()
    _wire_handler(h, svc, user_id="u_reg", conv="c_reg")

    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen",
                         _FakeUrlOpen(b"SAMPLE", "audio/mpeg"))

    out = h.execute({
        "name": "mavoix",
        "reference_audio_url": "http://example.com/sample.mp3",
        "reference_text": "hello",
        "language": "fr",
    })
    assert "Voice clone registered" in out
    assert "mavoix" in out

    # Second call with SAME audio → cached message (no duplicate resource).
    out2 = h.execute({
        "name": "autrenom",   # different name
        "reference_audio_url": "http://example.com/sample.mp3",
    })
    assert "already exists" in out2
    # The original name comes back, not the new one.
    assert "mavoix" in out2


def test_clone_voice_missing_params():
    from core.handlers.capabilities import CloneVoiceHandler
    h = CloneVoiceHandler()
    _wire_handler(h, _fish())
    out = h.execute({"name": ""})
    assert "Error" in out


def test_speak_handler_unknown_voice():
    from core.handlers.capabilities import SpeakHandler
    h = SpeakHandler()
    _wire_handler(h, _fish(), user_id="u_s_unknown", conv="c_s")
    out = h.execute({"voice": "doesnotexist", "text": "hi"})
    assert "unknown voice clone" in out.lower()


def test_speak_handler_synth_and_cache_hit(monkeypatch):
    from core.handlers.capabilities import CloneVoiceHandler, SpeakHandler

    uid, conv = "u_speak", "c_speak"
    svc = _fish()

    # Register a voice first.
    ch = CloneVoiceHandler()
    _wire_handler(ch, svc, user_id=uid, conv=conv)
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen",
                         _FakeUrlOpen(b"REF-BYTES", "audio/mpeg"))
    ch.execute({"name": "bob",
                 "reference_audio_url": "http://example.com/bob.mp3"})

    # Stub clone_speak on the service to count calls.
    calls = {"n": 0}

    def _fake_clone_speak(**kwargs):
        calls["n"] += 1
        return {"audio_bytes": b"SYNTHMP3",
                "content_type": "audio/mpeg",
                "source_url": ""}

    svc.clone_speak = _fake_clone_speak

    sh = SpeakHandler()
    _wire_handler(sh, svc, user_id=uid, conv=conv)

    out1 = sh.execute({"voice": "bob", "text": "Hello!"})
    assert "Speech synthesized" in out1
    assert "cached" not in out1
    assert calls["n"] == 1

    # Same (voice, text) → cache hit, provider NOT called again.
    out2 = sh.execute({"voice": "bob", "text": "Hello!"})
    assert "cached" in out2
    assert calls["n"] == 1

    # Different text → new provider call.
    out3 = sh.execute({"voice": "bob", "text": "Bonjour!"})
    assert "cached" not in out3
    assert calls["n"] == 2


def test_speak_handler_provider_mismatch(monkeypatch):
    """A voice registered with provider A cannot be used with provider B."""
    from core.handlers.capabilities import SpeakHandler

    uid, conv = "u_mismatch", "c_m"
    # Save entry directly with a different provider.
    _cache.save(uid, {
        "name": "alien",
        "provider": "someOtherProvider",
        "ref_audio_hash": "hh",
        "ref_audio_fid": "",
    })

    sh = SpeakHandler()
    _wire_handler(sh, _fish(), user_id=uid, conv=conv)
    out = sh.execute({"voice": "alien", "text": "Hi"})
    assert "was created with provider" in out


# ── hash_audio normalization ───────────────────────────────────────────


def test_hash_audio_falls_back_to_raw_without_ffmpeg(monkeypatch):
    """When ffmpeg is absent, hash_audio must hash raw bytes deterministically."""
    monkeypatch.setattr(_cache, "_FFMPEG", None)
    import hashlib
    payload = b"raw-audio-bytes"
    assert _cache.hash_audio(payload) == hashlib.sha256(payload).hexdigest()


def test_hash_audio_uses_pcm_when_ffmpeg_decodes(monkeypatch):
    """When ffmpeg can decode, the hash is taken over normalized PCM.

    We stub `_normalize_pcm` to return deterministic "PCM" bytes so we can
    assert hash_audio matched the PCM path (different from raw hash).
    """
    import hashlib
    monkeypatch.setattr(_cache, "_FFMPEG", "/fake/ffmpeg")
    monkeypatch.setattr(_cache, "_normalize_pcm", lambda b: b"PCM-" + b)
    payload = b"encoded-mp3"
    expected = hashlib.sha256(b"PCM-encoded-mp3").hexdigest()
    assert _cache.hash_audio(payload) == expected


def test_hash_audio_equivalent_encodings_hash_the_same(monkeypatch):
    """Two distinct byte payloads that decode to the same PCM get one hash."""
    monkeypatch.setattr(_cache, "_FFMPEG", "/fake/ffmpeg")
    # Any two inputs → same PCM. If hash_audio uses normalization, they must
    # hash identically.
    monkeypatch.setattr(_cache, "_normalize_pcm",
                          lambda b: b"PCM-DETERMINISTIC")
    assert _cache.hash_audio(b"mp3-variant-1") == _cache.hash_audio(b"mp3-variant-2")


# ── cascade_delete + DeleteVoiceHandler ─────────────────────────


def test_cascade_delete_removes_entry_and_tts_cache(monkeypatch):
    """cascade_delete must drop the entry, ref audio and rendered TTS files."""
    uid, conv = "u_cascade", "c_cascade"
    # Register a voice.
    _cache.save(uid, {
        "name": "tbd",
        "provider": "fishAudioVoiceClone",
        "ref_audio_hash": "hcasc",
        "ref_audio_fid": "",
    })
    # Cache one rendered TTS output keyed on the same ref hash.
    key = _cache.tts_cache_key("hcasc", "bonjour",
                                provider="fishAudioVoiceClone")
    fid = _cache.tts_store(
        user_id=uid, conversation_id=conv,
        cache_key=key,
        filename="out.mp3", audio_bytes=b"RENDERED",
        ref_audio_hash="hcasc",
    )
    assert _cache.tts_find(uid, conv, key) == fid

    out = _cache.cascade_delete(uid, "tbd", service=None)
    assert out["entry"] is True
    assert out["tts_cached"] == 1
    # Entry gone.
    assert _cache.get_by_name(uid, "tbd") is None
    # TTS cache gone.
    assert _cache.tts_find(uid, conv, key) is None


def test_cascade_delete_calls_provider_for_voice_id():
    """Paradigm-B voices (voice_id set) must trigger service.delete_voice_id."""
    uid = "u_cascade_vid"
    _cache.save(uid, {
        "name": "el",
        "provider": "elevenLabsVoiceClone",
        "voice_id": "EL_VID_42",
        "ref_audio_hash": "hvid",
    })

    calls = []

    class _FakeSvc:
        TYPE = "elevenLabsVoiceClone"

        def delete_voice_id(self, vid):
            calls.append(vid)
            return True

    out = _cache.cascade_delete(uid, "el", service=_FakeSvc())
    assert calls == ["EL_VID_42"]
    assert out["voice_id"] is True
    assert out["entry"] is True


def test_delete_voice_handler_happy_path():
    from core.handlers.capabilities import DeleteVoiceHandler
    uid, conv = "u_del_h", "c_del_h"
    _cache.save(uid, {
        "name": "gone",
        "provider": "fishAudioVoiceClone",
        "ref_audio_hash": "hgone",
    })

    h = DeleteVoiceHandler()
    _wire_handler(h, _fish(), user_id=uid, conv=conv)
    out = h.execute({"voice": "gone"})
    assert "deleted" in out.lower()
    assert _cache.get_by_name(uid, "gone") is None


def test_delete_voice_handler_unknown_voice():
    from core.handlers.capabilities import DeleteVoiceHandler
    h = DeleteVoiceHandler()
    _wire_handler(h, _fish(), user_id="u_dhm", conv="c_dhm")
    out = h.execute({"voice": "ghost"})
    assert "unknown voice clone" in out.lower()
