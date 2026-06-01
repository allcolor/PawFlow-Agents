"""Supertonic local TTS service tests."""

import urllib.error
import urllib.parse

import pytest

from core import ServiceError
from core import voice_clone_cache as _cache
from core.handlers.capabilities import SpeakHandler
from core.handlers.media import AudioGenerationHandler
from services import http_listener_service as _hl_mod
from services.supertonic_tts_service import SupertonicTTSService


class _Resp:
    def __init__(self, body, content_type="audio/wav"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


class _Listener:
    is_ssl = False
    public_hostname = ""


def test_supertonic_generate_posts_native_tts_payload(monkeypatch):
    svc = SupertonicTTSService({
        "base_url": "http://127.0.0.1:7788",
        "voice": "F1",
        "lang": "fr",
        "steps": 9,
        "speed": 1.1,
    })
    captured = {}

    def fake_post(body):
        captured.update(body)
        return b"WAV", "audio/wav"

    monkeypatch.setattr(svc, "_post_tts", fake_post)
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)

    out = svc.generate(prompt="Bonjour tout le monde")

    assert out == {"audio_bytes": b"WAV", "content_type": "audio/wav", "source_url": ""}
    assert captured == {
        "text": "Bonjour tout le monde",
        "voice": "F1",
        "lang": "fr",
        "steps": 9,
        "speed": 1.1,
        "response_format": "wav",
    }


def test_supertonic_generate_allows_per_call_tts_overrides(monkeypatch):
    svc = SupertonicTTSService({})
    captured = {}
    monkeypatch.setattr(svc, "_post_tts", lambda body: captured.update(body) or (b"OGG", "audio/ogg"))
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)

    out = svc.generate(
        prompt="Hello",
        voice="M4",
        lang="en",
        steps=5,
        speed=1.25,
        response_format="ogg",
        max_chunk_length=120,
    )

    assert out["audio_bytes"] == b"OGG"
    assert captured["voice"] == "M4"
    assert captured["lang"] == "en"
    assert captured["steps"] == 5
    assert captured["speed"] == 1.25
    assert captured["response_format"] == "ogg"
    assert captured["max_chunk_length"] == 120


def test_supertonic_external_relay_url_uses_proxy_route(monkeypatch):
    captured = {}
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id: "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")
    monkeypatch.setattr("core.relay_bindings.get_default", lambda cid, agent="": "relay1")

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        return _Resp(b"WAV", "audio/wav")

    monkeypatch.setattr("services.supertonic_tts_service.urllib.request.urlopen", fake_urlopen)
    svc = SupertonicTTSService({
        "base_url": "http://${conv.relay}/localhost:7788",
        "auto_start": False,
    })
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)

    out = svc.generate(prompt="Hello")

    assert out["audio_bytes"] == b"WAV"
    assert captured["url"] == "http://10.0.0.2:9090/relay-proxy/relay1/tok/localhost:7788/v1/tts"
    assert b"Hello" in captured["body"]


def test_supertonic_rejects_unsupported_format():
    svc = SupertonicTTSService({})
    svc.ensure_connected = lambda: None  # type: ignore[method-assign]

    with pytest.raises(ServiceError, match="unsupported Supertonic response_format"):
        svc.generate(prompt="Hello", response_format="mp3")


def test_supertonic_http_error_includes_preview(monkeypatch):
    svc = SupertonicTTSService({})

    class _HTTPError(urllib.error.HTTPError):
        def read(self):
            return b'{"detail":"bad voice"}'

    def raise_http_error(_req, timeout=None):
        raise _HTTPError("http://127.0.0.1:7788/v1/tts", 400, "Bad Request", {}, None)

    monkeypatch.setattr("services.supertonic_tts_service.urllib.request.urlopen", raise_http_error)

    with pytest.raises(ServiceError, match="bad voice"):
        svc.generate(prompt="Hello")


def test_supertonic_connection_starts_managed_daemon(monkeypatch):
    svc = SupertonicTTSService({"startup_timeout": 1})
    ready = {"n": 0}

    class _Proc:
        stderr = None

        def __init__(self, *args, **kwargs):
            self.args = args[0]
            self.kwargs = kwargs

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_ready():
        ready["n"] += 1
        return ready["n"] >= 2

    def fake_ensure_runtime():
        svc._venv_python().parent.mkdir(parents=True, exist_ok=True)
        svc._venv_python().write_text("", encoding="utf-8")

    monkeypatch.setattr(svc, "_server_ready", fake_ready)
    monkeypatch.setattr(svc, "_ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr("services.supertonic_tts_service.subprocess.Popen", _Proc)
    monkeypatch.setattr("services.supertonic_tts_service.time.sleep", lambda _s: None)

    conn = svc._create_connection()

    assert conn["managed"] is True
    assert conn["process"].args[:3] == [str(svc._venv_python()), "-c", "from supertonic.cli import main; raise SystemExit(main())"]
    assert conn["process"].args[3:6] == ["serve", "--host", "127.0.0.1"]


def test_supertonic_autostart_prepares_managed_runtime(monkeypatch, tmp_path):
    svc = SupertonicTTSService({"install_dir": str(tmp_path / "supertonic3")})
    calls = []

    def fake_ensure_runtime():
        calls.append("ensure_runtime")
        svc._venv_python().parent.mkdir(parents=True, exist_ok=True)
        svc._venv_python().write_text("", encoding="utf-8")

    monkeypatch.setattr(svc, "_ensure_runtime", fake_ensure_runtime)
    monkeypatch.setattr("services.supertonic_tts_service.subprocess.Popen", lambda *a, **k: a[0])

    cmd = svc._start_server(urllib.parse.urlparse("http://127.0.0.1:7788"))

    assert calls == ["ensure_runtime"]
    assert cmd[0] == str(svc._venv_python())


def test_supertonic_runtime_package_default_matches_schema():
    svc = SupertonicTTSService({})

    assert svc.package_spec == "supertonic[serve]>=0.1.0"
    assert svc.get_parameter_schema()["package_spec"]["default"] == svc.package_spec


def test_supertonic_prepare_install_creates_managed_runtime(monkeypatch, tmp_path):
    svc = SupertonicTTSService({
        "install_dir": str(tmp_path / "supertonic3"),
        "package_spec": "supertonic-test",
    })
    commands = []

    monkeypatch.setattr(
        "services.supertonic_tts_service.python_venv_requirement",
        lambda: {"name": "python3-venv", "required": True, "ok": True},
    )

    def fake_run_checked(cmd, **_kwargs):
        commands.append(list(cmd))
        if cmd[1:3] == ["-m", "venv"]:
            svc._venv_python().parent.mkdir(parents=True, exist_ok=True)
            svc._venv_python().write_text("", encoding="utf-8")

    monkeypatch.setattr("services.supertonic_tts_service.run_checked", fake_run_checked)

    result = svc.prepare_install()

    assert result["prepared"] is True
    assert commands[0][1:3] == ["-m", "venv"]
    assert commands[-1][-1] == "supertonic-test"
    assert (tmp_path / "supertonic3" / ".pawflow_install.json").is_file()


def test_supertonic_schema_does_not_expose_executable_override():
    schema = SupertonicTTSService({}).get_parameter_schema()
    assert "python_executable" not in schema
    assert "executable" not in "\n".join(schema)


def test_audio_handler_passes_tts_arguments_to_supertonic_service():
    handler = AudioGenerationHandler()
    svc = SupertonicTTSService({})
    calls = []
    svc.generate = lambda **kwargs: calls.append(kwargs) or {  # type: ignore[method-assign]
        "audio_bytes": b"WAV", "content_type": "audio/wav"}
    handler.set_service_resolver(lambda: (svc, None))

    from unittest.mock import patch
    with patch("core.storage_resolver.StorageResolver") as storage:
        storage.return_value.write.return_value = {"file_id": "file123"}
        result = handler.execute({
            "prompt": "Bonjour",
            "voice": "F2",
            "lang": "fr",
            "steps": 8,
            "speed": 1.05,
            "response_format": "wav",
        })

    assert calls == [{
        "prompt": "Bonjour",
        "voice": "F2",
        "lang": "fr",
        "steps": 8,
        "speed": 1.05,
        "response_format": "wav",
    }]
    assert "Audio generated" in result
    assert "file123" in result


def test_speak_handler_uses_native_tts_provider_voice(monkeypatch):
    handler = SpeakHandler()
    handler.set_user_id("u_super_speak")
    handler.set_conversation_id("c_super_speak")
    svc = SupertonicTTSService({})
    calls = []
    svc.speak = lambda **kwargs: calls.append(kwargs) or {  # type: ignore[method-assign]
        "audio_bytes": b"WAV", "content_type": "audio/wav"}
    handler.set_service_resolver(lambda: (svc, None))
    monkeypatch.setattr(_cache, "tts_find", lambda *a, **k: None)
    monkeypatch.setattr(_cache, "tts_store", lambda **kwargs: "tts123")

    result = handler.execute({
        "text": "Bonjour",
        "voice": "F2",
        "language": "fr",
        "response_format": "wav",
    })

    assert calls == [{
        "text": "Bonjour",
        "voice": "F2",
        "language": "fr",
        "response_format": "wav",
    }]
    assert "Speech synthesized" in result
    assert "tts123" in result


def test_speak_handler_keeps_transient_ttl_out_of_provider_payload(monkeypatch):
    handler = SpeakHandler()
    handler.set_user_id("u_super_transient")
    handler.set_conversation_id("c_super_transient")
    svc = SupertonicTTSService({})
    calls = []
    stored = {}
    svc.speak = lambda **kwargs: calls.append(kwargs) or {  # type: ignore[method-assign]
        "audio_bytes": b"WAV", "content_type": "audio/wav"}
    handler.set_service_resolver(lambda: (svc, None))
    monkeypatch.setattr(_cache, "tts_find", lambda *a, **k: None)
    monkeypatch.setattr(_cache, "tts_store", lambda **kwargs: stored.update(kwargs) or "tts123")

    result = handler.execute({
        "text": "Bonjour",
        "voice": "F2",
        "language": "fr",
        "response_format": "wav",
        "transient": True,
        "transient_ttl": 3600,
        "_tts_storage_ttl": 1800,
    })

    assert calls == [{
        "text": "Bonjour",
        "voice": "F2",
        "language": "fr",
        "response_format": "wav",
    }]
    assert stored["ttl"] == 1800
    assert "Speech synthesized" in result
