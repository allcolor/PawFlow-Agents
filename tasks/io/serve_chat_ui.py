"""ServeChatUI Task — Serve a self-contained chat HTML interface.

Returns a complete HTML page with embedded CSS and JavaScript that provides
a chat interface for the agentLoop. The UI handles conversation_id tracking,
message history, file download links, and markdown rendering.

The HTML template and JS are loaded from tasks/io/chat_ui/ at runtime
and assembled into a single page.

Flow pattern:
    httpReceiver (GET /chat) → serveChatUI → handleHTTPResponse
"""

import logging
from pathlib import Path
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

# Cache the assembled HTML in-process (loaded once per worker)
_cached_html: str = ""


def _load_chat_html() -> str:
    """Load and assemble the chat UI HTML from template + JS assets."""
    global _cached_html
    if _cached_html:
        return _cached_html

    chat_ui_dir = Path(__file__).parent / "chat_ui"
    template = (chat_ui_dir / "template.html").read_text(encoding="utf-8")

    # Load JS modules in order (they share a single global scope)
    _JS_MODULES = [
        "i18n.js", "state.js", "conversations.js", "messages.js",
        "active_agents.js", "typing.js", "sse.js",
        "dialogs.js", "commands.js", "context_editor.js", "memories.js",
        "secrets.js", "files_panel.js", "plans_panel.js", "attachments.js",
        "resources.js", "services.js", "file_viewer.js", "file_explorer.js",
    ]
    js_parts = []
    for mod in _JS_MODULES:
        mod_path = chat_ui_dir / mod
        if mod_path.exists():
            js_parts.append(f"// ── {mod} ──\n" + mod_path.read_text(encoding="utf-8"))
    js = "\n".join(js_parts)

    # Inject JS into the template placeholder
    html = template.replace("/* JS_PLACEHOLDER */", js)
    _cached_html = html
    logger.info("Chat UI loaded: %d chars (%d template + %d JS from %d modules)",
                len(html), len(template), len(js), len(js_parts))
    return html


class ServeChatUITask(BaseTask):
    """Serve a self-contained chat HTML interface."""

    TYPE = "serveChatUI"
    VERSION = "1.0.0"
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
                "description": "Custom CSS to append to the chat UI for theming",
            },
            "custom_css_file": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Path to a CSS file to append to the chat UI",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        agent_path = self.config.get("agent_path", "/api/agent")
        login_url = self.config.get("login_url", "")
        sse_path = self.config.get("sse_path", "/api/agent/events")
        html = _load_chat_html()
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
