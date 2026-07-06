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
import subprocess  # nosec B404
import threading
import time
import logging

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
    "CronCreate,CronDelete,CronList,AskUserQuestion,Monitor,"
    "ScheduleWakeup,PushNotification"
)




class InteractiveClaudeCodePool(_InteractiveContainerSpawnMixin):
    _instance: Optional["InteractiveClaudeCodePool"] = None
    _instance_lock = threading.Lock()

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
        # (service_id, pool_index) slots reserved by an in-flight _start_new —
        # a slot has been claimed but the container is not yet registered in
        # _sessions. Lets two concurrent ensure_started calls for different
        # conversations claim DISTINCT credential slots, so they never share a
        # single-use OAuth refresh_token (Anthropic rotates it on each refresh).
        self._reserved_slots: set = set()
        self._sweeper_started = False
        self._sweeper_stop = threading.Event()
        self._tick_seconds = 60
        self._idle_ttl = float(os.environ.get("PAWFLOW_CCI_IDLE_TTL_SECONDS", "1800"))
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
        return (value or "").replace(":", "_").replace("/", "_").replace("\\", "_")

    @staticmethod
    def _container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions/{}/{}".format(
            (conversation_id or "").replace(":", "_"),
            agent_name,
        )

    @staticmethod
    def _physical_container_workdir(user_id: str, conversation_id: str, agent_name: str) -> str:
        return "/cc_sessions_host/{}/{}/{}".format(
            InteractiveClaudeCodePool._safe(user_id),
            (conversation_id or "").replace(":", "_"),
            agent_name,
        )

    def ensure_started(self, client, model: str, user_id: str,
                       conversation_id: str, agent_name: str) -> InteractiveContainer:
        idle_ttl = getattr(client, "timeout", None)
        self.ensure_sweeper(idle_ttl_seconds=int(idle_ttl) if idle_ttl else None)
        service_id = getattr(client, "_agent_service", "") or ""
        key = (user_id, conversation_id, agent_name, service_id)
        with self._lock:
            existing = self._sessions.get(key)
            if existing and self._is_alive(existing.name):
                existing.last_used = time.time()
                return existing
            if existing:
                self._sessions.pop(key, None)
            # Claim an exclusive credential slot BEFORE spawning: Anthropic
            # refresh_tokens are single-use, so two concurrent containers on one
            # slot race and invalidate the loser. Reserved under the lock so a
            # concurrent ensure_started for another conversation sees it taken.
            api_key = getattr(client, "api_key", "")
            if callable(api_key):
                api_key = api_key()
            elif isinstance(api_key, property):
                api_key = ""
            claimed_idx = -1 if api_key else self._claim_pool_slot_locked(
                service_id, user_id, conversation_id)
        try:
            state = self._start_new(client, model, user_id, conversation_id,
                                    agent_name, key, pool_index=claimed_idx)
        except Exception:
            # Spawn failed — release the reservation so the slot is reusable.
            with self._lock:
                self._reserved_slots.discard((service_id, claimed_idx))
            raise
        with self._lock:
            self._sessions[key] = state
            # Now tracked via _sessions; drop the in-flight reservation.
            self._reserved_slots.discard((service_id, claimed_idx))
        return state

    def touch(self, state: InteractiveContainer) -> None:
        with self._lock:
            state.last_used = time.time()

    def _claim_pool_slot_locked(self, service_id: str, user_id: str,
                                conversation_id: str) -> int:
        """Pick a credential slot not used by any live/reserved container.

        MUST be called holding self._lock. Returns the slot index. Raises
        LLMClientError when no credentials are configured, or when every slot is
        busy — the hard cap that enforces 1 login = 1 concurrent container
        (Anthropic refresh_tokens are single-use, so sharing a slot across two
        live containers races and invalidates the loser's session).
        """
        from core.llm_client import LLMClientError
        from core.llm_providers._cc_credentials import _load_credentials_pool
        pool = _load_credentials_pool(
            service_id, user_id=user_id, conv_id=conversation_id) or []
        if not pool:
            raise LLMClientError(
                "Claude Code credentials not configured. "
                "Use /cls to authenticate with your Claude subscription.")
        occupied = {
            s.svc_pool_idx for s in self._sessions.values()
            if s.service_id == service_id and s.svc_pool_idx >= 0
        }
        occupied |= {idx for sid, idx in self._reserved_slots if sid == service_id}
        free = [i for i in range(len(pool)) if i not in occupied]
        if not free:
            raise LLMClientError(
                "All Claude Code credentials are in use by live sessions. "
                "Add more logins (/cls) or wait for one to free up.")
        idx = free[0]
        self._reserved_slots.add((service_id, idx))
        logger.info("[cci-live] claimed pool slot %d for %s (remaining free=%d)",
                    idx, service_id, len(free) - 1)
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
                    return state
                self._sessions.pop(state.key, None)
        return None

    def list_sessions(self, user_id: str, conversation_id: str,
                      service_id: str = "") -> list[dict]:
        """Return live interactive sessions for a conversation."""
        sessions: list[dict] = []
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
                    "provider": "claude-code-interactive",
                })
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
                    "provider": "claude-code-interactive",
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
                # Best-effort: never make sends worse than before. Proceed
                # with the paste + double-Enter and hope the TUI catches up.
                logging.getLogger(__name__).warning(
                    "[cci] TUI prompt not detected ready before first send to "
                    "%s; submitting best-effort", state.name)
        self._cancel_copy_mode(state)
        self._remember_injected_prompt(state, text)
        self._remember_injected_prompt_for_event_service(state, text)
        if not self._load_buffer(state, text):
            return False
        if not self._paste_buffer(state):
            return False
        # Let the TUI finish ingesting the paste before pressing Enter: an
        # Enter that lands inside the paste-detection window is treated as
        # pasted newline (inserts a blank line) instead of submitting.
        settle = self._paste_settle_seconds()
        if settle > 0:
            time.sleep(settle)
        try:
            delay = float(os.environ.get("PAWFLOW_CCI_SUBMIT_DELAY_SECONDS", "1.0") or "1.0")
        except ValueError:
            delay = 1.0
        # Submit with a double Enter separated by a short wait. At container
        # restart the Claude Code TUI can drop the first Enter before its input
        # box is focused, leaving the pasted prompt unsent. The first Enter
        # submits in the normal case; the second guarantees submission after a
        # restart. An extra Enter on an already-submitted or empty prompt is a
        # no-op in the CC TUI.
        if not self.send_keys(state, ["Enter"]):
            return False
        if delay > 0:
            time.sleep(delay)
        if not self.send_keys(state, ["Enter"]):
            return False
        self._verify_submitted(state, text)
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

    # Rendered by the CC TUI only while a turn is running — the strongest
    # version-tolerant signal that a submitted prompt was accepted.
    _RUNNING_MARKERS = ("esc to interrupt",)

    def _pane_shows_running(self, text: str) -> bool:
        low = (text or "").lower()
        return any(marker in low for marker in self._RUNNING_MARKERS)

    @staticmethod
    def _paste_settle_seconds() -> float:
        try:
            return max(0.0, float(os.environ.get(
                "PAWFLOW_CCI_PASTE_SETTLE_SECONDS", "0.2") or "0.2"))
        except ValueError:
            return 0.2

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

    def _verify_submitted(self, state: InteractiveContainer, text: str) -> None:
        """Best-effort post-submit check with Enter retries.

        Despite the settle delay, an Enter can still be coalesced into the
        paste burst and inserted as a literal newline, leaving the message
        sitting in the input box (forcing a human to press Enter in the
        tmux — the bug this guards against). Poll the pane: a running
        marker means the prompt was accepted; an idle prompt with the
        pasted text still visible means the Enter was swallowed — press it
        again. Never makes a send fail: extra Enters on an empty or
        already-submitted prompt are no-ops in the CC TUI.
        """
        try:
            window = float(os.environ.get(
                "PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS", "6.0") or "6.0")
        except ValueError:
            window = 6.0
        if window <= 0:
            return
        fragment = self._submit_probe_fragment(text)
        interval = 0.3
        polls = max(1, int(window / interval))
        retries = 0
        log = logging.getLogger(__name__)
        for _ in range(polls):
            pane = self._pane_text(state.name)
            if pane:
                if self._pane_shows_running(pane):
                    if not fragment or fragment not in pane:
                        return
                    # Running but our text is still on screen: either the
                    # interrupted OLD turn is winding down, or the TUI echoes
                    # the submitted prompt. Keep polling — never press Enter
                    # while a turn is running.
                    time.sleep(interval)
                    continue
                if fragment and fragment not in pane:
                    # Input box no longer holds the prompt: submitted.
                    return
                if self._pane_shows_prompt(pane):
                    if retries >= 3:
                        break
                    retries += 1
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
                            "tmux", "paste-buffer", "-t", "pawflow"],
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
            }
            with open(marker, "a", encoding="utf-8") as fh:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    @staticmethod
    def _remember_injected_prompt_for_event_service(state: InteractiveContainer,
                                                    text: str) -> None:
        try:
            from services.cc_interactive_event_service import get_or_create_cc_interactive_event_service
            _, _, event_service = get_or_create_cc_interactive_event_service()
            event_service.remember_injected_prompt(state.session_token, text or "")
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

    def send_interrupt(self, state: InteractiveContainer, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        self._cancel_copy_mode(state)
        self._remember_injected_prompt(state, text)
        self._remember_injected_prompt_for_event_service(state, text)
        if not (self._load_buffer(state, text) and self._paste_buffer(state)):
            return False
        if not self.send_keys(state, ["Escape"]):
            return False
        # The Escape interrupts the running turn and triggers a TUI
        # re-render; an Enter pressed during it is dropped or inserted as a
        # newline, stranding the message in the input box. Settle first,
        # then verify (the verifier re-presses Enter if it was swallowed).
        settle = self._paste_settle_seconds()
        if settle > 0:
            time.sleep(settle)
        if not self.send_keys(state, ["Enter"]):
            return False
        if settle > 0:
            # Let the interrupted turn's running marker leave the pane so
            # the verifier doesn't mistake the OLD turn for the new one.
            time.sleep(settle)
        # _verify_submitted polls the tmux pane for up to
        # PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS (default 6s) and only re-presses
        # Enter if the paste was swallowed. It is best-effort and its result
        # is unused, yet send_interrupt runs on the HTTP request thread
        # (POST /api/agent -> send_user_message -> send_interrupt), so running
        # it inline made every preempt block ~6-8s before the ack returned.
        # The Escape+Enter above already submitted in the normal case; run the
        # verification/retry in the background so the request returns at once.
        threading.Thread(
            target=self._verify_submitted, args=(state, text),
            name="cci-verify-submit", daemon=True,
        ).start()
        return True

    def force_stop(self, state: InteractiveContainer) -> bool:
        return self.send_keys(
            state, ["Space", "Space", "Escape", "Escape", "BSpace", "BSpace"])

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
        return True

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
            self._kill_container(state.name)
        return len(victims)

    def ensure_sweeper(self, tick_seconds: int = 60,
                       idle_ttl_seconds: Optional[int] = None) -> None:
        if idle_ttl_seconds and idle_ttl_seconds > 0:
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

    def sweep_idle(self, idle_ttl_seconds: Optional[float] = None) -> int:
        ttl = float(idle_ttl_seconds if idle_ttl_seconds is not None else self._idle_ttl)
        cutoff = time.time() - ttl
        to_kill: list[tuple[InteractiveContainer, str]] = []
        with self._lock:
            snapshot = list(self._sessions.items())
        dead: Dict[tuple[str, str, str, str], bool] = {}
        for key, state in snapshot:
            dead[key] = not self._is_alive(state.name)
        with self._lock:
            for key, state in snapshot:
                current = self._sessions.get(key)
                if current is not state:
                    continue
                reason = ""
                if dead.get(key):
                    reason = "dead_container"
                elif current.last_used < cutoff:
                    reason = f"idle>{int(ttl)}s"
                if reason:
                    self._sessions.pop(key, None)
                    to_kill.append((current, reason))
        for state, reason in to_kill:
            logger.info("[cci-live] evict %s (%s)", state.name, reason)
            self._recover_container_tokens(state)
            self._kill_container(state.name)
        return len(to_kill)

    def shutdown_all(self) -> None:
        self._sweeper_stop.set()
        with self._lock:
            states = list(self._sessions.values())
            self._sessions.clear()
        for state in states:
            self._recover_container_tokens(state)
            self._kill_container(state.name)

    @staticmethod
    def _kill_container(name: str) -> None:
        subprocess.run(docker_cmd() + ["rm", "-f", name], capture_output=True, timeout=15)  # nosec B603

    @staticmethod
    def _fmt_key(key: tuple[str, str, str, str]) -> str:
        user_id, conv_id, agent_name, service_id = key
        return (
            f"{user_id[:6] or '?'}/{conv_id[:8] or '?'}/"
            f"{agent_name or 'default'}@{service_id or 'default'}"
        )

