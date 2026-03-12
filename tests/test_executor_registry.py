"""Tests for the executor registry."""

import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from gui.services.executor_registry import ExecutorRegistry


class TestExecutorRegistry(unittest.TestCase):

    def setUp(self):
        # Reset singleton for each test
        ExecutorRegistry._instance = None
        self.registry = ExecutorRegistry.get_instance()

    def tearDown(self):
        ExecutorRegistry._instance = None
        # Clean up state file
        p = Path("continuous_state.json")
        if p.exists():
            p.unlink()

    def test_singleton(self):
        r1 = ExecutorRegistry.get_instance()
        r2 = ExecutorRegistry.get_instance()
        assert r1 is r2

    def test_register_and_get(self):
        mock_ex = MagicMock()
        self.registry.register("flow_1", mock_ex)
        assert self.registry.get("flow_1") is mock_ex
        assert self.registry.count() == 1

    def test_unregister(self):
        mock_ex = MagicMock()
        self.registry.register("flow_1", mock_ex)
        self.registry.unregister("flow_1")
        assert self.registry.get("flow_1") is None
        assert self.registry.count() == 0

    def test_get_all(self):
        ex1 = MagicMock()
        ex2 = MagicMock()
        self.registry.register("f1", ex1)
        self.registry.register("f2", ex2)
        all_ex = self.registry.get_all()
        assert len(all_ex) == 2
        assert "f1" in all_ex
        assert "f2" in all_ex

    def test_cleanup_dead(self):
        alive = MagicMock()
        alive.get_status.return_value = {"is_running": True}
        dead = MagicMock()
        dead.get_status.return_value = {"is_running": False}

        self.registry.register("alive", alive)
        self.registry.register("dead", dead)

        removed = self.registry.cleanup_dead()
        assert "dead" in removed
        assert self.registry.count() == 1
        assert self.registry.get("alive") is alive

    def test_save_state_creates_file(self):
        mock_ex = MagicMock()
        mock_ex._flow = MagicMock()
        mock_ex._flow.id = "test_flow"
        mock_ex._flow_version = 3
        mock_ex._max_workers = 4
        mock_ex._max_retries = 2
        mock_ex._parameter_context = MagicMock()
        mock_ex._parameter_context._params = {"key": "val"}

        self.registry.register("test_flow", mock_ex)

        p = Path("continuous_state.json")
        assert p.exists()
        data = json.loads(p.read_text())
        assert len(data["running_flows"]) == 1
        assert data["running_flows"][0]["flow_id"] == "test_flow"
        assert data["running_flows"][0]["flow_version"] == 3

    def test_unregister_updates_state_file(self):
        mock_ex = MagicMock()
        mock_ex._flow = MagicMock()
        mock_ex._flow.id = "test_flow"
        mock_ex._flow_version = 1
        mock_ex._max_workers = 4
        mock_ex._max_retries = 2
        mock_ex._parameter_context = MagicMock()
        mock_ex._parameter_context._params = {}

        self.registry.register("test_flow", mock_ex)
        self.registry.unregister("test_flow")

        p = Path("continuous_state.json")
        data = json.loads(p.read_text())
        assert len(data["running_flows"]) == 0

    def test_restore_skips_if_already_restored(self):
        self.registry._restored = True
        self.registry.restore_from_disk()  # Should be a no-op

    def test_restore_no_state_file(self):
        self.registry.restore_from_disk()  # Should not crash
        assert self.registry.count() == 0


if __name__ == "__main__":
    unittest.main()
