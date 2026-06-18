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


def test_agent_runtime_waiter_delivers_live_events_before_done():
    from core.agent_runtime_api import AgentResultWaiter
    from core.conversation_event_bus import ConversationEventBus

    ConversationEventBus.reset()
    AgentResultWaiter._instance = None
    waiter = AgentResultWaiter.instance()
    seen = []
    waiter.register("conv1", "turn1", lambda cid, event_type, data: seen.append((cid, event_type, data)))

    bus = ConversationEventBus.instance()
    bus.publish_event("conv1", "new_message", {"role": "assistant", "content": "live"})
    bus.publish_event("conv1", "done", {"turn_id": "turn1", "response": "final"})
    result = waiter.wait("conv1", "turn1", timeout=0.1)

    assert seen == [("conv1", "new_message", {"role": "assistant", "content": "live", "ts": seen[0][2]["ts"]})]
    assert result.response == "final"


def test_agent_runtime_wait_timeout_keeps_live_callback_until_done():
    from core.agent_runtime_api import AgentResultWaiter
    from core.conversation_event_bus import ConversationEventBus

    ConversationEventBus.reset()
    AgentResultWaiter._instance = None
    waiter = AgentResultWaiter.instance()
    seen = []
    waiter.register("conv1", "turn1", lambda cid, event_type, data: seen.append((event_type, data)))

    assert waiter.wait("conv1", "turn1", timeout=0.001) is None

    bus = ConversationEventBus.instance()
    bus.publish_event("conv1", "new_message", {"role": "assistant", "content": "late live"})
    bus.publish_event("conv1", "done", {"turn_id": "turn1", "response": "late final"})
    result = waiter.wait("conv1", "turn1", timeout=0.1)

    assert seen == [("new_message", {"role": "assistant", "content": "late live", "ts": seen[0][1]["ts"]})]
    assert result.response == "late final"


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
    from tasks.io import telegram_agent_client as mod
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
    assert created and "Send the conversation title" in created["text"]
    assert created["reply_markup"]["inline_keyboard"]
    mod._clear_wizard("alice:chat1")

    captured = {}

    def create_from_command(args, user_id):
        captured["args"] = args
        captured["user_id"] = user_id
        return "conv-new", "assistant"

    monkeypatch.setattr(
        TelegramAgentClientTask,
        "_create_conversation_from_command",
        staticmethod(create_from_command),
    )
    created = task._handle_command(
        "/conv new assistant --title T --relay relay1", "alice", "chat1")
    assert created and "Created and selected conversation: conv-new" in created
    assert ids.get_active_conv("alice", "telegram") == "conv-new"
    assert captured == {
        "args": "assistant --title T --relay relay1",
        "user_id": "alice",
    }

    store = ConversationStore.instance()
    cid = store.generate_id()
    store.save(cid, [], user_id="alice")
    store.set_extra(cid, "conv_agents", {
        "assistant": {"definition": "assistant", "llm_service": "llm"},
    })
    ids.set_active_conv("alice", "telegram", cid)

    current = task._handle_command("/conv current", "alice", "chat1")
    assert current and cid in current
    listing = task._handle_command("/conv list", "alice", "chat1")
    assert listing and "Conversations:" in listing["text"]
    assert listing["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("conv:resume:")


def test_telegram_agent_client_dispatches_help_command(monkeypatch):
    import json
    from tasks.io.telegram_agent_client import TelegramAgentClientTask

    captured = {}

    class RuntimeTask:
        def execute(self, flowfile):
            body = json.loads(flowfile.get_content().decode("utf-8"))
            captured["body"] = body
            captured["user_id"] = flowfile.get_attribute("http.auth.principal")
            flowfile.set_content(json.dumps({
                "help": "## Available Commands\n\n**Session**\n  `/help` — List commands",
            }).encode("utf-8"))
            return [flowfile]

    monkeypatch.setattr(
        "core.agent_runtime_ports.resolve_agent_runtime_task",
        lambda runtime_port: RuntimeTask(),
    )

    result = TelegramAgentClientTask(
        {"agent_runtime_port": "pawflow_agent.agent_runtime_in"},
    )._handle_command("/help", "alice", "chat1")

    assert result.startswith("*Available Commands*")
    assert "*Session*" in result
    assert "`/help`" in result
    assert captured["body"]["action"] == "command"
    assert captured["body"]["text"] == "/help"
    assert captured["body"]["_inline_response"] is True
    assert captured["user_id"] == "alice"


def test_telegram_agent_client_dispatch_command_requires_active_conversation(monkeypatch):
    from core.identity_service import IdentityService
    from tasks.io.telegram_agent_client import TelegramAgentClientTask

    IdentityService.reset()
    called = False

    def resolve(runtime_port):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr("core.agent_runtime_ports.resolve_agent_runtime_task", resolve)

    result = TelegramAgentClientTask({})._handle_command("/agent list", "alice", "chat1")

    assert result == "No resumed conversation. Use /conv list then /conv select <id>."
    assert called is False


def test_telegram_conv_new_parser_keeps_options_after_title():
    from tasks.io.telegram_agent_client import _parse_new_conversation_args

    opts = _parse_new_conversation_args(
        "assistant --title My Telegram chat --relay relay1 --llm llm1")

    assert opts == {
        "agent": "assistant",
        "title": "My Telegram chat",
        "relays": ["relay1"],
        "llm": "llm1",
    }


def test_telegram_agent_client_uses_selected_conversation_agent(monkeypatch):
    import core.paths as paths
    from core import FlowFile
    from core.agent_runtime_api import AgentFinalResult, AgentSubmission
    from core.conversation_store import ConversationStore
    from core.identity_service import IdentityService
    from tasks.io import telegram_agent_client as mod
    from tasks.io.telegram_agent_client import TelegramAgentClientTask

    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(paths, "CONVERSATIONS_DIR", tmp / "conversations")
    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp / "users")
    ConversationStore.reset()
    IdentityService.reset()

    store = ConversationStore.instance()
    ids = IdentityService.instance()
    assert ids.link("alice", "telegram", "tg1")
    cid = store.generate_id()
    store.save(cid, [], user_id="alice")
    store.set_extra(cid, "conv_agents", {
        "assistant": {"definition": "assistant", "llm_service": "llm"},
    })
    store.set_extra(cid, "active_resources", {"agent": "assistant"})
    ids.set_active_conv("alice", "telegram", cid)

    captured = {}

    def submit(request):
        captured["target_agent"] = request.target_agent
        captured["runtime_port"] = request.runtime_port
        return AgentSubmission("accepted", request.conversation_id, request.msg_id)

    def wait(conversation_id, turn_id, timeout=600):
        return AgentFinalResult(conversation_id, turn_id, response="pong")

    monkeypatch.setattr(mod.AgentRuntimeAPI, "submit_message", staticmethod(submit))
    monkeypatch.setattr(mod.AgentRuntimeAPI, "wait_for_done", staticmethod(wait))

    ff = FlowFile(content=b"ping")
    ff.set_attribute("telegram.user_id", "tg1")
    ff.set_attribute("telegram.chat_id", "chat1")
    ff.set_attribute("telegram.message_id", "42")

    out = TelegramAgentClientTask({
        "agent_runtime_port": "pawflow_agent.agent_runtime_in",
    }).execute(ff)[0]

    assert captured["target_agent"] == "assistant"
    assert captured["runtime_port"] == "pawflow_agent.agent_runtime_in"
    # Nothing was forwarded live this turn, so the final agent response must
    # reach Telegram instead of being suppressed.
    assert out.get_content() == b"pong"


def test_agent_runtime_api_uses_declared_runtime_port(monkeypatch):
    from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI
    from tasks.ai.agent_loop import AgentLoopTask

    class RuntimeTask:
        def __init__(self):
            self.called = False

        def execute(self, flowfile):
            import json
            self.called = True
            body = json.loads(flowfile.get_content().decode("utf-8"))
            flowfile.set_content(json.dumps({
                "status": "accepted",
                "conversation_id": body["conversation_id"],
            }).encode("utf-8"))
            return [flowfile]

    runtime = RuntimeTask()
    monkeypatch.setattr(
        "core.agent_runtime_ports.resolve_agent_runtime_task",
        lambda runtime_port: runtime,
    )
    monkeypatch.setattr(AgentLoopTask, "_live_instance", None)

    submission = AgentRuntimeAPI.submit_message(AgentRequest(
        user_id="alice",
        conversation_id="conv1",
        target_agent="assistant",
        message="hello",
        msg_id="telegram:1:2",
        runtime_port="pawflow_agent.agent_runtime_in",
    ))

    assert runtime.called is True
    assert submission.conversation_id == "conv1"
    assert submission.turn_id == "telegram:1:2"


def test_agent_runtime_submission_can_disable_done_wait(monkeypatch):
    from core.agent_runtime_api import AgentRequest, AgentRuntimeAPI
    from tasks.ai.agent_loop import AgentLoopTask

    class RuntimeTask:
        def execute(self, flowfile):
            import json
            body = json.loads(flowfile.get_content().decode("utf-8"))
            flowfile.set_content(json.dumps({
                "status": "accepted",
                "conversation_id": body["conversation_id"],
                "wait_for_done": False,
            }).encode("utf-8"))
            return [flowfile]

    monkeypatch.setattr(
        "core.agent_runtime_ports.resolve_agent_runtime_task",
        lambda runtime_port: RuntimeTask(),
    )
    monkeypatch.setattr(AgentLoopTask, "_live_instance", None)

    submission = AgentRuntimeAPI.submit_message(AgentRequest(
        user_id="alice",
        conversation_id="conv1",
        target_agent="assistant",
        message="preempt",
        msg_id="telegram:1:3",
        runtime_port="pawflow_agent.agent_runtime_in",
    ))

    assert submission.status == "accepted"
    assert submission.wait_for_done is False


def test_agent_runtime_port_resolver_finds_running_declared_port(monkeypatch):
    from types import SimpleNamespace
    from core.agent_runtime_ports import resolve_agent_runtime_task

    task = object()

    class DeployReg:
        def get_all(self):
            return {
                "inst1": SimpleNamespace(
                    instance_id="inst1",
                    flow_id="pawflow-agent",
                    flow_name="pawflow_agent",
                    flow_fqn="default.pawflow_agent:1.0.0",
                    flow_path="",
                    status="running",
                )
            }

    class Executor:
        flow = SimpleNamespace(ports={
            "agent_runtime_in": {"type": "agentRuntime", "task": "agent"},
        })

        def get_task(self, task_id):
            return task if task_id == "agent" else None

    class ExecReg:
        def get(self, instance_id):
            return Executor() if instance_id == "inst1" else None

    monkeypatch.setattr(
        "core.deployment_registry.DeploymentRegistry.get_instance",
        lambda: DeployReg(),
    )
    monkeypatch.setattr(
        "core.executor_registry.ExecutorRegistry.get_instance",
        lambda: ExecReg(),
    )

    assert resolve_agent_runtime_task("pawflow_agent.agent_runtime_in") is task


def test_telegram_relay_validation_uses_user_scope(monkeypatch):
    from tasks.io import telegram_agent_client as mod

    captured = {}

    def list_available_relays(user_id="", conv_id=""):
        captured["user_id"] = user_id
        captured["conv_id"] = conv_id
        return [{"relay_id": "relay1", "connected": True}]

    monkeypatch.setattr(
        "core.relay_bindings.list_available_relays", list_available_relays)

    assert mod._validate_relays(["relay1"], user_id="alice") == ["relay1"]
    assert captured == {"user_id": "alice", "conv_id": ""}


def test_telegram_new_conversation_wizard_builds_payload(monkeypatch):
    from tasks.io import telegram_agent_client as mod
    from tasks.io.telegram_agent_client import TelegramAgentClientTask

    class ServiceDef:
        service_id = "llm1"

    created = {}

    monkeypatch.setattr("tasks.io._telegram_client_helpers._available_agents", lambda user_id: [
        {"name": "assistant", "model": "", "tools": [], "max_depth": 1000, "skills": []},
    ])
    monkeypatch.setattr("tasks.io._telegram_client_helpers._agent_definition", lambda user_id, name: {
        "name": name, "model": "", "tools": [], "max_depth": 1000, "skills": [],
    })
    monkeypatch.setattr("tasks.io._telegram_client_helpers._available_llm_services", lambda user_id: [ServiceDef()])
    monkeypatch.setattr("tasks.io._telegram_client_helpers._available_relays", lambda user_id: [
        {"relay_id": "relay1", "connected": True},
    ])

    def create_conversation(user_id, payload):
        created["user_id"] = user_id
        created["payload"] = payload
        return {"conversation_id": "conv1", "agents": ["coder"]}

    class Ids:
        selected = None
        def set_active_conv(self, user_id, channel, conv_id):
            self.selected = (user_id, channel, conv_id)

    ids = Ids()
    monkeypatch.setattr("core.conversation_creation.create_conversation", create_conversation)
    monkeypatch.setattr("core.identity_service.IdentityService.instance", lambda: ids)

    task = TelegramAgentClientTask({})

    response = task._handle_command("/conv new", "alice", "chat1")
    assert "Send the conversation title" in response["text"]
    response = task._handle_wizard_input("Work", "", "alice", "chat1")
    assert "Choose an agent" in response["text"]
    response = task._handle_wizard_input("conv:new:agent:0", "conv:new:agent:0", "alice", "chat1")
    assert "instance name" in response["text"]
    response = task._handle_wizard_input("coder", "", "alice", "chat1")
    assert "Choose the LLM" in response["text"]
    response = task._handle_wizard_input("conv:new:llm:0", "conv:new:llm:0", "alice", "chat1")
    assert "Agents:" in response["text"]
    response = task._handle_wizard_input("conv:new:relays", "conv:new:relays", "alice", "chat1")
    assert "Select one or more relays" in response["text"]
    task._handle_wizard_input("conv:new:relay:0", "conv:new:relay:0", "alice", "chat1")
    response = task._handle_wizard_input("conv:new:create", "conv:new:create", "alice", "chat1")

    assert response["text"] == "Created and selected conversation: conv1"
    assert created["user_id"] == "alice"
    assert created["payload"]["title"] == "Work"
    assert created["payload"]["relays"] == ["relay1"]
    assert created["payload"]["default_relay"] == "relay1"
    assert created["payload"]["agents"][0]["instance_name"] == "coder"
    assert created["payload"]["agents"][0]["definition"] == "assistant"
    assert created["payload"]["agents"][0]["llm_service"] == "llm1"
    assert ids.selected == ("alice", "telegram", "conv1")


def test_telegram_filestore_media_does_not_fallback_to_unscoped_access():
    from core.file_store import FileStore
    from tasks.io.telegram_agent_client import _load_filestore_media

    fid = FileStore.instance().store(
        "secret.txt", b"secret", content_type="text/plain",
        user_id="alice", conversation_id="conv-a")

    try:
        _load_filestore_media(fid, "bob")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Telegram media load must not retry without user scope")

    name, raw, content_type = _load_filestore_media(fid, "alice")
    assert name == "secret.txt"
    assert raw == b"secret"
    assert content_type == "text/plain"

