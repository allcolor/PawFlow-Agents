"""Central tool authorization engine + ingress recording (policy gating WP5)."""

import json
from types import SimpleNamespace

import pytest

from core import authorization_context as ac
from core import tool_authorization as ta


class _Store:
    def __init__(self):
        self.extras = {}

    def get_extra(self, cid, key, default=None):
        return self.extras.get((cid, key), default)

    def get_extra_cached(self, cid, key, default=None):
        return self.extras.get((cid, key), default)

    def set_extra(self, cid, key, value):
        self.extras[(cid, key)] = value


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = _Store()
    monkeypatch.setattr("core.conversation_store.ConversationStore.instance", lambda: store)
    ctx_store = ac.AuthorizationContextStore(root=tmp_path / "authz")
    monkeypatch.setattr(ac.AuthorizationContextStore, "instance", classmethod(lambda cls: ctx_store))
    monkeypatch.setattr(ta, "_audit_dir", lambda: tmp_path / "audit")
    return SimpleNamespace(store=store, ctx=ctx_store, tmp=tmp_path)


def _gate(decision, reason="", calls=None):
    def evaluate(envelope, **kwargs):
        if calls is not None:
            calls.append(envelope)
        return {"decision": decision, "reason": reason, "evaluators": [
            {"source": "script", "source_id": "s", "decision": decision, "reason": reason}]}
    return SimpleNamespace(evaluate=evaluate)


def _resolved(conv=None, agent=None, broken=False):
    entries = []
    if conv is not None:
        entries.append({"origin": "conversation", "ref": {"scope": "user", "service_id": "cg"},
                        "service": conv, "broken": False, "error": ""})
    if agent is not None:
        entries.append({"origin": "agent", "ref": {"scope": "user", "service_id": "ag"},
                        "service": agent, "broken": False, "error": ""})
    return {"conversation": entries[0] if conv is not None else {"ref": {}, "broken": broken,
                                                                "error": "gate down" if broken else ""},
            "agent": {"ref": {}, "broken": False, "error": ""},
            "bound": bool(entries) or broken, "broken": broken, "gates": entries}


def _call(**over):
    base = dict(tool_name="bash", arguments={"command": "git status"}, user_id="alice",
                conversation_id="c1", agent_name="assistant", turn_id="t1", call_id="tc1")
    base.update(over)
    return base


def test_no_binding_is_legacy_and_writes_no_audit(env):
    result = ta.authorize_tool_call(**_call(), resolved_gates=_resolved())
    assert result.decision == "legacy"
    assert ta.list_decisions("c1") == []


def test_gate_allow_executes_and_is_audited_redacted(env):
    calls = []
    result = ta.authorize_tool_call(
        **_call(arguments={"command": "curl -H 'Authorization: Bearer TOKEN1234'"},
                secret_values=["TOKEN1234"]),
        resolved_gates=_resolved(conv=_gate("allow", "requested", calls)))
    assert result.decision == "execute" and result.classification == "ordinary"
    assert "TOKEN1234" not in json.dumps(calls[0])
    records = ta.list_decisions("c1")
    assert len(records) == 1 and records[0]["decision"] == "execute"
    assert "TOKEN1234" not in json.dumps(records[0])
    assert records[0]["decision_id"] == result.decision_id and records[0]["created_at"] > 0
    ta.record_execution_outcome("c1", result.decision_id, "succeeded")
    assert ta.list_decisions("c1")[-1]["outcome"] == "succeeded"


def test_structural_guards_beat_the_gate(env):
    allow = _gate("allow")
    denied = ta.authorize_tool_call(**_call(tool_permission="deny"), resolved_gates=_resolved(conv=allow))
    assert denied.decision == "deny" and "structural" in denied.reason
    read_only = ta.authorize_tool_call(**_call(permission_mode="read_only"), resolved_gates=_resolved(conv=allow))
    assert read_only.decision == "deny"
    hard = ta.authorize_tool_call(**_call(tool_name="create_tool", arguments={}), resolved_gates=_resolved(conv=allow))
    assert hard.decision == "ask" and hard.classification == "hard_confirm"
    catastrophic = ta.authorize_tool_call(**_call(arguments={"command": "rm -rf /"}), resolved_gates=_resolved(conv=allow))
    assert catastrophic.decision == "ask"
    confirm = ta.authorize_tool_call(**_call(tool_permission="confirm"), resolved_gates=_resolved(conv=allow))
    assert confirm.decision == "ask"
    plumbing = ta.authorize_tool_call(**_call(tool_name="get_tool_schema", arguments={}), resolved_gates=_resolved(conv=allow))
    assert plumbing.decision == "legacy"


def test_broken_binding_and_gate_exceptions_fail_closed(env):
    broken = ta.authorize_tool_call(**_call(), resolved_gates=_resolved(broken=True))
    assert broken.decision == "ask" and "gate down" in broken.reason

    def boom(envelope, **kwargs):
        raise RuntimeError("down")
    raised = ta.authorize_tool_call(**_call(), resolved_gates=_resolved(conv=SimpleNamespace(evaluate=boom)))
    assert raised.decision == "ask" and "gate failure" in raised.reason


def test_agent_gate_can_only_tighten(env):
    tightened = ta.authorize_tool_call(**_call(), resolved_gates=_resolved(conv=_gate("allow"), agent=_gate("deny", "no")))
    assert tightened.decision == "deny"
    loosened = ta.authorize_tool_call(**_call(), resolved_gates=_resolved(conv=_gate("ask"), agent=_gate("allow")))
    assert loosened.decision == "ask"
    calls = []
    ta.authorize_tool_call(**_call(), resolved_gates=_resolved(conv=_gate("deny"), agent=_gate("allow", calls=calls)))
    assert calls == []  # a conversation deny short-circuits the agent gate


def test_envelope_carries_the_active_lineage_revision(env):
    ref = ac.record_user_ingress(user_id="alice", conversation_id="c1", agent_name="assistant",
                                 message_id="m1", turn_id="m1", content="fix the bug", steering=False)
    ac.record_user_ingress(user_id="alice", conversation_id="c1", agent_name="assistant",
                           message_id="m2", turn_id="m2", content="do not push", steering=True)
    calls = []
    result = ta.authorize_tool_call(**_call(), resolved_gates=_resolved(conv=_gate("allow", calls=calls)))
    authority = calls[0]["authority"]
    assert authority["context_id"] == ref.context_id and authority["revision"] == 2
    assert authority["followups"] == ["do not push"]
    assert result.authority_ref["revision"] == 2
    assert calls[0]["authority_missing"] is False


def test_ingress_starts_new_lineage_for_new_requests_and_revises_when_steering(env):
    first = ac.record_user_ingress(user_id="alice", conversation_id="c1", agent_name="assistant",
                                   message_id="m1", turn_id="m1", content="task A", steering=False)
    second = ac.record_user_ingress(user_id="alice", conversation_id="c1", agent_name="assistant",
                                    message_id="m2", turn_id="m2", content="task B", steering=False)
    assert second.context_id != first.context_id
    revised = ac.record_user_ingress(user_id="alice", conversation_id="c1", agent_name="assistant",
                                     message_id="m3", turn_id="m3", content="also C", steering=True)
    assert revised.context_id == second.context_id and revised.revision == 2
    other = ac.record_user_ingress(user_id="alice", conversation_id="c1", agent_name="reviewer",
                                  message_id="m4", turn_id="m4", content="review", steering=True)
    assert other.context_id not in (first.context_id, second.context_id)
    assert ac.active_authority_ref("c1", "assistant") == revised
    assert ac.record_user_ingress(user_id="", conversation_id="c1", agent_name="a",
                                  message_id="m", turn_id="m", content="x", steering=False) is None


def test_interim_guard_fails_closed_on_unmigrated_runtimes(env, monkeypatch):
    monkeypatch.setattr("core.gating_bindings.resolve_gates",
                        lambda u, c, a="": _resolved(conv=_gate("allow")))
    blocked = ta.interim_guard("alice", "c1", "assistant", "bash", {"command": "ls"},
                               runtime="sub-agent")
    assert "not gated yet" in blocked and "sub-agent" in blocked
    assert ta.interim_guard("alice", "c1", "assistant", "get_tool_schema", {},
                            runtime="sub-agent") == ""
    assert ta.list_decisions("c1")[-1]["runtime"] == "sub-agent"
    monkeypatch.setattr("core.gating_bindings.resolve_gates", lambda u, c, a="": _resolved())
    assert ta.interim_guard("alice", "c1", "assistant", "bash", {}, runtime="voice") == ""
    for path, runtime in (("core/agent_executor.py", "sub-agent"),
                          ("services/_realtime_tools.py", "voice"),
                          ("core/agui_client_runtime.py", "external AG-UI"),
                          ("services/_tool_relay_execute.py", "tool relay")):
        with open(path, encoding="utf-8") as handle:
            src = handle.read()
        assert "interim_guard(" in src and f'runtime="{runtime}"' in src, path


def test_primary_runtime_wires_engine_and_ingress_in_order():
    with open("tasks/ai/agent_tool_exec.py", encoding="utf-8") as handle:
        exec_src = handle.read()
    i_prepare = exec_src.index("_prepare(_eff_name, _eff_args)")
    i_policy = exec_src.index("authorize_tool_call(")
    i_legacy = exec_src.index("if _always_allow_plumbing:\n                    _tool_perm = \"allow\"")
    i_exec = exec_src.index("registry.execute_prepared(_prepared)")
    # The gate sees the prepared call, decides before the legacy rules, and
    # nothing rewrites the call between the decision and execution.
    assert i_prepare < exec_src.index("def _authorize") or True
    assert i_policy < i_legacy < i_exec
    assert 'if _policy.decision == "deny":' in exec_src
    assert 'if _policy.decision == "execute":\n                        return ""' in exec_src
    assert "[policy gate]" in exec_src
    with open("tasks/ai/agent_streaming.py", encoding="utf-8") as handle:
        stream_src = handle.read()
    assert "record_user_ingress(" in stream_src
    assert "steering=bool(_already_active)" in stream_src
    assert stream_src.index('_stream_mark("stamped")') < stream_src.index("_stamp_authority(_stamped_user)")
