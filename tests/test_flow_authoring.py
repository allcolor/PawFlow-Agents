"""Flow Editor authoring foundation: drafts, optimistic locking, immutable
versions, round-trip of unknown fields, structured validation and diff."""

import copy

import pytest

from tasks import register_all_tasks
register_all_tasks()

from core.flow_authoring import (
    DraftConflict,
    DraftNotFound,
    FlowAuthoringService,
    FlowValidationFailed,
)
from core.flow_definition_validator import FlowDefinitionValidator


@pytest.fixture
def svc(tmp_path, monkeypatch):
    import core.paths as paths
    from core.repository import ScopedRepository
    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    ScopedRepository.reset()
    FlowAuthoringService.reset()
    yield FlowAuthoringService.instance()
    ScopedRepository.reset()
    FlowAuthoringService.reset()


def _definition():
    return {
        "id": "demo", "name": "demo", "version": "1.0.0",
        "description": "two logs",
        "parameters": {"greeting": {"type": "string", "default": "hi"}},
        "tasks": {
            "gen": {"type": "log", "parameters": {"message": "${greeting}"}},
            "out": {"type": "log", "parameters": {"message": "bye"}},
        },
        "relations": [{"from": "gen", "to": "out", "type": "success"}],
        "entries": ["gen"], "exits": ["out"],
        "runtime_links": [{"from": "gen", "to": "${port}", "type": "agentRuntime"}],
        "future_feature": {"x": 1},
    }


def _seed(svc, scope="user", user_id="alice"):
    from core.repository import ScopedRepository
    ScopedRepository.instance().create_flow(
        "my_flows.demo:1.0.0", scope, _definition(), user_id=user_id)
    return svc


# ── drafts ────────────────────────────────────────────────────────

def test_draft_create_save_conflict_reload_discard(svc):
    _seed(svc)
    draft = svc.create_draft("my_flows.demo", "user", "alice")
    assert draft["base_version"] == "1.0.0" and draft["revision"] == 0
    assert draft["reused"] is False
    assert draft["definition"]["future_feature"] == {"x": 1}
    assert "fqn" not in draft["definition"]          # repository keys stripped

    edited = copy.deepcopy(draft["definition"])
    edited["tasks"]["out"]["parameters"]["message"] = "ciao"
    saved = svc.save_draft(draft["draft_id"], "alice", edited, base_revision=0)
    assert saved["revision"] == 1

    # A second tab still on revision 0 must NOT win.
    with pytest.raises(DraftConflict) as exc:
        svc.save_draft(draft["draft_id"], "alice", edited, base_revision=0)
    assert exc.value.code == "draft_changed_elsewhere"
    assert exc.value.current_revision == 1

    reloaded = svc.load_draft(draft["draft_id"], "alice")
    assert reloaded["definition"]["tasks"]["out"]["parameters"]["message"] == "ciao"
    # Reopening the same flow surfaces the existing working copy.
    again = svc.create_draft("my_flows.demo", "user", "alice")
    assert again["draft_id"] == draft["draft_id"] and again["reused"] is True
    assert [d["draft_id"] for d in svc.list_drafts("alice")] == [draft["draft_id"]]

    assert svc.discard_draft(draft["draft_id"], "alice") is True
    assert svc.discard_draft(draft["draft_id"], "alice") is False
    with pytest.raises(DraftNotFound):
        svc.load_draft(draft["draft_id"], "alice")


def test_drafts_are_private_per_user(svc):
    _seed(svc)
    draft = svc.create_draft("my_flows.demo", "user", "alice")
    with pytest.raises(DraftNotFound):
        svc.load_draft(draft["draft_id"], "bob")
    with pytest.raises(DraftNotFound):
        svc.save_draft(draft["draft_id"], "bob", {}, base_revision=0)
    assert svc.discard_draft(draft["draft_id"], "bob") is False
    assert svc.list_drafts("bob") == []


# ── round-trip + versioning ───────────────────────────────────────

def test_round_trip_preserves_unknown_fields_and_versions_are_immutable(svc):
    from core.repository import ScopedRepository
    repo = ScopedRepository.instance()
    _seed(svc)
    original = repo.get_flow("my_flows.demo:1.0.0", "user", user_id="alice")

    draft = svc.create_draft("my_flows.demo", "user", "alice")
    # Save WITHOUT any change, then publish: nothing may be lost.
    svc.save_draft(draft["draft_id"], "alice", draft["definition"], base_revision=0)
    result = svc.publish(draft["draft_id"], "alice", "1.1.0")
    assert result["fqn"] == "my_flows.demo:1.1.0" and result["draft_discarded"]

    latest = repo.get_flow("my_flows.demo", "user", user_id="alice")
    assert latest["version"] == "1.1.0"
    for key in ("future_feature", "runtime_links", "parameters", "tasks",
                "relations", "entries", "exits", "description"):
        assert latest[key] == original[key], key
    # 1.0.0 is untouched, byte for byte.
    assert repo.get_flow("my_flows.demo:1.0.0", "user", user_id="alice") == original
    assert repo.list_flow_versions("my_flows.demo", "user", user_id="alice") == ["1.0.0", "1.1.0"]

    # Publishing over an existing version is refused (never overwrite).
    second = svc.create_draft("my_flows.demo", "user", "alice")
    with pytest.raises(ValueError):
        svc.publish(second["draft_id"], "alice", "1.0.0")
    assert repo.get_flow("my_flows.demo:1.0.0", "user", user_id="alice") == original
    with pytest.raises(ValueError):
        svc.publish(second["draft_id"], "alice", "not-a-version")


def test_publish_refuses_invalid_definitions(svc):
    _seed(svc)
    draft = svc.create_draft("my_flows.demo", "user", "alice")
    broken = copy.deepcopy(draft["definition"])
    broken["relations"].append({"from": "gen", "to": "nope", "type": "failure"})
    svc.save_draft(draft["draft_id"], "alice", broken, base_revision=0)
    with pytest.raises(FlowValidationFailed) as exc:
        svc.publish(draft["draft_id"], "alice", "1.1.0")
    codes = {p["code"] for p in exc.value.report["problems"]}
    assert "unknown_relation_target" in codes
    # The draft survives a refused publish.
    assert svc.load_draft(draft["draft_id"], "alice")["revision"] == 1


def test_new_flow_publishes_through_create_flow(svc):
    from core.repository import ScopedRepository
    draft = svc.new("my_flows", "daily_report", "1.0.0", "user", "alice",
                    description="fresh")
    assert draft["base_version"] == "" and draft["definition"]["tasks"] == {}
    assert draft["definition"]["layout"] == {}
    definition = copy.deepcopy(draft["definition"])
    definition["tasks"]["hello"] = {"type": "log", "parameters": {"message": "x"}}
    definition["entries"] = ["hello"]
    svc.save_draft(draft["draft_id"], "alice", definition, base_revision=0)
    result = svc.publish(draft["draft_id"], "alice")        # version from the draft
    assert result["version"] == "1.0.0"
    assert ScopedRepository.instance().list_flow_versions(
        "my_flows.daily_report", "user", user_id="alice") == ["1.0.0"]
    assert svc.versions("my_flows.daily_report", "user", user_id="alice")["latest"] == "1.0.0"
    # The flow now exists: `new` refuses to shadow it.
    with pytest.raises(ValueError):
        svc.new("my_flows", "daily_report", "2.0.0", "user", "alice")
    with pytest.raises(ValueError):
        svc.new("bad package!", "x", "1.0.0", "user", "alice")


def test_fork_read_only_flow_to_user_scope(svc):
    _seed(svc, scope="global", user_id="")
    draft = svc.fork("my_flows.demo", "global", "alice_flows", "demo_copy",
                     "1.0.0", "user", "alice")
    assert draft["forked_from"] == {"fqn": "my_flows.demo:1.0.0", "scope": "global"}
    assert draft["definition"]["id"] == "demo_copy"
    assert draft["definition"]["tasks"].keys() == {"gen", "out"}
    assert draft["definition"]["future_feature"] == {"x": 1}
    result = svc.publish(draft["draft_id"], "alice")
    assert result["scope"] == "user" and result["fqn"] == "alice_flows.demo_copy:1.0.0"
    # The diff of a fork is "everything added" (no base version).
    forked = svc.fork("my_flows.demo", "global", "alice_flows", "demo_copy2",
                      "1.0.0", "user", "alice")
    assert all(c["op"] == "added" for c in svc.diff_draft(forked["draft_id"], "alice")["changes"])


# ── diff ──────────────────────────────────────────────────────────

def test_diff_reports_structured_changes(svc):
    base = _definition()
    after = copy.deepcopy(base)
    after["tasks"]["validate"] = {"type": "log", "parameters": {"message": "v"}}
    after["tasks"]["out"]["parameters"]["message"] = "changed"
    after["relations"] = [
        {"from": "gen", "to": "validate", "type": "success"},
        {"from": "gen", "to": "validate", "type": "failure"},   # same pair, 2 queues
    ]
    after["parameters"]["timeout"] = {"type": "integer", "default": 5}
    after["exits"] = ["validate"]
    after["layout"] = {"nodes": {"gen": {"x": 1, "y": 2}}}
    after["description"] = "renamed"

    diff = svc.diff(base, after)
    rows = {(c["op"], c["kind"], c["id"]) for c in diff["changes"]}
    assert ("added", "task", "validate") in rows
    assert ("changed", "task", "out") in rows
    assert ("removed", "relation", "conn_gen__success__out") in rows
    assert ("added", "relation", "conn_gen__success__validate") in rows
    assert ("added", "relation", "conn_gen__failure__validate") in rows
    assert ("added", "parameter", "timeout") in rows
    assert ("removed", "exit", "out") in rows and ("added", "exit", "validate") in rows
    changed_out = next(c for c in diff["changes"] if c["kind"] == "task" and c["id"] == "out")
    assert changed_out["fields"] == ["parameters.message"]
    impact = {c["id"]: c["runtime_impact"] for c in diff["changes"]}
    assert impact["layout"] is False and impact["description"] is False
    assert impact["validate"] is True and diff["runtime_impact"] is True

    # Layout-only edits never imply a hot-swap.
    layout_only = copy.deepcopy(base)
    layout_only["layout"] = {"viewport": {"zoom": 0.9}}
    only = svc.diff(base, layout_only)
    assert only["count"] == 1 and only["runtime_impact"] is False
    assert svc.diff(base, copy.deepcopy(base)) == {"changes": [], "count": 0,
                                                   "runtime_impact": False}


# ── validator ─────────────────────────────────────────────────────

def test_validator_reports_structured_problems():
    report = FlowDefinitionValidator.validate({
        "tasks": {
            "a": {"type": "log", "parameters": {"message": "a"}},
            "b": {"type": "doesNotExist"},
            "c": {"parameters": {}},
            "lonely": {"type": "log"},
        },
        "relations": [
            {"from": "a", "to": "b", "type": "success"},
            {"from": "a", "to": "b", "type": "success"},
            {"from": "a", "to": "ghost"},
            "garbage",
        ],
        "entries": ["nope"], "exits": ["a"],
        "services": {"svc": {"type": "noSuchService"}, "untyped": {}},
        "groups": {"g": {"tasks": {"a": {"type": "log"}}}},
    })
    assert report["ok"] is False
    by_code = {}
    for p in report["problems"]:
        by_code.setdefault(p["code"], []).append(p)
    assert set(by_code) == {
        "missing_flow_name", "duplicate_task_id", "unknown_task_type",
        "missing_task_type", "duplicate_relation", "unknown_relation_target",
        "invalid_relation", "unknown_entry", "task_disconnected",
        "unknown_service_type", "missing_service_type",
        "missing_required_parameter",
    }
    # Only the bare `lonely` task lacks its required parameter(s).
    assert {p["entity_id"] for p in by_code["missing_required_parameter"]} == {"lonely"}
    assert all(p["field"] for p in by_code["missing_required_parameter"])
    dup = by_code["duplicate_relation"][0]
    assert dup["entity_type"] == "relation" and dup["entity_id"] == "conn_a__success__b"
    assert by_code["unknown_task_type"][0]["field"] == "type"
    assert {p["entity_id"] for p in by_code["task_disconnected"]} == {"c", "lonely"}
    assert report["errors"] + report["warnings"] == len(report["problems"])
    for p in report["problems"]:
        assert set(p) == {"severity", "code", "message", "entity_type", "entity_id", "field"}


def test_validator_is_static_and_keeps_two_relationships_apart():
    report = FlowDefinitionValidator.validate({
        "name": "static",
        "tasks": {
            "a": {"type": "log", "parameters": {"message": "${secret_that_does_not_exist}"}},
            "b": {"type": "log", "parameters": {"message": "ok"}},
        },
        "relations": [
            {"from": "a", "to": "b", "type": "success"},
            {"from": "a", "to": "b", "type": "failure"},
            {"source": "b", "target": "a", "relationships": ["retry"]},  # package shape
        ],
    })
    assert report == {"ok": True, "errors": 0, "warnings": 0, "problems": []}
    assert FlowDefinitionValidator.validate("nope")["problems"][0]["code"] == "invalid_definition"


def test_validator_reports_missing_embedded_service_parameters():
    from core import ServiceFactory

    service_type = next(
        service_type for service_type in ServiceFactory.list_types()
        if any(spec.get("required") for spec in
               FlowAuthoringService.service_schema(service_type)["schema"].values())
    )
    required = next(
        name for name, spec in FlowAuthoringService.service_schema(service_type)["schema"].items()
        if spec.get("required")
    )
    report = FlowDefinitionValidator.validate({
        "name": "embedded service validation",
        "tasks": {},
        "relations": [],
        "services": {"local": {"type": service_type, "parameters": {}}},
    })
    problem = next(item for item in report["problems"]
                   if item["code"] == "missing_required_service_parameter")
    assert problem["entity_type"] == "service"
    assert problem["entity_id"] == "local"
    assert problem["field"] == required


def test_validator_descends_into_inline_and_nested_process_groups():
    report = FlowDefinitionValidator.validate({
        "name": "Groups",
        "tasks": {},
        "groups": {"outer": {
            "tasks": {"inside": {"type": "log", "parameters": {}}},
            "relations": [{"from": "inside", "to": "missing", "type": "success"}],
            "input_ports": ["inside"],
            "child_groups": {"inner": {
                "id": "inner",
                "tasks": {"nested": {"type": "doesNotExist", "parameters": {}}},
                "relations": [],
            }},
        }},
        "relations": [],
    })
    by_code = {item["code"] for item in report["problems"]}
    assert "missing_required_parameter" in by_code
    assert "unknown_task_type" in by_code
    assert "unknown_relation_target" in by_code
    assert "invalid_group_port" in by_code


# ── catalogs ──────────────────────────────────────────────────────

def test_task_catalog_and_schema():
    catalog = FlowAuthoringService.task_catalog()
    log = next(row for row in catalog if row["type"] == "log")
    assert log["category"] == "System" and log["name"]
    assert catalog == sorted(catalog, key=lambda r: (r["category"], r["name"].lower()))
    schema = FlowAuthoringService.task_schema("log", {"message": "${x}"})
    assert schema["type"] == "log" and isinstance(schema["schema"], dict)
    assert schema["relationships"] == ["success"]
    routed = FlowAuthoringService.task_schema(
        "routeOnAttribute",
        {"routes": {"urgent": {}, "normal": {}},
         "default_relationship": "unmatched"},
    )
    assert routed["relationships"] == ["urgent", "normal", "unmatched"]
    with pytest.raises(KeyError):
        FlowAuthoringService.task_schema("doesNotExist")
    services = FlowAuthoringService.service_catalog()
    assert services and {"type", "name", "category"} <= set(services[0])
    with pytest.raises(KeyError):
        FlowAuthoringService.service_schema("doesNotExist")


def test_relation_queue_configuration_is_validated():
    definition = _definition()
    relation = definition["relations"][0]
    relation.update({
        "max_queue_size": 20,
        "max_queue_bytes": 4096,
        "flowfile_ttl_seconds": 30,
        "prioritizer": "fifo",
        "priority_attribute": "priority",
    })
    assert FlowDefinitionValidator.validate(definition)["ok"] is True

    for field, value in (
        ("max_queue_size", 0),
        ("max_queue_bytes", -1),
        ("flowfile_ttl_seconds", -1),
        ("prioritizer", "random"),
    ):
        broken = copy.deepcopy(definition)
        broken["relations"][0][field] = value
        report = FlowDefinitionValidator.validate(broken)
        assert report["ok"] is False
        assert any(p["code"] == "invalid_relation_setting"
                   and p["field"] == field for p in report["problems"])
