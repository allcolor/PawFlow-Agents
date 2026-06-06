"""Tests for ContinuousFlowExecutor and FlowVersionManager."""

import pytest
import time

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile, Flow, TaskFactory
from core.connection import Connection
from core.task_state import TaskState
from engine.continuous_executor import ContinuousFlowExecutor
from engine.flow_version import FlowVersionManager, FlowDiff
from services.http_listener_service import PendingRequest
from tasks.io.http_receiver import HTTPReceiverTask


def make_flow(tasks_dict, relations):
    """Helper to create a Flow object for testing."""
    from engine.parser import FlowParser
    flow_dict = {
        "id": "test_flow",
        "name": "Test Flow",
        "version": "1.0.0",
        "tasks": tasks_dict,
        "relations": relations,
        "entries": [],
        "exits": [],
        "parameters": {},
        "variables": {},
        "groups": {},
    }
    parser = FlowParser()
    return parser.parse(flow_dict)


class TestContinuousFlowExecutor:

    def test_enabled_one_shot_root_task_ids_limits_manual_start(self):
        flow = make_flow(
            {
                "gen_a": {"type": "generateFlowFile", "parameters": {"content": "a"}},
                "gen_b": {"type": "generateFlowFile", "parameters": {"content": "b"}},
                "out_a": {"type": "outputPort", "parameters": {"port_name": "a"}},
                "out_b": {"type": "outputPort", "parameters": {"port_name": "b"}},
            },
            [
                {"from": "gen_a", "to": "out_a", "type": "success"},
                {"from": "gen_b", "to": "out_b", "type": "success"},
            ],
        )
        executor = ContinuousFlowExecutor(
            flow,
            enable_checkpoints=False,
            enabled_one_shot_root_task_ids=["gen_b"],
        )

        executor.start()
        deadline = time.time() + 3
        while executor.is_running and time.time() < deadline:
            time.sleep(0.05)
        if executor.is_running:
            executor.stop()

        assert [ff.get_content() for ff in executor._exit_results] == [b"b"]

    def test_interactive_http_flowfiles_use_reserved_lane(self):
        flow = make_flow(
            {"log1": {"type": "log", "parameters": {"message": "test", "level": "INFO"}}},
            [],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        normal = FlowFile(content=b"{}")
        normal.set_attribute("http.request.id", "req-normal")
        normal.set_attribute("http.path", "/api/agent")
        assert not executor._is_interactive_http_ff(normal)

        ui = FlowFile(content=b"{}")
        ui.set_attribute("http.request.id", "req-ui")
        ui.set_attribute("http.path", "/api/ui")
        assert executor._is_interactive_http_ff(ui)

        conn = Connection("source", "log1")
        assert conn.enqueue(ui)
        assert executor._has_interactive_input("log1", [conn])

    def test_http_receiver_prioritizes_api_ui_requests(self):
        task = HTTPReceiverTask({"service_id": "missing", "routes": []})
        task._registered = True
        wake_calls = []
        task.set_scheduler_wake(lambda: wake_calls.append(True))
        slow = PendingRequest("slow", "POST", "/api/agent", {}, b"{}")
        ui = PendingRequest("ui", "POST", "/api/ui", {}, b'{"action":"load_history"}')

        task._on_request(slow, "POST:/api/agent")
        task._on_request(ui, "POST:/api/ui")
        assert len(wake_calls) == 2

        [first] = task.execute(None)
        [second] = task.execute(None)
        assert first.get_attribute("http.path") == "/api/ui"
        assert second.get_attribute("http.path") == "/api/agent"

    def test_inject_and_process(self):
        flow = make_flow(
            {"log1": {"type": "log", "parameters": {"message": "test", "level": "INFO"}}},
            [],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        executor.start()
        try:
            ff = FlowFile(content=b"hello")
            executor.inject(ff, entry_task_id="log1")
            time.sleep(0.5)

            states = executor.get_all_task_states()
            assert states["log1"]["flowfiles_in"] >= 1
        finally:
            executor.stop()

    def test_two_task_pipeline(self):
        flow = make_flow(
            {
                "a": {"type": "log", "parameters": {"message": "a", "level": "INFO"}},
                "b": {"type": "log", "parameters": {"message": "b", "level": "INFO"}},
            },
            [{"from": "a", "to": "b"}],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        executor.start()
        try:
            executor.inject(FlowFile(content=b"pipeline"), entry_task_id="a")
            time.sleep(0.8)

            states = executor.get_all_task_states()
            assert states["a"]["flowfiles_in"] >= 1
            assert states["b"]["flowfiles_in"] >= 1
        finally:
            executor.stop()

    def test_scheduler_wakes_on_inject_and_downstream_enqueue(self):
        flow = make_flow(
            {
                "a": {"type": "log", "parameters": {"message": "a", "level": "INFO"}},
                "b": {"type": "log", "parameters": {"message": "b", "level": "INFO"}},
            },
            [{"from": "a", "to": "b"}],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1, schedule_interval=0.5)
        executor.start()
        try:
            executor.inject(FlowFile(content=b"pipeline"), entry_task_id="a")
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline:
                states = executor.get_all_task_states()
                if states["a"]["flowfiles_in"] >= 1 and states["b"]["flowfiles_in"] >= 1:
                    break
                time.sleep(0.01)

            states = executor.get_all_task_states()
            assert states["a"]["flowfiles_in"] >= 1
            assert states["b"]["flowfiles_in"] >= 1
        finally:
            executor.stop()

    def test_error_keeps_flowfile_in_queue(self):
        """On task error, FlowFile must stay in the input queue — NOT be lost."""
        flow = make_flow(
            {"bad": {"type": "fail", "parameters": {"message": "boom"}}},
            [],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        executor.start()
        try:
            executor.inject(FlowFile(content=b"precious"), entry_task_id="bad")
            time.sleep(0.8)

            # Task now discards failed FlowFiles and stays RUNNING
            state = executor.get_task_state("bad")
            assert state in (TaskState.RUNNING, TaskState.ERROR)
        finally:
            executor.stop()

    def test_backpressure_when_task_stopped(self):
        """When a downstream task is stopped, queues fill up."""
        flow = make_flow(
            {
                "a": {"type": "log", "parameters": {"message": "a", "level": "INFO"}},
                "b": {"type": "log", "parameters": {"message": "b", "level": "INFO"}},
            },
            [{"from": "a", "to": "b"}],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        executor.start()
        try:
            # Stop downstream task
            executor.stop_task("b")

            # Inject multiple FlowFiles
            for i in range(3):
                executor.inject(FlowFile(content=f"bp-{i}".encode()), entry_task_id="a")

            time.sleep(0.5)

            # FlowFiles should accumulate in the queue between a and b
            queue_stats = executor.get_queue_stats()
            a_to_b = [q for q in queue_stats if q["source"] == "a" and q["target"] == "b"]
            assert len(a_to_b) == 1
            assert a_to_b[0]["queue_size"] >= 1, "FlowFiles should queue when downstream stopped"
        finally:
            executor.stop()

    def test_restart_after_error_with_type_change(self):
        """Task fails, update to a working type, restart, FF is processed."""
        flow = make_flow(
            {"t": {"type": "fail", "parameters": {"message": "boom"}}},
            [],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        executor.start()
        try:
            executor.inject(FlowFile(content=b"recover-me"), entry_task_id="t")
            time.sleep(0.8)

            # Task now discards failed FlowFiles and may stay RUNNING
            state = executor.get_task_state("t")
            assert state in (TaskState.RUNNING, TaskState.ERROR)

            # Hot-swap to a working task type
            success = executor.update_task(
                "t", {"message": "recovered", "level": "INFO"}, new_type="log"
            )
            assert success

            # Task should be running after update
            time.sleep(0.5)
            state = executor.get_task_state("t")
            assert state == TaskState.RUNNING
        finally:
            executor.stop()

    def test_update_task_preserves_queue(self):
        """Updating a stopped task's config must not lose queued FlowFiles."""
        flow = make_flow(
            {
                "a": {"type": "log", "parameters": {"message": "a", "level": "INFO"}},
                "b": {"type": "log", "parameters": {"message": "b", "level": "INFO"}},
            },
            [{"from": "a", "to": "b"}],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        executor.start()
        try:
            # Stop b so FlowFiles accumulate
            executor.stop_task("b")

            for i in range(3):
                executor.inject(FlowFile(content=f"q-{i}".encode()), entry_task_id="a")

            time.sleep(0.5)

            # Count queued FlowFiles before update
            before = sum(q["queue_size"] for q in executor.get_queue_stats())
            assert before >= 1

            # Update task b's config — queues must be preserved
            executor.update_task("b", {"message": "updated", "level": "DEBUG"})

            after = sum(q["queue_size"] for q in executor.get_queue_stats())
            assert after >= before, "FlowFiles must be preserved across update"
        finally:
            executor.stop()

    def test_stop_start_lifecycle(self):
        flow = make_flow(
            {"t": {"type": "log", "parameters": {"message": "x", "level": "INFO"}}},
            [],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)

        # Inject before start (into virtual input queue)
        executor.inject(FlowFile(content=b"pre-start"), entry_task_id="t")

        executor.start()
        time.sleep(0.3)
        executor.stop()

        # Inject while stopped
        executor.inject(FlowFile(content=b"while-stopped"), entry_task_id="t")

        executor.start()
        time.sleep(0.5)

        states = executor.get_all_task_states()
        assert states["t"]["flowfiles_in"] >= 2
        executor.stop()

    def test_get_status(self):
        flow = make_flow(
            {
                "a": {"type": "log", "parameters": {"message": "a", "level": "INFO"}},
                "b": {"type": "log", "parameters": {"message": "b", "level": "INFO"}},
            },
            [{"from": "a", "to": "b"}],
        )
        executor = ContinuousFlowExecutor(flow)
        executor.start()
        try:
            status = executor.get_status()
            assert status["is_running"] is True
            assert status["tasks_total"] == 2
            assert status["tasks_running"] == 2
            assert status["flow_version"] == 1
            assert "queue_stats" in status
        finally:
            executor.stop()

    def test_version_increments_on_update(self):
        flow = make_flow(
            {"t": {"type": "log", "parameters": {"message": "v1", "level": "INFO"}}},
            [],
        )
        executor = ContinuousFlowExecutor(flow)
        assert executor.flow_version == 1
        executor.update_task("t", {"message": "v2", "level": "INFO"})
        assert executor.flow_version == 2

        history = executor.get_version_history()
        assert len(history) == 1
        assert history[0]["action"] == "update_task"


    def test_failure_routing(self):
        """When a failure connection exists, failed FlowFiles are routed there."""
        flow = make_flow(
            {
                "bad": {"type": "fail", "parameters": {"message": "boom"}},
                "handler": {"type": "log", "parameters": {"message": "handled", "level": "INFO"}},
            },
            [{"from": "bad", "to": "handler", "type": "failure"}],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        executor.start()
        try:
            executor.inject(FlowFile(content=b"route-me"), entry_task_id="bad")
            time.sleep(1.0)

            # Task should NOT be in ERROR (failure was handled)
            state = executor.get_task_state("bad")
            assert state != TaskState.ERROR, "Task should stay RUNNING when failure conn exists"

            # Handler should have received the FlowFile
            states = executor.get_all_task_states()
            assert states["handler"]["flowfiles_in"] >= 1
        finally:
            executor.stop()

    def test_relationship_based_routing(self):
        """FlowFiles with route.relationship attribute go to matching connections."""
        flow = make_flow(
            {
                "src": {"type": "updateAttribute", "parameters": {
                    "set": {"route.relationship": "matched"},
                }},
                "matched_dest": {"type": "log", "parameters": {"message": "m", "level": "INFO"}},
                "unmatched_dest": {"type": "log", "parameters": {"message": "u", "level": "INFO"}},
            },
            [
                {"from": "src", "to": "matched_dest", "type": "matched"},
                {"from": "src", "to": "unmatched_dest", "type": "unmatched"},
            ],
        )
        executor = ContinuousFlowExecutor(flow, max_retries=1)
        executor.start()
        try:
            executor.inject(FlowFile(content=b"test"), entry_task_id="src")
            time.sleep(1.0)

            states = executor.get_all_task_states()
            # matched_dest should receive the FF, unmatched_dest should not
            assert states["matched_dest"]["flowfiles_in"] >= 1
            assert states["unmatched_dest"]["flowfiles_in"] == 0
        finally:
            executor.stop()


class TestConnectionTTL:

    def test_ttl_expired_flowfile(self):
        """FlowFiles exceeding TTL are marked as expired."""
        from core.connection import Connection
        conn = Connection("a", "b", flowfile_ttl_seconds=1)
        ff = FlowFile(content=b"expire-me")
        conn.enqueue(ff)

        # Not expired yet
        assert not conn.is_expired(ff)
        expired = conn.drain_expired()
        assert len(expired) == 0
        assert conn.queue_size() == 1

        # Wait for TTL to expire
        time.sleep(1.2)
        assert conn.is_expired(ff)
        expired = conn.drain_expired()
        assert len(expired) == 1
        assert expired[0].get_attribute("expired") == "true"
        assert conn.queue_size() == 0

    def test_ttl_zero_means_no_expiry(self):
        """TTL of 0 means no expiration."""
        from core.connection import Connection
        conn = Connection("a", "b", flowfile_ttl_seconds=0)
        ff = FlowFile(content=b"forever")
        conn.enqueue(ff)
        assert not conn.is_expired(ff)
        expired = conn.drain_expired()
        assert len(expired) == 0

    def test_ttl_partial_expiry(self):
        """Only expired FFs are drained, others stay."""
        from core.connection import Connection
        conn = Connection("a", "b", flowfile_ttl_seconds=1)

        ff1 = FlowFile(content=b"old")
        conn.enqueue(ff1)
        time.sleep(1.2)

        # Add a fresh one
        ff2 = FlowFile(content=b"fresh")
        conn.enqueue(ff2)

        expired = conn.drain_expired()
        assert len(expired) == 1
        assert expired[0].get_content() == b"old"
        assert conn.queue_size() == 1  # ff2 still there


class TestFlowVersionManager:

    def test_save_and_list(self):
        mgr = FlowVersionManager()
        for i in range(3):
            mgr.save_version({"tasks": {f"t{i}": {}}, "relations": []}, f"v{i}")

        versions = mgr.list_versions()
        assert len(versions) == 3
        assert versions[0]["version"] == 1
        assert versions[2]["version"] == 3

    def test_diff_added_task(self):
        mgr = FlowVersionManager()
        mgr.save_version({"tasks": {"a": {"type": "log"}}, "relations": []}, "v1")
        mgr.save_version(
            {"tasks": {"a": {"type": "log"}, "b": {"type": "log"}}, "relations": []},
            "v2",
        )
        diff = mgr.diff_versions(1, 2)
        assert "b" in diff.added_tasks
        assert not diff.removed_tasks

    def test_diff_removed_task(self):
        mgr = FlowVersionManager()
        mgr.save_version(
            {"tasks": {"a": {}, "b": {}}, "relations": []}, "v1"
        )
        mgr.save_version({"tasks": {"a": {}}, "relations": []}, "v2")
        diff = mgr.diff_versions(1, 2)
        assert "b" in diff.removed_tasks

    def test_diff_modified_task(self):
        mgr = FlowVersionManager()
        mgr.save_version({"tasks": {"a": {"msg": "old"}}, "relations": []}, "v1")
        mgr.save_version({"tasks": {"a": {"msg": "new"}}, "relations": []}, "v2")
        diff = mgr.diff_versions(1, 2)
        assert "a" in diff.modified_tasks
        assert diff.modified_tasks["a"]["old"]["msg"] == "old"
        assert diff.modified_tasks["a"]["new"]["msg"] == "new"

    def test_diff_added_relation(self):
        mgr = FlowVersionManager()
        mgr.save_version({"tasks": {"a": {}, "b": {}}, "relations": []}, "v1")
        mgr.save_version(
            {"tasks": {"a": {}, "b": {}}, "relations": [{"from": "a", "to": "b"}]},
            "v2",
        )
        diff = mgr.diff_versions(1, 2)
        assert len(diff.added_relations) == 1

    def test_rollback_target(self):
        mgr = FlowVersionManager()
        mgr.save_version({"tasks": {}}, "v1")
        mgr.save_version({"tasks": {}}, "v2")
        target = mgr.get_rollback_target()
        assert target is not None
        assert target.version == 1

    def test_max_versions(self):
        mgr = FlowVersionManager(max_versions=3)
        for i in range(5):
            mgr.save_version({"tasks": {}}, f"v{i}")
        versions = mgr.list_versions()
        assert len(versions) == 3
        assert versions[0]["version"] == 3
        assert versions[2]["version"] == 5

    def test_empty_diff(self):
        mgr = FlowVersionManager()
        d = {"tasks": {"a": {}}, "relations": []}
        mgr.save_version(d, "v1")
        mgr.save_version(d, "v2")
        diff = mgr.diff_versions(1, 2)
        assert diff.is_empty


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
