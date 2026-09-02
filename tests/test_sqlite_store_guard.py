"""Fail-closed behaviour of durable SQLite stores on a damaged main file."""
import logging
import sqlite3
from pathlib import Path

import pytest

from core import FlowFile
from core.a2a_store import A2AStore
from core.agent_inbox_store import AgentInboxStore
from core.confirmation_store import UserInteractionStore
from core.conversation_index import ConversationIndex
from core.flow_run_store import FlowRunStore
from core.llm_routing_store import LLMRoutingStore
from core.media_project_store import MediaProjectStore
from core.scratchpad_store import ScratchpadStore
from core.sqlite_store_guard import (
    SqliteStoreGuard,
    SqliteStoreUnavailableError,
    is_corruption_error,
    preflight_main_file,
)
from core.ui_surface import make_ui_surface
from core.ui_surface_store import UiSurfaceStore
from core.usage_ledger import UsageLedger
from core.workflow_agent_invocation import WorkflowParentInvocationStore
from core.workflow_proposal_store import WorkflowProposalStore
from core.workflow_run_store import WorkflowRunStore
from core.todo_store import TodoStore
from tasks.ai.actions.ui_surfaces import _handle_ui_surfaces

# The production signature: one 24-byte TLS alert record written at byte 39
# of page 1 (header 17 03 03 00 13 followed by 19 encrypted bytes).
TLS_ALERT = bytes.fromhex("1703030013") + bytes(range(1, 20))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    for store_type in (
        UiSurfaceStore,
        A2AStore,
        UserInteractionStore,
        FlowRunStore,
        WorkflowProposalStore,
        TodoStore,
        ScratchpadStore,
    ):
        monkeypatch.setattr(store_type, "_instance", None)
    yield


def _corrupt_page_one(path):
    with open(path, "r+b") as handle:
        handle.seek(39)
        handle.write(TLS_ALERT)


def _create_then_corrupt(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT)")
    _corrupt_page_one(path)


def _snapshot(path):
    return {
        child.name: child.read_bytes() for child in path.parent.iterdir()
    }


def _surface():
    return make_ui_surface(
        user_id="alice", conversation_id="conv", producer_kind="task",
        producer_id="custom-review", surface_id="uis_one", revision=1,
        semantic={"role": "review", "title": "Review", "fields": [],
                  "actions": []})


def test_preflight_accepts_missing_and_healthy_files(tmp_path):
    database = tmp_path / "missing.sqlite3"
    preflight_main_file(database, "Test")
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE t(x)")
    connection.close()
    preflight_main_file(database, "Test")
    assert not (tmp_path / "missing.sqlite3-wal").exists()


def test_preflight_rejects_empty_and_corrupt_files(tmp_path):
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    with pytest.raises(sqlite3.DatabaseError, match="file is empty"):
        preflight_main_file(empty, "Test")
    corrupt = tmp_path / "corrupt.sqlite3"
    connection = sqlite3.connect(corrupt)
    connection.execute("CREATE TABLE t(x)")
    connection.close()
    _corrupt_page_one(corrupt)
    before = corrupt.read_bytes()
    with pytest.raises(sqlite3.DatabaseError) as info:
        preflight_main_file(corrupt, "Test")
    assert is_corruption_error(info.value)
    assert corrupt.read_bytes() == before
    assert not (tmp_path / "corrupt.sqlite3-wal").exists()


def test_guard_trips_once_and_fails_fast(tmp_path, caplog):
    guard = SqliteStoreGuard("Test")
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(SqliteStoreUnavailableError, match="file is empty"):
            guard.preflight(empty)
        with pytest.raises(SqliteStoreUnavailableError):
            guard.require_available()
    assert guard.available is False
    critical = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert "preserved" in critical[0].getMessage()


def test_ui_surface_store_fails_closed_and_preserves_evidence(caplog):
    store = UiSurfaceStore.instance()
    store.upsert(_surface(), user_id="alice", conversation_id="conv")
    path = store.database_path
    UiSurfaceStore.reset()
    for sidecar in ("-wal", "-shm"):
        candidate = path.with_name(path.name + sidecar)
        if candidate.exists():
            candidate.unlink()
    _corrupt_page_one(path)
    before = _snapshot(path)

    with caplog.at_level(logging.CRITICAL):
        store = UiSurfaceStore.instance()
    assert store.available is False
    assert UiSurfaceStore.instance() is store
    with pytest.raises(SqliteStoreUnavailableError, match="UI surface"):
        store.list(user_id="alice", conversation_id="conv")
    with pytest.raises(SqliteStoreUnavailableError):
        store.upsert(_surface(), user_id="alice", conversation_id="conv")
    assert _snapshot(path) == before
    assert sum(1 for r in caplog.records if r.levelno == logging.CRITICAL) == 1

    flowfile = FlowFile()
    result = _handle_ui_surfaces(
        None, "ui_surface_list", {"conversation_id": "conv"}, None,
        "alice", flowfile)
    assert result[0].get_attribute("http.response.status") == "503"
    assert b"unavailable" in result[0].get_content()


def test_agent_inbox_store_fails_closed_and_preserves_evidence(tmp_path, caplog):
    path = tmp_path / "inbox.sqlite3"
    inbox = AgentInboxStore(path)
    inbox.enqueue("c1", "claude", {"role": "user", "content": "hi",
                                   "msg_id": "m1", "ts": "t"}, "web", now=1)
    del inbox
    for sidecar in ("-wal", "-shm"):
        candidate = path.with_name(path.name + sidecar)
        if candidate.exists():
            candidate.unlink()
    _corrupt_page_one(path)
    before = _snapshot(path)

    with caplog.at_level(logging.CRITICAL):
        inbox = AgentInboxStore(path)
    assert inbox.available is False
    with pytest.raises(SqliteStoreUnavailableError, match="Agent inbox"):
        inbox.pending_count("c1", "claude")
    with pytest.raises(SqliteStoreUnavailableError):
        inbox.enqueue("c1", "claude", {"role": "user", "content": "x",
                                       "msg_id": "m2", "ts": "t"}, "web", now=2)
    assert _snapshot(path) == before
    assert sum(1 for r in caplog.records if r.levelno == logging.CRITICAL) == 1


def test_healthy_stores_keep_working_and_report_available(tmp_path):
    inbox = AgentInboxStore(tmp_path / "inbox.sqlite3")
    assert inbox.available is True
    assert inbox.pending_count("c1", "claude") == 0
    store = UiSurfaceStore.instance()
    assert store.available is True
    assert store.list(user_id="alice", conversation_id="conv") == []


@pytest.mark.parametrize("name,factory", [
    ("workflow_runs", lambda path: WorkflowRunStore(path)),
    ("flow_runs", lambda path: FlowRunStore(
        path, before_live_write=lambda: None)),
    ("media_projects", lambda path: MediaProjectStore(path)),
    ("a2a", lambda path: A2AStore(path)),
    ("workflow_proposals", lambda path: WorkflowProposalStore(
        path, before_live_write=lambda: None)),
    ("confirmations", lambda path: UserInteractionStore(path)),
    ("workflow_parent_invocations",
     lambda path: WorkflowParentInvocationStore(path)),
])
def test_short_lived_internal_stores_fail_closed_and_preserve_evidence(
        tmp_path, caplog, name, factory):
    path = tmp_path / f"{name}.sqlite3"
    _create_then_corrupt(path)
    before = _snapshot(path)

    with caplog.at_level(logging.CRITICAL):
        store = factory(path)

    assert store.available is False
    with pytest.raises(SqliteStoreUnavailableError):
        store._connect()
    assert _snapshot(path) == before
    assert sum(1 for r in caplog.records if r.levelno == logging.CRITICAL) == 1


@pytest.mark.parametrize("store_name,directory_attr,filename,factory", [
    ("todo", "TODOLISTS_DIR", "todos.sqlite3", TodoStore),
    ("scratchpad", "SCRATCHPADS_DIR", "scratchpads.sqlite3", ScratchpadStore),
])
def test_scoped_internal_stores_fail_closed_and_preserve_evidence(
        tmp_path, monkeypatch, caplog, store_name, directory_attr, filename,
        factory):
    from core import paths

    directory = tmp_path / store_name
    monkeypatch.setattr(paths, directory_attr, directory)
    path = directory / filename
    _create_then_corrupt(path)
    before = _snapshot(path)

    with caplog.at_level(logging.CRITICAL):
        store = factory()

    assert store.available is False
    with pytest.raises(SqliteStoreUnavailableError):
        store._connect()
    assert _snapshot(path) == before
    assert sum(1 for r in caplog.records if r.levelno == logging.CRITICAL) == 1


@pytest.mark.parametrize("name,factory", [
    ("conversation_index", lambda path: ConversationIndex("alice", str(path))),
    ("usage", lambda path: UsageLedger(str(path))),
    ("llm_routing", lambda path: LLMRoutingStore(str(path))),
])
def test_long_lived_internal_stores_reject_corrupt_database_without_mutation(
        tmp_path, caplog, name, factory):
    path = tmp_path / f"{name}.db"
    _create_then_corrupt(path)
    before = _snapshot(path)

    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(SqliteStoreUnavailableError):
            factory(path)

    assert _snapshot(path) == before
    assert sum(1 for r in caplog.records if r.levelno == logging.CRITICAL) == 1


def test_runtime_database_failure_trips_usage_ledger_once(tmp_path, caplog):
    ledger = UsageLedger(str(tmp_path / "usage.db"))
    original = ledger._conn

    class FailingConnection:
        calls = 0

        def execute(self, *_args, **_kwargs):
            self.calls += 1
            raise sqlite3.DatabaseError("database disk image is malformed")

    failed = FailingConnection()
    ledger._conn = failed
    try:
        with caplog.at_level(logging.CRITICAL):
            with pytest.raises(SqliteStoreUnavailableError):
                ledger.summary(user_id="alice")
            with pytest.raises(SqliteStoreUnavailableError):
                ledger.summary(user_id="alice")
        assert failed.calls == 1
        assert ledger.available is False
        assert sum(
            1 for record in caplog.records
            if record.levelno == logging.CRITICAL) == 1
    finally:
        original.close()


def test_restore_store_singletons_survive_corrupt_databases(tmp_path, monkeypatch):
    from core import paths

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "RUNTIME_DIR", tmp_path / "runtime")
    targets = (
        (tmp_path / "data" / "confirmations.db", UserInteractionStore),
        (tmp_path / "runtime" / "flow_runs.sqlite3", FlowRunStore),
        (tmp_path / "runtime" / "workflow_proposals.sqlite3",
         WorkflowProposalStore),
    )
    for path, store_type in targets:
        _create_then_corrupt(path)
        store_type._instance = None

    assert UserInteractionStore.instance().available is False
    assert FlowRunStore.instance().available is False
    assert WorkflowProposalStore.instance().available is False


def test_http_listener_skips_a2a_routes_when_store_is_corrupt(
        tmp_path, monkeypatch):
    from core import paths
    from core.mcp_server_store import MCPServerStore
    from services import http_listener_service as listener_module

    monkeypatch.setattr(paths, "SYSTEM_DIR", tmp_path / "system")
    path = paths.SYSTEM_DIR / "a2a.sqlite3"
    _create_then_corrupt(path)
    A2AStore._instance = None
    monkeypatch.setattr(listener_module, "_instances", {})
    monkeypatch.setattr(
        MCPServerStore, "instance",
        classmethod(lambda cls: type(
            "EmptyMCPStore", (), {"has_servers": lambda self: False})()))

    service = listener_module.HTTPListenerService({
        "host": "127.0.0.1", "port": 19865})

    assert service.get_routes() == []


def test_cli_cold_start_omits_unavailable_todo_and_scratchpad(
        tmp_path, monkeypatch):
    from core import paths
    from core.llm_client import LLMClient, LLMMessage

    monkeypatch.setattr(paths, "TODOLISTS_DIR", tmp_path / "todos")
    monkeypatch.setattr(paths, "SCRATCHPADS_DIR", tmp_path / "scratchpads")
    _create_then_corrupt(paths.TODOLISTS_DIR / "todos.sqlite3")
    _create_then_corrupt(paths.SCRATCHPADS_DIR / "scratchpads.sqlite3")
    TodoStore._instance = None
    ScratchpadStore._instance = None
    workdir = tmp_path / "session"

    LLMClient("claude-code")._build_cli_initial_context_prompt(
        [LLMMessage(role="user", content="hello", conversation_id="conv")],
        system_prompt="system", user_text="hello", workdir=str(workdir),
        provider_workdir="/provider", user_id="alice",
        conversation_id="conv", agent_name="assistant",
        rel_path="initial_context.md")

    body = (workdir / "initial_context.md").read_text(encoding="utf-8")
    assert "## Durable Todo List" not in body
    assert "## Scratchpad Hint" not in body


def test_every_core_sqlite_connect_site_has_an_explicit_policy():
    guarded = {
        "a2a_store.py", "agent_inbox_store.py", "confirmation_store.py",
        "conversation_index.py", "flow_run_store.py", "llm_routing_store.py",
        "media_project_store.py", "scratchpad_store.py", "todo_store.py",
        "ui_surface_store.py", "usage_ledger.py",
        "workflow_agent_invocation.py", "workflow_proposal_store.py",
        "workflow_run_store.py",
    }
    explicit_other_policy = {
        "mcp_server_store.py",       # custom fail-closed circuit breaker
        "scratchdir_store.py",       # disposable metadata quarantine
        "sqlite_boot_canary.py",     # read-only diagnostics
        "sqlite_store_guard.py",     # guard implementation
        "storage_backends/sqlite_storage.py",  # user-selected backend
    }
    core = Path("core")
    connect_sites = {
        str(path.relative_to(core))
        for path in core.rglob("*.py")
        if "sqlite3.connect(" in path.read_text(encoding="utf-8")
    }

    assert connect_sites == guarded | explicit_other_policy
    for relative in guarded:
        source = (core / relative).read_text(encoding="utf-8")
        assert "SqliteStoreGuard" in source, relative
