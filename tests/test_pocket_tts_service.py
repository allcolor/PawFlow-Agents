"""Pocket TTS local service tests."""

import subprocess
import urllib.error
import urllib.parse

import pytest

from core import ServiceError
from services.pocket_tts_service import PocketTTSService


class _Resp:
    def __init__(self, body, content_type="audio/wav", status=200):
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def test_pocket_tts_generate_posts_builtin_voice(monkeypatch):
    svc = PocketTTSService({"voice": "estelle"})
    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data
        return _Resp(b"WAV")

    monkeypatch.setattr(svc, "ensure_connected", lambda: None)
    monkeypatch.setattr("services.pocket_tts_service.urllib.request.urlopen", fake_urlopen)

    out = svc.generate(prompt="Bonjour")

    assert out == {"audio_bytes": b"WAV", "content_type": "audio/wav", "source_url": ""}
    assert captured["url"] == "http://127.0.0.1:8000/tts"
    assert captured["headers"]["Content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="text"' in captured["body"]
    assert b"Bonjour" in captured["body"]
    assert b'name="voice_url"' in captured["body"]
    assert b"estelle" in captured["body"]


def test_pocket_tts_speak_uploads_reference_audio_bytes(monkeypatch):
    svc = PocketTTSService({})
    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["body"] = req.data
        return _Resp(b"WAV")

    monkeypatch.setattr(svc, "ensure_connected", lambda: None)
    monkeypatch.setattr("services.pocket_tts_service.urllib.request.urlopen", fake_urlopen)

    svc.speak(text="Hello", reference_audio_bytes=b"RIFFDATA")

    assert b'name="voice_wav"; filename="reference.wav"' in captured["body"]
    assert b"RIFFDATA" in captured["body"]
    assert b'name="voice_url"' not in captured["body"]


def test_pocket_tts_rejects_remote_voice_url_by_default(monkeypatch):
    svc = PocketTTSService({})
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)

    with pytest.raises(ServiceError, match="allow_remote_voice_urls=true"):
        svc.speak(text="Hello", voice="https://example.test/voice.wav")


def test_pocket_tts_allows_remote_voice_url_when_configured(monkeypatch):
    svc = PocketTTSService({"allow_remote_voice_urls": True})
    captured = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["body"] = req.data
        return _Resp(b"WAV")

    monkeypatch.setattr(svc, "ensure_connected", lambda: None)
    monkeypatch.setattr("services.pocket_tts_service.urllib.request.urlopen", fake_urlopen)

    svc.speak(text="Hello", voice="https://example.test/voice.wav")

    assert b"https://example.test/voice.wav" in captured["body"]


def test_pocket_tts_rejects_empty_text(monkeypatch):
    svc = PocketTTSService({})
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)

    with pytest.raises(ServiceError, match="No text provided"):
        svc.generate(prompt="")


def test_pocket_tts_http_error_includes_preview(monkeypatch):
    svc = PocketTTSService({})
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)

    class _HTTPError(urllib.error.HTTPError):
        def read(self):
            return b'{"detail":"bad voice"}'

    def raise_http_error(_req, timeout=None, context=None):
        raise _HTTPError("http://127.0.0.1:8000/tts", 400, "Bad Request", {}, None)

    monkeypatch.setattr("services.pocket_tts_service.urllib.request.urlopen", raise_http_error)

    with pytest.raises(ServiceError, match="bad voice"):
        svc.generate(prompt="Hello")


def test_pocket_tts_connection_starts_managed_daemon(monkeypatch, tmp_path):
    svc = PocketTTSService({
        "install_dir": str(tmp_path / "pocket-tts"),
        "language": "french_24l",
        "quantize": True,
        "startup_timeout": 1,
    })
    ready = {"n": 0}

    class _Proc:
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
    monkeypatch.setattr("services.pocket_tts_service.subprocess.Popen", _Proc)
    monkeypatch.setattr("services.pocket_tts_service.time.sleep", lambda _s: None)

    conn = svc._create_connection()

    assert conn["managed"] is True
    assert conn["process"].args[:3] == [str(svc._venv_python()), "-c", "from pocket_tts.main import cli_app; cli_app()"]
    assert conn["process"].args[3:] == ["serve", "--host", "127.0.0.1", "--port", "8000", "--language", "french_24l", "--quantize"]


def test_pocket_tts_prepare_install_creates_managed_runtime(monkeypatch, tmp_path):
    svc = PocketTTSService({
        "install_dir": str(tmp_path / "pocket-tts"),
        "package_spec": "pocket-tts-test",
    })
    commands = []

    monkeypatch.setattr(
        "services.pocket_tts_service.python_venv_requirement",
        lambda: {"name": "python3-venv", "required": True, "ok": True},
    )

    def fake_run_checked(cmd, **_kwargs):
        commands.append(list(cmd))
        if cmd[1:3] == ["-m", "venv"]:
            svc._venv_python().parent.mkdir(parents=True, exist_ok=True)
            svc._venv_python().write_text("", encoding="utf-8")

    monkeypatch.setattr("services.pocket_tts_service.run_checked", fake_run_checked)
    monkeypatch.setattr("services.pocket_tts_service.subprocess.run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0))

    result = svc.prepare_install()

    assert result["prepared"] is True
    assert commands[0][1:3] == ["-m", "venv"]
    assert commands[-1][-1] == "pocket-tts-test"
    assert (tmp_path / "pocket-tts" / ".pawflow_install.json").is_file()


def test_pocket_tts_existing_runtime_repairs_missing_server(monkeypatch, tmp_path):
    svc = PocketTTSService({
        "install_dir": str(tmp_path / "pocket-tts"),
        "package_spec": "pocket-tts-test",
    })
    python = svc._venv_python()
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1)

    def fake_run_checked(cmd, **_kwargs):
        calls.append(cmd)

    monkeypatch.setattr("services.pocket_tts_service.subprocess.run", fake_run)
    monkeypatch.setattr("services.pocket_tts_service.run_checked", fake_run_checked)

    svc._ensure_runtime()

    assert [str(python), "-m", "pip", "install", "pocket-tts-test"] in calls


def test_pocket_tts_runtime_package_default_matches_schema():
    svc = PocketTTSService({})

    assert svc.package_spec == "pocket-tts[audio]>=2.1.0"
    assert svc.get_parameter_schema()["package_spec"]["default"] == svc.package_spec


def test_pocket_tts_startup_error_reads_managed_log(tmp_path):
    svc = PocketTTSService({"install_dir": str(tmp_path / "pocket-tts"), "startup_timeout": 1})
    svc._managed_log_path.parent.mkdir(parents=True)
    svc._managed_log_path.write_text("missing torch wheel\n", encoding="utf-8")

    class DeadProc:
        def poll(self):
            return 1

    with pytest.raises(ServiceError, match="missing torch wheel"):
        svc._wait_ready(DeadProc())


def test_pocket_tts_service_registers_with_tasks_import():
    import tasks
    from core import ServiceFactory

    tasks._register_all_services()

    assert ServiceFactory.get("pocketTTS") is PocketTTSService

