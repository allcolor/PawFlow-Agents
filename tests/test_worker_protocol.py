"""Tests for worker protocol, SpillTracker, and worker server/client."""

import io
import time
import pytest

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile
from core.stream import (
    ContentReference, set_spill_threshold, SPILL_THRESHOLD,
    get_spill_tracker, _get_spill_dir,
)
from engine.worker_protocol import (
    FlowFileSerializer, serialize_flowfile, deserialize_flowfile,
    serialize_results, deserialize_results,
)


class TestFlowFileProtocol:

    def test_serialize_deserialize_simple(self):
        ff = FlowFile(content=b"hello world", attributes={"key": "val"})
        data = serialize_flowfile(ff, task_id="t1", task_type="log")
        ff2, meta = deserialize_flowfile(data)
        assert ff2.get_content() == b"hello world"
        assert ff2.get_attribute("key") == "val"
        assert meta["task_id"] == "t1"
        assert meta["task_type"] == "log"

    def test_serialize_deserialize_empty_content(self):
        ff = FlowFile(content=b"", attributes={"a": "1"})
        data = serialize_flowfile(ff)
        ff2, meta = deserialize_flowfile(data)
        assert ff2.get_content() == b""
        assert ff2.get_attribute("a") == "1"

    def test_serialize_with_config(self):
        ff = FlowFile(content=b"data")
        data = serialize_flowfile(ff, task_type="log", config={"message": "hi", "level": "INFO"})
        ff2, meta = deserialize_flowfile(data)
        assert meta["config"]["message"] == "hi"
        assert meta["config"]["level"] == "INFO"

    def test_serialize_large_content(self):
        original = SPILL_THRESHOLD
        try:
            set_spill_threshold(100)
            big_data = b"x" * 500
            ff = FlowFile(content=big_data, attributes={"big": "true"})
            assert ff.is_content_on_disk

            data = serialize_flowfile(ff, task_id="big1")
            ff2, meta = deserialize_flowfile(data)
            assert ff2.get_content() == big_data
            assert ff2.get_attribute("big") == "true"
        finally:
            set_spill_threshold(original)

    def test_stream_serialize_deserialize(self):
        ff = FlowFile(content=b"stream data", attributes={"s": "1"})
        buf = io.BytesIO()
        FlowFileSerializer.serialize_to_stream(ff, buf, task_id="s1", task_type="log")
        buf.seek(0)
        ff2, meta = FlowFileSerializer.deserialize_from_stream(buf)
        assert ff2.get_content() == b"stream data"
        assert meta["task_id"] == "s1"


class TestResultProtocol:

    def test_single_result(self):
        ff = FlowFile(content=b"result", attributes={"r": "1"})
        data = serialize_results([ff], assignment_id="a1")
        ffs, meta = deserialize_results(data)
        assert meta["assignment_id"] == "a1"
        assert meta["status"] == "completed"
        assert meta["count"] == 1
        assert len(ffs) == 1
        assert ffs[0].get_content() == b"result"

    def test_multiple_results(self):
        ffs_in = [
            FlowFile(content=b"one", attributes={"i": "1"}),
            FlowFile(content=b"two", attributes={"i": "2"}),
            FlowFile(content=b"three", attributes={"i": "3"}),
        ]
        data = serialize_results(ffs_in, assignment_id="multi")
        ffs_out, meta = deserialize_results(data)
        assert meta["count"] == 3
        assert len(ffs_out) == 3
        assert ffs_out[0].get_content() == b"one"
        assert ffs_out[1].get_content() == b"two"
        assert ffs_out[2].get_content() == b"three"
        assert ffs_out[1].get_attribute("i") == "2"

    def test_error_result(self):
        data = serialize_results([], assignment_id="err1", error="task exploded")
        ffs, meta = deserialize_results(data)
        assert meta["status"] == "failed"
        assert meta["error"] == "task exploded"
        assert meta["count"] == 0
        assert len(ffs) == 0

    def test_empty_result(self):
        data = serialize_results([], assignment_id="empty1")
        ffs, meta = deserialize_results(data)
        assert meta["status"] == "completed"
        assert len(ffs) == 0

    def test_large_multi_results(self):
        original = SPILL_THRESHOLD
        try:
            set_spill_threshold(50)
            ffs_in = [
                FlowFile(content=b"a" * 100, attributes={"n": "1"}),
                FlowFile(content=b"b" * 200, attributes={"n": "2"}),
            ]
            data = serialize_results(ffs_in, assignment_id="big_multi")
            ffs_out, meta = deserialize_results(data)
            assert len(ffs_out) == 2
            assert ffs_out[0].get_content() == b"a" * 100
            assert ffs_out[1].get_content() == b"b" * 200
        finally:
            set_spill_threshold(original)


class TestSpillTracker:

    def test_stats_initial(self):
        tracker = get_spill_tracker()
        stats = tracker.get_stats()
        assert "active_spill_files" in stats
        assert "total_bytes_on_disk" in stats

    def test_spill_tracked(self):
        original = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            tracker = get_spill_tracker()
            before = tracker.get_stats()["active_spill_files"]

            ref = ContentReference(data=b"x" * 50)
            assert ref.is_on_disk
            after = tracker.get_stats()["active_spill_files"]
            assert after == before + 1

            ref.release()
            final = tracker.get_stats()["active_spill_files"]
            assert final == before
        finally:
            set_spill_threshold(original)

    def test_cleanup_orphans(self):
        original = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            tracker = get_spill_tracker()

            # Create a ref and let it become orphaned
            ref = ContentReference(data=b"y" * 30)
            file_path = ref._file_path
            assert file_path.exists()

            # Simulate orphan: delete the python object without calling release
            ref._ref_count = 0  # mark as released but don't delete file
            del ref

            # The file should still exist but the weakref is dead
            cleaned = tracker.cleanup_orphans()
            # File should be cleaned
            assert not file_path.exists()
        finally:
            set_spill_threshold(original)

    def test_from_stream_tracked(self):
        original = SPILL_THRESHOLD
        try:
            set_spill_threshold(10)
            tracker = get_spill_tracker()
            before = tracker.get_stats()["active_spill_files"]

            stream = io.BytesIO(b"z" * 50)
            ref = ContentReference.from_stream(stream, size_hint=50)
            assert ref.is_on_disk
            after = tracker.get_stats()["active_spill_files"]
            assert after == before + 1

            ref.release()
        finally:
            set_spill_threshold(original)


class TestWorkerServerClient:

    def test_round_trip(self):
        """Start a real WorkerServer, send a task via WorkerClient, verify result."""
        from engine.worker_server import WorkerServer
        from engine.worker_client import WorkerClient

        server = WorkerServer(host="127.0.0.1", port=0, worker_name="test-worker")
        server.start()
        try:
            time.sleep(0.2)  # let server bind

            client = WorkerClient("127.0.0.1", server.port, timeout=10)

            # Test heartbeat
            assert client.heartbeat()

            # Test status
            status = client.get_status()
            assert status["worker_name"] == "test-worker"
            assert status["status"] == "running"

            # Test execute with "log" task
            ff = FlowFile(content=b"test data", attributes={"source": "test"})
            results, meta = client.execute_task(
                ff, task_id="t1", task_type="log",
                config={"message": "hello", "level": "INFO"}
            )
            assert meta["status"] == "completed"
            assert len(results) >= 1
            assert results[0].get_content() == b"test data"

        finally:
            server.stop()

    def test_round_trip_error(self):
        """Test that task errors are properly returned via protocol."""
        from engine.worker_server import WorkerServer
        from engine.worker_client import WorkerClient

        server = WorkerServer(host="127.0.0.1", port=0, worker_name="err-worker")
        server.start()
        try:
            time.sleep(0.2)

            client = WorkerClient("127.0.0.1", server.port, timeout=10)
            ff = FlowFile(content=b"data")

            results, meta = client.execute_task(
                ff, task_id="bad", task_type="nonExistentType999",
                config={}
            )
            assert meta["status"] == "failed"
            assert meta["error"] is not None

        finally:
            server.stop()

    def test_round_trip_large_content(self):
        """Test streaming large content through server/client."""
        from engine.worker_server import WorkerServer
        from engine.worker_client import WorkerClient

        server = WorkerServer(host="127.0.0.1", port=0, worker_name="big-worker")
        server.start()
        try:
            time.sleep(0.2)

            client = WorkerClient("127.0.0.1", server.port, timeout=10)

            big_data = b"X" * 100_000  # 100KB
            ff = FlowFile(content=big_data, attributes={"size": "big"})
            results, meta = client.execute_task(
                ff, task_id="t_big", task_type="log",
                config={"message": "big payload", "level": "INFO"}
            )
            assert meta["status"] == "completed"
            assert len(results) >= 1
            assert results[0].get_content() == big_data

        finally:
            server.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
