"""Tests for PrioritizedQueue and ReportingTask."""

import pytest
import json

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile
from core.prioritizer import PrioritizedQueue, PrioritizerType


class TestPrioritizedQueue:

    def test_fifo_order(self):
        q = PrioritizedQueue(PrioritizerType.FIFO)
        ff1 = FlowFile(content=b"first")
        ff2 = FlowFile(content=b"second")
        ff3 = FlowFile(content=b"third")
        q.put(ff1)
        q.put(ff2)
        q.put(ff3)
        assert q.get().get_content() == b"first"
        assert q.get().get_content() == b"second"
        assert q.get().get_content() == b"third"

    def test_newest_first(self):
        q = PrioritizedQueue(PrioritizerType.NEWEST_FIRST)
        ff1 = FlowFile(content=b"old", attributes={"timestamp": "2024-01-01T00:00:00"})
        ff2 = FlowFile(content=b"new", attributes={"timestamp": "2024-12-31T23:59:59"})
        ff3 = FlowFile(content=b"mid", attributes={"timestamp": "2024-06-15T12:00:00"})
        q.put(ff1)
        q.put(ff2)
        q.put(ff3)
        assert q.get().get_content() == b"new"
        assert q.get().get_content() == b"mid"
        assert q.get().get_content() == b"old"

    def test_oldest_first(self):
        q = PrioritizedQueue(PrioritizerType.OLDEST_FIRST)
        ff1 = FlowFile(content=b"new", attributes={"timestamp": "2024-12-31T23:59:59"})
        ff2 = FlowFile(content=b"old", attributes={"timestamp": "2024-01-01T00:00:00"})
        q.put(ff1)
        q.put(ff2)
        assert q.get().get_content() == b"old"
        assert q.get().get_content() == b"new"

    def test_priority_attribute(self):
        q = PrioritizedQueue(PrioritizerType.PRIORITY_ATTRIBUTE, priority_attribute="priority")
        ff1 = FlowFile(content=b"urgent", attributes={"priority": "10"})
        ff2 = FlowFile(content=b"low", attributes={"priority": "1"})
        ff3 = FlowFile(content=b"medium", attributes={"priority": "5"})
        q.put(ff1)
        q.put(ff2)
        q.put(ff3)
        # Higher number = more urgent (dequeued first)
        assert q.get().get_content() == b"urgent"
        assert q.get().get_content() == b"medium"
        assert q.get().get_content() == b"low"

    def test_backpressure(self):
        q = PrioritizedQueue(max_size=2)
        assert q.put(FlowFile(content=b"1")) is True
        assert q.put(FlowFile(content=b"2")) is True
        assert q.put(FlowFile(content=b"3")) is False
        assert q.size() == 2

    def test_empty_get(self):
        q = PrioritizedQueue()
        assert q.get() is None

    def test_peek(self):
        q = PrioritizedQueue()
        ff = FlowFile(content=b"peek")
        q.put(ff)
        assert q.peek().get_content() == b"peek"
        assert q.size() == 1

    def test_clear(self):
        q = PrioritizedQueue()
        q.put(FlowFile(content=b"1"))
        q.put(FlowFile(content=b"2"))
        q.clear()
        assert q.is_empty()

    def test_is_full(self):
        q = PrioritizedQueue(max_size=1)
        assert not q.is_full()
        q.put(FlowFile(content=b"1"))
        assert q.is_full()

    def test_priority_missing_attribute(self):
        q = PrioritizedQueue(PrioritizerType.PRIORITY_ATTRIBUTE)
        ff1 = FlowFile(content=b"no_prio")
        ff2 = FlowFile(content=b"has_prio", attributes={"priority": "1"})
        q.put(ff1)
        q.put(ff2)
        assert q.get().get_content() == b"has_prio"
        assert q.get().get_content() == b"no_prio"


class TestReportingTask:

    def test_summary_report_json(self):
        from tasks.system.reporting_task import ReportingTask
        task = ReportingTask({"report_type": "summary", "format": "json"})
        ff = FlowFile(content=b"input")
        results = task.execute(ff)
        assert len(results) == 1
        data = json.loads(results[0].get_content())
        assert "total_events" in data
        assert results[0].get_attribute("report.type") == "summary"
        assert results[0].get_attribute("report.format") == "json"

    def test_bulletin_report(self):
        from tasks.system.reporting_task import ReportingTask
        task = ReportingTask({"report_type": "bulletin", "format": "json"})
        ff = FlowFile(content=b"input")
        results = task.execute(ff)
        assert len(results) == 1
        data = json.loads(results[0].get_content())
        assert isinstance(data, list)

    def test_provenance_report(self):
        from tasks.system.reporting_task import ReportingTask
        task = ReportingTask({"report_type": "provenance", "format": "json"})
        ff = FlowFile(content=b"input")
        results = task.execute(ff)
        assert len(results) == 1
        data = json.loads(results[0].get_content())
        assert isinstance(data, list)

    def test_text_format(self):
        from tasks.system.reporting_task import ReportingTask
        task = ReportingTask({"report_type": "summary", "format": "text"})
        ff = FlowFile(content=b"input")
        results = task.execute(ff)
        content = results[0].get_content().decode("utf-8")
        assert "total_events" in content

    def test_default_params(self):
        from tasks.system.reporting_task import ReportingTask
        task = ReportingTask({})
        ff = FlowFile(content=b"input")
        results = task.execute(ff)
        assert results[0].get_attribute("report.type") == "summary"
        assert results[0].get_attribute("report.format") == "json"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
