"""Tests for coalesced relay project maintenance."""

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
    graph.build_from_relay.assert_called_once_with(service, "src", local=True)
    # The graph honors the job surface; the wiki is pinned to the relay
    # container — local=true would index the server/host runtime tree.
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
