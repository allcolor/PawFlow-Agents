from core.handlers._fs_base import BaseFsHandler
from core.tool_handler import ToolHandler
from core.tool_registry import ToolRegistry
from tasks.ai.agent_tool_config import AgentToolConfigMixin


class CapturingContextHandler(ToolHandler):
    name = "capture_context"
    description = "Capture injected context."

    def __init__(self):
        self.user_id = ""
        self.conversation_id = ""
        self.agent_name = ""
        self.base_url = ""

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def set_user_id(self, user_id):
        self.user_id = user_id

    def set_conversation_id(self, conversation_id):
        self.conversation_id = conversation_id

    def set_agent_name(self, agent_name):
        self.agent_name = agent_name

    def set_base_url(self, base_url):
        self.base_url = base_url

    def execute(self, arguments):
        return "ok"


class CapturingFsHandler(BaseFsHandler):
    name = "capture_fs"
    description = "Capture injected filesystem service."

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, arguments):
        return "ok"


class ConfiguringAgent(AgentToolConfigMixin):
    config = {"file_base_url": "https://files.example"}

    def _find_filesystem_service(self, user_id):
        return None


def test_configure_tool_handlers_injects_provider_invariants_generically():
    registry = ToolRegistry()
    handler = CapturingContextHandler()
    registry.register(handler)

    ConfiguringAgent()._configure_tool_handlers(
        registry,
        conversation_id="conv-1",
        user_id="user-1",
        agent_name="deepseek",
    )

    assert handler.user_id == "user-1"
    assert handler.conversation_id == "conv-1"
    assert handler.agent_name == "deepseek"
    assert handler.base_url == "https://files.example"


def test_configure_tool_handlers_resolves_default_relay_with_conversation_id(monkeypatch):
    import core.relay_bindings as relay_bindings

    fake_relay = object()
    calls = []

    class _Registry:
        def resolve(self, service_id, user_id="", conv_id=""):
            calls.append((service_id, user_id, conv_id))
            if service_id == "relay-conv" and conv_id == "conv-1":
                return fake_relay
            return None

    monkeypatch.setattr(relay_bindings, "get_default", lambda cid, agent="": "relay-conv")
    monkeypatch.setattr(relay_bindings, "get_linked", lambda cid, agent="": ["relay-conv"])
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        staticmethod(lambda: _Registry()),
    )

    registry = ToolRegistry()
    handler = CapturingFsHandler()
    registry.register(handler)

    ConfiguringAgent()._configure_tool_handlers(
        registry,
        conversation_id="conv-1",
        user_id="user-1",
        agent_name="assistant",
    )

    assert handler._fs_service is fake_relay
    assert ("relay-conv", "user-1", "conv-1") in calls
