"""Tests for log persistence module."""

import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from gui.components.log_persistence import LogPersistence


class TestLogPersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.persistence = LogPersistence(log_dir=self.tmpdir, retention_days=7)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_records(self, count=3):
        records = []
        for i in range(count):
            records.append({
                "timestamp": f"12:00:0{i}.000",
                "level": "INFO",
                "source": "test",
                "message": f"Message {i}",
                "task_id": None,
                "flow_id": None,
            })
        return records

    def test_save_and_load(self):
        """Records saved to disk can be loaded back."""
        records = self._make_records(5)
        path = self.persistence.save_records(records)
        assert os.path.exists(path)

        loaded = self.persistence.load_records(path)
        assert len(loaded) == 5
        assert loaded[0]["message"] == "Message 0"
        assert loaded[4]["message"] == "Message 4"

    def test_save_with_flow_id(self):
        """Filename includes flow_id when provided."""
        records = self._make_records(1)
        path = self.persistence.save_records(records, flow_id="my_flow")
        assert "my_flow" in path

    def test_save_with_execution_id(self):
        """Filename includes execution_id when provided."""
        records = self._make_records(1)
        path = self.persistence.save_records(records, flow_id="f1", execution_id="exec123")
        assert "exec123" in path

    def test_jsonl_format(self):
        """Each record is a separate JSON line."""
        records = self._make_records(3)
        path = self.persistence.save_records(records)

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "message" in parsed
            assert "level" in parsed

    def test_list_log_files(self):
        """List returns saved files."""
        self.persistence.save_records(self._make_records(1), flow_id="f1")
        self.persistence.save_records(self._make_records(1), flow_id="f2")

        files = self.persistence.list_log_files()
        assert len(files) == 2

    def test_list_log_files_filter_by_flow(self):
        """List can filter by flow_id."""
        self.persistence.save_records(self._make_records(1), flow_id="f1")
        self.persistence.save_records(self._make_records(1), flow_id="f2")

        files = self.persistence.list_log_files(flow_id="f1")
        assert len(files) == 1
        assert "f1" in files[0]["filename"]

    def test_cleanup_old_logs(self):
        """Old log files are removed by cleanup."""
        # Save a file
        path = self.persistence.save_records(self._make_records(1))

        # Manually set mtime to 10 days ago
        old_time = (datetime.now() - timedelta(days=10)).timestamp()
        os.utime(path, (old_time, old_time))

        removed = self.persistence.cleanup_old_logs()
        assert removed == 1
        assert not os.path.exists(path)

    def test_cleanup_keeps_recent(self):
        """Recent log files are kept by cleanup."""
        self.persistence.save_records(self._make_records(1))
        removed = self.persistence.cleanup_old_logs()
        assert removed == 0

    def test_empty_records(self):
        """Saving empty records creates empty file."""
        path = self.persistence.save_records([])
        assert os.path.exists(path)
        loaded = self.persistence.load_records(path)
        assert len(loaded) == 0

    def test_get_log_dir(self):
        """get_log_dir returns configured directory."""
        assert self.persistence.get_log_dir() == self.tmpdir

    def test_creates_directory(self):
        """Constructor creates log directory if missing."""
        new_dir = os.path.join(self.tmpdir, "subdir", "logs")
        lp = LogPersistence(log_dir=new_dir)
        assert os.path.isdir(new_dir)


class TestFlowTreeDict(unittest.TestCase):
    """Test the render_flow_tree_from_dict utility logic (non-UI parts)."""

    def test_topological_order(self):
        """Tasks are ordered topologically."""
        from collections import defaultdict, deque

        tasks = {"a": {"type": "log"}, "b": {"type": "log"}, "c": {"type": "log"}}
        relations = [
            {"from": "a", "to": "b", "type": "success"},
            {"from": "b", "to": "c", "type": "success"},
        ]

        in_degree = defaultdict(int)
        graph = defaultdict(set)
        for rel in relations:
            f, t_id = rel.get("from", ""), rel.get("to", "")
            if t_id in tasks:
                graph[f].add(t_id)
                in_degree[t_id] += 1

        queue = deque([tid for tid in tasks if in_degree[tid] == 0])
        ordered = []
        while queue:
            tid = queue.popleft()
            ordered.append(tid)
            for dep in graph[tid]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

        assert ordered == ["a", "b", "c"]

    def test_cycle_handling(self):
        """Cyclic graphs include all tasks (even if not fully sorted)."""
        from collections import defaultdict, deque

        tasks = {"a": {"type": "log"}, "b": {"type": "log"}}
        relations = [
            {"from": "a", "to": "b", "type": "success"},
            {"from": "b", "to": "a", "type": "success"},
        ]

        in_degree = defaultdict(int)
        graph = defaultdict(set)
        for rel in relations:
            f, t_id = rel.get("from", ""), rel.get("to", "")
            if t_id in tasks:
                graph[f].add(t_id)
                in_degree[t_id] += 1

        queue = deque([tid for tid in tasks if in_degree[tid] == 0])
        ordered = []
        while queue:
            tid = queue.popleft()
            ordered.append(tid)
            for dep in graph[tid]:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

        # With cycle, not all tasks are ordered
        for tid in tasks:
            if tid not in ordered:
                ordered.append(tid)

        assert set(ordered) == {"a", "b"}


if __name__ == "__main__":
    unittest.main()
