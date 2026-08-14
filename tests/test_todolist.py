import itertools
import json
from pathlib import Path

import pytest

import core.paths as paths
from core.handlers.todolist import TodoListHandler
from core.llm_client import LLMClient, LLMMessage
from core.todo_store import TodoStore
from core.tool_registry import create_default_registry


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "TODOLISTS_DIR", tmp_path / "todolists")
    TodoStore._instance = None
    yield TodoStore.instance()
    TodoStore._instance = None


def test_create_update_list_get_and_atomic_document(store):
    task = store.create(
        "user", "conv", "agent", subject="Implement durable todos",
        active_form="Implementing durable todos")
    assert task["id"].startswith("td_")
    assert task["status"] == "pending"

    updated = store.update(
        "user", "conv", "agent", task["id"], status="in_progress")
    assert updated["status"] == "in_progress"
    assert store.get("user", "conv", "agent", task["id"]) == updated
    assert store.list_tasks(
        "user", "conv", "agent", status="in_progress") == [updated]

    assert (paths.TODOLISTS_DIR / "todos.sqlite3").is_file()
    assert list(paths.TODOLISTS_DIR.rglob("*.tmp")) == []


def test_create_accepts_initial_status_and_rejects_invalid_status(store):
    task = store.create(
        "user", "conv", "agent", subject="Start now",
        status="in_progress")

    assert task["status"] == "in_progress"
    with pytest.raises(ValueError, match="status must be"):
        store.create(
            "user", "conv", "agent", subject="Broken",
            status="running")


def test_list_page_is_filtered_ordered_and_bounded(store, monkeypatch):
    now = iter(range(1, 100))
    monkeypatch.setattr("core.todo_store.time.time", lambda: float(next(now)))
    created = [
        store.create("u", "c", "a", subject=f"needle-{index}")
        for index in range(25)
    ]
    store.update("u", "c", "a", created[-1]["id"], status="completed")

    page = store.list_page(
        "u", "c", "a", status="pending", query="needle-1",
        limit=5, offset=0)

    assert [task["subject"] for task in page["tasks"]] == [
        "needle-19", "needle-18", "needle-17", "needle-16", "needle-15"]
    assert page["total"] == 11
    assert page["has_more"] is True
    assert page["counts"] == {
        "pending": 11, "in_progress": 0, "completed": 0}

    second = store.list_page(
        "u", "c", "a", status="pending", query="needle-1",
        limit=5, offset=5)
    assert len(second["tasks"]) == 5
    assert not ({task["id"] for task in page["tasks"]}
                & {task["id"] for task in second["tasks"]})


def test_legacy_atomic_document_is_migrated_once(tmp_path, monkeypatch):
    todo_dir = tmp_path / "todolists"
    legacy = todo_dir / "user" / "conv" / "agent.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "version": 1,
        "tasks": [{
            "id": "td_legacy", "status": "completed", "subject": "old",
            "description": "", "active_form": "", "owner": "",
            "blocks": [], "blocked_by": [], "metadata": {},
            "external_id": "", "source_call_id": "",
            "created_at": 1.0, "updated_at": 2.0,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(paths, "TODOLISTS_DIR", todo_dir)
    TodoStore._instance = None
    migrated = TodoStore.instance()
    try:
        assert migrated.get("user", "conv", "agent", "td_legacy")["subject"] == "old"
        assert not legacy.exists()
        assert (todo_dir / "todos.sqlite3").is_file()
    finally:
        TodoStore._instance = None


def test_scope_isolated_by_user_conversation_and_agent(store):
    store.create("u1", "c1", "a1", subject="one")
    assert store.list_tasks("u1", "c1", "a1")
    assert store.list_tasks("u2", "c1", "a1") == []
    assert store.list_tasks("u1", "c2", "a1") == []
    assert store.list_tasks("u1", "c1", "a2") == []


def test_scope_encoding_does_not_alias_distinct_identifiers(store):
    first = store.create("u/a", "c:a", "agent/name", subject="slash")
    second = store.create("u_a", "c_a", "agent_name", subject="underscore")

    assert store.list_tasks("u/a", "c:a", "agent/name") == [first]
    assert store.list_tasks("u_a", "c_a", "agent_name") == [second]


@pytest.mark.parametrize("field", ["blocks", "blocked_by"])
def test_create_rejects_non_array_dependencies(store, field):
    with pytest.raises(ValueError, match=f"{field} must be an array"):
        store.create("u", "c", "a", subject="invalid", **{field: "task-1"})


def test_source_call_replay_upserts_and_external_id_resolves(store):
    first = store.create(
        "u", "c", "a", subject="first", external_id="native-7",
        source_call_id="call-1")
    replay = store.create(
        "u", "c", "a", subject="corrected", external_id="native-7",
        source_call_id="call-1")
    assert replay["id"] == first["id"]
    assert replay["subject"] == "corrected"
    assert len(store.list_tasks("u", "c", "a")) == 1
    assert store.update(
        "u", "c", "a", "native-7", status="completed")["id"] == first["id"]


def test_context_has_all_active_and_only_five_recent_completed(store, monkeypatch):
    # core.todo_store.time is the global `time` module, so this patch is
    # process-wide: any daemon thread (pool sweepers, conversation writers)
    # calling time.time() consumes values too. A bounded iterator therefore
    # raised StopIteration under load — use an unbounded monotonic counter.
    now = itertools.count(1)
    monkeypatch.setattr("core.todo_store.time.time", lambda: float(next(now)))
    pending = store.create("u", "c", "a", subject="pending")
    active = store.create("u", "c", "a", subject="active")
    store.update("u", "c", "a", active["id"], status="in_progress")
    completed = []
    for index in range(6):
        task = store.create("u", "c", "a", subject=f"done-{index}")
        store.update("u", "c", "a", task["id"], status="completed")
        completed.append(task)

    text = store.context_text("u", "c", "a")
    assert pending["id"] in text
    assert active["id"] in text
    assert completed[0]["id"] not in text
    for task in completed[1:]:
        assert task["id"] in text


def test_handler_is_universal_and_uses_runtime_scope(store):
    handler = TodoListHandler()
    handler.set_user_id("u")
    handler.set_conversation_id("c")
    handler.set_agent_name("a")
    created = json.loads(handler.execute({
        "action": "create", "subject": "Ship it",
        "status": "in_progress"}))
    updated = json.loads(handler.execute({
        "action": "update", "task_id": created["id"],
        "status": "completed"}))
    listed = json.loads(handler.execute({"action": "list"}))
    assert created["status"] == "in_progress"
    assert updated["status"] == "completed"
    assert listed["tasks"][0]["id"] == created["id"]


def test_default_registry_exposes_todolist():
    assert create_default_registry().get("todolist") is not None


def test_cli_cold_context_injects_todos_before_bootstrap_contract(store, tmp_path):
    task = store.create("u", "c", "assistant", subject="Survive compaction")
    client = LLMClient("claude-code")
    messages = [
        LLMMessage(role="user", content="continue", conversation_id="c")]

    client._build_cli_initial_context_prompt(
        messages, system_prompt="rules", user_text="continue",
        workdir=str(tmp_path), provider_workdir="/provider",
        user_id="u", conversation_id="c", agent_name="assistant")

    body = (tmp_path / ".pawflow_cli" / "initial_context.md").read_text()
    assert "## Durable Todo List" in body
    assert task["id"] in body
    assert body.index("## Durable Todo List") < body.index("## Bootstrap Contract")
    assert (
        "resume and execute every pending or in-progress item"
        in body
    )
    assert (
        "Do not merely report that those items remain to be done."
        in body
    )


@pytest.mark.parametrize("scope", [
    ("", "c", "a"), ("u", "", "a"), ("u", "c", "")])
def test_missing_scope_is_rejected(store, scope):
    with pytest.raises(ValueError):
        store.list_tasks(*scope)
