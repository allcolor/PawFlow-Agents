"""Tests for runtime infrastructure: TaskState, Connection."""

import pytest

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile
from core.task_state import TaskState, TaskStateManager
from core.connection import Connection, ConnectionManager
from core.prioritizer import PrioritizerType


class TestTaskStateManager:

    def setup_method(self):
        self.mgr = TaskStateManager()

    def test_register_and_get(self):
        self.mgr.register_task("t1", "log")
        assert self.mgr.get_state("t1") == TaskState.STOPPED

    def test_start_stop(self):
        self.mgr.register_task("t1")
        assert self.mgr.start("t1")
        assert self.mgr.get_state("t1") == TaskState.RUNNING
        assert self.mgr.stop("t1")
        assert self.mgr.get_state("t1") == TaskState.STOPPED

    def test_cannot_start_disabled(self):
        self.mgr.register_task("t1")
        self.mgr.disable("t1")
        assert not self.mgr.start("t1")

    def test_error_state(self):
        self.mgr.register_task("t1")
        self.mgr.set_error("t1", "something broke")
        assert self.mgr.get_state("t1") == TaskState.ERROR
        info = self.mgr.get_info("t1")
        assert info.error_message == "something broke"
        assert info.error_count == 1

    def test_start_from_error(self):
        self.mgr.register_task("t1")
        self.mgr.set_error("t1", "err")
        assert self.mgr.start("t1")
        assert self.mgr.get_state("t1") == TaskState.RUNNING

    def test_invalid_cannot_stop(self):
        self.mgr.register_task("t1")
        self.mgr.set_invalid("t1", "bad config")
        assert not self.mgr.stop("t1")

    def test_disable_enable(self):
        self.mgr.register_task("t1")
        assert self.mgr.disable("t1")
        assert self.mgr.get_state("t1") == TaskState.DISABLED
        assert self.mgr.enable("t1")
        assert self.mgr.get_state("t1") == TaskState.STOPPED

    def test_enable_non_disabled_fails(self):
        self.mgr.register_task("t1")
        assert not self.mgr.enable("t1")  # already STOPPED

    def test_record_run(self):
        self.mgr.register_task("t1")
        self.mgr.record_run("t1", ff_in=2, ff_out=3, bytes_in=100, bytes_out=200)
        info = self.mgr.get_info("t1")
        assert info.run_count == 1
        assert info.flowfiles_in == 2
        assert info.bytes_out == 200

    def test_get_tasks_by_state(self):
        self.mgr.register_task("a")
        self.mgr.register_task("b")
        self.mgr.register_task("c")
        self.mgr.start("b")
        assert self.mgr.get_tasks_by_state(TaskState.STOPPED) == ["a", "c"]
        assert self.mgr.get_tasks_by_state(TaskState.RUNNING) == ["b"]

    def test_is_runnable(self):
        self.mgr.register_task("t1")
        assert not self.mgr.is_runnable("t1")
        self.mgr.start("t1")
        assert self.mgr.is_runnable("t1")

    def test_get_all_states(self):
        self.mgr.register_task("t1", "log")
        states = self.mgr.get_all_states()
        assert "t1" in states
        assert states["t1"]["task_type"] == "log"

    def test_nonexistent_task(self):
        assert self.mgr.get_state("nope") is None
        assert not self.mgr.start("nope")


class TestConnection:

    def test_enqueue_dequeue(self):
        conn = Connection("a", "b")
        ff = FlowFile(content=b"hello")
        assert conn.enqueue(ff)
        result = conn.dequeue()
        assert result.get_content() == b"hello"

    def test_backpressure_by_count(self):
        conn = Connection("a", "b", max_queue_size=2)
        assert conn.enqueue(FlowFile(content=b"1"))
        assert conn.enqueue(FlowFile(content=b"2"))
        assert not conn.enqueue(FlowFile(content=b"3"))
        assert conn.is_backpressured()

    def test_backpressure_by_bytes(self):
        conn = Connection("a", "b", max_queue_bytes=10)
        assert conn.enqueue(FlowFile(content=b"12345678"))
        assert not conn.enqueue(FlowFile(content=b"12345"))  # would exceed 10
        # Queue has 8 bytes, under 10 threshold, but enqueue was rejected
        assert conn.enqueue(FlowFile(content=b"ab"))  # 8+2=10, exactly at limit
        assert conn.is_backpressured()  # now at threshold

    def test_queue_stats(self):
        conn = Connection("a", "b", relationship="success")
        conn.enqueue(FlowFile(content=b"data"))
        stats = conn.get_stats()
        assert stats["source"] == "a"
        assert stats["target"] == "b"
        assert stats["queue_size"] == 1
        assert stats["flowfiles_in"] == 1

    def test_clear(self):
        conn = Connection("a", "b")
        conn.enqueue(FlowFile(content=b"data"))
        conn.clear()
        assert conn.is_empty()

    def test_peek(self):
        conn = Connection("a", "b")
        conn.enqueue(FlowFile(content=b"peek"))
        assert conn.peek().get_content() == b"peek"
        assert conn.queue_size() == 1


class TestConnectionManager:

    def test_build_from_flow(self):
        flow = {
            "tasks": {"a": {"type": "log"}, "b": {"type": "log"}},
            "relations": [{"from": "a", "to": "b", "type": "success"}],
        }
        mgr = ConnectionManager()
        mgr.build_from_flow(flow)
        assert len(mgr.get_outgoing("a")) == 1
        assert len(mgr.get_incoming("b")) == 1

    def test_any_backpressured(self):
        mgr = ConnectionManager()
        conn = Connection("a", "b", max_queue_size=1)
        mgr.add_connection(conn)
        conn.enqueue(FlowFile(content=b"x"))
        assert mgr.any_backpressured("a")
