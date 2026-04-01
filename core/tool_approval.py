"""Tool Approval Gate — universal tool permission system.

Claude Code-like approval for all tools. The user can approve once,
for the session (conversation), or always.

Permission modes (set per conversation via /permission command):
  - auto:          all tools auto-approved, no dialogs
  - default:       EXEMPT tools auto-approved, others ask (session-based)
  - approve_edits: same as default
  - read_only:     write tools blocked entirely

Thread-safe. Uses ConversationStore.extra for persistence.
"""

import logging
import threading
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ToolApprovalGate:
    """Universal tool approval gate. Checks per-conversation permissions."""

    # ── Tool classifications ─────────────────────────────────────────

    # EXEMPT: never need approval (read-only, informational, no side effects)
    EXEMPT_TOOLS = frozenset({
        # Memory (read)
        "recall", "semantic_recall",
        # Info / help
        "pawflow_help", "list_secrets", "get_agent_results",
        # File read
        "read", "list_dir", "stat", "exists", "glob", "grep",
        # Display / media info
        "show_file", "get_image_model_info",
        # Web / search
        "web_search",
        # History / context
        "read_history", "read_parent_context",
        # User interaction (no data modification)
        "notify_user", "ask_user",
        # Meta / internal
        "get_tool_schema", "compact_result", "share_file", "create_file",
        "use_skill",
    })

    # ALWAYS_ASK: always need approval (dangerous, irreversible, sensitive)
    ALWAYS_ASK = frozenset({
        # Code execution
        "remote_exec", "execute_script", "bash",
        # Screen / browser access
        "screen", "browser_action", "browser",
        # Security sensitive
        "store_secret", "link_identity",
        # Creates executable code
        "create_tool", "manage_resource",
    })

    # DEFAULT: everything else — ask once per session (write, edit, plans, etc.)
    # Not listed explicitly — anything not in EXEMPT or ALWAYS_ASK.

    # ── Filesystem action sub-classifications ─────────────────────────

    _FS_EXEMPT = frozenset({
        "list_dir", "read_file", "stat", "exists", "search", "grep",
        "git_status", "git_log", "git_diff",
    })
    _FS_ALWAYS_ASK = frozenset({
        "exec", "git_push", "git_checkout",
    })

    # ── See tool: file read is exempt, screenshot needs approval ──────

    _SEE_SCREEN_PATHS = frozenset({"screen", "screenshot"})

    # ── Dangerous bash/exec patterns (like Claude Code) ──────────────
    # Even if bash has session_allow, these patterns force re-approval.
    _DANGEROUS_BASH_PATTERNS = frozenset({
        # Interpreters — can run arbitrary code
        "python", "python3", "python2", "node", "deno", "bun", "tsx",
        "ruby", "perl", "php", "lua",
        # Package runners — can execute arbitrary packages
        "npx", "bunx", "npm run", "yarn run", "pnpm run", "bun run",
        "pip install", "pip3 install",
        # Shells / eval — can execute arbitrary commands
        "bash", "sh", "zsh", "fish", "eval", "exec", "source",
        # Privilege escalation / remote
        "sudo", "su ", "ssh", "scp", "rsync",
        # Dangerous file ops
        "rm -rf", "rm -r", "rmdir", "mkfs", "dd if=", "chmod 777",
        "> /dev/", "curl | sh", "wget | sh", "curl | bash", "wget | bash",
        # Network / download (can exfiltrate data)
        "curl", "wget", "nc ", "netcat",
        # Git destructive
        "git push --force", "git push -f", "git reset --hard",
        "git clean -f", "git checkout -- .",
        # Docker escape
        "docker run", "docker exec",
        # PowerShell
        "powershell", "pwsh", "cmd /c", "wsl",
    })

    # ── Protected paths — always ask for write/delete ────────────────
    _PROTECTED_PATHS = frozenset({
        ".git/", ".git\\",
        ".env", ".env.",
        ".claude/", ".claude\\",
        ".vscode/", ".vscode\\",
        "secrets", ".credentials",
        "id_rsa", "id_ed25519", ".ssh/",
        ".npmrc", ".pypirc",
        "docker-compose", "Dockerfile",
        ".github/workflows",
    })

    # ── State ─────────────────────────────────────────────────────────

    _lock = threading.Lock()
    _pending: Dict[str, threading.Event] = {}
    _results: Dict[str, Any] = {}

    @classmethod
    def check(
        cls, tool_name: str, action_summary: str,
        conversation_id: str, user_id: str,
        arguments: dict = None,
        agent_name: str = "",
    ) -> str:
        """Check if tool execution is approved.

        Returns "approved" or "denied" or "timeout".
        Permissions are scoped per (conversation, agent). Agent A's approval
        does not carry over to agent B.
        For filesystem/see tools, arguments determine the approval level.
        Users can always override with "always_allow" — even for dangerous tools.
        """
        # Determine effective approval level
        effective_name = tool_name
        is_always_ask = tool_name in cls.ALWAYS_ASK
        is_exempt = tool_name in cls.EXEMPT_TOOLS
        needs_ask = not is_exempt  # all non-exempt tools need approval

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

        # See: file read is exempt, screenshot needs approval
        if tool_name == "see" and arguments:
            _path = (arguments.get("path", "") or "").lower().strip()
            if _path in cls._SEE_SCREEN_PATHS:
                effective_name = "see.screenshot"
                needs_ask = True
                is_exempt = False
            else:
                is_exempt = True
                needs_ask = False

        # Memory write: not dangerous but has side effects — ask once
        if tool_name in ("remember", "forget"):
            is_exempt = False
            needs_ask = True

        if is_exempt:
            return "approved"

        # ── Dangerous bash content check ─────────────────────────────
        # Even with session_allow, dangerous patterns force re-approval.
        _force_ask = False
        if tool_name in ("bash", "execute_script") and arguments:
            _cmd = arguments.get("command", "") or arguments.get("code", "")
            if cls._is_dangerous_command(_cmd):
                _force_ask = True
                is_always_ask = True
                effective_name = f"{tool_name}:dangerous"

        # ── Protected path check ─────────────────────────────────────
        # Write/delete to protected paths always ask, even with session_allow.
        if tool_name in ("write", "edit", "delete", "batch_edit", "apply_patch",
                         "find_replace") and arguments:
            _path = arguments.get("path", "") or arguments.get("file_path", "")
            if cls._is_protected_path(_path):
                _force_ask = True
                is_always_ask = True
                effective_name = f"{tool_name}:protected"
        if tool_name == "filesystem" and arguments:
            _fs_action = arguments.get("action", "")
            if _fs_action in ("write_file", "edit", "delete_file", "find_replace",
                              "batch_edit", "apply_patch"):
                _path = arguments.get("path", "")
                if cls._is_protected_path(_path):
                    _force_ask = True
                    is_always_ask = True
                    effective_name = f"filesystem.{_fs_action}:protected"

        # Check conversation+agent scoped permissions
        perms = cls._get_permissions(conversation_id, agent_name)
        # Check allow-all scopes (e.g. _allow_all:filesystem, _allow_all:screen)
        if not _force_ask:
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
                "agent_name": agent_name,
            }

        # Publish SSE event for approval dialog
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "tool_approval_request", {
                    "request_id": request_id,
                    "tool_name": effective_name,
                    "action_summary": action_summary,
                    "agent_name": agent_name,
                    "arguments": cls._truncate_args(arguments or {}),
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
        _perm_agent = pending_info.get("agent_name", agent_name) if isinstance(pending_info, dict) else agent_name

        if choice == "allow_once":
            return "approved"
        elif choice == "allow_session":
            cls._set_permission(conversation_id, perm_name, "session_allow", agent_name=_perm_agent)
            return "approved"
        elif choice == "always_allow":
            cls._set_permission(conversation_id, perm_name, "always_allow", agent_name=_perm_agent)
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
    def _perm_key(cls, agent_name: str = "") -> str:
        """Return the store key for agent-scoped permissions."""
        if agent_name:
            return f"tool_permissions:{agent_name}"
        return "tool_permissions"

    @classmethod
    def _get_permissions(cls, conversation_id: str, agent_name: str = "") -> Dict[str, str]:
        """Get tool permissions for a (conversation, agent) pair."""
        if not conversation_id:
            return {}
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            # Agent-scoped permissions take priority
            if agent_name:
                agent_perms = store.get_extra(
                    conversation_id, cls._perm_key(agent_name)
                ) or {}
                if agent_perms:
                    return agent_perms
            # Fallback to conversation-level (legacy / no agent specified)
            return store.get_extra(
                conversation_id, "tool_permissions"
            ) or {}
        except Exception:
            return {}

    @classmethod
    def allow_all(cls, conversation_id: str, scope: str, agent_name: str = ""):
        """Auto-approve all operations for a scope (e.g. 'filesystem' or 'screen')."""
        cls._set_permission(conversation_id, f"_allow_all:{scope}", "always_allow", agent_name=agent_name)

    @classmethod
    def deny_all(cls, conversation_id: str, scope: str, agent_name: str = ""):
        """Revoke auto-approve for a scope."""
        cls._set_permission(conversation_id, f"_allow_all:{scope}", "", agent_name=agent_name)

    @classmethod
    def _set_permission(cls, conversation_id: str, tool_name: str, level: str,
                        agent_name: str = ""):
        """Set a tool permission for a (conversation, agent) pair."""
        if not conversation_id:
            return
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            key = cls._perm_key(agent_name)
            perms = store.get_extra(conversation_id, key) or {}
            perms[tool_name] = level
            store.set_extra(conversation_id, key, perms)
        except Exception as e:
            logger.warning("Failed to set tool permission: %s", e)

    @classmethod
    def get_mode(cls, conversation_id: str) -> str:
        """Get the permission mode for a conversation.

        Returns: 'auto', 'default', 'approve_edits', 'read_only'.
        The tool relay and agent loop both use this to decide gating.
        """
        if not conversation_id:
            return "default"
        try:
            from core.conversation_store import ConversationStore
            return ConversationStore.instance().get_extra(
                conversation_id, "permission_mode"
            ) or "default"
        except Exception:
            return "default"

    @staticmethod
    def _truncate_args(arguments: dict, max_val_len: int = 500) -> dict:
        """Truncate long argument values for the approval dialog."""
        result = {}
        for k, v in arguments.items():
            if isinstance(v, str) and len(v) > max_val_len:
                result[k] = v[:max_val_len] + f"... ({len(v)} chars)"
            else:
                result[k] = v
        return result

    @classmethod
    def _is_dangerous_command(cls, command: str) -> bool:
        """Check if a bash command contains dangerous patterns.

        Returns True if the command matches any pattern from Claude Code's
        dangerous command list (~90 patterns).
        """
        if not command:
            return False
        cmd_lower = command.lower().strip()
        # Check each pattern
        for pattern in cls._DANGEROUS_BASH_PATTERNS:
            if pattern in cmd_lower:
                return True
        # Pipe to shell (download + execute)
        if "|" in cmd_lower and any(sh in cmd_lower for sh in ("sh", "bash", "python", "node")):
            return True
        return False

    @classmethod
    def _is_catastrophic_command(cls, command: str) -> bool:
        """Check if a command is catastrophic (blocked even in auto mode).

        These commands are so dangerous they should NEVER run without
        explicit user confirmation, regardless of permission mode.
        """
        if not command:
            return False
        cmd = command.strip()
        _catastrophic = [
            "rm -rf /", "rm -rf /*", "rm -rf ~",
            "dd if=/dev/zero of=/dev/sd", "mkfs.",
            ":(){:|:&};:", "chmod -R 777 /",
            "mv / ", "mv /* ",
            "> /dev/sda",
            "del /f /s /q c:\\",
        ]
        for pat in _catastrophic:
            if pat in cmd.lower():
                return True
        return False

    @classmethod
    def _is_protected_path(cls, path: str) -> bool:
        """Check if a file path is protected (sensitive config/secrets).

        Protected paths always require approval even with session_allow.
        """
        if not path:
            return False
        path_lower = path.lower().replace("\\", "/")
        for protected in cls._PROTECTED_PATHS:
            if protected.lower() in path_lower:
                return True
        return False
