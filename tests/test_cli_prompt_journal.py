"""Submission evidence read from Claude Code / Codex session journals.

Fixture lines are trimmed copies of what the CLIs wrote during the
2026-09-26 probes (a message pasted behind a blocking tool).
"""
import json
import os

import pytest

from core import cli_prompt_journal as journal

CC_ENQUEUE = {"type": "queue-operation", "operation": "enqueue",
              "timestamp": "2026-09-26T09:58:32.115Z",
              "content": "PROBE-B marker"}
CC_REMOVE = {"type": "queue-operation", "operation": "remove",
             "timestamp": "2026-09-26T09:58:52.071Z",
             "content": "PROBE-B marker", "reason": "absorbed_mid_turn"}
CC_ABSORBED = {"type": "attachment", "attachment": {
    "type": "queued_command", "prompt": "PROBE-B marker",
    "commandMode": "prompt", "origin": {"kind": "human"}}}
CC_TOOL_RESULT_QUOTING = {"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1",
     "content": [{"type": "text", "text": "PROBE-B marker"}]}]}}
CX_HISTORY = {"session_id": "s", "ts": 1790417334, "text": "PROBE-Y marker"}
CX_USER_ITEM = {"timestamp": "2026-09-26T10:10:44.743Z", "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "PROBE-Y marker"}]}}
CX_ASSISTANT_QUOTING = {"type": "response_item", "payload": {
    "type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "PROBE-Y marker"}]}}


def _append(path, *entries, partial=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
        fh.write(partial)


@pytest.fixture
def cc(tmp_path):
    return tmp_path, tmp_path / "projects" / "-cc" / "session.jsonl"


@pytest.fixture
def cx(tmp_path):
    home = tmp_path / ".codex"
    return (tmp_path, home / "history.jsonl",
            home / "sessions" / "2026" / "09" / "26" / "rollout-a.jsonl")


def test_claude_code_queued_message_is_accepted_then_processed(cc):
    workdir, transcript = cc
    _append(transcript, {"type": "user", "message": {"content": "earlier"}})
    since = journal.mark(journal.CLAUDE_CODE, str(workdir))

    _append(transcript, CC_ENQUEUE)
    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          "PROBE-B marker", since) == journal.ACCEPTED

    _append(transcript, CC_REMOVE, CC_ABSORBED)
    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          "PROBE-B marker", since) == journal.PROCESSED


def test_claude_code_prompt_starting_a_turn_is_processed(cc):
    workdir, transcript = cc
    _append(transcript)
    since = journal.mark(journal.CLAUDE_CODE, str(workdir))
    _append(transcript, {"type": "user", "message": {
        "role": "user", "content": "line one\n\n  line two  "}})

    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          "line one line two", since) == journal.PROCESSED


def test_claude_code_text_quoted_in_a_tool_result_is_not_evidence(cc):
    workdir, transcript = cc
    _append(transcript)
    since = journal.mark(journal.CLAUDE_CODE, str(workdir))
    _append(transcript, CC_TOOL_RESULT_QUOTING)

    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          "PROBE-B marker", since) == ""


# Shape Claude Code journaled at 2026-09-26 14:11:12Z (GameDev2): the inline
# pieces verbatim, the collapsed rest wrapped in <pasted_content> tags.
PASTED = ("GD4 -> GD2 BLOCKER\n\n=== 1) LA MESURE ===\n```\n  ANNONCE 91\n"
          "```\nJ'ATTENDS UNE PAIRE STABLE. ***")
CC_CHIP_PROMPT = {"type": "user", "message": {"role": "user", "content": (
    "GD4 -> GD2 BLOCKER\n\n=== 1) LA MESURE ===\n\n<pasted_content id=\"cc5d\">"
    "\n\n```\n  ANNONCE 91\n```\nJ'ATTENDS UNE PAIRE STABLE. ***\n"
    "</pasted_content id=\"cc5d\">\n")}}


def test_a_message_partly_collapsed_into_a_chip_is_processed(cc):
    workdir, transcript = cc
    _append(transcript)
    since = journal.mark(journal.CLAUDE_CODE, str(workdir))
    _append(transcript, CC_CHIP_PROMPT)

    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          PASTED, since) == journal.PROCESSED


def test_a_queued_message_collapsed_into_a_chip_is_processed(cc):
    workdir, transcript = cc
    _append(transcript)
    since = journal.mark(journal.CLAUDE_CODE, str(workdir))
    wrapped = '<pasted_content id="ab12">\n' + PASTED + '\n</pasted_content id="ab12">'
    _append(transcript,
            {"type": "queue-operation", "operation": "enqueue",
             "content": wrapped},
            {"type": "attachment", "attachment": {
                "type": "queued_command", "prompt": wrapped}})

    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          PASTED, since) == journal.PROCESSED


def test_a_chip_boundary_inside_a_word_still_matches():
    journaled = 'ANNON<pasted_content id="x1">CE 91</pasted_content id="x1">'
    assert journal.same_message(journal.normalize("ANNONCE 91"), journaled)


def test_evidence_written_before_the_mark_does_not_count(cc):
    workdir, transcript = cc
    _append(transcript, CC_ENQUEUE, CC_ABSORBED)
    since = journal.mark(journal.CLAUDE_CODE, str(workdir))

    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          "PROBE-B marker", since) == ""


def test_a_partially_written_line_is_ignored_until_complete(cc):
    workdir, transcript = cc
    _append(transcript)
    since = journal.mark(journal.CLAUDE_CODE, str(workdir))
    line = json.dumps(CC_ABSORBED)
    _append(transcript, partial=line[:20])
    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          "PROBE-B marker", since) == ""

    _append(transcript, partial=line[20:] + "\n")
    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          "PROBE-B marker", since) == journal.PROCESSED


def test_a_transcript_created_after_the_mark_is_read_from_its_start(cc):
    workdir, transcript = cc
    since = journal.mark(journal.CLAUDE_CODE, str(workdir))
    _append(transcript, CC_ABSORBED)

    assert journal.status(journal.CLAUDE_CODE, str(workdir),
                          "PROBE-B marker", since) == journal.PROCESSED


def test_codex_history_proves_acceptance_and_rollout_processing(cx):
    workdir, history, rollout = cx
    _append(history)
    _append(rollout)
    since = journal.mark(journal.CODEX, str(workdir))

    _append(history, CX_HISTORY)
    assert journal.status(journal.CODEX, str(workdir),
                          "PROBE-Y marker", since) == journal.ACCEPTED

    _append(rollout, CX_ASSISTANT_QUOTING)
    assert journal.status(journal.CODEX, str(workdir),
                          "PROBE-Y marker", since) == journal.ACCEPTED

    _append(rollout, CX_USER_ITEM)
    assert journal.status(journal.CODEX, str(workdir),
                          "PROBE-Y marker", since) == journal.PROCESSED


def test_a_different_message_is_not_evidence(cx):
    workdir, history, rollout = cx
    since = journal.mark(journal.CODEX, str(workdir))
    _append(history, CX_HISTORY)
    _append(rollout, CX_USER_ITEM)

    assert journal.status(journal.CODEX, str(workdir),
                          "PROBE-Y marker and more", since) == ""


def test_a_truncated_journal_is_reread_from_its_start(cx):
    workdir, history, rollout = cx
    _append(rollout, CX_ASSISTANT_QUOTING, CX_ASSISTANT_QUOTING)
    since = journal.mark(journal.CODEX, str(workdir))
    os.truncate(rollout, 0)
    _append(rollout, CX_USER_ITEM)

    assert journal.status(journal.CODEX, str(workdir),
                          "PROBE-Y marker", since) == journal.PROCESSED


def test_unsupported_provider_is_refused(tmp_path):
    assert not journal.supported("openai")
    with pytest.raises(ValueError):
        journal.mark("openai", str(tmp_path))
