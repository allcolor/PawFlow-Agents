import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from core import ServiceError
from services.voicebox_service import VoiceboxService


class _Resp:
    def __init__(self, body, content_type="application/json"):
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body


def test_voicebox_transcribe_posts_multipart(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        if req.get_method() == "GET":
            return _Resp(b'{"ok":true}')
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _Resp(json.dumps({"text": "bonjour", "language": "fr"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoiceboxService({"base_url": "http://127.0.0.1:17493", "client_id": "test"})

    out = svc.transcribe(
        audio_bytes=b"audio", mime_type="audio/webm", language="fr",
        filename="speech.webm")

    assert out["text"] == "bonjour"
    assert captured["url"].endswith("/transcribe")
    assert captured["headers"]["X-voicebox-client-id"] == "test"
    assert b'name="file"; filename="speech.webm"' in captured["body"]
    assert b'name="language"' in captured["body"]


def test_voicebox_transcribe_normalizes_whisper_model_names(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        if req.get_method() == "GET":
            return _Resp(b'{"ok":true}')
        captured["body"] = req.data
        return _Resp(json.dumps({"text": "ok"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoiceboxService({"stt_model": "whisper-turbo"})

    svc.transcribe(audio_bytes=b"audio", mime_type="audio/webm", filename="speech.webm")

    assert b'name="model"' in captured["body"]
    assert b"\r\nturbo\r\n" in captured["body"]
    assert b"whisper-turbo" not in captured["body"]


def test_voicebox_transcribe_surfaces_model_download_status(monkeypatch):
    def fake_urlopen(req, timeout=0):
        if req.get_method() == "GET":
            if req.full_url.endswith("/tasks/active"):
                return _Resp(json.dumps({
                    "downloads": [{
                        "model_name": "whisper-turbo",
                        "status": "downloading",
                        "started_at": "2026-05-25T21:52:00Z",
                        "progress": 42.5,
                        "current": 1048576,
                        "total": 2097152,
                        "filename": "model.safetensors",
                    }],
                    "generations": [],
                }).encode())
            return _Resp(b'{"ok":true}')
        return _Resp(json.dumps({
            "detail": {
                "message": "Whisper model turbo is being downloaded. Please wait and try again.",
                "model_name": "whisper-turbo",
                "downloading": True,
            }
        }).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoiceboxService({"stt_model": "turbo"})

    with pytest.raises(ServiceError, match="42.5%"):
        svc.transcribe(audio_bytes=b"audio", mime_type="audio/wav", filename="speech.wav")


def test_voicebox_transcribe_surfaces_active_download_error(monkeypatch):
    def fake_urlopen(req, timeout=0):
        if req.get_method() == "GET":
            if req.full_url.endswith("/tasks/active"):
                return _Resp(json.dumps({
                    "downloads": [{
                        "model_name": "whisper-turbo",
                        "status": "error",
                        "started_at": "2026-05-25T21:52:00Z",
                        "error": "[Errno 32] Broken pipe",
                        "progress": 0,
                        "filename": "Connecting to HuggingFace...",
                    }],
                    "generations": [],
                }).encode())
            return _Resp(b'{"ok":true}')
        return _Resp(json.dumps({
            "detail": {
                "message": "Whisper model turbo is being downloaded. Please wait and try again.",
                "model_name": "whisper-turbo",
                "downloading": True,
            }
        }).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoiceboxService({"stt_model": "turbo"})

    with pytest.raises(ServiceError, match="Broken pipe"):
        svc.transcribe(audio_bytes=b"audio", mime_type="audio/wav", filename="speech.wav")


def test_voicebox_speak_posts_json_and_returns_audio(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        if req.get_method() == "GET":
            return _Resp(b'{"ok":true}')
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return _Resp(b"mp3", "audio/mpeg")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoiceboxService({"default_profile": "Morgan"})

    out = svc.speak(text="hello")

    assert out["audio_bytes"] == b"mp3"
    assert out["content_type"] == "audio/mpeg"
    assert captured["url"].endswith("/speak")
    assert captured["body"]["profile"] == "Morgan"


def test_luxtts_clone_speak_uses_reference_bytes(monkeypatch):
    from services.luxtts_service import LuxTTSService

    calls = {}

    class _Array:
        def squeeze(self):
            return [0.0, 0.1]

    class _Wav:
        def detach(self):
            return self
        def cpu(self):
            return self
        def numpy(self):
            return _Array()

    class _Model:
        def encode_prompt(self, path, duration=0, rms=0):
            calls["path"] = path
            calls["duration"] = duration
            calls["rms"] = rms
            return "encoded"
        def generate_speech(self, text, encoded, **kwargs):
            calls["text"] = text
            calls["encoded"] = encoded
            calls["kwargs"] = kwargs
            return _Wav()

    def fake_write(out, arr, rate, format="WAV"):
        out.write(b"RIFFaudio")

    monkeypatch.setitem(__import__("sys").modules, "soundfile", types.SimpleNamespace(write=fake_write))
    svc = LuxTTSService({"num_steps": 4})
    svc._load_model = lambda: _Model()

    out = svc.clone_speak(text="hello", reference_audio_bytes=b"wav")

    assert out["audio_bytes"] == b"RIFFaudio"
    assert out["content_type"] == "audio/wav"
    assert calls["text"] == "hello"
    assert calls["encoded"] == "encoded"
    assert calls["kwargs"]["num_steps"] == 4


def test_voice_io_services_are_registered():
    import tasks
    from core import ServiceFactory

    tasks._register_all_services()
    types = set(ServiceFactory.list_types())
    assert "voicebox" in types
    assert "openaiCompatibleSTT" in types
    assert "luxTTS" in types


def test_voicebox_auto_install_checks_out_pinned_ref(monkeypatch, tmp_path):
    commands = []
    repo = tmp_path / "voicebox"
    expected_ref = "b35b90961d5bc83a8b4e96e8b6ccde2a03152ff9"

    def fake_which(name):
        if name == "git":
            return "/usr/bin/git"
        if name == "just":
            return ""
        return ""

    def fake_check_call(cmd, cwd=None):
        commands.append((list(cmd), cwd))
        if cmd[:3] == ["/usr/bin/git", "clone", "--no-checkout"]:
            (repo / ".git").mkdir(parents=True)
        if cmd[:3] == ["/usr/bin/git", "-c", "safe.directory=*"]:
            (repo / "backend").mkdir(parents=True)
            (repo / "backend" / "requirements.txt").write_text("", encoding="utf-8")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("subprocess.check_call", fake_check_call)
    svc = VoiceboxService({"install_dir": str(repo)})

    svc._ensure_checkout()

    assert commands[0][0] == [
        "/usr/bin/git", "clone", "--no-checkout",
        "https://github.com/jamiepine/voicebox.git", str(repo),
    ]
    assert commands[1][0] == [
        "/usr/bin/git", "-c", "safe.directory=*", "-C", str(repo),
        "checkout", "--detach", expected_ref,
    ]
    assert commands[2][0] == [
        sys.executable, "-m", "venv", str(repo / "backend" / "venv"),
    ]


def test_voicebox_auto_install_repairs_partial_no_checkout_repo(monkeypatch, tmp_path):
    commands = []
    repo = tmp_path / "voicebox"
    (repo / ".git").mkdir(parents=True)

    def fake_which(name):
        if name == "git":
            return "/usr/bin/git"
        if name == "just":
            return ""
        return ""

    def fake_check_call(cmd, cwd=None):
        commands.append((list(cmd), cwd))
        if cmd[:3] == ["/usr/bin/git", "-c", "safe.directory=*"]:
            (repo / "backend").mkdir(parents=True)
            (repo / "backend" / "requirements.txt").write_text("", encoding="utf-8")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("subprocess.check_call", fake_check_call)
    svc = VoiceboxService({"install_dir": str(repo)})

    svc._ensure_checkout()

    assert commands[0][0][:6] == [
        "/usr/bin/git", "-c", "safe.directory=*", "-C", str(repo), "checkout",
    ]
    assert not any(cmd[:3] == ["/usr/bin/git", "clone", "--no-checkout"] for cmd, _ in commands)


def test_voicebox_auto_install_uses_wsl_for_wsl_unc_checkout(monkeypatch):
    repo = Path("//wsl$/Ubuntu-24.04/home/qan/Projets/PawFlow/data/runtime/voicebox")
    linux_repo = "/home/qan/Projets/PawFlow/data/runtime/voicebox"
    commands = []
    state = set()

    def fake_which(name):
        if name == "wsl.exe":
            return "C:/Windows/System32/wsl.exe"
        if name in {"git", "python3", "python"}:
            return f"C:/bad/{name}.BAT"
        return ""

    def norm(path):
        return str(path).replace("\\", "/")

    def fake_exists(path):
        text = norm(path)
        if text == norm(repo):
            return "repo" in state
        if text == norm(repo / ".git"):
            return "git" in state
        if text == norm(repo / "backend"):
            return "backend" in state
        if text == norm(repo / "backend" / "venv" / "bin" / "python"):
            return "python" in state
        return False

    def fake_check_call(cmd, cwd=None):
        commands.append((list(cmd), cwd))
        script = cmd[-1]
        if "git clone --no-checkout" in script:
            state.update({"repo", "git"})
        if "git -C" in script and "checkout --detach" in script:
            state.add("backend")
        if "python3 -m venv backend/venv" in script:
            state.add("python")

    def fake_run(cmd, **_kwargs):
        script = cmd[-1]
        ok = "python" in state and "backend/venv/bin/python" in script
        return subprocess.CompletedProcess(cmd, 0 if ok else 1)

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("pathlib.Path.exists", fake_exists)
    monkeypatch.setattr("subprocess.check_call", fake_check_call)
    monkeypatch.setattr("subprocess.run", fake_run)
    svc = VoiceboxService({"install_dir": str(repo)})

    svc._ensure_checkout()

    assert len(commands) == 3
    assert all(cmd[:6] == [
        "C:/Windows/System32/wsl.exe", "-d", "Ubuntu-24.04", "--", "bash", "-lc",
    ] for cmd, _ in commands)
    assert linux_repo in commands[0][0][-1]
    assert "git clone --no-checkout" in commands[0][0][-1]
    assert "git -C" in commands[1][0][-1]
    assert "python3 -m venv backend/venv" in commands[2][0][-1]
    flattened = "\n".join(cmd[-1] for cmd, _ in commands)
    assert "python3.BAT" not in flattened
    assert "data\\runtime\\voicebox" not in flattened


def test_voicebox_resolve_start_command_uses_wsl_for_wsl_unc_venv(monkeypatch):
    repo = Path("//wsl$/Ubuntu-24.04/home/qan/Projets/PawFlow/data/runtime/voicebox")
    linux_repo = "/home/qan/Projets/PawFlow/data/runtime/voicebox"

    def fake_which(name):
        if name == "wsl.exe":
            return "C:/Windows/System32/wsl.exe"
        return ""

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("subprocess.run", fake_run)
    svc = VoiceboxService({"install_dir": str(repo), "base_url": "http://127.0.0.1:17493"})

    cmd, cwd = svc._resolve_start_command()

    assert cwd is None
    assert cmd[:6] == ["C:/Windows/System32/wsl.exe", "-d", "Ubuntu-24.04", "--", "bash", "-lc"]
    assert f"cd {linux_repo}" in cmd[-1]
    assert "export HF_HUB_DISABLE_PROGRESS_BARS=1" in cmd[-1]
    assert f"exec {linux_repo}/backend/venv/bin/python -m uvicorn backend.main:app" in cmd[-1]

