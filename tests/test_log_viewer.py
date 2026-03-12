"""Tests for the LogCapture component."""

import logging
import pytest
from gui.components.log_viewer import LogCapture


@pytest.fixture(autouse=True)
def reset_log_capture():
    """Reset LogCapture state between tests."""
    LogCapture._instances.clear()
    LogCapture._global_instance = None
    yield
    LogCapture._instances.clear()
    LogCapture._global_instance = None


class TestLogCapture:
    """Tests for LogCapture handler."""

    def test_create_capture(self):
        capture = LogCapture(max_records=10)
        assert capture.count() == 0

    def test_emit_record(self):
        capture = LogCapture()
        logger = logging.getLogger("test.emit")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("Hello world")
            assert capture.count() == 1
            records = capture.get_records()
            assert records[0]["message"] == "Hello world"
            assert records[0]["level"] == "INFO"
        finally:
            logger.removeHandler(capture)

    def test_max_records(self):
        capture = LogCapture(max_records=5)
        logger = logging.getLogger("test.max")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            for i in range(10):
                logger.info(f"msg {i}")
            assert capture.count() == 5
            records = capture.get_records(limit=10)
            # Newest first, so last msg should be msg 9
            assert records[0]["message"] == "msg 9"
        finally:
            logger.removeHandler(capture)

    def test_filter_by_level(self):
        capture = LogCapture()
        logger = logging.getLogger("test.level")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("info msg")
            logger.warning("warn msg")
            logger.error("error msg")

            info_records = capture.get_records(level="INFO")
            assert len(info_records) == 1
            assert info_records[0]["message"] == "info msg"

            error_records = capture.get_records(level="ERROR")
            assert len(error_records) == 1
        finally:
            logger.removeHandler(capture)

    def test_clear(self):
        capture = LogCapture()
        logger = logging.getLogger("test.clear")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("msg")
            assert capture.count() == 1
            capture.clear()
            assert capture.count() == 0
        finally:
            logger.removeHandler(capture)

    def test_get_records_newest_first(self):
        capture = LogCapture()
        logger = logging.getLogger("test.order")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("first")
            logger.info("second")
            logger.info("third")
            records = capture.get_records()
            assert records[0]["message"] == "third"
            assert records[-1]["message"] == "first"
        finally:
            logger.removeHandler(capture)

    def test_limit(self):
        capture = LogCapture()
        logger = logging.getLogger("test.limit")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            for i in range(20):
                logger.info(f"msg {i}")
            records = capture.get_records(limit=5)
            assert len(records) == 5
        finally:
            logger.removeHandler(capture)

    def test_per_flow_isolation(self):
        cap_a = LogCapture.get_for_flow("flow_a")
        cap_b = LogCapture.get_for_flow("flow_b")
        assert cap_a is not cap_b

        logger_a = logging.getLogger("test.flow_a")
        logger_a.addHandler(cap_a)
        logger_a.setLevel(logging.DEBUG)

        logger_b = logging.getLogger("test.flow_b")
        logger_b.addHandler(cap_b)
        logger_b.setLevel(logging.DEBUG)
        try:
            logger_a.info("msg for A")
            logger_b.info("msg for B")

            assert cap_a.count() == 1
            assert cap_b.count() == 1
            assert cap_a.get_records()[0]["message"] == "msg for A"
            assert cap_b.get_records()[0]["message"] == "msg for B"
        finally:
            logger_a.removeHandler(cap_a)
            logger_b.removeHandler(cap_b)

    def test_get_for_flow_singleton(self):
        cap1 = LogCapture.get_for_flow("flow_x")
        cap2 = LogCapture.get_for_flow("flow_x")
        assert cap1 is cap2

    def test_remove_flow(self):
        LogCapture.get_for_flow("temp_flow")
        assert "temp_flow" in LogCapture._instances
        LogCapture.remove_flow("temp_flow")
        assert "temp_flow" not in LogCapture._instances

    def test_global_singleton(self):
        g1 = LogCapture.get_global()
        g2 = LogCapture.get_global()
        assert g1 is g2

    def test_record_contains_source(self):
        capture = LogCapture()
        logger = logging.getLogger("my.module.name")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("test")
            records = capture.get_records()
            assert records[0]["source"] == "my.module.name"
        finally:
            logger.removeHandler(capture)

    def test_record_timestamp_format(self):
        capture = LogCapture()
        logger = logging.getLogger("test.ts")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("test")
            records = capture.get_records()
            ts = records[0]["timestamp"]
            # Format: HH:MM:SS.mmm
            assert len(ts) == 12
            assert ts[2] == ":"
            assert ts[5] == ":"
            assert ts[8] == "."
        finally:
            logger.removeHandler(capture)

    def test_text_search_filter(self):
        """Log records should be filterable by text content."""
        capture = LogCapture()
        logger = logging.getLogger("test.search")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("Processing file data.csv")
            logger.info("Connecting to database")
            logger.warning("Timeout on connection")
            logger.info("Processing file report.xlsx")

            records = capture.get_records(limit=100)
            search = "data.csv"
            filtered = [r for r in records if search.lower() in r["message"].lower()]
            assert len(filtered) == 1
            assert "data.csv" in filtered[0]["message"]

            search2 = "test.search"
            filtered2 = [r for r in records if search2.lower() in r.get("source", "").lower()]
            assert len(filtered2) == 4
        finally:
            logger.removeHandler(capture)

    def test_export_format(self):
        """Log records should be exportable as plain text."""
        capture = LogCapture()
        logger = logging.getLogger("test.export")
        logger.addHandler(capture)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("Hello world")
            logger.error("Something failed")

            records = capture.get_records(limit=100)
            lines = []
            for rec in records:
                task_tag = f" [{rec['task_id']}]" if rec.get("task_id") else ""
                lines.append(
                    f"{rec['timestamp']} {rec['level']}{task_tag} "
                    f"{rec['source']} — {rec['message']}"
                )
            export_text = "\n".join(lines)

            assert "Hello world" in export_text
            assert "Something failed" in export_text
            assert "INFO" in export_text
            assert "ERROR" in export_text
        finally:
            logger.removeHandler(capture)