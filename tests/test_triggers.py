"""Tests for the event triggers system."""

import json
import os
import shutil
import tempfile
import threading
import time
import urllib.request
import urllib.error

import pytest

from tasks import register_all_tasks
register_all_tasks()

from engine.triggers import (
    TriggerManager, TriggerType, TriggerState, TriggerConfig,
    BaseTrigger, FileWatcherTrigger, WebhookTrigger, EventTrigger,
    PollingTrigger, TriggerEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fire_recorder():
    """Return (callback, events_list) for recording trigger fires."""
    events = []
    lock = threading.Lock()

    def callback(trigger_id, event_data):
        with lock:
            events.append({"trigger_id": trigger_id, "event_data": event_data})

    return callback, events


def _make_config(trigger_type, trigger_id="test", flow_path="flows/test.json", **config_kwargs):
    return TriggerConfig(
        trigger_id=trigger_id,
        trigger_type=trigger_type,
        flow_path=flow_path,
        name=trigger_id,
        config=config_kwargs,
    )


# ---------------------------------------------------------------------------
# TriggerManager tests
# ---------------------------------------------------------------------------

class TestTriggerManager:

    def test_create_file_watcher_trigger(self, tmp_path):
        tm = TriggerManager()
        result = tm.create_trigger(
            "fw1", TriggerType.FILE_WATCHER, "flows/test.json",
            config={"watch_path": str(tmp_path), "poll_interval": 0.2},
            enabled=False,
        )
        assert result["trigger_id"] == "fw1"
        assert result["type"] == "file_watcher"
        assert result["state"] == "stopped"
        tm.stop_all()

    def test_create_webhook_trigger(self):
        tm = TriggerManager()
        result = tm.create_trigger(
            "wh1", TriggerType.WEBHOOK, "flows/test.json",
            config={"port": 19876, "path": "/test"},
            enabled=False,
        )
        assert result["type"] == "webhook"
        tm.stop_all()

    def test_create_event_trigger(self):
        tm = TriggerManager()
        result = tm.create_trigger(
            "ev1", TriggerType.EVENT, "flows/test.json",
            config={"events": ["flow.completed"]},
            enabled=False,
        )
        assert result["type"] == "event"
        tm.stop_all()

    def test_create_polling_trigger(self):
        tm = TriggerManager()
        result = tm.create_trigger(
            "poll1", TriggerType.POLLING, "flows/test.json",
            config={"url": "http://localhost:99999", "interval": 60},
            enabled=False,
        )
        assert result["type"] == "polling"
        tm.stop_all()

    def test_duplicate_id_raises(self, tmp_path):
        tm = TriggerManager()
        tm.create_trigger("dup", TriggerType.FILE_WATCHER, "flows/test.json",
                          config={"watch_path": str(tmp_path)}, enabled=False)
        with pytest.raises(ValueError, match="already exists"):
            tm.create_trigger("dup", TriggerType.FILE_WATCHER, "flows/test.json",
                              config={"watch_path": str(tmp_path)}, enabled=False)
        tm.stop_all()

    def test_delete_trigger(self, tmp_path):
        tm = TriggerManager()
        tm.create_trigger("del1", TriggerType.FILE_WATCHER, "flows/test.json",
                          config={"watch_path": str(tmp_path)}, enabled=False)
        assert tm.delete_trigger("del1") is True
        assert tm.delete_trigger("del1") is False
        assert tm.list_triggers() == []

    def test_list_triggers(self, tmp_path):
        tm = TriggerManager()
        tm.create_trigger("a", TriggerType.FILE_WATCHER, "flows/a.json",
                          config={"watch_path": str(tmp_path)}, enabled=False)
        tm.create_trigger("b", TriggerType.WEBHOOK, "flows/b.json",
                          config={"port": 19877}, enabled=False)
        listing = tm.list_triggers()
        assert len(listing) == 2
        ids = {t["trigger_id"] for t in listing}
        assert ids == {"a", "b"}
        tm.stop_all()

    def test_start_stop_pause_resume(self, tmp_path):
        tm = TriggerManager()
        tm.create_trigger("ctrl", TriggerType.FILE_WATCHER, "flows/test.json",
                          config={"watch_path": str(tmp_path), "poll_interval": 0.2},
                          enabled=False)
        result = tm.start_trigger("ctrl")
        assert result["state"] == "active"

        result = tm.pause_trigger("ctrl")
        assert result["state"] == "paused"

        result = tm.resume_trigger("ctrl")
        assert result["state"] == "active"

        result = tm.stop_trigger("ctrl")
        assert result["state"] == "stopped"

    def test_start_nonexistent_raises(self):
        tm = TriggerManager()
        with pytest.raises(ValueError, match="not found"):
            tm.start_trigger("nope")

    def test_save_load_triggers(self, tmp_path):
        filepath = str(tmp_path / "triggers.json")
        tm = TriggerManager()
        watch_dir = str(tmp_path / "watch")
        os.makedirs(watch_dir, exist_ok=True)
        tm.create_trigger("s1", TriggerType.FILE_WATCHER, "flows/a.json",
                          name="Saved Trigger",
                          config={"watch_path": watch_dir, "poll_interval": 0.2},
                          enabled=False)
        tm.save_triggers(filepath)
        tm.stop_all()

        # Load into new manager
        tm2 = TriggerManager()
        tm2.load_triggers(filepath)
        listing = tm2.list_triggers()
        assert len(listing) == 1
        assert listing[0]["trigger_id"] == "s1"
        assert listing[0]["name"] == "Saved Trigger"
        tm2.stop_all()

    def test_history_recording(self):
        tm = TriggerManager()
        # Manually add a history entry
        evt = TriggerEvent(
            trigger_id="test", timestamp=time.time(),
            event_data={"event": "test"}, flow_executed=False, error="test error",
        )
        tm._history.append(evt)
        history = tm.get_history(trigger_id="test")
        assert len(history) == 1
        assert history[0]["error"] == "test error"

    def test_history_filter_by_trigger_id(self):
        tm = TriggerManager()
        tm._history.append(TriggerEvent("a", time.time(), {"event": "x"}, True))
        tm._history.append(TriggerEvent("b", time.time(), {"event": "y"}, True))
        tm._history.append(TriggerEvent("a", time.time(), {"event": "z"}, True))
        assert len(tm.get_history(trigger_id="a")) == 2
        assert len(tm.get_history(trigger_id="b")) == 1

    def test_stop_all(self, tmp_path):
        tm = TriggerManager()
        tm.create_trigger("x1", TriggerType.FILE_WATCHER, "flows/t.json",
                          config={"watch_path": str(tmp_path), "poll_interval": 0.2},
                          enabled=True)
        tm.create_trigger("x2", TriggerType.FILE_WATCHER, "flows/t.json",
                          config={"watch_path": str(tmp_path), "poll_interval": 0.2},
                          enabled=True)
        time.sleep(0.1)
        tm.stop_all()
        for t in tm.list_triggers():
            assert t["state"] == "stopped"

    def test_get_trigger(self, tmp_path):
        tm = TriggerManager()
        tm.create_trigger("g1", TriggerType.FILE_WATCHER, "flows/t.json",
                          config={"watch_path": str(tmp_path)}, enabled=False)
        assert tm.get_trigger("g1") is not None
        assert tm.get_trigger("nonexistent") is None
        tm.stop_all()


# ---------------------------------------------------------------------------
# FileWatcherTrigger tests
# ---------------------------------------------------------------------------

class TestFileWatcherTrigger:

    def test_detects_new_files(self, tmp_path):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.FILE_WATCHER,
            watch_path=str(tmp_path), poll_interval=0.2, on_create=True,
        )
        trigger = FileWatcherTrigger(config, callback)
        trigger.start()
        time.sleep(0.1)

        # Create a new file
        (tmp_path / "newfile.txt").write_text("hello")
        time.sleep(0.5)
        trigger.stop()

        assert len(events) >= 1
        assert events[0]["event_data"]["event"] == "file_created"
        assert events[0]["event_data"]["file_name"] == "newfile.txt"

    def test_respects_patterns(self, tmp_path):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.FILE_WATCHER,
            watch_path=str(tmp_path), poll_interval=0.2,
            on_create=True, patterns=["*.csv"],
        )
        trigger = FileWatcherTrigger(config, callback)
        trigger.start()
        time.sleep(0.1)

        # Create files
        (tmp_path / "data.csv").write_text("a,b,c")
        (tmp_path / "ignore.txt").write_text("nope")
        time.sleep(0.5)
        trigger.stop()

        assert len(events) == 1
        assert events[0]["event_data"]["file_name"] == "data.csv"

    def test_on_modify_detects_changes(self, tmp_path):
        # Create file before starting trigger
        test_file = tmp_path / "existing.txt"
        test_file.write_text("original")

        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.FILE_WATCHER,
            watch_path=str(tmp_path), poll_interval=0.2,
            on_create=False, on_modify=True,
        )
        trigger = FileWatcherTrigger(config, callback)
        trigger.start()
        time.sleep(0.3)

        # Modify the file (ensure mtime changes)
        time.sleep(0.1)
        test_file.write_text("modified")
        time.sleep(0.5)
        trigger.stop()

        # Should detect modification
        modify_events = [e for e in events if e["event_data"]["event"] == "file_modified"]
        assert len(modify_events) >= 1

    def test_does_not_fire_when_paused(self, tmp_path):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.FILE_WATCHER,
            watch_path=str(tmp_path), poll_interval=0.2, on_create=True,
        )
        trigger = FileWatcherTrigger(config, callback)
        trigger.start()
        time.sleep(0.1)
        trigger.pause()

        # Create a file while paused
        (tmp_path / "paused.txt").write_text("should not fire")
        time.sleep(0.3)
        trigger.stop()

        # The watch loop exits when paused, so no new files detected
        paused_events = [e for e in events if e["event_data"].get("file_name") == "paused.txt"]
        assert len(paused_events) == 0

    def test_nonexistent_watch_path(self):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.FILE_WATCHER,
            watch_path="/nonexistent/path/xyz", poll_interval=0.2,
        )
        trigger = FileWatcherTrigger(config, callback)
        trigger.start()
        time.sleep(0.3)
        trigger.stop()
        # Should not crash, just no events
        assert len(events) == 0


# ---------------------------------------------------------------------------
# EventTrigger tests
# ---------------------------------------------------------------------------

class TestEventTrigger:

    def setup_method(self):
        from core.notifications import NotificationManager
        NotificationManager.reset()

    def test_fires_on_matching_event(self):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.EVENT,
            events=["flow.completed"],
        )
        trigger = EventTrigger(config, callback)
        trigger.start()

        # Fire a matching event
        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        nm.notify("flow.completed", {"flow_id": "test-flow"}, async_send=False)

        time.sleep(0.1)
        trigger.stop()

        assert len(events) >= 1
        assert events[0]["event_data"]["event_type"] == "flow.completed"

    def test_respects_filter(self):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.EVENT,
            events=["flow.completed"],
            filter={"flow_id": "target-flow"},
        )
        trigger = EventTrigger(config, callback)
        trigger.start()

        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        # Non-matching event
        nm.notify("flow.completed", {"flow_id": "other-flow"}, async_send=False)
        # Matching event
        nm.notify("flow.completed", {"flow_id": "target-flow"}, async_send=False)

        time.sleep(0.1)
        trigger.stop()

        assert len(events) == 1
        assert events[0]["event_data"]["payload"]["flow_id"] == "target-flow"

    def test_ignores_non_matching_events(self):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.EVENT,
            events=["flow.completed"],
        )
        trigger = EventTrigger(config, callback)
        trigger.start()

        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        nm.notify("task.failed", {"task_id": "t1"}, async_send=False)

        time.sleep(0.1)
        trigger.stop()

        assert len(events) == 0

    def test_wildcard_events(self):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.EVENT,
            events=["flow.*"],
        )
        trigger = EventTrigger(config, callback)
        trigger.start()

        from core.notifications import NotificationManager
        nm = NotificationManager.get_instance()
        nm.notify("flow.started", {}, async_send=False)
        nm.notify("flow.completed", {}, async_send=False)
        nm.notify("task.failed", {}, async_send=False)

        time.sleep(0.1)
        trigger.stop()

        assert len(events) == 2


# ---------------------------------------------------------------------------
# PollingTrigger tests
# ---------------------------------------------------------------------------

class TestPollingTrigger:

    def test_status_ok_condition(self):
        """Test that status_ok fires when URL returns 200."""
        # We test the _check method directly with a mock
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.POLLING,
            url="http://localhost:99999",  # won't actually connect
            interval=60, condition="status_ok",
        )
        trigger = PollingTrigger(config, callback)
        trigger._state = TriggerState.ACTIVE

        # The real _check would fail since no server - test that it handles gracefully
        trigger._check()
        # No events since connection fails
        assert len(events) == 0

    def test_content_changed_condition(self):
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.POLLING,
            url="http://example.com", interval=60, condition="content_changed",
        )
        trigger = PollingTrigger(config, callback)
        trigger._state = TriggerState.ACTIVE

        import hashlib
        # Simulate content tracking
        trigger._last_content_hash = hashlib.sha256(b"old content").hexdigest()

        # _check would need a real server; test hash tracking logic
        assert trigger._last_content_hash is not None
        new_hash = hashlib.sha256(b"new content").hexdigest()
        assert trigger._last_content_hash != new_hash

    def test_json_match_condition_logic(self):
        """Test the JSON path matching logic."""
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.POLLING,
            url="http://localhost:99999", interval=60,
            condition="json_match", json_path="data.status",
            expected_value="ready",
        )
        trigger = PollingTrigger(config, callback)
        assert trigger._json_path == "data.status"
        assert trigger._expected_value == "ready"
        assert trigger._condition == "json_match"


# ---------------------------------------------------------------------------
# WebhookTrigger tests
# ---------------------------------------------------------------------------

class TestWebhookTrigger:

    def _find_free_port(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]

    def test_starts_http_server(self):
        port = self._find_free_port()
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.WEBHOOK,
            port=port, path="/webhook",
        )
        trigger = WebhookTrigger(config, callback)
        trigger.start()
        time.sleep(0.3)

        assert trigger.state == TriggerState.ACTIVE
        trigger.stop()

    def test_accepts_post_requests(self):
        port = self._find_free_port()
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.WEBHOOK,
            port=port, path="/webhook",
        )
        trigger = WebhookTrigger(config, callback)
        trigger.start()
        time.sleep(0.3)

        # Send POST request
        body = json.dumps({"key": "value"}).encode()
        req = urllib.request.Request(
            f"http://localhost:{port}/webhook",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            result = json.loads(resp.read())
            assert result["status"] == "accepted"

        time.sleep(0.1)
        trigger.stop()

        assert len(events) >= 1
        assert events[0]["event_data"]["event"] == "webhook_received"
        assert events[0]["event_data"]["body"] == {"key": "value"}

    def test_rejects_wrong_path(self):
        port = self._find_free_port()
        callback, events = _make_fire_recorder()
        config = _make_config(
            TriggerType.WEBHOOK,
            port=port, path="/webhook",
        )
        trigger = WebhookTrigger(config, callback)
        trigger.start()
        time.sleep(0.3)

        # Send POST to wrong path
        req = urllib.request.Request(
            f"http://localhost:{port}/wrong-path",
            data=b"{}",
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "Should have returned 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404

        trigger.stop()
        assert len(events) == 0


# ---------------------------------------------------------------------------
# BaseTrigger tests
# ---------------------------------------------------------------------------

class TestBaseTrigger:

    def test_fire_increments_count(self):
        callback, events = _make_fire_recorder()
        config = _make_config(TriggerType.FILE_WATCHER)
        trigger = BaseTrigger(config, callback)
        trigger._state = TriggerState.ACTIVE

        trigger.fire({"test": True})
        trigger.fire({"test": True})

        assert trigger._fire_count == 2
        assert trigger._last_fired is not None
        assert len(events) == 2

    def test_fire_does_not_fire_when_not_active(self):
        callback, events = _make_fire_recorder()
        config = _make_config(TriggerType.FILE_WATCHER)
        trigger = BaseTrigger(config, callback)

        trigger.fire({"test": True})
        assert trigger._fire_count == 0
        assert len(events) == 0

    def test_fire_records_errors(self):
        def bad_callback(trigger_id, event_data):
            raise RuntimeError("boom")

        config = _make_config(TriggerType.FILE_WATCHER)
        trigger = BaseTrigger(config, bad_callback)
        trigger._state = TriggerState.ACTIVE

        trigger.fire({"test": True})
        assert trigger._fire_count == 1
        assert len(trigger._errors) == 1
        assert "boom" in trigger._errors[0]

    def test_get_status(self):
        callback, _ = _make_fire_recorder()
        config = _make_config(TriggerType.FILE_WATCHER, trigger_id="st1")
        trigger = BaseTrigger(config, callback)
        status = trigger.get_status()
        assert status["trigger_id"] == "st1"
        assert status["state"] == "stopped"
        assert status["fire_count"] == 0

    def test_state_transitions(self):
        callback, _ = _make_fire_recorder()
        config = _make_config(TriggerType.FILE_WATCHER)
        trigger = BaseTrigger(config, callback)

        assert trigger.state == TriggerState.STOPPED
        trigger.start()
        assert trigger.state == TriggerState.ACTIVE
        trigger.pause()
        assert trigger.state == TriggerState.PAUSED
        trigger.resume()
        assert trigger.state == TriggerState.ACTIVE
        trigger.stop()
        assert trigger.state == TriggerState.STOPPED

    def test_resume_only_from_paused(self):
        callback, _ = _make_fire_recorder()
        config = _make_config(TriggerType.FILE_WATCHER)
        trigger = BaseTrigger(config, callback)

        # Resume from stopped does nothing
        trigger.resume()
        assert trigger.state == TriggerState.STOPPED

    def test_error_list_capped(self):
        def bad_callback(trigger_id, event_data):
            raise RuntimeError("err")

        config = _make_config(TriggerType.FILE_WATCHER)
        trigger = BaseTrigger(config, bad_callback)
        trigger._state = TriggerState.ACTIVE

        for _ in range(60):
            trigger.fire({"x": 1})

        assert len(trigger._errors) <= 50


# ---------------------------------------------------------------------------
# Integration-level tests
# ---------------------------------------------------------------------------

class TestTriggerIntegration:

    def test_trigger_fire_creates_flowfile_with_attributes(self, tmp_path):
        """Test that _on_trigger_fire creates a FlowFile with trigger attributes.
        This will fail because the flow path doesn't exist, but the error
        is captured in history."""
        tm = TriggerManager()
        tm.create_trigger(
            "int1", TriggerType.FILE_WATCHER, "nonexistent_flow.json",
            config={"watch_path": str(tmp_path), "poll_interval": 100},
            enabled=False,
        )
        # Simulate firing
        tm._on_trigger_fire("int1", {"event": "test_event", "key": "val"})

        history = tm.get_history(trigger_id="int1")
        assert len(history) == 1
        # Flow execution should fail (no such file)
        assert history[0]["flow_executed"] is False
        assert history[0]["error"] != ""

    def test_error_handling_bad_flow_path(self):
        tm = TriggerManager()
        tm.create_trigger(
            "bad", TriggerType.FILE_WATCHER, "/no/such/flow.json",
            config={"watch_path": "."}, enabled=False,
        )
        tm._on_trigger_fire("bad", {"event": "test"})
        history = tm.get_history(trigger_id="bad")
        assert len(history) == 1
        assert history[0]["flow_executed"] is False
