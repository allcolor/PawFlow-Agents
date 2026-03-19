"""Tool Approval Gate — universal tool permission system.

Claude Code-like approval for all tools. The user can approve once,
for the session (conversation), or always.

Thread-safe. Uses ConversationStore.extra for persistence.
"""

import logging
import threading
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ToolApprovalGate:
    """Universal tool approval gate. Checks per-conversation permissions."""

    # Tools that never need approval (read-only, informational)
    EXEMPT_TOOLS = frozenset({
        "recall", "semantic_recall", "pawflow_help", "list_secrets",
        "get_agent_results", "show_file", "manage_resource",
        "remember", "forget", "notify_user", "web_search",
    })

    # Tools that always need approval (dangerous operations)
    ALWAYS_ASK = frozenset({
        "remote_exec", "execute_script", "browser_action",
    })

    # Filesystem: read-only actions are exempt, write actions ask once,
    # dangerous actions always ask
    _FS_EXEMPT = frozenset({
        "list_dir", "read_file", "stat", "exists", "search", "grep",
        "git_status", "git_log", "git_diff",
    })
    _FS_ALWAYS_ASK = frozenset({
        "exec", "git_push", "git_checkout",
    })
    # Everything else (write_file, edit, mkdir, delete_file, find_replace,
    # git_commit) = ask once, allow "session" option

    _lock = threading.Lock()
    _pending: Dict[str, threading.Event] = {}
    _results: Dict[str, Any] = {}

    @classmethod
    def check(
        cls, tool_name: str, action_summary: str,
        conversation_id: str, user_id: str,
        arguments: dict = None,
    ) -> str:
        """Check if tool execution is approved.

        Returns "approved" or "denied" or "timeout".
        For filesystem tool, the action field determines the approval level.
        Users can always override with "always_allow" — even for dangerous tools.
        """
        # Determine effective approval level
        effective_name = tool_name
        needs_ask = tool_name in cls.ALWAYS_ASK
        is_exempt = tool_name in cls.EXEMPT_TOOLS

        # Filesystem: action-aware approval
        if tool_name == "filesystem" and arguments:
            fs_action = arguments.get("action", "")
            effective_name = f"filesystem.{fs_action}"
            if fs_action in cls._FS_EXEMPT:
                is_exempt = True
                needs_ask = False
            elif fs_action in cls._FS_ALWAYS_ASK:
                needs_ask = True
                is_exempt = False
            else:
                # Write actions: ask once, allow session
                needs_ask = True
                is_exempt = False

        if is_exempt:
            return "approved"

        # Check conversation-level permissions (user can override anything)
        perms = cls._get_permissions(conversation_id)
        # Check allow-all scopes (e.g. _allow_all:filesystem, _allow_all:filesystem.localFS)
        if tool_name == "filesystem" and arguments:
            svc_name = arguments.get("service", "")
            if svc_name and perms.get(f"_allow_all:filesystem.{svc_name}") == "always_allow":
                return "approved"
            if perms.get("_allow_all:filesystem") == "always_allow":
                return "approved"
        if perms.get(f"_allow_all:{tool_name}") == "always_allow":
            return "approved"
        tool_perm = perms.get(effective_name, "") or perms.get(tool_name, "")
        if tool_perm in ("always_allow", "session_allow"):
            return "approved"

        if not needs_ask:
            return "approved"

        # Need to ask the user
        if not conversation_id:
            return "denied"

        request_id = uuid.uuid4().hex[:12]
        event = threading.Event()

        with cls._lock:
            cls._pending[request_id] = {
                "event": event,
                "effective_name": effective_name,
                "conversation_id": conversation_id,
            }

        # Publish SSE event for approval dialog
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "tool_approval_request", {
                    "request_id": request_id,
                    "tool_name": effective_name,
                    "action_summary": action_summary,
                },
            )
        except Exception as e:
            logger.warning("Failed to publish tool approval request: %s", e)
            with cls._lock:
                cls._pending.pop(request_id, None)
            # If we can't show dialog, approve non-ALWAYS_ASK tools
            if tool_name not in cls.ALWAYS_ASK:
                return "approved"
            return "denied"

        # Block until user responds (60 seconds)
        if not event.wait(timeout=60):
            with cls._lock:
                cls._pending.pop(request_id, None)
                cls._results.pop(request_id, None)
            return "timeout"

        with cls._lock:
            pending_info = cls._pending.pop(request_id, {})
            result = cls._results.pop(request_id, None)

        if result is None:
            return "denied"

        choice = result.get("choice", "deny")
        perm_name = pending_info.get("effective_name", effective_name) if isinstance(pending_info, dict) else effective_name

        if choice == "allow_once":
            return "approved"
        elif choice == "allow_session":
            cls._set_permission(conversation_id, perm_name, "session_allow")
            return "approved"
        elif choice == "always_allow":
            cls._set_permission(conversation_id, perm_name, "always_allow")
            return "approved"
        else:
            return "denied"

    @classmethod
    def resolve_request(cls, request_id: str, result: Dict[str, Any]) -> bool:
        """Called when the user responds to an approval dialog."""
        with cls._lock:
            pending = cls._pending.get(request_id)
            if pending is None:
                logger.warning("tool_approval: resolve for unknown id: %s", request_id)
                return False
            event = pending["event"] if isinstance(pending, dict) else pending
            cls._results[request_id] = result
            event.set()
        return True

    @classmethod
    def _get_permissions(cls, conversation_id: str) -> Dict[str, str]:
        """Get tool permissions for a conversation."""
        if not conversation_id:
            return {}
        try:
            from core.conversation_store import ConversationStore
            return ConversationStore.instance().get_extra(
                conversation_id, "tool_permissions"
            ) or {}
        except Exception:
            return {}

    @classmethod
    def allow_all(cls, conversation_id: str, scope: str):
        """Auto-approve all operations for a scope (e.g. 'filesystem.localFS' or 'filesystem').

        Used by /allow-all command. Can be revoked with deny_all().
        """
        cls._set_permission(conversation_id, f"_allow_all:{scope}", "always_allow")

    @classmethod
    def deny_all(cls, conversation_id: str, scope: str):
        """Revoke auto-approve for a scope."""
        cls._set_permission(conversation_id, f"_allow_all:{scope}", "")

    @classmethod
    def _set_permission(cls, conversation_id: str, tool_name: str, level: str):
        """Set a tool permission for a conversation."""
        if not conversation_id:
            return
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            perms = store.get_extra(conversation_id, "tool_permissions") or {}
            perms[tool_name] = level
            store.set_extra(conversation_id, "tool_permissions", perms)
        except Exception as e:
            logger.warning("Failed to set tool permission: %s", e)

    @classmethod
    def is_enabled(cls, conversation_id: str) -> bool:
        """Check if tool approval is enabled for a conversation."""
        if not conversation_id:
            return False
        try:
            from core.conversation_store import ConversationStore
            return bool(ConversationStore.instance().get_extra(
                conversation_id, "tool_approval_enabled"
            ))
        except Exception:
            return False

    @classmethod
    def enable(cls, conversation_id: str):
        """Enable tool approval for a conversation."""
        try:
            from core.conversation_store import ConversationStore
            ConversationStore.instance().set_extra(
                conversation_id, "tool_approval_enabled", True
            )
        except Exception:
            pass

    @classmethod
    def disable(cls, conversation_id: str):
        """Disable tool approval for a conversation."""
        try:
            from core.conversation_store import ConversationStore
            ConversationStore.instance().set_extra(
                conversation_id, "tool_approval_enabled", False
            )
        except Exception:
            pass
