"""Tests for DetectDuplicate and AttributesToJSON tasks."""

import pytest
import json

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile
from tasks.data.detect_duplicate import DetectDuplicateTask
from tasks.data.attributes_to_json import AttributesToJSONTask
from services.distributed_cache import get_default_cache


class TestDetectDuplicate:

    def setup_method(self):
        cache = get_default_cache()
        cache.clear()

    def test_first_is_not_duplicate(self):
        task = DetectDuplicateTask({})
        ff = FlowFile(content=b"unique content")
        results = task.execute(ff)
        assert results[0].get_attribute("duplicate") == "false"

    def test_second_is_duplicate(self):
        task = DetectDuplicateTask({})
        ff1 = FlowFile(content=b"same")
        ff2 = FlowFile(content=b"same")
        task.execute(ff1)
        results = task.execute(ff2)
        assert results[0].get_attribute("duplicate") == "true"

    def test_different_content_not_duplicate(self):
        task = DetectDuplicateTask({})
        ff1 = FlowFile(content=b"aaa")
        ff2 = FlowFile(content=b"bbb")
        task.execute(ff1)
        results = task.execute(ff2)
        assert results[0].get_attribute("duplicate") == "false"

    def test_attribute_based_dedup(self):
        task = DetectDuplicateTask({"cache_entry_identifier": "record_id"})
        ff1 = FlowFile(content=b"data1", attributes={"record_id": "123"})
        ff2 = FlowFile(content=b"data2", attributes={"record_id": "123"})
        task.execute(ff1)
        results = task.execute(ff2)
        assert results[0].get_attribute("duplicate") == "true"

    def test_sets_duplicate_key(self):
        task = DetectDuplicateTask({})
        ff = FlowFile(content=b"test")
        results = task.execute(ff)
        assert results[0].get_attribute("duplicate.key") != ""


class TestAttributesToJSON:

    def test_all_attributes_to_content(self):
        task = AttributesToJSONTask({})
        ff = FlowFile(content=b"data", attributes={"key1": "val1", "key2": "val2"})
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert data["key1"] == "val1"
        assert data["key2"] == "val2"

    def test_filtered_attributes(self):
        task = AttributesToJSONTask({"attributes_list": "key1"})
        ff = FlowFile(content=b"data", attributes={"key1": "val1", "key2": "val2"})
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert "key1" in data
        assert "key2" not in data

    def test_destination_attribute(self):
        task = AttributesToJSONTask({
            "destination": "flowfile-attribute",
            "destination_attribute": "myJson",
        })
        ff = FlowFile(content=b"original", attributes={"x": "1"})
        results = task.execute(ff)
        assert results[0].get_content() == b"original"
        json_attr = results[0].get_attribute("myJson")
        assert json.loads(json_attr)["x"] == "1"

    def test_exclude_core_attributes(self):
        task = AttributesToJSONTask({"include_core_attributes": False})
        ff = FlowFile(content=b"data", attributes={"uuid": "abc", "custom": "val"})
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert "uuid" not in data
        assert "custom" in data

    def test_empty_attributes(self):
        task = AttributesToJSONTask({})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)
        data = json.loads(results[0].get_content())
        assert isinstance(data, dict)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
