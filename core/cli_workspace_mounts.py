"""Workspace bind mounts for CLI provider containers.

PawFlow routes filesystem operations through MCP relay tools. These mounts are
a compatibility fallback for CLI providers that accidentally try local
filesystem tools despite the MCP-only prompt.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

_ENV_KEY = "PAWFLOW_CLI_WORKSPACE_MOUNT"
_VALID_MODES = {"off", "ro", "rw"}


def normalize_workspace_mount_mode(value: str = "") -> str:
    """Return a validated workspace mount mode.

    Empty values fall back to PAWFLOW_CLI_WORKSPACE_MOUNT and then to "rw".
    """
    raw = (value or os.environ.get(_ENV_KEY, "") or "rw").strip().lower()
    if raw not in _VALID_MODES:
        logger.warning(
            "Invalid %s=%r; using 'off'", _ENV_KEY, raw)
        return "off"
    return raw


def set_workspace_mount_mode(value: str) -> str:
    """Validate and store the process-wide CLI workspace mount mode."""
    mode = normalize_workspace_mount_mode(value)
    os.environ[_ENV_KEY] = mode
    return mode


def cli_workspace_mount_enabled() -> bool:
    return normalize_workspace_mount_mode() in ("ro", "rw")


def _sanitize_relay_id(relay_id: str) -> str:
    """Return a path-safe relay id segment."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", relay_id or "")
    safe = safe.strip("._-")
    return safe or "relay"


def _mode_suffix(mode: str) -> str:
    return ":ro" if mode == "ro" else ""


def _relay_info_by_id(user_id: str = "") -> Dict[str, dict]:
    from core.relay_bindings import list_available_relays

    return {r.get("relay_id", ""): r for r in list_available_relays(user_id=user_id)}


def _docker_source_for_relay(relay_id: str, relay_info: dict) -> str:
    host_root = (relay_info.get("host_root") or "").strip()
    if not host_root:
        logger.info(
            "[cli-workspace-mount] relay %s has no host_root; skipping", relay_id)
        return ""
    if not os.path.isdir(host_root):
        logger.info(
            "[cli-workspace-mount] relay %s host_root is not mounted on this host: %s",
            relay_id, host_root)
        return ""
    from core.docker_utils import to_host_path, translate_path

    return translate_path(to_host_path(host_root))


def build_cli_workspace_mount_args(conversation_id: str, agent_name: str = "",
                                   user_id: str = "", mode: str = "") -> List[str]:
    """Build Docker -v args for CLI provider workspace fallback mounts.

    /workspace is the default relay for this agent/conversation.
    /relay/<relay-id> exposes every linked relay with a local host_root.
    """
    mount_mode = normalize_workspace_mount_mode(mode)
    if mount_mode == "off":
        return []
    if not conversation_id:
        logger.info("[cli-workspace-mount] missing conversation_id; no mounts")
        return []
    if conversation_id.startswith("_"):
        logger.info(
            "[cli-workspace-mount] internal conversation %s; no workspace mounts",
            conversation_id)
        return []

    from core.relay_bindings import get_default, get_linked

    try:
        linked = get_linked(conversation_id, agent_name)
        default_relay = get_default(conversation_id, agent_name) or ""
    except Exception as exc:
        logger.info(
            "[cli-workspace-mount] cannot resolve relay bindings for %s/%s: %s",
            conversation_id[:8], agent_name, exc)
        return []
    if not linked:
        logger.info(
            "[cli-workspace-mount] no linked relay for %s/%s", conversation_id[:8], agent_name)
        return []

    info_by_id = _relay_info_by_id(user_id=user_id)
    suffix = _mode_suffix(mount_mode)
    args: List[str] = []
    mounted: set[Tuple[str, str]] = set()

    def _add(relay_id: str, target: str):
        if not relay_id:
            return
        relay_info = info_by_id.get(relay_id) or {}
        if not relay_info.get("connected", False):
            logger.info(
                "[cli-workspace-mount] relay %s is not connected; skipping", relay_id)
            return
        source = _docker_source_for_relay(relay_id, relay_info)
        if not source:
            return
        key = (source, target)
        if key in mounted:
            return
        mounted.add(key)
        args.extend(["-v", f"{source}:{target}{suffix}"])

    if default_relay:
        _add(default_relay, "/workspace")
    else:
        logger.info(
            "[cli-workspace-mount] no default relay for %s/%s; /workspace not mounted",
            conversation_id[:8], agent_name)

    for relay_id in linked:
        _add(relay_id, f"/relay/{_sanitize_relay_id(relay_id)}")

    if args:
        logger.info(
            "[cli-workspace-mount] mode=%s mounts=%d conv=%s agent=%s",
            mount_mode, len(args) // 2, conversation_id[:8], agent_name)
    return args


def build_skill_mount_args(conversation_id: str, agent_name: str = "",
                           user_id: str = "") -> List[str]:
    """Build Docker -v args exposing the skill repository read-only.

    Rather than mounting each assigned skill individually, the skill
    repository scope directories are mounted once: global skills and this
    user's skill tree (which nests conversation-scoped skills). Mounting the
    parents means a skill assigned mid-session — or a skill run one-shot
    while unassigned — becomes visible inside the container without
    recreating it. The in-container layout mirrors the server:
    /skills/global/<name>, /skills/users/<uid>/<name>,
    /skills/users/<uid>/<conv>/<name>.
    """
    from core.paths import REPOSITORY_DIR
    from core.docker_utils import to_host_path, translate_path

    skills_base = (REPOSITORY_DIR / "skills").resolve()
    args: List[str] = []
    seen: set[str] = set()

    def _add(server_dir) -> None:
        try:
            # Create the mount point so a skill written into this scope later
            # in the session is visible without recreating the container.
            os.makedirs(server_dir, exist_ok=True)
            # World-readable so the uid-1000 CLI container can read the mount.
            try:
                os.chmod(server_dir, 0o755)
            except OSError:
                pass
            rel = os.path.relpath(str(server_dir), str(skills_base))
        except Exception as exc:
            logger.info("[skill-mount] skip %s: %s", server_dir, exc)
            return
        if rel.startswith(".."):
            return
        target = "/skills/" + rel.replace(os.sep, "/")
        if target in seen:
            return
        seen.add(target)
        source = translate_path(to_host_path(str(server_dir)))
        if not os.path.exists(source):
            logger.warning(
                "[skill-mount] host source %s not visible from this process "
                "(ok if the server is containerized with data on a volume; "
                "otherwise skills will be missing in the container)", source)
        args.extend(["-v", f"{source}:{target}:ro"])

    _add(skills_base / "global")
    if user_id:
        _add(skills_base / "users" / user_id)
    if args:
        logger.info("[skill-mount] mounts=%d conv=%s agent=%s",
                    len(args) // 2, (conversation_id or "")[:8], agent_name)
    return args
