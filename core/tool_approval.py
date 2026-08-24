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
import shlex
import threading
import time
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
        # One-shot delegation to the conversation agent's own LLM — pure
        # text completion, the delegate gets no tools (voice-front pattern)
        "consult_agent",
        # Info / help
        "pawflow_help", "list_secrets",
        # File read
        "read", "list_dir", "stat", "exists", "glob", "grep", "search",
        # Display / media info
        "show_file", "get_image_model_info",
        # Web / search
        "web_search",
        # History / context
        "read_history", "read_parent_context", "conversation_search",
        # User interaction (no data modification)
        "notify_user", "ask_user", "request_confirmation",
        # Meta / internal
        "get_tool_schema", "compact_result", "share_file", "create_file",
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

    # ── read_only mode allowlist ──────────────────────────────────────
    # Tools the relay accepts when the conversation's permission_mode is
    # 'read_only'. Allowlist (not blocklist) so any new tool added later
    # is denied by default — fail-closed. The relay calls
    # `is_read_only_allowed(tool_name, arguments)` to decide; the inner
    # filesystem.<action> dispatch reuses _FS_EXEMPT.
    READ_ONLY_ALLOWED = frozenset({
        # File / dir read
        "read", "list_dir", "stat", "exists", "glob", "grep", "search",
        "show_file",
        # Memory / history (read)
        "recall", "semantic_recall",
        "read_history", "read_parent_context", "conversation_search",
        # Help / introspection
        "pawflow_help", "get_tool_schema", "list_secrets",
        # Web search (read-only by definition)
        "web_search",
        # User interaction (no data modification)
        "notify_user", "ask_user", "request_confirmation",
    })
    ADVISOR_READ_ONLY_ALLOWED = READ_ONLY_ALLOWED - frozenset({
        "notify_user", "ask_user", "request_confirmation",
    })

    # ── See tool: file read is exempt, screenshot needs approval ──────

    _SEE_SCREEN_PATHS = frozenset({"screen", "screenshot"})

    # Tools whose approval must be judged on the command they carry, not on
    # their name. `monitor` belongs here because it is a bash call wearing a
    # different label: it builds a shell script and hands it to BashHandler
    # in-process, so the gate never sees a `bash` tool call. Without this, one
    # approval of a harmless `monitor` persisted as `session_allow` and every
    # later monitor command -- including a destructive one, including
    # local=true -- rode in on it with no second prompt.
    COMMAND_BEARING_TOOLS = frozenset({"bash", "execute_script", "monitor"})

    @staticmethod
    def normalize_tool_name(tool_name: str) -> str:
        """Return the case-insensitive spelling used by policy tables."""
        return str(tool_name or "").strip().casefold()

    @classmethod
    def escalated_policy_name(cls, tool_name: str) -> str:
        """Return the name whose severity governs this call.

        `shell`, `exec`, `run`, `terminal`, `run_command` and `execute` are
        executable aliases of `bash` (core/_llm_types.py:93). Judged on their
        own spelling they miss ALWAYS_ASK and the command-bearing content
        scan, so one session approval covered every later call - including a
        destructive one. An alias must never be judged more leniently than
        the tool it actually runs.

        Escalation only: an alias whose target is not ALWAYS_ASK keeps its own
        classification, so `create_file` (-> write) stays EXEMPT and no
        existing decision is tightened as a side effect.
        """
        policy = cls.normalize_tool_name(tool_name)
        try:
            from core._llm_types import _TOOL_ALIASES
        except Exception:
            return policy
        target = cls.normalize_tool_name(_TOOL_ALIASES.get(policy, policy))
        return target if target in cls.ALWAYS_ASK else policy

    @classmethod
    def is_command_bearing_tool(cls, tool_name: str) -> bool:
        return cls.escalated_policy_name(tool_name) in cls.COMMAND_BEARING_TOOLS

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

    _PATCH_PATH_PREFIXES = (
        "*** Add File:", "*** Update File:", "*** Delete File:",
        "*** Move to:",
    )

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
        allow_prompt: bool = True,
        attribution: Optional[Dict[str, Any]] = None,
        cancel_event: threading.Event = None,
        force_prompt: bool = False,
    ) -> str:
        """Check if tool execution is approved.

        Returns "approved" or "denied" or "timeout" — or "needs_approval"
        when allow_prompt=False and the decision would require the
        interactive dialog (voice sessions and other UX-less callers probe
        this way instead of blocking on a dialog nobody can answer).
        Permissions are scoped per (conversation, agent). Agent A's approval
        does not carry over to agent B.
        For filesystem/see tools, arguments determine the approval level.
        Users can always override with "always_allow" — even for dangerous tools.
        """
        # Determine effective approval level
        policy_name = cls.escalated_policy_name(tool_name)
        effective_name = tool_name
        is_exempt = policy_name in cls.EXEMPT_TOOLS
        needs_ask = not is_exempt  # all non-exempt tools need approval

        # Filesystem: action-aware approval
        if policy_name == "filesystem" and arguments:
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
        if policy_name == "see" and arguments:
            _path = (arguments.get("path", "") or "").lower().strip()
            if _path in cls._SEE_SCREEN_PATHS:
                effective_name = "see.screenshot"
                needs_ask = True
                is_exempt = False
            else:
                is_exempt = True
                needs_ask = False

        # Memory write: not dangerous but has side effects — ask once
        if policy_name in ("remember", "forget"):
            is_exempt = False
            needs_ask = True

        if force_prompt:
            is_exempt = False
            needs_ask = True
        if is_exempt:
            return "approved"

        # ── Dangerous bash content check ─────────────────────────────
        # Even with session_allow, dangerous patterns force re-approval.
        # Catastrophic patterns get a visible warning hint in the dialog.
        _force_ask = bool(force_prompt)
        _catastrophic_hint = False
        if cls.is_command_bearing_tool(tool_name) and arguments:
            _cmd = arguments.get("command", "") or arguments.get("code", "")
            if cls._is_catastrophic_command(_cmd):
                _force_ask = True
                _catastrophic_hint = True
                effective_name = f"{tool_name}:catastrophic"
                action_summary = f"\u26a0\ufe0f CATASTROPHIC: {action_summary}"
            elif cls._is_dangerous_command(_cmd):
                _force_ask = True
                effective_name = f"{tool_name}:dangerous"

        # ── Protected path check ─────────────────────────────────────
        # Write/delete to protected paths always ask, even with session_allow.
        if policy_name in ("write", "edit", "delete", "batch_edit", "apply_patch",
                         "find_replace") and arguments:
            if any(cls._is_protected_path(path) for path in
                   cls._write_paths(policy_name, arguments)):
                _force_ask = True
                effective_name = f"{tool_name}:protected"
        if policy_name == "filesystem" and arguments:
            _fs_action = arguments.get("action", "")
            if _fs_action in ("write_file", "edit", "delete_file", "find_replace",
                              "batch_edit", "apply_patch"):
                if any(cls._is_protected_path(path) for path in
                       cls._write_paths(_fs_action, arguments)):
                    _force_ask = True
                    effective_name = f"filesystem.{_fs_action}:protected"

        # Check conversation+agent scoped permissions
        perms = cls._get_permissions(conversation_id, agent_name)
        # Check allow-all scopes (e.g. _allow_all:filesystem, _allow_all:screen)
        if not _force_ask:
            if policy_name == "filesystem" and arguments:
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

        if not allow_prompt:
            # Probe mode: the caller has no approval UX to offer.
            return "needs_approval"

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
                "attribution": dict(attribution or {}),
            }

        # Publish SSE event for approval dialog. If nobody is connected,
        # fail closed immediately instead of blocking the agent for the full
        # approval timeout; a replayed approval request cannot be answered by
        # a tab that was absent when the tool wanted to run.
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            if bus.subscriber_count(conversation_id) <= 0:
                logger.warning(
                    "tool approval denied: no live subscriber for conv=%s tool=%s",
                    conversation_id[:8], effective_name)
                with cls._lock:
                    cls._pending.pop(request_id, None)
                return "denied"
            bus.publish_event(
                conversation_id, "tool_approval_request", {
                    "request_id": request_id,
                    "tool_name": effective_name,
                    "action_summary": action_summary,
                    "agent_name": agent_name,
                    "arguments": cls._truncate_args(arguments or {}),
                    "attribution": dict(attribution or {}),
                },
            )
        except Exception as e:
            logger.warning("Failed to publish tool approval request: %s", e)
            with cls._lock:
                cls._pending.pop(request_id, None)
            # Fail-closed by default in production: if we can't show the
            # dialog (no UI subscriber, bus broken, ...) the safe answer
            # is `denied`, not silent approval. Set
            # PAWFLOW_APPROVAL_FAIL_OPEN=true to keep the historical
            # dev-friendly behaviour where non-ALWAYS_ASK tools get
            # auto-approved instead.
            import os
            if os.environ.get("PAWFLOW_APPROVAL_FAIL_OPEN", "").lower() in (
                    "1", "true", "yes"):
                if policy_name not in cls.ALWAYS_ASK:
                    return "approved"
            return "denied"

        # Wait for the user while allowing an owning workflow to retire this
        # exact request promptly on cancellation.
        deadline = time.monotonic() + 60
        while not event.wait(timeout=min(0.1, max(0.0, deadline - time.monotonic()))):
            if cancel_event is not None and cancel_event.is_set():
                with cls._lock:
                    cls._pending.pop(request_id, None)
                    cls._results.pop(request_id, None)
                return "cancelled"
            if time.monotonic() >= deadline:
                break
        if not event.is_set():
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
    def conversation_for_request(cls, request_id: str) -> str:
        """Which conversation is waiting on approval `request_id`.

        The pending table is global and keyed by request_id alone. An answer
        arrives as a plain action carrying that id, so authorization has to
        resolve the conversation the agent is blocked on rather than trust the
        conversation_id sent beside it -- otherwise a caller pairs a
        conversation they may write to with someone else's request and
        approves a tool there.

        Empty when the id is unknown: nothing is waiting, and resolve_request
        will say so.
        """
        if not request_id:
            return ""
        with cls._lock:
            pending = cls._pending.get(request_id)
        if not isinstance(pending, dict):
            return ""
        return str(pending.get("conversation_id", "") or "")

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
                return agent_perms
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
    def is_read_only_allowed(cls, tool_name: str,
                              arguments: Optional[dict] = None) -> bool:
        """Single source of truth for which tools the read_only permission
        mode lets through. Used by both `tool_relay_service` (gating MCP
        execution) and any other call site that needs the same decision.

        Returns True when the tool may run in read_only mode. Anything
        not listed is denied — the allowlist is fail-closed by design,
        so a brand-new tool added later is automatically denied until
        an explicit entry classifies it.
        """
        policy_name = cls.normalize_tool_name(tool_name)
        if not policy_name:
            return False
        if policy_name in cls.READ_ONLY_ALLOWED:
            return True
        # filesystem.<action> dispatches against _FS_EXEMPT — same set the
        # default mode treats as approval-exempt.
        if policy_name == "filesystem" and isinstance(arguments, dict):
            return arguments.get("action", "") in cls._FS_EXEMPT
        # `see` is read-only iff the path isn't a screen/screenshot path.
        if policy_name == "see" and isinstance(arguments, dict):
            _path = (arguments.get("path", "") or "").lower().strip()
            return _path not in cls._SEE_SCREEN_PATHS
        return False

    @classmethod
    def is_advisor_read_only_allowed(
            cls, tool_name: str,
            arguments: Optional[dict] = None) -> bool:
        """Read-only policy for silent internal planning advisors."""
        if cls.normalize_tool_name(tool_name) in {"notify_user", "ask_user"}:
            return False
        return cls.is_read_only_allowed(tool_name, arguments)

    # ── Advisor conversations: in-process registry ────────────────────
    # Ephemeral advisor sub-conversations are never persisted, so their
    # permission mode cannot live in ConversationStore extras (set_extra
    # no-ops for a conv that has no file). The executor loop registers
    # the conv id here for the run's duration and always unregisters in
    # its finally block — nothing leaks to disk.

    _advisor_read_only_convs: set = set()
    _advisor_convs_lock = threading.Lock()

    @classmethod
    def mark_advisor_read_only(cls, conversation_id: str) -> None:
        if not conversation_id:
            return
        with cls._advisor_convs_lock:
            cls._advisor_read_only_convs.add(conversation_id)

    @classmethod
    def unmark_advisor_read_only(cls, conversation_id: str) -> None:
        if not conversation_id:
            return
        with cls._advisor_convs_lock:
            cls._advisor_read_only_convs.discard(conversation_id)

    @classmethod
    def is_advisor_read_only_conv(cls, conversation_id: str) -> bool:
        if not conversation_id:
            return False
        with cls._advisor_convs_lock:
            return conversation_id in cls._advisor_read_only_convs

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

    @classmethod
    def _write_paths(cls, action: str, arguments: dict) -> list[str]:
        """Return every path a write-like call can affect.

        Multi-file tools carry paths below ``edits`` or inside patch headers;
        approval must inspect the same targets the handler will execute.
        """
        paths = []
        for key in ("path", "file_path"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                paths.append(value)

        normalized = cls.normalize_tool_name(action)
        if normalized == "batch_edit":
            for edit in arguments.get("edits") or []:
                if isinstance(edit, dict):
                    value = edit.get("path")
                    if isinstance(value, str) and value:
                        paths.append(value)
        elif normalized == "apply_patch":
            paths.extend(cls._patch_paths(arguments.get("patch", "")))
        return paths

    @classmethod
    def _patch_paths(cls, patch: str) -> list[str]:
        """Extract targets from OpenAI and unified-diff patch headers."""
        paths = []
        for raw_line in str(patch or "").splitlines():
            line = raw_line.strip()
            for prefix in cls._PATCH_PATH_PREFIXES:
                if line.startswith(prefix):
                    value = line[len(prefix):].strip()
                    if value:
                        paths.append(value)
                    break
            else:
                if line.startswith(("--- ", "+++ ")):
                    value = line[4:].split("\t", 1)[0].strip()
                    if value and value != "/dev/null":
                        paths.append(value)
                elif line.startswith("diff --git "):
                    try:
                        parts = shlex.split(line)
                    except ValueError:
                        parts = []
                    if len(parts) >= 4:
                        paths.extend(parts[2:4])
        return paths
