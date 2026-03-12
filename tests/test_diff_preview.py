"""Tests for FlowDiff and DataPreviewManager."""

import pytest
from engine.flow_diff import FlowDiff, DiffEntry
from engine.data_preview import DataPreviewManager, DataSample


# ---------------------------------------------------------------------------
# Mock FlowFile for DataPreviewManager tests
# ---------------------------------------------------------------------------

class MockFlowFile:
    def __init__(self, content=b"", attributes=None):
        self.content = content
        self.attributes = attributes or {}

    def get_content(self):
        return self.content

    def get_attributes(self):
        return self.attributes


# ===========================================================================
# FlowDiff tests
# ===========================================================================

class TestFlowDiffNoChanges:
    """Compare identical flows."""

    def test_identical_empty_flows(self):
        diff = FlowDiff.compare({}, {})
        assert not diff.has_changes
        assert diff.entries == []

    def test_identical_full_flows(self):
        flow = {
            "name": "myflow",
            "version": "1.0",
            "tasks": {"t1": {"type": "logMessage", "parameters": {"message": "hi"}}},
            "relations": [{"from": "t1", "to": "t2", "type": "success"}],
            "parameters": {"p1": "v1"},
        }
        diff = FlowDiff.compare(flow, flow)
        assert not diff.has_changes


class TestFlowDiffAddedTasks:
    def test_detect_added_task(self):
        old = {"tasks": {}}
        new = {"tasks": {"t1": {"type": "logMessage"}}}
        diff = FlowDiff.compare(old, new)
        assert diff.has_changes
        added = diff.filter(category="task", change_type="added")
        assert len(added) == 1
        assert added[0].path == "tasks.t1"
        assert added[0].new_value == {"type": "logMessage"}

    def test_detect_multiple_added_tasks(self):
        old = {"tasks": {}}
        new = {"tasks": {"a": {"type": "x"}, "b": {"type": "y"}}}
        diff = FlowDiff.compare(old, new)
        added = diff.filter(category="task", change_type="added")
        assert len(added) == 2


class TestFlowDiffRemovedTasks:
    def test_detect_removed_task(self):
        old = {"tasks": {"t1": {"type": "logMessage"}}}
        new = {"tasks": {}}
        diff = FlowDiff.compare(old, new)
        removed = diff.filter(category="task", change_type="removed")
        assert len(removed) == 1
        assert removed[0].path == "tasks.t1"
        assert removed[0].old_value == {"type": "logMessage"}


class TestFlowDiffModifiedTaskParams:
    def test_detect_parameter_change_in_task(self):
        old = {"tasks": {"t1": {"type": "logMessage", "parameters": {"message": "old"}}}}
        new = {"tasks": {"t1": {"type": "logMessage", "parameters": {"message": "new"}}}}
        diff = FlowDiff.compare(old, new)
        assert diff.has_changes
        modified = diff.filter(category="task", change_type="modified")
        assert len(modified) == 1
        assert "message" in modified[0].path
        assert modified[0].old_value == "old"
        assert modified[0].new_value == "new"

    def test_detect_task_type_change(self):
        old = {"tasks": {"t1": {"type": "logMessage"}}}
        new = {"tasks": {"t1": {"type": "updateAttribute"}}}
        diff = FlowDiff.compare(old, new)
        modified = diff.filter(category="task", change_type="modified")
        assert len(modified) == 1
        assert modified[0].old_value == "logMessage"
        assert modified[0].new_value == "updateAttribute"


class TestFlowDiffRelations:
    def test_detect_added_relation(self):
        old = {"relations": []}
        new = {"relations": [{"from": "a", "to": "b", "type": "success"}]}
        diff = FlowDiff.compare(old, new)
        added = diff.filter(category="relation", change_type="added")
        assert len(added) == 1
        assert "a -> b" in added[0].path

    def test_detect_removed_relation(self):
        old = {"relations": [{"from": "a", "to": "b", "type": "success"}]}
        new = {"relations": []}
        diff = FlowDiff.compare(old, new)
        removed = diff.filter(category="relation", change_type="removed")
        assert len(removed) == 1

    def test_unchanged_relations(self):
        rels = [{"from": "a", "to": "b", "type": "success"}]
        diff = FlowDiff.compare({"relations": rels}, {"relations": rels})
        assert diff.filter(category="relation") == []


class TestFlowDiffMetadata:
    def test_detect_name_change(self):
        diff = FlowDiff.compare({"name": "old"}, {"name": "new"})
        meta = diff.filter(category="metadata")
        assert len(meta) == 1
        assert meta[0].path == "name"
        assert meta[0].old_value == "old"
        assert meta[0].new_value == "new"

    def test_detect_version_change(self):
        diff = FlowDiff.compare({"version": "1.0"}, {"version": "2.0"})
        meta = diff.filter(category="metadata")
        assert len(meta) == 1
        assert meta[0].path == "version"

    def test_no_metadata_change(self):
        diff = FlowDiff.compare({"name": "x", "version": "1"}, {"name": "x", "version": "1"})
        assert diff.filter(category="metadata") == []


class TestFlowDiffParameters:
    def test_detect_added_parameter(self):
        diff = FlowDiff.compare({"parameters": {}}, {"parameters": {"k": "v"}})
        added = diff.filter(category="parameter", change_type="added")
        assert len(added) == 1
        assert added[0].new_value == "v"

    def test_detect_removed_parameter(self):
        diff = FlowDiff.compare({"parameters": {"k": "v"}}, {"parameters": {}})
        removed = diff.filter(category="parameter", change_type="removed")
        assert len(removed) == 1
        assert removed[0].old_value == "v"

    def test_detect_modified_parameter(self):
        diff = FlowDiff.compare({"parameters": {"k": "a"}}, {"parameters": {"k": "b"}})
        modified = diff.filter(category="parameter", change_type="modified")
        assert len(modified) == 1
        assert modified[0].old_value == "a"
        assert modified[0].new_value == "b"


class TestFlowDiffSummary:
    def test_summary_counts(self):
        old = {"tasks": {"t1": {"type": "a"}}, "parameters": {"p": "1"}}
        new = {"tasks": {"t2": {"type": "b"}}, "parameters": {"p": "2"}}
        diff = FlowDiff.compare(old, new)
        s = diff.summary
        assert s["added"] >= 1
        assert s["removed"] >= 1
        assert s["modified"] >= 1

    def test_summary_empty(self):
        diff = FlowDiff.compare({}, {})
        assert diff.summary == {"added": 0, "removed": 0, "modified": 0}


class TestFlowDiffFilter:
    def test_filter_by_category(self):
        diff = FlowDiff.compare(
            {"name": "a", "tasks": {"t1": {"type": "x"}}},
            {"name": "b", "tasks": {}},
        )
        assert len(diff.filter(category="metadata")) == 1
        assert len(diff.filter(category="task")) == 1

    def test_filter_by_change_type(self):
        diff = FlowDiff.compare(
            {"tasks": {"t1": {"type": "x"}}},
            {"tasks": {"t2": {"type": "y"}}},
        )
        assert len(diff.filter(change_type="added")) >= 1
        assert len(diff.filter(change_type="removed")) >= 1

    def test_filter_combined(self):
        diff = FlowDiff.compare(
            {"tasks": {"t1": {"type": "x"}}},
            {"tasks": {}},
        )
        assert len(diff.filter(category="task", change_type="removed")) == 1
        assert len(diff.filter(category="task", change_type="added")) == 0


class TestFlowDiffHasChanges:
    def test_no_changes(self):
        assert not FlowDiff.compare({}, {}).has_changes

    def test_with_changes(self):
        assert FlowDiff.compare({"name": "a"}, {"name": "b"}).has_changes


class TestFlowDiffToDict:
    def test_to_dict_structure(self):
        diff = FlowDiff.compare({"name": "a"}, {"name": "b"})
        d = diff.to_dict()
        assert "summary" in d
        assert "has_changes" in d
        assert "total_changes" in d
        assert "entries" in d
        assert d["has_changes"] is True
        assert d["total_changes"] == 1
        entry = d["entries"][0]
        assert entry["category"] == "metadata"
        assert entry["change_type"] == "modified"
        assert entry["path"] == "name"
        assert entry["old_value"] == "a"
        assert entry["new_value"] == "b"

    def test_to_dict_empty(self):
        d = FlowDiff.compare({}, {}).to_dict()
        assert d["has_changes"] is False
        assert d["total_changes"] == 0
        assert d["entries"] == []


# ===========================================================================
# DataPreviewManager tests
# ===========================================================================

class TestDataPreviewEnableDisable:
    def test_enable_connection(self):
        pm = DataPreviewManager()
        pm.enable_connection("a", "b")
        assert pm.is_enabled("a", "b")
        assert not pm.is_enabled("x", "y")

    def test_disable_connection(self):
        pm = DataPreviewManager()
        pm.enable_connection("a", "b")
        pm.disable_connection("a", "b")
        assert not pm.is_enabled("a", "b")

    def test_disable_nonexistent_is_safe(self):
        pm = DataPreviewManager()
        pm.disable_connection("a", "b")  # should not raise


class TestDataPreviewEnableDisableAll:
    def test_enable_all(self):
        pm = DataPreviewManager()
        pm.enable_all()
        assert pm.is_enabled("any", "thing")
        assert pm.is_enabled("x", "y")

    def test_disable_all(self):
        pm = DataPreviewManager()
        pm.enable_connection("a", "b")
        pm.enable_all()
        pm.disable_all()
        assert not pm.is_enabled("a", "b")
        assert not pm.is_enabled("x", "y")


class TestDataPreviewCapture:
    def test_capture_basic(self):
        pm = DataPreviewManager()
        pm.enable_connection("s", "t")
        ff = MockFlowFile(content=b"hello world", attributes={"key": "val"})
        pm.capture("s", "t", ff)
        samples = pm.get_samples("s", "t")
        assert len(samples) == 1
        assert samples[0]["content_preview"] == "hello world"
        assert samples[0]["content_size"] == 11
        assert samples[0]["attributes"] == {"key": "val"}
        assert samples[0]["connection"] == "s -> t"

    def test_capture_not_enabled_skips(self):
        pm = DataPreviewManager()
        ff = MockFlowFile(content=b"data")
        pm.capture("s", "t", ff)
        assert pm.get_samples("s", "t") == []

    def test_capture_with_enable_all(self):
        pm = DataPreviewManager()
        pm.enable_all()
        ff = MockFlowFile(content=b"data")
        pm.capture("s", "t", ff)
        assert len(pm.get_samples("s", "t")) == 1


class TestDataPreviewContentType:
    def test_json_detection(self):
        pm = DataPreviewManager()
        pm.enable_connection("s", "t")
        pm.capture("s", "t", MockFlowFile(content=b'{"key": "value"}'))
        assert pm.get_samples("s", "t")[0]["content_type"] == "json"

    def test_json_array_detection(self):
        pm = DataPreviewManager()
        pm.enable_connection("s", "t")
        pm.capture("s", "t", MockFlowFile(content=b'[1, 2, 3]'))
        assert pm.get_samples("s", "t")[0]["content_type"] == "json"

    def test_csv_detection(self):
        pm = DataPreviewManager()
        pm.enable_connection("s", "t")
        pm.capture("s", "t", MockFlowFile(content=b"a,b,c\n1,2,3\n"))
        assert pm.get_samples("s", "t")[0]["content_type"] == "csv"

    def test_xml_detection(self):
        pm = DataPreviewManager()
        pm.enable_connection("s", "t")
        pm.capture("s", "t", MockFlowFile(content=b"<?xml version='1.0'?><root/>"))
        assert pm.get_samples("s", "t")[0]["content_type"] == "xml"

    def test_text_detection(self):
        pm = DataPreviewManager()
        pm.enable_connection("s", "t")
        pm.capture("s", "t", MockFlowFile(content=b"Just some plain text."))
        assert pm.get_samples("s", "t")[0]["content_type"] == "text"

    def test_empty_detection(self):
        pm = DataPreviewManager()
        pm.enable_connection("s", "t")
        pm.capture("s", "t", MockFlowFile(content=b""))
        assert pm.get_samples("s", "t")[0]["content_type"] == "empty"


class TestDataPreviewGetSamples:
    def test_get_samples_specific_connection(self):
        pm = DataPreviewManager()
        pm.enable_all()
        pm.capture("a", "b", MockFlowFile(content=b"ab"))
        pm.capture("c", "d", MockFlowFile(content=b"cd"))
        samples = pm.get_samples("a", "b")
        assert len(samples) == 1
        assert samples[0]["content_preview"] == "ab"

    def test_get_samples_all_connections(self):
        pm = DataPreviewManager()
        pm.enable_all()
        pm.capture("a", "b", MockFlowFile(content=b"ab"))
        pm.capture("c", "d", MockFlowFile(content=b"cd"))
        samples = pm.get_samples()
        assert len(samples) == 2

    def test_get_samples_limit(self):
        pm = DataPreviewManager()
        pm.enable_all()
        for i in range(5):
            pm.capture("a", "b", MockFlowFile(content=f"item{i}".encode()))
        samples = pm.get_samples("a", "b", limit=3)
        assert len(samples) == 3

    def test_get_samples_dict_format(self):
        pm = DataPreviewManager()
        pm.enable_connection("s", "t")
        pm.capture("s", "t", MockFlowFile(content=b"data", attributes={"a": "1"}))
        s = pm.get_samples("s", "t")[0]
        assert set(s.keys()) == {
            "connection", "timestamp", "content_preview",
            "content_size", "content_type", "attributes", "index",
        }


class TestDataPreviewGetConnectionsWithData:
    def test_returns_connections(self):
        pm = DataPreviewManager()
        pm.enable_all()
        pm.capture("a", "b", MockFlowFile(content=b"x"))
        pm.capture("c", "d", MockFlowFile(content=b"y"))
        conns = pm.get_connections_with_data()
        assert len(conns) == 2
        conn_ids = {c["connection"] for c in conns}
        assert "a -> b" in conn_ids
        assert "c -> d" in conn_ids
        for c in conns:
            assert c["sample_count"] == 1
            assert "latest" in c

    def test_empty_when_no_data(self):
        pm = DataPreviewManager()
        assert pm.get_connections_with_data() == []


class TestDataPreviewClear:
    def test_clear_specific_connection(self):
        pm = DataPreviewManager()
        pm.enable_all()
        pm.capture("a", "b", MockFlowFile(content=b"x"))
        pm.capture("c", "d", MockFlowFile(content=b"y"))
        pm.clear("a", "b")
        assert pm.get_samples("a", "b") == []
        assert len(pm.get_samples("c", "d")) == 1

    def test_clear_all(self):
        pm = DataPreviewManager()
        pm.enable_all()
        pm.capture("a", "b", MockFlowFile(content=b"x"))
        pm.capture("c", "d", MockFlowFile(content=b"y"))
        pm.clear()
        assert pm.get_samples() == []
        assert pm.get_connections_with_data() == []


class TestDataPreviewMaxSamples:
    def test_max_samples_limit(self):
        pm = DataPreviewManager(max_samples_per_connection=3)
        pm.enable_connection("s", "t")
        for i in range(10):
            pm.capture("s", "t", MockFlowFile(content=f"item{i}".encode()))
        samples = pm.get_samples("s", "t", limit=100)
        assert len(samples) == 3
        # Should keep the latest 3
        assert samples[-1]["content_preview"] == "item9"


class TestDataPreviewIsEnabled:
    def test_not_enabled_by_default(self):
        pm = DataPreviewManager()
        assert not pm.is_enabled("a", "b")

    def test_enabled_after_enable_connection(self):
        pm = DataPreviewManager()
        pm.enable_connection("a", "b")
        assert pm.is_enabled("a", "b")

    def test_enabled_after_enable_all(self):
        pm = DataPreviewManager()
        pm.enable_all()
        assert pm.is_enabled("anything", "works")
