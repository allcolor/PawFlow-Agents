"""Tests for coalesced relay project maintenance."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.project_maintenance import ProjectMaintenanceScheduler, _MaintenanceJob


def test_worker_refreshes_graph_and_wiki_for_same_relay(monkeypatch):
    graph = MagicMock()
    graph.nodes = [
        {"source_file": "core/main.py"},
        {"source_file": "core/main.py"},
        {"source_file": "core/leaf.py"},
    ]
    graph.build_from_relay.return_value = {"status": "built"}
    wiki = MagicMock()
    wiki.scan_from_relay.return_value = {"status": "refreshed"}
    wiki.auto_update.return_value = {"status": "updated"}
    graph_for_relay = MagicMock(return_value=graph)
    wiki_for_relay = MagicMock(return_value=wiki)
    monkeypatch.setattr("core.project_graph.ProjectGraph.for_relay", graph_for_relay)
    monkeypatch.setattr("core.project_wiki.ProjectWiki.for_relay", wiki_for_relay)
    service = MagicMock()
    client = MagicMock()
    summarizer = MagicMock()
    summarizer.resolve_llm_service.return_value = (client, 100_000, "wiki-llm")
    resolve_service = MagicMock(return_value=(summarizer, MagicMock(), True))
    monkeypatch.setattr(
        "core.summarizer_bindings.resolve_service", resolve_service)
    job = _MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=service,
        conversation_id="conv-a", agent_name="assistant",
        root="src", local=True)

    ProjectMaintenanceScheduler()._run(job)

    graph_for_relay.assert_called_once_with("alice", "relay-a")
    wiki_for_relay.assert_called_once_with("alice", "relay-a")
    # A server-local mutation may trigger maintenance, but both project
    # knowledge stores remain pinned to the relay project surface.
    graph.build_from_relay.assert_called_once_with(service, "src", local=False)
    wiki.scan_from_relay.assert_called_once_with(
        service, "src", local=False,
        initial_paths=["core/main.py", "core/leaf.py"])
    wiki.auto_update.assert_called_once_with(service, client, local=False)
    resolve_service.assert_called_once_with("alice", "conv-a")
    summarizer.resolve_llm_service.assert_called_once_with("alice", "conv-a")


def test_worker_never_falls_back_when_summarizer_is_unavailable(monkeypatch):
    graph = MagicMock(nodes=[])
    graph.build_from_relay.return_value = {"status": "built"}
    wiki = MagicMock()
    wiki.scan_from_relay.return_value = {"status": "refreshed"}
    wiki.auto_update.return_value = {"status": "pending"}
    monkeypatch.setattr(
        "core.project_graph.ProjectGraph.for_relay", lambda *_args: graph)
    monkeypatch.setattr(
        "core.project_wiki.ProjectWiki.for_relay", lambda *_args: wiki)
    resolve_service = MagicMock(return_value=(None, None, True))
    monkeypatch.setattr(
        "core.summarizer_bindings.resolve_service", resolve_service)
    service = MagicMock()

    ProjectMaintenanceScheduler()._run(_MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=service,
        conversation_id="conv-a"))

    wiki.auto_update.assert_called_once_with(service, None, local=False)
    resolve_service.assert_called_once_with("alice", "conv-a")


def test_wiki_workflow_submission_pins_exact_flow_and_silent_mode(monkeypatch):
    scheduler = ProjectMaintenanceScheduler()
    authorization = SimpleNamespace(
        root_turn_id="web:root",
        to_dict=lambda: {
            "context_id": "1a9834d2-59bb-4df9-931c-9418e250c904",
            "revision": 2,
            "root_turn_id": "web:root",
        },
    )
    monkeypatch.setattr(
        "core.authorization_context.active_authority_ref",
        lambda *_args: authorization)
    monkeypatch.setattr(
        "core.relay_bindings.get_default",
        lambda *_args, **_kwargs: "relay-a")
    monkeypatch.setattr(
        scheduler, "_resolve_wiki_service_id",
        lambda *_args: "wiki-llm")
    binding = {
        "flow_fqn": "pawflow.agents.wiki:1.0.0",
        "flow_scope": "global",
        "input_port": "agent_request",
        "terminal_port": "agent_terminal",
        "preempt_policy": "checkpoint",
        "allowed_effects": [
            "filesystem.read", "process.execute",
            "resource.read", "resource.write",
        ],
        "parameters": {
            "project_root": "src",
            "extractor_llm": "wiki-llm",
            "writer_llm": "wiki-llm",
            "batch_files": 8,
            "max_files": 10000,
        },
        "flow_ref": {
            "schema_version": 1,
            "resource_type": "flow",
            "name": "pawflow.agents.wiki:1.0.0",
            "scope": "global",
            "owner_id": None,
            "package_id": None,
            "package_version": None,
            "version": "1.0.0",
            "content_digest": "a" * 64,
            "source_id": "repository:global:pawflow.agents.wiki:1.0.0",
        },
    }
    bind = MagicMock(return_value=binding)
    monkeypatch.setattr(
        "core.workflow_agent_resources.bind_agent_workflow", bind)
    prepared = object()
    prepare = MagicMock(return_value=prepared)
    monkeypatch.setattr(
        "core.workflow_agent_runtime.prepare_workflow_turn", prepare)
    runtime = MagicMock()
    runtime.submit_bound.return_value = {
        "status": "accepted", "queued": False, "run_id": "wr_wiki"}
    monkeypatch.setattr(
        "core.workflow_agent_runtime.WorkflowAgentRuntime.instance",
        classmethod(lambda cls: runtime))
    job = _MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=MagicMock(),
        conversation_id="conv-a", agent_name="assistant", root="src")

    result = scheduler._submit_wiki_workflow(job, write_mode="shadow")

    assert result["run_id"] == "wr_wiki"
    candidate, user_id, conversation_id = bind.call_args.args
    assert candidate["flow_fqn"] == "pawflow.agents.wiki:1.0.0"
    assert candidate["parameters"]["extractor_llm"] == "wiki-llm"
    assert candidate["parameters"]["write_mode"] == "shadow"
    assert (user_id, conversation_id) == ("alice", "conv-a")
    assert prepare.call_args.kwargs["message_id"] == "web:root"
    runtime.submit_bound.assert_called_once()
    assert runtime.submit_bound.call_args.kwargs == {
        "invocation_mode": "silent_maintenance"}
    assert runtime.submit_bound.call_args.args[0] is prepared


def test_wiki_workflow_submission_requires_active_authority(monkeypatch):
    monkeypatch.setattr(
        "core.authorization_context.active_authority_ref",
        lambda *_args: None)
    result = ProjectMaintenanceScheduler()._submit_wiki_workflow(
        _MaintenanceJob(
            user_id="alice", relay_id="relay-a", service=MagicMock(),
            conversation_id="conv-a", agent_name="assistant"))

    assert result == {"status": "skipped", "reason": "missing authorization"}


def test_linked_wiki_agent_uses_its_own_workflow_without_summarizer(monkeypatch):
    scheduler = ProjectMaintenanceScheduler()
    authorization = SimpleNamespace(
        root_turn_id="web:root",
        to_dict=lambda: {
            "context_id": "1a9834d2-59bb-4df9-931c-9418e250c904",
            "revision": 2,
            "root_turn_id": "web:root",
        },
    )
    monkeypatch.setattr(
        "core.authorization_context.active_authority_ref",
        lambda *_args: authorization)
    monkeypatch.setattr(
        "core.relay_bindings.get_default",
        lambda *_args, **_kwargs: "relay-a")
    monkeypatch.setattr(
        "core.linked_service_bindings.resolve_agent_override",
        lambda *_args: ("Wiki", {"workflow": {
            "flow_fqn": "pawflow.agents.wiki:1.0.0",
            "input_port": "agent_request",
            "terminal_port": "agent_terminal",
            "preempt_policy": "checkpoint",
            "allowed_effects": ["resource.read", "resource.write"],
            "parameters": {
                "project_root": ".",
                "extractor_llm": "WikiWriter",
                "writer_llm": "WikiWriter",
            },
        }}, True))
    resolve_service = MagicMock(
        side_effect=AssertionError("linked agent must not require a summarizer"))
    monkeypatch.setattr(scheduler, "_resolve_wiki_service_id", resolve_service)
    prepared = object()
    prepare = MagicMock(return_value=prepared)
    monkeypatch.setattr(
        "core.workflow_agent_runtime.prepare_workflow_turn", prepare)
    runtime = MagicMock()
    runtime.submit_bound.return_value = {
        "status": "accepted", "queued": False, "run_id": "wr_wiki"}
    monkeypatch.setattr(
        "core.workflow_agent_runtime.WorkflowAgentRuntime.instance",
        classmethod(lambda cls: runtime))

    result = scheduler._submit_wiki_workflow(_MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=MagicMock(),
        conversation_id="conv-a", agent_name="assistant", root="src"),
        write_mode="shadow")

    assert result["run_id"] == "wr_wiki"
    resolve_service.assert_not_called()
    assert prepare.call_args.kwargs["agent_name"] == "Wiki"
    bound = runtime.submit_bound.call_args.args[1]
    assert bound.flow_fqn == "pawflow.agents.wiki:1.0.0"
    assert bound.parameters["writer_llm"] == "WikiWriter"
    assert bound.parameters["project_root"] == "src"
    assert bound.parameters["write_mode"] == "shadow"


def test_worker_cutover_submits_workflow_without_legacy_auto_update(
        monkeypatch):
    monkeypatch.setenv("PAWFLOW_WIKI_WORKFLOW_CUTOVER", "1")
    graph = MagicMock(nodes=[])
    graph.build_from_relay.return_value = {"status": "built"}
    wiki = MagicMock()
    wiki.scan_from_relay.return_value = {"status": "refreshed"}
    monkeypatch.setattr(
        "core.project_graph.ProjectGraph.for_relay", lambda *_args: graph)
    monkeypatch.setattr(
        "core.project_wiki.ProjectWiki.for_relay", lambda *_args: wiki)
    scheduler = ProjectMaintenanceScheduler()
    submit = MagicMock(return_value={
        "status": "accepted", "queued": False, "run_id": "wr_wiki"})
    monkeypatch.setattr(scheduler, "_submit_wiki_workflow", submit)
    job = _MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=MagicMock(),
        conversation_id="conv-a", agent_name="assistant")

    scheduler._run(job)

    submit.assert_called_once_with(job)
    wiki.auto_update.assert_not_called()


def test_schedule_marks_running_job_for_one_rerun():
    scheduler = ProjectMaintenanceScheduler()
    service = MagicMock()
    job = _MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=service, running=True)
    scheduler._jobs["alice::relay-a"] = job

    scheduled = scheduler.schedule(
        user_id="alice", relay_id="relay-a", service=service,
        changed_path="core/main.py", force=True)
    scheduled_again = scheduler.schedule(
        user_id="alice", relay_id="relay-a", service=service,
        changed_path="core/other.py", force=True)

    assert scheduled is False
    assert scheduled_again is False
    assert job.rerun is True
    assert job.changed_paths == {"core/main.py", "core/other.py"}
    assert job.timer is None


def test_worker_runs_scoped_scratchdir_expiry_cleanup(monkeypatch):
    manager = MagicMock()
    manager.cleanup_expired.return_value = True
    manager_type = MagicMock(return_value=manager)
    monkeypatch.setattr(
        "core.scratchdir_manager.ScratchDirManager", manager_type)
    graph = MagicMock(nodes=[])
    graph.build_from_relay.return_value = {"status": "built"}
    wiki = MagicMock()
    wiki.scan_from_relay.return_value = {"status": "refreshed"}
    wiki.auto_update.return_value = {"status": "idle"}
    monkeypatch.setattr(
        "core.project_graph.ProjectGraph.for_relay", lambda *_args: graph)
    monkeypatch.setattr(
        "core.project_wiki.ProjectWiki.for_relay", lambda *_args: wiki)
    monkeypatch.setattr(
        "core.summarizer_bindings.resolve_service",
        lambda *_args: (None, None, False))
    service = MagicMock()

    ProjectMaintenanceScheduler()._run(_MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=service,
        conversation_id="conv-a", agent_name="assistant"))

    manager_type.assert_called_once_with(service)
    manager.cleanup_expired.assert_called_once_with(
        "alice", "conv-a", "assistant", "relay-a")
