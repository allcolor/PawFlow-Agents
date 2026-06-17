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
import re
import threading
import time
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
    # resources.js was split into smaller modules (<=800 lines each); load
    # order is significant — resources.js (core: shared helpers + collapsed
    # state, runs top-level init) MUST stay first, the rest follow.
    "resources.js", "resources_pfp.js", "resources_flow_templates.js",
    "resources_render.js", "resources_menus.js", "resources_flow_dialogs.js",
    "resources_resource_dialogs.js", "resources_create_dialogs.js",
    "resources_service_dialogs.js", "resources_service_login.js",
    "services.js", "file_viewer.js", "file_explorer.js",
    "tabs.js",
    "terminal.js",
    "audio.js",
    "conversation_tts.js",
    "conversation_stt.js",
]

_html_cache: str = ""
_html_cache_sig = None
_html_cache_version = ""
_html_cache_lock = threading.Lock()
_html_preload_started = False
_html_cache_checked_at = 0.0
_HTML_SIG_CHECK_INTERVAL = 5.0


def _asset_signature():
    items = []
    for mod in _JS_MODULES:
        p = _CHAT_UI_DIR / mod
        try:
            st = p.stat()
            items.append((mod, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            items.append((mod, 0, 0))
    i18n_dir = _CHAT_UI_DIR / "i18n"
    try:
        i18n_paths = sorted(i18n_dir.glob("*.json"))
    except Exception:
        i18n_paths = []
    for p in i18n_paths:
        try:
            st = p.stat()
            items.append(("i18n/" + p.name, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            items.append(("i18n/" + p.name, 0, 0))
    return tuple(items)


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


def _initial_extensions_block(user_id: str = "", conversation_id: str = "") -> str:
    """Bootstrap manifest for installed UI extensions.

    Each entry carries `package`, `version`, `slots`, `hooks`, `i18n`, and a
    list of `assets` with public URLs already shaped as
    `/chat/ext/<package>/<short_hash>/<file>` so the browser-side runtime
    can `import()` them with an immutable cache key. Only `ui.v1`-compatible
    packages are emitted here; mismatched packages are silently dropped at
    serve time and logged once on install (where the user can still see them
    in the install plan).
    """
    if not user_id:
        return "<script>window.PAWFLOW_EXTENSIONS=[];</script>\n"
    if not _has_pfp_install_records(user_id, conversation_id):
        return "<script>window.PAWFLOW_EXTENSIONS=[];</script>\n"
    try:
        from core.pfp_package import list_installed_ui_extensions, _UI_API_VERSION
        from core.tool_mcp_filters import (
            _ui_extensions_globally_disabled, is_extension_enabled,
        )
        if _ui_extensions_globally_disabled():
            return "<script>window.PAWFLOW_EXTENSIONS=[];</script>\n"
        scope = "conversation" if conversation_id else "user"
        records = list_installed_ui_extensions(
            user_id=user_id, conversation_id=conversation_id, scope=scope)
    except Exception:
        logger.debug("PFP UI extensions lookup failed", exc_info=True)
        return "<script>window.PAWFLOW_EXTENSIONS=[];</script>\n"
    out = []
    for rec in records:
        if rec.get("version_compat") != _UI_API_VERSION:
            continue
        # Per-conversation toggle: drop extensions the user disabled in this
        # conversation. The kill switch was already handled above.
        if conversation_id and not is_extension_enabled(
                conversation_id, str(rec.get("package") or "")):
            continue
        package = rec.get("package") or ""
        assets = []
        for asset in rec.get("assets") or []:
            digest = str(asset.get("sha256") or "").replace("sha256:", "")
            if not digest:
                continue
            short = digest[:16]
            url = f"/chat/ext/{package}/{short}/{asset['path']}"
            assets.append({
                "kind": asset.get("kind", ""),
                "url": url,
                "path": asset.get("path", ""),
                "size": int(asset.get("size", 0) or 0),
                "sha256": asset.get("sha256", ""),
                "lang": asset.get("lang", ""),
            })
        out.append({
            "package": package,
            "version": rec.get("version", ""),
            "scope": rec.get("scope", ""),
            "version_compat": rec.get("version_compat", ""),
            "assets": assets,
            "slots": rec.get("slots", []),
            "hooks": rec.get("hooks", []),
            "i18n": rec.get("i18n", {}),
        })
    return (
        "<script>window.PAWFLOW_EXTENSIONS="
        + json.dumps(out, ensure_ascii=False)
        + ";</script>\n"
    )


def _compute_js_version(sig=None) -> str:
    """Short hash of chat asset metadata for boot cache busting."""
    h = hashlib.md5(usedforsecurity=False)
    for item in sig or _asset_signature():
        h.update(repr(item).encode("utf-8"))
    return h.hexdigest()[:8]


_EXTENSIONS_PLACEHOLDER = "<!--__PAWFLOW_EXTENSIONS_PLACEHOLDER__-->"


def _safe_package_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]", "_", str(value or "")) or "default"


def _has_pfp_install_records(user_id: str, conversation_id: str = "") -> bool:
    """Cheap pre-check before importing the heavier PFP package module."""
    try:
        from core.paths import REPOSITORY_DIR
        root = REPOSITORY_DIR / "packages"
        user_root = root / "users" / _safe_package_component(user_id)
        if user_root.exists() and any(user_root.glob("*.json")):
            return True
        if conversation_id:
            conv_root = (root / "conversations" / _safe_package_component(user_id)
                         / _safe_package_component(conversation_id))
            if conv_root.exists() and any(conv_root.glob("*.json")):
                return True
    except Exception:
        logger.debug("PFP install record fast check failed", exc_info=True)
        return True
    return False


def _load_html() -> str:
    global _html_cache, _html_cache_sig, _html_cache_version, _html_cache_checked_at
    with _html_cache_lock:
        now = time.monotonic()
        if _html_cache and now - _html_cache_checked_at < _HTML_SIG_CHECK_INTERVAL:
            return _html_cache
        sig = _asset_signature()
        if _html_cache and _html_cache_sig == sig:
            _html_cache_checked_at = now
            return _html_cache

        v = _compute_js_version(sig)
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
            + _EXTENSIONS_PLACEHOLDER
            + "\n".join(script_tags)
        )

        # Replace placeholder with script tags
        html = template.replace("/* JS_PLACEHOLDER */", "")
        html = html.replace("</body>", f"{scripts_html}\n</body>")

        _html_cache = html
        _html_cache_sig = sig
        _html_cache_version = v
        _html_cache_checked_at = now
        logger.info("Chat UI loaded: %d chars template, %d JS modules, version=%s",
                    len(template), len(_JS_MODULES), v)
        return html


def _start_html_preload_once() -> None:
    global _html_preload_started
    with _html_cache_lock:
        if _html_preload_started or _html_cache:
            return
        _html_preload_started = True

    def _preload() -> None:
        try:
            _load_html()
        except Exception:
            logger.debug("Chat UI preload failed", exc_info=True)

    # Defer the real work until the executor has finished its init phase. A
    # plain thread can still contend with startup under the GIL and make the
    # task initialize timing look slow even though initialize() does not join.
    timer = threading.Timer(0.2, _preload)
    timer.daemon = True
    timer.name = "chat-ui-preload"
    timer.start()


class ServeChatUITask(BaseTask):
    """Serve the chat HTML page."""

    TYPE = "serveChatUI"
    VERSION = "2.0.0"
    NAME = "Serve Chat UI"
    DESCRIPTION = "Serve an HTML chat interface for the agent"
    ICON = "chat"

    def initialize(self):
        _start_html_preload_once()

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
        user_id = (flowfile.get_attribute("http.auth.principal") or "").strip()
        conversation_id = (flowfile.get_attribute("http.cookie.pawflow_conv") or "").strip()
        ext_block = _initial_extensions_block(user_id, conversation_id)
        html = html.replace(_EXTENSIONS_PLACEHOLDER, ext_block, 1)
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
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
