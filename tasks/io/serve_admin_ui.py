"""ServeAdminUI Task — Serve the native admin GUI.

Same pattern as serve_chat_ui.py: loads HTML template + JS modules from
tasks/io/admin_ui/ and assembles them into a single page.

Flow pattern:
    httpReceiver (GET /admin) → serveAdminUI → handleHTTPResponse
"""

import logging
from pathlib import Path
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_cached_html: str = ""


def _load_admin_html() -> str:
    """Load and assemble the admin UI HTML from template + JS + CSS assets."""
    global _cached_html
    if _cached_html:
        return _cached_html

    admin_ui_dir = Path(__file__).parent / "admin_ui"
    template = (admin_ui_dir / "template.html").read_text(encoding="utf-8")

    # Load CSS
    css_path = admin_ui_dir / "styles.css"
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    template = template.replace("/* CSS_PLACEHOLDER */", css)

    # Load JS modules in order (they share a single global scope)
    _JS_MODULES = [
        "admin_core.js",
        "runtime_list.js",
        "runtime_detail.js",
        "runtime_queues.js",
        "editor_canvas.js",
        "editor_palette.js",
        "editor_config.js",
        "editor_toolbar.js",
    ]
    js_parts = []
    for mod in _JS_MODULES:
        mod_path = admin_ui_dir / mod
        if mod_path.exists():
            js_parts.append(f"// ── {mod} ──\n" + mod_path.read_text(encoding="utf-8"))
    js = "\n".join(js_parts)

    template = template.replace("/* JS_PLACEHOLDER */", js)
    _cached_html = template
    logger.info("Admin UI loaded: %d chars (%d JS from %d modules)",
                len(template), len(js), len(js_parts))
    return template


class ServeAdminUITask(BaseTask):
    """Serve the PawFlow admin GUI."""

    TYPE = "serveAdminUI"
    VERSION = "1.0.0"
    NAME = "Serve Admin UI"
    DESCRIPTION = "Serve the native PawFlow administration interface"
    ICON = "settings"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "api_path": {
                "type": "string",
                "required": False,
                "default": "/admin/api",
                "description": "Path of the admin API endpoint",
            },
            "login_url": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Login URL for OAuth2 redirect (empty = no auth)",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        api_path = self.config.get("api_path", "/admin/api")
        login_url = self.config.get("login_url", "")
        html = _load_admin_html()
        html = html.replace("{{API_PATH}}", api_path)
        html = html.replace("{{LOGIN_URL}}", login_url)

        flowfile.set_content(html.encode("utf-8"))
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type", "text/html; charset=utf-8")
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache")
        return [flowfile]


TaskFactory.register(ServeAdminUITask)
