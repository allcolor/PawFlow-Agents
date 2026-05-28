"""Tests for the executor registry."""

import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from core.executor_registry import ExecutorRegistry


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

    def test_register_and_get(self):
        mock_ex = MagicMock()
        self.registry.register("test_flow", mock_ex)
        assert self.registry.get("test_flow") is mock_ex

    def test_unregister(self):
        mock_ex = MagicMock()
        self.registry.register("test_flow", mock_ex)
        self.registry.unregister("test_flow")
        assert self.registry.get("test_flow") is None

    def test_restore_skips_if_already_restored(self):
        self.registry._restored = True
        self.registry.restore_from_disk()  # Should be a no-op

    def test_restore_no_deployments(self):
        # Mock DeploymentRegistry to return empty
        with patch("core.executor_registry._get_deployment_registry") as mock_dr:
            mock_dr.return_value = None
            self.registry.restore_from_disk()  # Should not crash
        assert self.registry.count() == 0

    def test_restore_merges_deployment_parameters_before_parse(self):
        from engine.continuous_executor import ContinuousFlowExecutor
        from tasks import register_all_tasks

        register_all_tasks()
        with tempfile.TemporaryDirectory() as td:
            flow_path = Path(td) / "flow.json"
            flow_path.write_text(json.dumps({
                "id": "installer",
                "name": "Installer",
                "version": "1.0.0",
                "parameters": {"port": 9090},
                "services": {
                    "http_listener": {
                        "type": "httpListener",
                        "parameters": {"host": "0.0.0.0", "port": "${port}"},
                    },
                },
                "tasks": {},
                "relations": [],
            }), encoding="utf-8")

            with patch.object(ContinuousFlowExecutor, "start", lambda self: None):
                ok = self.registry._restore_instance(
                    "installer", str(flow_path), parameters={"port": 19990})

            executor = self.registry.get("installer")
            assert ok is True
            assert executor is not None
            assert executor._flow.services["http_listener"].config.get("port") == "19990"


if __name__ == "__main__":
    unittest.main()
