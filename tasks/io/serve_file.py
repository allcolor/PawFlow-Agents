"""ServeFile Task — Serve files from the FileStore via HTTP.

Reads file_id from the FlowFile path parameters, looks up the file
in the FileStore, and sets the FlowFile content + headers for HTTP response.

Access levels are enforced here:
    private       — owner only (requires auth)
    shared        — owner + named users (requires auth)
    authenticated — any logged-in user (requires auth)
    gateway_key   — anyone with ?k= param (no auth needed)
    public        — anyone (no auth needed)

Flow pattern:
    httpReceiver (GET /files/{file_id}) → serveFile → handleHTTPResponse
"""

import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.file_store import FileStore

logger = logging.getLogger(__name__)


class ServeFileTask(BaseTask):
    """Serve a file from the FileStore with access control."""

    TYPE = "serveFile"
    VERSION = "2.0.0"
    NAME = "Serve File"
    DESCRIPTION = "Serve a file from the file store with access control"
    ICON = "download"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "file_id_attribute": {
                "type": "string",
                "required": False,
                "default": "http.path.file_id",
                "description": "FlowFile attribute containing the file ID",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        attr = self.config.get("file_id_attribute", "http.path.file_id")
        file_id = flowfile.get_attribute(attr)

        if not file_id:
            flowfile.set_content(b'{"error": "No file ID provided"}')
            flowfile.set_attribute("http.response.status", "400")
            flowfile.set_attribute("http.response.header.Content-Type",
                                   "application/json")
            return [flowfile]

        store = FileStore.instance()
        user_id = flowfile.get_attribute("http.auth.principal") or ""
        gateway_key = flowfile.get_attribute("http.query.k") or ""

        if not store.exists(file_id):
            flowfile.set_content(b'{"error": "File not found or expired"}')
            flowfile.set_attribute("http.response.status", "404")
            flowfile.set_attribute("http.response.header.Content-Type",
                                   "application/json")
            return [flowfile]

        if not store.check_access(file_id, user_id=user_id,
                                   gateway_key=gateway_key):
            flowfile.set_content(b'{"error": "Access denied"}')
            flowfile.set_attribute("http.response.status", "403")
            flowfile.set_attribute("http.response.header.Content-Type",
                                   "application/json")
            return [flowfile]

        result = store.get(file_id, user_id=user_id,
                           gateway_key=gateway_key)
        if result is None:
            flowfile.set_content(b'{"error": "File not found or expired"}')
            flowfile.set_attribute("http.response.status", "404")
            flowfile.set_attribute("http.response.header.Content-Type",
                                   "application/json")
            return [flowfile]

        filename, content, content_type = result

        flowfile.set_content(content)
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type",
                               content_type)
        flowfile.set_attribute(
            "http.response.header.Content-Disposition",
            f'inline; filename="{filename}"')
        flowfile.set_attribute("http.response.header.Content-Length",
                               str(len(content)))

        return [flowfile]


TaskFactory.register(ServeFileTask)
