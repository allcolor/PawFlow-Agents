"""Tests for the realtime voice stack — adapters, service, bridge, route.

No live provider API: the adapter is tested against a local fake WS server
and the bridge against a scripted fake adapter over a socketpair.
"""

import base64
import json
import os
import queue
import socket
import struct
import threading
import time
from pathlib import Path

import pytest

from core import ServiceError
from services._realtime_adapters import (
    OpenAIRealtimeAdapter, RealtimeWSClient, build_adapter)
from services._realtime_bridge import (
    RealtimeSessionBridge, _active_bridges, register_realtime_route,
    stop_realtime_session)
from services.realtime_voice_service import RealtimeVoiceConnectionService


# ── helpers ─────────────────────────────────────────────────────────

def _client_send_frame(sock, opcode, payload: bytes):
    """Send a masked client frame (RFC 6455 §5.1 — clients MUST mask)."""
    mask = os.urandom(4)
    hdr = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        hdr.append(0x80 | n)
    elif n < 65536:
        hdr.append(0x80 | 126)
        hdr.extend(struct.pack("!H", n))
    else:
        hdr.append(0x80 | 127)
        hdr.extend(struct.pack("!Q", n))
    hdr.extend(mask)
    sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _client_recv_frame(sock, timeout=5.0):
    """Receive one (unmasked) server frame."""
    sock.settimeout(timeout)

    def rx(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    hdr = rx(2)
    if hdr is None:
        return None, b""
    opcode = hdr[0] & 0x0F
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", rx(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", rx(8))[0]
    payload = rx(length) if length else b""
    return opcode, payload


# ── adapter: URL derivation & event normalization ───────────────────

class TestOpenAIRealtimeAdapter:

    def _adapter(self, base="https://api.openai.com/v1"):
        return OpenAIRealtimeAdapter(base, "sk-test")

    @pytest.mark.parametrize("base,expected", [
        ("https://api.openai.com/v1",
         "wss://api.openai.com/v1/realtime?model=gpt-realtime"),
        ("https://my-azure.openai.azure.com/openai/v1",
         "wss://my-azure.openai.azure.com/openai/v1/realtime?model=gpt-realtime"),
        ("https://api.openai.com",
         "wss://api.openai.com/v1/realtime?model=gpt-realtime"),
    ])
    def test_realtime_url_from_base_url(self, base, expected):
        assert self._adapter(base)._realtime_url("gpt-realtime") == expected

    def test_normalize_audio_delta_both_names(self):
        a = self._adapter()
        raw = base64.b64encode(b"\x01\x02").decode()
        for name in ("response.output_audio.delta", "response.audio.delta"):
            evt = a._normalize({"type": name, "delta": raw})
            assert evt == {"type": "audio", "data": b"\x01\x02"}

    def test_normalize_transcripts(self):
        a = self._adapter()
        assert a._normalize({
            "type": "response.audio_transcript.delta", "delta": "He",
        }) == {"type": "transcript_agent", "text": "He", "final": False}
        assert a._normalize({
            "type": "response.output_audio_transcript.done",
            "transcript": "Hello.",
        }) == {"type": "transcript_agent", "text": "Hello.", "final": True}
        assert a._normalize({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "Hi agent",
        }) == {"type": "transcript_user", "text": "Hi agent", "final": True}

    def test_normalize_speech_started_tool_call_done_error(self):
        a = self._adapter()
        assert a._normalize({"type": "input_audio_buffer.speech_started"}) == \
            {"type": "speech_started"}
        tc = a._normalize({"type": "response.function_call_arguments.done",
                           "call_id": "c1", "name": "read",
                           "arguments": "{}"})
        assert tc == {"type": "tool_call", "call_id": "c1", "name": "read",
                      "arguments": "{}"}
        done = a._normalize({"type": "response.done",
                             "response": {"usage": {"total_tokens": 5}}})
        assert done == {"type": "response_done",
                        "usage": {"total_tokens": 5}}
        err = a._normalize({"type": "error",
                            "error": {"message": "boom", "code": "x"}})
        assert err == {"type": "error", "message": "boom", "fatal": True}
        benign = a._normalize({"type": "error", "error": {
            "message": "no active response",
            "code": "response_cancel_not_active"}})
        assert benign["fatal"] is False

    def test_response_create_deferred_while_response_active(self):
        """A response.create fired while a response is active is rejected by
        the provider and lost (regression: after a fast tool call the agent
        never spoke the result — the live session went silent, the Telegram
        turn hung to its 120s timeout). The adapter must defer the create
        until the active response's `response.done`, then send it."""
        a = self._adapter()
        sent = []

        class _WS:
            def send_text(self, t):
                sent.append(json.loads(t))

        a._ws = _WS()
        # No response active → create goes out immediately.
        a.commit_input()
        assert [m["type"] for m in sent] == ["input_audio_buffer.commit",
                                             "response.create"]
        sent.clear()
        # Response active → the tool result's create is deferred...
        a._normalize({"type": "response.created"})
        a.send_tool_result("c1", "42")
        assert [m["type"] for m in sent] == ["conversation.item.create"]
        # ...and flushed exactly once when the active response finishes.
        a._normalize({"type": "response.done", "response": {"usage": {}}})
        assert [m["type"] for m in sent] == ["conversation.item.create",
                                             "response.create"]
        a._normalize({"type": "response.done", "response": {"usage": {}}})
        assert [m["type"] for m in sent].count("response.create") == 1

    def test_normalize_active_response_collision_is_benign(self):
        """send_tool_result/inject_context each fire response.create; when
        one collides with a still-active response the provider rejects it —
        that race must NOT kill the session (regression: treated as fatal,
        a fast tool result could tear down a live conversation)."""
        a = self._adapter()
        evt = a._normalize({"type": "error", "error": {
            "message": "already has an active response",
            "code": "conversation_already_has_active_response"}})
        assert evt["fatal"] is False

    def test_normalize_ignores_unknown_events(self):
        a = self._adapter()
        assert a._normalize({"type": "session.created"}) is None
        assert a._normalize({"type": "rate_limits.updated"}) is None

    def test_build_adapter_unknown_protocol(self):
        with pytest.raises(ValueError):
            build_adapter("quantum_voice", base_url="", api_key="")


# ── WS client against a local fake server ───────────────────────────

class TestRealtimeWSClient:

    def _fake_server(self, received):
        """Minimal RFC 6455 server: handshake, echo one event, read frames."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        ready = threading.Event()

        def run():
            ready.set()
            conn, _ = srv.accept()
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            received["handshake"] = data.decode("latin-1")
            import hashlib
            key = ""
            for line in data.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(),
                usedforsecurity=False).digest()).decode()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            # Server → client: one unmasked text frame.
            payload = json.dumps({"type": "session.created"}).encode()
            conn.sendall(bytes([0x81, len(payload)]) + payload)
            # Client → server: read one masked frame.
            hdr = conn.recv(2)
            length = hdr[1] & 0x7F
            assert hdr[1] & 0x80, "client frames must be masked"
            mask = conn.recv(4)
            body = b""
            while len(body) < length:
                body += conn.recv(length - len(body))
            received["frame"] = bytes(
                b ^ mask[i % 4] for i, b in enumerate(body))
            conn.close()
            srv.close()

        threading.Thread(target=run, daemon=True).start()
        ready.wait(2)
        return port

    def test_handshake_frames_and_masking(self):
        received = {}
        port = self._fake_server(received)
        client = RealtimeWSClient(
            f"ws://127.0.0.1:{port}/v1/realtime?model=m",
            {"Authorization": "Bearer sk-test"}).connect()
        assert "Authorization: Bearer sk-test" in received["handshake"]
        opcode, payload = client.recv_frame(timeout=5)
        assert opcode == 0x1
        assert json.loads(payload) == {"type": "session.created"}
        client.send_text('{"type":"ping"}')
        for _ in range(50):
            if "frame" in received:
                break
            time.sleep(0.05)
        assert received["frame"] == b'{"type":"ping"}'
        client.close()


# ── frame parser resilience ──────────────────────────────────────────

class _JitterySock:
    """Delivers scripted byte bursts; raises socket.timeout when drained."""

    def __init__(self, bursts):
        self._bursts = list(bursts)

    def settimeout(self, t):
        pass

    def recv(self, n):
        if not self._bursts:
            raise socket.timeout()
        return self._bursts.pop(0)


def test_recv_frame_survives_timeout_mid_frame():
    """A timeout between the header and the payload must NOT desync the
    stream (regression: the consumed header bytes were lost, so the next
    read parsed payload bytes as a header)."""
    payload = json.dumps({"type": "session.created"}).encode()
    frame = bytes([0x81, len(payload)]) + payload
    client = RealtimeWSClient("ws://x/", {})
    client._sock = _JitterySock([frame[:2]])  # header only, then timeout
    client._rxbuf = bytearray()
    with pytest.raises(socket.timeout):
        client.recv_frame(timeout=0.01)
    # Rest of the frame arrives — the parse must resume cleanly.
    client._sock._bursts.append(frame[2:])
    opcode, got = client.recv_frame(timeout=0.01)
    assert opcode == 0x1
    assert got == payload


def test_recv_frame_reassembles_split_extended_frame():
    payload = b"x" * 300  # forces the 126 extended-length header
    frame = bytes([0x82, 126]) + struct.pack("!H", 300) + payload
    client = RealtimeWSClient("ws://x/", {})
    client._sock = _JitterySock([frame[:3]])  # split inside the ext length
    client._rxbuf = bytearray()
    with pytest.raises(socket.timeout):
        client.recv_frame(timeout=0.01)
    client._sock._bursts.extend([frame[3:150], frame[150:]])
    opcode, got = client.recv_frame(timeout=0.01)
    assert opcode == 0x2
    assert got == payload


def test_recv_frame_reassembles_fragmented_message():
    """FIN=0 fragments + continuation frames form ONE message (RFC 6455
    §5.4), and an interleaved ping passes through without breaking the
    assembly (regression: fragments were returned as broken standalone
    frames, so a fragmented provider event was silently dropped)."""
    frag1 = bytes([0x01, 3]) + b"hel"   # text, FIN=0
    ping = bytes([0x89, 1]) + b"p"      # control frame, interleaved
    frag2 = bytes([0x80, 2]) + b"lo"    # continuation, FIN=1
    client = RealtimeWSClient("ws://x/", {})
    client._sock = _JitterySock([frag1 + ping + frag2])
    client._rxbuf = bytearray()
    assert client.recv_frame(timeout=0.01) == (0x9, b"p")
    assert client.recv_frame(timeout=0.01) == (0x1, b"hello")


def test_connect_closes_socket_on_rejected_handshake(monkeypatch):
    """A non-101 handshake raises out of connect() — the TCP socket has no
    owner then and must be closed, not leaked until GC."""
    class _FakeSock:
        closed = False

        def setsockopt(self, *a):
            pass

        def sendall(self, data):
            pass

        def recv(self, n):
            return b"HTTP/1.1 403 Forbidden\r\n\r\n"

        def close(self):
            self.closed = True

    fake = _FakeSock()
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **kw: fake)
    client = RealtimeWSClient("ws://x/realtime", {})
    with pytest.raises(ConnectionError):
        client.connect(timeout=0.1)
    assert fake.closed
    assert client._sock is None


def test_recv_frame_rejects_insane_frame_length():
    """A corrupted/hostile 64-bit length field must raise instead of
    buffering gigabytes waiting for a payload that never comes."""
    frame = bytes([0x82, 127]) + struct.pack("!Q", 1 << 40)
    client = RealtimeWSClient("ws://x/", {})
    client._sock = _JitterySock([frame])
    client._rxbuf = bytearray()
    with pytest.raises(ConnectionError):
        client.recv_frame(timeout=0.01)


def test_browser_ws_recv_rejects_insane_frame_length():
    """Same guard on the browser leg: an authenticated client claiming a
    huge frame is treated as a disconnect, not buffered."""
    from services.audio_proxy import _ws_recv
    server_sock, client_sock = socket.socketpair()
    try:
        client_sock.sendall(bytes([0x82, 0x80 | 127])
                            + struct.pack("!Q", 1 << 40))
        opcode, payload = _ws_recv(server_sock)
        assert opcode is None and payload == b""
    finally:
        server_sock.close()
        client_sock.close()


def _masked_client_frame(first_byte, payload):
    mask = b"\x01\x02\x03\x04"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes([first_byte, 0x80 | len(payload)]) + mask + masked


def test_browser_ws_recv_reassembles_fragmented_message():
    """Browsers fragment large sends (RFC 6455 §5.4): FIN=0 fragments plus
    continuation frames must come back as ONE message, with an interleaved
    ping dropped (regression: fragments were returned as broken standalone
    frames — truncated audio, unparseable control JSON)."""
    from services.audio_proxy import _ws_recv
    server_sock, client_sock = socket.socketpair()
    try:
        client_sock.sendall(
            _masked_client_frame(0x02, b"hel")      # binary, FIN=0
            + _masked_client_frame(0x89, b"p")      # interleaved ping
            + _masked_client_frame(0x80, b"lo"))    # continuation, FIN=1
        assert _ws_recv(server_sock) == (0x2, b"hello")
    finally:
        server_sock.close()
        client_sock.close()


# ── service config validation ────────────────────────────────────────

class TestRealtimeVoiceService:

    def test_requires_llm_service_and_model(self):
        svc = RealtimeVoiceConnectionService({"model": "gpt-realtime"})
        with pytest.raises(ServiceError):
            svc._create_connection()
        svc = RealtimeVoiceConnectionService({"llm_service": "llm"})
        with pytest.raises(ServiceError):
            svc._create_connection()

    def test_rejects_unknown_protocol(self):
        svc = RealtimeVoiceConnectionService({
            "llm_service": "llm", "model": "m", "protocol": "nope"})
        with pytest.raises(ServiceError):
            svc._create_connection()

    def test_valid_config_and_schema(self):
        svc = RealtimeVoiceConnectionService({
            "llm_service": "llm", "model": "gpt-realtime"})
        assert svc._create_connection() == {"ready": True}
        schema = svc.get_parameter_schema()
        for key in ("llm_service", "protocol", "model", "voice", "vad",
                    "instructions_mode", "max_session_seconds"):
            assert key in schema
        assert svc.TYPE == "realtimeVoiceConnection"

    def test_registered_in_service_factory(self):
        from core import ServiceFactory
        import tasks  # noqa: F401 — triggers _register_all_services
        assert ServiceFactory.get("realtimeVoiceConnection") \
            is RealtimeVoiceConnectionService

    def test_open_session_uses_explicit_identity_not_runtime_state(
            self, monkeypatch):
        """Registry service instances are SHARED: a concurrent session's
        set_runtime_context can overwrite the runtime fields between this
        session's set_runtime_context and open_session (regression: the
        llmConnection then resolved in the OTHER user's scope — wrong
        user-scoped API key). Identity passed to open_session must win."""
        seen = {}

        class _LLM:
            provider = "openai"
            api_key = "sk-a"
            base_url = "https://api.openai.com/v1"

        class _Def:
            service_type = "llmConnection"

        class _Reg:
            def resolve_definition(self, sid, *, user_id="", conv_id=""):
                seen["definition"] = (user_id, conv_id)
                return _Def()

            def resolve(self, sid, *, user_id="", conv_id=""):
                seen["resolve"] = (user_id, conv_id)
                return _LLM()

        monkeypatch.setattr(
            "core.service_registry.ServiceRegistry.get_instance",
            staticmethod(lambda: _Reg()))

        class _Adapter:
            def connect(self, **kw):
                pass

        monkeypatch.setattr("services.realtime_voice_service.build_adapter",
                            lambda *a, **kw: _Adapter())
        svc = RealtimeVoiceConnectionService(
            {"llm_service": "llm", "model": "gpt-realtime"})
        svc.set_runtime_context(user_id="alice", conversation_id="conv-a")
        # A concurrent session on the shared instance clobbers the fields.
        svc.set_runtime_context(user_id="bob", conversation_id="conv-b")
        svc.open_session(user_id="alice", conversation_id="conv-a")
        assert seen["definition"] == ("alice", "conv-a")
        assert seen["resolve"] == ("alice", "conv-a")
        # Legacy callers without explicit identity still get the fields.
        svc.open_session()
        assert seen["resolve"] == ("bob", "conv-b")


# ── bridge over a socketpair with a scripted adapter ─────────────────

class _FakeAdapter:
    def __init__(self, events):
        self._events = queue.Queue()
        for e in events:
            self._events.put(e)
        self.sent_audio = []
        self.interrupts = 0
        self.commits = 0
        self.tool_results = []
        self.closed = False

    def send_audio(self, chunk):
        self.sent_audio.append(bytes(chunk))

    def commit_input(self):
        self.commits += 1

    def send_tool_result(self, call_id, result):
        self.tool_results.append((call_id, result))

    def interrupt(self):
        self.interrupts += 1

    def recv_event(self, timeout=1.0):
        try:
            return self._events.get(timeout=min(timeout, 0.1))
        except queue.Empty:
            return None

    def close(self):
        self.closed = True


class _FakeService:
    instructions_mode = "custom"
    instructions = "Be brief."
    max_session_seconds = 30
    tool_profile = ""

    def __init__(self, adapter):
        self._adapter = adapter
        self.opened_with = None
        self.opened_tools = None

    def open_session(self, instructions="", tools=None, vad="", **kw):
        self.opened_with = instructions
        self.opened_tools = tools
        self.opened_identity = (kw.get("user_id"), kw.get("conversation_id"))
        return self._adapter


class TestRealtimeSessionBridge:

    def _run_bridge(self, events, persisted):
        server_sock, client_sock = socket.socketpair()
        adapter = _FakeAdapter(events)
        service = _FakeService(adapter)
        bridge = RealtimeSessionBridge(server_sock, "conv1", "claude",
                                       "quentin", service)
        bridge._persist = lambda role, text: persisted.append((role, text))
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        return bridge, adapter, service, client_sock, thread

    def _read_until(self, sock, want_type, limit=30):
        """Read frames until a JSON control of `want_type`; collect audio."""
        audio = []
        for _ in range(limit):
            opcode, payload = _client_recv_frame(sock)
            if opcode is None:
                break
            if opcode == 0x2:
                audio.append(payload)
                continue
            if opcode == 0x1:
                msg = json.loads(payload)
                if msg.get("type") == want_type:
                    return msg, audio
        raise AssertionError(f"never received {want_type}")

    def test_full_session_flow(self):
        persisted = []
        events = [
            {"type": "transcript_user", "text": "hello", "final": True},
            {"type": "audio", "data": b"\x00\x01\x02\x03"},
            {"type": "transcript_agent", "text": "hi ", "final": False},
            {"type": "transcript_agent", "text": "hi there", "final": True},
            {"type": "response_done", "usage": {"total_tokens": 7}},
        ]
        bridge, adapter, service, client, thread = self._run_bridge(
            events, persisted)
        try:
            msg, _ = self._read_until(client, "ready")
            assert msg["state"] == "listening"
            # The client shows the manual push-to-talk control from this.
            assert msg["vad"] == "server"
            assert service.opened_with == "Be brief."
            # mic uplink → adapter
            _client_send_frame(client, 0x2, b"\x11\x22")
            usage, audio = self._read_until(client, "usage")
            assert usage["usage"] == {"total_tokens": 7}
            assert b"\x00\x01\x02\x03" in audio
            for _ in range(50):
                if adapter.sent_audio:
                    break
                time.sleep(0.05)
            assert adapter.sent_audio == [b"\x11\x22"]
            # transcripts persisted with roles
            assert ("user", "hello") in persisted
            assert ("assistant", "hi there") in persisted
            # stop control → closed + adapter closed
            _client_send_frame(client, 0x1, b'{"type": "stop"}')
            closed, _ = self._read_until(client, "closed")
            assert closed["reason"] == "client_stop"
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert adapter.closed
        finally:
            client.close()

    def test_ready_reports_manual_vad(self):
        """A manual-VAD service must be announced in `ready` so the client
        shows the push-to-talk Send control (otherwise the session is mute
        — nothing ever commits the audio buffer)."""
        server_sock, client_sock = socket.socketpair()
        adapter = _FakeAdapter([])
        service = _FakeService(adapter)
        service.vad = "manual"
        bridge = RealtimeSessionBridge(server_sock, "conv1", "claude",
                                       "quentin", service)
        bridge._persist = lambda role, text: None
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        try:
            msg, _ = self._read_until(client_sock, "ready")
            assert msg["vad"] == "manual"
            _client_send_frame(client_sock, 0x1, b'{"type": "stop"}')
            self._read_until(client_sock, "closed")
            thread.join(timeout=5)
        finally:
            client_sock.close()

    def test_late_user_transcript_keeps_question_answer_order(self):
        """The whisper user transcript usually lands AFTER the agent
        transcript of the same turn (regression: persisted in arrival
        order, the conversation history read answer-before-question)."""
        persisted = []
        events = [
            {"type": "speech_started"},
            {"type": "transcript_agent", "text": "il fait beau",
             "final": True},
            {"type": "transcript_user", "text": "quel temps ?",
             "final": True},
            {"type": "response_done", "usage": {}},
        ]
        bridge, adapter, service, client, thread = self._run_bridge(
            events, persisted)
        try:
            self._read_until(client, "usage")
            for _ in range(50):
                if len(persisted) >= 2:
                    break
                time.sleep(0.05)
            assert persisted == [("user", "quel temps ?"),
                                 ("assistant", "il fait beau")]
            _client_send_frame(client, 0x1, b'{"type": "stop"}')
            self._read_until(client, "closed")
            thread.join(timeout=5)
        finally:
            client.close()

    def test_held_assistant_transcript_persists_when_user_never_arrives(
            self, monkeypatch):
        """If whisper never delivers the user transcript, the held assistant
        final must persist after the grace window instead of never."""
        import services._realtime_bridge as rb
        monkeypatch.setattr(rb, "_ASSISTANT_ORDER_GRACE_S", 0.2)
        persisted = []
        events = [
            {"type": "speech_started"},
            {"type": "transcript_agent", "text": "hello", "final": True},
        ]
        bridge, adapter, service, client, thread = self._run_bridge(
            events, persisted)
        try:
            self._read_until(client, "speech_started")
            for _ in range(60):
                if persisted:
                    break
                time.sleep(0.05)
            assert persisted == [("assistant", "hello")]
            _client_send_frame(client, 0x1, b'{"type": "stop"}')
            self._read_until(client, "closed")
            thread.join(timeout=5)
        finally:
            client.close()

    def test_barge_in_interrupts_and_notifies(self):
        persisted = []
        events = [{"type": "speech_started"}]
        bridge, adapter, service, client, thread = self._run_bridge(
            events, persisted)
        try:
            self._read_until(client, "ready")
            self._read_until(client, "speech_started")
            assert adapter.interrupts >= 1
            _client_send_frame(client, 0x1, b'{"type": "stop"}')
            self._read_until(client, "closed")
            thread.join(timeout=5)
        finally:
            client.close()

    def test_fatal_provider_error_closes_session(self):
        persisted = []
        events = [{"type": "error", "message": "quota", "fatal": True}]
        bridge, adapter, service, client, thread = self._run_bridge(
            events, persisted)
        try:
            self._read_until(client, "ready")
            err, _ = self._read_until(client, "error")
            assert err["message"] == "quota"
            # The bridge tears down; the client socket sees the close.
            client.settimeout(5)
            # drain until socket closes
            for _ in range(20):
                opcode, _p = _client_recv_frame(client)
                if opcode is None or opcode == 0x8:
                    break
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert adapter.closed
        finally:
            client.close()

    def test_tool_call_executes_through_tool_bridge(self, monkeypatch):
        """tool_profile set → provider tool_call runs through the bridge:
        definitions sent at open, result returned, UI status events."""
        class _FakeToolBridge:
            def __init__(self, profile, cid, agent, user):
                assert profile == "echo"

            def tool_definitions(self):
                return [{"type": "function", "name": "echo",
                         "description": "d", "parameters": {}}]

            def handle_call(self, call_id, name, arguments, *,
                            send_result, announce=None, **kw):
                send_result(call_id, f"echoed:{name}")
                return "done"

        import services._realtime_tools as rt_tools
        monkeypatch.setattr(rt_tools, "RealtimeToolBridge", _FakeToolBridge)
        persisted = []
        events = [{"type": "tool_call", "call_id": "c7", "name": "echo",
                   "arguments": "{}"}]
        server_sock, client = socket.socketpair()
        adapter = _FakeAdapter(events)
        service = _FakeService(adapter)
        service.tool_profile = "echo"
        bridge = RealtimeSessionBridge(server_sock, "conv1", "claude",
                                       "quentin", service)
        bridge._persist = lambda role, text: persisted.append((role, text))
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        try:
            self._read_until(client, "ready")
            assert service.opened_tools and \
                service.opened_tools[0]["name"] == "echo"
            for _ in range(50):
                if adapter.tool_results:
                    break
                time.sleep(0.05)
            assert adapter.tool_results == [("c7", "echoed:echo")]
            # UI got running → done status events
            done_evt, _ = self._read_until(client, "tool")
            assert done_evt["name"] == "echo"
            _client_send_frame(client, 0x1, b'{"type": "stop"}')
            self._read_until(client, "closed")
            thread.join(timeout=5)
        finally:
            client.close()

    def test_late_tool_result_persists_as_system_after_session_end(self):
        """A delegated tool finishing after teardown must not be lost."""
        persisted = []
        bridge, adapter, service, client, thread = self._run_bridge(
            [], persisted)
        try:
            self._read_until(client, "ready")
            _client_send_frame(client, 0x1, b'{"type": "stop"}')
            self._read_until(client, "closed")
            thread.join(timeout=5)
            bridge._announce_tool_result("bash finished: ok")
            assert ("system", "bash finished: ok") in persisted
        finally:
            client.close()

    def test_session_cap_enforced_with_silent_client(self):
        """max_session_seconds must fire even when the browser sends nothing
        (muted mic): the handler thread blocks in _ws_recv, so the deadline
        lives in the provider pump."""
        persisted = []
        server_sock, client = socket.socketpair()
        adapter = _FakeAdapter([])
        service = _FakeService(adapter)
        service.max_session_seconds = 1
        bridge = RealtimeSessionBridge(server_sock, "conv1", "claude",
                                       "quentin", service)
        bridge._persist = lambda role, text: persisted.append((role, text))
        thread = threading.Thread(target=bridge.run, daemon=True)
        thread.start()
        try:
            self._read_until(client, "ready")
            # Send NOTHING — just wait for the cap to close the session.
            closed, _ = self._read_until(client, "closed", limit=10)
            assert closed["reason"] == "max_session_seconds"
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert adapter.closed
        finally:
            client.close()

    def test_force_stop_wired_into_cancel_action(self):
        """The conversation force-stop path must kill the voice session."""
        from pathlib import Path
        src = Path("tasks/ai/actions/cancel_interrupt.py").read_text(
            encoding="utf-8")
        assert "stop_realtime_session" in src

    def test_p1_refuses_stray_tool_calls(self):
        persisted = []
        events = [{"type": "tool_call", "call_id": "c9", "name": "bash",
                   "arguments": "{}"}]
        bridge, adapter, service, client, thread = self._run_bridge(
            events, persisted)
        try:
            self._read_until(client, "ready")
            for _ in range(50):
                if adapter.tool_results:
                    break
                time.sleep(0.05)
            assert adapter.tool_results
            assert adapter.tool_results[0][0] == "c9"
            assert "not enabled" in adapter.tool_results[0][1]
            _client_send_frame(client, 0x1, b'{"type": "stop"}')
            self._read_until(client, "closed")
            thread.join(timeout=5)
        finally:
            client.close()


# ── route + registry ─────────────────────────────────────────────────

class _FakeHttpService:
    def __init__(self):
        self.routes = []

    def get_routes(self):
        return [{"pattern": p} for (_m, p) in self.routes]

    def register_route(self, method, pattern, owner, callback=None,
                       ws_handler=None, **kw):
        assert ws_handler is not None
        self.routes.append((method, pattern))


def test_register_realtime_route_is_idempotent():
    svc = _FakeHttpService()
    register_realtime_route(svc)
    register_realtime_route(svc)
    assert svc.routes == [("GET", "/ws/realtime/{conversation_id}")]


def test_stop_realtime_session_without_active_returns_false():
    _active_bridges.clear()
    assert stop_realtime_session("nope") is False


def test_open_failure_does_not_leak_active_bridge(monkeypatch):
    """open_session failure exits run() without _teardown — the handler
    must still deregister the bridge, or stop_realtime_session would
    report killing a session that is already dead."""
    import services._realtime_bridge as rb
    monkeypatch.setattr("core.flow_runtime_access.conversation_owner",
                        lambda cid: "alice")

    class _Svc:
        max_session_seconds = 5
        tool_profile = ""

        def set_runtime_context(self, **kw):
            pass

        def open_session(self, **kw):
            raise RuntimeError("provider down")

    class _Def:
        service_type = "realtimeVoiceConnection"

    class _Reg:
        def resolve_definition(self, sid, **kw):
            return _Def()

        def resolve(self, sid, **kw):
            return _Svc()

    monkeypatch.setattr("core.service_registry.ServiceRegistry.get_instance",
                        staticmethod(lambda: _Reg()))
    server_sock, client_sock = socket.socketpair()
    try:
        rb.realtime_ws_handler(
            server_sock, {"conversation_id": "conv-openfail"},
            {"query": "service=rt&agent=claude",
             "auth_user_id": "alice", "auth_role": "user"})
        with rb._bridges_lock:
            assert "conv-openfail" not in rb._active_bridges
        assert rb.stop_realtime_session("conv-openfail") is False
    finally:
        server_sock.close()
        client_sock.close()


def test_ws_handler_rejects_identityless_caller(monkeypatch):
    """API-key / internal-auth WS connections carry no auth_user_id; a voice
    session must refuse them even on an ownerless conversation."""
    import services._realtime_bridge as rb
    monkeypatch.setattr("core.flow_runtime_access.conversation_owner",
                        lambda cid: "")  # ownerless/legacy conversation
    server_sock, client_sock = socket.socketpair()
    try:
        handler = threading.Thread(
            target=rb.realtime_ws_handler,
            args=(server_sock, {"conversation_id": "conv1"},
                  {"query": "service=rt&agent=claude",
                   "auth_user_id": "", "auth_role": ""}),
            daemon=True)
        handler.start()
        opcode, payload = _client_recv_frame(client_sock)
        assert opcode == 0x1
        msg = json.loads(payload)
        assert msg["type"] == "error"
        assert "user session" in msg["message"]
        handler.join(timeout=5)
        assert not handler.is_alive()
    finally:
        client_sock.close()


def test_ws_handler_rejects_foreign_conversation(monkeypatch):
    import services._realtime_bridge as rb
    monkeypatch.setattr("core.flow_runtime_access.conversation_owner",
                        lambda cid: "alice")
    server_sock, client_sock = socket.socketpair()
    try:
        handler = threading.Thread(
            target=rb.realtime_ws_handler,
            args=(server_sock, {"conversation_id": "conv1"},
                  {"query": "service=rt&agent=claude",
                   "auth_user_id": "mallory", "auth_role": "user"}),
            daemon=True)
        handler.start()
        opcode, payload = _client_recv_frame(client_sock)
        assert opcode == 0x1
        assert json.loads(payload)["type"] == "error"
        assert "not your conversation" in json.loads(payload)["message"]
        handler.join(timeout=5)
        assert not handler.is_alive()
    finally:
        client_sock.close()


# ── UI wiring (static introspection, house pattern) ─────────────────

def test_chat_ui_voice_mode_is_wired():
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
    assert 'id="voiceModeBtn"' in template
    assert 'onclick="toggleVoiceMode()"' in template

    serve = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    assert '"conversation_voice.js",' in serve

    js = Path("tasks/io/chat_ui/conversation_voice.js").read_text(encoding="utf-8")
    assert "function toggleVoiceMode()" in js
    assert "'/ws/realtime/' + encodeURIComponent(cid)" in js
    assert "list_realtime_services" in js
    assert "_voiceFlushPlayback" in js  # barge-in path
    # P2b voice-mode overlay + linked-agent treatment
    assert "_voiceShowOverlay" in js
    assert "_voiceToggleMute" in js
    assert "_voiceToolActivity" in js
    assert "_voiceLinkedService" in js
    assert "if (_voiceStarting) return;" in js  # double-start guard
    # Partial capture failure (post-getUserMedia) must release the mic.
    assert js.index("_voiceStopCapture(); //") < js.index("voiceMicDenied")
    # Manual-VAD push-to-talk: send button wired to the commit control.
    assert 'id="voiceCommitBtn"' in js
    assert "{ type: 'commit' }" in js
    assert "msg.vad === 'manual'" in js
    # Service picker is a clickable list, not a prompt().
    assert "prompt(" not in js
    assert 'voiceServicePick' in js

    for lang in ("en", "fr", "es"):
        data = json.loads(Path(f"tasks/io/chat_ui/i18n/{lang}.json")
                          .read_text(encoding="utf-8"))
        assert "voiceModeStartTitle" in data
        assert "voiceStateListening" in data
        assert "realtimeVoiceService" in data
        assert "voiceSendTurn" in data  # manual-VAD send button


def test_agent_editor_exposes_realtime_voice_link():
    menus = Path("tasks/io/chat_ui/resources_menus.js").read_text(encoding="utf-8")
    assert "acc-rtvoice" in menus
    assert "realtime_voice_service" in menus


# ── voice-native agent link (P2b) ────────────────────────────────────

def test_agent_config_carries_realtime_voice_service():
    from core.conv_agent_config import AGENT_CONFIG_DEFAULTS
    assert AGENT_CONFIG_DEFAULTS.get("realtime_voice_service") == ""


def test_update_agent_conv_config_accepts_realtime_voice_service():
    src = Path("tasks/ai/actions/_agentres_k5.py").read_text(encoding="utf-8")
    assert '"realtime_voice_service"' in src


def test_get_agent_config_returns_link(monkeypatch):
    import core.conv_agent_config as cac
    monkeypatch.setattr(
        cac, "get_all_agent_configs",
        lambda cid: {"claude": {"llm_service": "llm",
                                "realtime_voice_service": "rt-voice"}})
    cfg = cac.get_agent_config("conv1", "claude")
    assert cfg["realtime_voice_service"] == "rt-voice"
    # absent → default empty, never KeyError
    monkeypatch.setattr(cac, "get_all_agent_configs", lambda cid: {})
    assert cac.get_agent_config("conv1", "claude")[
        "realtime_voice_service"] == ""
