"""WP1 opt-in workflow routing and compatibility gates."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core import FlowFile
from core.agent_feature_flags import (
    BASE_AGENT_RUNTIME_KINDS,
    WORKFLOW_AGENTS_ENV,
    allowed_agent_runtime_kinds,
    validate_agent_runtime_kind,
)
from core.agent_runtime_router import (
    AgentRunKey,
    AgentRuntimeRouter,
    AgentRuntimeRoutingError,
)
from core.conv_agent_config import add_agent_to_conv
from core.workflow_agent_contracts import PreparedAgentTurn
from tasks.ai.actions._agentres_k5 import _handle_agentres_k5
from tasks.ai.actions._conv_import import _validate_imported_agent_runtimes


@pytest.fixture(autouse=True)
def _reset_router(monkeypatch):
    monkeypatch.delenv(WORKFLOW_AGENTS_ENV, raising=False)
    AgentRuntimeRouter._instance = None
    yield
    AgentRuntimeRouter._instance = None


def _prepared_turn(agent_name: str = "wiki") -> PreparedAgentTurn:
    auth_id = "0f1e2d3c-4b5a-4698-8071-625344332211"
    turn_id = "web:turn-1"
    return PreparedAgentTurn.from_dict({
        "schema_version": 1,
        "turn_identity": {
            "schema_version": 1,
            "conversation_id": "conv-1",
            "root_conversation_id": "conv-1",
            "agent_instance": agent_name,
            "turn_id": turn_id,
            "ingress_msg_id": turn_id,
            "turn_epoch": 1,
            "run_generation": 1,
            "authorization_context_id": auth_id,
            "authorization_revision_at_start": 1,
            "source_kind": "user",
            "source_id": "alice",
            "created_at": "2026-08-24T08:00:00Z",
        },
        "conversation_id": "conv-1",
        "agent_name": agent_name,
        "user_id": "alice",
        "root_turn_id": turn_id,
        "request_message_ids": [turn_id],
        "channel": "web",
        "message": "refresh",
        "attachments": [],
        "source": {"type": "user", "name": "alice"},
        "permission_mode": "default",
        "authorization_ref": {
            "context_id": auth_id,
            "revision": 1,
            "root_turn_id": turn_id,
        },
    })


def test_workflow_runtime_kind_is_strictly_server_owned(monkeypatch):
    assert allowed_agent_runtime_kinds() == BASE_AGENT_RUNTIME_KINDS
    with pytest.raises(ValueError, match="disabled by the server"):
        validate_agent_runtime_kind("workflow")

    monkeypatch.setenv(WORKFLOW_AGENTS_ENV, "true")
    assert validate_agent_runtime_kind("workflow") == "workflow"
    assert allowed_agent_runtime_kinds() == BASE_AGENT_RUNTIME_KINDS | {"workflow"}

    monkeypatch.setenv(WORKFLOW_AGENTS_ENV, "truthy")
    with pytest.raises(ValueError, match=WORKFLOW_AGENTS_ENV):
        allowed_agent_runtime_kinds()


def test_add_agent_rejects_workflow_disabled_and_needs_no_llm_when_enabled(
        monkeypatch):
    with pytest.raises(ValueError, match="disabled by the server"):
        add_agent_to_conv(
            "conv-1", "Wiki", llm_service="", definition="wiki",
            runtime_kind="workflow")

    stored = {}
    store = MagicMock()
    store.get_extra.side_effect = lambda _cid, key: stored.get(key)
    store.set_extra.side_effect = lambda _cid, key, value: stored.__setitem__(key, value)
    monkeypatch.setenv(WORKFLOW_AGENTS_ENV, "1")
    with patch("core.conversation_store.ConversationStore.instance",
               return_value=store), patch(
                   "core.workflow_agent_resources.bind_agent_workflow",
                   return_value={"flow_fqn": "pawflow.wiki:1.0.0"}):
        config = add_agent_to_conv(
            "conv-1", "Wiki", llm_service="", definition="wiki",
            runtime_kind="workflow")
    assert config["runtime_kind"] == "workflow"
    assert config["llm_service"] == ""


def test_resource_action_rejects_request_side_workflow_enablement():
    flowfile = FlowFile(content=b"{}")
    result = _handle_agentres_k5(
        None,
        "add_agent_to_conv",
        {"conversation_id": "conv-1", "instance_name": "Wiki",
         "definition": "wiki", "runtime_kind": "workflow"},
        MagicMock(),
        "alice",
        flowfile,
    )
    assert result == [flowfile]
    assert flowfile.get_attribute("http.response.status") == "400"
    assert "disabled by the server" in json.loads(flowfile.get_content())["error"]


def test_archive_import_cannot_bypass_workflow_capability(monkeypatch):
    extras = {"conv_agents": {
        "Wiki": {"definition": "wiki", "runtime_kind": "workflow"},
    }}
    with pytest.raises(ValueError, match="disabled by the server"):
        _validate_imported_agent_runtimes(extras)

    monkeypatch.setenv(WORKFLOW_AGENTS_ENV, "yes")
    _validate_imported_agent_runtimes(extras)
    _validate_imported_agent_runtimes({
        "conv_agents": {"Assistant": {"definition": "assistant"}},
    })


def test_router_dispatches_only_canonical_workflow_adapter(monkeypatch):
    monkeypatch.setenv(WORKFLOW_AGENTS_ENV, "on")
    monkeypatch.setattr(
        "core.conv_agent_config.resolve_agent_config_entry",
        lambda _cid, _name: (
            "parent-conv", "Wiki",
            {"runtime_kind": "workflow", "definition": "wiki"}),
    )
    calls = []

    class Adapter:
        runtime_kind = "workflow"

        def submit(self, request):
            calls.append(("submit", request.agent_name))
            return "accepted"

        def cancel(self, key, reason, force):
            calls.append(("cancel", key.agent_name, reason, force))
            return True

    router = AgentRuntimeRouter()
    router.register(Adapter())
    resolved = router.resolve("conv-1", "wiki")
    assert resolved.roster_conversation_id == "parent-conv"
    assert resolved.canonical_agent_name == "Wiki"
    assert router.dispatch(_prepared_turn()) == "accepted"
    assert router.cancel(AgentRunKey("conv-1", "wiki"), "stop", True) is True
    assert calls == [
        ("submit", "wiki"),
        ("cancel", "Wiki", "stop", True),
    ]

    with pytest.raises(ValueError, match="only the workflow adapter"):
        router.register(type("Legacy", (), {"runtime_kind": "llm"})())


def test_router_fails_closed_before_adapter_when_capability_is_disabled(
        monkeypatch):
    monkeypatch.setattr(
        "core.conv_agent_config.resolve_agent_config_entry",
        lambda _cid, _name: (
            "conv-1", "Wiki", {"runtime_kind": "workflow"}),
    )
    with pytest.raises(AgentRuntimeRoutingError) as exc:
        AgentRuntimeRouter().resolve("conv-1", "Wiki")
    assert exc.value.code == "workflow_agents_disabled"


def test_autonomous_workflow_wake_cannot_fall_through_to_llm(monkeypatch):
    from tasks.ai.agent_poller import AgentPollerMixin

    poller = AgentPollerMixin.__new__(AgentPollerMixin)
    monkeypatch.setattr(
        poller, "_extract_agent_from_reasons", lambda _reasons: "Wiki")
    monkeypatch.setattr(
        "core.conv_agent_config.get_agent_config",
        lambda _cid, _agent: {"runtime_kind": "workflow"})
    writer = MagicMock()
    external_route = MagicMock()
    with patch("core.conversation_writer.ConversationWriter.for_conversation",
               return_value=writer), \
            patch("services.external_agent_runtime_router.route_external_agent_prompt",
                  external_route):
        handled = poller._redirect_external_mcp_wake(
            "conv-1", ["[pending] wake Wiki"])
    assert handled is True
    writer.assert_not_called()
    external_route.assert_not_called()


def test_streaming_persists_then_refuses_disabled_workflow_without_llm_worker():
    from tasks.ai.agent_loop import AgentLoopTask

    events = []
    fake_store = MagicMock()
    fake_store.get_extra_snapshot.return_value = 1
    task = AgentLoopTask({
        "api_key": "test", "streaming": True, "conversation_store": False})
    conversation_id = "conv-workflow-disabled"
    agent_key = f"{conversation_id}:Wiki"
    with task._active_contexts_lock:
        task._active_turns.pop(agent_key, None)
        task._active_contexts.pop(agent_key, None)
        task._active_claude_client.pop(agent_key, None)

    class HookRunner:
        def __init__(self, **_kwargs):
            pass

        def run(self, *_args, **_kwargs):
            return {"decision": "allow"}

    class NoThread:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("disabled workflow must not start an LLM worker")

    writer = MagicMock()
    writer.enqueue_message.side_effect = lambda *_a, **_k: events.append("persist")
    external_route = MagicMock()
    ff = FlowFile(content=json.dumps({
        "message": "refresh", "conversation_id": conversation_id,
        "target_agent": "Wiki", "msg_id": "m-workflow",
    }).encode(), attributes={"http.auth.principal": "alice"})

    with patch("core.conversation_access.authorize_message_submission"), \
            patch("core.authorization_context.record_user_ingress", return_value=None), \
            patch("core.conversation_store.ConversationStore.instance",
                  return_value=fake_store), \
            patch("core.agent_hooks.AgentHookRunner", HookRunner), \
            patch("core.conversation_writer.ConversationWriter.for_conversation",
                  return_value=writer), \
            patch("core.conv_agent_config.resolve_agent_config_entry",
                  return_value=(conversation_id, "Wiki",
                                {"runtime_kind": "workflow"})), \
            patch("services.external_agent_runtime_router.route_external_agent_prompt",
                  external_route), \
            patch("tasks.ai.agent_streaming.threading.Thread", NoThread):
        result = task._execute_streaming(ff)

    body = json.loads(result[0].get_content())
    assert events == ["persist"]
    assert body["code"] == "workflow_agents_disabled"
    assert result[0].get_attribute("http.response.status") == "503"
    external_route.assert_not_called()


@pytest.mark.parametrize("channel", ["web", "telegram", "a2a"])
def test_enabled_workflow_ingress_dispatches_one_correlated_prepared_turn(
        monkeypatch, channel):
    from core.authorization_context import AuthorizationRef
    from tasks.ai.agent_loop import AgentLoopTask

    monkeypatch.setenv(WORKFLOW_AGENTS_ENV, "1")
    submitted = []

    class Adapter:
        runtime_kind = "workflow"

        def submit(self, request):
            submitted.append(request)
            return {"status": "accepted", "queued": False, "run_id": "wr_test"}

        def cancel(self, key, reason, force):
            return True

    AgentRuntimeRouter.instance().register(Adapter())
    task = AgentLoopTask({
        "api_key": "test", "streaming": True, "conversation_store": False})
    conversation_id = f"conv-workflow-{channel}"
    fake_store = MagicMock()
    fake_store.get_extra_snapshot.return_value = 1
    writer = MagicMock()
    inbox = MagicMock()
    ff = FlowFile(content=json.dumps({
        "message": "refresh",
        "conversation_id": conversation_id,
        "target_agent": "Wiki",
        "msg_id": f"{channel}:turn",
    }).encode(), attributes={
        "http.auth.principal": "alice",
        "agent.client_channel": channel,
    })
    auth = AuthorizationRef(
        context_id="0f1e2d3c-4b5a-4698-8071-625344332211",
        revision=1,
        root_turn_id=f"{channel}:turn",
    )
    with (
        patch("core.conversation_access.authorize_message_submission"),
        patch("core.authorization_context.record_user_ingress",
              return_value=auth),
        patch("core.conversation_store.ConversationStore.instance",
              return_value=fake_store),
        patch("core.agent_hooks.AgentHookRunner") as hooks,
        patch("core.conversation_writer.ConversationWriter.for_conversation",
              return_value=writer),
        patch("core.agent_inbox_store.AgentInboxStore.instance",
              return_value=inbox),
        patch("core.conv_agent_config.resolve_agent_config_entry",
              return_value=(conversation_id, "Wiki",
                            {"runtime_kind": "workflow"})),
    ):
        hooks.return_value.run.return_value = {"decision": "allow"}
        result = task._execute_streaming(ff)

    ack = json.loads(result[0].get_content())
    assert ack["status"] == "accepted"
    assert ack["wait_for_done"] is True
    assert ack["runtime_kind"] == "workflow"
    assert ack["run_id"] == "wr_test"
    assert len(submitted) == 1
    assert submitted[0].root_turn_id == f"{channel}:turn"
    assert submitted[0].channel == channel
    assert submitted[0].authorization_ref.context_id == auth.context_id
    writer.enqueue_message_if_absent.assert_called_once()
    inbox.prepare_receipt.assert_called_once()
    inbox.mark_transcript_persisted.assert_called_once_with(
        conversation_id, "Wiki", f"{channel}:turn")
