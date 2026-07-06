"""VoxCPM TTS service tests."""

import json

import pytest

from core import ServiceError
from services import http_listener_service as _hl_mod
from services.voxcpm_tts_service import VoxCPMTTSService


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


def test_voxcpm_speak_posts_to_openai_relay_url(monkeypatch):
    captured = {}
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id, conv_id="": "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")
    monkeypatch.setattr("core.relay_bindings.get_default", lambda cid, agent="": "relay1")

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(b"WAV", "audio/wav")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoxCPMTTSService({
        "base_url": "http://${conv.relay}/localhost:8000",
        "voice": "default",
        "model": "openbmb/VoxCPM2",
        "response_format": "wav",
    })
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")

    out = svc.speak("Bonjour", speed=1.1)

    assert out == {"audio_bytes": b"WAV", "content_type": "audio/wav", "source_url": ""}
    assert captured["url"] == "http://10.0.0.2:9090/relay-proxy/relay1/tok/localhost:8000/v1/audio/speech"
    assert captured["body"] == {
        "model": "openbmb/VoxCPM2",
        "input": "Bonjour",
        "voice": "default",
        "response_format": "wav",
        "speed": 1.1,
    }


def test_voxcpm_schema_defaults_to_vllm_openai_mode():
    svc = VoxCPMTTSService({})
    schema = svc.get_parameter_schema()

    assert schema["api_mode"]["default"] == "openai"
    assert schema["base_url"]["default"] == "relay://$" "{conv.relay}/localhost:8000"
    assert svc.SUPPORTS_NATIVE_TTS_VOICES is True


def test_voxcpm_cli_clone_speak_uses_official_clone_command(monkeypatch):
    captured = {}

    class _Proc:
        returncode = 0
        stderr = b""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        output_path = argv[argv.index("--output") + 1]
        with open(output_path, "wb") as fh:
            fh.write(b"WAV")
        return _Proc()

    monkeypatch.setattr("services.voxcpm_tts_service.subprocess.run", fake_run)
    svc = VoxCPMTTSService({
        "api_mode": "cli",
        "cli_command": "voxcpm",
        "response_format": "wav",
    })

    out = svc.clone_speak(
        text="Bonjour",
        reference_audio_bytes=b"REF",
        reference_audio_content_type="audio/wav",
        reference_text="Salut",
        ultimate_clone=True,
    )

    argv = captured["argv"]
    assert out == {"audio_bytes": b"WAV", "content_type": "audio/wav", "source_url": ""}
    assert argv[:3] == ["voxcpm", "clone", "--text"]
    assert "Bonjour" in argv
    assert "--reference-audio" in argv
    assert "--prompt-audio" in argv
    assert argv[argv.index("--prompt-text") + 1] == "Salut"
    assert captured["kwargs"]["check"] is False


def test_voxcpm_openai_mode_rejects_clone_speak(monkeypatch):
    svc = VoxCPMTTSService({
        "base_url": "http://127.0.0.1:8000",
        "allow_private_base_url": True,
    })

    with pytest.raises(ServiceError, match="requires api_mode=cli"):
        svc.clone_speak(text="Bonjour", reference_audio_bytes=b"REF")


def test_voxcpm_direct_private_url_requires_opt_in():
    svc = VoxCPMTTSService({"base_url": "http://127.0.0.1:8000"})

    with pytest.raises(ServiceError, match="private/local network"):
        svc.connect()


def test_voxcpm_rejects_unknown_api_mode():
    with pytest.raises(ServiceError, match="unsupported VoxCPM api_mode"):
        VoxCPMTTSService({"api_mode": "http"})
