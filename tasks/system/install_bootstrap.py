"""Install bootstrap HTTP task."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.install_bootstrap import (
    finalize_install,
    get_install_status,
    is_install_complete,
    prepare_llm_credential_pool,
    save_llm_credential,
)


logger = logging.getLogger(__name__)


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
            if is_install_complete() and not (
                (method == "GET" and path == "/install/api")
                or (method == "POST" and path == "/install/api/finalize")
            ):
                status = 410
                payload = {"error": "installer is already finalized"}
            elif method == "GET" and path == "/install/api":
                status = 200
                payload = get_install_status()
            elif method == "POST" and path == "/install/api/llm-credential/prepare":
                status = 200
                payload = prepare_llm_credential_pool(self._request_json(flowfile))
            elif method == "POST" and path == "/install/api/llm-credential/paste":
                status = 200
                payload = save_llm_credential(self._request_json(flowfile))
            elif method == "POST" and path == "/install/api/llm-credential/server-login":
                status, payload = self._server_login(flowfile, self._request_json(flowfile), "start")
            elif method == "POST" and path == "/install/api/llm-credential/server-login/status":
                status, payload = self._server_login(flowfile, self._request_json(flowfile), "status")
            elif method == "POST" and path == "/install/api/llm-credential/server-login/cleanup":
                status, payload = self._server_login(flowfile, self._request_json(flowfile), "cleanup")
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
        except Exception as exc:
            logger.exception("Install bootstrap endpoint failed")
            status = 500
            payload = {"error": str(exc) or exc.__class__.__name__}

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

    @staticmethod
    def _server_login(flowfile: FlowFile, payload: Dict[str, Any], phase: str) -> tuple[int, Dict[str, Any]]:
        provider = str(payload.get("llm_provider") or "").strip()
        login_cli = str(payload.get("login_cli") or payload.get("server_login_cli") or "").strip()
        action_prefix = {
            "claude-code": "claude_code",
            "codex-app-server": "codex",
            "gemini": "gemini",
            "antigravity-interactive": "gemini",
        }.get(provider)
        if action_prefix == "gemini" and login_cli in {"agy", "antigravity"}:
            action_prefix = "agy"
        if not action_prefix:
            raise ValueError("selected LLM provider does not support server login")
        service_id = str(payload.get("credential_service_id") or "").strip()
        if not service_id:
            raise ValueError("credential_service_id is required")
        credential_scope = str(
            payload.get("credential_pool_scope")
            or payload.get("llm_credential_scope")
            or payload.get("llm_service_scope")
            or payload.get("service_scope")
            or "global"
        ).strip().lower()
        if credential_scope not in {"global", "user"}:
            raise ValueError("credential pool scope must be 'global' or 'user'")
        admin_username = str(payload.get("admin_username") or "admin").strip() or "admin"
        action = {
            "start": f"{action_prefix}_server_login",
            "status": f"{action_prefix}_server_login_status",
            "cleanup": f"{action_prefix}_server_login_cleanup",
        }[phase]

        from tasks.ai.actions.service_flow import _handle_service_flow

        body = {
            "service_id": service_id,
            "session_id": str(payload.get("session_id") or ""),
            "conversation_id": "",
            "scope": credential_scope,
        }
        flowfile.set_content(b"")
        flowfile.set_attribute("http.auth.roles", "admin")
        flowfile.set_attribute("auth.session_id", "install-bootstrap")
        out = _handle_service_flow(
            None, action, body, None,
            admin_username if credential_scope == "user" else "install-bootstrap",
            flowfile)
        if not out:
            return 400, {"error": "server login action did not produce a response"}
        try:
            data = json.loads((out[0].get_content() or b"{}").decode("utf-8"))
        except Exception as exc:
            raise ValueError("server login action returned invalid JSON") from exc
        return int(out[0].get_attribute("http.response.status") or 200), data


TaskFactory.register(InstallBootstrapTask)
