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
