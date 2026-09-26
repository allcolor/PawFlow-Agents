"""Per-message submission evidence read from a CLI's own session journals.

A message pasted into a running interactive CLI goes through two states that
no hook reports reliably:

* **accepted** -- the CLI took it out of the composer and holds it for the
  model. Claude Code fires ``UserPromptSubmit`` at that moment; Codex fires
  nothing until the model reads it (measured 2026-09-26: two minutes later,
  behind a blocking tool).
* **processed** -- the model has received it. Claude Code reports nothing at
  that moment; Codex fires ``UserPromptSubmit`` then.

Both CLIs write both facts, per message and with the full text, to files in
their session directory, which PawFlow owns:

* Claude Code (``CLAUDE_CONFIG_DIR/projects/<slug>/<session>.jsonl``): a
  ``queue-operation`` ``enqueue`` line when a message is queued behind a
  running step; a ``queued_command`` attachment when the model absorbs it; a
  ``user`` line when a prompt starts a turn.
* Codex (``CODEX_HOME``): a ``history.jsonl`` line on Enter; a ``user``
  ``message`` ``response_item`` in ``sessions/**/rollout-*.jsonl`` when the
  model reads it.

``mark`` snapshots where each journal ends before a paste, and ``status``
reads only what was appended since, so the check never rescans a session's
history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ACCEPTED = "accepted"
PROCESSED = "processed"

CLAUDE_CODE = "claude-code-interactive"
CODEX = "codex-interactive"

# A journal line longer than this is still parsed; the cap only bounds how
# much one status() call reads after a mark.
_MAX_READ_BYTES = 64 * 1024 * 1024
# Only the most recently written journals can receive a new message.
_RECENT_FILES = 4


def normalize(text: str) -> str:
    """Whitespace-insensitive form: the TUIs rewrap and trim pasted text."""
    return " ".join((text or "").split())


def same_message(expected: str, candidate: str) -> bool:
    return bool(expected) and normalize(candidate) == expected


@dataclass
class JournalMark:
    """Where each journal ended when the paste was about to happen."""

    offsets: dict = field(default_factory=dict)


def _recent(paths: list) -> list:
    existing = []
    for path in paths:
        try:
            existing.append((path.stat().st_mtime, path))
        except OSError:
            continue
    existing.sort(reverse=True)
    return [path for _mtime, path in existing[:_RECENT_FILES]]


def _journals(provider: str, workdir: str) -> dict:
    """Journal files by role: ``accept`` and/or ``process``."""
    root = Path(workdir)
    if provider == CLAUDE_CODE:
        transcripts = _recent(list((root / "projects").glob("*/*.jsonl")))
        return {path: ("accept", "process") for path in transcripts}
    if provider == CODEX:
        home = root / ".codex"
        found = {home / "history.jsonl": ("accept",)}
        for path in _recent(list((home / "sessions").glob("*/*/*/rollout-*.jsonl"))):
            found[path] = ("process",)
        return found
    raise ValueError(f"no submission journal for provider {provider!r}")


def mark(provider: str, workdir: str) -> JournalMark:
    """Snapshot the journals' ends. Call BEFORE pasting."""
    offsets = {}
    for path in _journals(provider, workdir):
        try:
            offsets[str(path)] = path.stat().st_size
        except OSError:
            offsets[str(path)] = 0
    return JournalMark(offsets=offsets)


def _read_since(path: Path, offset: int) -> list:
    """Complete lines appended after ``offset`` (a partial tail is skipped)."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size < offset:
        # Rewritten or truncated: everything in it is new to us.
        offset = 0
    if size == offset:
        return []
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(min(size - offset, _MAX_READ_BYTES))
    end = data.rfind(b"\n")
    if end < 0:
        return []
    lines = []
    for raw in data[:end].split(b"\n"):
        if not raw.strip():
            continue
        try:
            lines.append(json.loads(raw.decode("utf-8", errors="replace")))
        except ValueError:
            continue
    return lines


def _text_blocks(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in {"text", "input_text"}:
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


def _claude_code_evidence(entry: dict, expected: str) -> str:
    kind = entry.get("type")
    if kind == "queue-operation":
        if (entry.get("operation") == "enqueue"
                and same_message(expected, str(entry.get("content") or ""))):
            return ACCEPTED
        return ""
    if kind == "attachment":
        attachment = entry.get("attachment") or {}
        if (attachment.get("type") == "queued_command"
                and same_message(expected, str(attachment.get("prompt") or ""))):
            return PROCESSED
        return ""
    if kind == "user":
        message = entry.get("message") or {}
        if same_message(expected, _text_blocks(message.get("content"))):
            return PROCESSED
    return ""


def _codex_evidence(entry: dict, expected: str, role: str) -> str:
    if role == "accept":
        return ACCEPTED if same_message(expected, str(entry.get("text") or "")) else ""
    if entry.get("type") != "response_item":
        return ""
    payload = entry.get("payload") or {}
    if (payload.get("type") == "message" and payload.get("role") == "user"
            and same_message(expected, _text_blocks(payload.get("content")))):
        return PROCESSED
    return ""


def status(provider: str, workdir: str, text: str, since: JournalMark) -> str:
    """``processed``, ``accepted`` or ``""`` for ``text`` pasted after ``since``.

    Processed implies accepted. A journal created after the mark (a new
    session file) is read from its start.
    """
    expected = normalize(text)
    if not expected:
        return ""
    best = ""
    for path, roles in _journals(provider, workdir).items():
        entries = _read_since(path, since.offsets.get(str(path), 0))
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if provider == CLAUDE_CODE:
                found = _claude_code_evidence(entry, expected)
            else:
                found = _codex_evidence(entry, expected, roles[0])
            if found == PROCESSED:
                return PROCESSED
            if found == ACCEPTED:
                best = ACCEPTED
    return best


def supported(provider: str) -> bool:
    return provider in {CLAUDE_CODE, CODEX}


__all__ = [
    "ACCEPTED", "PROCESSED", "JournalMark", "mark", "normalize",
    "same_message", "status", "supported",
]
