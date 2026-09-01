"""Regression tests for the mandatory post-install PawFlow runtime."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core import FlowFile
from tasks.ai.actions.service_flow import _handle_service_flow


@pytest.mark.parametrize("action", ["stop_flow", "undeploy_flow"])
def test_service_flow_cannot_stop_or_undeploy_main_runtime(action):
    deployment = SimpleNamespace(owner=None)
    deployments = MagicMock()
    deployments.get.return_value = deployment
    executor = MagicMock(is_running=True)
    executors = MagicMock()
    executors.get.return_value = executor
    flowfile = FlowFile(
        content=b"", attributes={"http.auth.roles": "admin"})

    with patch(
        "core.deployment_registry.DeploymentRegistry.get_instance",
        return_value=deployments,
    ), patch(
        "core.executor_registry.ExecutorRegistry.get_instance",
        return_value=executors,
    ):
        result = _handle_service_flow(
            None, action, {"instance_id": "pawflow-agent"},
            None, "admin", flowfile)

    payload = json.loads(result[0].get_content().decode())
    assert result[0].get_attribute("http.response.status") == "409"
    assert "required system flow" in payload["error"]
    executor.stop.assert_not_called()
    executors.unregister.assert_not_called()
    deployments.undeploy.assert_not_called()
