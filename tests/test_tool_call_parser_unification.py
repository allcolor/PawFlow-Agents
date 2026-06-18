"""Tool-call argument decoding is unified on core.tool_json.parse_tool_arguments.

Regression guard for the bug where mcp_bridge.py and tool_relay_service.py each
had their OWN inline decode/repair copy that diverged from the canonical one
(meta_tools): the same envelope succeeded on one route and failed on another.
Now all routes call parse_tool_arguments, and the bridge runs standalone in the
LLM container via a vendored flat copy of core/tool_json.py at /opt/pawflow/.
"""

import importlib.util
import pathlib

import pytest

from core.tool_json import parse_tool_arguments, tool_argument_parse_error

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── envelope shapes that previously diverged between the copies ──────
def test_arguments_json_string_decodes():
    assert parse_tool_arguments('{"command": "ls"}', tool_name="bash") == {"command": "ls"}


def test_dict_passthrough_is_idempotent():
    # The bridge already decodes to a dict; the relay calling parse again must
    # be a no-op (this is what kills the "double decode").
    d = {"command": "ls"}
    assert parse_tool_arguments(d, tool_name="bash") is d


def test_double_encoded_unwraps():
    assert parse_tool_arguments('"{\\"a\\": 1}"', tool_name="x") == {"a": 1}


def test_malformed_returns_error_sentinel():
    parsed = parse_tool_arguments("{bad json", tool_name="bash")
    assert tool_argument_parse_error(parsed)


def test_empty_is_empty_dict():
    assert parse_tool_arguments("", tool_name="x") == {}
    assert parse_tool_arguments("{}", tool_name="x") == {}


# ── vendoring invariant: core/tool_json.py works as a flat module ───
def test_tool_json_is_vendorable_flat():
    """The bridge imports `from tool_json import ...` in the container, where
    core/tool_json.py is bind-mounted flat at /opt/pawflow/tool_json.py."""
    path = ROOT / "core" / "tool_json.py"
    spec = importlib.util.spec_from_file_location("tool_json_flat", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert callable(mod.parse_tool_arguments)
    assert callable(mod.tool_argument_parse_error)
    assert mod.parse_tool_arguments('{"a": 1}', tool_name="x") == {"a": 1}


# ── the inline copies are gone (source guards) ──────────────────────
def test_bridge_uses_canonical_no_inline_copy():
    src = (ROOT / "tools" / "mcp_bridge.py").read_text()
    assert "from tool_json import parse_tool_arguments" in src  # vendored fallback
    assert "parse_tool_arguments(" in src
    # the removed inline-copy helper names must not reappear
    assert "_shared_repair_invalid_json_escapes" not in src
    assert "def _autoclose_truncated_json" not in src


def test_relay_uses_canonical_no_inline_copy():
    src = "".join(f.read_text() for f in sorted((ROOT / "services").glob("*tool_relay*.py")))  # split across _tool_relay_*.py
    assert "parse_tool_arguments(" in src
    assert "Defensive: double-encoded JSON string" not in src


# ── vendoring is wired into every bridge mount site ─────────────────
@pytest.mark.parametrize("relpath", [
    "core/claude_code_pool.py",
    "core/gemini_pool.py",
    "core/codex_pool.py",
    # interactive pool's mcp_bridge/tool_json vendoring lives in the spawn module
    "core/_cci_pool_spawn.py",
    "core/antigravity_observer_pool.py",
    "scripts/install-pawflow.sh",
])
def test_tool_json_vendored_at_every_mount(relpath):
    src = (ROOT / relpath).read_text()
    assert "tool_json.py" in src, f"{relpath} does not vendor tool_json.py next to mcp_bridge"
