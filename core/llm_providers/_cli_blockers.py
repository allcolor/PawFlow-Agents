"""Detect an interactive CLI that is blocked waiting for an answer.

A Claude Code or Codex TUI can stop mid-turn while its tmux pane waits for
input the proxy never sees:

* a rate-limit banner (``429``, ``usage limit``, ``limit will reset``) -- the
  CLI stops issuing requests, so the event stream goes silent;
* a question or a menu (model switch, confirmation) -- the CLI waits for a
  keystroke nobody sends.

Both looked like a slow but healthy turn: the coordinator kept polling, the
agent stayed in Active Agents, and the webchat showed nothing at all. The pane
text is the only place that says why, so read it once the stream has been idle
for the probe window and fail the turn with a message the user can act on.

Matching stays deliberately conservative. Only the tail of the pane is read,
because tmux keeps older screen content around; question patterns must match
one of the last lines, which is where a live prompt sits. An answer that merely
discusses rate limits, or a plan with numbered steps, must not be mistaken for
a blocked turn.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class CliBlocker(NamedTuple):
    """What the pane says the interactive CLI is waiting for."""

    kind: str      #: "rate_limited" or "question"
    reason: str    #: short label for the reported message
    excerpt: str   #: the matching pane line, trimmed


#: How many non-empty pane lines are inspected. A banner or a prompt is the last
#: thing printed, so the tail is enough and keeps old scrollback out.
_TAIL_LINES = 20

#: Question patterns must match one of the last lines: that is where a live
#: prompt is. Anything higher up is history.
_QUESTION_TAIL_LINES = 3

#: The TUI prints this hint while it is *working*. A pane that shows it is not
#: waiting for input, whatever else is on screen: the CLI is inside a tool run,
#: and any rate-limit wording there is the model's prose, a path it is editing,
#: or the status footer. A long local tool (pytest, a build) emits no event for
#: minutes -- exactly the silence this probe reacts to -- so reading that pane as
#: "rate limited" killed a healthy turn non-retryably while the CLI kept
#: working. Mirrors ``_RUNNING_MARKERS`` in
#: ``core.claude_code_interactive_pool``.
_RUNNING_MARKERS = ("esc to interrupt",)

_RATE_LIMIT_PATTERNS = (
    r"\b429\b",
    r"rate[ _-]?limit",
    r"too many requests",
    r"quota (?:exceeded|reached)",
    # A limit that was REACHED: "you have reached your session usage limit",
    # "Usage limit reached for 5 hour". The healthy status footer
    # ("Approaching usage limit - resets at 5pm") carries no reached/exceeded,
    # so the bare words are not a pattern of their own.
    r"(?:reached|exceeded)[^\n]{0,40}limit",
    r"(?:session|weekly|daily|hourly) limit (?:reached|exceeded)",
    r"limit will reset",
)

_QUESTION_PATTERNS = (
    r"\[\s*y\s*/\s*n\s*\]",
    r"\(\s*y\s*/\s*n\s*\)",
    r"\byes\s*/\s*no\b",
    r"do you want to",
    r"would you like to",
    r"are you sure",
    r"press (?:any key|enter) to",
    r"esc to cancel",
    r"enter to confirm",
    r"enter to select",
    r"switch to (?:a |the )?(?:different )?(?:model|account)",
)


def tail_lines(text: str, count: int) -> list:
    """Return the last ``count`` non-empty lines of a pane capture."""
    lines = [line.strip() for line in str(text or "").splitlines()]
    return [line for line in lines if line][-count:]


def _first_match(lines: list, patterns: tuple) -> str:
    for line in lines:
        lowered = line.lower()
        for pattern in patterns:
            if re.search(pattern, lowered):
                return line
    return ""


def _excerpt(line: str) -> str:
    return str(line or "").strip()[:200]


def detect_cli_blocker(pane: str) -> CliBlocker | None:
    """Return what the pane is waiting for, or None when it is not blocked."""
    tail = tail_lines(pane, _TAIL_LINES)
    if not tail:
        return None
    # A working CLI is never a blocked one: see _RUNNING_MARKERS. When the pane
    # cannot say, stay silent -- this failure is not retryable, so guessing is
    # worse than waiting one more probe window.
    screen = "\n".join(tail).lower()
    if any(marker in screen for marker in _RUNNING_MARKERS):
        return None
    ratelimited = _first_match(tail, _RATE_LIMIT_PATTERNS)
    if ratelimited:
        return CliBlocker(
            "rate_limited", "the CLI hit a provider rate limit",
            _excerpt(ratelimited))
    question = _first_match(tail[-_QUESTION_TAIL_LINES:], _QUESTION_PATTERNS)
    if question:
        return CliBlocker(
            "question", "the CLI is waiting for an answer",
            _excerpt(question))
    return None
