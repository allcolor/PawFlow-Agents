import tempfile
from pathlib import Path


def test_conversation_event_bus_notifies_internal_listeners():
    from core.conversation_event_bus import ConversationEventBus

    ConversationEventBus.reset()
    bus = ConversationEventBus.instance()
    seen = []

    def listener(cid, event_type, data):
        seen.append((cid, event_type, data))

    bus.add_listener(listener)
    bus.publish_event("conv1", "done", {"turn_id": "t1", "response": "ok"})

    assert seen == [("conv1", "done", {"turn_id": "t1", "response": "ok", "ts": seen[0][2]["ts"]})]


def test_stream_done_payload_includes_transport_correlation():
    from tasks.ai.agent_emitter import AgentResult, StreamEmitter

    class Bus:
        def __init__(self):
            self.events = []

        def publish_event(self, cid, event_type, data):
            self.events.append((cid, event_type, data))

    bus = Bus()
    emitter = StreamEmitter(
        "conv1", bus,
        {"channel": "telegram", "request_msg_id": "telegram:1:2",
         "active_agent_name": "assistant"},
        agent=None, gen_key="conv1", generation=1)
    emitter.on_done(AgentResult(response_content="hello", finish_reason="stop"))

    _, event_type, data = bus.events[0]
    assert event_type == "done"
    assert data["channel"] == "telegram"
    assert data["turn_id"] == "telegram:1:2"
    assert data["request_msg_id"] == "telegram:1:2"
    assert data["finish_reason"] == "stop"


def test_telegram_agent_client_conv_commands(monkeypatch):
    import core.paths as paths
    from core.conversation_store import ConversationStore
    from core.identity_service import IdentityService
    from tasks.io.telegram_agent_client import TelegramAgentClientTask

    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(paths, "CONVERSATIONS_DIR", tmp / "conversations")
    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp / "users")
    ConversationStore.reset()
    IdentityService.reset()

    ids = IdentityService.instance()
    assert ids.link("alice", "telegram", "tg1")
    task = TelegramAgentClientTask({})

    created = task._handle_command("/conv new", "alice", "chat1")
    assert created and "Created and selected conversation" in created
    current = task._handle_command("/conv current", "alice", "chat1")
    assert current and "Active conversation:" in current
    listing = task._handle_command("/conv list", "alice", "chat1")
    assert listing and "Conversations:" in listing

