"""Antigravity CLI interactive sessions.

This pool starts the real ``agy`` CLI in tmux with Gemini OAuth/MCP config and
a transparent observer proxy for ``daily-cloudcode-pa.googleapis.com``. The
same tmux/proxy foundation is used by both the diagnostics observer action and
the ``antigravity-interactive`` LLM provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import hashlib
import logging
import subprocess  # nosec B404 - Docker/tmux process control is this module's job.
import time


if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)
# Split out of antigravity_observer_pool.py for the <=800-line rule; the
# mixin is composed back into AntigravityObserverPool (invariant 2: MRO/shared state).

from core._antigravity_base import AntigravityObserverSession, ANTIGRAVITY_BACKEND_HOST  # noqa: F401,E402


def docker_cmd():
    """Resolve docker_cmd through the pool module at call time so tests that
    monkeypatch core.antigravity_observer_pool.docker_cmd still take effect
    after this mixin was split out."""
    import core.antigravity_observer_pool as _pool
    return _pool.docker_cmd()


class _AntigravityInputMixin:
    """tmux text/keys input: send_text, prompt injection tracking, literal paste."""

    def send_text(self, state: AntigravityObserverSession, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        submit_hash = self._prompt_hash(text)
        with state.active_submit_lock:
            if state.active_submit_hash == submit_hash:
                state.last_error = "duplicate in-flight Antigravity tmux submit"
                logger.info(
                    "[antigravity-interactive] rejected duplicate in-flight tmux submit container=%s bytes=%d",
                    state.name, len((text or "").encode("utf-8")))
                return False
            state.active_submit_hash = submit_hash
            state.active_submit_at = time.time()
        self._cancel_copy_mode(state)
        self._remember_injected_prompt(state, text)
        logger.info(
            "[antigravity-interactive] tmux submit start container=%s bytes=%d",
            state.name, len((text or "").encode("utf-8")))
        if not self._send_multiline_text(state, text):
            return False
        if state.manual_ingest_stop.is_set() or not self._is_alive(state.name):
            state.last_error = f"Container {state.name} was invalidated during tmux submit"
            logger.info(
                "[antigravity-interactive] tmux submit aborted after paste container=%s",
                state.name)
            return False
        # Antigravity renders tmux-injected text with a short delay. Submit only
        # after a bounded drain window so Enter does not race ahead of input.
        time.sleep(min(1.5, max(0.15, len(text or "") / 50000.0)))
        if state.manual_ingest_stop.is_set() or not self._is_alive(state.name):
            state.last_error = f"Container {state.name} was invalidated before tmux submit"
            logger.info(
                "[antigravity-interactive] tmux submit aborted before Enter container=%s",
                state.name)
            return False
        ok = self.send_keys(state, ["Enter"])
        if ok:
            logger.info(
                "[antigravity-interactive] tmux submit sent container=%s bytes=%d",
                state.name, len((text or "").encode("utf-8")))
        return ok

    def mark_submit_complete(self, state: AntigravityObserverSession) -> None:
        with state.active_submit_lock:
            state.active_submit_hash = ""
            state.active_submit_at = 0.0

    def send_interrupt(self, state: AntigravityObserverSession, text: str) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        self._cancel_copy_mode(state)
        self._remember_injected_prompt(state, text)
        return (self._send_multiline_text(state, text)
                and not state.manual_ingest_stop.is_set()
                and self.send_keys(state, ["Escape"])
                and self.send_keys(state, ["Enter"]))

    @staticmethod
    def _prompt_hash(text: str) -> str:
        return hashlib.sha256((text or "").rstrip("\r\n").encode("utf-8")).hexdigest()

    def _remember_injected_prompt(self, state: AntigravityObserverSession, text: str) -> None:
        if not text:
            return
        now = time.time()
        cutoff = now - 300.0
        state.injected_prompt_hashes = {
            digest: ts for digest, ts in state.injected_prompt_hashes.items()
            if float(ts or 0) >= cutoff
        }
        state.pending_injected_prompt_ignores = [
            ts for ts in state.pending_injected_prompt_ignores
            if float(ts or 0) >= cutoff
        ]
        state.injected_prompt_hashes[self._prompt_hash(text)] = now
        state.pending_injected_prompt_ignores.append(now)

    def _consume_injected_prompt(self, state: AntigravityObserverSession, text: str) -> bool:
        now = time.time()
        cutoff = now - 300.0
        state.injected_prompt_hashes = {
            digest: ts for digest, ts in state.injected_prompt_hashes.items()
            if float(ts or 0) >= cutoff
        }
        state.pending_injected_prompt_ignores = [
            ts for ts in state.pending_injected_prompt_ignores
            if float(ts or 0) >= cutoff
        ]
        digest = self._prompt_hash(text) if text else ""
        if digest and digest in state.injected_prompt_hashes:
            state.injected_prompt_hashes.pop(digest, None)
            if state.pending_injected_prompt_ignores:
                state.pending_injected_prompt_ignores.pop(0)
            logger.info(
                "[antigravity-interactive] ignored PawFlow-injected prompt in manual ingest container=%s",
                state.name)
            return True
        if state.pending_injected_prompt_ignores:
            state.pending_injected_prompt_ignores.pop(0)
            self._pop_oldest_injected_prompt(state)
            logger.info(
                "[antigravity-interactive] ignored pending PawFlow-injected prompt in manual ingest container=%s",
                state.name)
            return True
        return False

    @staticmethod
    def _pop_oldest_injected_prompt(state: AntigravityObserverSession) -> None:
        if not state.injected_prompt_hashes:
            return
        oldest = min(state.injected_prompt_hashes, key=state.injected_prompt_hashes.get)
        state.injected_prompt_hashes.pop(oldest, None)

    @staticmethod
    def _is_provider_context_prompt(text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        markers = (
            "<identity>\nYou are Antigravity",
            "You are Antigravity, a powerful agentic AI coding assistant",
            "PawFlow cold-session bootstrap.",
            ".pawflow_ag/initial_context.md",
            "Use your local filesystem/file-read capability",
            "Latest turn to answer now:",
            "<web_application_development>",
            "<communication_style>",
        )
        return any(marker in text for marker in markers)

    def _send_multiline_text(self, state: AntigravityObserverSession, text: str) -> bool:
        """Paste the complete prompt into agy without line-by-line key replay."""
        payload = (text or "").rstrip("\r\n")
        if not payload:
            return True
        # Do not replay lines with Shift+Enter: after compact this can leave a
        # visible, minutes-long prompt injection in tmux while agy is still
        # rendering prior output. tmux bracketed paste keeps the payload literal
        # and submits only when send_text sends the final Enter.
        return self._load_buffer(state, payload) and self._paste_buffer(state)

    def _send_literal_text(self, state: AntigravityObserverSession, text: str) -> bool:
        payload = text or ""
        if not payload:
            return True
        chunk = []
        size = 0
        for ch in payload:
            encoded_len = len(ch.encode("utf-8"))
            if chunk and size + encoded_len > self._LITERAL_CHUNK_BYTES:
                if not self._send_literal_chunk(state, "".join(chunk)):
                    return False
                time.sleep(self._LITERAL_CHUNK_DELAY_SECONDS)
                chunk = []
                size = 0
            chunk.append(ch)
            size += encoded_len
        if chunk and not self._send_literal_chunk(state, "".join(chunk)):
            return False
        return True

    def _send_literal_chunk(self, state: AntigravityObserverSession, chunk: str) -> bool:
        # Do not pass prompt text as a command-line argument: on Windows/WSL
        # relay paths it can be re-wrapped by a shell, and markup like
        # </message> is then parsed as redirection. Buffer stdin preserves the
        # text literally.
        return self._load_buffer(state, chunk) and self._paste_buffer(state)

    def force_stop(self, state: AntigravityObserverSession) -> bool:
        return self.send_keys(state, ["Escape", "Escape"])

    def _cancel_copy_mode(self, state: AntigravityObserverSession) -> None:
        try:
            subprocess.run(  # nosec B603
                docker_cmd() + ["exec", "--user", self._user_spec(), state.name,
                                "tmux", "send-keys", "-t", self._TMUX_TARGET, "-X", "cancel"],
                capture_output=True, timeout=5)
        except Exception:
            logger.debug("Ignored exception", exc_info=True)

    def is_interrupted_prompt(self, state: AntigravityObserverSession) -> bool:
        """Return True when manual Escape has stopped AGY and returned to prompt."""
        text = self.capture_tmux_tail(state, lines=80)
        if not text:
            return False
        markers = (
            "Interrupted - What should Antigravity CLI do instead?",
            "Interrupted - What should Antigravity do instead?",
            "What should Antigravity CLI do instead?",
        )
        return any(marker in text for marker in markers)

    def capture_tmux_tail(self, state: AntigravityObserverSession, lines: int = 80) -> str:
        if not self._is_alive(state.name):
            return ""
        start = f"-{max(1, int(lines or 80))}"
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), state.name,
                            "tmux", "capture-pane", "-pt", self._TMUX_TARGET,
                            "-S", start],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            logger.debug("tmux capture-pane failed for %s: %s", state.name,
                         self._command_error("tmux capture-pane", r))
            return ""
        return r.stdout.decode("utf-8", errors="replace")

    def send_keys(self, state: AntigravityObserverSession, keys: list[str]) -> bool:
        state.last_error = ""
        if not self._is_alive(state.name):
            state.last_error = f"Container {state.name} is not running"
            return False
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), state.name,
                            "tmux", "send-keys", "-t", self._TMUX_TARGET, *keys],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux send-keys", r)
            return False
        return True

    def _load_buffer(self, state: AntigravityObserverSession, text: str) -> bool:
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "-i", "--user", self._user_spec(), state.name,
                            "tmux", "load-buffer", "-"],
            input=(text or "").encode("utf-8"), capture_output=True, timeout=15)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux load-buffer", r)
            return False
        return True

    def _paste_buffer(self, state: AntigravityObserverSession) -> bool:
        r = subprocess.run(  # nosec B603
            docker_cmd() + ["exec", "--user", self._user_spec(), state.name,
                            "tmux", "paste-buffer", "-p", "-t", self._TMUX_TARGET],
            capture_output=True, timeout=10)
        if r.returncode != 0:
            state.last_error = self._command_error("tmux paste-buffer", r)
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
