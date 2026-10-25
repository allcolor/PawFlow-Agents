"""Automatic project wiki maintenance is opt-in, like the AST graph.

The wiki used to run on every relay unconditionally: a scan of the whole tree
plus an LLM batch, scheduled from every filesystem write and from context
preparation. On a large relay the scan alone takes hours, so the wiki was
permanently busy -- the user's report on 2026-10-24: 'le wiki prend des heures
sur mes relays, comment on désactive ou corrige pour pas que ça tourne tout le
temps pour rien'.

These tests pin the switch: ``PAWFLOW_PROJECT_WIKI_AUTO`` resolves exactly like
``PAWFLOW_PROJECT_GRAPH_AUTO`` (process env, then the conversation -> user ->
global variable cascade) and defaults to OFF. Turning it off must not stop the
ScratchDir expiry cleanup, which shares the maintenance run.
"""

from unittest.mock import MagicMock

import pytest

from core.project_maintenance import (
    ProjectMaintenanceScheduler, _MaintenanceJob)


@pytest.fixture(autouse=True)
def _no_ambient_switch(monkeypatch):
    monkeypatch.delenv("PAWFLOW_PROJECT_WIKI_AUTO", raising=False)
    monkeypatch.delenv("PAWFLOW_PROJECT_GRAPH_AUTO", raising=False)


def _run(monkeypatch, *, wiki_agent=""):
    graph = MagicMock(nodes=[])
    graph.build_from_relay.return_value = {"status": "built"}
    wiki = MagicMock()
    wiki.scan_from_relay.return_value = {"status": "refreshed"}
    wiki.auto_update.return_value = {"status": "idle"}
    monkeypatch.setattr(
        "core.project_graph.ProjectGraph.for_relay", lambda *_a: graph)
    monkeypatch.setattr(
        "core.project_wiki.ProjectWiki.for_relay", lambda *_a: wiki)
    monkeypatch.setattr(
        "core.linked_service_bindings.resolve_agent_override",
        lambda *_a, **_k: (wiki_agent, {}, bool(wiki_agent)))
    monkeypatch.setattr(
        "core.summarizer_bindings.resolve_service",
        lambda *_a: (None, None, False))
    monkeypatch.setattr(
        "core.scratchdir_manager.ScratchDirManager", MagicMock())
    ProjectMaintenanceScheduler()._run(_MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=MagicMock(),
        conversation_id="conv-a", agent_name="assistant"))
    return wiki


def test_wiki_auto_is_off_by_default(monkeypatch):
    """No variable, no env: the wiki must not scan or spend an LLM batch."""
    monkeypatch.setattr("core.expression.resolve_expression",
                        lambda *_a, **_k: "")

    wiki = _run(monkeypatch)

    wiki.scan_from_relay.assert_not_called()
    wiki.auto_update.assert_not_called()


def test_wiki_auto_opt_in_resolves_through_the_variable_cascade(monkeypatch):
    monkeypatch.setattr("core.expression.resolve_expression",
                        lambda *_a, **_k: "true")

    wiki = _run(monkeypatch)

    wiki.scan_from_relay.assert_called_once()
    wiki.auto_update.assert_called_once()


def test_wiki_auto_opt_in_honours_the_conversation_scope(monkeypatch):
    seen = {}

    def _resolve(expr, **kwargs):
        seen.update(kwargs, expr=expr)
        return "1"

    monkeypatch.setattr("core.expression.resolve_expression", _resolve)

    _run(monkeypatch)

    assert "PAWFLOW_PROJECT_WIKI_AUTO" in seen["expr"]
    assert (seen["owner"], seen["conversation_id"]) == ("alice", "conv-a")


def test_process_env_enables_the_wiki_over_variables(monkeypatch):
    monkeypatch.setenv("PAWFLOW_PROJECT_WIKI_AUTO", "1")
    monkeypatch.setattr("core.expression.resolve_expression",
                        lambda *_a, **_k: "")

    wiki = _run(monkeypatch)

    wiki.scan_from_relay.assert_called_once()


def test_an_explicit_wiki_agent_keeps_working_when_auto_is_off(monkeypatch):
    """An operator who bound a wiki agent asked for it: it still scans.

    A bound agent routes the update through the workflow runtime instead of
    the direct LLM path, so only the scan and the routing are pinned here.
    """
    monkeypatch.setattr("core.expression.resolve_expression",
                        lambda *_a, **_k: "")
    submitted = []
    monkeypatch.setattr(
        ProjectMaintenanceScheduler, "_submit_wiki_workflow",
        lambda self, job: submitted.append(job.relay_id) or {"status": "queued"})

    wiki = _run(monkeypatch, wiki_agent="wiki")

    wiki.scan_from_relay.assert_called_once()
    assert submitted == ["relay-a"]
    wiki.auto_update.assert_not_called()


def test_scratchdir_cleanup_still_runs_when_the_wiki_is_off(monkeypatch):
    """The switch turns off the wiki, not the whole maintenance run."""
    monkeypatch.setattr("core.expression.resolve_expression",
                        lambda *_a, **_k: "")
    manager = MagicMock()
    monkeypatch.setattr(
        "core.scratchdir_manager.ScratchDirManager", lambda *_a: manager)
    monkeypatch.setattr(
        "core.project_wiki.ProjectWiki.for_relay", lambda *_a: MagicMock())
    monkeypatch.setattr(
        "core.project_graph.ProjectGraph.for_relay", lambda *_a: MagicMock())

    ProjectMaintenanceScheduler()._run(_MaintenanceJob(
        user_id="alice", relay_id="relay-a", service=MagicMock(),
        conversation_id="conv-a", agent_name="assistant"))

    manager.cleanup_expired.assert_called_once()
