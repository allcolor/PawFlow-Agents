"""The number a read_history result shows must be usable to ask for it back.

GameDev2, 2026-10-25: it read a slice, reused the ``[#N]`` numbers it was shown
with ``range_by_seq``, and got older September messages back -- "so the
numbering doesn't line up".

It does not, and the tool was the reason. ``[#N]`` is a POSITION index in the
displayed transcript (``_collect`` yields ``start + i``), while ``range_by_seq``
filters on the persisted ``seq`` field of each message. Nothing in the rendered
output ever showed that ``seq``, so the only number an agent could see was the
one the tool would not accept.

Two fixes, pinned here:
- the rendered header carries the real ``seq``;
- ``range_by_index`` accepts the position index the header shows.
"""

import pytest

from core.handlers.history import ReadHistoryHandler


class _Store:
    """Minimal display store: one window of messages with distinct seq values."""

    def __init__(self, messages):
        self._messages = messages

    def iter_display_windows(self, _cid):
        yield 0, list(self._messages)


class _Handler(ReadHistoryHandler):
    def _owns_conversation(self, _store):
        return True


def _msg(seq, msg_id, content):
    return {"seq": seq, "msg_id": msg_id, "role": "assistant",
            "content": content, "source": {"name": "GameDev2"}}


@pytest.fixture()
def handler():
    h = _Handler()
    h._conversation_id = "conv"
    return h


@pytest.fixture()
def store():
    # seq values deliberately do NOT match position indexes: this is the real
    # shape (compaction and hidden rows make seq drift far from the position).
    return _Store([
        _msg(4001, "m1", "first"),
        _msg(4002, "m2", "second"),
        _msg(4003, "m3", "third"),
    ])


def test_the_rendered_header_shows_the_real_seq(handler, store):
    """An agent must be able to read the seq it is expected to pass back."""
    out = handler._render_slice(
        [(0, store._messages[0]), (1, store._messages[1]),
         (2, store._messages[2])],
        "Messages", "", "", action="recent", offset=0, limit=10, total=3)

    assert "seq=4001" in out
    assert "seq=4002" in out
    assert "seq=4003" in out


def test_range_by_index_accepts_the_displayed_position(handler, store):
    """The numbers a result shows must round-trip through range_by_index."""
    out = handler._do_range_by_index(
        store, {"from_index": 1, "to_index": 2}, "", "")

    assert "second" in out
    assert "third" in out
    assert "first" not in out


def test_range_by_seq_still_works_with_the_seq_it_shows(handler, store):
    """The documented seq path keeps working with the value now displayed."""
    out = handler._do_range_by_seq(
        store, {"from_seq": 4002, "to_seq": 4003}, "", "")

    assert "second" in out
    assert "third" in out
    assert "first" not in out


def test_range_by_index_rejects_an_inverted_range(handler, store):
    out = handler._do_range_by_index(
        store, {"from_index": 3, "to_index": 1}, "", "")

    assert out.startswith("Error:")
