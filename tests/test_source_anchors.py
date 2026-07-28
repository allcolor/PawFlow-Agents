"""The source-scan convention checks itself.

Two things are covered here: that every declared anchor still identifies
exactly one place in the code it points at, and that `tests/_srcscan.py`
actually refuses the marker failures it claims to refuse. The second half
matters as much as the first - a guardrail that silently stops guarding is
worse than none, because the tests built on it keep passing.
"""
import pytest

from tests import _anchors, _srcscan
from tests._srcscan import MarkerError


# -- The registry --

@pytest.mark.parametrize("name", sorted(_anchors.ANCHORS))
def test_anchor_still_identifies_exactly_one_place(name):
    """A rename lands here, once, with the reason the anchor exists.

    Without the registry the same rename breaks whichever tests happen to use
    the marker, at a distance, with a bare substring error.
    """
    a = _anchors.ANCHORS[name]
    try:
        _anchors.find(name)
    except AssertionError as exc:
        pytest.fail(
            f"anchor `{name}` no longer resolves.\n"
            f"  marker: {a.marker!r}\n"
            f"  site:   {a.site}\n"
            f"  why it exists: {a.why}\n\n"
            f"{exc}\n\n"
            "Fix the marker in tests/_anchors.py (one line, one place) or "
            "restore the name in the source.")


def test_every_anchor_documents_its_site_and_its_reason():
    """An anchor with no stated reason cannot be safely retired later."""
    for name, a in _anchors.ANCHORS.items():
        assert a.site, f"anchor `{name}` does not say where it lives"
        assert len(a.why) > 40, f"anchor `{name}` does not say why it exists"


def test_scoped_anchor_is_checked_inside_its_scope():
    """`after` must narrow the search, not disable the uniqueness check."""
    src = _anchors.loop_src()
    scoped = _anchors.ANCHORS["platform_note_attach"]
    assert scoped.after == "tool_output_envelope"
    # Unique only within the scope: unscoped, the definition and the call site
    # both match, and the check must still be a real one.
    assert src.count(scoped.marker) > 1
    assert _anchors.find("platform_note_attach") > _anchors.find(
        "tool_output_envelope")


def test_anchor_lookup_names_the_known_anchors():
    with pytest.raises(KeyError, match="tool_result_loop_header"):
        _anchors.marker("no_such_anchor")


# -- The guardrails --
#
# Each case below is a failure that actually happened, or one line away from it.

_SRC = "\n".join([
    "def _append(msg):",
    "    pass",
    "",
    "def _maybe_auto_compact_after_append(msg, reason):",
    "    compact(trigger_fraction=trigger_fraction)",
    "",
    "def _later():",
    "    pass",
])


def test_a_prefix_collision_is_reported_not_silently_matched():
    """The exact bug: `def _append` matching `def _append_platform_note`.

    Plain str.index() returns the wrong offset, the region collapses, and the
    assertions inside fail against an empty string.
    """
    src = _SRC.replace("def _append(msg):", "def _append_platform_note(c, n):")
    with pytest.raises(MarkerError) as exc:
        _srcscan.find(src, "def _append")
    assert "whole token" in str(exc.value)
    assert "_append_platform_note" in str(exc.value)
    assert "line 1" in str(exc.value)


def test_a_missing_marker_says_which_one_and_where_to_look():
    with pytest.raises(MarkerError) as exc:
        _srcscan.find(_SRC, "def _renamed", what="the loop source")
    assert "'def _renamed'" in str(exc.value)
    assert "the loop source" in str(exc.value)
    assert "_anchors" in str(exc.value)


def test_an_ambiguous_marker_is_refused_rather_than_pinned_to_the_first():
    with pytest.raises(MarkerError) as exc:
        _srcscan.find(_SRC + _SRC, "def _later")
    assert "ambiguous" in str(exc.value)
    assert "2 whole-token matches" in str(exc.value)


def test_an_empty_marker_is_refused():
    with pytest.raises(MarkerError):
        _srcscan.find(_SRC, "")


def test_region_searches_the_end_marker_after_the_start_marker():
    """A duplicate end marker earlier in the file must not empty the region.

    `_srcscan.region` is the whole reason the original failure cannot repeat:
    the end boundary can only be found after the start.
    """
    body = _srcscan.region(
        _SRC, "def _maybe_auto_compact_after_append", "def _later")
    assert "trigger_fraction=trigger_fraction" in body
    # `def _append` occurs *before* the start marker; a plain index() would
    # have produced a reversed, empty slice.
    with pytest.raises(MarkerError) as exc:
        _srcscan.region(
            _SRC, "def _maybe_auto_compact_after_append", "def _append")
    assert "not after the start marker" in str(exc.value)


def test_a_marker_may_start_mid_token():
    """Only the tail is boundary-checked; `append(msg)` must keep working."""
    assert _srcscan.find(_SRC, "append(msg)") > 0
