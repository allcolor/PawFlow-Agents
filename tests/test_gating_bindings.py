"""Policy gate bindings: conversation + agent, strict resolution (plan WP3)."""

import json
from types import SimpleNamespace

import pytest

from core import FlowFile
from core import gating_bindings as gb


class _Store:
    def __init__(self):
        self.extras = {}

    def get_extra_cached(self, cid, key, default=None):
        return self.extras.get((cid, key), default)

    def get_extra(self, cid, key, default=None):
        return self.extras.get((cid, key), default)

    def set_extra(self, cid, key, value):
        self.extras[(cid, key)] = value


def _sdef(service_id, scope="user", enabled=True, service_type="gating", **config):
    return SimpleNamespace(service_id=service_id, scope=scope, scope_id="alice" if scope == "user" else "",
                           service_type=service_type, enabled=enabled, description="",
                           config=dict({"prompt": "p", "llm_service": "llm"}, **config))


class _Registry:
    def __init__(self, defs, live=None):
        self.defs = defs
        self.live = live or {}

    def get_definition(self, scope, scope_id, service_id):
        sdef = self.defs.get(service_id)
        return sdef if sdef and sdef.scope == scope else None

    def resolve_definition(self, service_id, *, user_id="", conv_id=""):
        return self.defs.get(service_id)

    def resolve_by_type(self, service_type, *, user_id="", conv_id="", enabled_only=True):
        return [d for d in self.defs.values() if d.service_type == service_type
                and (d.enabled or not enabled_only)]

    def get_live_instance(self, scope, scope_id, service_id):
        return self.live.get(service_id)


@pytest.fixture
def env(monkeypatch):
    store = _Store()
    live = SimpleNamespace(evaluate=lambda *a, **k: {"decision": "allow"})
    registry = _Registry({
        "conv_gate": _sdef("conv_gate"),
        "agent_gate": _sdef("agent_gate"),
        "script_gate": _sdef("script_gate", scripts=["no_push"]),
        "disabled": _sdef("disabled", enabled=False),
        "summ": _sdef("summ", service_type="summarizer"),
    }, live={"conv_gate": live, "agent_gate": live, "script_gate": live})
    monkeypatch.setattr(gb, "_store", lambda: store)
    monkeypatch.setattr(gb, "_registry", lambda: registry)
    monkeypatch.setattr("core.relay_bindings.get_linked_all", lambda cid: [])
    agents = {}
    monkeypatch.setattr("core.conv_agent_config.get_agent_config",
                        lambda cid, name: agents.get(name, {"gating_service": ""}))
    return SimpleNamespace(store=store, registry=registry, agents=agents, live=live)


def test_no_binding_means_no_gate_and_no_fallback(env):
    resolved = gb.resolve_gates("alice", "c1", "assistant")
    assert resolved["bound"] is False and resolved["gates"] == []
    assert [d["service_id"] for d in gb.list_available("alice", "c1")] == [
        "conv_gate", "agent_gate", "script_gate"]
    assert gb.summary("alice", "c1")["bound"] is False


def test_conversation_binding_is_explicit_and_validated(env):
    with pytest.raises(ValueError, match="not available"):
        gb.validate_binding("user", "disabled", "alice", "c1")
    with pytest.raises(ValueError, match="not available"):
        gb.validate_binding("user", "summ", "alice", "c1")
    with pytest.raises(ValueError, match="link a relay"):
        gb.validate_binding("user", "script_gate", "alice", "c1")
    gb.validate_binding("user", "conv_gate", "alice", "c1")
    gb.set_binding("c1", "user", "conv_gate")
    assert gb.get_binding("c1") == {"scope": "user", "service_id": "conv_gate"}
    resolved = gb.resolve_gates("alice", "c1", "assistant")
    assert resolved["bound"] and not resolved["broken"]
    assert [g["origin"] for g in resolved["gates"]] == ["conversation"]
    assert gb.clear_binding("c1") is True and gb.get_binding("c1") == {}


def test_broken_binding_fails_closed_and_is_reported(env):
    gb.set_binding("c1", "user", "conv_gate")
    env.registry.defs["conv_gate"].enabled = False
    resolved = gb.resolve_gates("alice", "c1")
    assert resolved["bound"] is True and resolved["broken"] is True
    assert resolved["gates"] == []
    assert "unavailable" in resolved["conversation"]["error"]
    assert gb.summary("alice", "c1")["conversation"]["broken"] is True
    # A script gate whose relay went away is broken too.
    env.registry.defs["conv_gate"].enabled = True
    gb.set_binding("c1", "user", "script_gate")
    assert "linked relay" in gb.resolve_gates("alice", "c1")["conversation"]["error"]


def test_agent_gate_is_additional_and_deduplicated(env):
    gb.set_binding("c1", "user", "conv_gate")
    env.agents["assistant"] = {"gating_service": {"scope": "user", "service_id": "agent_gate"}}
    env.agents["twin"] = {"gating_service": "conv_gate"}
    both = gb.resolve_gates("alice", "c1", "assistant")
    assert [g["origin"] for g in both["gates"]] == ["conversation", "agent"]
    same = gb.resolve_gates("alice", "c1", "twin")
    assert [g["origin"] for g in same["gates"]] == ["conversation"]
    gb.clear_binding("c1")
    only_agent = gb.resolve_gates("alice", "c1", "assistant")
    assert only_agent["bound"] and [g["origin"] for g in only_agent["gates"]] == ["agent"]


def test_actions_link_unlink_and_list(env):
    from tasks.ai.actions.misc import _handle_misc
    ff = FlowFile()
    _handle_misc(None, "gating_link", {"conversation_id": "c1", "scope": "user",
                                       "service_id": "script_gate"}, None, "alice", ff)
    assert "link a relay" in json.loads(ff.content)["error"]
    ff = FlowFile()
    _handle_misc(None, "gating_link", {"conversation_id": "c1", "scope": "user",
                                       "service_id": "conv_gate"}, None, "alice", ff)
    data = json.loads(ff.content)
    assert data["ok"] is True and data["gating"]["bound"] is True
    ff = FlowFile()
    _handle_misc(None, "gating_list_available", {"conversation_id": "c1"}, None, "alice", ff)
    assert json.loads(ff.content)["binding"] == {"scope": "user", "service_id": "conv_gate"}
    ff = FlowFile()
    _handle_misc(None, "gating_unlink", {"conversation_id": "c1"}, None, "alice", ff)
    data = json.loads(ff.content)
    assert data["removed"] is True and data["gating"]["bound"] is False


def test_gating_decisions_action_reads_the_audit_log(tmp_path, monkeypatch):
    from core import tool_authorization as ta
    from tasks.ai.actions.misc import _handle_misc
    monkeypatch.setattr(ta, "_audit_dir", lambda: tmp_path / "audit")
    ta._audit("c9", {"decision_id": "d1", "decision": "ask", "tool": "bash"})
    ff = FlowFile()
    _handle_misc(None, "gating_decisions", {"conversation_id": "c9", "limit": "5"}, None, "alice", ff)
    data = json.loads(ff.content)
    assert data["decisions"][0]["decision_id"] == "d1"
    ff = FlowFile()
    _handle_misc(None, "gating_decisions", {}, None, "alice", ff)
    assert "error" in json.loads(ff.content)


def test_webchat_exposes_policy_gate_binding_and_decisions():
    with open("tasks/io/chat_ui/resources_render.js", encoding="utf-8") as handle:
        render = handle.read()
    with open("tasks/io/chat_ui/resources_flow_templates.js", encoding="utf-8") as handle:
        dialogs = handle.read()
    with open("tasks/io/chat_ui/resources.js", encoding="utf-8") as handle:
        sections = handle.read()
    assert "data.gating" in render and "_showGatingLinkDialog()" in render
    assert "_unlinkGating()" in render and "_showGatingDecisions()" in render
    assert "'_gating'" in sections
    for action in ("gating_list_available", "gating_link", "gating_unlink", "gating_decisions"):
        assert "action$('" + action + "'" in dialogs, action
    for lang in ("en", "fr", "es"):
        with open(f"tasks/io/chat_ui/i18n/{lang}.json", encoding="utf-8") as handle:
            catalog = json.load(handle)
        for key in ("policyGate", "linkPolicyGate", "noPolicyGate", "noPolicyGateServices",
                    "policyGateBroken", "policyGateDecisions", "policyGateAgent"):
            assert catalog[key], (lang, key)


def test_agent_config_carries_gating_service():
    from core.conv_agent_config import AGENT_CONFIG_DEFAULTS
    assert AGENT_CONFIG_DEFAULTS["gating_service"] == ""
    with open("tasks/ai/actions/_agentres_k5.py", encoding="utf-8") as handle:
        src = handle.read()
    assert 'gating_service=body.get("gating_service") or ""' in src
    # Match membership, not the exact set literal: asserting the literal made
    # this break every time an unrelated runtime field was appended, pointing
    # at the wrong change each time.
    allowed_literal = src.split("_allowed = {", 1)[1].split("}", 1)[0]
    allowed_fields = {
        part.strip().strip('"').strip("'")
        for part in allowed_literal.replace("\n", " ").split(",")
        if part.strip()
    }
    assert "gating_service" in allowed_fields
    assert "validate_binding(_gref.get(" in src
