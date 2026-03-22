"""ServeAssets Task — Serve static assets from the flow's assets directory.

Maps an HTTP route to a directory of static files (JS, CSS, images, etc.).
Assets are resolved via the standard BaseTask asset resolution chain:
flow_source_dir/assets/ → flow_source_dir/ → task module dir/

Flow pattern:
    httpReceiver (GET /chat/assets/{path}) → serveAssets → handleHTTPResponse

Config:
    base_path: URL path prefix (e.g. "/chat/assets") — informational only
    assets_prefix: Subdirectory within assets/ (e.g. "chat_ui")
    cache_control: Cache-Control header (default: "public, max-age=3600")
"""

import logging
import mimetypes
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

# Ensure common web types are registered
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")


class ServeAssetsTask(BaseTask):
    """Serve static files from the flow's assets directory."""

    TYPE = "serveAssets"
    VERSION = "1.0.0"
    NAME = "Serve Assets"
    DESCRIPTION = "Serve static assets (JS, CSS, images) from flow directory"
    ICON = "folder"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "assets_prefix": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Subdirectory within assets/ to serve from (e.g. 'chat_ui')",
            },
            "cache_control": {
                "type": "string",
                "required": False,
                "default": "public, max-age=3600",
                "description": "Cache-Control header for responses",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        # Extract the requested path from URL
        # Convention: the route captures {path} as http.path.path
        requested_path = (
            flowfile.get_attribute("http.path.path")
            or flowfile.get_attribute("http.path.filename")
            or ""
        )

        if not requested_path:
            flowfile.set_content(b'{"error": "No path specified"}')
            flowfile.set_attribute("http.response.status", "400")
            flowfile.set_attribute("http.response.content-type", "application/json")
            return [flowfile]

        # Security: prevent path traversal
        clean_path = requested_path.replace("\\", "/")
        if ".." in clean_path or clean_path.startswith("/"):
            flowfile.set_content(b'{"error": "Invalid path"}')
            flowfile.set_attribute("http.response.status", "403")
            flowfile.set_attribute("http.response.content-type", "application/json")
            return [flowfile]

        # Build the full asset path
        prefix = self.config.get("assets_prefix", "")
        if prefix:
            asset_path = f"{prefix}/{clean_path}"
        else:
            asset_path = clean_path

        # Load the asset
        try:
            content = self.get_asset(asset_path)
        except FileNotFoundError:
            flowfile.set_content(b'{"error": "Not found"}')
            flowfile.set_attribute("http.response.status", "404")
            flowfile.set_attribute("http.response.content-type", "application/json")
            return [flowfile]

        # Determine MIME type
        mime_type, _ = mimetypes.guess_type(clean_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        # Set response
        flowfile.set_content(content)
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.content-type", mime_type)

        cache_control = self.config.get("cache_control", "public, max-age=3600")
        if cache_control:
            flowfile.set_attribute("http.response.cache-control", cache_control)

        return [flowfile]


TaskFactory.register(ServeAssetsTask)
