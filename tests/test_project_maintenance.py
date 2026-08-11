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
    job = _MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=service,
        conversation_id="conv-a", agent_name="assistant", llm_client=client,
        root="src", local=True)

    ProjectMaintenanceScheduler()._run(job)

    graph_for_relay.assert_called_once_with("alice", "relay-a")
    wiki_for_relay.assert_called_once_with("alice", "relay-a")
    graph.build_from_relay.assert_called_once_with(service, "src", local=True)
    wiki.scan_from_relay.assert_called_once_with(
        service, "src", local=True,
        initial_paths=["core/main.py", "core/leaf.py"])
    wiki.auto_update.assert_called_once_with(service, client, local=True)


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
