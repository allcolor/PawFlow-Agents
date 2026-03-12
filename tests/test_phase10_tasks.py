"""Tests for Phase 10 tasks: SplitJSON, ControlRate, ListenHTTP."""

import pytest
import json
import time

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile
from tasks.data.split_json import SplitJSONTask
from tasks.control.control_rate import ControlRateTask
from tasks.io.listen_http import ListenHTTPTask


class TestSplitJSON:

    def test_split_root_array(self):
        task = SplitJSONTask({})
        data = json.dumps([{"a": 1}, {"b": 2}, {"c": 3}]).encode()
        ff = FlowFile(content=data)
        results = task.execute(ff)
        assert len(results) == 3
        assert json.loads(results[0].get_content()) == {"a": 1}
        assert json.loads(results[2].get_content()) == {"c": 3}

    def test_split_index_attributes(self):
        task = SplitJSONTask({})
        data = json.dumps(["x", "y"]).encode()
        ff = FlowFile(content=data)
        results = task.execute(ff)
        assert results[0].get_attribute("split.index") == "0"
        assert results[1].get_attribute("split.index") == "1"
        assert results[0].get_attribute("split.count") == "2"

    def test_split_nested_path(self):
        task = SplitJSONTask({"json_path_expression": "$.items"})
        data = json.dumps({"items": [1, 2, 3], "meta": "info"}).encode()
        ff = FlowFile(content=data)
        results = task.execute(ff)
        assert len(results) == 3
        assert json.loads(results[0].get_content()) == 1

    def test_split_deep_nested(self):
        task = SplitJSONTask({"json_path_expression": "$.data.records"})
        data = json.dumps({"data": {"records": ["a", "b"]}}).encode()
        ff = FlowFile(content=data)
        results = task.execute(ff)
        assert len(results) == 2

    def test_split_single_object(self):
        """Non-array target gets wrapped in a list."""
        task = SplitJSONTask({})
        data = json.dumps({"key": "value"}).encode()
        ff = FlowFile(content=data)
        results = task.execute(ff)
        assert len(results) == 1
        assert json.loads(results[0].get_content()) == {"key": "value"}

    def test_split_empty_array(self):
        task = SplitJSONTask({})
        data = json.dumps([]).encode()
        ff = FlowFile(content=data)
        results = task.execute(ff)
        assert len(results) == 0

    def test_split_preserves_attributes(self):
        task = SplitJSONTask({})
        data = json.dumps([1, 2]).encode()
        ff = FlowFile(content=data, attributes={"source": "test"})
        results = task.execute(ff)
        assert results[0].get_attribute("source") == "test"

    def test_split_invalid_json(self):
        task = SplitJSONTask({})
        ff = FlowFile(content=b"not json")
        with pytest.raises(json.JSONDecodeError):
            task.execute(ff)


class TestControlRate:

    def test_basic_throttle(self):
        task = ControlRateTask({"rate": 100, "time_period": "1s"})
        ff = FlowFile(content=b"data")
        start = time.time()
        results = task.execute(ff)
        elapsed = time.time() - start
        assert len(results) == 1
        assert elapsed >= 0.005  # at least 10ms delay (1s/100)

    def test_delay_attribute(self):
        task = ControlRateTask({"rate": 10, "time_period": "1s"})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)
        delay = int(results[0].get_attribute("controlrate.delay_ms"))
        assert delay == 100  # 1000ms / 10

    def test_parse_milliseconds(self):
        task = ControlRateTask({"rate": 1, "time_period": "50ms"})
        ff = FlowFile(content=b"data")
        start = time.time()
        task.execute(ff)
        elapsed = time.time() - start
        assert elapsed >= 0.04

    def test_parse_minutes(self):
        task = ControlRateTask({})
        assert task._parse_duration("2m") == 120.0

    def test_parse_hours(self):
        task = ControlRateTask({})
        assert task._parse_duration("1h") == 3600.0

    def test_preserves_content(self):
        task = ControlRateTask({"rate": 1000, "time_period": "100ms"})
        ff = FlowFile(content=b"important data")
        results = task.execute(ff)
        assert results[0].get_content() == b"important data"


class TestListenHTTP:

    def test_basic(self):
        task = ListenHTTPTask({"port": 9090, "base_path": "/api"})
        ff = FlowFile(content=b"request body")
        results = task.execute(ff)
        assert len(results) == 1
        assert results[0].get_content() == b"request body"
        assert results[0].get_attribute("http.listener.port") == "9090"
        assert results[0].get_attribute("http.listener.path") == "/api"

    def test_timestamp_set(self):
        task = ListenHTTPTask({})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)
        assert results[0].get_attribute("http.received.timestamp") is not None

    def test_default_params(self):
        task = ListenHTTPTask({})
        ff = FlowFile(content=b"data")
        results = task.execute(ff)
        assert results[0].get_attribute("http.listener.port") == "8080"
        assert results[0].get_attribute("http.listener.path") == "/contentListener"

    def test_preserves_attributes(self):
        task = ListenHTTPTask({})
        ff = FlowFile(content=b"data", attributes={"source": "external"})
        results = task.execute(ff)
        assert results[0].get_attribute("source") == "external"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
