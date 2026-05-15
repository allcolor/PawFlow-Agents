"""ServeChatUI Task — Serve the chat HTML page.

The HTML template is in tasks/io/chat_ui/template.html.
JS modules are served separately by serveAssets task via /chat/js/{path}.

Flow pattern:
    httpReceiver (GET /chat)           → serveChatUI  → handleHTTPResponse
    httpReceiver (GET /chat/js/{path}) → serveAssets   → handleHTTPResponse
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, Any, List
from urllib.parse import unquote

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_CHAT_UI_DIR = Path(__file__).parent / "chat_ui"

# JS modules in load order (each file must be standalone)
# ext_runtime.js must load early so other modules can fire hooks safely.
_JS_MODULES = [
    "i18n.js", "state.js", "rxbus.js", "ext_runtime.js",
    "themes.js", "conversations.js", "messages.js",
    "active_agents.js", "typing.js", "notifications.js", "sse.js",
    "dialogs.js",
    "admin_settings.js",
    "cmd_agent.js", "cmd_context.js", "cmd_resources.js", "cmd_conversation.js", "cmd_misc.js",
    "commands.js", "file_mention.js", "context_editor.js", "memories.js", "diary.js", "knowledge_graph.js", "project_graph.js",
    "secrets.js", "files_panel.js", "plans_panel.js", "attachments.js",
    "resources.js", "services.js", "file_viewer.js", "file_explorer.js",
    "tabs.js",
    "terminal.js",
    "audio.js",
]

_html_cache: str = ""


def _cookie_value(cookie_header: str, name: str) -> str:
    for part in (cookie_header or "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return unquote(value)
    return ""


def _safe_style_text(css: str) -> str:
    return (css or "").replace("</style", "<\\/style")


def _initial_theme_block(flowfile: FlowFile) -> str:
    cookie_header = flowfile.get_attribute("http.header.cookie") or ""
    theme_ref = _cookie_value(cookie_header, "pawflow_theme_ref") or "global:pawflow_dark"
    if theme_ref.startswith("builtin:"):
        theme_ref = "global:" + theme_ref.split(":", 1)[1]
    user_id = flowfile.get_attribute("auth.user_id") or "__global__"
    try:
        from core.chat_themes import resolve_theme
        theme = resolve_theme(theme_ref, user_id=user_id, conversation_id="")
        if not theme and theme_ref != "global:pawflow_dark":
            theme_ref = "global:pawflow_dark"
            theme = resolve_theme(theme_ref, user_id=user_id, conversation_id="")
        css = _safe_style_text((theme or {}).get("css", ""))
    except Exception:
        css = ""
    if not css:
        return ""
    return (
        "<style id=\"custom-theme\">\n"
        + css
        + "\n</style>\n"
        + "<script>window.PAWFLOW_INITIAL_THEME_REF="
        + json.dumps(theme_ref)
        + ";</script>\n"
    )


def _initial_i18n_block() -> str:
    """Embed boot i18n catalogs so the UI does not depend on nested JSON assets."""
    i18n_dir = _CHAT_UI_DIR / "i18n"
    languages = []
    catalogs = {}
    try:
        languages = json.loads((i18n_dir / "languages.json").read_text(encoding="utf-8"))
    except Exception:
        languages = [{"code": "en", "label": "English", "native_label": "English"}]
    for code in ("en", "fr", "es"):
        try:
            catalogs[code] = json.loads((i18n_dir / f"{code}.json").read_text(encoding="utf-8"))
        except Exception:
            catalogs[code] = {}
    return (
        "<script>window.PAWFLOW_I18N_LANGUAGES="
        + json.dumps(languages, ensure_ascii=False)
        + ";window.PAWFLOW_I18N_CATALOGS="
        + json.dumps(catalogs, ensure_ascii=False)
        + ";</script>\n"
    )


def _initial_extensions_block() -> str:
    """Bootstrap manifest for installed UI extensions.

    Phase 1 ships the runtime without PFP integration; the bootstrap is
    always empty here. PFP install (phase 2) will populate this list with
    the asset manifest of each installed `ui_extension` object.
    """
    return "<script>window.PAWFLOW_EXTENSIONS=[];</script>\n"


def _compute_js_version() -> str:
    """Short hash of all chat assets that affect boot-time rendering."""
    h = hashlib.md5()
    for mod in _JS_MODULES:
        p = _CHAT_UI_DIR / mod
        if p.exists():
            h.update(p.read_bytes())
    i18n_dir = _CHAT_UI_DIR / "i18n"
    if i18n_dir.exists():
        for p in sorted(i18n_dir.glob("*.json")):
            h.update(p.read_bytes())
    return h.hexdigest()[:8]


def _load_html() -> str:
    global _html_cache
    if _html_cache:
        return _html_cache

    v = _compute_js_version()
    template = (_CHAT_UI_DIR / "template.html").read_text(encoding="utf-8")

    # Build <script defer> tags — all load in parallel, execute in order,
    # only AFTER HTML is fully parsed (no HTTP slot contention)
    script_tags = []
    for mod in _JS_MODULES:
        if (_CHAT_UI_DIR / mod).exists():
            script_tags.append(f'<script defer src="/chat/js/{mod}?v={v}"></script>')
    scripts_html = (
        f'<script>window.PAWFLOW_ASSET_VERSION={json.dumps(v)};</script>\n'
        + _initial_i18n_block()
        + _initial_extensions_block()
        + "\n".join(script_tags)
    )

    # Replace placeholder with script tags
    html = template.replace("/* JS_PLACEHOLDER */", "")
    html = html.replace("</body>", f"{scripts_html}\n</body>")

    _html_cache = html
    logger.info("Chat UI loaded: %d chars template, %d JS modules, version=%s",
                len(template), len(_JS_MODULES), v)
    return html


class ServeChatUITask(BaseTask):
    """Serve the chat HTML page."""

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
                "description": "Path of the agent POST endpoint",
            },
            "login_url": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Login URL for OAuth2 redirect",
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
                "description": "Custom CSS to inject",
            },
            "custom_css_file": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Path to a CSS file to append",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        agent_path = self.config.get("agent_path", "/api/agent")
        login_url = self.config.get("login_url", "")
        sse_path = self.config.get("sse_path", "/api/agent/events")
        html = _load_html()
        initial_theme = _initial_theme_block(flowfile)
        if initial_theme:
            html = html.replace("</head>", initial_theme + "</head>", 1)
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
            html = html.replace("</style>",
                                f"\n/* Custom theme */\n{custom_css}\n</style>", 1)

        flowfile.set_content(html.encode("utf-8"))
        flowfile.set_attribute("http.response.status", "200")
        flowfile.set_attribute("http.response.header.Content-Type",
                               "text/html; charset=utf-8")
        flowfile.set_attribute("http.response.header.Cache-Control", "no-cache")
        # Enable SharedArrayBuffer for AudioWorklet zero-copy ring buffer.
        # Both parent AND iframes (noVNC) must send matching COOP/COEP.
        flowfile.set_attribute("http.response.header.Cross-Origin-Opener-Policy", "same-origin")
        flowfile.set_attribute("http.response.header.Cross-Origin-Embedder-Policy", "require-corp")
        return [flowfile]


TaskFactory.register(ServeChatUITask)
