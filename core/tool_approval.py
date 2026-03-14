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
        "recall", "semantic_recall", "pyfi2_help", "list_secrets",
        "get_agent_results", "show_file", "manage_resource",
    })

    # Tools that always need approval (dangerous operations)
    ALWAYS_ASK = frozenset({
        "remote_exec", "execute_script", "local_files",
        "filesystem", "browser_action",
    })

    _lock = threading.Lock()
    _pending: Dict[str, threading.Event] = {}
    _results: Dict[str, Any] = {}

    @classmethod
    def check(
        cls, tool_name: str, action_summary: str,
        conversation_id: str, user_id: str,
    ) -> str:
        """Check if tool execution is approved.

        Returns "approved" or "denied" or "timeout".
        """
        if tool_name in cls.EXEMPT_TOOLS:
            return "approved"

        # Check conversation-level permissions
        perms = cls._get_permissions(conversation_id)

        tool_perm = perms.get(tool_name, "")
        if tool_perm == "always_allow":
            return "approved"
        if tool_perm == "session_allow":
            return "approved"

        # Need to ask the user
        if not conversation_id:
            # No conversation context — can't show dialog
            if tool_name not in cls.ALWAYS_ASK:
                return "approved"
            return "denied"

        request_id = uuid.uuid4().hex[:12]
        event = threading.Event()

        with cls._lock:
            cls._pending[request_id] = event

        # Publish SSE event for approval dialog
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "tool_approval_request", {
                    "request_id": request_id,
                    "tool_name": tool_name,
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
            result = cls._results.pop(request_id, None)
            cls._pending.pop(request_id, None)

        if result is None:
            return "denied"

        choice = result.get("choice", "deny")

        if choice == "allow_once":
            return "approved"
        elif choice == "allow_session":
            cls._set_permission(conversation_id, tool_name, "session_allow")
            return "approved"
        elif choice == "always_allow":
            cls._set_permission(conversation_id, tool_name, "always_allow")
            return "approved"
        else:
            return "denied"

    @classmethod
    def resolve_request(cls, request_id: str, result: Dict[str, Any]) -> bool:
        """Called when the user responds to an approval dialog."""
        with cls._lock:
            event = cls._pending.get(request_id)
            if event is None:
                logger.warning("tool_approval: resolve for unknown id: %s", request_id)
                return False
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
