"""validateHTTPAuth — validates HTTP authentication on incoming requests.

Place this task between httpReceiver and your business logic.
On success, the FlowFile continues with auth attributes added.
On failure, the FlowFile is routed to "failure" relationship with
appropriate 401/403 status, and a response is auto-sent via the
HTTP listener service.

Config:
    auth_service_id: str    — ID of the HTTPAuthService
    listener_service_id: str — ID of the HTTPListenerService (for auto-response)
    auto_respond: bool      — if True, automatically sends 401/403 response (default True)
    header_name: str        — header containing auth (default "authorization")

FlowFile attributes set on success:
    http.auth.principal     — authenticated user/token identifier
    http.auth.valid         — "true"

FlowFile attributes set on failure:
    http.auth.valid         — "false"
    http.auth.error         — error message
    route.relationship      — set to "failure"
"""

import json
import logging
from typing import Any, Dict, List, Optional

from core import FlowFile
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class ValidateHTTPAuthTask(BaseTask):
    """Validate HTTP auth headers using an HTTPAuthService."""

    TYPE = "validateHTTPAuth"
    DESCRIPTION = "Validate Bearer/Basic authentication on HTTP requests"
    TAGS = ["http", "auth", "security"]

    PARAMETERS = {
        "auth_service_id": {
            "type": "string",
            "description": "ID of the HTTPAuthService",
            "required": True,
        },
        "listener_service_id": {
            "type": "string",
            "description": "ID of the HTTPListenerService (for auto-response on failure)",
            "required": False,
        },
        "auto_respond": {
            "type": "boolean",
            "description": "Automatically send 401/403 response on auth failure",
            "required": False,
            "default": True,
        },
        "header_name": {
            "type": "string",
            "description": "Header name containing auth credentials",
            "required": False,
            "default": "authorization",
        },
    }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        # Get auth service
        auth_service_id = self.config.get("auth_service_id", "")
        auth_svc = self.get_service(auth_service_id)
        if not auth_svc:
            raise RuntimeError(f"HTTPAuthService '{auth_service_id}' not found")

        # Get the authorization header from FlowFile attributes
        header_name = self.config.get("header_name", "authorization")
        auth_header = flowfile.get_attribute(f"http.header.{header_name}")

        # Validate
        result = auth_svc.validate(auth_header)

        if result.valid:
            # Auth OK — add attributes and pass through
            flowfile.set_attribute("http.auth.valid", "true")
            flowfile.set_attribute("http.auth.principal", result.principal)
            if result.roles:
                flowfile.set_attribute("http.auth.roles", ",".join(result.roles))
            return [flowfile]

        # Auth FAILED
        flowfile.set_attribute("http.auth.valid", "false")
        flowfile.set_attribute("http.auth.error", result.error)
        flowfile.set_attribute("route.relationship", "failure")

        # Auto-respond with 401/403
        auto_respond = self.config.get("auto_respond", True)
        if auto_respond:
            request_id = flowfile.get_attribute("http.request.id")
            listener_service_id = self.config.get("listener_service_id", "")
            listener_svc = self.get_service(listener_service_id)

            if listener_svc and request_id:
                status = result.status_code or 401
                headers = {"Content-Type": "application/json"}
                # Add WWW-Authenticate header for 401
                if status == 401 and hasattr(auth_svc, 'realm'):
                    headers["WWW-Authenticate"] = f'Bearer realm="{auth_svc.realm}"'

                body = json.dumps({
                    "error": "Unauthorized" if status == 401 else "Forbidden",
                    "message": result.error,
                }).encode()

                listener_svc.submit_response(request_id, status, headers, body)
                flowfile.set_attribute("http.response.sent", "true")
                flowfile.set_attribute("http.response.status.sent", str(status))

        return [flowfile]


from core import TaskFactory
TaskFactory.register(ValidateHTTPAuthTask)
