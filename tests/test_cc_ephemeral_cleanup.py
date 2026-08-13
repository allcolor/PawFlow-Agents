"""Regression tests for Claude Code pre-dispatch ephemeral cleanup."""

import inspect
from types import SimpleNamespace

import pytest

from core.llm_client import LLMClient


@pytest.mark.parametrize("failure", [RuntimeError("setup"), KeyboardInterrupt()])
def test_pre_dispatch_failure_reclaims_spawned_process(
        monkeypatch, failure):
    client = LLMClient("claude-code")
    proc = object()
    state = SimpleNamespace(
        proc=proc, workdir="/tmp/cc-ephemeral", user_id="alice",
        conv_id="_project_wiki_job", _mcp_internal_token="internal")
    cleaned = []
    recovered = []
    revoked = []

    def fail_before_dispatch(*_args, _lifecycle_guard=None, **_kwargs):
        _lifecycle_guard.update(armed=True, state=state)
        raise failure

    monkeypatch.setattr(
        client, "_stream_claude_code_inner", fail_before_dispatch)
    monkeypatch.setattr(
        client, "_cleanup_proc", lambda item: cleaned.append(item) or "")
    monkeypatch.setattr(
        client, "_recover_tokens",
        lambda workdir, **scope: recovered.append((workdir, scope)))
    monkeypatch.setattr(
        "core.internal_auth.revoke_token", lambda token: revoked.append(token))

    with pytest.raises(type(failure)):
        client._stream_claude_code(
            [], "", 0.7, 0, None,
            call_user_id="alice",
            call_conversation_id="_project_wiki_job",
            call_agent_name="project-wiki",
            call_ephemeral_stream=True)

    assert cleaned == [proc]
    assert recovered == [("/tmp/cc-ephemeral", {
        "user_id": "alice", "conversation_id": "_project_wiki_job"})]
    assert revoked == ["internal"]


def test_401_retry_forwards_all_per_call_scope_values():
    source = inspect.getsource(LLMClient._stream_claude_code_inner)
    for value in (
        "call_user_id=st.user_id",
        "call_conversation_id=st.conv_id",
        "call_agent_name=st.agent_name",
        "call_event_cid=st._raw_event_cid",
        "call_ephemeral_stream=st._is_ephemeral",
    ):
        assert value in source
