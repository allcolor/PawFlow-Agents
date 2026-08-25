import copy
import time
from unittest.mock import MagicMock

import pytest

from core import FlowFile
from core.flow_definition_validator import FlowDefinitionValidator
from core.flow_run_coordinator import FlowRunCoordinator
from core.flow_run_store import FlowRunStore
from engine import FlowParser
from engine.continuous_executor import ContinuousFlowExecutor
from tasks import register_all_tasks
from tasks.control.complete_flow_run import CompleteFlowRunTask


def _ref():
    return {
        "schema_version": 1, "resource_type": "flow",
        "name": "demo.oneshot:1.0.0", "scope": "conversation",
        "owner_id": "conversation-1", "version": "1.0.0",
        "content_digest": "b" * 64,
        "source_id": "repository:conversation:demo.oneshot:1.0.0",
    }


def _create(store):
    return store.create(
        user_id="alice", conversation_id="conversation-1",
        flow_ref=_ref(), authorization_ref={"root_turn_id": "turn-1"},
        input_snapshot={"content": "hello", "attributes": {"x": "1"}},
        parameters={"mode": "safe"}, proposal_id="proposal-1",
    )


def test_flow_run_store_commits_exactly_one_terminal_and_outbox(tmp_path):
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    run = _create(store)
    store.transition(run["run_id"], "starting")
    store.transition(run["run_id"], "running")
    terminal = {"schema_version": 1, "summary": "done", "artifacts": []}
    staged = store.stage_terminal(run["run_id"], terminal)
    assert staged["status"] == "committing"
    assert store.stage_terminal(run["run_id"], terminal)["terminal"] == terminal
    with pytest.raises(ValueError, match="different terminal"):
        store.stage_terminal(run["run_id"], {**terminal, "summary": "other"})
    committed = store.commit(run["run_id"])
    assert committed["status"] == "completed"
    assert store.pending_events()[0]["terminal"] == terminal
    with pytest.raises(ValueError, match="invalid flow run transition"):
        store.transition(run["run_id"], "running")


def test_import_terminal_run_is_idempotent_history_without_outbox(tmp_path):
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    arguments = {
        "run_id": "fr_legacy_abc",
        "user_id": "alice",
        "conversation_id": "conversation-1",
        "flow_ref": _ref(),
        "proposal_id": "wp_legacy_abc",
        "status": "completed",
        "terminal": {
            "imported": True,
            "legacy_plan_id": "p_1234",
            "step_statuses": [{"index": 1, "status": "done", "note": "ok"}],
        },
        "import_metadata": {
            "schema_version": 1,
            "source_type": "legacy_plan",
            "source_id": "p_1234",
            "source_digest": "a" * 64,
        },
        "created_at": 10.0,
        "terminal_at": 20.0,
    }

    imported = store.import_terminal(**arguments)
    retried = store.import_terminal(**arguments)

    assert imported == retried
    assert imported["status"] == "completed"
    assert imported["import_metadata"]["source_id"] == "p_1234"
    assert imported["created_at"] == 10.0
    assert imported["terminal_at"] == 20.0
    assert store.pending_events() == []
    with pytest.raises(ValueError, match="different imported run"):
        store.import_terminal(**{
            **arguments,
            "terminal": {**arguments["terminal"], "legacy_plan_id": "p_other"},
        })


@pytest.mark.parametrize("status", ["created", "running", "waiting"])
def test_import_terminal_run_rejects_non_terminal_status(tmp_path, status):
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    with pytest.raises(ValueError, match="terminal"):
        store.import_terminal(
            run_id="fr_legacy_abc",
            user_id="alice",
            conversation_id="conversation-1",
            flow_ref=_ref(),
            proposal_id="wp_legacy_abc",
            status=status,
            terminal={"imported": True},
            import_metadata={"source_type": "legacy_plan"},
            created_at=10.0,
            terminal_at=20.0,
        )


def test_flow_run_replay_has_new_identity_and_current_authorization(tmp_path):
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    original = _create(store)
    store.transition(original["run_id"], "failed", "fixture terminal")
    replay = FlowRunCoordinator(store).replay(
        original["run_id"], authorization_ref={"root_turn_id": "turn-2"})
    assert replay["run_id"] != original["run_id"]
    assert replay["deployment_instance_id"] != original["deployment_instance_id"]
    assert replay["generation"] == 2
    assert replay["replay_of"] == original["run_id"]
    assert replay["authorization_ref"] == {"root_turn_id": "turn-2"}
    assert replay["input"] == original["input"]


def test_complete_flow_run_requires_context_and_commits(tmp_path):
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    run = _create(store)
    store.transition(run["run_id"], "starting")
    store.transition(run["run_id"], "running")
    task = CompleteFlowRunTask({})
    with pytest.raises(Exception, match="durable_one_shot"):
        task.execute(FlowFile(content=b"done"))
    task.set_flow_run_context(
        {"run_id": run["run_id"]}, store=store,
        coordinator=FlowRunCoordinator(store))
    result = task.execute(FlowFile(content=b"done", attributes={
        "flow.run.artifacts": "[]", "result.score": "1"}))
    assert result[0].get_attribute("flow.run.status") == "completed"
    assert store.get(run["run_id"])["terminal"]["attributes"] == {
        "result.score": "1"}


def test_terminal_commit_survives_retryable_projection_failure(
        monkeypatch, tmp_path):
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    run = _create(store)
    store.transition(run["run_id"], "starting")
    store.transition(run["run_id"], "running")
    coordinator = FlowRunCoordinator(store)
    proposal_store = MagicMock()
    proposal_store.mark_run_status.return_value = {
        "proposal_id": "proposal-1", "conversation_id": "conversation-1"}
    monkeypatch.setattr(
        "core.workflow_proposal_notifications.publish_proposal_update",
        lambda _proposal: (_ for _ in ()).throw(RuntimeError("SSE unavailable")))

    store.stage_terminal(run["run_id"], {
        "schema_version": 1, "summary": "done", "artifacts": []})
    committed = store.commit(run["run_id"])
    assert committed["status"] == "completed"
    assert coordinator.deliver_pending_events(proposal_store) == 0
    assert len(store.pending_events()) == 1


def test_durable_one_shot_definition_requires_exactly_one_terminal():
    register_all_tasks()
    base = {
        "id": "one", "name": "One", "version": "1.0.0",
        "execution_mode": "durable_one_shot", "run_contract": {
            "mode": "durable_one_shot"},
        "tasks": {}, "services": {}, "groups": {}, "relations": [],
        "entries": [], "exits": [],
    }
    report = FlowDefinitionValidator.validate(base)
    assert "invalid_flow_run_terminal_count" in {
        item["code"] for item in report["problems"]}
    valid = copy.deepcopy(base)
    valid["tasks"]["done"] = {"type": "completeFlowRun", "parameters": {}}
    valid["entries"] = valid["exits"] = ["done"]
    assert FlowDefinitionValidator.validate(valid)["ok"]
    normal = copy.deepcopy(valid)
    normal.pop("execution_mode")
    normal.pop("run_contract")
    assert "complete_flow_run_without_contract" in {
        item["code"] for item in FlowDefinitionValidator.validate(normal)["problems"]}


def test_attach_executor_injects_unique_run_context(monkeypatch, tmp_path):
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    run = _create(store)
    task = MagicMock()
    executor = MagicMock()
    executor._runtime_context = {}
    executor._tasks = {"done": task}
    registry = MagicMock()
    monkeypatch.setattr(
        "core.executor_registry.ExecutorRegistry.get_instance",
        classmethod(lambda cls: registry))
    FlowRunCoordinator(store).attach_and_start(run["run_id"], executor)
    assert executor._runtime_context["flow_run_context"]["run_id"] == run["run_id"]
    assert executor._instance_id == run["deployment_instance_id"]
    executor.inject.assert_called_once()
    assert store.get(run["run_id"])["status"] == "running"


def test_recovery_uses_checkpoint_without_reinjecting_original_input(
        monkeypatch, tmp_path):
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    run = _create(store)
    store.transition(run["run_id"], "starting")
    store.transition(run["run_id"], "running")
    executor = MagicMock()
    executor._runtime_context = {}
    executor._tasks = {}
    registry = MagicMock()
    monkeypatch.setattr(
        "core.executor_registry.ExecutorRegistry.get_instance",
        classmethod(lambda cls: registry))
    FlowRunCoordinator(store).recover(run["run_id"], lambda _run: executor)
    executor.inject.assert_not_called()
    recovered = store.get(run["run_id"])
    assert recovered["status"] == "running"
    assert recovered["recovery_count"] == 1


def test_idle_one_shot_without_terminal_fails_closed(monkeypatch, tmp_path):
    from engine._continuous_exec_run import _ContinuousExecRunMixin

    store = FlowRunStore(tmp_path / "runs.sqlite3")
    run = _create(store)
    store.transition(run["run_id"], "starting")
    store.transition(run["run_id"], "running")
    monkeypatch.setattr(
        "core.confirmation_store.ConfirmationStore.instance",
        classmethod(lambda cls: MagicMock(has_pending_waits=lambda _id: False)))
    monkeypatch.setattr(
        "core.workflow_agent_invocation.WorkflowParentInvocationStore.instance",
        classmethod(lambda cls: MagicMock(has_pending=lambda _id: False)))

    holder = _ContinuousExecRunMixin()
    holder._instance_id = run["deployment_instance_id"]
    holder._runtime_context = {
        "flow_run_store": store,
        "flow_run_context": {"run_id": run["run_id"]},
    }
    assert holder._has_pending_durable_continuations() is False
    failed = store.get(run["run_id"])
    assert failed["status"] == "failed"
    assert "without reaching completeFlowRun" in failed["error"]


def test_durable_one_shot_commits_and_auto_stops_without_deleting_history(
        monkeypatch, tmp_path):
    register_all_tasks()
    store = FlowRunStore(tmp_path / "runs.sqlite3")
    run = _create(store)
    flow = FlowParser.parse({
        "id": "one", "name": "One", "version": "1.0.0",
        "execution_mode": "durable_one_shot",
        "run_contract": {"mode": "durable_one_shot"},
        "tasks": {"done": {"type": "completeFlowRun", "parameters": {}}},
        "services": {}, "groups": {}, "relations": [],
        "entries": ["done"], "exits": ["done"],
    })
    executor = ContinuousFlowExecutor(
        flow, schedule_interval=0.01, enable_checkpoints=False)
    registry = MagicMock()
    monkeypatch.setattr(
        "core.executor_registry.ExecutorRegistry.get_instance",
        classmethod(lambda cls: registry))
    FlowRunCoordinator(store).attach_and_start(
        run["run_id"], executor, entry_task_id="done")
    deadline = time.time() + 3
    while executor.is_running and time.time() < deadline:
        time.sleep(0.01)
    if executor.is_running:
        executor.stop()
    persisted = store.get(run["run_id"])
    assert persisted["status"] == "completed"
    assert persisted["terminal"]["summary"] == "hello"
    assert store.list("conversation-1")[0]["run_id"] == run["run_id"]
