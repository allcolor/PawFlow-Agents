"""Tests for the scope-bounded PawFlow API facade (core.flow_pawflow_api) and
its injection into executeScript.
"""

import time

import pytest

from core import FlowFile
from core.conversation_store import ConversationStore
from core.flow_runtime_access import FlowRuntimeAccessError, make_runtime_context
from core.flow_pawflow_api import FlowPawflowApi

OWNER = "user-owner"
OTHER = "user-other"


@pytest.fixture
def store(tmp_path):
    ConversationStore.reset()
    s = ConversationStore(store_dir=str(tmp_path / "conversations"))
    ConversationStore._instance = s
    yield s
    ConversationStore.reset()


def _api(scope="user", user_id=OWNER, requester=""):
    ctx = make_runtime_context(scope=scope, user_id=user_id)
    return FlowPawflowApi(ctx, requester_user_id=requester)


def _new_conv(store, owner=OWNER):
    cid = store.generate_id()
    store.save(cid, [], user_id=owner)
    return cid


def test_user_scope_blocks_foreign_conversation(store):
    foreign = _new_conv(store, owner=OTHER)
    api = _api(user_id=OWNER)
    with pytest.raises(FlowRuntimeAccessError):
        api.get_extra(foreign, "whatever")
    with pytest.raises(FlowRuntimeAccessError):
        api.delete_conversation(foreign)


def test_user_scope_allows_own_conversation(store):
    cid = _new_conv(store, owner=OWNER)
    api = _api(user_id=OWNER)
    api.set_extra(cid, "k", "v")
    assert api.get_extra(cid, "k") == "v"


def test_set_tool_filters_writes_custom_allowlist(store):
    cid = _new_conv(store)
    api = _api()
    api.set_tool_filters(cid, "helper", ["web_search", "fetch"])
    from core.tool_mcp_filters import get_filters, is_tool_enabled
    filters = get_filters(cid)
    scoped = filters["agent_overrides"]["helper"]["tools"]
    assert scoped["mode"] == "custom"
    assert set(scoped["selected"]) == {"web_search", "fetch"}
    # Allowlist semantics: only listed tools enabled for that agent.
    assert is_tool_enabled(cid, "web_search", agent_name="helper")
    assert is_tool_enabled(cid, "fetch", agent_name="helper")
    assert not is_tool_enabled(cid, "bash", agent_name="helper")
    assert not is_tool_enabled(cid, "write", agent_name="helper")


def test_sliding_ttl_and_expiry(store):
    cid = _new_conv(store)
    api = _api()
    api.set_conversation_ttl(cid, 3600)
    assert not api.is_conversation_expired(cid)
    # Past expiry → expired.
    api.set_extra(cid, "_meta_expires_at", time.time() - 1)
    assert api.is_conversation_expired(cid)
    # Re-arming pushes it back into the future.
    api.set_conversation_ttl(cid, 3600)
    assert not api.is_conversation_expired(cid)


def test_find_conversations_by_extra_and_delete(store):
    cid = _new_conv(store)
    other = _new_conv(store)
    api = _api()
    api.set_extra(cid, "help_bot_user_key", "helper:42")
    found = api.find_conversations("help_bot_user_key", "helper:42")
    assert found == [cid]
    assert api.delete_conversation(cid)
    assert api.find_conversations("help_bot_user_key", "helper:42") == []
    # Untouched conversation still listed.
    assert any(c.get("conversation_id") == other
               for c in api.list_conversations())


def test_create_conversation_passes_ttl_and_authorizes_user(store, monkeypatch):
    created = {}

    def _fake_create(user_id, payload):
        created["user_id"] = user_id
        created["payload"] = payload
        cid = _new_conv(store, owner=user_id)
        return {"conversation_id": cid, "agents": []}

    monkeypatch.setattr(
        "core.conversation_creation.create_conversation", _fake_create)
    api = _api(user_id=OWNER)
    cid = api.create_conversation(
        agents=[{"definition": "helper", "instance_name": "helper"}],
        title="t", relays=[], ttl=1800)
    # Owner is forced to the runtime user, not anything the caller passes.
    assert created["user_id"] == OWNER
    # TTL was stamped.
    assert not api.is_conversation_expired(cid)
    expires = store.get_extra(cid, "_meta_expires_at")
    assert expires and expires > time.time()


def test_run_agent_hard_timeout_cancels(store, monkeypatch):
    cid = _new_conv(store)
    api = _api()

    import core.agent_runtime_api as ara

    class _Sub:
        status = "accepted"
        conversation_id = cid
        turn_id = "turn-1"
        wait_for_done = True

    monkeypatch.setattr(ara.AgentRuntimeAPI, "submit_message",
                        staticmethod(lambda req: _Sub()))
    monkeypatch.setattr(ara.AgentRuntimeAPI, "wait_for_done",
                        staticmethod(lambda c, t, timeout=600.0: None))

    cancelled = {}

    class _Inst:
        def cancel_agent(self, conversation_id, agent_name="", reason=""):
            cancelled["conv"] = conversation_id
            cancelled["agent"] = agent_name
            cancelled["reason"] = reason

    monkeypatch.setattr(api, "_runtime_instance",
                        lambda port: (_Inst(), port or ""))

    res = api.run_agent(cid, "helper", "hi", timeout=0.01)
    assert res["timed_out"] is True
    assert res["response"] == ""
    assert cancelled["conv"] == cid
    assert cancelled["agent"] == "helper"
    assert cancelled["reason"] == "response_timeout"


def test_no_implicit_response_timeout_defaults(store):
    # Project rule: NO implicit timeout. Both the facade and the runtime wait
    # must default to unbounded (timeout=None).
    import inspect
    import core.flow_pawflow_api as mod
    import core.agent_runtime_api as ara
    assert inspect.signature(
        mod.FlowPawflowApi.run_agent).parameters["timeout"].default is None
    assert inspect.signature(
        ara.AgentRuntimeAPI.wait_for_done).parameters["timeout"].default is None


def test_run_agent_unbounded_never_cancels(store, monkeypatch):
    # With the default (timeout=None) the wait is unbounded and the turn is
    # never force-cancelled, even if the waiter yields no result.
    cid = _new_conv(store)
    api = _api()
    import core.agent_runtime_api as ara

    class _Sub:
        status = "accepted"
        conversation_id = cid
        turn_id = "turn-nb"
        wait_for_done = True

    monkeypatch.setattr(ara.AgentRuntimeAPI, "submit_message",
                        staticmethod(lambda req: _Sub()))
    monkeypatch.setattr(ara.AgentRuntimeAPI, "wait_for_done",
                        staticmethod(lambda c, t, timeout=None: None))
    cancelled = {"called": False}
    monkeypatch.setattr(api, "cancel_agent",
                        lambda *a, **k: cancelled.__setitem__("called", True))
    res = api.run_agent(cid, "helper", "hi")  # default timeout=None
    assert res["timed_out"] is False
    assert cancelled["called"] is False


def test_run_agent_returns_response(store, monkeypatch):
    cid = _new_conv(store)
    api = _api()
    import core.agent_runtime_api as ara

    class _Sub:
        status = "accepted"
        conversation_id = cid
        turn_id = "turn-2"
        wait_for_done = True

    class _Res:
        response = "hello back"
        error = ""

    monkeypatch.setattr(ara.AgentRuntimeAPI, "submit_message",
                        staticmethod(lambda req: _Sub()))
    monkeypatch.setattr(ara.AgentRuntimeAPI, "wait_for_done",
                        staticmethod(lambda c, t, timeout=600.0: _Res()))
    res = api.run_agent(cid, "helper", "hi", timeout=5)
    assert res["timed_out"] is False
    assert res["response"] == "hello back"


def test_executescript_injects_scoped_pawflow(store):
    from tasks.system.execute_script import ExecuteScriptTask
    task = ExecuteScriptTask({
        "script": "result = type(pawflow).__name__ + ':' + pawflow.user_id",
    })
    task.set_runtime_context(user_id=OWNER, scope="user")
    ff = FlowFile(content=b"")
    out = task.execute(ff)[0]
    assert out.get_content().decode() == "FlowPawflowApi:" + OWNER
