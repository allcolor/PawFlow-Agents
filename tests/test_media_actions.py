import base64
import json
from types import SimpleNamespace

from core import FlowFile
from core.conversation_store import ConversationStore
from core.file_store import FileStore
from tasks.ai.actions.media import _handle_media


def _payload(flowfile):
    return json.loads(flowfile.get_content().decode("utf-8"))


class _SttService:
    def __init__(self):
        self.runtime_context = None
        self.calls = []

    def set_runtime_context(self, **kwargs):
        self.runtime_context = kwargs

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        audio_path = kwargs.get("audio_path") or ""
        assert audio_path
        with open(audio_path, "rb") as handle:
            assert handle.read() == b"RIFFaudio"
        assert kwargs.get("audio_bytes") == b""
        return {"text": "bonjour", "language": "fr", "duration": 1.2}


class _BrowserAudioSttService:
    ACCEPTS_BROWSER_STT_AUDIO = True

    def __init__(self):
        self.calls = []

    def set_runtime_context(self, **_kwargs):
        pass

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        audio_path = kwargs.get("audio_path") or ""
        assert audio_path
        with open(audio_path, "rb") as handle:
            assert handle.read() == b"WEBM"
        assert kwargs.get("audio_bytes") == b""
        assert kwargs.get("mime_type") == "audio/webm"
        assert kwargs.get("filename") == "speech.webm"
        return {"text": "bonjour", "language": "fr", "duration": 1.2}


class _WarmupTtsService:
    def __init__(self):
        self.calls = []

    def set_runtime_context(self, **kwargs):
        self.calls.append(("context", kwargs))

    def ensure_connected(self):
        self.calls.append(("ensure", {}))

    def warmup(self, **kwargs):
        self.calls.append(("warmup", kwargs))


class _WarmupSttService:
    def __init__(self):
        self.calls = []

    def set_runtime_context(self, **kwargs):
        self.calls.append(("context", kwargs))

    def warmup_stt(self, **kwargs):
        self.calls.append(("warmup_stt", kwargs))


def test_stt_transcribe_deletes_transient_filestore_audio(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", file_store)

    svc = _SttService()

    class _Registry:
        @staticmethod
        def get_instance():
            return _Registry()

        def resolve(self, name, user_id="", conv_id=""):
            assert name == "stt1"
            assert user_id == "alice"
            assert conv_id == "conv1"
            return svc

    monkeypatch.setattr("core.service_registry.ServiceRegistry", _Registry)
    holder = SimpleNamespace(config={})
    ff = FlowFile(content=b"")

    _handle_media(
        holder,
        "stt_transcribe",
        {
            "conversation_id": "conv1",
            "service": "stt1",
            "audio_b64": base64.b64encode(b"RIFFaudio").decode("ascii"),
            "mime_type": "audio/wav",
            "filename": "speech.wav",
            "language": "fr",
        },
        conv_store,
        "alice",
        ff,
    )

    assert _payload(ff)["text"] == "bonjour"
    assert svc.runtime_context["conversation_id"] == "conv1"
    assert file_store.count() == 0


def test_stt_transcribe_keeps_browser_audio_for_native_services(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    file_store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", file_store)

    svc = _BrowserAudioSttService()

    class _Registry:
        @staticmethod
        def get_instance():
            return _Registry()

        def resolve(self, name, user_id="", conv_id=""):
            assert name == "voicebox1"
            return svc

    monkeypatch.setattr("core.service_registry.ServiceRegistry", _Registry)
    holder = SimpleNamespace(config={})
    ff = FlowFile(content=b"")

    _handle_media(
        holder,
        "stt_transcribe",
        {
            "conversation_id": "conv1",
            "service": "voicebox1",
            "audio_b64": base64.b64encode(b"WEBM").decode("ascii"),
            "mime_type": "audio/webm",
            "filename": "speech.webm",
            "language": "fr",
        },
        conv_store,
        "alice",
        ff,
    )

    assert _payload(ff)["text"] == "bonjour"
    assert len(svc.calls) == 1
    assert file_store.count() == 0


def test_tts_warmup_invokes_service_warmup(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    svc = _WarmupTtsService()

    class _Registry:
        @staticmethod
        def get_instance():
            return _Registry()

        def resolve(self, name, user_id="", conv_id=""):
            assert name == "tts1"
            return svc

    monkeypatch.setattr("core.service_registry.ServiceRegistry", _Registry)
    holder = SimpleNamespace(config={})
    ff = FlowFile(content=b"")

    _handle_media(
        holder,
        "tts_warmup",
        {
            "conversation_id": "conv1",
            "service": "tts1",
            "voice": "F1",
            "language": "fr",
        },
        conv_store,
        "alice",
        ff,
    )

    assert _payload(ff)["ok"] is True
    assert svc.calls == [
        ("context", {"user_id": "alice", "conversation_id": "conv1", "agent_name": "agent"}),
        ("warmup", {"voice": "F1", "language": "fr"}),
    ]


def test_stt_warmup_invokes_service_warmup(tmp_path, monkeypatch):
    conv_store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    svc = _WarmupSttService()

    class _Registry:
        @staticmethod
        def get_instance():
            return _Registry()

        def resolve(self, name, user_id="", conv_id=""):
            assert name == "stt1"
            return svc

    monkeypatch.setattr("core.service_registry.ServiceRegistry", _Registry)
    holder = SimpleNamespace(config={})
    ff = FlowFile(content=b"")

    _handle_media(
        holder,
        "stt_warmup",
        {
            "conversation_id": "conv1",
            "service": "stt1",
            "language": "fr",
            "model": "turbo",
        },
        conv_store,
        "alice",
        ff,
    )

    assert _payload(ff)["ok"] is True
    assert svc.calls == [
        ("context", {"user_id": "alice", "conversation_id": "conv1", "agent_name": "agent"}),
        ("warmup_stt", {"language": "fr", "model": "turbo"}),
    ]
