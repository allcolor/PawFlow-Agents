"""Secrets stay references in text that agents or users read.

Messages to other agents, task prompts, user notifications and persisted
notes are stored and shown, so a $NAME / ${NAME} secret reference in them is
never replaced by its value. Execution tools still receive the value.
"""

import pytest

from core.tool_approval import ToolApprovalGate as Gate

_SECRET_NAME = "PF_TEST_TOKEN"
_SECRET_VALUE = "TOPSECRET-VALUE"


@pytest.fixture
def secret_store(monkeypatch):
    from core.secret_resolver import SecretResolver

    def resolve_name(_self, key, **_kwargs):
        return _SECRET_VALUE if key == _SECRET_NAME else None

    monkeypatch.setattr(SecretResolver, "resolve_name", resolve_name)
    monkeypatch.setattr(
        "services.tool_relay_service.resolve_secrets_env",
        lambda *_args, **_kwargs: {_SECRET_NAME: _SECRET_VALUE})
    monkeypatch.setattr(
        "services.tool_relay_service.resolve_secret_values",
        lambda *_args, **_kwargs: (set(), {}))
    monkeypatch.setattr(Gate, "check", lambda *_args, **_kwargs: "approved")


def _run(tool_name, arguments):
    from core.llm_client import LLMToolCall
    from core.tool_handler import ToolHandler
    from core.tool_registry import ToolRegistry
    from tasks.ai.agent_tool_exec import AgentToolExecMixin

    executed = []

    class Handler(ToolHandler):
        name = tool_name
        description = "test"
        parameters_schema = {
            "type": "object",
            "properties": {key: {"type": "string"} for key in arguments},
        }

        def execute(self, args):
            executed.append(dict(args))
            return "ok"

    registry = ToolRegistry()
    registry.register(Handler())
    result = AgentToolExecMixin()._execute_tool_calls(
        [LLMToolCall(id="call-1", name=tool_name, arguments=dict(arguments))],
        registry, {}, 10,
        conversation_id="conv-secret-ref", user_id="owner", parallel=False,
    )
    assert result[0][1] == "ok"
    return executed[0]


@pytest.mark.parametrize("tool_name, arguments", [
    ("delegate", {"agent": "reviewer",
                  "message": f"use ${{{_SECRET_NAME}}} and ${_SECRET_NAME}"}),
    ("flash_delegate", {"message": f"${_SECRET_NAME}"}),
    ("notify_user", {"content": f"token ${_SECRET_NAME}"}),
    ("remember", {"text": f"token ${_SECRET_NAME}"}),
])
def test_agent_message_tools_keep_secret_references(secret_store, tool_name, arguments):
    executed = _run(tool_name, arguments)
    assert executed == arguments
    assert _SECRET_VALUE not in repr(executed)


def test_execution_tool_still_receives_secret_value(secret_store):
    executed = _run("web_fetch", {"url": f"https://example.test/?k=${_SECRET_NAME}"})
    assert executed["url"] == f"https://example.test/?k={_SECRET_VALUE}"


@pytest.mark.parametrize("tool_name, arguments, allowed", [
    ("delegate", {}, False),
    ("use_tool", {"tool_name": "delegate", "arguments_json": "{}"}, False),
    ("mcp__pawflow__use_tool", {"tool_name": "notify_user", "arguments": {}}, False),
    ("use_tool", {"tool_name": "web_fetch", "arguments_json": "{}"}, True),
    ("web_fetch", {}, True),
])
def test_wrapped_calls_are_judged_by_their_inner_tool(tool_name, arguments, allowed):
    from services._tool_relay_base import allows_var_substitution
    assert allows_var_substitution(tool_name, arguments) is allowed


def test_task_prompt_variables_never_resolve_secrets(secret_store, monkeypatch):
    from core.handlers.task_assign import AssignTaskHandler

    monkeypatch.setenv("PF_TEST_REGION", "eu")
    text = AssignTaskHandler._resolve_task_vars(
        f"deploy ${{target}} in ${{PF_TEST_REGION}} with ${{{_SECRET_NAME}}}",
        {"target": "api"}, user_id="owner", conversation_id="conv-secret-ref")
    assert text == f"deploy api in eu with ${{{_SECRET_NAME}}}"


def test_resolve_expression_can_skip_secrets(secret_store):
    from core.expression import resolve_expression

    template = f"${{{_SECRET_NAME}}}"
    assert resolve_expression(template, owner="owner") == _SECRET_VALUE
    assert resolve_expression(
        template, owner="owner", include_secrets=False) == template
