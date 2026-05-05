"""Install bootstrap HTTP task."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.install_bootstrap import finalize_install, get_install_status


class InstallBootstrapTask(BaseTask):
    """Serve dynamic first-run installer API responses."""

    TYPE = "installBootstrap"
    VERSION = "1.0.0"
    NAME = "Install Bootstrap"
    DESCRIPTION = "Expose the first-run installer state and finalization API"
    ICON = "settings"
    TAGS = ["system", "install", "bootstrap"]

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        method = (flowfile.get_attribute("http.method") or "GET").upper()
        path = flowfile.get_attribute("http.path") or ""

        try:
            if method == "GET" and path == "/install/api":
                status = 200
                payload = get_install_status()
            elif method == "POST" and path == "/install/api/finalize":
                status = 200
                payload = finalize_install(self._request_json(flowfile))
            else:
                status = 404
                payload = {"error": "unknown installer endpoint"}
        except PermissionError as exc:
            status = 403
            payload = {"error": str(exc)}
        except ValueError as exc:
            status = 400
            payload = {"error": str(exc)}

        flowfile.set_content(json.dumps(payload).encode("utf-8"))
        flowfile.set_attribute("mime.type", "application/json")
        flowfile.set_attribute("http.response.status", str(status))
        flowfile.set_attribute("http.response.header.Cache-Control", "no-store")
        return [flowfile]

    @staticmethod
    def _request_json(flowfile: FlowFile) -> Dict[str, Any]:
        raw = flowfile.get_content() or b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data


TaskFactory.register(InstallBootstrapTask)
