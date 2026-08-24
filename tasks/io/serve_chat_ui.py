"""ServeChatUI Task — Serve the chat HTML page.

The page is rendered from the Jinja2 template tree under
``tasks/io/chat_ui/templates/`` (see docs/CHAT_UI_TEMPLATES.md): ``chat.html``
is the skeleton, it includes the region partials and exposes the extension
points. JS modules are served separately by serveAssets via /chat/js/{path};
CSS modules (``_CSS_MODULES``) the same way under /chat/js/css/{file}.

Flow pattern:
    httpReceiver (GET /chat)           → serveChatUI  → handleHTTPResponse
    httpReceiver (GET /chat/js/{path}) → serveAssets   → handleHTTPResponse
"""

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import unquote

from jinja2 import Environment, FileSystemLoader, StrictUndefined, pass_context
from markupsafe import escape

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)

_CHAT_UI_DIR = Path(__file__).parent / "chat_ui"
_TEMPLATES_DIR = _CHAT_UI_DIR / "templates"
_CSS_DIR = _CHAT_UI_DIR / "css"

# JS modules in load order (each file must be standalone)
# ext_runtime.js must load early so other modules can fire hooks safely.
_JS_MODULES = [
    "i18n.js", "state.js", "rxbus.js", "semantic_runtime.js", "ext_runtime.js",
    "tooltips.js",
    "themes.js", "appearance.js", "search.js",
    # conversations.js = list/state/render/history core (loads early);
    # _io = delete/export/import; _menu = context menu + git dialogs;
    # _share = shared/invite sidebar sections + share dialog. _share loads
    # before the core because renderConvList calls into it on every render.
    # (escapeHtml is canonical in state.js, loaded earlier.)
    "conversations_share.js", "conversations.js", "conversations_io.js",
    "conversations_menu.js",
    # messages.js = tool-summary/badges/technical-grouping core; _render = addMsg;
    # _tools = tool-output/diff/escape/media; _markdown = markdown/traces/scroll.
    # Order matters: core → render → tools → markdown (markdown holds load-time
    # #messages scroll listeners).
    "messages.js", "messages_render.js", "messages_tools.js", "messages_markdown.js",
    "turn_view.js",
    # OpenSpace is split by responsibility (all files stay <=800 lines).
    # The files share classic-script globals; order is therefore significant.
    # three.js itself remains a lazy dynamic import from the core module.
    "openspace.js", "openspace_environment.js", "openspace_scene.js", "openspace_room.js",
    "openspace_flow.js", "openspace_agents.js", "openspace_runtime.js",
    "openspace_dialogs.js",
    "active_agents.js", "task_tabs.js", "usage_cost.js", "usage_dashboard.js", "typing.js", "notifications.js",
    # sse.js was split (<=800 lines each); load order matters: sse_state.js
    # (globals + per-connection state + shared helpers) before the wire
    # files, then sse.js (connectSSE resets state + calls _sseWireA/B).
    "sse_state.js", "sse_handlers_a.js", "sse_handlers_b.js", "sse.js",
    "dialogs.js",
    "admin_settings.js",
    # commands_help.js (HELP_DATA) before the cmd_* group so /help's data
    # exists before any command handler that reads it (define-before-use).
    "commands_help.js",
    "cmd_agent.js", "cmd_context.js", "cmd_resources.js", "cmd_conversation.js", "cmd_misc.js",
    "commands.js", "file_mention.js", "context_editor.js", "cognitive_panel_helpers.js",
    "memories.js", "diary.js", "todos.js", "knowledge_graph.js", "project_graph.js", "project_wiki.js", "scratchpad.js", "scratchdir.js",
    "secrets.js", "files_panel.js", "plans_panel.js", "confirmations_panel.js", "attachments.js",
    # resources.js was split into smaller modules (<=800 lines each); load
    # order is significant — resources.js (core: shared helpers + collapsed
    # state, runs top-level init) MUST stay first, the rest follow.
    "resources.js", "resources_pfp.js", "resources_flow_templates.js",
    "resources_render.js", "service_tunnels.js", "resources_mcp_publish.js", "resources_a2a.js",
    "workflow_run_inspector.js", "resources_menus.js",
    "resources_flow_dialogs.js",
    "resources_resource_dialogs.js", "resources_create_dialogs.js",
    # schema_form.js = the single schema-driven form renderer (services, Flow
    # Editor properties, flow parameters); must precede its first caller.
    "schema_form.js", "workflow_agent_forms.js",
    "resources_service_dialogs.js", "resources_service_login.js",
    "resources_service_templates.js",
    "services.js", "file_viewer.js", "file_explorer.js",
    "tabs.js",
    # terminal.js = xterm engine; terminal_commands.js = /terminal,/code,
    # /desktop,/audio,/port-forward,/vm + agent-tmux handlers (load right after).
    # grab.js reuses terminal.js helpers (_terminalInputB64,
    # _estimateTerminalSize, _agentLlmProvider) — must load after it.
    "terminal.js", "terminal_commands.js", "grab.js",
    "audio.js",
    "conversation_tts.js",
    "conversation_stt.js",
    "conversation_voice.js",
    # LiveKit engine live sessions — must load after conversation_voice.js
    # (reuses its overlay helpers) and before first use via the mic button.
    "conversation_livekit.js",
]

# CSS modules in cascade order (the NN_ prefix mirrors it), emitted by
# head/styles.html as <link rel="stylesheet" href="/chat/js/css/<file>?v=...">
# before the operator custom CSS and the theme block. The order is the one
# the former inline <style> block had: mobile overrides and the theme
# variable bridge rely on coming after the rules they override.
_CSS_MODULES: Tuple[str, ...] = (
    "00_base.css",            # reset, :root, app layout, sidebar, sharing
    "10_chrome.css",          # collapsible grips, header status widgets
    "20_messages.css",        # gauges, messages, simplified live view, send
    "30_mobile.css",          # narrow-viewport overrides
    "40_delegates.css",       # delegate blocks, cancel, ask_parent
    "50_composer.css",        # composer drawer, cognitive panel chrome
    "55_appearance.css",      # user zoom, background media, atmosphere dialog
    "58_modern_ui.css",       # search, composer shortcuts, code and memory cards
    "60_openspace.css",       # OpenSpace 3D view
    "70_grab.css",            # terminal grab mode
    "75_composer_shell.css",  # unified prompt surface and slash/mention picker
    "76_composer_agent.css",  # selected-agent overlay and quick selector
    "80_dialogs.css",         # exec approval + generic dialogs
    "85_terminal_files.css",  # terminal output, file explorer
    "90_tabs.css",            # tab bar, tab panels, desktop/audio tabs
    "95_action_dock.css",     # action menu + conversation dock
    "99_theme_bridge.css",    # --pf-* variable bridge (must stay last)
)

# One environment per process. auto_reload re-reads a partial whose mtime
# changed (hotpatch workflow); autoescape + StrictUndefined: the template
# marks the server-built blocks |safe explicitly, a missing context key is
# an error. trim/lstrip keep block tags from leaving blank lines;
# keep_trailing_newline keeps each partial's final newline so an include
# never glues its last line to the next line of the skeleton.
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=True,
    undefined=StrictUndefined,
    auto_reload=True,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)

_preload_started = False
_preload_lock = threading.Lock()
_i18n_block_cache: Tuple[tuple, str] = ((), "")
_i18n_block_lock = threading.Lock()
# Server-rendered PFP template fragments, keyed by (package, sha256): the
# file is read and digest-checked once per installed version.
_fragment_cache: Dict[Tuple[str, str], str] = {}
_fragment_lock = threading.Lock()
_fragment_warned: set = set()
# Mirrors core.pfp_package._UI_TEMPLATE_MAX_BYTES without importing the
# package module on every page (the install validator is the authority).
_TEMPLATE_FRAGMENT_MAX_BYTES = 64 * 1024


@pass_context
def _ext_fragments(context, slot: str) -> str:
    """HTML of the enabled packages' fragments for ``slot`` (use with |safe)."""
    slots = context.get("template_slots") or {}
    return "".join(slots.get(slot) or [])


@pass_context
def _ext_hidden(context, slot: str) -> str:
    """`` hidden`` for a conditional host that received no fragment."""
    slots = context.get("template_slots") or {}
    return "" if slots.get(slot) else " hidden"


_env.globals["ext_fragments"] = _ext_fragments
_env.globals["ext_hidden"] = _ext_hidden


def _stat_items(base: Path, pattern: str, prefix: str) -> list:
    items = []
    if not base.exists():
        return items
    for p in sorted(base.glob(pattern)):
        if not p.is_file():
            continue
        st = p.stat()
        items.append((prefix + p.relative_to(base).as_posix(), st.st_mtime_ns, st.st_size))
    return items


def _asset_signature():
    """mtime/size of everything that shapes the served page.

    Templates and CSS modules are included so that editing any partial or
    stylesheet changes the ``?v=`` asset version (browser cache busting); the
    Jinja environment re-reads changed templates on its own.
    """
    items = _stat_items(_TEMPLATES_DIR, "**/*.html", "templates/")
    items += _stat_items(_CSS_DIR, "*.css", "css/")
    for mod in _JS_MODULES:
        p = _CHAT_UI_DIR / mod
        try:
            st = p.stat()
            items.append((mod, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            items.append((mod, 0, 0))
    items += _i18n_signature()
    return tuple(items)


def _i18n_signature() -> list:
    return _stat_items(_CHAT_UI_DIR / "i18n", "*.json", "i18n/")


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
    """Embed boot i18n catalogs so the UI does not depend on nested JSON assets.

    Serialising the three catalogs is the only costly part of a render; the
    block is cached per i18n file signature.
    """
    global _i18n_block_cache
    sig = tuple(_i18n_signature())
    with _i18n_block_lock:
        cached_sig, cached_html = _i18n_block_cache
        if cached_html and cached_sig == sig:
            return cached_html
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
    html = (
        "<script>window.PAWFLOW_I18N_LANGUAGES="
        + json.dumps(languages, ensure_ascii=False)
        + ";window.PAWFLOW_I18N_CATALOGS="
        + json.dumps(catalogs, ensure_ascii=False)
        + ";</script>\n"
    )
    with _i18n_block_lock:
        _i18n_block_cache = (sig, html)
    return html


def _enabled_ui_extension_records(user_id: str = "", conversation_id: str = "") -> List[Dict[str, Any]]:
    """Installed ``ui.v1`` extension records this page may use.

    One gate for the boot manifest and the server-rendered fragments: a
    session user, install records present, the global kill switch, the
    ``ui.v1`` contract and the per-conversation toggle.
    """
    if not user_id:
        return []
    if not _has_pfp_install_records(user_id, conversation_id):
        return []
    try:
        from core.pfp_package import list_installed_ui_extensions, _UI_API_VERSION
        from core.tool_mcp_filters import (
            _ui_extensions_globally_disabled, is_extension_enabled,
        )
        if _ui_extensions_globally_disabled():
            return []
        scope = "conversation" if conversation_id else "user"
        records = list_installed_ui_extensions(
            user_id=user_id, conversation_id=conversation_id, scope=scope)
    except Exception:
        logger.debug("PFP UI extensions lookup failed", exc_info=True)
        return []
    out = []
    for rec in records:
        if rec.get("version_compat") != _UI_API_VERSION:
            continue
        # Per-conversation toggle: drop extensions the user disabled in this
        # conversation. The kill switch was already handled above.
        if conversation_id and not is_extension_enabled(
                conversation_id, str(rec.get("package") or "")):
            continue
        out.append(rec)
    return out


def _initial_extensions_block(user_id: str = "", conversation_id: str = "",
                              records: Optional[List[Dict[str, Any]]] = None) -> str:
    """Bootstrap manifest for installed UI extensions.

    Each entry carries `package`, `version`, `slots`, `hooks`, `i18n`,
    `templates` (the server-rendered fragments, as ``{slot, path}``) and a
    list of `assets` with public URLs already shaped as
    `/chat/ext/<package>/<short_hash>/<file>` so the browser-side runtime
    can `import()` them with an immutable cache key. Template fragments have
    no URL: they are inserted into the page by :func:`_template_fragments`.
    Only `ui.v1`-compatible packages are emitted here; mismatched packages
    are silently dropped at serve time and logged once on install (where the
    user can still see them in the install plan).
    """
    context = {"user": user_id, "conversation": conversation_id}
    if records is None:
        records = _enabled_ui_extension_records(user_id, conversation_id)
    out = []
    for rec in records:
        package = rec.get("package") or ""
        assets = []
        templates = []
        for asset in rec.get("assets") or []:
            digest = str(asset.get("sha256") or "").replace("sha256:", "")
            if not digest:
                continue
            if asset.get("kind") == "template":
                templates.append({"slot": asset.get("slot", ""), "path": asset.get("path", "")})
                continue
            short = digest[:16]
            url = f"/chat/ext/{package}/{short}/{asset['path']}"
            assets.append({
                "kind": asset.get("kind", ""),
                "id": asset.get("id", ""),
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
            "templates": templates,
            "slots": rec.get("slots", []),
            "hooks": rec.get("hooks", []),
            "i18n": rec.get("i18n", {}),
        })
    context_json = json.dumps(context, ensure_ascii=False).replace("<", "\\u003c")
    entries_json = json.dumps(out, ensure_ascii=False).replace("<", "\\u003c")
    return (
        "<script>window.PAWFLOW_EXTENSION_CONTEXT=" + context_json
        + ";window.PAWFLOW_EXTENSIONS=" + entries_json + ";</script>\n")


def _warn_fragment_once(key: Tuple[str, str], message: str) -> None:
    with _fragment_lock:
        if key in _fragment_warned:
            return
        _fragment_warned.add(key)
    logger.warning("PFP template fragment skipped (%s/%s): %s", key[0], key[1], message)


def _read_fragment(package: str, content_dir: str, asset: Dict[str, Any]) -> str:
    """The fragment text, or "" when it must be skipped (never breaks the page).

    Containment, size and SHA-256 are checked against the signed install
    record, exactly like the asset server does for URL assets.
    """
    path = str(asset.get("path") or "")
    expected = str(asset.get("sha256") or "").lower().replace("sha256:", "")
    key = (package, expected or path)
    with _fragment_lock:
        cached = _fragment_cache.get(key)
    if cached is not None:
        return cached
    if not expected:
        _warn_fragment_once(key, "install record carries no digest")
        return ""
    root = Path(content_dir or "").resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        _warn_fragment_once(key, "fragment escapes the content directory")
        return ""
    try:
        if target.stat().st_size > _TEMPLATE_FRAGMENT_MAX_BYTES:
            _warn_fragment_once(key, "fragment larger than the template limit")
            return ""
        data = target.read_bytes()
    except OSError as exc:
        _warn_fragment_once(key, f"cannot read fragment: {exc}")
        return ""
    if hashlib.sha256(data).hexdigest() != expected:
        _warn_fragment_once(key, "fragment digest does not match the install record")
        return ""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        _warn_fragment_once(key, "fragment is not UTF-8")
        return ""
    with _fragment_lock:
        _fragment_cache[key] = text
    return text


def _template_fragments(records: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """``{slot: [wrapped fragment html, ...]}`` for the enabled packages.

    The text is inserted verbatim (never compiled by Jinja). DOM slots wrap
    it in ``<div data-pf-ext="<package>" data-pf-template-slot="<slot>">`` so
    the runtime can find and tear it down; ``head`` fragments are bracketed
    by comments instead (a div is not valid inside <head>).
    """
    slots: Dict[str, List[str]] = {}
    for rec in records:
        package = str(rec.get("package") or "")
        content_dir = str(rec.get("content_dir") or "")
        for asset in rec.get("assets") or []:
            if asset.get("kind") != "template":
                continue
            slot = str(asset.get("slot") or "")
            if not slot:
                continue
            text = _read_fragment(package, content_dir, asset)
            if not text:
                continue
            pkg_attr = str(escape(package))
            if slot == "head":
                # A <div> is not valid inside <head>: bracket with comments.
                wrapped = (f"<!-- pf-ext:{pkg_attr}:head -->\n{text}\n"
                           f"<!-- /pf-ext:{pkg_attr} -->\n")
            else:
                wrapped = (f'<div data-pf-ext="{pkg_attr}" data-pf-template-slot="{escape(slot)}">'
                           f"{text}</div>")
                # The two page-level points sit between block elements, so
                # they close their own line; a DOM slot host stays inline.
                if slot == "body_end":
                    wrapped += "\n"
            slots.setdefault(slot, []).append(wrapped)
    return slots


def _compute_js_version(sig=None) -> str:
    """Short hash of chat asset metadata for boot cache busting."""
    h = hashlib.md5(usedforsecurity=False)
    for item in sig or _asset_signature():
        h.update(repr(item).encode("utf-8"))
    return h.hexdigest()[:8]


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


def render_chat_page(*, agent_path: str = "/api/agent",
                     sse_path: str = "/api/agent/events", login_url: str = "",
                     theme_block: str = "", extensions_block: Optional[str] = None,
                     template_slots: Optional[Dict[str, List[str]]] = None,
                     custom_css: str = "") -> str:
    """Render ``templates/chat.html`` for one request.

    ``theme_block``, ``extensions_block`` (the empty boot manifest when
    omitted) and the i18n block are HTML built by this module that the
    template inserts with ``|safe``; ``template_slots`` holds the PFP
    fragments per slot (``ext_fragments()`` / ``ext_hidden()`` globals);
    ``custom_css`` is operator configuration inserted in its own ``<style>``;
    every other value is autoescaped by the template (paths go through
    ``tojson`` in scripts).
    """
    sig = _asset_signature()
    if extensions_block is None:
        extensions_block = _initial_extensions_block(records=[])
    return _env.get_template("chat.html").render(
        asset_version=_compute_js_version(sig),
        js_modules=[mod for mod in _JS_MODULES if (_CHAT_UI_DIR / mod).exists()],
        css_modules=list(_CSS_MODULES),
        i18n_block=_initial_i18n_block(),
        theme_block=theme_block or "",
        extensions_block=extensions_block,
        template_slots=template_slots or {},
        agent_path=agent_path,
        sse_path=sse_path,
        login_url=login_url,
        custom_css=_safe_style_text(custom_css),
    )


def _start_preload_once() -> None:
    """Compile the template tree and the i18n block off the init path."""
    global _preload_started
    with _preload_lock:
        if _preload_started:
            return
        _preload_started = True

    def _preload() -> None:
        try:
            _env.get_template("chat.html")
            _initial_i18n_block()
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
        _start_preload_once()

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
        user_id = (flowfile.get_attribute("http.auth.principal") or "").strip()
        conversation_id = (flowfile.get_attribute("http.cookie.pawflow_conv") or "").strip()

        custom_css = self.config.get("custom_css", "")
        custom_css_file = self.config.get("custom_css_file", "")
        if custom_css_file:
            try:
                css_path = Path(custom_css_file)
                if css_path.is_file():
                    custom_css += "\n" + css_path.read_text(encoding="utf-8")
            except Exception:
                logger.debug("Ignored exception", exc_info=True)

        records = _enabled_ui_extension_records(user_id, conversation_id)
        html = render_chat_page(
            agent_path=self.config.get("agent_path", "/api/agent"),
            sse_path=self.config.get("sse_path", "/api/agent/events"),
            login_url=self.config.get("login_url", ""),
            theme_block=_initial_theme_block(flowfile),
            extensions_block=_initial_extensions_block(user_id, conversation_id, records=records),
            template_slots=_template_fragments(records),
            custom_css=custom_css,
        )

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
