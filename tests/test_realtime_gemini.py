"""Gemini Live adapter (P3) — protocol normalization, resampling, wiring.

No live API: the adapter is exercised through its message normalizer and a
fake WS client capturing outbound JSON.
"""

import base64
import json

import pytest

from services._realtime_adapters import build_adapter
from services._realtime_gemini import GeminiLiveAdapter, resample_pcm16


# ── resampler ─────────────────────────────────────────────────────────

class TestResample:

    def test_identity_when_rates_match(self):
        pcm = b"\x01\x00\x02\x00\x03\x00"
        assert resample_pcm16(pcm, 24000, 24000) == pcm

    def test_24k_to_16k_ratio(self):
        # 300 samples at 24k → 200 samples at 16k
        pcm = b"\x00\x00" * 300
        out = resample_pcm16(pcm, 24000, 16000)
        assert len(out) == 2 * 200

    def test_constant_signal_survives(self):
        import array
        src = array.array("h", [1000] * 240)
        out = array.array("h")
        out.frombytes(resample_pcm16(src.tobytes(), 24000, 16000))
        assert all(v == 1000 for v in out)

    def test_odd_length_defensive(self):
        out = resample_pcm16(b"\x01\x00\x02", 24000, 16000)
        assert len(out) % 2 == 0

    def test_empty(self):
        assert resample_pcm16(b"", 24000, 16000) == b""


# ── outbound protocol (fake WS) ───────────────────────────────────────

class _FakeWS:
    def __init__(self):
        self.sent = []

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def close(self):
        pass


class TestGeminiOutbound:

    def _adapter(self, vad="server"):
        a = GeminiLiveAdapter("", "key")
        a._ws = _FakeWS()
        a._vad = vad
        return a

    def test_send_audio_resamples_to_16k(self):
        a = self._adapter()
        a.send_audio(b"\x00\x00" * 300)  # 300 samples @24k
        msg = a._ws.sent[-1]["realtimeInput"]["audio"]
        assert msg["mimeType"] == "audio/pcm;rate=16000"
        assert len(base64.b64decode(msg["data"])) == 2 * 200

    def test_manual_vad_wraps_turn_in_activity_markers(self):
        a = self._adapter(vad="manual")
        a.send_audio(b"\x00\x00" * 6)
        a.send_audio(b"\x00\x00" * 6)
        a.commit_input()
        types = [next(iter(m)) for m in a._ws.sent]
        assert types == ["realtimeInput"] * 4
        assert "activityStart" in a._ws.sent[0]["realtimeInput"]
        assert "audio" in a._ws.sent[1]["realtimeInput"]
        assert "audio" in a._ws.sent[2]["realtimeInput"]
        assert "activityEnd" in a._ws.sent[3]["realtimeInput"]

    def test_manual_commit_without_audio_is_noop(self):
        a = self._adapter(vad="manual")
        a.commit_input()
        assert a._ws.sent == []

    def test_server_vad_sends_no_activity_markers(self):
        a = self._adapter(vad="server")
        a.send_audio(b"\x00\x00" * 6)
        a.commit_input()  # no-op for server VAD
        assert len(a._ws.sent) == 1
        assert "audio" in a._ws.sent[0]["realtimeInput"]

    def test_send_tool_result_shape(self):
        a = self._adapter()
        a.send_tool_result("fc-1", "42")
        fr = a._ws.sent[-1]["toolResponse"]["functionResponses"][0]
        assert fr["id"] == "fc-1"
        assert fr["response"]["output"] == "42"

    def test_inject_context_is_a_client_turn(self):
        a = self._adapter()
        a.inject_context("tool finished")
        cc = a._ws.sent[-1]["clientContent"]
        assert cc["turnComplete"] is True
        assert cc["turns"][0]["parts"][0]["text"] == "tool finished"

    def test_function_declarations_from_openai_shape(self):
        decls = GeminiLiveAdapter._function_declarations([
            {"type": "function", "name": "recall",
             "description": "d", "parameters": {"type": "object"}}])
        assert decls == [{"name": "recall", "description": "d",
                          "parameters": {"type": "object"}}]


# ── inbound normalization ─────────────────────────────────────────────

class TestGeminiNormalize:

    def _adapter(self):
        return GeminiLiveAdapter("", "key")

    def test_audio_blob(self):
        a = self._adapter()
        raw = base64.b64encode(b"\x01\x02").decode()
        evts = a._normalize_message({"serverContent": {"modelTurn": {
            "parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000",
                                      "data": raw}}]}}})
        assert evts == [{"type": "audio", "data": b"\x01\x02"}]

    def test_transcripts_accumulate_and_flush_on_turn_complete(self):
        a = self._adapter()
        a._normalize_message({"serverContent": {
            "inputTranscription": {"text": "quel "}}})
        a._normalize_message({"serverContent": {
            "inputTranscription": {"text": "temps ?"}}})
        a._normalize_message({"serverContent": {
            "outputTranscription": {"text": "il pleut"}}})
        evts = a._normalize_message({"serverContent": {"turnComplete": True}})
        finals = [e for e in evts if e.get("final")]
        # user final BEFORE agent final (bridge ordering contract)
        assert finals[0] == {"type": "transcript_user",
                             "text": "quel temps ?", "final": True}
        assert finals[1] == {"type": "transcript_agent",
                             "text": "il pleut", "final": True}
        assert evts[-1]["type"] == "response_done"

    def test_interrupted_flushes_and_signals_speech(self):
        a = self._adapter()
        a._normalize_message({"serverContent": {
            "outputTranscription": {"text": "je disais"}}})
        evts = a._normalize_message({"serverContent": {"interrupted": True}})
        assert {"type": "transcript_agent", "text": "je disais",
                "final": True} in evts
        assert evts[-1] == {"type": "speech_started"}

    def test_tool_calls(self):
        a = self._adapter()
        evts = a._normalize_message({"toolCall": {"functionCalls": [
            {"id": "fc-1", "name": "recall", "args": {"q": "x"}}]}})
        assert evts == [{"type": "tool_call", "call_id": "fc-1",
                         "name": "recall", "arguments": '{"q": "x"}'}]

    def test_resumption_handle_captured(self):
        a = self._adapter()
        assert a.resumption_state() == ""
        a._normalize_message({"sessionResumptionUpdate": {
            "newHandle": "h-123", "resumable": True}})
        assert a.resumption_state() == "h-123"
        # Non-resumable updates must not clobber a good handle.
        a._normalize_message({"sessionResumptionUpdate": {
            "newHandle": "h-456", "resumable": False}})
        assert a.resumption_state() == "h-123"

    def test_usage_metadata_lands_on_response_done(self):
        a = self._adapter()
        a._normalize_message({"usageMetadata": {"totalTokenCount": 7}})
        evts = a._normalize_message({"serverContent": {"turnComplete": True}})
        assert evts[-1] == {"type": "response_done",
                            "usage": {"totalTokenCount": 7}}

    def test_error_is_fatal(self):
        a = self._adapter()
        evts = a._normalize_message({"error": {"message": "boom"}})
        assert evts == [{"type": "error", "message": "boom", "fatal": True}]

    def test_recv_event_drains_pending_queue(self):
        a = self._adapter()
        a._pending.extend([{"type": "audio", "data": b"x"},
                           {"type": "response_done", "usage": {}}])
        assert a.recv_event(timeout=0.01) == {"type": "audio", "data": b"x"}
        assert a.recv_event(timeout=0.01) == {"type": "response_done",
                                              "usage": {}}


# ── factory / service wiring ─────────────────────────────────────────

def test_build_adapter_gemini_live():
    a = build_adapter("gemini_live", base_url="", api_key="k")
    assert isinstance(a, GeminiLiveAdapter)


def test_service_accepts_gemini_live_protocol():
    from services.realtime_voice_service import RealtimeVoiceConnectionService
    svc = RealtimeVoiceConnectionService({
        "llm_service": "gem", "model": "gemini-2.5-flash-native-audio",
        "protocol": "gemini_live"})
    assert svc._create_connection() == {"ready": True}


def test_service_requires_gemini_credentials_for_gemini_live(monkeypatch):
    from core import ServiceError
    from services.realtime_voice_service import RealtimeVoiceConnectionService

    class _LLM:
        provider = "openai"   # wrong provider for gemini_live
        api_key = "sk"

    class _Def:
        service_type = "llmConnection"

    class _Reg:
        def resolve_definition(self, sid, **kw):
            return _Def()

        def resolve(self, sid, **kw):
            return _LLM()

    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: _Reg()))
    svc = RealtimeVoiceConnectionService({
        "llm_service": "llm", "model": "m", "protocol": "gemini_live"})
    with pytest.raises(ServiceError, match="requires a 'gemini'"):
        svc._resolve_llm_service(user_id="u")


def test_gemini_url_uses_api_key_and_default_host():
    a = GeminiLiveAdapter("", "secret-key")
    url = a._url()
    assert url.startswith("wss://generativelanguage.googleapis.com/ws/")
    assert "key=secret-key" in url
