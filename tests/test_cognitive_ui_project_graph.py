"""Tests for relay-scoped Project Graph webchat actions."""

import json
from unittest.mock import MagicMock

from tasks.ai.actions.cognitive_ui import _handle_cognitive_ui


class _FlowFile:
    def __init__(self):
        self.content = b""

    def set_content(self, content):
        self.content = content

    def payload(self):
        return json.loads(self.content)


def test_project_graph_report_reads_shared_relay_graph(monkeypatch):
    graph = MagicMock()
    graph.has_graph.return_value = True
    graph.get_report.return_value = "relay report"
    graph.nodes = [{"id": "one"}]
    graph.edges = [{"source": "one", "target": "two"}]
    for_relay = MagicMock(return_value=graph)
    monkeypatch.setattr(
        "core.project_context.resolve_active_project",
        lambda *_args, **_kwargs: ("relay-a", None, False))
    monkeypatch.setattr("core.project_graph.ProjectGraph.for_relay", for_relay)
    flowfile = _FlowFile()

    result = _handle_cognitive_ui(
        None, "project_graph_report", {"conversation_id": "conv-a"},
        None, "alice", flowfile)

    assert result == [flowfile]
    assert flowfile.payload() == {
        "report": "relay report", "has_graph": True, "nodes": 1, "edges": 1}
    for_relay.assert_called_once_with("alice", "relay-a")


def test_project_graph_build_uses_active_relay_surface(monkeypatch):
    service = MagicMock()
    graph = MagicMock()
    graph.build_from_relay.return_value = {"status": "built", "nodes": 3}
    monkeypatch.setattr(
        "core.project_context.resolve_active_project",
        lambda *_args, **_kwargs: ("relay-a", service, True))
    monkeypatch.setattr(
        "core.project_graph.ProjectGraph.for_relay", MagicMock(return_value=graph))
    flowfile = _FlowFile()

    _handle_cognitive_ui(
        None, "project_graph_build",
        {"conversation_id": "conv-a", "path": "src"},
        None, "alice", flowfile)

    assert flowfile.payload()["status"] == "built"
    graph.build_from_relay.assert_called_once_with(service, "src", local=True)


def test_explicit_linked_relay_selects_non_default_stored_graph(monkeypatch):
    graph = MagicMock()
    graph.has_graph.return_value = True
    graph.get_report.return_value = "relay-b report"
    graph.nodes = []
    graph.edges = []
    monkeypatch.setattr("core.relay_bindings.get_linked_all", lambda _cid: ["relay-a", "relay-b"])
    monkeypatch.setattr("core.relay_bindings.get_default_local", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        lambda: MagicMock(resolve=MagicMock(return_value=None)))
    for_relay = MagicMock(return_value=graph)
    monkeypatch.setattr("core.project_graph.ProjectGraph.for_relay", for_relay)
    flowfile = _FlowFile()

    _handle_cognitive_ui(None, "project_graph_report", {
        "conversation_id": "conv-a", "relay_id": "relay-b",
    }, None, "alice", flowfile)

    assert flowfile.payload()["report"] == "relay-b report"
    for_relay.assert_called_once_with("alice", "relay-b")


def test_explicit_unlinked_relay_is_rejected(monkeypatch):
    monkeypatch.setattr("core.relay_bindings.get_linked_all", lambda _cid: ["relay-a"])
    flowfile = _FlowFile()

    _handle_cognitive_ui(None, "project_graph_report", {
        "conversation_id": "conv-a", "relay_id": "relay-b",
    }, None, "alice", flowfile)

    assert "not linked" in flowfile.payload()["error"]


def test_explicit_disconnected_relay_still_rejects_build(monkeypatch):
    monkeypatch.setattr("core.relay_bindings.get_linked_all", lambda _cid: ["relay-b"])
    monkeypatch.setattr("core.relay_bindings.get_default_local", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "core.service_registry.ServiceRegistry.get_instance",
        lambda: MagicMock(resolve=MagicMock(return_value=None)))
    monkeypatch.setattr("core.project_graph.ProjectGraph.for_relay", MagicMock())
    flowfile = _FlowFile()

    _handle_cognitive_ui(None, "project_graph_build", {
        "conversation_id": "conv-a", "relay_id": "relay-b",
    }, None, "alice", flowfile)

    assert flowfile.payload() == {"error": "Relay 'relay-b' is not connected."}


def test_project_graph_ego_clamps_params_and_requires_graph(monkeypatch):
    graph = MagicMock()
    graph.has_graph.return_value = True
    graph.ego_subgraph.return_value = {"center": "n", "nodes": [], "edges": []}
    monkeypatch.setattr(
        "core.project_context.resolve_active_project",
        lambda *_args, **_kwargs: ("relay-a", None, False))
    monkeypatch.setattr(
        "core.project_graph.ProjectGraph.for_relay", MagicMock(return_value=graph))
    flowfile = _FlowFile()

    _handle_cognitive_ui(
        None, "project_graph_ego",
        {"conversation_id": "conv-a", "label": "Auth", "depth": 9, "max_nodes": 9999},
        None, "alice", flowfile)

    assert flowfile.payload()["center"] == "n"
    graph.ego_subgraph.assert_called_once_with(label="Auth", depth=3, max_nodes=300)

    graph.has_graph.return_value = False
    _handle_cognitive_ui(
        None, "project_graph_ego", {"conversation_id": "conv-a"},
        None, "alice", flowfile)
    assert "No project graph" in flowfile.payload()["error"]


def test_project_wiki_ui_lists_and_saves_on_active_relay(monkeypatch):
    service = MagicMock()
    wiki = MagicMock()
    wiki.list_pages.return_value = [{"slug": "overview"}]
    wiki.upsert_page.return_value = {"title": "Overview"}
    monkeypatch.setattr(
        "core.project_context.resolve_active_project",
        lambda *_args, **_kwargs: ("relay-a", service, False))
    monkeypatch.setattr(
        "core.project_graph.ProjectGraph.for_relay", MagicMock())
    for_relay = MagicMock(return_value=wiki)
    monkeypatch.setattr("core.project_wiki.ProjectWiki.for_relay", for_relay)

    flowfile = _FlowFile()
    _handle_cognitive_ui(
        None, "project_wiki_pages",
        {"conversation_id": "conv-a", "query": "over"},
        None, "alice", flowfile)
    assert flowfile.payload()["pages"] == [{"slug": "overview"}]
    wiki.list_pages.assert_called_once_with("over")

    _handle_cognitive_ui(
        None, "project_wiki_save", {
            "conversation_id": "conv-a", "slug": "overview",
            "title": "Overview", "summary": "Summary",
            "content": "Body", "sources": ["README.md"],
        }, None, "alice", flowfile)
    wiki.upsert_page.assert_called_once_with(
        "overview", "Overview", "Summary", "Body", ["README.md"])
    for_relay.assert_called_with("alice", "relay-a")
