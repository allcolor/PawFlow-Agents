"""Load-bearing source-scan markers, declared in one place.

A source-scan test couples itself to a string in a file that does not know it
is a marker. Whoever renames it cannot see the coupling, and the break surfaces
later, in an unrelated test, as a substring error. Markers that more than one
test depends on - or that have already broken once - are declared here and
referenced by name, so that:

* a rename breaks `tests/test_source_anchors.py`, once, with the anchor's name
  and the reason it exists, instead of N tests elsewhere;
* the fix is one line here instead of a hunt through the callers;
* the anchored site carries an `# anchor: <name>` comment, which is the part
  that actually stops the rename before it is written.

The markers are matched against the *reconstructed* loop source: the <=800-line
split routes per-call locals through `st.` and turned the loop's closures into
`_alc_*` methods, and `tests/_agent_core_src.py` reverses that mechanically.
So a marker here is generally not greppable verbatim - each anchor records the
real file and the real spelling in `site`.
"""
from dataclasses import dataclass

from tests import _srcscan
from tests._agent_core_src import agent_core_src

AGENT_LOOP = "the agent loop source (tests/_agent_core_src.py)"


@dataclass(frozen=True)
class Anchor:
    """A marker string that tests depend on, and the reason they do."""
    marker: str
    site: str
    why: str
    after: str = ""
    """Name of the anchor this one is scoped to.

    Some markers are unique only within a region - a call site whose name also
    appears at its definition, say. Scoping keeps the uniqueness check honest
    instead of dropping it.
    """


ANCHORS = {
    "tool_result_batch_guard": Anchor(
        "if results:",
        "tasks/ai/_alc_iteration.py, as `if st.results:`",
        "The read-conflict notice is taken under this guard. pending_block() "
        "clears on read, so taking it when the batch has no result to carry "
        "it would drop the notice on the floor.",
    ),
    "tool_result_loop_header": Anchor(
        "for tc, result_text in results:",
        "tasks/ai/_alc_iteration.py, as `for st.tc, st.result_text in "
        "st.results:`",
        "test_codex_mid_turn_compact.py slices the tool-result loop body with "
        "this header as its start marker. Rewriting it - an enumerate(), say "
        "- breaks that test from a distance; it already happened once, which "
        "is why the loop counts down instead of enumerating.",
    ),
    "tool_output_envelope": Anchor(
        "_wrapped = self._wrap_tool_output",
        "tasks/ai/_alc_iteration.py",
        "Marks where tool output enters the untrusted-content envelope. Tests "
        "assert that the platform note is attached to its output, never to "
        "the raw text going in.",
    ),
    "platform_note_attach": Anchor(
        "_attach_platform_note",
        "defined in tasks/ai/agent_core.py, called in tasks/ai/"
        "_alc_iteration.py",
        "A PawFlow-generated warning must sit outside the untrusted envelope, "
        "otherwise the agent is taught to distrust our own warnings. The name "
        "must also not begin with `def _append`, which is the end boundary of "
        "the post_append_compact_helper region - that collision is what this "
        "whole registry came out of.",
        after="tool_output_envelope",
    ),
    "tool_result_batch_end": Anchor(
        "# Per-turn aggregate cap",
        "tasks/ai/_alc_iteration.py",
        "End boundary of the tool-result loop region in "
        "test_codex_mid_turn_compact.py.",
    ),
    "post_append_compact_helper": Anchor(
        "def _maybe_auto_compact_after_append",
        "tasks/ai/_alc_closures1.py, as `def "
        "_alc_maybe_auto_compact_after_append(self, st, ...)`",
        "Two tests in test_codex_mid_turn_compact.py slice this helper to "
        "check it forwards trigger_fraction and takes the CLI restart path.",
    ),
    "append_helper": Anchor(
        "def _append",
        "tasks/ai/_alc_closures1.py, as `def _alc_append(self, st, msg)`",
        "End boundary of the post_append_compact_helper region. It is a "
        "prefix of any other `_append*` helper, so a new one silently stole "
        "the boundary once and emptied the region. tests/_srcscan.py now "
        "rejects prefix matches, but keep new helpers off this prefix.",
    ),
}

_cache = {}


def loop_src():
    """The reconstructed agent-loop source the anchors are matched against."""
    if "src" not in _cache:
        _cache["src"] = agent_core_src()
    return _cache["src"]


def marker(name):
    """The marker string registered under `name`."""
    try:
        return ANCHORS[name].marker
    except KeyError:
        raise KeyError(
            f"no anchor named {name!r}; known anchors: "
            f"{', '.join(sorted(ANCHORS))}") from None


def find(name, src=None, start=0):
    """Index of the anchor `name` in the loop source."""
    m = marker(name)  # names the known anchors when `name` is a typo
    src = src if src is not None else loop_src()
    scope = ANCHORS[name].after
    if scope:
        start = max(start, find(scope, src) + len(marker(scope)))
    return _srcscan.find(src, m, what=AGENT_LOOP, start=start)


def region(start_name, end_name, src=None):
    """The loop source between two anchors."""
    src = src if src is not None else loop_src()
    a = find(start_name, src)
    b = _srcscan.find(src, marker(end_name), what=AGENT_LOOP,
                      start=a + len(marker(start_name)))
    return src[a:b]
