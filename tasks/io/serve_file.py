"""ServeFile Task — Serve files from the FileStore via HTTP.

Reads file_id from the FlowFile path parameters, looks up the file
in the FileStore, and sets the FlowFile content + headers for HTTP response.

Flow pattern:
    httpReceiver (GET /files/{file_id}/{filename}) → serveFile → handleHTTPResponse
"""

import logging
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask
from core.file_store import FileStore

logger = logging.getLogger(__name__)


class ServeFileTask(BaseTask):
    """Serve a file from the FileStore."""

    TYPE = "serveFile"
    VERSION = "1.0.0"
    NAME = "Serve File"
    DESCRIPTION = "Serve a file from the temporary file store"
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
            logger.warning("serveFile: no file_id found")
            flowfile.set_content(b'{"error": "Not Found", "message": "No file ID provided"}')
            flowfile.set_attribute("http.response.status", "400")
            flowfile.set_attribute("http.response.header.Content-Type", "application/json")
            return [flowfile]

        store = FileStore.instance()
        user_id = flowfile.get_attribute("http.auth.principal") or ""
        result = store.get(file_id, user_id=user_id)

        if result is None:
            # Distinguish 403 from 404
            raw_entry = store.get(file_id)  # check if exists without user filter
            if raw_entry:
                logger.info(f"serveFile: file {file_id} access denied for user '{user_id}'")
                flowfile.set_attribute("http.response.status", "403")
                flowfile.set_content(b"Access denied")
            else:
                logger.info(f"serveFile: file {file_id} not found or expired")
                flowfile.set_content(b'{"error": "Not Found", "message": "File not found or expired"}')
                flowfile.set_attribute("http.response.status", "404")
            flowfile.set_attribute("http.response.header.Content-Type", "application/json")
            return [flowfile]

        filename, content, content_type = result

        flowfile.set_content(content)
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type", content_type)
        flowfile.set_attribute(
            "http.response.header.Content-Disposition",
            f'attachment; filename="{filename}"'
        )
        flowfile.set_attribute("http.response.header.Content-Length", str(len(content)))
        # Allow loading under COEP: require-corp (needed for SharedArrayBuffer)
        flowfile.set_attribute("http.response.header.Cross-Origin-Resource-Policy", "same-origin")

        logger.info(f"serveFile: serving '{filename}' ({len(content)} bytes)")
        return [flowfile]


TaskFactory.register(ServeFileTask)
