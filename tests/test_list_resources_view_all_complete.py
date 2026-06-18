"""Regression: admin view-all must not return a SPARSE list_resources payload.

The a49 view-all branch early-returned a dict containing only the repo-backed
catalogs (agents/skills/mcp/task_defs/prompts/hooks). Every other section --
deployed flows, flow templates, summarizer, relays, remote FS, tasks -- was
absent, so the resource panel half-emptied when an admin toggled "Tous"
(notably "Dépôt Flows" went blank). The fix builds the full self-view, then
overlays the catalogs cross-user. This test pins the no-blanking contract:
every section key in the self-view must also be present under view=all.
"""

import json

import pytest

from core import FlowFile
from tasks import register_all_tasks
from tasks.ai.actions.agent_resource import _handle_agent_resource

register_all_tasks()


class _StubStore:
    def get_extras_snapshot(self, c):
        return {}

    def get_extra(self, c, k, default=None):
        return default


class _StubSelf:
    def _ensure_active_agent(self, conv_id, active, uid):
        return active or {}


class _FakeDeploymentRegistry:
    def get_all(self):
        return {}


def _list_resources(monkeypatch, *, view_all):
    import core.deployment_registry as dr_mod
    monkeypatch.setattr(dr_mod.DeploymentRegistry, "get_instance",
                        classmethod(lambda cls: _FakeDeploymentRegistry()))
    body = {"action": "list_resources", "conversation_id": ""}
    if view_all:
        body["view"] = "all"
    ff = FlowFile(b"")
    ff.set_attribute("http.auth.roles", "admin")
    ff.set_attribute("http.auth.principal", "allcolor")
    out = _handle_agent_resource(
        _StubSelf(), "list_resources", body, _StubStore(), "allcolor", ff)
    assert out == [ff]
    return json.loads(ff.content.decode("utf-8"))


def test_view_all_is_not_sparse(monkeypatch):
    self_view = _list_resources(monkeypatch, view_all=False)
    all_view = _list_resources(monkeypatch, view_all=True)

    # view=all is flagged, and downgrade-free admin keeps every section.
    assert all_view.get("view") == "all"
    assert self_view.get("view") is None

    missing = set(self_view) - set(all_view)
    assert not missing, f"view=all dropped sections: {sorted(missing)}"

    # The repo catalogs that the panel renders must all survive the overlay.
    for key in ("repo_agents", "skills", "mcp_servers", "task_defs",
                "prompts", "agent_hooks", "flows", "flow_templates"):
        assert key in all_view, f"view=all missing {key!r}"


def test_non_admin_view_all_is_downgraded(monkeypatch):
    import core.deployment_registry as dr_mod
    monkeypatch.setattr(dr_mod.DeploymentRegistry, "get_instance",
                        classmethod(lambda cls: _FakeDeploymentRegistry()))
    ff = FlowFile(b"")
    ff.set_attribute("http.auth.roles", "user")
    ff.set_attribute("http.auth.principal", "bob")
    out = _handle_agent_resource(
        _StubSelf(), "list_resources",
        {"action": "list_resources", "conversation_id": "", "view": "all"},
        _StubStore(), "bob", ff)
    data = json.loads(out[0].content.decode("utf-8"))
    # Non-admin asking for view=all is silently downgraded to self view.
    assert data.get("view") is None
