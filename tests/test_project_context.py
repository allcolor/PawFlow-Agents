"""Tests for active relay project resolution and prompt context."""

from unittest.mock import MagicMock

from core import project_context
from core.handlers.project_wiki import ProjectWikiHandler


def test_prepare_active_project_context_schedules_relay_and_returns_digests(monkeypatch):
    service = MagicMock()
    scheduled = MagicMock(return_value=True)
    monkeypatch.setattr(project_context, "resolve_active_project",
                        lambda *_args, **_kwargs: ("relay-a", service, True))
    monkeypatch.setattr("core.project_maintenance.schedule_project_maintenance", scheduled)
    monkeypatch.setattr("core.project_graph_digest.build_project_graph_digest",
                        lambda user_id, relay_id: f"graph:{user_id}:{relay_id}")
    monkeypatch.setattr("core.project_wiki_digest.build_project_wiki_digest",
                        lambda user_id, relay_id: f"wiki:{user_id}:{relay_id}")
    result = project_context.prepare_active_project_context(
        "alice", "conversation-a", "assistant")

    assert result == ("relay-a", "graph:alice:relay-a", "wiki:alice:relay-a")
    scheduled.assert_called_once_with(
        user_id="alice", relay_id="relay-a", service=service,
        conversation_id="conversation-a", agent_name="assistant",
        local=True)


def test_resolve_active_project_uses_single_link_when_no_default(monkeypatch):
    service = MagicMock()
    registry = MagicMock()
    registry.resolve.return_value = service
    monkeypatch.setattr("core.relay_bindings.get_default", lambda *_a, **_k: "")
    monkeypatch.setattr("core.relay_bindings.get_linked",
                        lambda *_a, **_k: ["relay-only"])
    monkeypatch.setattr("core.relay_bindings.get_default_local",
                        lambda *_a, **_k: False)
    monkeypatch.setattr("core.service_registry.ServiceRegistry.get_instance",
                        lambda: registry)

    result = project_context.resolve_active_project(
        "alice", "conversation-a", "assistant")

    assert result == ("relay-only", service, False)
    registry.resolve.assert_called_once_with(
        "relay-only", user_id="alice", conv_id="conversation-a")


def test_resolve_active_project_rejects_ambiguous_links(monkeypatch):
    monkeypatch.setattr("core.relay_bindings.get_default", lambda *_a, **_k: "")
    monkeypatch.setattr("core.relay_bindings.get_linked",
                        lambda *_a, **_k: ["relay-a", "relay-b"])

    assert project_context.resolve_active_project(
        "alice", "conversation-a", "assistant") == ("", None, False)


def test_project_wiki_handler_uses_resolved_relay_scope(monkeypatch):
    service = MagicMock()
    service._service_id = "relay-a"
    wiki = MagicMock()
    wiki.status.return_value = {"initialized": True, "pages": 2}
    wiki.query.return_value = [{"slug": "architecture"}]
    for_relay = MagicMock(return_value=wiki)
    monkeypatch.setattr("core.project_wiki.ProjectWiki.for_relay", for_relay)
    handler = ProjectWikiHandler()
    handler.set_user_id("alice")
    handler.set_fs_service(service)

    assert '"pages": 2' in handler.execute({"action": "status"})
    assert "architecture" in handler.execute(
        {"action": "query", "question": "system design"})
    for_relay.assert_called_with("alice", "relay-a")
    wiki.query.assert_called_once_with("system design", limit=8)


def test_filesystem_mutation_hook_schedules_relay_maintenance(monkeypatch):
    service = MagicMock()
    service._service_id = "relay-a"
    schedule = MagicMock(return_value=True)
    monkeypatch.setattr(
        "core.project_maintenance.schedule_project_maintenance", schedule)
    handler = ProjectWikiHandler()
    handler.set_user_id("alice")
    handler.set_conversation_id("conversation-a")
    handler.set_agent_name("assistant")

    handler._schedule_project_refresh(service, "core/auth.py", local=True)

    schedule.assert_called_once_with(
        user_id="alice", relay_id="relay-a", service=service,
        conversation_id="conversation-a", agent_name="assistant",
        local=True, changed_path="core/auth.py", force=True)
