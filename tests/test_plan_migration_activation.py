"""WP9 global PlanStore migration activation saga."""

import json

import pytest

from core.confirmation_store import ConfirmationStore
from core.flow_run_store import FlowRunStore
from core.plan_migration import legacy_plan_digest
from core.plan_migration_activation import LegacyPlanMigrationActivator
from core.plan_migration_manifest import PlanMigrationManifestStore
from core.workflow_proposal_store import WorkflowProposalStore


class Scheduler:
    def __init__(self, schedules):
        self.schedules = {
            value["key"]: dict(value) for value in schedules}

    def cancel(self, key):
        return self.schedules.pop(key, None) is not None

    def get(self, key):
        value = self.schedules.get(key)
        return dict(value) if value else None

    def schedule(self, conversation_id, recheck_at, user_id="", reason="", key=""):
        self.schedules[key] = {
            "conversation_id": conversation_id,
            "key": key,
            "recheck_at": recheck_at,
            "user_id": user_id,
            "reason": reason,
            "created_at": 20.0,
        }


class Repository:
    def __init__(self, authoring):
        self.authoring = authoring

    def delete(self, rtype, name, scope, user_id="", conv_id=""):
        assert (rtype, scope) == ("flow", "conv")
        key = (f"{name}:1.0.0", user_id, conv_id)
        return self.authoring.flows.pop(key, None) is not None


class Authoring:
    def __init__(self):
        self.flows = {}
        self.repo = Repository(self)

    def versions(self, fqn, scope, user_id="", conv_id=""):
        key = (fqn, user_id, conv_id)
        return {
            "flow": fqn.rsplit(":", 1)[0],
            "scope": scope,
            "versions": ["1.0.0"] if key in self.flows else [],
            "latest": "1.0.0" if key in self.flows else "",
        }

    def load(self, fqn, scope, user_id="", conv_id=""):
        assert scope == "conv"
        return self.flows[(fqn, user_id, conv_id)]


def _plan(plan_id, status, step_status, **extra):
    value = {
        "id": plan_id,
        "conversation_id": "conv-1",
        "title": f"Plan {plan_id}",
        "status": status,
        "created_by": "user",
        "created_at": 10.0,
        "updated_at": 20.0,
        "steps": [{
            "index": 1,
            "description": "Build",
            "status": step_status,
            "note": "ready",
        }],
    }
    value.update(extra)
    return value


def _environment(tmp_path, monkeypatch):
    source = tmp_path / "plans"
    directory = source / "user-a" / "conv-1"
    directory.mkdir(parents=True)
    schedule = {
        "conversation_id": "conv-1",
        "key": "conv-1::plan::p_wait::verify1::reviewer",
        "recheck_at": 30.0,
        "user_id": "user-a",
        "reason": "[plan_verify:p_wait:1:builder] (reviewer)",
        "created_at": 20.0,
    }
    plans = [
        (_plan("p_done", "completed", "done"), "completed", None, {}),
        (_plan("p_review", "pending_approval", "pending"),
         "pending_approval", None, {}),
        (_plan("p_ready", "approved", "pending"),
         "approved_not_started", None, {}),
        (_plan(
            "p_wait", "in_progress", "pending_verification",
            created_by="builder", verifier="reviewer"),
         "waiting_verification", {
             "schema_version": 1,
             "kind": "legacy_plan_verification",
             "plan_id": "p_wait",
             "step": 1,
             "executor": "builder",
             "verifier": "reviewer",
             "schedule": schedule,
         }, {
             "builder": {"adapter": "invokeWorkflowAgent"},
             "reviewer": {"adapter": "invokeWorkflowAgent"},
         }),
    ]
    records = []
    for plan, classification, checkpoint, adapters in plans:
        relative = f"user-a/conv-1/{plan['id']}.json"
        (source / relative).write_text(json.dumps(plan), encoding="utf-8")
        records.append({
            "source_path": relative,
            "source_digest": legacy_plan_digest(plan),
            "user_id": "user-a",
            "conversation_id": "conv-1",
            "plan_id": plan["id"],
            "classification": classification,
            "assigned_agents": sorted(adapters),
            "agent_adapters": adapters,
            "checkpoint": checkpoint,
            "created_at": 10.0,
            "updated_at": 20.0,
        })
    report = {
        "schema_version": 1,
        "source_root": source.as_posix(),
        "record_count": len(records),
        "counts": {},
        "records": records,
        "blockers": [],
        "warnings": [],
        "activation_allowed": True,
    }
    manifests = PlanMigrationManifestStore(
        tmp_path / "manifests", activation_enabled=lambda: True)
    prepared = manifests.prepare(report)
    waits = ConfirmationStore(tmp_path / "waits.sqlite3")
    monkeypatch.setattr(waits, "ensure_sweeper", lambda: None)
    return {
        "source": source,
        "manifest_store": manifests,
        "migration_id": prepared["migration_id"],
        "flow_runs": FlowRunStore(tmp_path / "runs.sqlite3"),
        "proposals": WorkflowProposalStore(tmp_path / "proposals.sqlite3"),
        "waits": waits,
        "scheduler": Scheduler([schedule]),
        "authoring": Authoring(),
    }


def _activator(environment):
    authoring = environment["authoring"]

    def publish(conversion, *, authoring):
        imported = conversion["imported_plan"]
        fqn = conversion["flow"]["fqn"]
        ref = {
            "schema_version": 1,
            "resource_type": "flow",
            "name": fqn,
            "scope": "conversation",
            "owner_id": imported["user_id"],
            "package_id": None,
            "package_version": None,
            "version": "1.0.0",
            "content_digest": conversion["source_digest"],
            "source_id": f"repository:conversation:{fqn}",
        }
        authoring.flows[(
            fqn, imported["user_id"], imported["conversation_id"])] = {
                "migration": {
                    "source_digest": conversion["source_digest"],
                },
            }
        return ref

    return LegacyPlanMigrationActivator(
        manifest_store=environment["manifest_store"],
        authoring=authoring,
        flow_runs=environment["flow_runs"],
        proposals=environment["proposals"],
        waits=environment["waits"],
        scheduler=environment["scheduler"],
        publisher=publish,
    )


def _authorization():
    return {
        "context_id": "migration-authority",
        "revision": 1,
        "root_turn_id": "migration-turn",
    }


def test_activation_imports_every_representable_state_and_rolls_back(
        tmp_path, monkeypatch):
    environment = _environment(tmp_path, monkeypatch)
    activator = _activator(environment)

    active = activator.activate(
        environment["migration_id"], authorization_ref=_authorization())

    assert active["state"] == "active"
    assert len(environment["authoring"].flows) == 4
    assert len(environment["flow_runs"].list("conv-1")) == 3
    assert len(environment["proposals"].list(
        user_id="user-a", conversation_id="conv-1")) == 4
    assert len(environment["waits"].list_waits()) == 1
    assert environment["scheduler"].schedules == {}

    rolled_back = environment["manifest_store"].rollback(
        environment["migration_id"], remove_artifact=activator.remove_artifact)

    assert rolled_back["state"] == "rolled_back"
    assert environment["authoring"].flows == {}
    assert environment["flow_runs"].list("conv-1") == []
    assert environment["proposals"].list(
        user_id="user-a", conversation_id="conv-1") == []
    assert environment["waits"].list_waits() == []
    assert list(environment["scheduler"].schedules) == [
        "conv-1::plan::p_wait::verify1::reviewer"]


def test_failed_manifest_commit_restores_schedule_and_compensates_batch(
        tmp_path, monkeypatch):
    environment = _environment(tmp_path, monkeypatch)
    activator = _activator(environment)

    def fail_activate(*_args, **_kwargs):
        raise RuntimeError("manifest commit failed")

    monkeypatch.setattr(
        environment["manifest_store"], "activate", fail_activate)
    with pytest.raises(RuntimeError, match="manifest commit failed"):
        activator.activate(
            environment["migration_id"], authorization_ref=_authorization())

    assert environment["authoring"].flows == {}
    assert environment["flow_runs"].list("conv-1") == []
    assert environment["proposals"].list(
        user_id="user-a", conversation_id="conv-1") == []
    assert environment["waits"].list_waits() == []
    assert list(environment["scheduler"].schedules) == [
        "conv-1::plan::p_wait::verify1::reviewer"]
