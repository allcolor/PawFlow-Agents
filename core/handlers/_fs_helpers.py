"""Filesystem service-lookup, glob, and output-capping helpers.

Free functions and shared constants extracted from _fs_base.py to keep each
module <=800 lines. _fs_base re-exports every name here, so the public import
path (core.handlers._fs_base) stays unchanged for all importers.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Binary/base64 content cap — wastes context tokens
_BINARY_CAP = 2000

_FS_ALIASES = frozenset({"filestore", "store", "server"})
_FS_TYPES = ("relay", "filesystem", "googleDrive", "oneDrive")


def _expand_glob_braces(pattern: str, max_patterns: int = 256) -> List[str]:
    """Expand shell-style glob braces for Python glob/fnmatch callers."""
    def _split_options(body: str) -> List[str]:
        parts = []
        start = 0
        depth = 0
        for idx, ch in enumerate(body):
            if ch == "{":
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(body[start:idx])
                start = idx + 1
        parts.append(body[start:])
        return parts

    def _expand_one(value: str) -> List[str]:
        start = value.find("{")
        if start < 0:
            return [value]
        depth = 0
        end = -1
        for idx in range(start, len(value)):
            ch = value[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break
        if end < 0:
            return [value]
        prefix = value[:start]
        suffix = value[end + 1:]
        expanded = []
        for option in _split_options(value[start + 1:end]):
            for tail in _expand_one(suffix):
                expanded.append(prefix + option + tail)
                if len(expanded) >= max_patterns:
                    return expanded
        return expanded

    return _expand_one(pattern or "*")[:max_patterns]


def find_fs_service(user_id: str, service_name: str = "", conversation_id: str = ""):
    """Standalone service lookup (for non-handler code like HTTP actions).

    Walks conv > user > global scope chain via ServiceRegistry.
    Returns the live service instance or None.
    """
    def _set_uid(svc):
        if hasattr(svc, 'set_user_id') and user_id:
            svc.set_user_id(user_id)
        return svc

    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        if conversation_id:
            try:
                from core.relay_bindings import get_default, get_linked
                linked = get_linked(conversation_id)
                default_id = get_default(conversation_id) or ""
            except Exception:
                linked = []
                default_id = ""
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if service_name:
                linked_by_lower = {str(s).lower(): s for s in linked}
                if service_name not in linked:
                    service_name = linked_by_lower.get(service_name.lower(), "")
                if not service_name:
                    return None
            elif default_id and default_id in linked:
                service_name = default_id
            elif len(linked) == 1:
                service_name = linked[0]
            else:
                return None
        if service_name:
            svc = reg.resolve(service_name, user_id=user_id, conv_id=conversation_id)
            if svc:
                return _set_uid(svc)
        else:
            for fs_type in _FS_TYPES:
                for sdef in reg.resolve_by_type(fs_type, user_id=user_id, conv_id=conversation_id):
                    svc = reg.resolve(sdef.service_id, user_id=user_id, conv_id=conversation_id)
                    if svc:
                        return _set_uid(svc)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    return None


def get_tool_relay_env() -> Dict[str, str]:
    """Get PawFlow SDK environment variables for scripts running in relay/Docker.

    Returns dict with PAWFLOW_TOOL_RELAY_URL, PAWFLOW_TOOL_RELAY_TOKEN, etc.
    Empty dict if no tool relay is available.
    """
    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        for sid, sdef in reg.get_all("global", "").items():
            if getattr(sdef, "service_type", "") != "toolRelay":
                continue
            svc = reg.get_live_instance("global", "", sid)
            if svc:
                cfg = getattr(sdef, "config", {}) or {}
                port = int(cfg.get("port", 0))
                token = cfg.get("token", "")
                if port and token:
                    return {
                        "PAWFLOW_TOOL_RELAY_URL": f"ws://host.docker.internal:{port}/ws/tools",
                        "PAWFLOW_TOOL_RELAY_TOKEN": token,
                    }
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return {}


def cap_binary_output(text: str, cap: int = _BINARY_CAP) -> str:
    """Reduce cap for output that looks like binary or base64 data."""
    if not text or len(text) < cap:
        return text
    _b64_ratio = sum(
        1 for c in text[:2000]
        if c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/='
    ) / min(len(text), 2000)
    if _b64_ratio > 0.85:
        return text[:cap] + f"\n\n... [base64/binary data truncated — {len(text)} chars total]"
    if 'data:' in text[:200] and ';base64,' in text[:200]:
        return text[:cap] + f"\n\n... [data URI truncated — {len(text)} chars total]"
    return text


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


