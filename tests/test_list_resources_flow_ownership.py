"""Security regression: list_resources must not leak other users' deployments.

The resource panel (`list_resources`) is a per-user view. User- and
conversation-scoped deployed flows are visible ONLY to their owner / within
their own conversation. The admin role grants NO cross-user visibility here:
a user-scoped deployment owned by another account (e.g. a technical user)
must never appear in an admin's resource panel. Cross-user management lives
in dedicated admin endpoints, not in list_resources.
"""

import json
from types import SimpleNamespace

import pytest

from core import FlowFile
from tasks.ai.actions.agent_resource import _handle_agent_resource


class _StubStore:
    def get_extra(self, conv_id, key, default=None):
        return default

    def get_extras_snapshot(self, conv_id):
        return {}

    def set_extra(self, conv_id, key, value):
        pass


class _StubSelf:
    def _ensure_active_agent(self, conv_id, active, uid):
        return active or {}


class _FakeDeploymentRegistry:
    def __init__(self, instances):
        self._instances = instances

    def get_all(self):
        return self._instances


def _inst(owner, conversation_id, flow_name):
    return SimpleNamespace(
        owner=owner,
        conversation_id=conversation_id,
        flow_name=flow_name,
        status="running",
        flow_id=f"http_bots.{flow_name}:1.0.0",
    )


def _list_resources_flows(monkeypatch, *, viewer, roles, conv_id):
    import core.deployment_registry as dr_mod
    import core.relay_bindings as rb

    conv = "convAAAA00000001"
    other_conv = "convBBBB00000002"
    instances = {
        "glob1": _inst("", "", "pawflow_agent"),                 # global
        "alice_user": _inst("alice", "", "web_help_bot"),         # user: alice
        "bob_user": _inst("bob", "", "web_help_bot"),             # user: bob (private)
        "alice_conv": _inst("alice", conv, "custom_bot"),         # conv: this conv
        "bob_conv": _inst("bob", other_conv, "custom_bot"),       # conv: other conv
    }
    fake_reg = _FakeDeploymentRegistry(instances)
    monkeypatch.setattr(dr_mod.DeploymentRegistry, "get_instance",
                        classmethod(lambda cls: fake_reg))
    monkeypatch.setattr(rb, "get_bindings",
                        lambda cid: {"linked": {}, "default": {}})

    ff = FlowFile(b"")
    ff.set_attribute("http.auth.roles", roles)
    out = _handle_agent_resource(
        _StubSelf(), "list_resources",
        {"conversation_id": conv_id}, _StubStore(), viewer, ff)
    assert out == [ff]
    data = json.loads(ff.content.decode("utf-8"))
    return {f["instance_id"] for f in data.get("flows", [])}


def test_admin_does_not_see_other_users_user_scoped_flows(monkeypatch):
    # Admin viewing as 'alice' must see global + alice's own + this conv's
    # flows, and NEVER bob's user-scoped or other-conv deployments.
    ids = _list_resources_flows(
        monkeypatch, viewer="alice", roles="admin", conv_id="convAAAA00000001")
    assert "glob1" in ids
    assert "alice_user" in ids
    assert "alice_conv" in ids
    assert "bob_user" not in ids   # the leak this fix closes
    assert "bob_conv" not in ids


def test_admin_and_plain_user_see_identical_flow_set(monkeypatch):
    # The admin role must grant NO extra deployment visibility.
    admin_ids = _list_resources_flows(
        monkeypatch, viewer="alice", roles="admin", conv_id="convAAAA00000001")
    user_ids = _list_resources_flows(
        monkeypatch, viewer="alice", roles="user", conv_id="convAAAA00000001")
    assert admin_ids == user_ids
