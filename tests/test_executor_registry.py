"""Tests for the executor registry."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.executor_registry import ExecutorRegistry


class TestExecutorRegistry(unittest.TestCase):

    def setUp(self):
        # Reset singleton for each test
        ExecutorRegistry._instance = None
        self.registry = ExecutorRegistry.get_instance()

    def tearDown(self):
        ExecutorRegistry._instance = None
        try:
            from services.http_listener_service import _instances
            _instances.clear()
        except Exception:
            pass
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
        assert mock_ex._instance_id == "flow_1"
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

    def test_restore_skips_if_already_restored(self):
        self.registry._restored = True
        self.registry.restore_from_disk()  # Should be a no-op

    def test_restore_no_deployments(self):
        # Mock DeploymentRegistry to return empty
        with patch("core.executor_registry._get_deployment_registry") as mock_dr:
            mock_dr.return_value = None
            self.registry.restore_from_disk()  # Should not crash
        assert self.registry.count() == 0

    def test_restore_required_instance_first_even_when_persisted_error(self):
        instances = {
            "ordinary": SimpleNamespace(
                status="running", flow_path="ordinary.json", max_workers=4,
                max_retries=0, parameters={}, service_overrides={},
                service_configs={}, owner=None, conversation_id=None,
                flow_fqn="", flow_scope="", agent_name=""),
            "pawflow-agent": SimpleNamespace(
                status="error", flow_path="main.json", max_workers=4,
                max_retries=0, parameters={}, service_overrides={},
                service_configs={}, owner=None, conversation_id=None,
                flow_fqn="", flow_scope="", agent_name=""),
        }
        deployments = MagicMock()
        deployments.get_all.return_value = instances
        restored = []

        def fake_restore(instance_id, *args, **kwargs):
            restored.append((instance_id, kwargs))
            return True

        with patch("core.executor_registry._get_deployment_registry",
                   return_value=deployments), patch.object(
                       self.registry, "_restore_instance", side_effect=fake_restore):
            self.registry.restore_from_disk(required_instance_id="pawflow-agent")

        assert [item[0] for item in restored] == ["pawflow-agent", "ordinary"]
        assert restored[0][1]["strict_initialization"] is True
        assert restored[0][1]["require_http_routes"] is True
        assert self.registry._restored is True

    def test_failed_required_restore_remains_retryable(self):
        main = SimpleNamespace(
            status="error", flow_path="main.json", max_workers=4,
            max_retries=0, parameters={}, service_overrides={},
            service_configs={}, owner=None, conversation_id=None,
            flow_fqn="", flow_scope="", agent_name="")
        deployments = MagicMock()
        deployments.get_all.return_value = {"pawflow-agent": main}

        with patch("core.executor_registry._get_deployment_registry",
                   return_value=deployments), patch.object(
                       self.registry, "_restore_instance", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "pawflow-agent"):
                self.registry.restore_from_disk(
                    required_instance_id="pawflow-agent")

        assert self.registry._restored is False

    def test_required_instance_cannot_be_unregistered_by_default(self):
        executor = MagicMock()
        with patch("core.executor_registry._get_deployment_registry",
                   return_value=None):
            self.registry.register("pawflow-agent", executor)
            with self.assertRaisesRegex(RuntimeError, "required system flow"):
                self.registry.unregister("pawflow-agent")
            assert self.registry.get("pawflow-agent") is executor
            self.registry.unregister("pawflow-agent", allow_required=True)
        assert self.registry.get("pawflow-agent") is None

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

    def test_restore_merges_service_configs_before_parse(self):
        from engine.continuous_executor import ContinuousFlowExecutor
        from tasks import register_all_tasks

        register_all_tasks()
        with tempfile.TemporaryDirectory() as td:
            flow_path = Path(td) / "flow.json"
            flow_path.write_text(json.dumps({
                "id": "installer",
                "name": "Installer",
                "version": "1.0.0",
                "services": {
                    "http_listener": {
                        "type": "httpListener",
                        "parameters": {"host": "0.0.0.0", "port": 9090},
                    },
                },
                "tasks": {},
                "relations": [],
            }), encoding="utf-8")

            with patch.object(ContinuousFlowExecutor, "start", lambda self: None):
                ok = self.registry._restore_instance(
                    "installer", str(flow_path),
                    service_configs={"http_listener": {"port": 19991}})

            executor = self.registry.get("installer")
            listener = executor._flow.services["http_listener"]
            assert ok is True
            assert listener.config.get("port") == 19991
            assert listener.port == 19991

    def test_restore_injects_instance_id_parameter(self):
        """The unique deploy id is exposed as the reserved ${_instance_id} param.

        Flows mint per-instance, collision-free routes from it (e.g.
        /webhooks/github/${_instance_id}). It must be present even when no
        deployment parameters are supplied.
        """
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
                    "installer__xyz789", str(flow_path))

            executor = self.registry.get("installer__xyz789")
            assert ok is True
            assert executor._flow.parameters.get("_instance_id") == "installer__xyz789"
            # Template defaults survive the injection.
            assert executor._flow.parameters.get("port") == 9090


if __name__ == "__main__":
    unittest.main()
