"""Server-side performance changes from docs/PERFORMANCE_SERVER_IMPLEMENTATION.md.

Covers:
- single-pass cursor history (``load_page(before_msg_id=...)``): equivalence
  with the former two-pass algorithm, trace alignment, rewrite/unknown cursor
  fallback and the decoded-row bound;
- amortized replay-buffer expiry sweeps on the conversation event bus;
- project maintenance scan cadence separated from wiki backlog processing;
- conversation index skipping the full-table title UPDATE when unchanged;
- tool batch worker ceiling and cancellation of queued calls;
- conversation writer backlog instrumentation.
"""

import logging
import threading
import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.conversation_store import ConversationStore


# ---------------------------------------------------------------------------
# Single-pass cursor history
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_store_singleton():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(store_dir=str(tmp_path / "conversations"))


def _row(role, content, **extra):
    row = {"role": role, "content": content, "msg_id": uuid.uuid4().hex[:12],
           "ts": time.time()}
    row.update(extra)
    return row


def _recount(store, cid):
    """Refresh the hot message count after writing the transcript directly."""
    with store._cache_lock:
        store._cache.pop(cid, None)
    cached = store._reload_cache(cid)
    store._persist_recomputed_hot_metadata(cid, cached)


def _write_transcript(store, rows):
    """Persist raw rows (messages, trace anchors, trace updates) in file order."""
    cid = store.generate_id()
    store.save(cid, [], user_id="alice")
    store._transcript_log(cid).replace_dicts(
        [store._stamp_line(cid, dict(row)) for row in rows])
    _recount(store, cid)
    return cid


def _legacy_offset_after_msg_id(log, msg_id):
    """The former first pass: display rows at or after the cursor."""
    offset = 0
    for line in log.iter_rows_reverse():
        if not line.get("role"):
            continue
        offset += 1
        if line.get("msg_id") == msg_id:
            return offset
    return None


def _legacy_page(store, cid, limit, before_msg_id):
    """The former two-pass result: resolve the cursor, then read the tail."""
    log = store._transcript_log(cid)
    offset = _legacy_offset_after_msg_id(log, before_msg_id)
    assert offset is not None
    return store._read_tail(log, store.message_count(cid), limit, offset)


def _mixed_rows(count):
    """Messages interleaved with tool rows, trace anchors and their updates."""
    rows = []
    for i in range(count):
        kind = i % 7
        if kind == 3:
            trace_id = f"trace-{i}"
            rows.append(_row("sub_agent_trace", f"trace {i}", trace_id=trace_id,
                             trace=[]))
            rows.append(_row("assistant", f"a{i}"))
            # Append-only updates land AFTER later rows.
            rows.append({"t": "trace_update", "trace_id": trace_id,
                         "entry": {"role": "assistant", "content": f"u{i}"},
                         "content_update": f"+{i}", "ts": time.time()})
        elif kind == 5:
            rows.append(_row("assistant", f"call {i}"))
            rows.append(_row("tool_call", f"tc {i}"))
            rows.append(_row("tool", f"result {i}"))
        else:
            rows.append(_row("user" if i % 2 else "assistant", f"m{i}"))
    return rows


class TestSinglePassCursorHistory:

    def test_matches_the_former_two_pass_result_at_every_cursor(self, store):
        rows = _mixed_rows(60)
        cid = _write_transcript(store, rows)
        display = [r for r in rows if r.get("role")]
        for limit in (1, 5, 13):
            for cursor in display[1:]:
                got = store.load_page(cid, limit=limit, offset=999,
                                      before_msg_id=cursor["msg_id"])
                expected = _legacy_page(store, cid, limit, cursor["msg_id"])
                assert got == expected, (limit, cursor["content"])

    def test_trace_updates_newer_than_the_cursor_reach_their_anchor(self, store):
        anchor = _row("sub_agent_trace", "anchor", trace_id="t1", trace=[])
        cursor = _row("user", "cursor")
        rows = [
            _row("user", "older"),
            anchor,
            cursor,
            _row("assistant", "newer"),
            {"t": "trace_update", "trace_id": "t1",
             "entry": {"role": "assistant", "content": "late"},
             "content_update": " more", "ts": time.time()},
        ]
        cid = _write_transcript(store, rows)

        page = store.load_page(cid, limit=10, before_msg_id=cursor["msg_id"])

        assert [m["content"] for m in page["messages"]] == ["older", "anchor more"]
        assert page["messages"][1]["trace"] == [
            {"role": "assistant", "content": "late"}]
        assert page["offset"] == 2
        assert page["has_more"] is False

    def test_an_anchor_newer_than_the_cursor_never_keeps_the_scan_open(
            self, store, monkeypatch):
        rows = [_row("user", f"m{i}") for i in range(200)]
        cursor = _row("user", "cursor")
        rows.append(cursor)
        rows.append(_row("sub_agent_trace", "new anchor", trace_id="t9", trace=[]))
        rows.append({"t": "trace_update", "trace_id": "t9",
                     "entry": {"role": "assistant", "content": "x"},
                     "ts": time.time()})
        cid = _write_transcript(store, rows)
        log = store._transcript_log(cid)
        decoded = []
        original = type(log).iter_rows_reverse

        def counting(self_log):
            for line in original(self_log):
                decoded.append(line)
                yield line
        monkeypatch.setattr(type(log), "iter_rows_reverse", counting)

        page = store.load_page(cid, limit=5, before_msg_id=cursor["msg_id"])

        assert [m["content"] for m in page["messages"]] == [
            f"m{i}" for i in range(195, 200)]
        assert not any(m.get("trace_id") == "t9" for m in page["messages"])
        # 3 rows at/after the cursor + limit + 20 margin, nowhere near 200.
        assert len(decoded) <= 3 + 5 + 20

    def test_deep_cursor_decodes_each_row_once(self, store, monkeypatch):
        rows = [_row("user", f"m{i}") for i in range(3000)]
        cid = _write_transcript(store, rows)
        log = store._transcript_log(cid)
        calls = []
        decoded = []
        original = type(log).iter_rows_reverse

        def counting(self_log):
            calls.append(1)
            for line in original(self_log):
                decoded.append(line)
                yield line
        monkeypatch.setattr(type(log), "iter_rows_reverse", counting)
        cursor = rows[1000]

        page = store.load_page(cid, limit=50, before_msg_id=cursor["msg_id"])

        assert len(calls) == 1
        assert page["offset"] == 2000
        assert [m["content"] for m in page["messages"]] == [
            f"m{i}" for i in range(950, 1000)]
        assert page["has_more"] is True
        # Former algorithm: 2000 (resolve) + 2070 (tail) rows decoded.
        assert len(decoded) == 2000 + 50 + 20

    def test_unknown_cursor_falls_back_to_the_numeric_offset(self, store):
        rows = [_row("user", f"m{i}") for i in range(10)]
        cid = _write_transcript(store, rows)

        page = store.load_page(cid, limit=3, offset=2, before_msg_id="nope")

        assert [m["content"] for m in page["messages"]] == ["m5", "m6", "m7"]
        assert page["offset"] == 2

    def test_cursor_survives_patch_and_truncate_rewrites(self, store):
        rows = [_row("user", f"m{i}") for i in range(10)]
        cid = _write_transcript(store, rows)
        store.patch_message(cid, rows[3]["msg_id"], content="patched")

        page = store.load_page(cid, limit=2, before_msg_id=rows[5]["msg_id"])
        assert [m["content"] for m in page["messages"]] == ["patched", "m4"]

        store._transcript_log(cid).truncate_after_msg_id(rows[4]["msg_id"])
        _recount(store, cid)
        # The old cursor row is gone: numeric offset takes over.
        page = store.load_page(cid, limit=2, offset=0,
                               before_msg_id=rows[5]["msg_id"])
        assert [m["content"] for m in page["messages"]] == ["patched", "m4"]
        assert page["total_count"] == 5

    def test_cursor_at_the_start_reports_no_more_history(self, store):
        rows = [_row("user", f"m{i}") for i in range(4)]
        cid = _write_transcript(store, rows)

        page = store.load_page(cid, limit=50, before_msg_id=rows[1]["msg_id"])

        assert [m["content"] for m in page["messages"]] == ["m0"]
        assert page["has_more"] is False
        first = store.load_page(cid, limit=50, before_msg_id=rows[0]["msg_id"])
        assert first["messages"] == []
        assert first["has_more"] is False


# ---------------------------------------------------------------------------
# Event bus: amortized expiry sweep
# ---------------------------------------------------------------------------

class TestAmortizedReplayExpiry:

    @pytest.fixture(autouse=True)
    def _bus(self):
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.reset()
        yield ConversationEventBus.instance()
        ConversationEventBus.reset()

    def test_buffered_publishes_within_the_interval_sweep_once(self, _bus, monkeypatch):
        sweeps = []
        original = _bus._cleanup_expired_buffers
        monkeypatch.setattr(
            _bus, "_cleanup_expired_buffers",
            lambda now=0.0: (sweeps.append(now), original(now))[1])

        for i in range(50):
            _bus.publish_event(f"conv-{i}", "new_message", {"msg_id": str(i)})

        assert len(sweeps) == 1
        assert _bus.buffer_stats()["conversations"] == 50

    def test_sweep_runs_again_once_the_interval_elapsed(self, _bus):
        import core.conversation_event_bus as bus_mod
        _bus.publish_event("stale", "new_message", {"msg_id": "old"})
        ts, event = _bus._buffer["stale"][0]
        _bus._buffer["stale"][0] = (ts - bus_mod._BUFFER_TTL - 1, event)
        _bus._last_buffer_sweep = time.monotonic() - bus_mod._BUFFER_SWEEP_INTERVAL - 1

        _bus.publish_event("other", "new_message", {"msg_id": "x"})

        assert "stale" not in _bus._buffer
        assert "other" in _bus._buffer

    def test_sweep_cadence_ignores_wall_clock_corrections(self, _bus, monkeypatch):
        """A backwards wall-clock step must not suppress the sweep, and a
        forwards step must not trigger one: cadence is monotonic, TTL is wall."""
        import core.conversation_event_bus as bus_mod
        clock = {"wall": 1_000_000.0, "mono": 5_000.0}
        monkeypatch.setattr(bus_mod, "time", SimpleNamespace(
            time=lambda: clock["wall"], monotonic=lambda: clock["mono"]))
        sweeps = []
        original = _bus._cleanup_expired_buffers
        monkeypatch.setattr(
            _bus, "_cleanup_expired_buffers",
            lambda now=0.0: (sweeps.append(now), original(now))[1])

        _bus.publish_event("a", "new_message", {"msg_id": "1"})
        assert len(sweeps) == 1
        # Wall clock jumps forward by an hour, monotonic barely moves: no sweep.
        clock["wall"] += 3600.0
        clock["mono"] += 0.1
        _bus.publish_event("b", "new_message", {"msg_id": "2"})
        assert len(sweeps) == 1
        # Wall clock steps back two hours, monotonic passes the interval: sweep.
        clock["wall"] -= 7200.0
        clock["mono"] += bus_mod._BUFFER_SWEEP_INTERVAL
        _bus.publish_event("c", "new_message", {"msg_id": "3"})
        assert len(sweeps) == 2
        # The sweep itself still expires on wall-clock age: "a" and "b" were
        # stamped an hour or more "in the future" and stay; "c" is fresh.
        assert set(_bus._buffer) == {"a", "b", "c"}
        clock["mono"] += bus_mod._BUFFER_SWEEP_INTERVAL
        clock["wall"] += 7200.0 + bus_mod._BUFFER_TTL + 1
        _bus.publish_event("d", "new_message", {"msg_id": "4"})
        assert len(sweeps) == 3
        assert set(_bus._buffer) == {"d"}
        assert _bus.buffer_stats()["last_sweep_age_s"] == 0.0

    def test_delivery_still_filters_by_age_between_sweeps(self, _bus):
        import core.conversation_event_bus as bus_mod
        _bus.publish_event("conv", "new_message", {"msg_id": "old"})
        _bus.publish_event("conv", "new_message", {"msg_id": "fresh"})
        ts, event = _bus._buffer["conv"][0]
        _bus._buffer["conv"][0] = (ts - bus_mod._BUFFER_TTL - 1, event)
        _bus._last_buffer_sweep = time.monotonic()  # no sweep is due

        writer = _bus.subscribe("conv", replay=True, client_id="tab")
        writer.close()
        chunks = list(writer.iterate(timeout=0.1))

        assert len(chunks) == 1
        assert b'"msg_id": "fresh"' in chunks[0]

    def test_buffer_stats_counts_events_and_sweep_age(self, _bus):
        _bus.publish_event("a", "new_message", {"msg_id": "1"})
        _bus.publish_event("a", "new_message", {"msg_id": "2"})
        _bus.publish_event("b", "new_message", {"msg_id": "3"})

        stats = _bus.buffer_stats()

        assert stats["conversations"] == 2
        assert stats["events"] == 3
        assert 0.0 <= stats["last_sweep_age_s"] < 5.0


# ---------------------------------------------------------------------------
# Project maintenance: scan cadence vs backlog processing
# ---------------------------------------------------------------------------

class _FakeTimer:
    def __init__(self, delay, fn, args=()):
        self.delay, self.fn, self.args = delay, fn, args
        self.cancelled = False
        self.daemon = False
        self.name = ""

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fn(*self.args)


@pytest.fixture
def maintenance(monkeypatch):
    import core.project_maintenance as pm
    monkeypatch.setattr(pm.threading, "Timer", _FakeTimer)
    scheduler = pm.ProjectMaintenanceScheduler()
    runs = []
    monkeypatch.setattr(scheduler, "_run", lambda job: runs.append(job.scan))
    return scheduler, runs, pm


class TestMaintenanceScanCadence:

    def test_no_ready_backlog_inside_the_interval_schedules_nothing(self, maintenance):
        scheduler, runs, pm = maintenance
        scheduler._wiki_backlog_ready = lambda u, r: False
        job = pm._MaintenanceJob(user_id="alice", relay_id="r", service=MagicMock(),
                                 last_scan_at=time.time(), last_run_at=time.time())
        scheduler._jobs["alice::r"] = job

        assert scheduler.schedule(user_id="alice", relay_id="r", service=job.service) is False
        assert job.timer is None

    def test_ready_backlog_inside_the_interval_runs_without_a_scan(self, maintenance):
        scheduler, runs, pm = maintenance
        scheduler._wiki_backlog_ready = lambda u, r: True
        last_scan = time.time() - 5
        job = pm._MaintenanceJob(user_id="alice", relay_id="r", service=MagicMock(),
                                 last_scan_at=last_scan,
                                 last_run_at=time.time() - pm._BACKLOG_PROCESS_SECONDS - 1)
        scheduler._jobs["alice::r"] = job

        assert scheduler.schedule(user_id="alice", relay_id="r", service=job.service) is True
        assert job.scan_pending is False
        job.timer.fire()

        assert runs == [False]
        assert job.last_scan_at == last_scan
        assert job.runs == 1 and job.scans == 0

    def test_process_only_runs_are_spaced_out(self, maintenance):
        scheduler, runs, pm = maintenance
        scheduler._wiki_backlog_ready = lambda u, r: True
        job = pm._MaintenanceJob(user_id="alice", relay_id="r", service=MagicMock(),
                                 last_scan_at=time.time(), last_run_at=time.time())
        scheduler._jobs["alice::r"] = job

        assert scheduler.schedule(user_id="alice", relay_id="r", service=job.service) is False

    def test_a_due_scan_runs_the_full_refresh(self, maintenance):
        scheduler, runs, pm = maintenance
        scheduler._wiki_backlog_ready = lambda u, r: False
        job = pm._MaintenanceJob(user_id="alice", relay_id="r", service=MagicMock(),
                                 last_scan_at=time.time() - pm._LAZY_REFRESH_SECONDS - 1)
        scheduler._jobs["alice::r"] = job

        assert scheduler.schedule(user_id="alice", relay_id="r", service=job.service) is True
        assert job.scan_pending is True
        job.timer.fire()

        assert runs == [True]
        assert job.scans == 1 and job.runs == 1
        assert time.time() - job.last_scan_at < 5

    def test_a_write_forces_a_scan_even_inside_the_interval(self, maintenance):
        scheduler, runs, pm = maintenance
        scheduler._wiki_backlog_ready = lambda u, r: False
        job = pm._MaintenanceJob(user_id="alice", relay_id="r", service=MagicMock(),
                                 last_scan_at=time.time(), last_run_at=time.time())
        scheduler._jobs["alice::r"] = job

        assert scheduler.schedule(user_id="alice", relay_id="r", service=job.service,
                                  changed_path="core/x.py") is True
        assert job.scan_pending is True
        assert job.changed_paths == {"core/x.py"}

    def test_a_backlog_request_never_downgrades_a_pending_scan(self, maintenance):
        scheduler, runs, pm = maintenance
        scheduler._wiki_backlog_ready = lambda u, r: True
        job = pm._MaintenanceJob(user_id="alice", relay_id="r", service=MagicMock(),
                                 last_scan_at=0.0, last_run_at=0.0)
        scheduler._jobs["alice::r"] = job
        assert scheduler.schedule(user_id="alice", relay_id="r", service=job.service) is True
        assert job.scan_pending is True
        job.last_scan_at = time.time()  # pretend a scan is no longer due

        assert scheduler.schedule(user_id="alice", relay_id="r", service=job.service) is True

        assert job.scan_pending is True
        job.timer.fire()
        assert runs == [True]

    def test_blocked_and_deferred_sources_are_not_ready(self, monkeypatch):
        import core.project_maintenance as pm
        wiki = MagicMock()
        wiki.status.return_value = {"dirty_sources": 3, "blocked_sources": 1,
                                    "deferred_sources": 2}
        monkeypatch.setattr("core.project_wiki.ProjectWiki.for_relay",
                            lambda *_a: wiki)
        assert pm.ProjectMaintenanceScheduler._wiki_backlog_ready("a", "r") is False

        wiki.status.return_value = {"dirty_sources": 3, "blocked_sources": 1,
                                    "deferred_sources": 1}
        assert pm.ProjectMaintenanceScheduler._wiki_backlog_ready("a", "r") is True

    def test_process_only_run_skips_graph_and_scan_but_updates_wiki(self, monkeypatch):
        import core.project_maintenance as pm
        graph_for_relay = MagicMock(side_effect=AssertionError("graph must not be touched"))
        wiki = MagicMock()
        wiki.scan_from_relay.side_effect = AssertionError("scan must not run")
        wiki.auto_update.return_value = {"status": "updated"}
        monkeypatch.setattr("core.project_graph.ProjectGraph.for_relay", graph_for_relay)
        monkeypatch.setattr("core.project_wiki.ProjectWiki.for_relay", lambda *_a: wiki)
        client = MagicMock()
        summarizer = MagicMock()
        summarizer.resolve_llm_service.return_value = (client, 100_000, "wiki-llm")
        monkeypatch.setattr("core.summarizer_bindings.resolve_service",
                            MagicMock(return_value=(summarizer, MagicMock(), True)))
        service = MagicMock()
        job = pm._MaintenanceJob(user_id="alice", relay_id="r", service=service,
                                 conversation_id="conv", agent_name="assistant",
                                 scan=False)

        pm.ProjectMaintenanceScheduler()._run(job)

        wiki.auto_update.assert_called_once_with(service, client, local=False)

    def test_snapshot_exposes_scan_counters(self, maintenance):
        scheduler, runs, pm = maintenance
        job = pm._MaintenanceJob(user_id="alice", relay_id="r", service=MagicMock(),
                                 runs=3, scans=1, last_scan_at=12.0)
        scheduler._jobs["alice::r"] = job

        snap = scheduler.snapshot("alice", "r")

        assert snap["runs"] == 3 and snap["scans"] == 1
        assert snap["last_scan_at"] == 12.0
        assert snap["scan_pending"] is False


# ---------------------------------------------------------------------------
# Conversation index: unchanged-title UPDATE skip
# ---------------------------------------------------------------------------

class TestIndexTitleUpdateSkip:

    @pytest.fixture(autouse=True)
    def _index_dir(self, tmp_path, monkeypatch):
        import core.paths as _paths
        from core.conversation_index import ConversationIndex
        monkeypatch.setattr(_paths, "CONVERSATION_INDEX_DIR",
                            tmp_path / "conversation_index")
        ConversationIndex.reset()
        yield
        ConversationIndex.reset()

    @staticmethod
    def _updates(index, monkeypatch):
        """Record every statement; sqlite connections cannot be patched in place."""
        executed = []

        class _Recording:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *args):
                executed.append(" ".join(str(sql).split()))
                return self._conn.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        monkeypatch.setattr(index, "_conn", _Recording(index._conn))
        return executed

    def test_appending_to_a_conversation_with_the_same_title_runs_no_update(
            self, store, monkeypatch):
        from core.conversation_index import ConversationIndex
        cid = store.generate_id()
        store.save(cid, [{"role": "user", "content": "first"}], user_id="alice")
        store.set_extra(cid, "title", "Stable", user_id="alice")
        index = ConversationIndex.for_user("alice")
        index.refresh(store)
        store.append_message(cid, _row("assistant", "second",
                                       source={"type": "agent", "name": "bot"}),
                             user_id="alice")
        executed = self._updates(index, monkeypatch)

        index.refresh(store)

        assert not [s for s in executed if s.startswith("UPDATE messages")]
        assert index.search("second")[0]["title"] == "Stable"

    def test_a_rename_still_rewrites_the_old_rows(self, store, monkeypatch):
        from core.conversation_index import ConversationIndex
        cid = store.generate_id()
        store.save(cid, [{"role": "user", "content": "pick a codec"}], user_id="alice")
        store.set_extra(cid, "title", "Old", user_id="alice")
        index = ConversationIndex.for_user("alice")
        index.refresh(store)
        store.set_extra(cid, "title", "Codec choice", user_id="alice")
        executed = self._updates(index, monkeypatch)

        index.refresh(store)

        assert len([s for s in executed if s.startswith("UPDATE messages")]) == 1
        assert index.search("codec")[0]["title"] == "Codec choice"

    def test_a_new_conversation_runs_no_update(self, store, monkeypatch):
        from core.conversation_index import ConversationIndex
        index = ConversationIndex.for_user("alice")
        index.refresh(store)
        cid = store.generate_id()
        store.save(cid, [{"role": "user", "content": "hello"}], user_id="alice")
        store.set_extra(cid, "title", "Fresh", user_id="alice")
        executed = self._updates(index, monkeypatch)

        index.refresh(store)

        assert not [s for s in executed if s.startswith("UPDATE messages")]
        assert index.search("hello")[0]["title"] == "Fresh"

    def test_the_title_update_is_a_full_table_scan(self):
        """Why the skip matters: conversation_id is UNINDEXED in FTS5."""
        from core.conversation_index import ConversationIndex
        index = ConversationIndex.for_user("alice")
        plan = index._conn.execute(
            "EXPLAIN QUERY PLAN UPDATE messages SET title = ? "
            "WHERE conversation_id = ? AND title != ?", ("t", "c", "t")).fetchall()
        detail = " ".join(str(row[-1]) for row in plan).upper()
        assert "SCAN" in detail and "MESSAGES" in detail


# ---------------------------------------------------------------------------
# Tool batch worker ceiling
# ---------------------------------------------------------------------------

class TestToolBatchWorkers:

    def test_default_ceiling_and_small_batches(self, monkeypatch):
        from tasks.ai.agent_tool_exec import tool_batch_max_workers
        monkeypatch.delenv("PAWFLOW_TOOL_BATCH_MAX_WORKERS", raising=False)
        assert tool_batch_max_workers(0) == 1
        assert tool_batch_max_workers(3) == 3
        assert tool_batch_max_workers(40) == 8

    def test_env_override(self, monkeypatch):
        from tasks.ai.agent_tool_exec import tool_batch_max_workers
        monkeypatch.setenv("PAWFLOW_TOOL_BATCH_MAX_WORKERS", "2")
        assert tool_batch_max_workers(40) == 2

    @pytest.mark.parametrize("raw", ["0", "-3", "many"])
    def test_invalid_env_is_a_configuration_error(self, monkeypatch, raw):
        from tasks.ai.agent_tool_exec import tool_batch_max_workers
        monkeypatch.setenv("PAWFLOW_TOOL_BATCH_MAX_WORKERS", raw)
        with pytest.raises(ValueError):
            tool_batch_max_workers(4)


def _tool_registry(handler):
    from core.tool_registry import ToolRegistry
    registry = ToolRegistry()
    registry.register(handler)
    return registry


class _Agent:
    def _run_hook(self, *args, **kwargs):
        return None


def _agent():
    from tasks.ai.agent_tool_exec import AgentToolExecMixin

    class Agent(AgentToolExecMixin, _Agent):
        pass
    return Agent()


class TestToolBatchExecution:

    @pytest.fixture(autouse=True)
    def _approve(self, monkeypatch):
        from core.tool_approval import ToolApprovalGate
        monkeypatch.setattr(ToolApprovalGate, "check",
                            lambda *args, **kwargs: "approved")

    def test_calls_past_the_ceiling_queue_and_keep_their_order(self, monkeypatch):
        from core.llm_client import LLMToolCall
        from core.tool_handler import ToolHandler
        monkeypatch.setenv("PAWFLOW_TOOL_BATCH_MAX_WORKERS", "3")
        state = {"active": 0, "peak": 0}
        lock = threading.Lock()

        class Slow(ToolHandler):
            name = "slow"
            description = "sleep"

            @property
            def parameters_schema(self):
                return {"type": "object", "properties": {}}

            def execute(self, arguments):
                with lock:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                time.sleep(0.05)
                with lock:
                    state["active"] -= 1
                return f"done {arguments['n']}"

        calls = [LLMToolCall(id=f"tc-{n}", name="slow", arguments={"n": n})
                 for n in range(12)]

        results = _agent()._execute_tool_calls(
            calls, _tool_registry(Slow()), {}, 100,
            agent_name="bot", conversation_id="conv-1", user_id="user-1")

        assert [r[1] for r in results] == [f"done {n}" for n in range(12)]
        assert state["peak"] == 3

    def test_cancelling_a_batch_releases_queued_owner_reservations(self, monkeypatch):
        import core.background_tool as _bg
        from core.llm_client import LLMToolCall
        from core.tool_handler import ToolHandler
        monkeypatch.setenv("PAWFLOW_TOOL_BATCH_MAX_WORKERS", "1")
        started = threading.Event()
        release = threading.Event()

        class Blocking(ToolHandler):
            name = "block"
            description = "wait"

            @property
            def parameters_schema(self):
                return {"type": "object", "properties": {}}

            def execute(self, arguments):
                started.set()
                release.wait(timeout=5)
                return "released"

        calls = [LLMToolCall(id=f"tc-{n}", name="block", arguments={})
                 for n in range(3)]

        def cancel_check():
            if started.is_set():
                raise RuntimeError("interrupted")

        try:
            results = _agent()._execute_tool_calls(
                calls, _tool_registry(Blocking()), {}, 100,
                agent_name="bot", conversation_id="conv-1", user_id="user-1",
                cancel_check=cancel_check)
        finally:
            release.set()

        assert [r[1] for r in results[1:]] == [
            "[Cancelled — agent was interrupted]"] * 2
        with _bg._lock:
            assert "tc-1" not in _bg._pending_owners
            assert "tc-2" not in _bg._pending_owners

    def test_a_queued_call_can_be_backgrounded_and_still_completes(self, monkeypatch):
        """Ceiling 1: the first call holds the only worker, the user backgrounds
        the queued second call, the batch returns, and the queued call later
        runs, delivers its result through the background registry and releases
        its ownership reservation."""
        import core.background_tool as _bg
        from core.llm_client import LLMToolCall
        from core.tool_handler import ToolHandler
        monkeypatch.setenv("PAWFLOW_TOOL_BATCH_MAX_WORKERS", "1")
        injected = []
        monkeypatch.setattr(
            _bg, "_inject_result",
            lambda tc_id, text, is_cancel=False: injected.append((tc_id, text)))
        first_started = threading.Event()
        release_first = threading.Event()

        class Hold(ToolHandler):
            name = "hold"
            description = "first call blocks, later calls return"

            @property
            def parameters_schema(self):
                return {"type": "object", "properties": {}}

            def execute(self, arguments):
                if arguments["n"] == 0:
                    first_started.set()
                    release_first.wait(timeout=10)
                return f"done {arguments['n']}"

        calls = [LLMToolCall(id=f"tc-{n}", name="hold", arguments={"n": n})
                 for n in range(2)]

        def user_backgrounds_the_queued_call():
            assert first_started.wait(timeout=5)
            _bg.background("tc-1")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with _bg._lock:
                    if "tc-1" in _bg._backgrounded:
                        break
                time.sleep(0.05)
            release_first.set()

        helper = threading.Thread(target=user_backgrounds_the_queued_call)
        helper.start()
        try:
            results = _agent()._execute_tool_calls(
                calls, _tool_registry(Hold()), {}, 100,
                agent_name="bot", conversation_id="conv-1", user_id="user-1")
        finally:
            release_first.set()
            helper.join(timeout=5)

        assert results[0][1] == "done 0"
        assert results[1][1].startswith("[Running in background (tc_id=tc-1)]")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not injected:
            time.sleep(0.05)
        assert injected == [("tc-1", "done 1")]
        with _bg._lock:
            task = _bg._backgrounded.pop("tc-1")
            assert task["status"] == "done"
            assert "tc-0" not in _bg._pending_owners
            assert "tc-1" not in _bg._pending_owners
            assert "tc-1" not in _bg._pending_bg


# ---------------------------------------------------------------------------
# Conversation writer backlog instrumentation
# ---------------------------------------------------------------------------

def _writer_msg(content="x"):
    return {"role": "assistant", "content": content, "ts": time.time(),
            "msg_id": uuid.uuid4().hex[:12]}


class _WriterStore:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.written = []
        self.gate = threading.Event()
        self.gate.set()

    def append_message(self, cid, msg, agent_name="", user_id="", ttl=0):
        self.gate.wait(timeout=5)
        if self.delay:
            time.sleep(self.delay)
        self.written.append(msg["content"])

    def flush_append_handles(self, cid):
        pass


@pytest.fixture
def writer_store(monkeypatch):
    from core import conversation_store as _cs
    from core.conversation_writer import ConversationWriter
    fs = _WriterStore()
    monkeypatch.setattr(_cs.ConversationStore, "instance",
                        classmethod(lambda _c: fs))
    yield fs
    with ConversationWriter._global_lock:
        for w in ConversationWriter._instances.values():
            w._stop = True
        ConversationWriter._instances.clear()


class TestWriterBacklogInstrumentation:

    def test_snapshot_reports_depth_age_and_latency(self, writer_store):
        from core.conversation_writer import ConversationWriter
        writer_store.gate.clear()
        writer = ConversationWriter.for_conversation("conv-backlog")
        for i in range(3):
            writer.enqueue_message(_writer_msg(f"m{i}"))
        time.sleep(0.05)

        before = ConversationWriter.backlog_snapshot()
        assert before["queued_total"] == 3  # queued + in-flight batch
        assert before["oldest_age_s"] > 0.0

        writer_store.gate.set()
        assert writer.flush(timeout=5) is True
        state = writer.backlog_state()

        assert state["queued"] == 0
        assert state["persisted"] == 3
        assert state["batches"] >= 1
        assert state["max_latency_ms"] >= 40.0
        assert ConversationWriter.backlog_snapshot()["queued_total"] == 0

    def test_deep_queue_warns_once_per_interval(self, writer_store, monkeypatch, caplog):
        import core.conversation_writer as cw
        monkeypatch.setattr(cw, "_WRITER_BACKLOG_WARN_ITEMS", 2)
        writer_store.gate.clear()
        writer = cw.ConversationWriter.for_conversation("conv-warn")
        with caplog.at_level(logging.WARNING, logger="core.conversation_writer"):
            for i in range(6):
                writer.enqueue_message(_writer_msg(f"m{i}"))
        writer_store.gate.set()
        writer.flush(timeout=5)

        warnings = [r for r in caplog.records if "persistence backlog" in r.getMessage()]
        assert len(warnings) == 1
        assert "queued=" in warnings[0].getMessage()

    def test_slow_persistence_warns_on_latency(self, writer_store, monkeypatch, caplog):
        import core.conversation_writer as cw
        monkeypatch.setattr(cw, "_WRITER_BACKLOG_WARN_SECONDS", 0.02)
        writer_store.delay = 0.05
        writer = cw.ConversationWriter.for_conversation("conv-slow")
        with caplog.at_level(logging.WARNING, logger="core.conversation_writer"):
            writer.enqueue_message(_writer_msg("slow"))
            writer.flush(timeout=5)

        assert any("enqueue-to-persist latency" in r.getMessage()
                   for r in caplog.records)
