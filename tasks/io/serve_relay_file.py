"""ServeRelayFile Task — Serve a file from a filesystem service via HTTP.

Mirrors `ServeFileTask` (which serves from FileStore) but reads the file
from a relay/filesystem service. The webchat uses this so it can render
images / audio / video stored on the user's relay inline (`<img src=
"/fs/<service>/<path>">`) without an extra `call_tool('read')` round-trip.

Auth: the user must be the principal of the HTTP session AND own
(or have access to) the relay service that holds the file. Service
resolution is identical to other fs_* handlers (conv > user > global
scope) via `find_fs_service`.

Flow:
    httpReceiver (GET /fs/{service_name}/{rest+}) → validate_auth
    → route_after_auth (relationship "fs") → serveRelayFile
    → handleHTTPResponse
"""

import logging
import mimetypes
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.handlers._fs_base import find_fs_service

logger = logging.getLogger(__name__)


class ServeRelayFileTask(BaseTask):
    """Serve a file from a relay/filesystem service with auth + Content-Type."""

    TYPE = "serveRelayFile"
    VERSION = "1.0.0"
    NAME = "Serve Relay File"
    DESCRIPTION = ("Serve a file from a filesystem/relay service over HTTP "
                   "(used by the chat UI to inline media stored on the relay).")
    ICON = "download"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "service_attribute": {
                "type": "string", "required": False,
                "default": "http.path.service_name",
                "description": "FlowFile attribute holding the service name",
            },
            "path_attribute": {
                "type": "string", "required": False,
                "default": "http.path.rest",
                "description": "FlowFile attribute holding the file path "
                               "(relative to the service root)",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        svc_attr = self.config.get("service_attribute", "http.path.service_name")
        path_attr = self.config.get("path_attribute", "http.path.rest")
        service_name = flowfile.get_attribute(svc_attr) or ""
        rel_path = flowfile.get_attribute(path_attr) or ""
        user_id = flowfile.get_attribute("http.auth.principal") or ""

        if not service_name or not rel_path:
            return self._error(flowfile, 400,
                               "Missing service or path in /fs/<service>/<path>.")
        if not user_id:
            return self._error(flowfile, 401, "Authentication required.")

        svc = find_fs_service(user_id, service_name)
        if svc is None:
            return self._error(flowfile, 404,
                               f"Service '{service_name}' not found or not "
                               f"accessible to user '{user_id}'.")
        if not hasattr(svc, "read_file"):
            return self._error(flowfile, 400,
                               f"Service '{service_name}' does not support "
                               f"file reads.")
        try:
            data = svc.read_file(rel_path)
        except FileNotFoundError:
            return self._error(flowfile, 404,
                               f"File '{rel_path}' not found on '{service_name}'.")
        except PermissionError:
            return self._error(flowfile, 403,
                               f"Access denied to '{rel_path}' on '{service_name}'.")
        except Exception as e:
            logger.warning("serveRelayFile: read_file(%s, %s) failed: %s",
                           service_name, rel_path, e)
            return self._error(flowfile, 502,
                               f"Read failed on '{service_name}': {e}")

        if not isinstance(data, (bytes, bytearray)):
            data = str(data).encode("utf-8")

        fname = rel_path.rsplit("/", 1)[-1] if "/" in rel_path else rel_path
        content_type = mimetypes.guess_type(fname)[0] or "application/octet-stream"

        flowfile.set_content(bytes(data))
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type", content_type)
        flowfile.set_attribute(
            "http.response.header.Content-Disposition",
            f'inline; filename="{fname}"')
        flowfile.set_attribute("http.response.header.Content-Length",
                               str(len(data)))
        return [flowfile]

    @staticmethod
    def _error(flowfile: FlowFile, status: int, message: str) -> List[FlowFile]:
        import json as _json
        flowfile.set_content(_json.dumps({"error": message}).encode("utf-8"))
        flowfile.set_attribute("http.response.status", str(status))
        flowfile.set_attribute("http.response.header.Content-Type",
                               "application/json")
        return [flowfile]


TaskFactory.register(ServeRelayFileTask)
