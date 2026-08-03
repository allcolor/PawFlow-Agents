"""No module may re-implement tool-argument decoding.

The u1/u2 unification routed mcp_bridge and tool_relay_service onto
core.tool_json.parse_tool_arguments, and tests/test_tool_call_parser_unification
guards those two named routes. It could not catch the copies it never knew
about: BaseFsHandler._unwrap_json (19 filesystem handlers, returned {}) and
RealtimeToolBridge._parse_args (voice sessions, returned {}) both survived it
for months.

This test guards the property instead of the route list: any decode-and-repair
loop over tool arguments outside core/tool_json.py is a divergence waiting to
happen - the same envelope succeeding on one path and failing on another.

Remaining sites are listed with the reason they are still open, so a NEW one
fails immediately instead of joining them silently.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Directories that carry the server-side tool-call path.
_SCANNED = ("core", "services", "tasks")

# The canonical decoder itself, plus the vendored flat copy the MCP bridge
# needs to run standalone inside the LLM container (see the u1/u2 docstring).
_CANONICAL = {
    "core/tool_json.py",
    "tools/tool_json.py",
}

# Known remaining decoders, each with the reason it has not moved yet.
# Shrink this list; never grow it without the reason being written down.
_KNOWN_DEBT = {
    # Codex owns the LLM layer right now (fallback/handover work). Beyond the
    # conflict: autoclosing a STREAMING fragment could declare a tool call
    # complete before it is, so this one needs a strict mode or proof that it
    # only ever sees terminal events - not a mechanical swap.
    "core/_llm_types.py",
    # Local fallback that wraps an undecodable payload as {"summary": <raw>}.
    # Routing it through the canonical parser changes what a malformed compact
    # call produces, which is a product decision, not a refactor.
    "core/handlers/compact_result.py",
    # Leaves the string intact on failure and hands it to registry.execute,
    # which decodes it canonically - so nothing is lost. The residue is that
    # _normalize_tool_args is skipped for the string form, i.e. display and
    # reconciliation see a non-canonical shape.
    "services/_tool_relay_execute.py",
}

# `json.loads` applied to something argument-shaped, inside a retry loop or a
# try/except that swallows the failure.
_DECODE = re.compile(r"json\.loads\(\s*(arguments|args|tool_args|raw_args)\b")


def _python_files():
    for directory in _SCANNED:
        for path in (ROOT / directory).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "/build/" in rel or rel.startswith("build/"):
                continue
            yield rel, path


def test_only_the_canonical_module_decodes_tool_arguments():
    offenders = sorted(
        rel for rel, path in _python_files()
        if rel not in _CANONICAL
        and rel not in _KNOWN_DEBT
        and _DECODE.search(path.read_text(encoding="utf-8"))
    )

    assert offenders == [], (
        "these modules decode tool arguments themselves instead of calling "
        "core.tool_json.parse_tool_arguments: " + ", ".join(offenders) + ". "
        "Route them through the canonical parser, or add them to _KNOWN_DEBT "
        "with the reason they cannot move yet."
    )


def test_the_two_paths_fixed_here_no_longer_decode_on_their_own():
    """Regression guard for the two copies u1/u2 missed."""
    for rel in ("core/handlers/_fs_base.py", "services/_realtime_tools.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not _DECODE.search(src), rel
        assert "parse_tool_arguments" in src, rel


def test_the_debt_list_is_accurate():
    """Every entry must still be a real decoder, or it should be dropped."""
    stale = sorted(
        rel for rel in _KNOWN_DEBT
        if not _DECODE.search((ROOT / rel).read_text(encoding="utf-8"))
    )
    assert stale == [], (
        "these no longer decode anything and must leave _KNOWN_DEBT: "
        + ", ".join(stale))
