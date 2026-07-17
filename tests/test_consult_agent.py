"""consult_agent: one-shot delegation to the conversation agent's LLM.

No network: agent resolution, the service registry, and the conversation
context resolver are stubbed at their module boundaries.
"""

from types import SimpleNamespace

import pytest

from core.handlers.consult_agent import ConsultAgentHandler


@pytest.fixture()
def handler():
    h = ConsultAgentHandler()
    h.set_user_id("quentin")
    h.set_conversation_id("conv1")
    h.set_agent_name("claude")
    return h


@pytest.fixture()
def stubbed(monkeypatch):
    """Happy-path stubs; individual tests override pieces."""
    import core.agent_executor as ax
    import core.service_registry as sr
    import core.handlers.spawn_agents as sa

    task_obj = SimpleNamespace(system_prompt="You are Claude, the brain.",
                               llm_service="strong-llm")
    calls = {}

    def _resolve_agent_task(agent_name, message, user_id,
                            conversation_id="", extra_skills=None):
        calls["resolved"] = (agent_name, message, user_id, conversation_id)
        return task_obj
    monkeypatch.setattr(ax, "resolve_agent_task", _resolve_agent_task)

    class _Svc:
        def complete(self, messages, **kwargs):
            calls["messages"] = messages
            calls["kwargs"] = kwargs
            return SimpleNamespace(content="the smart answer")

    registry = SimpleNamespace(
        resolve=lambda name, user_id="", conv_id="": calls.setdefault(
            "svc_name", name) and _Svc() or _Svc())
    monkeypatch.setattr(sr.ServiceRegistry, "get_instance",
                        classmethod(lambda cls: registry))

    monkeypatch.setattr(
        sa, "resolve_context_messages",
        lambda mode, cid, uid: [{"role": "user",
                                 "content": "[Context summary] earlier"}])
    return calls


class TestConsultAgent:
    def test_task_required(self, handler):
        assert "task is required" in handler.execute({})

    def test_needs_conversation_context(self):
        h = ConsultAgentHandler()
        h.set_agent_name("claude")
        assert "conversation" in h.execute({"task": "think"})

    def test_unknown_agent_reports_error(self, handler, stubbed,
                                         monkeypatch):
        import core.agent_executor as ax

        def _boom(*a, **k):
            raise KeyError("Agent 'claude' is not in conversation 'conv1'")
        monkeypatch.setattr(ax, "resolve_agent_task", _boom)
        out = handler.execute({"task": "think"})
        assert out.startswith("Error:") and "claude" in out

    def test_unresolvable_llm_service_reports_error(self, handler, stubbed,
                                                    monkeypatch):
        import core.service_registry as sr
        registry = SimpleNamespace(
            resolve=lambda name, user_id="", conv_id="": None)
        monkeypatch.setattr(sr.ServiceRegistry, "get_instance",
                            classmethod(lambda cls: registry))
        out = handler.execute({"task": "think"})
        assert "strong-llm" in out and out.startswith("Error:")

    def test_happy_path_composition(self, handler, stubbed):
        out = handler.execute({"task": "design the plan"})
        assert out == "the smart answer"
        # Delegation resolved against the conversation's agent.
        assert stubbed["resolved"] == ("claude", "design the plan",
                                       "quentin", "conv1")
        msgs = stubbed["messages"]
        assert msgs[0].role == "system"
        assert msgs[0].content == "You are Claude, the brain."
        assert any("[Context summary]" in m.content for m in msgs)
        assert msgs[-1].role == "user"
        assert msgs[-1].content == "design the plan"
        # Attributed to the agent for usage tracking.
        assert stubbed["kwargs"]["call_agent_name"] == "claude"
        assert stubbed["kwargs"]["call_conversation_id"] == "conv1"

    def test_long_answer_truncated(self, handler, stubbed, monkeypatch):
        import core.handlers.consult_agent as ca
        monkeypatch.setattr(ca, "_ANSWER_MAX_CHARS", 10)
        out = handler.execute({"task": "go"})
        assert out.startswith("the smart ")
        assert out.endswith("[answer truncated]")

    def test_delegate_failure_is_reported(self, handler, stubbed,
                                          monkeypatch):
        import core.service_registry as sr

        class _Broken:
            def complete(self, messages, **kwargs):
                raise RuntimeError("provider down")
        registry = SimpleNamespace(
            resolve=lambda name, user_id="", conv_id="": _Broken())
        monkeypatch.setattr(sr.ServiceRegistry, "get_instance",
                            classmethod(lambda cls: registry))
        out = handler.execute({"task": "think"})
        assert "provider down" in out and out.startswith("Error:")


class TestIntegrationSurface:
    def test_registered_in_default_registry(self):
        from core.tool_registry import create_default_registry
        names = {h.name for h in create_default_registry().list_tools()}
        assert "consult_agent" in names

    def test_approval_exempt(self):
        from core.tool_approval import ToolApprovalGate
        assert "consult_agent" in ToolApprovalGate.EXEMPT_TOOLS

    def test_exposable_through_realtime_tool_profile(self):
        from services._realtime_tools import RealtimeToolBridge
        bridge = RealtimeToolBridge("consult_agent", "conv1", "claude",
                                    "quentin")
        defs = bridge.tool_definitions()
        assert [d["name"] for d in defs] == ["consult_agent"]
