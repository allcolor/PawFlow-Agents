"""Tests for bounded per-agent scratchpad storage and context hints."""

import json

import pytest

import core.paths as paths
from core.handlers.scratchpad import ScratchpadHandler
from core.llm_client import LLMClient, LLMMessage
from core.scratchpad_store import ScratchpadStore
from core.tool_registry import create_default_registry


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SCRATCHPADS_DIR", tmp_path / "scratchpads")
    ScratchpadStore._instance = None
    value = ScratchpadStore.instance()
    yield value
    ScratchpadStore._instance = None


def test_crud_search_and_scope_isolation(store):
    first = store.create(
        "u", "c", "a", topic="Root cause", content="PTY helper missing",
        tags=["windows", "terminal"], ttl_hours=24)
    second = store.create(
        "u", "c", "a", topic="Next check", content="Install beta",
        ttl_hours=48)

    page = store.list_page("u", "c", "a", query="terminal")
    assert [note["id"] for note in page["notes"]] == [first["id"]]
    assert page["total"] == 1
    assert store.list_page("u", "other", "a")["notes"] == []
    assert store.list_page("u", "c", "other")["notes"] == []

    updated = store.update(
        "u", "c", "a", first["id"], content="Bundle helper binaries",
        tags=["windows"], ttl_hours=72)
    assert updated["content"] == "Bundle helper binaries"
    assert updated["tags"] == ["windows"]
    assert store.get("u", "c", "a", first["id"]) == updated
    assert store.delete("u", "c", "a", second["id"]) is True
    assert store.clear("u", "c", "a") == 1


def test_expired_notes_are_pruned(store, monkeypatch):
    monkeypatch.setattr("core.scratchpad_store.time.time", lambda: 100.0)
    note = store.create(
        "u", "c", "a", topic="Temporary", content="value", ttl_hours=1)
    monkeypatch.setattr("core.scratchpad_store.time.time", lambda: 3701.0)

    assert store.get("u", "c", "a", note["id"]) is None
    assert store.list_page("u", "c", "a")["total"] == 0


def test_validation_and_page_bounds(store):
    with pytest.raises(ValueError, match="ttl_hours"):
        store.create("u", "c", "a", topic="x", content="y", ttl_hours=0)
    with pytest.raises(ValueError, match="tags must be an array"):
        store.create("u", "c", "a", topic="x", content="y", tags="bad")
    with pytest.raises(ValueError, match="limit"):
        store.list_page("u", "c", "a", limit=101)


def test_context_hint_names_topics_but_never_injects_content(store):
    store.create(
        "u", "c", "a", topic="Terminal diagnosis",
        content="secret transient evidence", ttl_hours=24)

    hint = store.context_hint("u", "c", "a")

    assert "Terminal diagnosis" in hint
    assert "secret transient evidence" not in hint
    assert "Contents are not injected automatically" in hint
    assert "todolist" in hint and "memory" in hint


def test_handler_and_default_registry(store):
    handler = ScratchpadHandler()
    handler.set_user_id("u")
    handler.set_conversation_id("c")
    handler.set_agent_name("a")
    created = json.loads(handler.execute({
        "action": "create", "topic": "Resume", "content": "Run test"}))
    listed = json.loads(handler.execute({"action": "list", "query": "Run"}))
    updated = json.loads(handler.execute({
        "action": "update", "note_id": created["id"],
        "content": "Run full test"}))
    assert listed["notes"][0]["id"] == created["id"]
    assert updated["content"] == "Run full test"
    assert create_default_registry().get("scratchpad") is not None


def test_cli_cold_context_injects_hint_without_note_content(store, tmp_path):
    store.create(
        "u", "c", "assistant", topic="Resume marker",
        content="do not inject this body")
    client = LLMClient("claude-code")
    messages = [LLMMessage(
        role="user", content="continue", conversation_id="c")]

    client._build_cli_initial_context_prompt(
        messages, system_prompt="rules", user_text="continue",
        workdir=str(tmp_path), provider_workdir="/provider",
        user_id="u", conversation_id="c", agent_name="assistant")

    body = (tmp_path / ".pawflow_cli" / "initial_context.md").read_text()
    assert "## Scratchpad Hint" in body
    assert "Resume marker" in body
    assert "do not inject this body" not in body
    assert body.index("## Scratchpad Hint") < body.index("## Bootstrap Contract")
