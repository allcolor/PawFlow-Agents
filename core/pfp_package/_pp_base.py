"""PawFlow Package (.pfp) build, verify, inspect, install, and uninstall.

PFP packages are untrusted distribution artifacts. A .pfp is a zip containing
pfp.json, pfp.lock.json, signature.ed25519, and content files. The signature
covers canonical JSON for the manifest and lock, and the lock covers every
package file hash. Installing a package always goes through an install plan;
code-bearing tools/services/tasks execute only through the relay package runtime.
"""

from __future__ import annotations

import logging
import re





logger = logging.getLogger(__name__)


FORMAT_VERSION = "pawflow.package.v1"
LOCK_VERSION = "pawflow.package.lock.v1"
SIGNATURE_FILE = "signature.ed25519"
MANIFEST_FILE = "pfp.json"
LOCK_FILE = "pfp.lock.json"

_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/@:+-]+$")
_PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,120}[a-z0-9]$")
_RESOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_VERSION_REF_RE = re.compile(r"^[A-Za-z0-9._+*<>=!~,^ -]{1,80}$")
_RESERVED_SKILL_WORDS = ("anthropic", "claude")

_RESOURCE_TYPES = {
    "agent": "agent",
    "prompt": "prompt",
    "skill": "skill",
    "theme": "theme",
    "task": "task_def",
    "task_def": "task_def",
}
_INSTALLABLE_TYPES = set(_RESOURCE_TYPES) | {"flow", "service", "service_definition"}
_INSTALLABLE_TYPES.update({"tool", "agent_hook", "service_provider", "flow_task", "task_provider"})
_INSTALLABLE_TYPES.add("ui_extension")

_RUNTIME_OBJECT_TYPES = {"tool", "agent_hook", "service_provider", "flow_task", "task_provider"}
_SUPPORTED_RUNTIME_RUNNERS = {"python"}

# Slot and hook names accepted by the browser-side `ui.v1` contract.
# Adding a new slot / hook is additive; removing or renaming bumps to ui.v2
# and packages declaring `version_compat: "ui.v1"` must fail install.
_UI_API_VERSION = "ui.v1"
_UI_KNOWN_SLOTS = {
    "action_menu", "gear_menu", "resources_panel",
    "sidebar_top", "sidebar_bottom",
    "header_actions", "tab_bar",
}
_UI_KNOWN_HOOKS = {
    "boot", "shutdown",
    "conversation_changed", "conversation_created", "conversation_deleted",
    "message_appended", "message_streaming",
    "tool_call_started", "tool_call_completed",
    "command_submitted", "command_result",
    "before_send",
    "agent_changed", "theme_changed",
    "tab_switched", "permission_mode_changed",
    "sse_event",
}
# `.html` is intentionally absent: a same-origin HTML page served from
# `/chat/ext/...` could run inline <script> blocks under the user's session
# even though the runtime only auto-loads .js/.css. Extensions that need to
# build markup do it through DOM APIs (createElement + textContent) from
# their .js code.
_UI_ASSET_EXTENSIONS = {".js", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2"}


class PfpError(ValueError):
    """Raised for invalid, unsafe, or unsupported PFP operations."""


















































































































_UI_HANDLER_ACTION_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")




























































































































































