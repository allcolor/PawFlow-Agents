import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from core import ServiceError
from services import http_listener_service as _hl_mod
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


class _Listener:
    is_ssl = False
    public_hostname = ""


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


def test_voicebox_installs_backend_runner_when_missing(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1)

    def fake_check_call(cmd, **_kwargs):
        calls.append(cmd)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "check_call", fake_check_call)
    python = tmp_path / "backend" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    requirements = tmp_path / "backend" / "requirements.txt"
    requirements.write_text("sqlalchemy\n", encoding="utf-8")

    svc = VoiceboxService({"install_dir": str(tmp_path)})
    svc._ensure_backend_runner(python)

    assert [str(python), "-c", "import uvicorn, fastapi, numpy, torch; import backend.main"] in calls
    assert [str(python), "-m", "pip", "install", "-r", str(requirements)] in calls
    assert [
        str(python), "-m", "pip", "install",
        "fastapi>=0.110", "numpy>=1.26", "uvicorn[standard]>=0.30",
    ] in calls
    assert [
        str(python), "-m", "pip", "install", "torch>=2.3",
        "--index-url", "https://download.pytorch.org/whl/cpu",
    ] in calls


def test_voicebox_startup_error_reads_backend_log(tmp_path):
    svc = VoiceboxService({"install_dir": str(tmp_path), "startup_timeout": 1})
    svc._managed_log_path.parent.mkdir(parents=True)
    svc._managed_log_path.write_text("line 1\nNo module named uvicorn\n", encoding="utf-8")

    class DeadProc:
        def poll(self):
            return 1

    with pytest.raises(ServiceError, match="No module named uvicorn"):
        svc._wait_ready(DeadProc())


def test_voicebox_external_relay_url_uses_proxy_route(monkeypatch):
    captured = {}
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id: "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")
    monkeypatch.setattr("core.relay_bindings.get_default", lambda cid, agent="": "relay1")

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = req.data
        return _Resp(b"MP3", "audio/mpeg")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoiceboxService({
        "base_url": "http://${conv.relay}/localhost:17493",
        "auto_start": False,
        "default_profile": "Siwis",
    })
    svc.set_runtime_context(user_id="alice", conversation_id="conv1")
    monkeypatch.setattr(svc, "ensure_connected", lambda: None)
    monkeypatch.setattr(svc, "_resolve_profile_id", lambda profile: "p1")
    monkeypatch.setattr(svc, "_active_download_error", lambda model_name="": "")

    out = svc.speak("Bonjour")

    assert out["audio_bytes"] == b"MP3"
    assert captured["url"] == "http://10.0.0.2:9090/relay-proxy/relay1/tok/localhost:17493/speak"
    assert b"Bonjour" in captured["body"]


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


def test_voicebox_warmup_stt_transcribes_silent_wav_once(monkeypatch):
    svc = VoiceboxService({"stt_model": "turbo"})
    calls = []

    def fake_transcribe(**kwargs):
        calls.append(kwargs)
        return {"text": "", "language": "", "duration": 0}

    monkeypatch.setattr(svc, "transcribe", fake_transcribe)

    svc.warmup_stt(language="fr")
    svc.warmup_stt(language="fr")

    assert len(calls) == 1
    assert calls[0]["mime_type"] == "audio/wav"
    assert calls[0]["filename"] == "warmup.wav"
    assert calls[0]["language"] == "fr"
    assert calls[0]["model"] == "turbo"
    assert calls[0]["audio_bytes"].startswith(b"RIFF")


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


def test_voicebox_speak_uses_async_speak_for_known_profile(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append((req.get_method(), req.full_url, req.data))
        if req.get_method() == "GET" and req.full_url.endswith("/health"):
            return _Resp(b'{"ok":true}')
        if req.get_method() == "GET" and req.full_url.endswith("/profiles"):
            return _Resp(json.dumps([{"id": "p1", "name": "Siwis"}]).encode())
        if req.get_method() == "POST" and req.full_url.endswith("/speak"):
            body = json.loads(req.data.decode())
            assert body["profile"] == "p1"
            assert body["language"] == "fr"
            return _Resp(json.dumps({
                "id": "gen1",
                "profile_id": "p1",
                "status": "generating",
            }).encode())
        if req.get_method() == "GET" and req.full_url.endswith("/history/gen1"):
            return _Resp(json.dumps({
                "id": "gen1",
                "status": "completed",
                "audio_path": "audio/gen1.wav",
            }).encode())
        if req.get_method() == "GET" and req.full_url.endswith("/audio/gen1"):
            return _Resp(b"RIFFaudio", "audio/wav")
        raise AssertionError(req.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    svc = VoiceboxService({"default_profile": "Siwis"})

    out = svc.speak(text="bonjour", language="fr")

    assert out["audio_bytes"] == b"RIFFaudio"
    assert out["content_type"] == "audio/wav"
    assert any(url.endswith("/speak") for _method, url, _data in calls)


def test_voicebox_speak_resolves_preset_voice_id_to_profile(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=0):
        if req.get_method() == "GET" and req.full_url.endswith("/health"):
            return _Resp(b'{"ok":true}')
        if req.get_method() == "GET" and req.full_url.endswith("/profiles"):
            return _Resp(json.dumps([{
                "id": "p-siwis",
                "name": "Siwis",
                "preset_voice_id": "ff_siwis",
            }]).encode())
        if req.get_method() == "POST" and req.full_url.endswith("/speak"):
            captured["body"] = json.loads(req.data.decode())
            return _Resp(b"mp3", "audio/mpeg")
        raise AssertionError(req.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoiceboxService({"default_profile": "ff_siwis"})

    out = svc.speak(text="bonjour", language="fr")

    assert out["audio_bytes"] == b"mp3"
    assert captured["body"]["profile"] == "p-siwis"
    assert captured["body"]["language"] == "fr"


def test_voicebox_speak_waits_for_async_speak_audio(monkeypatch):
    history_calls = 0

    def fake_urlopen(req, timeout=0):
        nonlocal history_calls
        if req.get_method() == "GET" and req.full_url.endswith("/health"):
            return _Resp(b'{"ok":true}')
        if req.get_method() == "POST" and req.full_url.endswith("/speak"):
            return _Resp(json.dumps({
                "id": "gen1",
                "profile_id": "p1",
                "status": "generating",
            }).encode())
        if req.get_method() == "GET" and req.full_url.endswith("/history/gen1"):
            history_calls += 1
            status = "completed" if history_calls > 1 else "generating"
            return _Resp(json.dumps({
                "id": "gen1",
                "status": status,
                "audio_path": "audio/gen1.wav" if status == "completed" else "",
            }).encode())
        if req.get_method() == "GET" and req.full_url.endswith("/audio/gen1"):
            return _Resp(b"RIFFasync", "audio/wav")
        raise AssertionError(req.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    svc = VoiceboxService({"timeout": 3})

    out = svc.speak(text="bonjour")

    assert out["audio_bytes"] == b"RIFFasync"
    assert out["content_type"] == "audio/wav"


def test_voicebox_speak_reads_local_audio_when_audio_endpoint_cannot_resolve_path(monkeypatch, tmp_path):
    generation_id = "gen-local"
    audio_path = tmp_path / "voicebox" / "data" / "generations" / f"{generation_id}.wav"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"RIFFlocal")

    def fake_urlopen(req, timeout=0):
        if req.get_method() == "GET" and req.full_url.endswith("/health"):
            return _Resp(b'{"ok":true}')
        if req.get_method() == "POST" and req.full_url.endswith("/speak"):
            return _Resp(json.dumps({"id": generation_id, "status": "generating"}).encode())
        if req.get_method() == "GET" and req.full_url.endswith(f"/history/{generation_id}"):
            return _Resp(json.dumps({
                "id": generation_id,
                "status": "completed",
                "audio_path": "runtime/voicebox/data/generations/gen-local.wav",
            }).encode())
        if req.get_method() == "GET" and req.full_url.endswith(f"/audio/{generation_id}"):
            raise AssertionError("local audio should be read before /audio fallback")
        raise AssertionError(req.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    svc = VoiceboxService({"install_dir": str(tmp_path / "voicebox")})

    out = svc.speak(text="bonjour")

    assert out["audio_bytes"] == b"RIFFlocal"
    assert out["content_type"] == "audio/x-wav"


def test_voicebox_speak_auto_creates_preset_default_profile(monkeypatch):
    created = {}

    def fake_urlopen(req, timeout=0):
        if req.get_method() == "GET" and req.full_url.endswith("/health"):
            return _Resp(b'{"ok":true}')
        if req.get_method() == "GET" and req.full_url.endswith("/profiles"):
            return _Resp(b"[]")
        if req.get_method() == "GET" and req.full_url.endswith("/profiles/presets/kokoro"):
            return _Resp(json.dumps({
                "engine": "kokoro",
                "voices": [{
                    "voice_id": "ff_siwis",
                    "name": "Siwis",
                    "gender": "female",
                    "language": "fr",
                }],
            }).encode())
        if req.get_method() == "POST" and req.full_url.endswith("/profiles"):
            created.update(json.loads(req.data.decode()))
            return _Resp(json.dumps({"id": "siwis-id", **created}).encode())
        if req.get_method() == "POST" and req.full_url.endswith("/speak"):
            body = json.loads(req.data.decode())
            assert body["profile"] == "siwis-id"
            return _Resp(json.dumps({
                "id": "gen-siwis",
                "profile_id": "siwis-id",
                "status": "generating",
            }).encode())
        if req.get_method() == "GET" and req.full_url.endswith("/history/gen-siwis"):
            return _Resp(json.dumps({
                "id": "gen-siwis",
                "status": "completed",
                "audio_path": "audio/gen-siwis.wav",
            }).encode())
        if req.get_method() == "GET" and req.full_url.endswith("/audio/gen-siwis"):
            return _Resp(b"RIFFsiwis", "audio/wav")
        raise AssertionError(req.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    svc = VoiceboxService({"default_profile": "Siwis"})

    out = svc.speak(text="bonjour", language="fr")

    assert out["audio_bytes"] == b"RIFFsiwis"
    assert created["voice_type"] == "preset"
    assert created["preset_engine"] == "kokoro"
    assert created["preset_voice_id"] == "ff_siwis"
    assert created["language"] == "fr"


def test_voicebox_service_exposes_profile_actions():
    action_ids = {item["id"] for item in VoiceboxService({}).get_service_actions()}

    assert "voicebox_profiles_list" in action_ids
    assert "voicebox_preset_voices_list" in action_ids
    assert "voicebox_profile_save" in action_ids
    assert "voicebox_tasks_clear" in action_ids


def test_voicebox_managed_checkout_patch_silences_tqdm(tmp_path):
    progress = tmp_path / "voicebox" / "backend" / "utils" / "hf_progress.py"
    progress.parent.mkdir(parents=True)
    progress.write_text(
        """
filtered_kwargs["disable"] = False
kwargs["disable"] = False
def update(self, n=1):
                result = super().update(n)

                # Report progress
def patched_update(tqdm_self, n=1):
                        result = tracker._hf_tqdm_original_update(tqdm_self, n)

                        # Track this progress
""".strip(),
        encoding="utf-8",
    )
    svc = VoiceboxService({"install_dir": str(tmp_path / "voicebox")})

    svc._patch_managed_checkout()

    patched = progress.read_text(encoding="utf-8")
    assert 'filtered_kwargs["disable"] = True' in patched
    assert 'kwargs["disable"] = True' in patched
    assert "except BrokenPipeError" in patched
    assert "tqdm_self.n = before + n" in patched


def test_voicebox_close_connection_shuts_down_existing_loopback_backend(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append((req.get_method(), req.full_url, timeout))
        return _Resp(b'{"message":"Shutting down..."}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    svc = VoiceboxService({"base_url": "http://127.0.0.1:17493", "auto_start": True})

    svc._close_connection()

    assert calls == [("POST", "http://127.0.0.1:17493/shutdown", 5)]


def test_voicebox_close_connection_does_not_shutdown_external_backend(monkeypatch):
    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: calls.append("urlopen"))
    svc = VoiceboxService({"base_url": "http://voicebox.example.test:17493", "auto_start": False})

    svc._close_connection()

    assert calls == []


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


def test_voicebox_prepare_install_preloads_model_with_reporter(monkeypatch, tmp_path):
    svc = VoiceboxService({
        "install_dir": str(tmp_path / "voicebox"),
        "preload_stt_model": True,
        "auto_start": True,
    })
    calls = []
    steps = []

    class Reporter:
        def step(self, phase, message="", status="running", progress=None, **_extra):
            steps.append((phase, message, status, progress))

    monkeypatch.setattr(svc, "get_install_requirements", lambda: [])
    monkeypatch.setattr(svc, "_ensure_checkout", lambda reporter=None: calls.append("checkout"))
    monkeypatch.setattr(svc, "_server_ready", lambda: False)
    monkeypatch.setattr(svc, "_start_server", lambda: calls.append("start"))
    monkeypatch.setattr(svc, "_preload_stt_model", lambda reporter=None: calls.append("preload"))
    monkeypatch.setattr(svc, "_close_connection", lambda: calls.append("close"))

    result = svc.prepare_install(Reporter())

    assert result["prepared"] is True
    assert calls == ["checkout", "start", "preload", "close"]
    assert ("checking_requirements", "Checking Voicebox requirements", "running", None) in steps
    assert (svc.install_dir / ".pawflow_install.json").exists()


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

    assert len(commands) == 4
    assert all(cmd[:6] == [
        "C:/Windows/System32/wsl.exe", "-d", "Ubuntu-24.04", "--", "bash", "-lc",
    ] for cmd, _ in commands)
    assert linux_repo in commands[0][0][-1]
    assert "git clone --no-checkout" in commands[0][0][-1]
    assert "git -C" in commands[1][0][-1]
    assert "python3 -m venv backend/venv" in commands[2][0][-1]
    assert "backend/utils/hf_progress.py" in commands[3][0][-1]
    assert "BrokenPipeError" in commands[3][0][-1]
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

