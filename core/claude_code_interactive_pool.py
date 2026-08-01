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
                       conversation_id: str, agent_name: str,
                       before_launch=None) -> InteractiveContainer:
        """Return the live container for this key, launching one if there is none.

        ``before_launch`` is called at the instant this becomes a LAUNCH and
        not a reuse, and may refuse it by raising. That is the one place a
        caller can still say "the context I hold was built for a running
        process, so do not start a fresh one with it" -- see
        ``_cli_require_cold_context``. It is never called on the reuse path.
        """
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
            # About to launch. Ask before claiming a credential slot, so a
            # refusal costs nothing and releases nothing.
            if before_launch is not None:
                before_launch()
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
        # Once, not once per attempt: the guard that keeps hooks from filing
        # our own paste as a user message counts tickets, and a second record
        # for the same text would leave one unspent to swallow the next thing
        # the human really types.
        self._remember_injected_prompt(state, text)
        self._remember_injected_prompt_for_event_service(state, text)
        # Paste, then PROVE it landed, and paste again if it did not.
        #
        # Pressing Enter was the only retry there was, and it answers the
        # wrong failure. When the paste itself never reaches the composer --
        # a TUI still drawing itself, a pane not yet focused -- the composer
        # is empty, and `_verify_submitted` reads an empty composer as
        # "submitted": the same picture a delivered prompt leaves behind. So
        # send_text returned True, raised nothing, and no prompt was ever
        # submitted; the turn then sat waiting on a session that had been
        # asked for nothing, holding the agent "active" until the 300s
        # no-event timeout. Enter cannot fix an empty composer. Only another
        # paste can.
        settle = self._paste_settle_seconds()
        landed = False
        for attempt in range(1, self._PASTE_ATTEMPTS + 1):
            if not self._load_buffer(state, text):
                return False
            if not self._paste_buffer(state):
                return False
            # Let the TUI finish ingesting the paste before pressing Enter: an
            # Enter that lands inside the paste-detection window is treated as
            # pasted newline (inserts a blank line) instead of submitting.
            if settle > 0:
                time.sleep(settle)
            if self._paste_landed(state, text):
                landed = True
                break
            logging.getLogger(__name__).warning(
                "[cci] paste did not reach the composer of %s; pasting again "
                "(attempt %d/%d)", state.name, attempt, self._PASTE_ATTEMPTS)
        if not landed:
            # Bounded, and a failure rather than a silent success: the caller
            # raises, which is strictly better than a turn that waits on a
            # session nobody ever prompted.
            state.last_error = (
                f"prompt never reached the composer after "
                f"{self._PASTE_ATTEMPTS} paste attempts")
            return False
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
    # forever.
    _COMPOSER_PROMPT_PREFIX = ""

    def _composer_text(self, pane: str) -> str:
        """The input-box region of the pane: the last prompt line onward.

        Returns '' when the prompt line is not on screen — the caller treats
        that as "cannot tell", never as "empty composer".
        """
        prefix = self._COMPOSER_PROMPT_PREFIX
        if not prefix:
            return pane or ""
        lines = (pane or "").splitlines()
        for idx in range(len(lines) - 1, -1, -1):
            if self._is_composer_line(lines[idx], prefix):
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

    # How many times the same prompt is pasted before the pool gives up. The
    # retry answers a paste that never arrived, which is a startup race and
    # settles within a couple of attempts; more would only spend seconds
    # re-pasting into a TUI that is not coming up.
    _PASTE_ATTEMPTS = 3
    # How long to watch for the paste to appear in the composer before
    # concluding it never will. Longer than the settle -- a TUI busy drawing
    # itself ingests a multi-kilobyte buffer slowly -- and short enough that
    # three attempts stay well inside a turn.
    _PASTE_LANDED_SECONDS = 3.0

    def _paste_landed(self, state: InteractiveContainer, text: str) -> bool:
        """Did the paste actually reach the input box?

        The answers, in the order they settle the question:

        - the chip is in the composer, or the probe fragment is on the pane:
          it landed;
        - the turn is already running: it landed and was accepted between the
          paste and this look;
        - the composer is readable and holds neither: it did not, and Enter
          would be pressed into an empty box;
        - the composer is not on screen: not an answer yet. Keep looking until
          the window closes, then call it a failure.

        That last one used to return True immediately, and it accepted the one
        case this check exists for. `>_ OpenAI Codex (v0.104.0)` is drawn the
        instant the TUI starts and the input box is not: a pane holding only
        the header locates no composer, so a paste into a TUI that has no
        input box yet -- past the readiness timeout, or mid-redraw -- was
        declared landed, Enter went nowhere, and the turn waited out its 300s
        no-event timeout. A pool that declares no composer prefix is
        unaffected: its whole pane IS the composer, so it is always located,
        and the answer stays what it was.

        The fragment is still looked for across the whole pane, and on a TUI
        that echoes pasted text that cannot tell this paste from the same text
        still visible in the transcript. Every way of narrowing it costs a
        FALSE NEGATIVE -- a landed paste declared missing is pasted a second
        time, and the composer then holds the prompt twice, which is worse
        than the ambiguity. It stays as it is until the composer of such a TUI
        can be located outright.

        It is looked for on the SQUEEZED pane, though. The pane is a screen,
        not a buffer: the composer hard-wraps the prompt at the pane width,
        and a tail that falls across a wrap is not on the pane verbatim even
        though every character of it is. That failure is deterministic -- the
        same prompt wraps at the same column on every attempt -- so all three
        pastes were declared missing and a turn whose prompt was sitting in
        the composer, needing only Enter, failed with "never reached the
        composer". `_verify_submitted` keeps the verbatim test on purpose:
        there "fragment absent" means "submitted", so a wrap-blinded match
        errs toward leaving a running turn alone, while a squeezed one would
        keep finding the prompt Claude Code echoes into its own transcript.

        Reads the pane purely as transport signal -- the provider still never
        assembles a response from tmux output.
        """
        fragment = self._submit_probe_fragment(text)
        if not self._PASTE_CHIP_MARKERS and not fragment:
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
                holds = self._pane_holds_unsent_paste(pane)
                if holds:
                    # The chip is authoritative: the prompt is in the input
                    # box, whatever else the pane shows. Press Enter again.
                    if retries >= 3:
                        break
                    retries += 1
                    log.warning(
                        "[cci] pasted prompt still in the input box of %s; "
                        "pressing Enter again (retry %d)", state.name, retries)
                    if not self.send_keys(state, ["Enter"]):
                        break
                    time.sleep(interval)
                    continue
                if self._pane_shows_running(pane):
                    if holds is False or not fragment or fragment not in pane:
                        return
                    # Running but our text is still on screen: either the
                    # interrupted OLD turn is winding down, or the TUI echoes
                    # the submitted prompt. Keep polling — never press Enter
                    # while a turn is running.
                    time.sleep(interval)
                    continue
                if holds is False:
                    # Composer located and free of the chip: submitted.
                    return
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
