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
import tempfile
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

    def test_typed_ping_is_throttled_separately_from_keepalive(self):
        writer = SSEWriter()
        closer = threading.Timer(0.24, writer.close)
        closer.start()
        try:
            chunks = list(writer.iterate(timeout=0.05, ping_interval=0.15))
        finally:
            closer.cancel()
        keepalives = [c for c in chunks if c == b": keepalive\n\n"]
        typed_pings = [c for c in chunks if b"event: sse_ping" in c]
        assert len(keepalives) >= 2
        assert len(typed_pings) == 1


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

    def test_event_replayed_when_only_subscriber_dies_during_publish(self):
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("conv1", client_id="tab-a")
        writer._max_queue = 1
        writer._queue = queue.Queue(maxsize=1)

        bus.publish_event("conv1", "token", {"n": 1})
        bus.publish_event("conv1", "token", {"n": 2})

        replay = bus.subscribe("conv1", replay=True, client_id="tab-a")
        replay.close()
        chunks = list(replay.iterate(timeout=0.1))

        assert len(chunks) == 1
        assert b"event: token" in chunks[0]
        assert b'"n": 2' in chunks[0]

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

    def test_context_gauge_update_is_logged_with_formula(self):
        bus = ConversationEventBus.instance()
        payload = {
            "agent_name": "assistant",
            "msg_id": "m1",
            "context_used": 65000,
            "context_max": 100000,
            "context_pct": 0.65,
            "context_source": "unit_test",
            "context_cache_mode": "full",
            "context_message_count": 12,
            "updated_at": 123.4,
        }
        with self.assertLogs("core.conversation_event_bus", level="DEBUG") as logs:
            bus.publish_event("conv123456", "message_meta", payload)
        text = "\n".join(logs.output)
        assert "[context-gauge:conv1234] send event=message_meta" in text
        assert "agent=assistant" in text
        assert "msg_id=m1" in text
        assert "formula=used/max used=65000 max=100000" in text
        assert "pct_calc=0.6500 pct_payload=0.6500" in text
        assert "source=unit_test" in text
        assert "cache_mode=full" in text
        assert "message_count=12" in text

    def test_publish_sse_event_object(self):
        bus = ConversationEventBus.instance()
        writer = bus.subscribe("conv1")
        bus.publish("conv1", SSEEvent(event="custom", data="payload"))
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))
        assert len(chunks) == 1
        assert b"event: custom" in chunks[0]

    def test_no_replay_subscriber_does_not_discard_buffer_for_next_reconnect(self):
        bus = ConversationEventBus.instance()
        bus.publish_event("conv1", "tool_result", {"tc_id": "tc1", "result": "ok"})

        no_replay = bus.subscribe("conv1", replay=False, client_id="tab-a")
        no_replay.close()
        assert list(no_replay.iterate(timeout=0.1)) == []

        replay = bus.subscribe("conv1", replay=True, client_id="tab-b")
        replay.close()
        chunks = list(replay.iterate(timeout=0.1))

        assert len(chunks) == 1
        assert b"event: tool_result" in chunks[0]
        assert b'"tc_id": "tc1"' in chunks[0]

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

    def test_preempt_rescue_retriggers_until_provider_proves_handled(self):
        from tasks.ai.agent_core import _preempt_rescue_requires_retrigger

        msg = type("Msg", (), {"_pending_source": "preempt_rescue"})()

        assert _preempt_rescue_requires_retrigger(
            msg, provider_completed_at=time.time(), provider="codex-app-server",
            preempt_proven_handled=False)
        assert not _preempt_rescue_requires_retrigger(
            msg, provider_completed_at=time.time(), provider="codex-app-server",
            preempt_proven_handled=True)

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
            "active_agent_name": "assistant",
        })
        task._streaming_agent_loop = MagicMock()

        ff = FlowFile(content=json.dumps({
            "message": "hello",
            "conversation_id": "test-conv-123",
            "target_agent": "assistant",
        }).encode())
        with patch.object(ConversationStore.instance(), 'message_count', return_value=0):
            results = task._execute_streaming(ff)

        assert len(results) == 1
        body = json.loads(results[0].get_content().decode())
        assert body["status"] == "accepted"
        assert body["conversation_id"] == "test-conv-123"
        assert results[0].get_attribute("agent.streaming") == "true"

    def test_pre_user_message_hook_runs_once_between_streaming_ingress_and_context(self):
        from tasks.ai.agent_loop import AgentLoopTask

        hook_calls = []

        class _HookRunner:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def run(self, event, payload, fail_policy=""):
                hook_calls.append({
                    "event": event,
                    "payload": payload,
                    "fail_policy": fail_policy,
                })
                return {"decision": "allow"}

        class _EmptyRegistry:
            def __init__(self):
                self.tools = []

            def list_tools(self):
                return list(self.tools)

            def register(self, handler):
                self.tools.append(handler)

        class _FakeClient:
            provider = "openai"
            default_model = "test-model"
            base_url = ""
            _real_context_size = 200000

        class _FakeService:
            provider = "openai"
            default_model = "test-model"
            base_url = ""
            TYPE = "llm"
            config = {"max_context_size": 200000}

        class _SyncThread:
            def __init__(self, target, daemon=False, name=""):
                self.target = target
                self.daemon = daemon
                self.name = name

            def start(self):
                self.target()

        fake_store = MagicMock()
        fake_store.message_count.return_value = 0
        fake_store.get_extra.return_value = None

        task = AgentLoopTask({
            "api_key": "test-key",
            "streaming": True,
            "conversation_store": False,
        })
        task.get_tool_registry = MagicMock(return_value=_EmptyRegistry())
        task._resolve_client = MagicMock(return_value=(_FakeClient(), _FakeService()))
        task._resolve_service_param = MagicMock(return_value="")
        task._wire_embed_fn = MagicMock()
        task._configure_tool_handlers = MagicMock()
        task._get_summarizer_client = MagicMock(return_value=(None, 0, ""))
        task._streaming_agent_loop = MagicMock()

        ff = FlowFile(content=json.dumps({
            "message": "hello",
            "conversation_id": "test-conv-hook-once",
            "target_agent": "assistant",
        }).encode(), attributes={"http.auth.principal": "alice"})

        with patch("core.agent_hooks.AgentHookRunner", _HookRunner), \
                patch("core.conversation_store.ConversationStore.instance",
                      return_value=fake_store), \
                patch("core.conversation_writer.ConversationWriter.for_conversation") as writer_for_conv, \
                patch("tasks.ai.agent_streaming.threading.Thread", _SyncThread):
            writer_for_conv.return_value.enqueue_message = MagicMock()
            results = task._execute_streaming(ff)

        assert len(results) == 1
        assert json.loads(results[0].get_content().decode())["status"] == "accepted"
        assert len(hook_calls) == 1
        assert hook_calls[0]["event"] == "pre_user_message"
        assert hook_calls[0]["payload"]["content"] == "hello"
        assert hook_calls[0]["payload"]["attachments"] == []
        assert hook_calls[0]["payload"]["target_agent"] == "assistant"
        assert hook_calls[0]["payload"]["channel"] == "web"
        assert hook_calls[0]["fail_policy"] == "closed"
        task._streaming_agent_loop.assert_called_once()
        ctx = task._streaming_agent_loop.call_args.args[0]
        assert ctx["messages"][-1].content == "hello"

    def test_streaming_wakes_pending_after_active_cleanup(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.pending_queue import PendingQueue
        from core.poll_scheduler import PollScheduler

        conversation_id = "test-conv-pending"
        agent_name = "deepseek"
        agent_key = f"{conversation_id}:{agent_name}"

        class _FakeStore:
            def __init__(self, root):
                self._store_dir = root / "convs"

            def _conv_dir(self, cid, user_id=""):
                path = self._store_dir / "u" / cid
                path.mkdir(parents=True, exist_ok=True)
                return path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_store = _FakeStore(root)
            with patch("core.conversation_store.ConversationStore.instance",
                       return_value=fake_store), \
                    patch.object(_paths, "POLL_SCHEDULE_FILE",
                                 root / "poll_schedule.json"):
                PendingQueue.drop_cache()
                PollScheduler.reset()
                task = AgentLoopTask({"api_key": "k", "streaming": True})
                task._conv_generation[agent_key] = 0
                with task._active_lock:
                    task._active_conversations[conversation_id] = 1
                    task._user_active_conversations.add(conversation_id)
                with task._active_contexts_lock:
                    task._active_turns[agent_key] = {
                        "conversation_id": conversation_id,
                        "agent_name": agent_name,
                    }

                def _enqueue_during_cleanup(ctx, cid, bus):
                    PendingQueue.for_agent(cid, agent_name).enqueue({
                        "role": "user",
                        "content": "arrived at done",
                        "msg_id": "m1",
                        "ts": 1234.5,
                    }, source="http")

                task._streaming_agent_loop_inner = _enqueue_during_cleanup
                task._streaming_agent_loop({
                    "active_agent_name": agent_name,
                    "user_id": "u1",
                    "_gen_key": agent_key,
                    "_generation": 0,
                    "_active_turn_key": agent_key,
                }, conversation_id, ConversationEventBus.instance())

                assert conversation_id not in task._active_conversations
                assert agent_key not in task._active_turns
                wake = PollScheduler.instance().get(
                    f"{conversation_id}::pending::{agent_name}")
                assert wake is not None
                assert wake["reason"] == "[pending] 1 queued msg(s) after idle"
                assert wake["recheck_at"] <= time.time() + 1

        PendingQueue.drop_cache()
        PollScheduler.reset()

    def test_interrupted_cleanup_still_wakes_queued_pending_message(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.pending_queue import PendingQueue
        from core.poll_scheduler import PollScheduler

        conversation_id = "test-conv-interrupted-pending-wake"
        agent_name = "assistant"
        agent_key = f"{conversation_id}:{agent_name}"

        class _FakeStore:
            def __init__(self, root):
                self._store_dir = root / "convs"

            def _conv_dir(self, cid, user_id=""):
                path = self._store_dir / "u" / cid
                path.mkdir(parents=True, exist_ok=True)
                return path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_store = _FakeStore(root)
            with patch("core.conversation_store.ConversationStore.instance",
                       return_value=fake_store), \
                    patch.object(_paths, "POLL_SCHEDULE_FILE",
                                 root / "poll_schedule.json"):
                PendingQueue.drop_cache()
                PollScheduler.reset()
                task = AgentLoopTask({"api_key": "k", "streaming": True})
                task._conv_generation[agent_key] = 1
                with task._active_lock:
                    task._active_conversations[conversation_id] = 1
                    task._user_active_conversations.add(conversation_id)
                with task._active_contexts_lock:
                    task._active_turns[agent_key] = {
                        "conversation_id": conversation_id,
                        "agent_name": agent_name,
                    }

                def _enqueue_during_interrupted_cleanup(ctx, cid, bus):
                    PendingQueue.for_agent(cid, agent_name).enqueue({
                        "role": "user",
                        "content": "arrived while old generation exits",
                        "msg_id": "m-interrupted",
                        "ts": 1234.5,
                    }, source="http")

                task._streaming_agent_loop_inner = _enqueue_during_interrupted_cleanup
                task._streaming_agent_loop({
                    "active_agent_name": agent_name,
                    "user_id": "u1",
                    "_gen_key": agent_key,
                    "_generation": 0,
                    "_active_turn_key": agent_key,
                }, conversation_id, ConversationEventBus.instance())

                wake = PollScheduler.instance().get(
                    f"{conversation_id}::pending::{agent_name}")
                assert wake is not None
                assert wake["reason"] == "[pending] 1 queued msg(s) after interrupted turn"
                assert wake["recheck_at"] <= time.time() + 1

        PendingQueue.drop_cache()
        PollScheduler.reset()

    def test_user_message_during_preparing_turn_queues_without_killing_live_client(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.pending_queue import PendingQueue

        conversation_id = "test-conv-preparing-preempt"
        agent_name = "assistant"
        agent_key = f"{conversation_id}:{agent_name}"

        class _FakeStore:
            def __init__(self, root):
                self._store_dir = root / "convs"

            def _conv_dir(self, cid, user_id=""):
                path = self._store_dir / "u" / cid
                path.mkdir(parents=True, exist_ok=True)
                return path

            def message_count(self, cid):
                return 0

        class _ExistingThread:
            name = f"agent-stream-{agent_key}"

            def is_alive(self):
                return True

        started_threads = []

        class _NewThread:
            def __init__(self, target, daemon=False, name=""):
                self.target = target
                self.daemon = daemon
                self.name = name

            def start(self):
                started_threads.append(self)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_store = _FakeStore(root)
            with patch("core.conversation_store.ConversationStore.instance",
                       return_value=fake_store), \
                    patch("core.conversation_writer.ConversationWriter.for_conversation") as writer_for_conv, \
                    patch("tasks.ai.agent_streaming.threading.enumerate",
                          return_value=[_ExistingThread()]), \
                    patch("tasks.ai.agent_streaming.threading.Thread", _NewThread):
                writer_for_conv.return_value.enqueue_message = MagicMock()
                PendingQueue.drop_cache()
                task = AgentLoopTask({"api_key": "k", "streaming": True})
                task._conv_generation[agent_key] = 0
                with task._active_contexts_lock:
                    task._active_turns[agent_key] = {
                        "conversation_id": conversation_id,
                        "agent_name": agent_name,
                        "status": "preparing",
                        "generation": 0,
                    }

                ff = FlowFile(content=json.dumps({
                    "message": "new instruction",
                    "conversation_id": conversation_id,
                    "target_agent": agent_name,
                    "msg_id": "m-preempt",
                }).encode())

                results = task._execute_streaming(ff)

                assert len(results) == 1
                body = json.loads(results[0].get_content().decode())
                assert body["status"] == "queued"
                assert results[0].get_attribute("agent.streaming") is None
                assert results[0].get_attribute("agent.fast_restart_after_preempt") is None
                assert len(started_threads) == 0
                assert task._conv_generation[agent_key] == 0
                assert PendingQueue.for_agent(conversation_id, agent_name).peek_count() == 1

                with task._active_contexts_lock:
                    assert task._active_turns[agent_key]["generation"] == 0

        PendingQueue.drop_cache()

    def test_user_message_during_preparing_turn_queues_before_thread_visible(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.pending_queue import PendingQueue

        conversation_id = "test-conv-preparing-race"
        agent_name = "assistant"
        agent_key = f"{conversation_id}:{agent_name}"
        started_threads = []

        class _FakeStore:
            def __init__(self, root):
                self._store_dir = root / "convs"

            def _conv_dir(self, cid, user_id=""):
                path = self._store_dir / "u" / cid
                path.mkdir(parents=True, exist_ok=True)
                return path

            def message_count(self, cid):
                return 0

        class _NewThread:
            def __init__(self, target, daemon=False, name=""):
                self.target = target
                self.daemon = daemon
                self.name = name

            def start(self):
                started_threads.append(self)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_store = _FakeStore(root)
            with patch("core.conversation_store.ConversationStore.instance",
                       return_value=fake_store), \
                    patch("core.conversation_writer.ConversationWriter.for_conversation") as writer_for_conv, \
                    patch("tasks.ai.agent_streaming.threading.enumerate",
                          return_value=[]), \
                    patch("tasks.ai.agent_streaming.threading.Thread", _NewThread):
                writer_for_conv.return_value.enqueue_message = MagicMock()
                PendingQueue.drop_cache()
                task = AgentLoopTask({"api_key": "k", "streaming": True})
                with task._active_contexts_lock:
                    task._active_turns[agent_key] = {
                        "conversation_id": conversation_id,
                        "agent_name": agent_name,
                        "status": "preparing",
                        "generation": 0,
                    }

                ff = FlowFile(content=json.dumps({
                    "message": "retry same request",
                    "conversation_id": conversation_id,
                    "target_agent": agent_name,
                    "msg_id": "m-preparing-race",
                }).encode())

                results = task._execute_streaming(ff)

                body = json.loads(results[0].get_content().decode())
                assert body["status"] == "queued"
                assert len(started_threads) == 0
                assert PendingQueue.for_agent(conversation_id, agent_name).peek_count() == 1

        PendingQueue.drop_cache()

    def test_user_message_live_preempt_passes_request_identity(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.pending_queue import PendingQueue

        conversation_id = "test-conv-live-preempt"
        agent_name = "assistant"
        agent_key = f"{conversation_id}:{agent_name}"

        class _FakeStore:
            def __init__(self, root):
                self._store_dir = root / "convs"

            def _conv_dir(self, cid, user_id=""):
                path = self._store_dir / "u" / cid
                path.mkdir(parents=True, exist_ok=True)
                return path

            def message_count(self, cid):
                return 0

        class _ExistingThread:
            name = f"agent-stream-{agent_key}"

            def is_alive(self):
                return True

        class _LiveClient:
            supports_live_preempt = True

            def __init__(self):
                self.calls = []

            def send_user_message(self, text, attachments=None, **kwargs):
                self.calls.append((text, attachments, kwargs))
                return True

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_store = _FakeStore(root)
            live_client = _LiveClient()
            with patch("core.conversation_store.ConversationStore.instance",
                       return_value=fake_store), \
                    patch("core.conversation_writer.ConversationWriter.for_conversation") as writer_for_conv, \
                    patch("tasks.ai.agent_streaming.threading.enumerate",
                          return_value=[_ExistingThread()]):
                writer_for_conv.return_value.enqueue_message = MagicMock()
                PendingQueue.drop_cache()
                task = AgentLoopTask({"api_key": "k", "streaming": True})
                with task._active_contexts_lock:
                    task._active_claude_client[agent_key] = live_client
                    task._active_contexts[agent_key] = {
                        "user_id": "alice",
                        "_turn_mode": {"type": "user", "source_agent": None},
                    }

                ff = FlowFile(content=json.dumps({
                    "message": "preempt now",
                    "conversation_id": conversation_id,
                    "target_agent": agent_name,
                    "attachments": [{"url": "fs://filestore/x/y.png"}],
                }).encode(), attributes={"http.auth.principal": "alice"})

                results = task._execute_streaming(ff)

                assert len(results) == 1
                assert live_client.calls
                text, attachments, kwargs = live_client.calls[0]
                assert text == "preempt now"
                assert attachments == [{"url": "fs://filestore/x/y.png"}]
                assert kwargs == {
                    "user_id": "alice",
                    "conversation_id": conversation_id,
                    "agent_name": agent_name,
                }

        PendingQueue.drop_cache()

    def test_interrupt_live_preempt_passes_active_context_user_id(self):
        from tasks.ai.agent_loop import AgentLoopTask, SOFT_INTERRUPT_USER_COMMAND

        conversation_id = "test-conv-interrupt-preempt"
        agent_name = "assistant"
        agent_key = f"{conversation_id}:{agent_name}"

        class _LiveClient:
            def __init__(self):
                self.calls = []

            def send_user_message(self, text, **kwargs):
                self.calls.append((text, kwargs))
                return True

        live_client = _LiveClient()
        task = AgentLoopTask({"api_key": "k", "streaming": True})
        with task._active_contexts_lock:
            task._active_claude_client[agent_key] = live_client
            task._active_contexts[agent_key] = {"user_id": "alice"}

        with patch("services.tool_relay_service.ToolRelayService.cancel_agent"):
            task.interrupt_agent(conversation_id, agent_name)

        assert live_client.calls == [(
            SOFT_INTERRUPT_USER_COMMAND,
            {
                "user_id": "alice",
                "conversation_id": conversation_id,
                "agent_name": agent_name,
            },
        )]

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

    def test_http_listener_closes_stream_iterator_on_disconnect(self):
        src = Path("services/http_listener_service.py").read_text(encoding="utf-8")
        stream_block = src[src.index("if req.response_stream is not None:"):src.index("elif req.response_body:")]
        assert 'getattr(req.response_stream, "close", None)' in stream_block
        assert "close_stream()" in stream_block


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

    def test_routes_from_http_in_have_relations(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        routes = data["tasks"]["http_in"]["parameters"]["routes"]
        relation_keys = {(rel["from"], rel["type"]) for rel in data["relations"]}
        for route in routes:
            relationship = route.get("relationship") or f"{route.get('method', 'GET').upper()}:{route.get('pattern', '/')}"
            assert ("http_in", relationship) in relation_keys

    def test_chat_ui_has_sse_path(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["tasks"]["chat_ui"]["parameters"]["sse_path"] == "/api/agent/events"


# ── i18n streaming keys ────────────────────────────────────────────

