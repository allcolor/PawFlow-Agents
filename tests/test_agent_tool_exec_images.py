from core.handlers.meta_tools import UseToolHandler
from core.llm_client import LLMToolCall
from core.tool_handler import ToolHandler
from core.tool_registry import ToolRegistry
from tasks.ai.agent_core import AgentCoreMixin
from tasks.ai.agent_tool_exec import AgentToolExecMixin


class ImageHandler(ToolHandler):
    name = "see"
    description = "Return an image marker."
    _returns_images = True

    @property
    def parameters_schema(self):
        return {"type": "object", "properties": {}}

    def execute(self, arguments):
        return "Image: screenshot.png (1 bytes, image/png)\n__image_data__:image/png:AA=="


class Agent(AgentToolExecMixin):
    def _run_hook(self, *args, **kwargs):
        return None


def test_use_tool_wrapped_image_result_becomes_multimodal(monkeypatch):
    from core.tool_approval import ToolApprovalGate
    monkeypatch.setattr(ToolApprovalGate, "check", lambda *args, **kwargs: "approved")

    registry = ToolRegistry()
    registry.register(ImageHandler())
    registry.register(UseToolHandler(registry))

    tc = LLMToolCall(
        id="tc-img",
        name="use_tool",
        arguments={"tool_name": "see", "arguments": {"path": "screen"}},
    )

    results = Agent()._execute_tool_calls(
        [tc], registry, {}, 100,
        agent_name="deepseek", conversation_id="conv-1", user_id="user-1",
    )

    content = results[0][1]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "Image: screenshot.png (1 bytes, image/png)"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AA=="


def test_wrapped_multimodal_tool_output_preserves_image_blocks():
    content = [
        {"type": "text", "text": "Image: screenshot.png (1 bytes, image/png)"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]

    wrapped = AgentCoreMixin._wrap_tool_output("use_tool", content)

    assert isinstance(wrapped, list)
    assert wrapped[0] == content[0]
    assert wrapped[1] == content[1]
