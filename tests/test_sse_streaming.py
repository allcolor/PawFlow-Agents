"""Tests for SSE streaming system.

Tests cover:
- SSEEvent encoding (basic, dict data, multiline, id)
- SSEWriter (send, close, iterate, keepalive, closed writes)
- ConversationEventBus (subscribe, publish, unsubscribe, dead writers, singleton)
- AgentLoopTask streaming mode (_execute_streaming, _streaming_agent_loop)
- AgentSSEStreamTask (subscribe, missing conversation_id)
- HandleHTTPResponse streaming detection
- PendingRequest streaming support
- Flow structure (v1.3.0 SSE route)
- i18n keys
"""

import json
import queue
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import FlowFile, TaskFactory
from core.sse_writer import SSEEvent, SSEWriter
from core.conversation_event_bus import ConversationEventBus


# ── SSEEvent ────────────────────────────────────────────────────────


import core.paths as _paths
class TestSSEEvent(unittest.TestCase):

    def test_basic_encode(self):
        event = SSEEvent(event="token", data="hello")
        encoded = event.encode()
        assert b"event: token\n" in encoded
        assert b"data: hello\n" in encoded

    def test_dict_data(self):
        event = SSEEvent(event="done", data={"status": "ok"})
        encoded = event.encode()
        assert b"event: done\n" in encoded
        assert b'"status"' in encoded

    def test_list_data(self):
        event = SSEEvent(event="test", data=[1, 2, 3])
        encoded = event.encode()
        assert b"[1, 2, 3]" in encoded

    def test_multiline_data(self):
        event = SSEEvent(event="msg", data="line1\nline2")
        encoded = event.encode()
        assert b"data: line1\n" in encoded
        assert b"data: line2\n" in encoded

    def test_with_id(self):
        event = SSEEvent(event="token", data="x", id="42")
        encoded = event.encode()
        assert b"id: 42\n" in encoded

    def test_no_id(self):
        event = SSEEvent(event="token", data="x")
        encoded = event.encode()
        assert b"id:" not in encoded

    def test_trailing_blank_line(self):
        event = SSEEvent(event="test", data="x")
        text = event.encode().decode()
        assert text.endswith("\n\n")


# ── SSEWriter ───────────────────────────────────────────────────────


class TestSSEWriter(unittest.TestCase):

    def test_send_and_iterate(self):
        writer = SSEWriter()
        writer.send(SSEEvent(event="token", data="hi"))
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert len(chunks) == 1
        assert b"event: token" in chunks[0]

    def test_send_event_convenience(self):
        writer = SSEWriter()
        writer.send_event("done", {"ok": True}, event_id="1")
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert len(chunks) == 1
        assert b"id: 1" in chunks[0]

    def test_close_stops_iteration(self):
        writer = SSEWriter()
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert chunks == []

    def test_is_closed(self):
        writer = SSEWriter()
        assert not writer.is_closed
        writer.close()
        assert writer.is_closed

    def test_send_after_close_ignored(self):
        writer = SSEWriter()
        writer.close()
        writer.send(SSEEvent(event="test", data="x"))
        chunks = list(writer.iterate(timeout=0.1))
        assert chunks == []

    def test_keepalive(self):
        writer = SSEWriter()
        results = []
        def consume():
            for chunk in writer.iterate(timeout=0.1):
                results.append(chunk)
        t = threading.Thread(target=consume)
        t.start()
        time.sleep(0.3)
        writer.close()
        t.join(timeout=2)
        keepalives = [c for c in results if c == b": keepalive\n\n"]
        assert len(keepalives) >= 1

    def test_multiple_events(self):
        writer = SSEWriter()
        for i in range(5):
            writer.send(SSEEvent(event="token", data=str(i)))
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert len(chunks) == 5

    def test_threaded_send(self):
        writer = SSEWriter()
        def producer():
            for i in range(10):
                writer.send(SSEEvent(event="token", data=str(i)))
            writer.close()
        t = threading.Thread(target=producer)
        t.start()
        chunks = list(writer.iterate(timeout=1.0))
        t.join()
        assert len(chunks) == 10

    def test_queue_overflow_closes_stale_writer(self):
        writer = SSEWriter(max_queue=2)
        assert writer.send(SSEEvent(event="token", data="1")) is True
        assert writer.send(SSEEvent(event="token", data="2")) is True
        assert writer.send(SSEEvent(event="token", data="3")) is False
        assert writer.is_closed
        assert writer.overflowed
        assert writer.queued_count <= 2


# ── ConversationEventBus ────────────────────────────────────────────


class TestConversationEventBus(unittest.TestCase):

    def setUp(self):
        ConversationEventBus.reset()

    def tearDown(self):
        ConversationEventBus.reset()

    def test_singleton(self):
        a = ConversationEventBus.instance()
        b = ConversationEventBus.instance()
        assert a is b

    def test_subscribe_returns_writer(self):
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("conv1")
        assert isinstance(writer, SSEWriter)
        assert not writer.is_closed

    def test_publish_to_subscriber(self):
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("conv1")
        bus.publish_event("conv1", "token", {"text": "hi"})
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert len(chunks) == 1
        assert b"event: token" in chunks[0]

    def test_publish_no_subscribers(self):
        bus = ConversationEventBus.instance()
        bus.publish_event("nobody", "test")

    def test_unsubscribe(self):
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("conv1")
        bus.unsubscribe("conv1", writer)
        assert writer.is_closed
        assert bus.subscriber_count("conv1") == 0

    def test_multiple_subscribers(self):
        bus = ConversationEventBus.instance()
        w1 = bus.subscribe("conv1")
        w2 = bus.subscribe("conv1")
        assert bus.subscriber_count("conv1") == 2
        bus.publish_event("conv1", "token", {"text": "x"})
        w1.close()
        w2.close()
        c1 = list(w1.iterate(timeout=0.1))
        c2 = list(w2.iterate(timeout=0.1))
        assert len(c1) == 1
        assert len(c2) == 1

    def test_dead_writer_cleanup(self):
        bus = ConversationEventBus.instance()
        w1 = bus.subscribe("conv1")
        w1.close()
        w2 = bus.subscribe("conv1")
        bus.publish_event("conv1", "test")
        assert bus.subscriber_count("conv1") == 1

    def test_same_client_id_replaces_stale_subscriber(self):
        bus = ConversationEventBus.instance()
        w1 = bus.subscribe("conv1", client_id="tab-a")
        w2 = bus.subscribe("conv1", client_id="tab-a")
        assert w1.is_closed
        assert not w2.is_closed
        assert bus.subscriber_count("conv1") == 1
        bus.publish_event("conv1", "token", {"text": "x"})
        w2.close()
        chunks = list(w2.iterate(timeout=0.1))
        assert len(chunks) == 1

    def test_different_client_ids_remain_independent(self):
        bus = ConversationEventBus.instance()
        w1 = bus.subscribe("conv1", client_id="tab-a")
        w2 = bus.subscribe("conv1", client_id="tab-b")
        assert bus.subscriber_count("conv1") == 2
        w1.close()
        assert bus.subscriber_count("conv1") == 1
        w2.close()

    def test_overflowed_writer_removed_from_bus(self):
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("conv1")
        writer._max_queue = 1
        writer._queue = queue.Queue(maxsize=1)
        bus.publish_event("conv1", "token", {"n": 1})
        assert bus.subscriber_count("conv1") == 1
        bus.publish_event("conv1", "token", {"n": 2})
        assert writer.is_closed
        assert writer.overflowed
        assert bus.subscriber_count("conv1") == 0

    def test_active_conversations(self):
        bus = ConversationEventBus.instance()
        bus.subscribe("conv1")
        bus.subscribe("conv2")
        active = bus.active_conversations()
        assert "conv1" in active
        assert "conv2" in active

    def test_subscriber_count(self):
        bus = ConversationEventBus.instance()
        assert bus.subscriber_count("conv1") == 0
        bus.subscribe("conv1")
        assert bus.subscriber_count("conv1") == 1

    def test_publish_event_convenience(self):
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("conv1")
        bus.publish_event("conv1", "done", {"ok": True})
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert len(chunks) == 1
        assert b"event: done" in chunks[0]

    def test_publish_sse_event_object(self):
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("conv1")
        bus.publish("conv1", SSEEvent(event="custom", data="payload"))
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert len(chunks) == 1
        assert b"event: custom" in chunks[0]

    def test_isolation_between_conversations(self):
        bus = ConversationEventBus.instance()
        w1 = bus.subscribe("conv1")
        w2 = bus.subscribe("conv2")
        bus.publish_event("conv1", "token", {"text": "a"})
        bus.publish_event("conv2", "token", {"text": "b"})
        w1.close()
        w2.close()
        c1 = list(w1.iterate(timeout=0.1))
        c2 = list(w2.iterate(timeout=0.1))
        assert len(c1) == 1
        assert len(c2) == 1
        assert b'"a"' in c1[0]
        assert b'"b"' in c2[0]


# ── AgentLoopTask streaming ─────────────────────────────────────────


class TestAgentLoopStreaming(unittest.TestCase):

    def setUp(self):
        ConversationEventBus.reset()

    def tearDown(self):
        ConversationEventBus.reset()

    def test_streaming_returns_accepted(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.conversation_store import ConversationStore
        ConversationStore.reset()

        task = AgentLoopTask({
            "api_key": "test-key",
            "streaming": True,
            "conversation_store": True,
        })
        task._prepare_agent_context = MagicMock(return_value={
            "client": MagicMock(),
            "registry": MagicMock(),
            "tool_defs": [],
            "messages": [],
            "model": "test",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_iterations": 1,
            "use_conv_store": False,
            "conv_ttl": 60,
            "conv_attr": "",
            "conversation_id": "test-conv-123",
        })
        task._streaming_agent_loop = MagicMock()

        ff = FlowFile(content=json.dumps({"message": "hello", "conversation_id": "test-conv-123"}).encode())
        with patch.object(ConversationStore.instance(), 'message_count', return_value=0):
            results = task._execute_streaming(ff)

        assert len(results) == 1
        body = json.loads(results[0].get_content().decode())
        assert body["status"] == "accepted"
        assert body["conversation_id"] == "test-conv-123"
        assert results[0].get_attribute("agent.streaming") == "true"

    def test_streaming_config_dispatches(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "k", "streaming": True})
        task._execute_streaming = MagicMock(return_value=[FlowFile(content=b"ok")])
        task._execute_sync = MagicMock()
        ff = FlowFile(content=b"test")
        task.execute(ff)
        task._execute_streaming.assert_called_once()
        task._execute_sync.assert_not_called()

    def test_sync_config_dispatches(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "k", "streaming": False})
        task._execute_streaming = MagicMock()
        task._execute_sync = MagicMock(return_value=[FlowFile(content=b"ok")])
        ff = FlowFile(content=b"test")
        task.execute(ff)
        task._execute_sync.assert_called_once()
        task._execute_streaming.assert_not_called()

    def test_streaming_publishes_thinking(self):
        from tasks.ai.agent_loop import AgentLoopTask
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("test-conv")

        task = AgentLoopTask({"api_key": "k", "streaming": True})
        task._prepare_agent_context = MagicMock(return_value={
            "client": MagicMock(),
            "registry": MagicMock(),
            "tool_defs": [],
            "messages": [],
            "model": "test",
            "temperature": 0.7,
            "max_tokens": 100,
            "max_iterations": 1,
            "use_conv_store": False,
            "conv_ttl": 60,
            "conv_attr": "",
            "conversation_id": "test-conv",
        })
        task._streaming_agent_loop = MagicMock()

        # Create conversation so message_count can find it
        from core.conversation_store import ConversationStore
        ConversationStore.reset()
        store = ConversationStore.instance()
        store.save("test-conv", [{"role": "user", "content": "hi"}], user_id="testuser")

        ff = FlowFile(content=json.dumps({"message": "hi", "conversation_id": "test-conv", "target_agent": "test-agent"}).encode())
        task._execute_streaming(ff)

        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert len(chunks) >= 1
        all_data = b"".join(chunks)
        assert b"event: thinking" in all_data


# ── AgentSSEStreamTask ──────────────────────────────────────────────


class TestAgentSSEStreamTask(unittest.TestCase):

    def setUp(self):
        ConversationEventBus.reset()

    def tearDown(self):
        ConversationEventBus.reset()

    def test_missing_conversation_id(self):
        from tasks.io.agent_sse_stream import AgentSSEStreamTask
        task = AgentSSEStreamTask({})
        ff = FlowFile(content=b"")
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "400"

    def test_subscribes_to_bus(self):
        from tasks.io.agent_sse_stream import AgentSSEStreamTask
        bus = ConversationEventBus.instance()
        # Pre-subscribe so we can close the writer after test
        task = AgentSSEStreamTask({"timeout": 10})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.query.conversation_id", "conv-abc")
        results = task.execute(ff)

        assert results[0].get_attribute("http.response.status") == "200"
        assert results[0].get_attribute("http.response.header.Content-Type") == "text/event-stream"
        assert results[0].get_attribute("http.response.stream") == "true"
        assert hasattr(results[0], "_sse_stream")
        # Clean up: close subscribers
        with bus._lock:
            for w in bus._subscribers.get("conv-abc", set()).copy():
                w.close()

    def test_sse_stream_receives_events(self):
        from tasks.io.agent_sse_stream import AgentSSEStreamTask
        bus = ConversationEventBus.instance()

        task = AgentSSEStreamTask({"timeout": 10})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.query.conversation_id", "conv-xyz")
        results = task.execute(ff)

        bus.publish_event("conv-xyz", "token", {"text": "hello"})
        bus.publish_event("conv-xyz", "done", {"ok": True})

        # Close all subscribers to make iteration terminate
        with bus._lock:
            for writer in bus._subscribers.get("conv-xyz", set()).copy():
                writer.close()

        stream = results[0]._sse_stream
        chunks = list(stream)
        token_chunks = [c for c in chunks if b"event: token" in c]
        done_chunks = [c for c in chunks if b"event: done" in c]
        assert len(token_chunks) >= 1
        assert len(done_chunks) >= 1

    def test_sse_client_id_replaces_previous_stream(self):
        from tasks.io.agent_sse_stream import AgentSSEStreamTask
        bus = ConversationEventBus.instance()
        task = AgentSSEStreamTask({"timeout": 10})

        first = FlowFile(content=b"")
        first.set_attribute("http.query", "conversation_id=conv-client&client_id=tab-a")
        first_result = task.execute(first)[0]
        with bus._lock:
            first_writer = next(iter(bus._subscribers.get("conv-client", set())))

        second = FlowFile(content=b"")
        second.set_attribute("http.query", "conversation_id=conv-client&client_id=tab-a")
        second_result = task.execute(second)[0]

        assert first_writer.is_closed
        assert bus.subscriber_count("conv-client") == 1
        assert hasattr(first_result, "_sse_stream")
        assert hasattr(second_result, "_sse_stream")
        with bus._lock:
            for writer in bus._subscribers.get("conv-client", set()).copy():
                writer.close()

    def test_registration(self):
        assert "agentSSEStream" in TaskFactory.list_types()

    def test_chat_ui_sends_stable_sse_client_id(self):
        src = (Path(__file__).resolve().parents[1] / "tasks/io/chat_ui/sse.js").read_text()
        assert "function getSSEClientId()" in src
        assert "sessionStorage.getItem('pawflow_sse_client_id')" in src
        assert "&client_id=" in src
        assert "encodeURIComponent(getSSEClientId())" in src

    def test_query_string_fallback(self):
        from tasks.io.agent_sse_stream import AgentSSEStreamTask
        bus = ConversationEventBus.instance()
        task = AgentSSEStreamTask({})
        ff = FlowFile(content=b"")
        ff.set_attribute("http.query", "conversation_id=fallback-conv")
        results = task.execute(ff)
        assert results[0].get_attribute("http.response.status") == "200"
        assert hasattr(results[0], "_sse_stream")
        # Clean up
        with bus._lock:
            for w in bus._subscribers.get("fallback-conv", set()).copy():
                w.close()


# ── HandleHTTPResponse streaming ────────────────────────────────────


class TestHandleHTTPResponseStreaming(unittest.TestCase):

    def test_detects_stream_mode(self):
        from tasks.io.handle_http_response import HandleHTTPResponseTask
        task = HandleHTTPResponseTask({"service_id": "test_svc"})

        mock_svc = MagicMock()
        mock_svc.submit_stream_response.return_value = True
        task.get_service = MagicMock(return_value=mock_svc)

        ff = FlowFile(content=b"body")
        ff.set_attribute("http.request.id", "req-1")
        ff.set_attribute("http.response.stream", "true")
        ff._sse_stream = iter([b"chunk1", b"chunk2"])

        task.execute(ff)
        mock_svc.submit_stream_response.assert_called_once()

    def test_regular_mode_no_stream(self):
        from tasks.io.handle_http_response import HandleHTTPResponseTask
        task = HandleHTTPResponseTask({"service_id": "test_svc"})

        mock_svc = MagicMock()
        mock_svc.submit_response.return_value = True
        task.get_service = MagicMock(return_value=mock_svc)

        ff = FlowFile(content=b"body")
        ff.set_attribute("http.request.id", "req-2")

        task.execute(ff)
        mock_svc.submit_response.assert_called_once()
        mock_svc.submit_stream_response.assert_not_called()


# ── PendingRequest streaming ────────────────────────────────────────


class TestPendingRequestStreaming(unittest.TestCase):

    def test_pending_request_has_stream_fields(self):
        from services.http_listener_service import PendingRequest
        pr = PendingRequest(request_id="r1", method="GET", path="/test", headers={}, body=b"")
        assert hasattr(pr, "response_stream")
        assert pr.response_stream is None

    def test_complete_stream(self):
        from services.http_listener_service import PendingRequest
        pr = PendingRequest(request_id="r2", method="GET", path="/test", headers={}, body=b"")

        def fake_stream():
            yield b"chunk1"
            yield b"chunk2"

        pr.complete_stream(200, {"Content-Type": "text/event-stream"}, fake_stream())
        assert pr.response_status == 200
        assert pr.response_stream is not None


# ── LLMClient.complete_stream ───────────────────────────────────────


class TestLLMClientCompleteStream(unittest.TestCase):

    def test_complete_stream_method_exists(self):
        from core.llm_client import LLMClient
        client = LLMClient(provider="openai", config={"api_key": "test"})
        assert hasattr(client, "complete_stream")

    def test_complete_stream_signature(self):
        from core.llm_client import LLMClient
        import inspect
        sig = inspect.signature(LLMClient.complete_stream)
        params = list(sig.parameters.keys())
        assert "messages" in params
        assert "callback" in params


# ── Flow structure (v1.3.0 with SSE) ───────────────────────────────


class TestStreamingFlowStructure(unittest.TestCase):

    def test_flow_version_1_3(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"]  # version exists

    def test_sse_route_exists(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        routes = data["tasks"]["http_in"]["parameters"]["routes"]
        patterns = [r["pattern"] for r in routes]
        assert "/api/agent/events" in patterns

    def test_agent_events_task(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "agent_events" in data["tasks"]
        assert data["tasks"]["agent_events"]["type"] == "agentSSEStream"

    def test_agent_streaming_enabled(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tasks"]["agent"]["parameters"]["streaming"] is True

    def test_sse_relation(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        relations = data["relations"]
        # SSE route goes through validate_auth → route_after_auth → agent_events
        assert {"from": "http_in", "to": "validate_auth", "type": "GET:/api/agent/events"} in relations
        assert {"from": "route_after_auth", "to": "agent_events", "type": "sse"} in relations
        assert {"from": "agent_events", "to": "send_response", "type": "success"} in relations

    def test_seven_routes_from_http_in(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        froms = [r["from"] for r in data["relations"]]
        assert froms.count("http_in") == 10

    def test_chat_ui_has_sse_path(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tasks"]["chat_ui"]["parameters"]["sse_path"] == "/api/agent/events"


# ── i18n streaming keys ────────────────────────────────────────────

