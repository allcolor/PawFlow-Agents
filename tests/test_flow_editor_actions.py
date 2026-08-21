"""flow_editor_* actions: auth, scope gates, 409 on stale revision, publish."""

import json

import pytest

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import core.paths as paths
    from core.repository import ScopedRepository
    from core.flow_authoring import FlowAuthoringService
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    ScopedRepository.reset()
    FlowAuthoringService.reset()
    yield
    ScopedRepository.reset()
    FlowAuthoringService.reset()


def _call(action, body, user_id="alice", roles=""):
    from tasks.ai.actions.flow_editor import _handle_flow_editor
    ff = FlowFile()
    if roles:
        ff.set_attribute("http.auth.roles", roles)
    result = _handle_flow_editor(None, action, body, None, user_id, ff)
    assert result is not None
    return json.loads(ff.get_content().decode()), ff.get_attribute("http.response.status") or "200"


def test_handler_is_registered_and_ignores_other_actions():
    from tasks.ai import agent_actions
    from tasks.ai.actions.flow_editor import _handle_flow_editor
    assert _handle_flow_editor in agent_actions._ACTION_HANDLERS
    assert _handle_flow_editor(None, "something_else", {}, None, "alice", FlowFile()) is None


def test_requires_a_session_user():
    data, status = _call("flow_editor_task_catalog", {}, user_id="")
    assert status == "401" and "error" in data


def test_new_save_conflict_publish_get_roundtrip():
    data, status = _call("flow_editor_new", {
        "package": "my_flows", "name": "report", "version": "1.0.0", "scope": "user"})
    assert status == "200", data
    draft = data["draft"]
    definition = draft["definition"]
    definition["tasks"]["hello"] = {"type": "log", "parameters": {"message": "x"}}
    definition["entries"] = ["hello"]
    definition["future_feature"] = {"keep": True}

    data, status = _call("flow_editor_save_draft", {
        "draft_id": draft["draft_id"], "definition": definition, "base_revision": 0})
    assert status == "200" and data["revision"] == 1

    # Stale tab → 409, never last-writer-wins.
    data, status = _call("flow_editor_save_draft", {
        "draft_id": draft["draft_id"], "definition": definition, "base_revision": 0})
    assert status == "409" and data["error"] == "draft_changed_elsewhere"
    assert data["current_revision"] == 1
    data, status = _call("flow_editor_save_draft", {
        "draft_id": draft["draft_id"], "definition": definition})
    assert status == "400"

    data, status = _call("flow_editor_validate", {"definition": definition})
    assert status == "200" and data["ok"] is True
    data, status = _call("flow_editor_diff", {"draft_id": draft["draft_id"]})
    assert status == "200" and ("added", "task", "hello") in {
        (c["op"], c["kind"], c["id"]) for c in data["changes"]}

    data, status = _call("flow_editor_publish", {"draft_id": draft["draft_id"]})
    assert status == "200" and data["fqn"] == "my_flows.report:1.0.0", data
    data, status = _call("flow_editor_get", {"fqn": "my_flows.report", "scope": "user"})
    assert status == "200" and data["flow"]["future_feature"] == {"keep": True}
    data, status = _call("flow_editor_versions", {"fqn": "my_flows.report", "scope": "user"})
    assert data["versions"] == ["1.0.0"] and data["latest"] == "1.0.0"
    # The draft was consumed by publish.
    data, status = _call("flow_editor_load_draft", {"draft_id": draft["draft_id"]})
    assert status == "404"
    assert _call("flow_editor_list_drafts", {})[0]["drafts"] == []


def test_publish_answers_422_with_the_validation_report():
    draft = _call("flow_editor_new", {
        "package": "my_flows", "name": "broken", "version": "1.0.0", "scope": "user"})[0]["draft"]
    definition = draft["definition"]
    definition["tasks"]["a"] = {"type": "log", "parameters": {}}
    definition["relations"] = [{"from": "a", "to": "missing", "type": "success"}]
    _call("flow_editor_save_draft", {"draft_id": draft["draft_id"],
                                     "definition": definition, "base_revision": 0})
    data, status = _call("flow_editor_publish", {"draft_id": draft["draft_id"]})
    assert status == "422" and data["error"] == "validation_failed"
    assert data["validation"]["errors"] >= 1


def test_global_scope_writes_require_admin_and_drafts_are_private():
    body = {"package": "shared", "name": "flow", "version": "1.0.0", "scope": "global"}
    data, status = _call("flow_editor_new", body)
    assert status == "403"
    data, status = _call("flow_editor_new", body, roles="admin")
    assert status == "200", data
    draft_id = data["draft"]["draft_id"]
    data, status = _call("flow_editor_load_draft", {"draft_id": draft_id}, user_id="bob")
    assert status == "404"
    data, status = _call("flow_editor_create_draft",
                         {"fqn": "x.y", "scope": "conversation"})
    assert status == "403" and "conversation_id" in data["error"]


def test_catalogs_and_schema_for_current_parameters():
    data, status = _call("flow_editor_task_catalog", {})
    assert status == "200" and any(r["type"] == "log" for r in data["tasks"])
    data, status = _call("flow_editor_task_schema",
                         {"task_type": "log", "parameters": {"message": "${s}"}})
    assert status == "200" and data["type"] == "log"
    assert data["relationships"] == ["success"]
    data, status = _call("flow_editor_task_schema", {"task_type": "nope"})
    assert status == "404"
    data, status = _call("flow_editor_service_catalog", {})
    assert status == "200" and data["services"]
    service_type = data["services"][0]["type"]
    data, status = _call("flow_editor_service_schema",
                         {"service_type": service_type,
                          "parameters": {"provider": "configured"}})
    assert status == "200" and data["type"] == service_type


def test_delete_version_is_scope_gated_and_repoints_latest():
    draft = _call("flow_editor_new", {
        "package": "my_flows", "name": "v", "version": "1.0.0", "scope": "user"})[0]["draft"]
    definition = draft["definition"]
    definition["tasks"]["hello"] = {"type": "log", "parameters": {"message": "x"}}
    definition["entries"] = ["hello"]
    _call("flow_editor_save_draft", {"draft_id": draft["draft_id"],
                                     "definition": definition, "base_revision": 0})
    assert _call("flow_editor_publish", {"draft_id": draft["draft_id"]})[1] == "200"
    draft = _call("flow_editor_create_draft", {"fqn": "my_flows.v", "scope": "user"})[0]["draft"]
    assert _call("flow_editor_publish", {"draft_id": draft["draft_id"],
                                         "version": "2.0.0"})[1] == "200"

    data, status = _call("flow_editor_delete_version", {"scope": "user"})
    assert status == "400"
    data, status = _call("flow_editor_delete_version",
                         {"fqn": "my_flows.v:2.0.0", "scope": "global"})
    assert status == "403"
    data, status = _call("flow_editor_delete_version",
                         {"fqn": "my_flows.v:2.0.0", "scope": "user"}, user_id="bob")
    assert status == "404"
    data, status = _call("flow_editor_delete_version",
                         {"fqn": "my_flows.v:2.0.0", "scope": "user"})
    assert status == "200" and data["ok"] is True and data["latest"] == "1.0.0", data
    data, status = _call("flow_editor_versions", {"fqn": "my_flows.v", "scope": "user"})
    assert data["versions"] == ["1.0.0"] and data["latest"] == "1.0.0"
    # The last version is refused: the flow itself must be deleted instead.
    data, status = _call("flow_editor_delete_version",
                         {"fqn": "my_flows.v:1.0.0", "scope": "user"})
    assert status == "400" and "delete the flow" in data["error"]
