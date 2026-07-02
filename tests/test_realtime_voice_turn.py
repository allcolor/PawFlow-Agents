"""Tests for the turn-based realtime voice runner (P2c) and its Telegram
voice-note integration. Fake adapters/services throughout — no provider,
no ffmpeg (transcode helpers are monkeypatched at the seam)."""

import base64
import json
import queue

import pytest

from core import FlowFile
from services._realtime_turn import run_voice_turn


class _TurnAdapter:
    def __init__(self, events):
        self._events = queue.Queue()
        for e in events:
            self._events.put(e)
        self.sent_audio = []
        self.commits = 0
        self.tool_results = []
        self.closed = False

    def send_audio(self, chunk):
        self.sent_audio.append(bytes(chunk))

    def commit_input(self):
        self.commits += 1

    def send_tool_result(self, call_id, result):
        self.tool_results.append((call_id, result))

    def recv_event(self, timeout=1.0):
        try:
            return self._events.get(timeout=min(timeout, 0.05))
        except queue.Empty:
            return None

    def close(self):
        self.closed = True


class _TurnService:
    instructions_mode = "custom"
    instructions = "Be brief."
    tool_profile = ""

    def __init__(self, adapter):
        self._adapter = adapter
        self.opened_vad = None
        self.opened_tools = None

    def open_session(self, instructions="", tools=None, vad=""):
        self.opened_vad = vad
        self.opened_tools = tools
        return self._adapter


@pytest.fixture
def persisted(monkeypatch):
    calls = []
    import services._realtime_bridge as rb

    def _fake_persist(cid, agent, user, role, text, channel="voice"):
        calls.append((role, text, channel))

    monkeypatch.setattr(rb, "persist_voice_transcript", _fake_persist)
    return calls


class TestRunVoiceTurn:

    def test_happy_path(self, persisted):
        adapter = _TurnAdapter([
            {"type": "transcript_user", "text": "salut", "final": True},
            {"type": "audio", "data": b"\x01\x02"},
            {"type": "audio", "data": b"\x03\x04"},
            {"type": "transcript_agent", "text": "bonjour !", "final": True},
            {"type": "response_done", "usage": {}},
        ])
        service = _TurnService(adapter)
        result = run_voice_turn(
            service, conversation_id="conv1", agent_name="claude",
            user_id="quentin", pcm16=b"\x00" * 100000, timeout_s=10,
            user_channel="telegram")
        assert result["audio"] == b"\x01\x02\x03\x04"
        assert result["user_text"] == "salut"
        assert result["agent_text"] == "bonjour !"
        # manual VAD forced for one-shot turns, audio chunked + committed
        assert service.opened_vad == "manual"
        assert adapter.commits == 1
        assert b"".join(adapter.sent_audio) == b"\x00" * 100000
        assert len(adapter.sent_audio) > 1  # chunked
        assert adapter.closed
        # transcripts persisted; user side carries its origin channel
        assert ("user", "salut", "telegram") in persisted
        assert ("assistant", "bonjour !", "voice") in persisted

    def test_tool_call_path(self, persisted, monkeypatch):
        class _FakeToolBridge:
            def __init__(self, profile, cid, agent, user):
                assert profile == "echo"

            def tool_definitions(self):
                return [{"type": "function", "name": "echo",
                         "description": "d", "parameters": {}}]

            def handle_call(self, call_id, name, arguments, *,
                            send_result, announce=None, **kw):
                send_result(call_id, "tool says 4")
                return "done"

        import services._realtime_tools as rt_tools
        monkeypatch.setattr(rt_tools, "RealtimeToolBridge", _FakeToolBridge)
        # Realistic provider ordering: the function-call response ends with
        # its OWN response_done; the spoken answer follows as a second
        # response with a second response_done. The turn must not stop at
        # the first one (regression: it did when the user transcript had
        # already arrived).
        adapter = _TurnAdapter([
            {"type": "transcript_user", "text": "2+2 ?", "final": True},
            {"type": "tool_call", "call_id": "c1", "name": "echo",
             "arguments": "{}"},
            {"type": "response_done", "usage": {}},  # function-call response
            {"type": "audio", "data": b"\x05"},
            {"type": "transcript_agent", "text": "quatre", "final": True},
            {"type": "response_done", "usage": {}},  # spoken follow-up
        ])
        service = _TurnService(adapter)
        service.tool_profile = "echo"
        result = run_voice_turn(
            service, conversation_id="conv1", agent_name="claude",
            user_id="quentin", pcm16=b"\x00\x01", timeout_s=10)
        assert adapter.tool_results == [("c1", "tool says 4")]
        assert service.opened_tools and \
            service.opened_tools[0]["name"] == "echo"
        assert result["agent_text"] == "quatre"
        assert result["audio"] == b"\x05"  # collected AFTER the first done

    def test_no_tools_refuses_stray_call(self, persisted):
        adapter = _TurnAdapter([
            {"type": "tool_call", "call_id": "c9", "name": "bash",
             "arguments": "{}"},
            {"type": "response_done", "usage": {}},  # function-call response
            {"type": "transcript_agent", "text": "ok", "final": True},
            {"type": "response_done", "usage": {}},
            {"type": "transcript_user", "text": "hey", "final": True},
        ])
        service = _TurnService(adapter)
        run_voice_turn(service, conversation_id="conv1",
                       agent_name="claude", user_id="quentin",
                       pcm16=b"\x00", timeout_s=10)
        assert adapter.tool_results
        assert "not enabled" in adapter.tool_results[0][1]

    def test_fatal_error_without_output_raises(self, persisted):
        adapter = _TurnAdapter([
            {"type": "error", "message": "quota", "fatal": True},
        ])
        service = _TurnService(adapter)
        with pytest.raises(RuntimeError):
            run_voice_turn(service, conversation_id="conv1",
                           agent_name="claude", user_id="quentin",
                           pcm16=b"\x00", timeout_s=10)
        assert adapter.closed
        assert persisted == []  # nothing persisted on failure

    def test_empty_audio_rejected(self, persisted):
        with pytest.raises(ValueError):
            run_voice_turn(_TurnService(_TurnAdapter([])),
                           conversation_id="c", agent_name="a",
                           user_id="u", pcm16=b"", timeout_s=5)


# ── Telegram voice-note integration ─────────────────────────────────

class TestTelegramRealtimeVoiceReply:

    def _payload(self):
        return json.dumps({
            "type": "voice",
            "data_base64": base64.b64encode(b"OGGDATA").decode(),
            "mime_type": "audio/ogg",
        })

    def test_no_agent_link_returns_false(self, monkeypatch):
        import tasks.io._telegram_voice as tv
        monkeypatch.setattr(tv, "_agent_realtime_service_id",
                            lambda cid, agent: "")
        ff = FlowFile(content=b"")
        assert tv._telegram_realtime_voice_reply(
            ff, self._payload(), "quentin", "conv1", "claude") is False

    def test_happy_path_attaches_voice_note(self, monkeypatch):
        import tasks.io._telegram_voice as tv
        import services._realtime_turn as turn_mod

        class _SvcDef:
            service_type = "realtimeVoiceConnection"

        class _Svc:
            def set_runtime_context(self, **kw):
                self.ctx = kw

        class _Reg:
            def resolve_definition(self, sid, **kw):
                return _SvcDef()

            def resolve(self, sid, **kw):
                return _Svc()

        monkeypatch.setattr(tv, "_agent_realtime_service_id",
                            lambda cid, agent: "rt-voice")
        monkeypatch.setattr(
            "core.service_registry.ServiceRegistry.get_instance",
            staticmethod(lambda: _Reg()))
        monkeypatch.setattr(tv, "_decode_audio_to_pcm16",
                            lambda audio, suffix=".ogg": b"PCMIN")
        monkeypatch.setattr(tv, "_encode_pcm16_to_ogg",
                            lambda pcm: b"OGGOUT")
        captured = {}

        def _fake_turn(service, **kw):
            captured.update(kw)
            return {"audio": b"PCMOUT", "user_text": "salut",
                    "agent_text": "bonjour"}

        monkeypatch.setattr(turn_mod, "run_voice_turn", _fake_turn)
        ff = FlowFile(content=b"")
        assert tv._telegram_realtime_voice_reply(
            ff, self._payload(), "quentin", "conv1", "claude") is True
        assert captured["pcm16"] == b"PCMIN"
        assert captured["user_channel"] == "telegram"
        assert base64.b64decode(
            ff.get_attribute("telegram.tts_audio_base64")) == b"OGGOUT"
        assert ff.get_attribute("telegram.tts_content_type") == "audio/ogg"
        assert ff.get_attribute("telegram.tts_filename") == "voice_reply.ogg"
        # text travels via the live bridge, not the direct reply
        assert ff.get_content() == b""

    def test_encode_failure_downgrades_to_text_not_stt_fallback(
            self, monkeypatch):
        """After a SUCCESSFUL turn (transcripts persisted), an OGG encode
        failure must reply with text and still return True — a False here
        would push the same voice note through the STT pipeline again."""
        import tasks.io._telegram_voice as tv
        import services._realtime_turn as turn_mod

        class _SvcDef:
            service_type = "realtimeVoiceConnection"

        class _Reg:
            def resolve_definition(self, sid, **kw):
                return _SvcDef()

            def resolve(self, sid, **kw):
                return object()

        monkeypatch.setattr(tv, "_agent_realtime_service_id",
                            lambda cid, agent: "rt-voice")
        monkeypatch.setattr(
            "core.service_registry.ServiceRegistry.get_instance",
            staticmethod(lambda: _Reg()))
        monkeypatch.setattr(tv, "_decode_audio_to_pcm16",
                            lambda audio, suffix=".ogg": b"PCMIN")

        def _bad_encode(pcm):
            raise RuntimeError("no libopus")

        monkeypatch.setattr(tv, "_encode_pcm16_to_ogg", _bad_encode)
        monkeypatch.setattr(
            turn_mod, "run_voice_turn",
            lambda service, **kw: {"audio": b"PCMOUT", "user_text": "salut",
                                   "agent_text": "bonjour"})
        ff = FlowFile(content=b"")
        assert tv._telegram_realtime_voice_reply(
            ff, self._payload(), "quentin", "conv1", "claude") is True
        assert ff.get_content() == b"bonjour"
        assert not ff.get_attribute("telegram.tts_audio_base64")

    def test_turn_failure_falls_back(self, monkeypatch):
        import tasks.io._telegram_voice as tv
        import services._realtime_turn as turn_mod

        class _SvcDef:
            service_type = "realtimeVoiceConnection"

        class _Reg:
            def resolve_definition(self, sid, **kw):
                return _SvcDef()

            def resolve(self, sid, **kw):
                return object()

        monkeypatch.setattr(tv, "_agent_realtime_service_id",
                            lambda cid, agent: "rt-voice")
        monkeypatch.setattr(
            "core.service_registry.ServiceRegistry.get_instance",
            staticmethod(lambda: _Reg()))
        monkeypatch.setattr(tv, "_decode_audio_to_pcm16",
                            lambda audio, suffix=".ogg": b"PCMIN")

        def _boom(service, **kw):
            raise RuntimeError("provider down")

        monkeypatch.setattr(turn_mod, "run_voice_turn", _boom)
        ff = FlowFile(content=b"")
        assert tv._telegram_realtime_voice_reply(
            ff, self._payload(), "quentin", "conv1", "claude") is False

    def test_wrong_service_type_returns_false(self, monkeypatch):
        import tasks.io._telegram_voice as tv

        class _SvcDef:
            service_type = "openaiCompatibleTTS"

        class _Reg:
            def resolve_definition(self, sid, **kw):
                return _SvcDef()

        monkeypatch.setattr(tv, "_agent_realtime_service_id",
                            lambda cid, agent: "tts-not-rt")
        monkeypatch.setattr(
            "core.service_registry.ServiceRegistry.get_instance",
            staticmethod(lambda: _Reg()))
        ff = FlowFile(content=b"")
        assert tv._telegram_realtime_voice_reply(
            ff, self._payload(), "quentin", "conv1", "claude") is False


def test_telegram_client_routes_voice_through_realtime_first():
    from pathlib import Path
    src = Path("tasks/io/telegram_agent_client.py").read_text(encoding="utf-8")
    idx_rt = src.index("_telegram_realtime_voice_reply")
    idx_stt = src.index("_transcribe_telegram_voice_result(\n                text")
    assert idx_rt < idx_stt
