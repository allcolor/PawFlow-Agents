"""Guardrails for this repo's source-scan test convention.

Parts of the agent loop are not executable in isolation - `_alc_iteration`
threads one large `st` state object through a dozen collaborators - so a few
structural properties (ordering, guard placement, which envelope wraps what)
are pinned by scanning source text instead of by running it. The convention
works, but it couples a test to a marker string living in a file that has no
idea it is a marker, and the raw `str.index` failure modes say nothing useful:

    ValueError: substring not found        # which marker? which file? renamed?
    assert 'trigger_fraction=...' in ''    # the region silently went empty

The second shape is the expensive one. `def _append` is a prefix of
`def _append_platform_note`; adding that helper earlier in the file moved a
region's end boundary onto the wrong `def`, emptied the slice, and every
assertion inside it then failed for a reason unrelated to what it tests.

`find()` and `region()` refuse both cases up front and report which marker, how
many times it matched, on which lines, and - for the prefix case - what it
collided with. They do not remove the coupling; they make it diagnosable in
seconds instead of a full-suite run plus a bisection. Markers that more than
one test depends on belong in `tests/_anchors.py`.
"""
import re

_WORD = re.compile(r"\w")


class MarkerError(AssertionError):
    """A source-scan marker no longer identifies exactly one place."""


def _line(src, idx):
    return src.count("\n", 0, idx) + 1


def _at(src, idx, width=64):
    return src[idx:idx + width].split("\n")[0]


def _scan(src, marker):
    """Split every occurrence of `marker` into whole-token and prefix hits.

    A marker ending on an identifier character must not be followed by one:
    `def _append` matching inside `def _append_platform_note` is the collision
    this module exists to report. Only the tail is checked - a marker that
    deliberately starts mid-token (`append(msg)`) is legitimate and common.
    """
    exact, prefix = [], []
    tail_is_word = bool(marker) and bool(_WORD.match(marker[-1]))
    i = src.find(marker)
    while i != -1:
        after = src[i + len(marker):i + len(marker) + 1]
        (prefix if tail_is_word and after and _WORD.match(after) else
         exact).append(i)
        i = src.find(marker, i + 1)
    return exact, prefix


def find(src, marker, *, what="the scanned source", start=0):
    """Index of the one place `marker` identifies in `src`, at or after `start`.

    Raises MarkerError - never ValueError, never a silent wrong answer - when
    the marker is missing, ambiguous, or only matches as a prefix.
    """
    if not marker:
        raise MarkerError("an empty source-scan marker matches everything")
    exact, prefix = _scan(src, marker)
    hits = [i for i in exact if i >= start]
    if len(hits) == 1:
        return hits[0]

    where = f" at or after line {_line(src, start)}" if start else ""
    if not hits and prefix:
        collisions = "\n".join(
            f"    line {_line(src, i)}: {_at(src, i)!r}" for i in prefix[:5])
        raise MarkerError(
            f"source-scan marker {marker!r} matches nothing in {what}{where} "
            f"as a whole token, but {len(prefix)} longer name(s) start with "
            f"it:\n{collisions}\n"
            "  A marker must match a whole token, or it silently anchors to "
            "the wrong place. Either rename the new symbol so it does not "
            "begin with the marker, or make the marker longer.")
    if not hits:
        after = " (it exists earlier, but not after the start marker)" if (
            exact and start) else ""
        raise MarkerError(
            f"source-scan marker {marker!r} is not in {what}{where}{after}. "
            "It was almost certainly renamed or reformatted; the code it "
            "points at is what this test is about, so update the marker "
            "rather than the assertion - and check tests/_anchors.py, which "
            "may name it.")
    lines = ", ".join(str(_line(src, i)) for i in hits[:8])
    raise MarkerError(
        f"source-scan marker {marker!r} is ambiguous in {what}{where}: "
        f"{len(hits)} whole-token matches (lines {lines}). Narrow the region "
        "first, or pass a longer marker - picking the first match would pin "
        "this test to whichever one happens to come first today.")


def region(src, start_marker, end_marker, *, what="the scanned source"):
    """The source between two markers, both required to be unambiguous.

    The end marker is searched *after* the start marker, so a new symbol that
    duplicates the end marker earlier in the file can no longer reverse the
    slice into emptiness - the failure that motivated this module.
    """
    a = find(src, start_marker, what=what)
    b = find(src, end_marker, what=what, start=a + len(start_marker))
    return src[a:b]
