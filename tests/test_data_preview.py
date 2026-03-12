"""Tests for DataPreviewManager and FlowDiff."""

import time
import threading
import pytest

from engine.data_preview import DataPreviewManager, DataSample
from engine.flow_diff import FlowDiff, DiffEntry
from core import FlowFile


# ============================================================================
# Data Preview Tests
# ============================================================================

class TestDataPreviewEnableDisable:
    """Test enable/disable connection tracking."""

    def test_enable_connection(self):
        preview = DataPreviewManager()
        preview.enable_connection("task1", "task2")
        assert preview.is_enabled("task1", "task2")
        assert not preview.is_enabled("task1", "task3")

    def test_disable_connection(self):
        preview = DataPreviewManager()
        preview.enable_connection("task1", "task2")
        preview.disable_connection("task1", "task2")
        assert not preview.is_enabled("task1", "task2")

    def test_disable_nonexistent_connection(self):
        """Disabling a connection that was never enabled should not error."""
        preview = DataPreviewManager()
        preview.disable_connection("x", "y")  # no error

    def test_enable_all(self):
        preview = DataPreviewManager()
        preview.enable_all()
        assert preview.is_enabled("any", "connection")
        assert preview.is_enabled("foo", "bar")

    def test_disable_all(self):
        preview = DataPreviewManager()
        preview.enable_connection("task1", "task2")
        preview.enable_all()
        preview.disable_all()
        assert not preview.is_enabled("task1", "task2")
        assert not preview.is_enabled("any", "connection")


class TestDataPreviewCapture:
    """Test FlowFile sample capture."""

    def test_capture_sample(self):
        preview = DataPreviewManager()
        preview.enable_connection("task1", "task2")

        ff = FlowFile(content=b'{"key": "value"}', attributes={"mime": "application/json"})
        preview.capture("task1", "task2", ff)

        samples = preview.get_samples("task1", "task2")
        assert len(samples) == 1
        assert samples[0]["content_preview"] == '{"key": "value"}'
        assert samples[0]["content_size"] == 16
        assert samples[0]["content_type"] == "json"
        assert samples[0]["attributes"]["mime"] == "application/json"
        assert samples[0]["connection"] == "task1 -> task2"

    def test_capture_not_enabled(self):
        """Capture is skipped if connection is not enabled."""
        preview = DataPreviewManager()
        ff = FlowFile(content=b"hello")
        preview.capture("task1", "task2", ff)
        samples = preview.get_samples("task1", "task2")
        assert len(samples) == 0

    def test_content_preview_truncation(self):
        """Content preview is limited to 2000 chars."""
        preview = DataPreviewManager()
        preview.enable_all()
        big_content = b"x" * 5000
        ff = FlowFile(content=big_content)
        preview.capture("a", "b", ff)
        samples = preview.get_samples("a", "b")
        assert len(samples[0]["content_preview"]) == 2000
        assert samples[0]["content_size"] == 5000

    def test_max_samples_limit(self):
        preview = DataPreviewManager(max_samples_per_connection=3)
        preview.enable_connection("a", "b")

        for i in range(5):
            ff = FlowFile(content=f"msg{i}".encode())
            preview.capture("a", "b", ff)

        samples = preview.get_samples("a", "b")
        assert len(samples) == 3
        # Should keep the latest 3
        assert samples[0]["content_preview"] == "msg2"
        assert samples[2]["content_preview"] == "msg4"

    def test_get_samples_filtered(self):
        preview = DataPreviewManager()
        preview.enable_all()

        preview.capture("a", "b", FlowFile(content=b"ab"))
        preview.capture("c", "d", FlowFile(content=b"cd"))

        ab_samples = preview.get_samples("a", "b")
        assert len(ab_samples) == 1
        assert ab_samples[0]["content_preview"] == "ab"

        cd_samples = preview.get_samples("c", "d")
        assert len(cd_samples) == 1

    def test_get_all_samples_sorted(self):
        preview = DataPreviewManager()
        preview.enable_all()

        preview.capture("a", "b", FlowFile(content=b"first"))
        time.sleep(0.01)
        preview.capture("c", "d", FlowFile(content=b"second"))

        all_samples = preview.get_samples()
        assert len(all_samples) == 2
        # Most recent first
        assert all_samples[0]["content_preview"] == "second"
        assert all_samples[1]["content_preview"] == "first"

    def test_clear_specific_connection(self):
        preview = DataPreviewManager()
        preview.enable_all()

        preview.capture("a", "b", FlowFile(content=b"ab"))
        preview.capture("c", "d", FlowFile(content=b"cd"))

        preview.clear("a", "b")
        assert len(preview.get_samples("a", "b")) == 0
        assert len(preview.get_samples("c", "d")) == 1

    def test_clear_all(self):
        preview = DataPreviewManager()
        preview.enable_all()

        preview.capture("a", "b", FlowFile(content=b"ab"))
        preview.capture("c", "d", FlowFile(content=b"cd"))

        preview.clear()
        assert len(preview.get_samples()) == 0

    def test_connections_with_data(self):
        preview = DataPreviewManager()
        preview.enable_all()

        preview.capture("a", "b", FlowFile(content=b"1"))
        preview.capture("a", "b", FlowFile(content=b"2"))
        preview.capture("c", "d", FlowFile(content=b"3"))

        conns = preview.get_connections_with_data()
        assert len(conns) == 2
        conn_map = {c["connection"]: c for c in conns}
        assert conn_map["a -> b"]["sample_count"] == 2
        assert conn_map["c -> d"]["sample_count"] == 1

    def test_thread_safety(self):
        """Concurrent captures should not corrupt data."""
        preview = DataPreviewManager(max_samples_per_connection=100)
        preview.enable_all()

        errors = []

        def worker(tid):
            try:
                for i in range(20):
                    ff = FlowFile(content=f"t{tid}-{i}".encode())
                    preview.capture("src", "tgt", ff)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        samples = preview.get_samples("src", "tgt", limit=200)
        assert len(samples) <= 100
        assert len(samples) > 0


class TestContentTypeDetection:
    """Test content type detection."""

    def test_json_object(self):
        assert DataPreviewManager._detect_type('{"key": "val"}') == "json"

    def test_json_array(self):
        assert DataPreviewManager._detect_type('[1, 2, 3]') == "json"

    def test_xml(self):
        assert DataPreviewManager._detect_type('<?xml version="1.0"?><root/>') == "xml"

    def test_xml_tag(self):
        assert DataPreviewManager._detect_type('<root><child/></root>') == "xml"

    def test_csv(self):
        assert DataPreviewManager._detect_type('name,age\nAlice,30\nBob,25') == "csv"

    def test_text(self):
        assert DataPreviewManager._detect_type('hello world') == "text"

    def test_binary(self):
        # Create a string with many non-printable chars
        binary_str = ''.join(chr(i) for i in range(0, 20))
        assert DataPreviewManager._detect_type(binary_str) == "binary"

    def test_empty(self):
        assert DataPreviewManager._detect_type('') == "empty"
        assert DataPreviewManager._detect_type('   ') == "empty"


# ============================================================================
# Flow Diff Tests
# ============================================================================

class TestFlowDiffNoChanges:
    """Test diff with identical flows."""

    def test_no_changes(self):
        flow = {"name": "test", "tasks": {"t1": {"type": "logMessage"}}, "relations": []}
        diff = FlowDiff.compare(flow, flow)
        assert not diff.has_changes
        assert diff.summary == {"added": 0, "removed": 0, "modified": 0}


class TestFlowDiffTasks:
    """Test task-level diffs."""

    def test_added_task(self):
        old = {"tasks": {"t1": {"type": "logMessage"}}}
        new = {"tasks": {"t1": {"type": "logMessage"}, "t2": {"type": "updateAttribute"}}}
        diff = FlowDiff.compare(old, new)
        assert diff.has_changes
        added = diff.filter(category="task", change_type="added")
        assert len(added) == 1
        assert added[0].path == "tasks.t2"
        assert "Added task 't2'" in added[0].description

    def test_removed_task(self):
        old = {"tasks": {"t1": {"type": "logMessage"}, "t2": {"type": "updateAttribute"}}}
        new = {"tasks": {"t1": {"type": "logMessage"}}}
        diff = FlowDiff.compare(old, new)
        removed = diff.filter(category="task", change_type="removed")
        assert len(removed) == 1
        assert removed[0].path == "tasks.t2"

    def test_modified_task_parameter(self):
        old = {"tasks": {"t1": {"type": "logMessage", "parameters": {"message": "hello"}}}}
        new = {"tasks": {"t1": {"type": "logMessage", "parameters": {"message": "world"}}}}
        diff = FlowDiff.compare(old, new)
        modified = diff.filter(category="task", change_type="modified")
        assert len(modified) == 1
        assert "message" in modified[0].path
        assert modified[0].old_value == "hello"
        assert modified[0].new_value == "world"


class TestFlowDiffRelations:
    """Test relation-level diffs."""

    def test_added_relation(self):
        old = {"relations": []}
        new = {"relations": [{"from": "t1", "to": "t2", "type": "success"}]}
        diff = FlowDiff.compare(old, new)
        added = diff.filter(category="relation", change_type="added")
        assert len(added) == 1
        assert "t1 -> t2" in added[0].description

    def test_removed_relation(self):
        old = {"relations": [{"from": "t1", "to": "t2", "type": "success"}]}
        new = {"relations": []}
        diff = FlowDiff.compare(old, new)
        removed = diff.filter(category="relation", change_type="removed")
        assert len(removed) == 1


class TestFlowDiffParameters:
    """Test parameter-level diffs."""

    def test_added_parameter(self):
        old = {"parameters": {}}
        new = {"parameters": {"env": "production"}}
        diff = FlowDiff.compare(old, new)
        added = diff.filter(category="parameter", change_type="added")
        assert len(added) == 1
        assert added[0].new_value == "production"

    def test_removed_parameter(self):
        old = {"parameters": {"env": "staging"}}
        new = {"parameters": {}}
        diff = FlowDiff.compare(old, new)
        removed = diff.filter(category="parameter", change_type="removed")
        assert len(removed) == 1
        assert removed[0].old_value == "staging"

    def test_modified_parameter(self):
        old = {"parameters": {"env": "staging"}}
        new = {"parameters": {"env": "production"}}
        diff = FlowDiff.compare(old, new)
        modified = diff.filter(category="parameter", change_type="modified")
        assert len(modified) == 1
        assert modified[0].old_value == "staging"
        assert modified[0].new_value == "production"


class TestFlowDiffMetadata:
    """Test metadata changes."""

    def test_name_changed(self):
        old = {"name": "Flow A"}
        new = {"name": "Flow B"}
        diff = FlowDiff.compare(old, new)
        modified = diff.filter(category="metadata", change_type="modified")
        assert len(modified) == 1
        assert modified[0].path == "name"

    def test_version_changed(self):
        old = {"version": "1.0"}
        new = {"version": "2.0"}
        diff = FlowDiff.compare(old, new)
        modified = diff.filter(category="metadata")
        assert any(e.path == "version" for e in modified)


class TestFlowDiffDeep:
    """Test deep dict comparison."""

    def test_nested_dict_diff(self):
        old = {"tasks": {"t1": {"type": "x", "config": {"a": {"b": 1}}}}}
        new = {"tasks": {"t1": {"type": "x", "config": {"a": {"b": 2}}}}}
        diff = FlowDiff.compare(old, new)
        modified = diff.filter(change_type="modified")
        assert len(modified) == 1
        assert modified[0].path == "tasks.t1.config.a.b"
        assert modified[0].old_value == 1
        assert modified[0].new_value == 2


class TestFlowDiffSummary:
    """Test summary and serialization."""

    def test_summary_counts(self):
        old = {"tasks": {"t1": {"type": "a"}}, "parameters": {"x": "1"}}
        new = {"tasks": {"t1": {"type": "a"}, "t2": {"type": "b"}}, "parameters": {}}
        diff = FlowDiff.compare(old, new)
        s = diff.summary
        assert s["added"] == 1   # t2 added
        assert s["removed"] == 1  # parameter x removed

    def test_filter_by_category(self):
        old = {"tasks": {"t1": {"type": "a"}}, "parameters": {"x": "1"}}
        new = {"tasks": {"t1": {"type": "b"}}, "parameters": {"x": "2"}}
        diff = FlowDiff.compare(old, new)
        task_entries = diff.filter(category="task")
        param_entries = diff.filter(category="parameter")
        assert len(task_entries) >= 1
        assert len(param_entries) == 1

    def test_filter_by_change_type(self):
        old = {"tasks": {}}
        new = {"tasks": {"t1": {"type": "a"}}}
        diff = FlowDiff.compare(old, new)
        added = diff.filter(change_type="added")
        removed = diff.filter(change_type="removed")
        assert len(added) == 1
        assert len(removed) == 0

    def test_to_dict(self):
        old = {"name": "A"}
        new = {"name": "B"}
        diff = FlowDiff.compare(old, new)
        d = diff.to_dict()
        assert "summary" in d
        assert "has_changes" in d
        assert d["has_changes"] is True
        assert "total_changes" in d
        assert d["total_changes"] == 1
        assert "entries" in d
        assert len(d["entries"]) == 1
        entry = d["entries"][0]
        assert entry["category"] == "metadata"
        assert entry["change_type"] == "modified"
        assert entry["path"] == "name"
        assert entry["old_value"] == "A"
        assert entry["new_value"] == "B"
