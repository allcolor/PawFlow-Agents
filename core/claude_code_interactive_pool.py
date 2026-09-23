"""Persistent Docker sessions for claude-code-interactive.

Unlike ``ClaudeCodePool`` where one ``claude -p`` exec owns one throwaway
container, this pool keeps one interactive Claude Code tmux session alive per
``(user, conversation, agent, service)``. Output is not read from tmux or the
Claude transcript; the provider consumes MITM-observed SSE events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional
try:
    import fcntl  # POSIX-only; the flock below is best-effort on platforms that have it
except ImportError:  # Windows: server still boots, the marker file is written without an advisory lock
    fcntl = None  # type: ignore[assignment]
import hashlib
import json
import os
import shutil
import subprocess  # nosec B404
import threading
import time
import logging
import uuid

from core.docker_utils import docker_cmd
from core._cci_pool_spawn import InteractiveContainer, _InteractiveContainerSpawnMixin
# _paths kept as a module attribute: tests patch pool_mod._paths.CLAUDE_SESSIONS_DIR
# (the spawn methods now live in _cci_pool_spawn but read the same core.paths).
import core.paths as _paths  # noqa: F401


logger = logging.getLogger(__name__)

# Native file-IO tools (Read/Edit/Write/Glob/Grep/NotebookEdit) are deliberately
# ALLOWED (mirrors the codex provider): the agent must read its local PawFlow
# bootstrap (/cc_sessions/.../initial_context.md) and session files even when no
# relay is connected. Only tools that target the WRONG environment (Bash = the
# container shell, not the relay /workspace) or shadow a PawFlow MCP equivalent
# remain blocked; steering project work to the PawFlow MCP tools is done via the
# system/bootstrap prompt, not a hard fs block.
_DISALLOWED_BUILTIN_TOOLS = (
    "Bash,WebFetch,WebSearch,"
    "Task,Agent,ToolSearch,ListMcpResourcesTool,ReadMcpResourceTool,"
    "EnterPlanMode,ExitPlanMode,EnterWorktree,ExitWorktree,"
    "RemoteTrigger,Skill,TaskOutput,TaskStop,TodoWrite,"
    "CronCreate,CronDelete,CronList,Monitor,"
    "ScheduleWakeup,PushNotification"
)

# Above this, a container removal is reported. Measured on this image with an
# idle daemon: ~0.16s. See InteractiveClaudeCodePool._kill_container.
_SLOW_KILL_SECONDS = 2.0




class InteractiveClaudeCodePool(_InteractiveContainerSpawnMixin):
    _instance: Optional["InteractiveClaudeCodePool"] = None
    _instance_lock = threading.Lock()
    _REQUIRE_PROMPT_READY = False
    _INITIAL_SUBMIT_ENTERS = 2
    _SUBMIT_DELAY_DEFAULT = 1.0
    # Claude Code keeps the historical low-latency preempt: its pane verifier
    # runs in the background.  Codex overrides this because its event receipt is
    # the only trustworthy proof that the TUI accepted the interrupted prompt.
    _VERIFY_INTERRUPT_SYNCHRONOUS = False
    _PREPARE_INTERRUPT_BEFORE_PASTE = False
    # A removal costs ~0.2s once the AppArmor profile lets the container
    # receive SIGKILL (docker/apparmor/pawflow-mount). It cost 10.2s while
    # that rule was missing, which is what the old 15s left no margin over.
    # The timeout stays generous: it guards a loaded daemon, not that bug.
    _KILL_TIMEOUT_SECONDS = float(
        os.environ.get("PAWFLOW_CLI_CONTAINER_KILL_TIMEOUT_SECONDS", "45")
        or 45)

    @classmethod
    def instance(cls) -> "InteractiveClaudeCodePool":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
                cls._instance._register_death_handlers()
            return cls._instance

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: Dict[tuple[str, str, str, str], InteractiveContainer] = {}
        self._disallowed_builtin_tools = _DISALLOWED_BUILTIN_TOOLS
        # (service_id, pool_index) slots reserved by in-flight _start_new
        # calls — a slot has been claimed but the container is not yet
        # registered in _sessions. A list (not a set) because slots are
        # SHARED: several concurrent launches may legitimately claim the
        # same credential slot, and each reservation must count once toward
        # that slot's load for least-loaded balancing.
        self._reserved_slots: list = []
        # Containers whose removal failed, kept so the sweeper can retry.
        # Every eviction path drops the pool entry BEFORE killing, so a
        # removal that fails silently leaves a live CLI nothing tracks: see
        # _kill_container.
        self._pending_kills: set[str] = set()
        self._sweeper_started = False
        self._sweeper_stop = threading.Event()
        self._tick_seconds = 60
        # No default TTL: a reaper that nobody asked for is exactly the kind
        # of silent fallback this project forbids. Eviction is opt-in through
        # PAWFLOW_CCI_IDLE_TTL_SECONDS or a service `timeout`; 0 or unset
        # means containers are never evicted for being idle.
        self._idle_ttl = float(
            os.environ.get("PAWFLOW_CCI_IDLE_TTL_SECONDS", "0") or 0)
        self._shutdown_once = False
        # Run the in-container CLI as the host launcher's uid/gid (the same
        # PAWFLOW_RUN_UID/GID the batch claude_code_pool honours) instead of a
        # hardcoded 1000. The server provisions the session workdir under this
        # uid, and server-side tools (e.g. the memory-skill `write`) write into
        # projects/ via the combined-fs as this uid — so the CLI must own those
        # subtrees with the SAME uid or those writes hit EACCES.
        self.run_uid = self._numeric_env("PAWFLOW_RUN_UID", "1000")
        self.run_gid = self._numeric_env("PAWFLOW_RUN_GID", "1000")

    def _register_death_handlers(self):
        """Kill tracked interactive containers when the Python process exits."""
        import atexit
        import signal
        import sys

        def _kill_all(*_args, **_kwargs):
            if getattr(self, "_shutdown_once", False):
                return
            self._shutdown_once = True
            try:
                self.shutdown_all()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        atexit.register(_kill_all)
        try:
            if threading.current_thread() is threading.main_thread():
                for _sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        _prev = signal.getsignal(_sig)

                        def _handler(signum, frame, prev=_prev):
                            _kill_all()
                            if callable(prev):
                                prev(signum, frame)
                            elif prev == signal.SIG_DFL:
                                sys.exit(128 + signum)

                        signal.signal(_sig, _handler)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    @staticmethod
    def _numeric_env(name: str, default: str) -> str:
        value = os.environ.get(name, default).strip()
        return value if value.isdigit() else default

    def _user_spec(self) -> str:
        """`<uid>:<gid>` for `docker exec --user`, mapped to the host launcher."""
        return f"{self.run_uid}:{self.run_gid}"

    @staticmethod
    def _safe(value: str) -> str:
        # `..` as a path component (agent named ".." or "a/../..") would escape
        # the session root on the host side. Replace it before the separators
        # so ".." can never survive as a component.
        return ((value or "").replace("..", "_")
                .replace(":", "_").replace("/", "_").replace("\\", "_"))

    @staticmethod
    def _container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions/{}/{}".format(
            InteractiveClaudeCodePool._safe(conversation_id or ""),
            InteractiveClaudeCodePool._safe(agent_name),
        )

    @staticmethod
    def _physical_container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions_host/{}/{}/{}".format(
            InteractiveClaudeCodePool._safe(user_id),
            InteractiveClaudeCodePool._safe(conversation_id),
            InteractiveClaudeCodePool._safe(agent_name),
        )

    def ensure_started(self, client, model: str, user_id: str,
                       conversation_id: str, agent_name: str,
                       before_launch=None) -> InteractiveContainer:
        """Return the live container for this key, launching one if there is none.

        ``before_launch`` is called at the instant this becomes a LAUNCH and
        not a reuse, and may refuse it by raising. That is the one place a
        caller can still say "the context I hold was built for a running
        process, so do not start a fresh one with it" -- see
        ``_cli_require_cold_context``. It is never called on the reuse path.
        """
        # `client.timeout` collapses an explicit 0 into None, which made
        # "no timeout configured" indistinguishable from "unset" and left the
        # reaper running. `idle_ttl_seconds` keeps the two apart.
        idle_ttl = getattr(client, "idle_ttl_seconds", None)
        self.ensure_sweeper(
            idle_ttl_seconds=int(idle_ttl) if idle_ttl is not None else None)
        service_id = getattr(client, "_agent_service", "") or ""
        key = (user_id, conversation_id, agent_name, service_id)
        with self._lock:
            existing = self._sessions.get(key)
        # Docker/tmux liveness probes are subprocess round trips — run them
        # OUTSIDE the pool lock so a slow docker daemon never stalls every
        # other session's pool access (perf audit F: 2 probes per turn were
        # executing under the global RLock).
        container_alive = bool(existing and self._is_alive(existing.name))
        tmux_alive = bool(existing and container_alive
                          and self._tmux_is_alive(existing.name))
        # The pool key is unchanged; the concrete provider, observation mode
        # and managed launch revision are compared on the live state instead.
        compatible = existing is None or self._session_compatible(
            existing, client)
        dead_existing = None
        stopped_existing = None
        with self._lock:
            current = self._sessions.get(key)
            if current is not None and current is not existing:
                # Someone else replaced the session while we probed —
                # theirs is fresher, reuse it.
                current.last_used = time.time()
                return current
            if (existing is not None and container_alive and tmux_alive
                    and compatible):
                existing.last_used = time.time()
                return existing
            if existing is not None:
                self._sessions.pop(key, None)
                if container_alive:
                    dead_existing = existing
                else:
                    stopped_existing = existing
        if stopped_existing is not None:
            # The container is gone, but its event session is still registered
            # and would keep adopting traffic as orphan captures.
            logger.warning("[cci-live] container %s stopped; recreating the "
                           "interactive session", stopped_existing.name)
            self._recover_container_tokens(stopped_existing)
            self._kill_container(stopped_existing.name)
            self._unregister_event_session(stopped_existing)
        if dead_existing is not None:
            if tmux_alive and not compatible:
                logger.info(
                    "[cci-live] live session %s was launched for %s/%s and "
                    "cannot serve %s; recreating it",
                    dead_existing.name, dead_existing.provider,
                    dead_existing.observation_mode,
                    self._client_provider(client) or "?")
            else:
                logger.warning(
                    "[cci-live] tmux session died inside live container %s; "
                    "recreating the interactive session", dead_existing.name)
            self._recover_container_tokens(dead_existing)
            self._kill_container(dead_existing.name)
            self._unregister_event_session(dead_existing)
        # About to launch. Ask before claiming a credential slot, so a
        # refusal costs nothing and releases nothing.
        if before_launch is not None:
            before_launch()
        api_key = getattr(client, "api_key", "")
        if callable(api_key):
            api_key = api_key()
        elif isinstance(api_key, property):
            api_key = ""
        # Read + decrypt the credentials file OUTSIDE the lock (beta-186
        # regression: file I/O + crypto were executing under the pool lock).
        pool_size = 0 if api_key else self._credentials_pool_size(
            service_id, user_id, conversation_id)
        # Claim a credential slot BEFORE spawning. Slots are shared —
        # any number of containers may run on one credential — but the
        # reservation is recorded under the lock so concurrent
        # ensure_started calls balance across slots (least-loaded).
        with self._lock:
            claimed_idx = -1 if api_key else self._claim_pool_slot_locked(
                service_id, user_id, conversation_id, pool_size)
        try:
            state = self._start_new(client, model, user_id, conversation_id,
                                    agent_name, key, pool_index=claimed_idx)
        except Exception:
            # Spawn failed — release the reservation so the slot's load drops.
            with self._lock:
                self._release_slot_locked(service_id, claimed_idx)
            raise
        with self._lock:
            self._sessions[key] = state
            # Now tracked via _sessions; drop the in-flight reservation.
            self._release_slot_locked(service_id, claimed_idx)
        return state

    def _release_slot_locked(self, service_id: str, idx: int) -> None:
        """Drop ONE in-flight reservation for this slot. MUST hold self._lock.

        No-op for API-key mode (idx < 0, never reserved) and when the
        reservation was already released.
        """
        try:
            self._reserved_slots.remove((service_id, idx))
        except (ValueError, KeyError):
            pass

    def touch(self, state: InteractiveContainer) -> None:
        with self._lock:
            state.last_used = time.time()

    def begin_turn(self, state: InteractiveContainer) -> None:
        """Mark a turn as running against this session (never idle-evicted)."""
        with self._lock:
            state.in_flight += 1
            state.last_used = time.time()

    def end_turn(self, state: InteractiveContainer) -> None:
        """Release the turn taken by ``begin_turn`` and restart the idle clock."""
        with self._lock:
            state.in_flight = max(0, state.in_flight - 1)
            state.last_used = time.time()

    def _credentials_pool_size(self, service_id: str, user_id: str,
                               conversation_id: str) -> int:
        """Load the credentials pool (file read + decrypt) and return its size.

        Deliberately called WITHOUT self._lock — the I/O and crypto cost must
        not serialize the pool. Raises LLMClientError when no credentials are
        configured at all.
        """
        from core.llm_client import LLMClientError
        from core.llm_providers._cc_credentials import _load_credentials_pool
        pool = _load_credentials_pool(
            service_id, user_id=user_id, conv_id=conversation_id) or []
        if not pool:
            raise LLMClientError(
                "Claude Code credentials not configured. "
                "Use /cls to authenticate with your Claude subscription.")
        return len(pool)

    def _claim_pool_slot_locked(self, service_id: str, user_id: str,
                                conversation_id: str,
                                pool_size: int = 0) -> int:
        """Pick the least-loaded credential slot for a new container.

        MUST be called holding self._lock. Returns the slot index. Slots are
        SHARED — one Claude login can back any number of concurrent
        containers — so this never refuses a launch because slots are busy;
        it only spreads containers across the configured logins. Raises
        LLMClientError only when no credentials are configured at all.
        ``pool_size`` should be precomputed outside the lock via
        ``_credentials_pool_size``; when 0, it is loaded here (test path).
        """
        if pool_size <= 0:
            pool_size = self._credentials_pool_size(
                service_id, user_id, conversation_id)
        load = [0] * pool_size
        for s in self._sessions.values():
            if s.service_id == service_id and 0 <= s.svc_pool_idx < pool_size:
                load[s.svc_pool_idx] += 1
        for sid, r_idx in self._reserved_slots:
            if sid == service_id and 0 <= r_idx < pool_size:
                load[r_idx] += 1
        idx = min(range(pool_size), key=lambda i: load[i])
        self._reserved_slots.append((service_id, idx))
        logger.info("[cci-live] claimed shared pool slot %d for %s "
                    "(slot load now %d)", idx, service_id, load[idx] + 1)
        return idx

    def _recover_container_tokens(self, state: InteractiveContainer) -> None:
        """Best-effort: copy any CLI-rotated OAuth token back to its pool slot.

        Claude Code refreshes its own token in-container; without this, a slot
        reused after a container that rotated its (single-use) refresh_token
        would hand the next container a stale token. No-op for API-key mode
        (svc_pool_idx<0) or when the credentials file is absent. Never raises.
        """
        if (not state or state.svc_pool_idx < 0
                or not state.service_id or not state.workdir):
            return
        try:
            from core.llm_providers._cc_credentials import recover_tokens_from_workdir
            recover_tokens_from_workdir(
                state.workdir, state.service_id, state.svc_pool_idx,
                user_id=state.user_id, conv_id=state.conv_id)
        except Exception:
            logger.debug("[cci-live] token recover failed", exc_info=True)

    def find_session(self, user_id: str, conversation_id: str,
                     agent_name: str, service_id: str = "") -> Optional[InteractiveContainer]:
        """Return the newest live interactive session for an agent."""
        stopped = []
        found = None
        with self._lock:
            candidates = [
                state for key, state in self._sessions.items()
                if key[0] == user_id
                and key[1] == conversation_id
                and key[2] == agent_name
                and (not service_id or key[3] == service_id)
            ]
            candidates.sort(key=lambda state: state.last_used, reverse=True)
            for state in candidates:
                if self._is_alive(state.name):
                    state.last_used = time.time()
                    found = state
                    break
                self._sessions.pop(state.key, None)
                stopped.append(state)
        self._retire_stopped(stopped)
        return found

    def _retire_stopped(self, states) -> None:
        """Clean up containers dropped from the pool because they stopped.

        Called outside the pool lock. Dropping the pool entry alone left the
        event session registered, so traffic from it was adopted as orphan
        captures that no pool lookup could ever reach again.
        """
        for state in states:
            logger.warning("[cci-live] container %s stopped; dropped from "
                           "the pool", state.name)
            self._recover_container_tokens(state)
            self._kill_container(state.name)
            self._unregister_event_session(state)

    def find_by_session_token(self, session_token: str) -> Optional[InteractiveContainer]:
        """The live container behind a proxy session token, if any.

        No liveness probe: the caller is reading that session's event stream,
        which is evidence enough that the container is up, and a docker
        inspect on every capture start would be pure latency.
        """
        if not session_token:
            return None
        with self._lock:
            for state in self._sessions.values():
                if state.session_token == session_token:
                    return state
        return None

    def list_sessions(self, user_id: str, conversation_id: str,
                      service_id: str = "") -> list[dict]:
        """Return live interactive sessions for a conversation."""
        sessions: list[dict] = []
        stopped = []
        with self._lock:
            candidates = [
                (key, state) for key, state in self._sessions.items()
                if key[0] == user_id
                and key[1] == conversation_id
                and (not service_id or key[3] == service_id)
            ]
            candidates.sort(key=lambda row: row[1].last_used, reverse=True)
            for key, state in candidates:
                if not self._is_alive(state.name):
                    self._sessions.pop(key, None)
                    stopped.append(state)
                    continue
                sessions.append({
                    "agent_name": key[2],
                    "service_id": key[3],
                    "container_name": state.name,
                    "last_used": state.last_used,
                    "live": True,
                    "reuse_count": 1 if state.initial_context_loaded else 0,
                    "created_at": state.created_at,
                    "idle_seconds": max(0.0, time.time() - state.last_used),
                    "lived_seconds": max(0.0, time.time() - state.created_at),
                    "provider": (getattr(state, "provider", "")
                                 or "claude-code-interactive"),
                    "observation_mode": getattr(
                        state, "observation_mode", "mitm"),
                })
        self._retire_stopped(stopped)
        return sessions

    def list_sessions_snapshot(self, user_id: str, conversation_id: str,
                               service_id: str = "") -> list[dict]:
        """Return in-memory sessions without probing Docker.

        Polling endpoints such as list_active must not run docker inspect for
        every warm session. Reuse/send paths still validate liveness before
        using a container; this snapshot is only UI status.
        """
        sessions: list[dict] = []
        now = time.time()
        with self._lock:
            candidates = [
                (key, state) for key, state in self._sessions.items()
                if key[0] == user_id
                and key[1] == conversation_id
                and (not service_id or key[3] == service_id)
            ]
            candidates.sort(key=lambda row: row[1].last_used, reverse=True)
            for key, state in candidates:
                sessions.append({
                    "agent_name": key[2],
                    "service_id": key[3],
                    "container_name": state.name,
                    "last_used": state.last_used,
                    "live": True,
                    "reuse_count": 1 if state.initial_context_loaded else 0,
                    "created_at": state.created_at,
                    "idle_seconds": max(0.0, now - state.last_used),
                    "lived_seconds": max(0.0, now - state.created_at),
                    "provider": (getattr(state, "provider", "")
                                 or "claude-code-interactive"),
                    "observation_mode": getattr(
                        state, "observation_mode", "mitm"),
                })
        return sessions

    def send_text(self, state: InteractiveContainer, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        # Cold-start race: the Claude Code TUI takes a moment to draw its
        # input box after `tmux new-session`. A paste + Enter that lands
        # before the box is interactive is silently dropped, leaving the
        # very first message unsent until a human presses Enter. Block the
        # first send until the TUI prompt is actually on screen. Subsequent
        # sends short-circuit on the flag (no capture cost in steady state),
        # and a fresh container gets a fresh state with prompt_ready=False.
        if not state.prompt_ready:
            if self._wait_for_prompt_ready(state.name):
                state.prompt_ready = True
            else:
                if self._REQUIRE_PROMPT_READY:
                    state.last_error = (
                        "TUI composer was not ready before the cold-start "
                        "readiness timeout")
                    logging.getLogger(__name__).error(
                        "[cci] refusing first send to %s because the TUI "
                        "composer is not ready%s",
                        state.name, self._pane_diagnostic(state.name))
                    return False
                # Best-effort: never make sends worse than before. Proceed
                # with the paste + double-Enter and hope the TUI catches up.
                logging.getLogger(__name__).warning(
                    "[cci] TUI prompt not detected ready before first send to "
                    "%s; submitting best-effort%s",
                    state.name, self._pane_diagnostic(state.name))
        self._cancel_copy_mode(state)
        if not self._prepare_prompt_input(state):
            return False
        # Once, not once per attempt: the guard that keeps hooks from filing
        # our own paste as a user message counts tickets, and a second record
        # for the same text would leave one unspent to swallow the next thing
        # the human really types.
        self._remember_injected_prompt(state, text)
        event_service = self._remember_injected_prompt_for_event_service(
            state, text)
        submit_marker = (0, 0)
        if event_service is not None:
            try:
                submit_marker = event_service.submission_marker(
                    state.session_token)
            except Exception:
                logging.getLogger(__name__).debug(
                    "Could not mark CCI prompt submission", exc_info=True)
                event_service = None
        # Load the whole prompt once and paste it once. Pane inspection is not
        # authoritative enough to replay a paste: a false negative would stack
        # the complete prompt in the composer. An inconclusive transport check
        # therefore fails visibly and leaves the single paste untouched.
        settle = self._paste_settle_seconds()
        before = self._pane_text(state.name)
        if not self._load_buffer(state, text):
            return False
        if not self._paste_buffer(state):
            return False
        # Let the TUI finish ingesting the paste before pressing Enter: an
        # Enter that lands inside the paste-detection window is treated as a
        # pasted newline (inserts a blank line) instead of submitting.
        if settle > 0:
            time.sleep(settle)
        if not self._paste_landed(state, text, before):
            state.last_error = "prompt was not confirmed after the single paste"
            logging.getLogger(__name__).error(
                "[cci] paste was not confirmed for %s; refusing to replay it%s",
                state.name, self._pane_diagnostic(state.name))
            return False
        delay = self._submit_delay_seconds()
        # Claude submits with a double Enter separated by a short wait. At container
        # restart the Claude Code TUI can drop the first Enter before its input
        # box is focused, leaving the pasted prompt unsent. The first Enter
        # submits in the normal case; the second guarantees submission after a
        # restart. An extra Enter on an already-submitted or empty prompt is a
        # no-op in the CC TUI.
        enter_count = max(1, int(self._INITIAL_SUBMIT_ENTERS))
        for index in range(enter_count):
            if not self.send_keys(state, ["Enter"]):
                return False
            if index + 1 < enter_count and delay > 0:
                time.sleep(delay)
        verified = self._verify_submitted(
            state, text, event_service=event_service,
            submit_marker=submit_marker)
        if verified is False:
            if not state.last_error:
                state.last_error = "prompt submission was not confirmed"
            # A prompt left in the input box would be stacked under the
            # caller's next paste. Only here, where the failure reaches the
            # caller: the interrupt verifier runs detached and must not
            # silently erase a preempt.
            if (self._CLEAR_STRANDED_ON_FAILED_SEND
                    and self._pane_holds_stranded_prompt(
                        self._pane_text(state.name),
                        self._submit_probe_fragment(text))):
                self._clear_stranded_prompt(state)
            return False
        return True

    # Footer/affordance strings the Claude Code TUI only renders once its
    # input box is interactive. Matching any (case-insensitive substring) is
    # a robust, version-tolerant readiness signal that does not depend on
    # box-drawing layout. The bypass-permissions footer is always present in
    # our `--dangerously-skip-permissions` configuration.
    _PROMPT_READY_MARKERS = (
        "for shortcuts",
        "shift+tab",
        "bypass permissions",
        "auto-accept edits",
    )

    def _pane_text(self, name: str) -> str:
        """Return the visible tmux pane text, or '' on any failure.

        Reads output solely to detect input-prompt readiness (transport
        concern); the provider still never parses tmux output to assemble a
        response.
        """
        try:
            r = subprocess.run(  # nosec B603
                docker_cmd() + ["exec", "--user", self._user_spec(), name,
                                "tmux", "capture-pane", "-p", "-t", "pawflow"],
                capture_output=True, text=True, timeout=10)
        except Exception:
            return ""
        return r.stdout or ""

    def _pane_shows_prompt(self, text: str) -> bool:
        low = (text or "").lower()
        return any(marker in low for marker in self._PROMPT_READY_MARKERS)

    # How much of the pane a failure carries into the log. Enough to hold the
    # header, the composer and the footer of a 50-line pane; short enough that
    # a stuck TUI cannot flood the log, and that the injected prompt below the
    # first screenful is not copied there wholesale.
    _PANE_DIAGNOSTIC_CHARS = 2000

    def _pane_diagnostic(self, name: str) -> str:
        """The screen a failure happened on, ready to append to its log line.

        Every check in this file reads the pane, and every failure used to
        report only its own verdict: "TUI prompt not detected ready", "paste
        did not reach the composer". Neither says what was actually drawn --
        so when a TUI release moves its footer or boxes its composer, the log
        records that our reading of the screen failed and never the screen,
        and the next person is left inferring the pane from a photograph of a
        terminal. The pane is the evidence; a failure should carry it.

        Best-effort by construction: it costs one extra capture on a path that
        has already failed, and an unreadable pane degrades to a note saying
        so rather than to an exception inside a warning.

        Note this puts the head of the pane in the server log, and on a fresh
        session that includes the beginning of the injected prompt. It is
        bounded, it only happens on failure, and the alternative has been
        guessing.
        """
        try:
            pane = self._pane_text(name)
        except Exception:
            return " [pane unreadable]"
        if not pane:
            return " [pane empty or unreadable]"
        limit = self._PANE_DIAGNOSTIC_CHARS
        head = pane[:limit]
        suffix = f" (+{len(pane) - limit} chars)" if len(pane) > limit else ""
        return f"; pane{suffix}:\n{head}"

    # Rendered by the CC TUI only while a turn is running — the strongest
    # version-tolerant signal that a submitted prompt was accepted.
    _RUNNING_MARKERS = ("esc to interrupt",)

    def _pane_shows_running(self, text: str) -> bool:
        low = (text or "").lower()
        return any(marker in low for marker in self._RUNNING_MARKERS)

    # How long to let the TUI finish ingesting a paste before pressing Enter.
    # Per-pool because the ingestion cost is the TUI's, not ours: a TUI that
    # buffers the whole paste into a single attachment needs longer than one
    # that echoes it character by character.
    _PASTE_SETTLE_DEFAULT = 0.2

    def _paste_settle_seconds(self) -> float:
        try:
            return max(0.0, float(os.environ.get(
                "PAWFLOW_CCI_PASTE_SETTLE_SECONDS", "")
                or self._PASTE_SETTLE_DEFAULT))
        except ValueError:
            return self._PASTE_SETTLE_DEFAULT

    def _submit_delay_seconds(self) -> float:
        try:
            return max(0.0, float(os.environ.get(
                "PAWFLOW_CCI_SUBMIT_DELAY_SECONDS", "")
                or self._SUBMIT_DELAY_DEFAULT))
        except ValueError:
            return self._SUBMIT_DELAY_DEFAULT

    # A TUI that collapses pasted text into a placeholder chip
    # ("[Pasted Content 24470 chars]") never shows the text itself, so the
    # probe-fragment heuristic below cannot see it and reads "gone from the
    # input box" as "submitted" — the exact opposite of the truth. Pools whose
    # TUI does that declare their chip here and the chip becomes the signal.
    # Empty means the TUI echoes pasted text verbatim (Claude Code): the
    # fragment heuristic stands unchanged.
    _PASTE_CHIP_MARKERS: tuple = ()
    # First characters of the TUI's input-box line, used to cut the pane down
    # to the composer. Without it a chip left in the *transcript* by an already
    # submitted message would read as an unsent one, and we would press Enter
    # forever. A tuple lists every prefix a TUI release may draw.
    _COMPOSER_PROMPT_PREFIX: "str | tuple" = ""

    def _composer_text(self, pane: str) -> str:
        """The input-box region of the pane: the last prompt line onward.

        Returns '' when the prompt line is not on screen — the caller treats
        that as "cannot tell", never as "empty composer".
        """
        prefixes = self._COMPOSER_PROMPT_PREFIX
        if not prefixes:
            return pane or ""
        if isinstance(prefixes, str):
            prefixes = (prefixes,)
        lines = (pane or "").splitlines()
        for idx in range(len(lines) - 1, -1, -1):
            if any(self._is_composer_line(lines[idx], prefix)
                   for prefix in prefixes):
                return "\n".join(lines[idx:])
        return ""

    @staticmethod
    def _is_composer_line(line: str, prefix: str) -> bool:
        """Is this the TUI's input-box line, or chrome that shares its prefix?

        `startswith(prefix)` alone is not enough. Codex's prefix is `>`, and
        its pane carries a PERMANENT header, `>_ OpenAI Codex (v...)`, which
        starts with it too. When the composer is off screen the backward scan
        then stops on that header and _composer_text() returns the whole
        transcript as "the composer" — so a chip left by an already-submitted
        message reads as an unsent paste, and the verifier presses Enter into
        a turn that is running.

        What separates them is the character right after the prefix: an input
        box is the prefix then a space (`> `, `> [Pasted Content ...]`) or
        nothing at all. The header glues punctuation to it.
        """
        stripped = line.lstrip()
        if not stripped.startswith(prefix):
            return False
        rest = stripped[len(prefix):]
        return rest == "" or rest[0].isspace()

    def _pane_holds_unsent_paste(self, pane: str):
        """Is the pasted prompt still sitting in the input box?

        Tri-state on purpose: True (chip in the composer), False (composer
        located and clean), None (this TUI has no chip, or the composer is not
        on screen) — None keeps the caller on the fragment heuristic.
        """
        if not self._PASTE_CHIP_MARKERS:
            return None
        composer = self._composer_text(pane)
        if not composer:
            return None
        low = composer.lower()
        return any(marker in low for marker in self._PASTE_CHIP_MARKERS)

    @staticmethod
    def _submit_probe_fragment(text: str) -> str:
        """A distinctive tail of the injected text to look for in the pane.

        Short enough to usually survive line wrapping, long enough not to
        match TUI chrome. Empty when no line is distinctive enough — the
        verifier then relies on the running marker alone.
        """
        for line in reversed((text or "").strip().splitlines()):
            line = line.strip()
            if len(line) >= 8:
                return line[-24:]
        return ""

    @staticmethod
    def _pane_squeeze(text: str) -> str:
        """Pane text with everything the TUI's layout added taken back out.

        `capture-pane` returns what is on the SCREEN, and a composer is not a
        text buffer: Claude Code draws a box around it and hard-wraps the
        prompt to the pane width, so a line of the injected text arrives as
        `\u2502 ... commun` / `ication ... \u2502` -- the wrap falls wherever the
        pane happens to end, mid-word as often as not. A verbatim search for a
        24-character tail therefore fails whenever that tail straddles a wrap,
        which is not a rare case but a function of the prompt's length and the
        terminal's width.

        Removing the borders and ALL whitespace from both sides makes the
        search indifferent to where the TUI chose to break the line. It does
        not make it less specific: a 24-character tail of the prompt is no
        likelier to appear in chrome squeezed than spaced.
        """
        out = []
        for ch in (text or ""):
            if ch.isspace() or ch in "\u2502\u2551|":
                continue
            out.append(ch)
        return "".join(out)

    @classmethod
    def _fragment_on_pane(cls, pane: str, fragment: str) -> bool:
        """Is the probe fragment on the pane, however the TUI wrapped it?"""
        if not fragment:
            return False
        return cls._pane_squeeze(fragment) in cls._pane_squeeze(pane)

    # How long to watch for the paste to appear in the composer before
    # reporting an inconclusive single paste. Longer than the settle because a
    # TUI busy drawing itself can ingest a multi-kilobyte buffer slowly.
    _PASTE_LANDED_SECONDS = 3.0

    def _paste_landed(self, state: InteractiveContainer, text: str,
                      before_pane: str = "") -> bool:
        """Did the paste actually reach the input box?

        The question is answered first by the pane itself, then by what we can
        recognise in it:

        - the pane is no longer what it was just before the paste: it landed;
        - the chip is in the composer, or the probe fragment is on the pane:
          it landed;
        - the turn is already running: it landed and was accepted between the
          paste and this look;
        - the pane is readable and none of the above: not an answer yet. Keep
          looking until the window closes, then call it a failure.

        The before/after comparison leads because it is the only test that
        does not model the TUI. Every recognition-based test does: the chip
        probe assumes the composer can be located by its prompt prefix, the
        fragment probe assumes the pasted text is rendered as text and
        findable across the layout the TUI wrapped it in. Both assumptions
        are a moving target -- they are read off a TUI that ships a new
        version every few weeks -- so a failed check must never trigger a
        second paste into a composer that may already hold the whole prompt.
        Whether the screen changed is a fact about the screen. A TUI that
        renames its chip, boxes its composer or re-wraps its text still
        redraws when 16 kB arrive.

        A composer that cannot be located used to return True immediately,
        and it accepted the one
        case this check exists for. `>_ OpenAI Codex (v0.104.0)` is drawn the
        instant the TUI starts and the input box is not: a pane holding only
        the header locates no composer, so a paste into a TUI that has no
        input box yet -- past the readiness timeout, or mid-redraw -- was
        declared landed, Enter went nowhere, and the turn waited out its 300s
        no-event timeout. That case is still a refusal -- an unchanged pane
        is not evidence of anything -- and it is now reached by the pane
        comparison rather than by recognising an empty box.

        What the comparison costs: a pane that redraws for its own reasons
        (a footer that ticks, a spinner winding down) reads as a landed
        paste. That direction is the cheap one. A false "landed" presses
        Enter into a box that may be empty, which is a no-op, and the turn
        then fails visibly on the no-event timeout with the session left
        clean. A false "missing" pastes the whole prompt a second time into a
        composer that already holds it, and what the user gets is a turn that
        cannot be submitted at all because the input box now contains the
        duplicated. Those are not symmetrical, and the transport must never
        replay the paste on visual evidence alone.

        The fragment is still looked for across the whole pane, and on a TUI
        that echoes pasted text that cannot tell this paste from the same text
        still visible in the transcript. Every way of narrowing it costs a
        false negative, for the reason above. It stays as it is.

        It is looked for on the SQUEEZED pane, though. The pane is a screen,
        not a buffer: the composer hard-wraps the prompt at the pane width,
        and a tail that falls across a wrap is not on the pane verbatim even
        though every character of it is. That failure is deterministic -- the
        same prompt wraps at the same column on every check -- so a prompt
        sitting in the composer, needing only Enter, could be declared
        missing. `_verify_submitted` keeps the verbatim test on purpose:
        there "fragment absent" means "submitted", so a wrap-blinded match
        errs toward leaving a running turn alone, while a squeezed one would
        keep finding the prompt Claude Code echoes into its own transcript.

        Reads the pane purely as transport signal -- the provider still never
        assembles a response from tmux output.
        """
        fragment = self._submit_probe_fragment(text)
        if not before_pane and not self._PASTE_CHIP_MARKERS and not fragment:
            # Nothing to look for: this TUI leaves no chip and the text is too
            # short to probe for. Unknowable, so it must not become a refusal.
            return True
        deadline = time.time() + max(0.0, self._PASTE_LANDED_SECONDS)
        interval = 0.3
        while True:
            pane = self._pane_text(state.name)
            if not pane:
                # The pane could not be read at all -- also unknowable.
                return True
            if before_pane and pane != before_pane:
                # The screen moved. Something arrived; only the paste did.
                return True
            if self._pane_holds_unsent_paste(pane):
                return True
            if self._fragment_on_pane(pane, fragment):
                return True
            if self._pane_shows_running(pane):
                # Already accepted between the paste and this look.
                return True
            if time.time() >= deadline:
                return False
            time.sleep(interval)

    def _verify_submitted(self, state: InteractiveContainer, text: str, *,
                          event_service=None,
                          submit_marker=(0, 0)):
        """Post-submit check with spaced Enter retries.

        Despite the settle delay, an Enter can still be coalesced into the
        paste burst and inserted as a literal newline, leaving the message
        sitting in the input box (forcing a human to press Enter in the
        tmux — the bug this guards against).

        With an event service the exact UserPromptSubmit receipt (or the
        MITM model request) is the proof, see _verify_submitted_by_receipt.
        Without one (isolated diagnostics/tests) poll the pane: a running
        marker means the prompt was accepted; an idle prompt with the pasted
        text still visible means the Enter was swallowed — press it again.
        """
        try:
            window = float(os.environ.get(
                "PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS", "6.0") or "6.0")
        except ValueError:
            window = 6.0
        if window <= 0:
            return True
        fragment = self._submit_probe_fragment(text)
        if event_service is not None:
            return self._verify_submitted_by_receipt(
                state, text, fragment, window, event_service, submit_marker)
        interval = 0.3
        # An Enter inside the TUI's paste-detection window is a pasted
        # newline, not a submit: three retries 0.3 s apart (observed
        # 2026-09-23) all landed in it. Space them by the submit delay.
        retry_gap = max(interval, self._submit_delay_seconds())
        last_retry_at = 0.0
        polls = max(1, int(window / interval))
        retries = 0
        log = logging.getLogger(__name__)
        for _ in range(polls):
            pane = self._pane_text(state.name)
            if pane:
                holds = self._pane_holds_unsent_paste(pane)
                if holds:
                    # The chip is authoritative: the prompt is in the input
                    # box, whatever else the pane shows. Press Enter again.
                    if retries >= 3:
                        break
                    if time.time() - last_retry_at < retry_gap:
                        time.sleep(interval)
                        continue
                    retries += 1
                    last_retry_at = time.time()
                    log.warning(
                        "[cci] pasted prompt still in the input box of %s; "
                        "pressing Enter again (retry %d)", state.name, retries)
                    if not self.send_keys(state, ["Enter"]):
                        break
                    time.sleep(interval)
                    continue
                if self._pane_shows_running(pane):
                    if holds is False or not fragment or fragment not in pane:
                        return True
                    # Running but our text is still on screen: either the
                    # interrupted OLD turn is winding down, or the TUI echoes
                    # the submitted prompt. Keep polling — never press Enter
                    # while a turn is running.
                    time.sleep(interval)
                    continue
                if holds is False:
                    # Composer located and free of the chip: submitted.
                    return True
                if fragment and fragment not in pane:
                    # Input box no longer holds the prompt: submitted.
                    return True
                if self._pane_shows_prompt(pane):
                    if retries >= 3:
                        break
                    if time.time() - last_retry_at < retry_gap:
                        time.sleep(interval)
                        continue
                    retries += 1
                    last_retry_at = time.time()
                    log.warning(
                        "[cci] pasted prompt still unsubmitted in %s; "
                        "pressing Enter again (retry %d)", state.name, retries)
                    if not self.send_keys(state, ["Enter"]):
                        break
            time.sleep(interval)
        if retries:
            log.warning(
                "[cci] submit verification inconclusive for %s after %d "
                "Enter retries", state.name, retries)
        return None

    _MAX_SUBMIT_ENTER_RETRIES = 3
    # Keys that empty the Claude Code input box without opening its rewind
    # menu: the two spaces make it non-empty first, because a double Esc on
    # an EMPTY input box opens the message selector instead of clearing.
    _CLEAR_INPUT_KEYS = ["Space", "Space", "Escape", "Escape",
                         "BSpace", "BSpace"]
    _PANE_TAIL_DIAGNOSTIC_CHARS = 1200
    _CLEAR_STRANDED_ON_FAILED_SEND = True

    def _pane_holds_stranded_prompt(self, pane: str, fragment: str) -> bool:
        """Is our prompt visibly sitting unsent in an idle TUI?"""
        if not pane or self._pane_shows_running(pane):
            return False
        holds = self._pane_holds_unsent_paste(pane)
        if holds is not None:
            return holds
        return bool(fragment) and fragment in pane and self._pane_shows_prompt(pane)

    def _pane_tail_diagnostic(self, pane: str) -> str:
        """The bottom of the pane -- the input box and footer -- for a log."""
        if not pane:
            return " [pane empty or unreadable]"
        return (f"; pane tail:\n"
                f"{pane.rstrip()[-self._PANE_TAIL_DIAGNOSTIC_CHARS:]}")

    def _verify_submitted_by_receipt(self, state: InteractiveContainer,
                                     text: str, fragment: str, window: float,
                                     event_service, submit_marker):
        """Prove the submit with the UserPromptSubmit receipt.

        Incident 2026-09-23: a prompt stayed in the input box after three
        Enter retries 0.3 s apart. The check returned "inconclusive", the
        send was reported successful, and the turn waited on a CLI that had
        never received it until the idle-pane probe failed it a minute later
        ("went back to its prompt without answering"). The stranded text was
        then still in the input box for the next prompt to land on.

        Now the receipt decides. Enter is retried only while the prompt is
        visibly stranded in an idle TUI, at most three times, one submit
        delay apart. If the window closes with no receipt and the prompt
        still stranded, the send fails now (and send_text empties the input
        box, so the next prompt is not stacked onto this one).
        """
        log = logging.getLogger(__name__)
        after_submit, after_request = submit_marker
        retry_gap = max(0.3, self._submit_delay_seconds())
        deadline = time.monotonic() + window
        retries = 0
        saw_other = False
        pane = ""
        while True:
            remaining = deadline - time.monotonic()
            proof = event_service.wait_for_prompt_submission(
                state.session_token, text,
                after_submit=after_submit, after_request=after_request,
                timeout=max(0.0, min(retry_gap, remaining)))
            if proof in ("hook", "request"):
                return True
            if proof == "fragment":
                state.last_error = (
                    "Claude Code submitted only a fragment of the pasted prompt")
                log.error("[cci] fragmented prompt submission for %s",
                          state.name)
                return False
            if proof == "other" and not saw_other:
                # Enter submitted something else first (a stale or typed
                # prompt). Wait for ours past that receipt.
                saw_other = True
                try:
                    after_submit, after_request = (
                        event_service.submission_marker(state.session_token))
                except Exception:
                    log.debug("[cci] could not refresh the submission marker",
                              exc_info=True)
                log.warning("[cci] a different prompt was submitted in %s; "
                            "still waiting for ours", state.name)
            pane = self._pane_text(state.name)
            stranded = self._pane_holds_stranded_prompt(pane, fragment)
            if time.monotonic() >= deadline:
                break
            if stranded and retries < self._MAX_SUBMIT_ENTER_RETRIES:
                retries += 1
                log.warning(
                    "[cci] pasted prompt still unsubmitted in %s; pressing "
                    "Enter again (retry %d)", state.name, retries)
                if not self.send_keys(state, ["Enter"]):
                    return False
        if self._pane_holds_stranded_prompt(pane, fragment):
            state.last_error = (
                "prompt stayed in the Claude Code input box after "
                f"{retries} Enter retries and no UserPromptSubmit arrived")
            log.error("[cci] prompt not submitted in %s after %d Enter "
                      "retries%s", state.name, retries,
                      self._pane_tail_diagnostic(pane))
            return False
        log.warning(
            "[cci] no submission receipt for %s within %.1fs after %d Enter "
            "retries; the input box no longer shows the prompt%s",
            state.name, window, retries, self._pane_tail_diagnostic(pane))
        return None

    def _clear_stranded_prompt(self, state: InteractiveContainer) -> None:
        """Empty the input box, keeping the send's own last_error."""
        error = state.last_error
        try:
            self.send_keys(state, list(self._CLEAR_INPUT_KEYS))
        except Exception:
            logging.getLogger(__name__).debug(
                "[cci] could not clear the input box", exc_info=True)
        state.last_error = error

    def _wait_for_prompt_ready(self, name: str, *,
                               timeout: Optional[float] = None) -> bool:
        """Block until the Claude Code TUI input prompt is on screen.

        Polls the pane until a readiness marker appears. Returns True when
        ready, False if the timeout elapses first (caller proceeds best-
        effort). A non-positive timeout performs a single probe so callers
        and tests can opt out of waiting.
        """
        if timeout is None:
            try:
                timeout = float(os.environ.get(
                    "PAWFLOW_CCI_PROMPT_READY_TIMEOUT_SECONDS", "45") or "45")
            except ValueError:
                timeout = 45.0
        deadline = time.time() + max(0.0, timeout)
        while True:
            if self._pane_shows_prompt(self._pane_text(name)):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.4)

    def _cancel_copy_mode(self, state: InteractiveContainer) -> None:
        try:
            subprocess.run(  # nosec B603
                docker_cmd() + ["exec", "--user", self._user_spec(), state.name,
                                "tmux", "send-keys", "-t", "pawflow", "-X", "cancel"],
                capture_output=True, timeout=5)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def _prepare_prompt_input(self, state: InteractiveContainer) -> bool:
        """Prepare the TUI before pasting a prompt.

        Claude Code needs no extra key sequence. Provider-specific pools may
        override this hook when their TUI requires one.
        """
        return True

    def _load_buffer(self, state: InteractiveContainer, text: str) -> bool:
        cmd = docker_cmd() + [
            "exec", "-i", "--user", self._user_spec(), state.name,
            "tmux", "load-buffer", "-",
        ]
        r = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=15)  # nosec B603
        if r.returncode != 0:
            state.last_error = self._command_error("tmux load-buffer", r)
            return False
        return True

    def _paste_buffer(self, state: InteractiveContainer) -> bool:
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), state.name,
                            "tmux", "paste-buffer", "-p", "-t", "pawflow"],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux paste-buffer", r)
            return False
        return True

    @staticmethod
    def _remember_injected_prompt(state: InteractiveContainer, text: str) -> None:
        """Record PawFlow-injected tmux prompts so hooks can ignore them."""
        try:
            marker_dir = Path(state.workdir) / ".pawflow_cci"
            marker_dir.mkdir(parents=True, exist_ok=True)
            marker = marker_dir / "injected_prompts.jsonl"
            payload = {
                "sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
                "length": len(text or ""),
                "ts": time.time(),
                # The hook runs in a separate process and the in-memory event
                # service can disappear across stop/compact.  Keep the
                # whitespace-normalised text here so that process can still
                # recognise and consume a TUI-split piece of our paste.
                "remaining": " ".join((text or "").split()),
            }
            with open(marker, "a", encoding="utf-8") as fh:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    @staticmethod
    def _remember_injected_prompt_for_event_service(state: InteractiveContainer,
                                                    text: str):
        try:
            from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service
            _, _, event_service = get_or_create_cc_interactive_event_service()
            event_service.remember_injected_prompt(state.session_token, text or "")
            return event_service
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            return None

    def send_interrupt(self, state: InteractiveContainer, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        self._cancel_copy_mode(state)
        canonical_input = self._PREPARE_INTERRUPT_BEFORE_PASTE
        if canonical_input and not self._prepare_prompt_input(state):
            return False
        if canonical_input:
            # Escape is asynchronous in the Codex TUI. A pane redraw caused by
            # cancelling the old turn is not evidence that the new paste reached
            # an input box. Wait for the structural editable-composer signal
            # before loading anything; on timeout the caller keeps the pending
            # rescue queued and replays it after the old turn releases ownership.
            if not self._wait_for_prompt_ready(state.name):
                state.last_error = "TUI composer was not ready after interrupt"
                logging.getLogger(__name__).warning(
                    "[cci] refusing interrupt paste to %s because the TUI "
                    "composer was not ready after interrupt%s",
                    state.name, self._pane_diagnostic(state.name))
                return False
            state.prompt_ready = True
        self._remember_injected_prompt(state, text)
        event_service = self._remember_injected_prompt_for_event_service(
            state, text)
        submit_marker = (0, 0)
        if event_service is not None:
            try:
                submit_marker = event_service.submission_marker(
                    state.session_token)
            except Exception:
                logging.getLogger(__name__).debug(
                    "Could not mark CCI interrupt submission", exc_info=True)
                event_service = None
        if not (self._load_buffer(state, text) and self._paste_buffer(state)):
            return False
        if not canonical_input and not self.send_keys(state, ["Escape"]):
            return False
        # Codex has already performed Esc, Esc before the paste. Claude Code
        # keeps its historical paste, Escape, settle, Enter sequence.
        settle = self._paste_settle_seconds()
        if settle > 0:
            time.sleep(settle)
        enter_count = (max(1, int(self._INITIAL_SUBMIT_ENTERS))
                       if canonical_input else 1)
        delay = self._submit_delay_seconds()
        for index in range(enter_count):
            if not self.send_keys(state, ["Enter"]):
                return False
            if index + 1 < enter_count and delay > 0:
                time.sleep(delay)
        if not canonical_input and settle > 0:
            time.sleep(settle)
        # _verify_submitted polls the tmux pane / submission receipts for up to
        # PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS (default 6s). It is best-effort and
        # its result is unused for Claude Code, yet send_interrupt runs on the
        # HTTP request thread
        # (POST /api/agent -> send_user_message -> send_interrupt), so running
        # it inline made every preempt block ~6-8s before the ack returned.
        # Codex must prove the preempt before the caller marks its rescue as
        # handled.  Otherwise a swallowed Enter returns True, `_had_preempts`
        # suppresses the rescue at final drain, and the message appears only
        # after the old turn's done (or is lost).  Claude Code retains the
        # background verifier to preserve its existing HTTP latency.
        if self._VERIFY_INTERRUPT_SYNCHRONOUS:
            verified = self._verify_submitted(
                state, text, event_service=event_service,
                submit_marker=submit_marker)
            if verified is False:
                if not state.last_error:
                    state.last_error = "interrupt prompt submission was not confirmed"
                return False
            return True
        threading.Thread(
            target=self._verify_submitted, args=(state, text),
            kwargs={"event_service": event_service,
                    "submit_marker": submit_marker},
            name="cci-verify-submit", daemon=True,
        ).start()
        return True

    def force_stop(self, state: InteractiveContainer) -> bool:
        return self.send_keys(state, list(self._CLEAR_INPUT_KEYS))

    def send_keys(self, state: InteractiveContainer, keys: list[str]) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), state.name,
                            "tmux", "send-keys", "-t", "pawflow", *keys],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux send-keys", r)
            return False
        return True

    @staticmethod
    def _command_error(label: str, result) -> str:
        stderr = getattr(result, "stderr", b"") or b""
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        detail = (stderr or stdout or "").strip()
        if detail:
            return f"{label} failed: {detail[:500]}"
        return f"{label} failed with exit code {getattr(result, 'returncode', '?')}"

    def kill_session(self, user_id: str, conversation_id: str,
                     agent_name: str, service_id: str = "") -> bool:
        key = (user_id, conversation_id, agent_name, service_id or "")
        with self._lock:
            state = self._sessions.pop(key, None)
        if not state:
            return False
        self._recover_container_tokens(state)
        self._kill_container(state.name)
        self._unregister_event_session(state)
        return True

    def destroy_ephemeral(self, state: InteractiveContainer) -> None:
        """Destroy one ephemeral interactive session and its runtime directory."""
        if state is None:
            return
        with self._lock:
            if self._sessions.get(state.key) is state:
                self._sessions.pop(state.key, None)
        self._recover_container_tokens(state)
        self._kill_container(state.name)
        try:
            from services.cc_interactive_event_service import (
                get_or_create_cc_interactive_event_service)
            get_or_create_cc_interactive_event_service()[2].unregister_session(
                state.session_token)
        except Exception:
            logger.debug("[cci-ephemeral] event session cleanup failed",
                         exc_info=True)
        if state.internal_token:
            try:
                from core.internal_auth import revoke_token
                revoke_token(state.internal_token)
            except Exception:
                logger.debug("[cci-ephemeral] internal token revoke failed",
                             exc_info=True)
        try:
            root = Path(self._session_root()).resolve()
            workdir = Path(state.workdir).resolve()
            if workdir == root or root not in workdir.parents:
                raise ValueError(
                    f"ephemeral workdir escapes session root: {workdir}")
            stale = workdir.with_name(
                f".stale-ephemeral-{workdir.name}-{uuid.uuid4().hex[:8]}")
            if workdir.exists():
                workdir.replace(stale)
                shutil.rmtree(stale, ignore_errors=True)
            parent = workdir.parent
            while parent != root and root in parent.parents:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
            logger.info("[cci-ephemeral] destroyed session runtime: %s",
                        workdir)
        except Exception:
            logger.debug("[cci-ephemeral] runtime cleanup failed: %s",
                         state.workdir, exc_info=True)

    def kill_and_evict_by_session_token(self, session_token: str, reason: str) -> int:
        """Retire one observed session without touching a replacement's key."""
        with self._lock:
            victim = next(((key, state) for key, state in self._sessions.items()
                           if state.session_token == session_token), None)
            if victim is None:
                return 0
            key, state = victim
            self._sessions.pop(key)
        logger.info("[cci-live] kill_by_session %s (%s)", self._fmt_key(key), reason)
        self._recover_container_tokens(state)
        self._unregister_event_session(state)
        self._kill_container(state.name)
        return 1

    def kill_and_evict_by_conv(self, conv_id: str, reason: str) -> int:
        """Kill live containers for every interactive session in a conversation."""
        with self._lock:
            victims = [(key, state) for key, state in self._sessions.items()
                       if key[1] == conv_id]
            for key, _state in victims:
                self._sessions.pop(key, None)
        for key, state in victims:
            logger.info("[cci-live] kill_by_conv %s (%s)",
                        self._fmt_key(key), reason)
            self._recover_container_tokens(state)
            self._unregister_event_session(state)
            self._kill_container(state.name)
        return len(victims)

    def kill_and_evict_by_conv_agent(self, conv_id: str, agent_name: str,
                                      reason: str) -> int:
        """Kill live containers for one interactive (conversation, agent) pair."""
        with self._lock:
            victims = [(key, state) for key, state in self._sessions.items()
                       if key[1] == conv_id and key[2] == agent_name]
            for key, _state in victims:
                self._sessions.pop(key, None)
        for key, state in victims:
            logger.info("[cci-live] kill_by_conv_agent %s (%s)",
                        self._fmt_key(key), reason)
            self._recover_container_tokens(state)
            self._unregister_event_session(state)
            self._kill_container(state.name)
        return len(victims)

    def find_live_by_conv_agent(self, conv_id: str,
                                agent_name: str) -> Optional[InteractiveContainer]:
        """Return the live container for one (conversation, agent) pair.

        Used to reach a tmux session that is working outside any streaming
        worker — a captured turn — so a webchat message can still be typed
        into it instead of being parked in the PendingQueue.
        """
        with self._lock:
            for key, state in self._sessions.items():
                if key[1] == conv_id and key[2] == agent_name:
                    if self._is_alive(state.name):
                        return state
        return None

    def ensure_sweeper(self, tick_seconds: int = 60,
                       idle_ttl_seconds: Optional[int] = None) -> None:
        # 0 is a VALUE, not "unset": the service asked for no timeout, so
        # nothing may reap its containers. Only None means "no opinion --
        # keep whatever the pool already has".
        if idle_ttl_seconds is not None:
            if idle_ttl_seconds <= 0:
                self._idle_ttl = 0.0
            elif self._idle_ttl > 0:
                self._idle_ttl = max(self._idle_ttl, float(idle_ttl_seconds))
        self._tick_seconds = max(1, int(tick_seconds or 60))
        if self._sweeper_started:
            return
        self._sweeper_started = True

        def _loop():
            while not self._sweeper_stop.wait(self._tick_seconds):
                try:
                    self.sweep_idle()
                except Exception:
                    logger.debug("[cci-live] sweeper tick failed", exc_info=True)

        threading.Thread(target=_loop, daemon=True, name="cci-live-sweeper").start()

    def _activity_at(self, state: InteractiveContainer) -> float:
        """Most recent evidence that this session is alive and working.

        ``last_used`` only moves when a PawFlow streaming worker drives the
        turn and calls ``touch``. Claude Code also resumes on its own -- a
        backgrounded task reporting back, a queued message -- and those turns
        go through the MITM proxy without any PawFlow coordinator attached,
        so ``last_used`` froze while the session was demonstrably working and
        the sweeper evicted it mid-flight. The proxy's ``last_event_at`` is
        the signal that does not depend on PawFlow's turn bookkeeping.
        """
        last = float(getattr(state, "last_used", 0.0) or 0.0)
        try:
            from services.cc_interactive_event_service import (
                get_or_create_cc_interactive_event_service)
            events = get_or_create_cc_interactive_event_service()[2]
            sess = events.session_state(state.session_token)
        except Exception:
            logger.debug("[cci-live] event-service probe failed for %s",
                         state.name, exc_info=True)
            return last
        if sess is None:
            return last
        return max(last, float(getattr(sess, "last_event_at", 0.0) or 0.0))

    def sweep_idle(self, idle_ttl_seconds: Optional[float] = None) -> int:
        ttl = float(idle_ttl_seconds if idle_ttl_seconds is not None else self._idle_ttl)
        # ttl <= 0 disables idle eviction entirely; a dead container is still
        # evicted, because that is bookkeeping, not reaping.
        cutoff = (time.time() - ttl) if ttl > 0 else None
        to_kill: list[tuple[InteractiveContainer, str]] = []
        with self._lock:
            snapshot = list(self._sessions.items())
        dead: Dict[tuple[str, str, str, str], bool] = {}
        activity: Dict[tuple[str, str, str, str], float] = {}
        # Both probes are round trips (docker, then the event service's own
        # lock) — keep them OUTSIDE the pool lock, like the liveness probe.
        for key, state in snapshot:
            dead[key] = not self._is_alive(state.name)
            activity[key] = self._activity_at(state)
        with self._lock:
            for key, state in snapshot:
                current = self._sessions.get(key)
                if current is not state:
                    continue
                reason = ""
                if dead.get(key):
                    reason = "dead_container"
                elif current.in_flight > 0:
                    # A turn is running: the CLI may be busy with a local
                    # tool and emitting nothing for minutes. Never idle.
                    reason = ""
                elif cutoff is not None and activity.get(
                        key, current.last_used) < cutoff:
                    reason = f"idle>{int(ttl)}s"
                if reason:
                    self._sessions.pop(key, None)
                    to_kill.append((current, reason))
        for state, reason in to_kill:
            logger.info("[cci-live] evict %s (%s)", state.name, reason)
            self._recover_container_tokens(state)
            self._kill_container(state.name)
            self._unregister_event_session(state)
        # The sessions that survive this tick are exactly the ones whose
        # tokens nothing else will rescue: teardown is the only other moment
        # recovery runs, and a server killed hard -- an update whose stop
        # grace expires -- never reaches it. The CLI rotates its own
        # single-use refresh_token in-container, so a copy that never happens
        # leaves the pool holding a token the next launch cannot refresh.
        # recover_tokens_from_workdir skips a token it has already copied,
        # so a tick where nothing rotated writes nothing.
        with self._lock:
            survivors = list(self._sessions.values())
        for state in survivors:
            self._recover_container_tokens(state)
        # A container whose removal failed on an earlier tick is still running
        # a CLI no pool entry points at. Retry it here, where the failure is
        # cheap, instead of leaving it to adopt orphan turns for hours.
        self._retry_pending_kills()
        return len(to_kill)

    def shutdown_all(self) -> None:
        self._sweeper_stop.set()
        with self._lock:
            states = list(self._sessions.values())
            self._sessions.clear()
        # Every token first, then every kill. Interleaving them meant a
        # shutdown that ran out of time -- `docker rm -f` is up to 15s per
        # container inside Docker's 10s SIGTERM grace -- was SIGKILLed
        # part-way through, and every session after the cut point lost the
        # token its container had rotated. Recovery is a couple of file
        # operations; it costs nothing to finish it before the slow part.
        for state in states:
            self._recover_container_tokens(state)
        for state in states:
            self._kill_container(state.name)
        for state in states:
            self._unregister_event_session(state)

    @staticmethod
    def _unregister_event_session(state: InteractiveContainer) -> None:
        """Drop the dead container's event-service session.

        Sessions were never unregistered outside the ephemeral path, so a
        long-lived server accumulated one CCInteractiveSessionEvents per
        container it ever ran — a slow leak plus O(all-sessions) scans in
        publish_agent_event.
        """
        token = getattr(state, "session_token", "")
        if not token:
            return
        try:
            from services.cc_interactive_event_service import (
                get_or_create_cc_interactive_event_service)
            get_or_create_cc_interactive_event_service()[2].unregister_session(
                token)
        except Exception:
            logger.debug("[cci-live] event session cleanup failed",
                         exc_info=True)

    def _kill_container(self, name: str) -> bool:
        """Remove one container, and SAY whether it is really gone.

        `docker rm -f` does fail -- a daemon busy with twenty CLI containers
        and a game engine, a kill whose exit event never arrives -- and every
        eviction path here pops the pool entry BEFORE killing. Ignoring the
        exit status therefore turned one failed removal into a permanent,
        invisible leak: the pool no longer knew the session (the webchat
        answered "No live interactive tmux session"), the next turn launched
        yet another container, and the abandoned CLI kept answering into a
        stream nobody read -- holding Active Agents until an orphan capture
        finally adopted it hours later. A failed removal is now logged and
        queued for `_retry_pending_kills`.

        The timeout is _KILL_TIMEOUT_SECONDS, not 15: removals measured
        10.2s each (2026-09-23, six containers) because the pool's AppArmor
        profile denied the container SIGKILL from unconfined runc/dockerd --
        fixed in docker/apparmor/pawflow-mount, where a removal is back to
        ~0.16s. A client killed by the timeout drops the request the daemon
        is serving, which is how a removal could be lost without a trace.

        Returns True when the container is gone (or was never there).
        """
        if not name:
            return True
        started = time.time()
        try:
            result = subprocess.run(  # nosec B603
                docker_cmd() + ["rm", "-f", name],
                capture_output=True, text=True,
                timeout=self._KILL_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning("[cci-live] docker rm -f %s did not answer after "
                           "%.1fs (%s); queued for retry",
                           name, time.time() - started, exc)
            with self._lock:
                self._pending_kills.add(name)
            return False
        elapsed = time.time() - started
        stderr = (result.stderr or "").strip()
        if result.returncode == 0 or "no such container" in stderr.lower():
            # ~0.16s is the normal cost. A removal that takes seconds means
            # the container is not accepting SIGKILL -- the AppArmor
            # signal-receive regression, or a daemon in trouble -- so the
            # duration is reported instead of assumed.
            if elapsed >= _SLOW_KILL_SECONDS:
                logger.warning("[cci-live] docker rm -f %s took %.1fs",
                               name, elapsed)
            else:
                logger.debug("[cci-live] docker rm -f %s took %.1fs",
                             name, elapsed)
            with self._lock:
                self._pending_kills.discard(name)
            return True
        logger.warning("[cci-live] docker rm -f %s failed (exit %s: %s); "
                       "queued for retry", name, result.returncode,
                       stderr[:200])
        with self._lock:
            self._pending_kills.add(name)
        return False

    def _retry_pending_kills(self) -> int:
        """Re-kill the containers whose removal failed on an earlier tick.

        Called from the sweeper. A container that is no longer running is
        dropped from the queue: `_is_alive` answers False only when docker
        said so, so a probe that cannot answer keeps the retry armed instead
        of forgetting a live orphan.
        """
        with self._lock:
            names = sorted(self._pending_kills)
        retried = 0
        for name in names:
            if not self._is_alive(name):
                with self._lock:
                    self._pending_kills.discard(name)
                continue
            retried += 1
            self._kill_container(name)
        return retried

    @staticmethod
    def _fmt_key(key: tuple[str, str, str, str]) -> str:
        user_id, conv_id, agent_name, service_id = key
        return (
            f"{user_id[:6] or '?'}/{conv_id[:8] or '?'}/"
            f"{agent_name or 'default'}@{service_id or 'default'}"
        )
