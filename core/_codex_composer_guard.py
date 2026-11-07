"""Codex TUI composer guards for tmux prompt submission.

Incident (2026-09-23, codex-cli 0.156.1): a delegated prompt vanished and
the turn failed with "the submission pane is inconclusive". Two defects
combined:

- The prompt preparation sent ``Escape Escape``. Codex binds a second Esc on
  an empty composer to *backtrack*: when the two keys reach the TUI as two
  reads, it opens "Browsing transcript ... ↵ rewind · esc back". A paste is
  dropped there and Enter means *rewind*: the conversation is reverted to an
  earlier prompt ("Conversation reverted to this point"). Reproduced on a
  disposable Codex with the same image.
- Codex now draws its composer as ``›`` instead of ``>``. The composer was
  never located, so a chip stranded in it could not authorize the Enter
  retry, and every verdict degraded to "inconclusive".

This mixin prepares with ONE Escape, closes the overlay if it is open anyway,
never takes a changed pane with an overlay on it as proof of a paste (the
overlay opening is exactly such a change), trusts the composer's content over
any pane change once the composer is located, and refuses any Enter while the
backtrack overlay is on screen. It reads the pane only as a transport signal
and never logs prompt text.

codex-cli 0.156 draws transcript user turns with the same `›` as the
composer; the composer is the one at the bottom, below any transcript or
running-turn chrome, and no overlay hides it.

Incident (2026-11-07 08:15Z, codex-cli 0.167.0, GameDev3): a turn failed with
"the pane looks submitted but no hook/MITM receipt arrived" while Codex ran a
turn on the PREVIOUS prompt, re-submitted byte for byte, and the new prompt
was never delivered. Reproduced on a disposable 0.167: Esc, Esc on an idle
composer opens the overlay, the paste is dropped, the first Enter rewinds and
the second submits the previous prompt again. So an idle composer gets no Esc
at all (one Esc there only primes backtrack, and any second Esc from anywhere
completes it), and every Enter of a send is pressed only while the located
composer holds THAT send's paste (``state.expected_paste``): an Enter on a
composer holding anything else -- a recalled prompt, the empty placeholder
after the first Enter submitted -- is skipped.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class CodexComposerGuardMixin:
    """Mixed in before InteractiveClaudeCodePool in the Codex pool's MRO."""

    # Footer of the backtrack transcript overlay (codex-cli 0.156.1).
    _BACKTRACK_OVERLAY_MARKERS = ("↵ rewind", "browsing transcript")
    # Footer of the Ctrl+T transcript pager, which also hides the composer.
    _TRANSCRIPT_OVERLAY_MARKERS = ("esc browse prompts",)
    # Transcript chrome that can only be drawn ABOVE the composer.
    _TRANSCRIPT_LINE_PREFIXES = ("•", "■", "└")
    # Hint shown once a first Esc has primed backtrack.
    _BACKTRACK_PRIMED_MARKER = "esc again to edit previous message"
    _OVERLAY_EXIT_ATTEMPTS = 3
    _ESC_SETTLE_SECONDS = 0.3

    def _pane_shows_backtrack(self, pane: str) -> bool:
        low = (pane or "").lower()
        return any(marker in low for marker in self._BACKTRACK_OVERLAY_MARKERS)

    def _pane_shows_overlay(self, pane: str) -> bool:
        low = (pane or "").lower()
        return self._pane_shows_backtrack(pane) or any(
            marker in low for marker in self._TRANSCRIPT_OVERLAY_MARKERS)

    def _composer_text(self, pane: str) -> str:
        """The bottom `›` region, or '' when it is a transcript user turn."""
        if self._pane_shows_overlay(pane):
            return ""
        composer = super()._composer_text(pane)
        for line in composer.splitlines()[1:]:
            stripped = line.lstrip()
            if (stripped.startswith(self._TRANSCRIPT_LINE_PREFIXES)
                    or self._pane_shows_running(stripped)):
                return ""
        return composer

    def _composer_is_idle(self, pane: str) -> bool:
        """An editable composer and nothing to stop or dismiss.

        False whenever it cannot be told (unreadable pane, composer not
        located): the caller then keeps its single Esc.
        """
        if not pane or self._pane_shows_overlay(pane):
            return False
        if self._pane_shows_running(pane):
            return False
        if self._BACKTRACK_PRIMED_MARKER in pane.lower():
            return True
        return bool(self._composer_text(pane))

    def _pane_state_summary(self, pane: str) -> str:
        """Structural pane verdicts for logs -- never the prompt text."""
        if not pane:
            return "pane=unreadable"
        composer = self._composer_text(pane)
        if not composer:
            held = "absent"
        elif self._pane_holds_unsent_paste(pane):
            held = "chip"
        else:
            first = composer.splitlines()[0].lstrip()[1:].strip()
            held = "nonempty" if first else "empty"
        low = pane.lower()
        return (f"composer={held} "
                f"backtrack_overlay={self._pane_shows_backtrack(pane)} "
                f"backtrack_primed={self._BACKTRACK_PRIMED_MARKER in low} "
                f"running={self._pane_shows_running(pane)}")

    def _leave_backtrack_overlay(self, state) -> bool:
        """Close the transcript overlay if an Esc opened it. False if stuck."""
        for _attempt in range(self._OVERLAY_EXIT_ATTEMPTS):
            time.sleep(self._ESC_SETTLE_SECONDS)
            pane = self._pane_text(state.name)
            if not pane or not self._pane_shows_backtrack(pane):
                return True
            logger.warning(
                "[codex-interactive] backtrack overlay open in %s before the "
                "paste; closing it with Esc", state.name)
            if not super().send_keys(state, ["Escape"]):
                return False
        pane = self._pane_text(state.name)
        if pane and self._pane_shows_backtrack(pane):
            state.last_error = (
                "Codex backtrack overlay stayed open; refusing to paste into it")
            return False
        return True

    def send_keys(self, state, keys: list[str]) -> bool:
        """Press Enter only on this send's paste, never into the overlay.

        In the overlay Enter rewinds. On a located composer that does not
        hold ``state.expected_paste`` there is nothing of ours to submit, so
        the Enter is skipped (True: not a transport failure); the submission
        verifier then reports what really happened.
        """
        if "Enter" in keys:
            pane = self._pane_text(state.name)
            if pane and self._pane_shows_backtrack(pane):
                state.last_error = (
                    "Codex backtrack overlay is open; refusing Enter, which "
                    "would rewind the conversation")
                logger.error("[codex-interactive] %s (%s)", state.last_error,
                             self._pane_state_summary(pane))
                return False
            expected = getattr(state, "expected_paste", "")
            composer = self._composer_text(pane) if (pane and expected) else ""
            if composer and not self._composer_holds_paste(
                    composer, pane, expected, ""):
                # INFO, not WARNING: the second Enter of every successful
                # send lands here once the first one submitted.
                logger.info(
                    "[codex-interactive] skipping Enter: the composer of %s "
                    "no longer holds this send's paste (%s)", state.name,
                    self._pane_state_summary(pane))
                remaining = [key for key in keys if key != "Enter"]
                return (super().send_keys(state, remaining)
                        if remaining else True)
        return super().send_keys(state, keys)

    def _composer_holds_paste(self, composer: str, pane: str, text: str,
                              before_pane: str) -> bool:
        """The located composer shows this paste: its chip or its text."""
        if self._pane_holds_unsent_paste(pane):
            return True
        fragment = self._submit_probe_fragment(text)
        if fragment:
            return self._fragment_on_pane(composer, fragment)
        return composer != self._composer_text(before_pane)

    def _strict_paste_landed(self, state, text: str,
                             before_pane: str = "") -> bool:
        fragment = self._submit_probe_fragment(text)
        deadline = time.time() + max(0.0, self._PASTE_LANDED_SECONDS)
        while True:
            pane = self._pane_text(state.name)
            if not pane:
                return True  # unknowable; the Enter guard still applies
            if self._pane_shows_overlay(pane):
                break  # the overlay dropped the paste
            if self._pane_shows_running(pane):
                return True
            composer = self._composer_text(pane)
            if composer:
                if self._composer_holds_paste(composer, pane, text, before_pane):
                    return True
            elif ((before_pane and pane != before_pane)
                  or self._fragment_on_pane(pane, fragment)):
                # Composer chrome this build does not draw as `›`/`>` (a
                # boxed composer): only the screen's reaction can tell.
                return True
            if time.time() >= deadline:
                break
            time.sleep(0.3)
        logger.error("[codex-interactive] paste not in the composer of %s (%s)",
                     state.name, self._pane_state_summary(pane))
        return False
