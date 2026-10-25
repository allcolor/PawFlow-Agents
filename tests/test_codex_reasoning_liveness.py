"""A reasoning-only Codex turn must still show a sign of life.

2026-10-25: GameDev2 showed "Working (50m 14s)" in the Codex TUI while the
webchat showed nothing at all -- no thinking, no tool call, no text. The model
was working: its rollout held 32 Reasoning items in 20 minutes. But 1472 of the
1514 reasoning items recorded that day carried an EMPTY ``summary`` and only an
``encrypted_content`` blob, so Codex emits no ``response.reasoning_*_text.delta``
and PawFlow had nothing to forward.

Measured on that day: 81 reasoning-only streaks of 3+ items, median 68 s, p90
142 s, longest 303 s. A turn can therefore look dead for five minutes while it
is actively thinking.

A completed reasoning item with no readable text must still tell the user that
reasoning happened -- without inventing its content.
"""

from types import SimpleNamespace

from core.llm_providers._codex_interactive_turn import (
    _CodexInteractiveTurnCoordinator)


def _coord(sink):
    return _CodexInteractiveTurnCoordinator(
        SimpleNamespace(wait_event=lambda *_a, **_k: {}),
        "tok", thinking_callback=sink.append)


def _reasoning_item(summary_text=""):
    """A completed Reasoning item as Codex reports it."""
    return {
        "type": "reasoning",
        "id": "rs_1",
        "summary": ([{"type": "summary_text", "text": summary_text}]
                    if summary_text else []),
        "encrypted_content": "gAAAAAB...",
    }


def test_an_empty_reasoning_summary_still_reports_life():
    """No readable summary must not mean no signal at all."""
    seen = []
    coord = _coord(seen)

    coord._observe_reasoning_item(_reasoning_item())

    assert seen, "a completed reasoning item must publish something"
    assert "reason" in seen[0].lower()


def test_a_readable_reasoning_summary_is_not_duplicated():
    """When the model does publish text, the deltas already carried it."""
    seen = []
    coord = _coord(seen)

    coord._observe_reasoning_item(_reasoning_item("Planning the next step"))

    assert seen == []


def test_the_signal_counts_reasoning_cycles():
    """A count proves the turn moves even when nothing is printed."""
    seen = []
    coord = _coord(seen)

    for _ in range(3):
        coord._observe_reasoning_item(_reasoning_item())

    assert len(seen) == 3
    assert "3" in seen[-1]
