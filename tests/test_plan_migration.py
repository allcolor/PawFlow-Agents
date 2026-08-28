"""WP9 characterization for legacy PlanStore migration preflight."""

import json

import pytest

import core.paths as core_paths
from core.flow_run_store import FlowRunStore
from core.plan_migration import (
    LegacyPlanClass,
    LegacyPlanMigrationPreflight,
    build_legacy_conversion_plan,
    classify_legacy_plan,
    legacy_plan_digest,
    resolve_legacy_agent_adapter,
    resolve_legacy_plan_checkpoint,
)
from core.plan_migration_manifest import PlanMigrationManifestStore
from core.plan_migration_runtime import (
    prepare_legacy_plan_migration,
    run_legacy_plan_preflight,
)
from core.workflow_proposal_store import WorkflowProposalStore


def _plan(status="approved", steps=None, **extra):
    value = {
        "id": "p_1234",
        "conversation_id": "conv-1",
        "title": "Legacy plan",
        "status": status,
        "created_at": 100.0,
        "assigned_to": ["builder"],
        "steps": steps if steps is not None else [
            {
                "index": 1,
                "description": "Build",
                "status": "pending",
                "assigned_to": "builder",
            },
        ],
    }
    value.update(extra)
    return value


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (_plan(status="completed", steps=[{"index": 1, "description": "A", "status": "done"}]),
         LegacyPlanClass.COMPLETED),
        (_plan(status="cancelled"), LegacyPlanClass.CANCELLED),
        (_plan(status="pending_approval"), LegacyPlanClass.PENDING_APPROVAL),
        (_plan(status="approved"), LegacyPlanClass.APPROVED_NOT_STARTED),
        (_plan(status="in_progress", steps=[{
            "index": 1, "description": "A", "status": "in_progress",
            "assigned_to": "builder",
        }]), LegacyPlanClass.IN_PROGRESS),
        (_plan(status="active", steps=[{
            "index": 1, "description": "A", "status": "pending_verification",
            "assigned_to": "builder", "verifier": "reviewer",
        }]), LegacyPlanClass.WAITING_VERIFICATION),
        (_plan(status="failed", steps=[{
            "index": 1, "description": "A", "status": "error",
            "assigned_to": "builder",
        }]), LegacyPlanClass.FAILED),
    ],
)
def test_classifies_every_required_legacy_state(plan, expected):
    assert classify_legacy_plan(plan) is expected


@pytest.mark.parametrize(
    "plan",
    [
        _plan(status="mystery"),
        _plan(status="completed", steps=[{
            "index": 1, "description": "A", "status": "in_progress",
        }]),
        _plan(status="active", steps=[
            {"index": 1, "description": "A", "status": "in_progress"},
            {"index": 2, "description": "B", "status": "in_progress"},
        ]),
        _plan(status="active", steps=[]),
    ],
)
def test_ambiguous_or_unrepresentable_state_is_rejected(plan):
    with pytest.raises(ValueError):
        classify_legacy_plan(plan)


def test_digest_is_canonical_and_detects_source_changes():
    first = _plan()
    reordered = json.loads(json.dumps(first, sort_keys=True))
    assert legacy_plan_digest(first) == legacy_plan_digest(reordered)
    reordered["title"] = "Changed"
    assert legacy_plan_digest(first) != legacy_plan_digest(reordered)


def test_inventory_derives_scope_from_paths_and_blocks_active_missing_adapter(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "p_1234.json").write_text(
        json.dumps(_plan()), encoding="utf-8")

    report = LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=lambda _name, _user, _conv: None,
    ).run()

    assert report["counts"] == {"approved_not_started": 1}
    assert report["records"][0]["user_id"] == "user-a"
    assert report["records"][0]["conversation_id"] == "conv-1"
    assert report["records"][0]["source_digest"]
    assert report["records"][0]["assigned_agents"] == ["builder"]
    assert report["activation_allowed"] is False
    assert report["blockers"][0]["code"] == "missing_exact_agent_adapter"


def test_terminal_record_keeps_unresolved_assignment_as_warning(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    terminal = _plan(
        status="completed",
        steps=[{"index": 1, "description": "A", "status": "done",
                "assigned_to": "retired-agent"}],
        assigned_to=["retired-agent"],
    )
    (plan_dir / "p_1234.json").write_text(json.dumps(terminal), encoding="utf-8")

    report = LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=lambda _name, _user, _conv: None,
    ).run()

    assert report["activation_allowed"] is True
    assert report["blockers"] == []
    assert report["warnings"][0]["code"] == "unresolved_terminal_assignment"


def test_exact_adapter_metadata_is_captured_for_active_plan(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "p_1234.json").write_text(
        json.dumps(_plan()), encoding="utf-8")

    def resolve(name, _user, _conv):
        return {
            "agent": name,
            "adapter": "invokeAgent",
            "resource_ref": {"name": name, "version": "1"},
        }

    report = LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=resolve,
    ).run()

    assert report["activation_allowed"] is True
    assert report["records"][0]["agent_adapters"]["builder"]["adapter"] == "invokeAgent"


def test_plan_level_verifier_requires_exact_adapter_for_live_plan(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    plan = _plan(verifier="reviewer")
    (plan_dir / "p_1234.json").write_text(json.dumps(plan), encoding="utf-8")

    report = LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=lambda name, _user, _conv: (
            {"agent": name, "adapter": "invokeAgent"}
            if name == "builder" else None),
    ).run()

    assert report["records"][0]["assigned_agents"] == ["builder", "reviewer"]
    assert report["blockers"][0]["agent"] == "reviewer"


@pytest.mark.parametrize(
    ("step_status", "classification"),
    [
        ("in_progress", LegacyPlanClass.IN_PROGRESS),
        ("pending_verification", LegacyPlanClass.WAITING_VERIFICATION),
    ],
)
def test_mid_step_live_state_requires_checkpoint_mapping(
    tmp_path, step_status, classification,
):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    plan = _plan(
        status="in_progress",
        steps=[{
            "index": 1,
            "description": "A",
            "status": step_status,
            "assigned_to": "builder",
            "verifier": "reviewer" if step_status == "pending_verification" else "",
        }],
    )
    (plan_dir / "p_1234.json").write_text(json.dumps(plan), encoding="utf-8")

    adapters = lambda name, _user, _conv: {
        "agent": name, "adapter": "invokeAgent"}
    blocked = LegacyPlanMigrationPreflight(
        plans_dir=source, resolve_agent_adapter=adapters).run()
    assert blocked["records"][0]["classification"] == classification.value
    assert blocked["blockers"][0]["code"] == "missing_active_checkpoint_adapter"

    mapped = LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=adapters,
        resolve_active_checkpoint=lambda record, _user, _conv: {
            "kind": "legacy_plan_checkpoint",
            "plan_id": record["id"],
            "step": 1,
        },
    ).run()
    assert mapped["activation_allowed"] is True
    assert mapped["records"][0]["checkpoint"]["step"] == 1


def test_corrupt_and_scope_mismatched_records_block_activation(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "broken.json").write_text("{", encoding="utf-8")
    mismatch = _plan()
    mismatch["conversation_id"] = "other"
    (plan_dir / "p_1234.json").write_text(json.dumps(mismatch), encoding="utf-8")

    report = LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=lambda name, _user, _conv: {
            "agent": name, "adapter": "invokeAgent"},
    ).run()

    assert report["activation_allowed"] is False
    assert {item["code"] for item in report["blockers"]} == {
        "corrupt_plan", "conversation_scope_mismatch",
    }


def test_preflight_is_deterministic(tmp_path):
    source = tmp_path / "plans"
    for user, conv, plan in [
        ("z-user", "conv-2", _plan(status="cancelled")),
        ("a-user", "conv-1", _plan(status="completed", steps=[
            {"index": 1, "description": "A", "status": "done"}])),
    ]:
        path = source / user / conv
        path.mkdir(parents=True)
        (path / f"{plan['id']}.json").write_text(json.dumps(plan), encoding="utf-8")

    preflight = LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=lambda _name, _user, _conv: None)
    assert preflight.run() == preflight.run()


@pytest.mark.parametrize(
    ("classification", "proposal_status", "run_status", "mode"),
    [
        ("completed", "completed", "completed", "archive"),
        ("cancelled", "cancelled", "cancelled", "archive"),
        ("failed", "failed", "failed", "archive"),
        ("pending_approval", "user_review", None, "review"),
        ("approved_not_started", "accepted", "created", "resume"),
        ("in_progress", "running", "running", "resume"),
        ("waiting_verification", "running", "waiting", "resume"),
    ],
)
def test_conversion_plan_projects_proposal_and_run_states(
    classification, proposal_status, run_status, mode,
):
    source_by_class = {
        "completed": _plan(
            status="completed",
            steps=[{"index": 1, "description": "Build", "status": "done",
                    "assigned_to": "builder"}]),
        "cancelled": _plan(status="cancelled"),
        "failed": _plan(
            status="failed",
            steps=[{"index": 1, "description": "Build", "status": "error",
                    "assigned_to": "builder"}]),
        "pending_approval": _plan(status="pending_approval"),
        "approved_not_started": _plan(status="approved"),
        "in_progress": _plan(
            status="in_progress",
            steps=[{"index": 1, "description": "Build",
                    "status": "in_progress", "assigned_to": "builder"}]),
        "waiting_verification": _plan(
            status="in_progress",
            steps=[{"index": 1, "description": "Build",
                    "status": "pending_verification",
                    "assigned_to": "builder", "verifier": "reviewer"}]),
    }
    plan = source_by_class[classification]
    record = {
        "source_path": "user-a/conv-1/p_1234.json",
        "source_digest": legacy_plan_digest(plan),
        "user_id": "user-a",
        "conversation_id": "conv-1",
        "plan_id": "p_1234",
        "classification": classification,
        "agent_adapters": {
            "builder": {"agent": "builder", "adapter": "invokeAgent"},
        },
        "checkpoint": (
            {"kind": "legacy", "step": 1}
            if classification in {"in_progress", "waiting_verification"}
            else None),
    }

    converted = build_legacy_conversion_plan(record, plan)

    assert converted["mode"] == mode
    assert converted["proposal"]["status"] == proposal_status
    assert converted["run"]["status"] == run_status if run_status else converted["run"] is None
    assert converted["flow"]["fqn"].endswith(":1.0.0")
    assert converted["flow"]["scope"] == "conversation"
    assert converted["flow"]["owner_id"] == "conv-1"
    assert converted["imported_plan"]["source_id"] == "p_1234"


def test_conversion_plan_preserves_order_and_effective_roles():
    plan = _plan(
        assigned_to=[],
        created_by="planner",
        verifier="reviewer",
        steps=[
            {"index": 2, "description": "Review", "status": "pending",
             "assigned_to": "specialist", "verifier": ""},
            {"index": 1, "description": "Draft", "status": "pending",
             "assigned_to": "", "verifier": ""},
        ],
    )
    record = {
        "source_path": "user-a/conv-1/p_1234.json",
        "source_digest": legacy_plan_digest(plan),
        "user_id": "user-a",
        "conversation_id": "conv-1",
        "plan_id": "p_1234",
        "classification": "approved_not_started",
        "agent_adapters": {
            name: {"agent": name, "adapter": "invokeAgent"}
            for name in ("planner", "reviewer", "specialist")
        },
        "checkpoint": None,
    }

    converted = build_legacy_conversion_plan(record, plan)

    assert [step["index"] for step in converted["steps"]] == [1, 2]
    assert converted["steps"][0]["executor"] == "planner"
    assert converted["steps"][0]["verifier"] == "reviewer"
    assert converted["steps"][1]["executor"] == "specialist"


def test_conversion_plan_rejects_source_drift():
    plan = _plan()
    record = {
        "source_path": "user-a/conv-1/p_1234.json",
        "source_digest": legacy_plan_digest(plan),
        "user_id": "user-a",
        "conversation_id": "conv-1",
        "plan_id": "p_1234",
        "classification": "approved_not_started",
        "agent_adapters": {},
        "checkpoint": None,
    }
    plan["title"] = "Changed after preflight"

    with pytest.raises(ValueError, match="changed after preflight"):
        build_legacy_conversion_plan(record, plan)


def _preflight_report(source):
    return LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=lambda name, _user, _conv: {
            "agent": name, "adapter": "invokeAgent"},
    ).run()


def _activate_manifest(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(core_paths, "RUNTIME_DIR", runtime)
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "p_1234.json").write_text(
        json.dumps(_plan()), encoding="utf-8")
    store = PlanMigrationManifestStore(
        runtime / "plan_migrations", activation_enabled=lambda: True)
    prepared = store.prepare(_preflight_report(source))
    store.activate(prepared["migration_id"], artifacts=[])
    return store, prepared["migration_id"]


def test_manifest_preparation_backs_up_exact_source_and_is_idempotent(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "p_1234.json"
    raw = json.dumps(_plan(), indent=2)
    plan_path.write_text(raw, encoding="utf-8")
    store = PlanMigrationManifestStore(
        tmp_path / "migration", activation_enabled=lambda: True)

    first = store.prepare(_preflight_report(source))
    second = store.prepare(_preflight_report(source))

    assert first["migration_id"] == second["migration_id"]
    assert second["idempotent"] is True
    backup = (
        tmp_path / "migration" / first["migration_id"] / "backups"
        / "user-a" / "conv-1" / "p_1234.json")
    assert backup.read_text(encoding="utf-8") == raw


def test_manifest_preparation_rejects_source_change(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "p_1234.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    report = _preflight_report(source)
    changed = _plan()
    changed["title"] = "Changed"
    plan_path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(ValueError, match="changed after preflight"):
        PlanMigrationManifestStore(
            tmp_path / "migration", activation_enabled=lambda: True,
        ).prepare(report)


def test_blocked_report_cannot_activate(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "p_1234.json"
    plan_path.write_text(json.dumps(_plan()), encoding="utf-8")
    report = LegacyPlanMigrationPreflight(
        plans_dir=source,
        resolve_agent_adapter=lambda _name, _user, _conv: None).run()
    store = PlanMigrationManifestStore(
        tmp_path / "migration", activation_enabled=lambda: True)
    prepared = store.prepare(report)

    with pytest.raises(ValueError, match="blockers"):
        store.activate(prepared["migration_id"], artifacts=[])


def test_manifest_rollback_restores_sources_before_first_new_write(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "p_1234.json"
    raw = json.dumps(_plan(), indent=2)
    plan_path.write_text(raw, encoding="utf-8")
    store = PlanMigrationManifestStore(
        tmp_path / "migration", activation_enabled=lambda: True)
    prepared = store.prepare(_preflight_report(source))
    artifacts = [
        {"kind": "flow", "id": "legacy_plans.plan:1.0.0"},
        {"kind": "proposal", "id": "wp_legacy_1"},
    ]
    store.activate(prepared["migration_id"], artifacts=artifacts)
    plan_path.unlink()
    removed = []

    rolled_back = store.rollback(
        prepared["migration_id"], remove_artifact=removed.append)

    assert rolled_back["state"] == "rolled_back"
    assert removed == list(reversed(artifacts))
    assert plan_path.read_text(encoding="utf-8") == raw


def test_manifest_rollback_is_fenced_after_first_new_write(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "p_1234.json").write_text(
        json.dumps(_plan()), encoding="utf-8")
    store = PlanMigrationManifestStore(
        tmp_path / "migration", activation_enabled=lambda: True)
    prepared = store.prepare(_preflight_report(source))
    store.activate(prepared["migration_id"], artifacts=[])
    store.mark_first_write(prepared["migration_id"])

    with pytest.raises(ValueError, match="first post-activation write"):
        store.rollback(prepared["migration_id"])


def test_live_proposal_write_fences_active_migration(tmp_path, monkeypatch):
    manifests, migration_id = _activate_manifest(tmp_path, monkeypatch)

    WorkflowProposalStore(tmp_path / "proposals.sqlite3").create(
        user_id="user-a",
        conversation_id="conv-1",
        title="Canonical proposal",
        summary="",
        draft_id="fd_live",
        draft_revision=1,
        digest="a" * 64,
        created_by="planner",
    )

    assert manifests.get(migration_id)["first_write_at"] is not None
    with pytest.raises(ValueError, match="first post-activation write"):
        manifests.rollback(migration_id)


def test_live_flow_run_write_fences_active_migration(tmp_path, monkeypatch):
    manifests, migration_id = _activate_manifest(tmp_path, monkeypatch)

    FlowRunStore(tmp_path / "runs.sqlite3").create(
        user_id="user-a",
        conversation_id="conv-1",
        flow_ref={
            "schema_version": 1,
            "resource_type": "flow",
            "name": "legacy.plan:1.0.0",
            "scope": "conversation",
            "owner_id": "user-a",
            "version": "1.0.0",
            "content_digest": "b" * 64,
            "source_id": "repository:conversation:legacy.plan:1.0.0",
        },
        authorization_ref={
            "context_id": "6b3a346a-7ffd-4ae1-b9bf-949d0c90f284",
            "revision": 1,
            "root_turn_id": "turn-1",
        },
        execution_authority={
            "agent_name": "assistant",
            "permission_mode": "default",
            "allowed_effects": ["resource.write"],
            "service_snapshot": {"bindings": {}, "services": {}},
        },
        input_snapshot={"content": "", "attributes": {}},
        parameters={},
    )

    assert manifests.get(migration_id)["first_write_at"] is not None
    with pytest.raises(ValueError, match="first post-activation write"):
        manifests.rollback(migration_id)


def test_manifest_activation_is_fenced_by_server_flag(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "p_1234.json").write_text(
        json.dumps(_plan()), encoding="utf-8")
    store = PlanMigrationManifestStore(
        tmp_path / "migration", activation_enabled=lambda: False)
    prepared = store.prepare(_preflight_report(source))

    with pytest.raises(ValueError, match="disabled by the server"):
        store.activate(prepared["migration_id"], artifacts=[])


class _Resources:
    def __init__(self, definition):
        self.definition = definition

    def get_any(self, resource_type, name, user_id, conversation_id=""):
        assert (resource_type, name, user_id, conversation_id) == (
            "agent", "builder", "user-a", "conv-1")
        return self.definition


def test_production_agent_adapter_resolves_only_workflow_runtime():
    workflow = {
        "name": "builder",
        "prompt": "Build",
        "_scope": "user",
        "version": "2",
        "runtime_defaults": {
            "kind": "workflow",
            "workflow": {
                "flow_fqn": "agents.builder:1.0.0",
                "input_port": "request",
                "terminal_port": "terminal",
                "preempt_policy": "queue",
            },
        },
    }
    adapter = resolve_legacy_agent_adapter(
        "builder", "user-a", "conv-1",
        resource_store=_Resources(workflow),
        workflow_resolver=lambda *_args: {
            "flow_ref": {"name": "agents.builder:1.0.0"}})

    assert adapter["adapter"] == "invokeWorkflowAgent"
    assert adapter["runtime_kind"] == "workflow"
    assert adapter["agent_ref"]["resource_type"] == "agent"
    assert adapter["agent_ref"]["content_digest"]
    assert adapter["workflow"]["flow_fqn"] == "agents.builder:1.0.0"
    assert adapter["flow_ref"]["name"] == "agents.builder:1.0.0"

    prompt_only = {**workflow, "runtime_defaults": {"kind": "llm"}}
    assert resolve_legacy_agent_adapter(
        "builder", "user-a", "conv-1",
        resource_store=_Resources(prompt_only),
        workflow_resolver=lambda *_args: {
            "flow_ref": {"name": "agents.builder:1.0.0"}}) is None


def test_verification_schedule_maps_to_exact_checkpoint_only():
    plan = _plan(
        status="in_progress",
        verifier="reviewer",
        steps=[{
            "index": 1,
            "description": "Build",
            "status": "pending_verification",
            "assigned_to": "builder",
            "verifier": "",
        }],
    )
    schedules = [{
        "conversation_id": "conv-1",
        "key": "conv-1::plan::p_1234::verify1::reviewer",
        "recheck_at": 123.0,
        "user_id": "user-a",
        "reason": "[plan_verify:p_1234:1:builder] (reviewer)",
    }]

    checkpoint = resolve_legacy_plan_checkpoint(plan, schedules)

    assert checkpoint == {
        "schema_version": 1,
        "kind": "legacy_plan_verification",
        "plan_id": "p_1234",
        "step": 1,
        "executor": "builder",
        "verifier": "reviewer",
        "schedule": schedules[0],
    }
    assert resolve_legacy_plan_checkpoint(
        {**plan, "steps": [{**plan["steps"][0], "status": "in_progress"}]},
        schedules,
    ) is None
    assert resolve_legacy_plan_checkpoint(plan, schedules + [dict(schedules[0])]) is None


class _Scheduler:
    def __init__(self, schedules=()):
        self.schedules = list(schedules)

    def list_all(self):
        return list(self.schedules)


def test_runtime_preflight_and_prepare_use_exact_production_resolvers(tmp_path):
    source = tmp_path / "plans"
    plan_dir = source / "user-a" / "conv-1"
    plan_dir.mkdir(parents=True)
    (plan_dir / "p_1234.json").write_text(
        json.dumps(_plan()), encoding="utf-8")
    workflow = {
        "name": "builder",
        "prompt": "Build",
        "_scope": "user",
        "runtime_defaults": {
            "kind": "workflow",
            "workflow": {
                "flow_fqn": "agents.builder:1.0.0",
                "input_port": "request",
                "terminal_port": "terminal",
                "preempt_policy": "queue",
            },
        },
    }
    flow = lambda *_args: {
        "flow_ref": {"name": "agents.builder:1.0.0"}}

    report = run_legacy_plan_preflight(
        plans_dir=source,
        resource_store=_Resources(workflow),
        scheduler=_Scheduler(),
        workflow_resolver=flow,
    )
    prepared = prepare_legacy_plan_migration(
        manifest_root=tmp_path / "migration",
        plans_dir=source,
        resource_store=_Resources(workflow),
        scheduler=_Scheduler(),
        workflow_resolver=flow,
    )

    assert report["activation_allowed"] is True
    assert report["records"][0]["agent_adapters"]["builder"]["agent_ref"]
    assert prepared["state"] == "prepared"
    assert prepared["report"] == report
