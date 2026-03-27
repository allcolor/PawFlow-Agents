"""ServeChatUI Task — Serve chat HTML + individual JS modules.

Routes:
    GET /chat        → HTML page with <script src> tags
    GET /chat/js/*   → individual JS files (cached)

The HTML template is in tasks/io/chat_ui/template.html.
JS modules are individual files in tasks/io/chat_ui/*.js,
served separately for proper caching and debugging.
"""

import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_CHAT_UI_DIR = Path(__file__).parent / "chat_ui"

# JS modules in load order (each file must be standalone)
_JS_MODULES = [
    "i18n.js", "state.js", "conversations.js", "messages.js",
    "active_agents.js", "typing.js", "sse.js",
    "dialogs.js", "commands.js", "context_editor.js", "memories.js",
    "secrets.js", "files_panel.js", "plans_panel.js", "attachments.js",
    "resources.js", "services.js", "file_viewer.js", "file_explorer.js",
]

# Cache: filename → (content_bytes, etag, content_hash)
_js_cache: Dict[str, tuple] = {}
_html_cache: str = ""
_js_version: str = ""  # hash of all JS combined — used as cache buster


def _compute_js_version() -> str:
    """Compute a short hash of all JS files for cache busting."""
    h = hashlib.md5()
    for mod in _JS_MODULES:
        p = _CHAT_UI_DIR / mod
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:8]


def _load_html() -> str:
    global _html_cache, _js_version
    if _html_cache:
        return _html_cache

    _js_version = _compute_js_version()
    template = (_CHAT_UI_DIR / "template.html").read_text(encoding="utf-8")

    # Build <script src> tags instead of inline JS
    script_tags = []
    for mod in _JS_MODULES:
        if (_CHAT_UI_DIR / mod).exists():
            script_tags.append(f'<script src="/chat/js/{mod}?v={_js_version}"></script>')
    scripts_html = "\n".join(script_tags)

    # Replace the JS placeholder with script tags
    html = template.replace("/* JS_PLACEHOLDER */", "")
    # Insert script tags before </body>
    html = html.replace("</body>", f"{scripts_html}\n</body>")

    _html_cache = html
    logger.info("Chat UI loaded: %d chars template, %d JS modules, version=%s",
                len(template), len(_JS_MODULES), _js_version)
    return html


def _load_js(filename: str) -> tuple:
    """Load a JS file. Returns (content_bytes, etag) or (None, None)."""
    if filename in _js_cache:
        return _js_cache[filename]
    p = _CHAT_UI_DIR / filename
    if not p.exists() or not filename.endswith(".js"):
        return None, None
    content = p.read_bytes()
    etag = hashlib.md5(content).hexdigest()[:12]
    _js_cache[filename] = (content, etag)
    return content, etag


class ServeChatUITask(BaseTask):
    """Serve the chat HTML interface and its JS assets."""

    TYPE = "serveChatUI"
    VERSION = "2.0.0"
    NAME = "Serve Chat UI"
    DESCRIPTION = "Serve an HTML chat interface for the agent"
    ICON = "chat"

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "agent_path": {
                "type": "string",
                "required": False,
                "default": "/api/agent",
                "description": "Path of the agent POST endpoint (for the chat JS to call)",
            },
            "login_url": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Login URL for OAuth2 redirect (empty = no auth required)",
            },
            "sse_path": {
                "type": "string",
                "required": False,
                "default": "/api/agent/events",
                "description": "Path of the SSE events endpoint",
            },
            "custom_css": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Custom CSS to inject into the chat UI",
            },
            "custom_css_file": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Path to a CSS file to append to the chat UI",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        req_path = flowfile.get_attribute("http.request.path") or "/chat"

        # Serve individual JS files
        if req_path.startswith("/chat/js/"):
            filename = req_path.split("/chat/js/", 1)[1].split("?")[0]
            content, etag = _load_js(filename)
            if content is None:
                flowfile.set_content(b"Not found")
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            flowfile.set_content(content)
            flowfile.set_attribute("http.response.status", "200")
            flowfile.set_attribute("http.response.header.Content-Type",
                                   "application/javascript; charset=utf-8")
            flowfile.set_attribute("http.response.header.Cache-Control",
                                   "public, max-age=31536000, immutable")
            flowfile.set_attribute("http.response.header.ETag", f'"{etag}"')
            return [flowfile]

        # Serve HTML page
        agent_path = self.config.get("agent_path", "/api/agent")
        login_url = self.config.get("login_url", "")
        sse_path = self.config.get("sse_path", "/api/agent/events")
        html = _load_html()
        html = html.replace("{{AGENT_PATH}}", agent_path)
        html = html.replace("{{LOGIN_URL}}", login_url)
        html = html.replace("{{SSE_PATH}}", sse_path)

        custom_css = self.config.get("custom_css", "")
        custom_css_file = self.config.get("custom_css_file", "")
        if custom_css_file:
            try:
                css_path = Path(custom_css_file)
                if css_path.is_file():
                    custom_css += "\n" + css_path.read_text(encoding="utf-8")
            except Exception:
                pass
        if custom_css:
            html = html.replace("</style>", f"\n/* Custom theme */\n{custom_css}\n</style>", 1)

        flowfile.set_content(html.encode("utf-8"))
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type", "text/html; charset=utf-8")
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache")

        return [flowfile]


TaskFactory.register(ServeChatUITask)
