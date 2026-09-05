"""Public client integration for native ACP and OpenCode providers."""

from unittest.mock import Mock

import pytest

from core._llm_types import LLMClientError, LLMMessage, LLMResponse
from core.llm_client import LLMClient
from services.llm_connection import LLMConnectionService


PROVIDERS = ("cursor-acp", "grok-build-acp", "opencode")


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
def test_native_public_client_dispatch_preserves_call_scope(provider, streaming):
    client = LLMClient(provider, {"auth_mode": "none", "max_retries": 3})
    result = LLMResponse(content="native reply", input_usage_native=False)
    transport = Mock(return_value=result)
    setattr(client, "_stream_opencode" if provider == "opencode" else "_stream_acp", transport)
    messages = [LLMMessage(role="user", content="hello", conversation_id="c")]
    complete = client.complete_stream if streaming else client.complete
    reply = complete(messages, model="vendor/model", call_user_id="u",
                     call_conversation_id="c", call_agent_name="a")
    assert reply.content == "native reply"
    assert reply.tokens_in == 0
    assert reply.tokens_out == 0
    args, kwargs = transport.call_args
    assert args[0] is messages
    assert args[1] == "vendor/model"
    assert args[4] is None
    assert kwargs["call_user_id"] == "u"
    assert kwargs["call_conversation_id"] == "c"
    assert kwargs["call_agent_name"] == "a"


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
def test_failed_native_turn_is_not_replayed_or_sent_to_fallback(provider, streaming):
    client = LLMClient(provider, {"auth_mode": "none", "max_retries": 3,
                                  "fallback_model": "vendor/fallback"})
    transport = Mock(side_effect=LLMClientError("503 temporarily unavailable"))
    setattr(client, "_stream_opencode" if provider == "opencode" else "_stream_acp", transport)
    complete = client.complete_stream if streaming else client.complete
    with pytest.raises(LLMClientError):
        complete([LLMMessage(role="user", content="do this once", conversation_id="c")], model="vendor/model")
    assert transport.call_count == 1


def test_opencode_clones_share_sessions_but_not_cancellation():
    client = LLMClient("opencode", {"auth_mode": "none"})
    first = client.clone_for_call()
    second = client.clone_for_call()
    first._opencode_live_sessions[("svc", "u", "c", "a")] = "session"
    assert second._opencode_live_sessions is client._opencode_live_sessions
    assert second._opencode_live_sessions[("svc", "u", "c", "a")] == "session"
    assert first._opencode_live_lock is second._opencode_live_lock
    first._abort.set()
    assert not client._abort.is_set()
    assert not second._abort.is_set()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_public_abort_reaches_native_runtime(provider, monkeypatch):
    client = LLMClient(provider, {"auth_mode": "none"})
    cancel = Mock()
    setattr(client, "_opencode_abort_active" if provider == "opencode" else "_acp_abort_active", cancel)
    monkeypatch.setattr("core.sqlite_boot_canary.run_sqlite_abort_canary", lambda: None)
    client.abort()
    assert client._abort.is_set()
    cancel.assert_called_once_with(force=True)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_service_close_reaps_native_sessions(provider):
    service = LLMConnectionService({"provider": provider, "auth_mode": "none"})
    close = Mock()
    setattr(service._client, "_opencode_close_all" if provider == "opencode" else "_acp_close_all", close)
    service._close_connection()
    close.assert_called_once_with()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_native_service_actions_match_backend_contract(provider):
    service = LLMConnectionService({"provider": provider, "auth_mode": "none"})
    actions = {a["id"]: a for a in service.get_service_actions()
               if provider in a.get("when", {}).get("provider", [])}
    for action, flow in (("native_cli_server_login", "native_cli_login_server"),
                         ("native_cli_status", "simple"),
                         ("native_cli_versions", "simple"),
                         ("native_cli_update", "native_cli_update")):
        assert actions[action]["server_action"] == action
        assert actions[action]["flow"] == flow
        assert not actions[action].get("before_install")
    effective = {}
    for rule in service.get_parameter_rules():
        if provider in rule.get("when", {}).get("provider", []):
            for name, changes in rule["set"].items():
                effective.setdefault(name, {}).update(changes)
    assert effective["auth_mode"]["options"] == ["none"]
    assert effective["api_key"]["visible"] is False
    assert effective["credential_service_id"]["visible"] is False
    if provider != "opencode":
        assert effective["acp_cwd"]["required"] is True
        assert effective["acp_command"]["required"] is False
    else:
        assert effective["default_model"]["required"] is True
        assert effective["opencode_env"]["visible"] is True
