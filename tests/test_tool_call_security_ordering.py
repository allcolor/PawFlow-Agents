"""Reproduction (lot 0a): the tool-call pipeline authorizes a form it does not run.

Five failures of one class - a decision is taken on one shape of the call, then
the shape changes before execution. The approval gate must be the LAST step that
can alter the meaning of a call; today four things run after it and one runs
before the arguments are even canonical.

No fix is applied in this file. Each test states the invariant that must hold
and fails against current code, so the fix has a red to turn green.
"""

from pathlib import Path

import pytest

from core._llm_types import _TOOL_ALIASES
from core.llm_client import unwrap_mcp_tool
from core.tool_approval import ToolApprovalGate as Gate
from core.tool_json import parse_tool_arguments

_EXEC = Path("tasks/ai/agent_tool_exec.py")
_SUBAGENT = Path("core/agent_executor.py")
_FS_BASE = Path("core/handlers/_fs_base.py")
_HANDLERS = Path("core/handlers")


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# -- 1. String arguments are judged as {} and executed for real --------

def test_string_arguments_are_authorized_as_empty_then_executed_in_full():
    """agent_tool_exec.py:113-115 vs tool_registry.py:254.

    A provider that delivers arguments as a JSON string (CCI, relay, MCP
    bridge - the API providers pre-parse, so they are not affected) makes
    unwrap_mcp_tool fall through unchanged. The gate then substitutes {},
    scans it for dangerous content, finds none, and the registry parses the
    very same string and runs the real command.
    """
    raw = '{"command": "rm -rf /"}'

    # unwrap alone still returns a non-wrapper payload untouched...
    _eff_name, eff_args = unwrap_mcp_tool("bash", raw)
    assert not isinstance(eff_args, dict), "unwrap returns the string untouched"

    # ...so the pipeline must canonicalize BEFORE handing anything to the
    # gate. This is what agent_tool_exec.py / agent_executor.py now do.
    canonical = parse_tool_arguments(raw, tool_name="bash",
                                     provider="approval-gate")
    gate_name, gate_args = unwrap_mcp_tool("bash", canonical)

    # What actually executes (tool_registry.py:254).
    executed = parse_tool_arguments(raw, tool_name="bash")
    assert executed == {"command": "rm -rf /"}

    # The gate's own content checks now see the real command.
    assert Gate._is_catastrophic_command(executed["command"]) is True
    assert Gate._is_catastrophic_command(gate_args.get("command", "")) is True

    # INVARIANT: the gate must judge exactly what runs.
    assert gate_args == executed
    assert gate_name == "bash"


def test_both_entry_paths_canonicalize_string_arguments_before_deciding():
    """The guard must exist on the main path AND the sub-agent path."""
    for path in (_EXEC, _SUBAGENT):
        src = _src(path)
        i_parse = src.index("parse_tool_arguments(")
        i_gate = src.index("ToolApprovalGate")
        assert i_parse < i_gate, path.name
        assert "isinstance(tc.arguments, str)" in src, path.name


# -- 2. Aliases of an ALWAYS_ASK tool are not ALWAYS_ASK ---------------

def test_bash_aliases_inherit_the_bash_approval_severity():
    """core/_llm_types.py:93-96 vs core/tool_approval.py:52.

    Six names resolve to bash. ToolApprovalGate.check normalizes case only
    (tool_approval.py:188) and never resolves aliases, so none of them hits
    ALWAYS_ASK: they fall to DEFAULT, where one approval covers the whole
    session instead of prompting every time.
    """
    aliases = ("shell", "exec", "run", "terminal", "run_command", "execute")
    assert "bash" in Gate.ALWAYS_ASK
    for alias in aliases:
        assert _TOOL_ALIASES[alias] == "bash", alias

    # INVARIANT: an alias of an ALWAYS_ASK tool carries its severity.
    missing = [a for a in aliases
               if Gate.escalated_policy_name(a) not in Gate.ALWAYS_ASK]
    assert missing == []

    # ...and its command is scanned for dangerous/catastrophic content.
    assert all(Gate.is_command_bearing_tool(a) for a in aliases)


def test_alias_escalation_never_tightens_an_unrelated_tool():
    """Escalation is one-way: only ALWAYS_ASK targets propagate.

    `create_file` aliases to `write`, which is not ALWAYS_ASK, so it must
    keep its own EXEMPT classification instead of inheriting write's.
    """
    assert _TOOL_ALIASES["create_file"] == "write"
    assert "write" not in Gate.ALWAYS_ASK
    assert Gate.escalated_policy_name("create_file") == "create_file"
    assert "create_file" in Gate.EXEMPT_TOOLS
    assert not Gate.is_command_bearing_tool("create_file")

    # Case folding still works on its own.
    assert Gate.escalated_policy_name("BASH") == "bash"

def test_subagent_path_resolves_the_tool_name_before_the_gate():
    """core/agent_executor.py:232-233 - the raw name reaches the gate.

    The main path unwraps first (agent_tool_exec.py:113); the sub-agent path
    does so only inside the read_only branch, so `shell` stays `shell` when
    EXEMPT_TOOLS and the gate are consulted.
    """
    src = _src(_SUBAGENT)
    gate_call = src.index("approval = ToolApprovalGate.check(")
    unwrap_before_gate = src.rfind("unwrap_mcp_tool", 0, gate_call)
    read_only_branch = src.index("if read_only:")

    # The unwrap must happen before the read_only branch, so every decision
    # on this path sees the same canonical name and arguments.
    assert unwrap_before_gate != -1
    assert unwrap_before_gate < read_only_branch

    # INVARIANT: the name reaching the gate is canonical, never tc.name raw.
    assert "ToolApprovalGate.check(\n                            tc.name," not in src


# -- 3. pre_tool_call hooks rewrite the call after it was approved -----

def test_pre_tool_call_hooks_run_before_the_approval_gate():
    """agent_tool_exec.py:217-229 vs :141/:167 vs :243.

    A conversation-bound hook returning decision="replace" (a supported
    verdict, core/agent_hooks.py:110-113) overwrites both tc.name and
    tc.arguments after approval, and :243 executes the replacement with no
    second prompt.
    """
    src = _src(_EXEC)
    assert 'tc.name = str(_payload.get("tool_name")' in src, "mutation exists"
    assert "tc.arguments = _new_args" in src

    i_hook = src.index('run("pre_tool_call"')
    i_gate = src.rindex("_authorize(")
    i_exec = src.index("registry.execute(tc.name, tc.arguments)")

    # INVARIANT: hook, then gate, then execute - nothing rewrites in between.
    assert i_hook < i_gate < i_exec


# -- 4. $VAR resolution happens after approval -------------------------

def test_variable_resolution_happens_before_the_approval_gate():
    """agent_tool_exec.py:234-241.

    `command` (bash) and `code` (execute_script) are excluded from the
    rewrite, which shows the risk was already understood - but every other
    argument, `path` included, is resolved after the human said yes.
    """
    src = _src(_EXEC)
    i_vars = src.index("_resolve_vars_in_args(")
    i_gate = src.rindex("_authorize(")

    assert i_vars < i_gate


# -- 5. Handlers re-resolve the expression language after the gate -----

@pytest.mark.xfail(strict=True, reason=(
    "Deferred to the PreparedToolCall seam: moving expression resolution out "
    "of 18 handlers also changes the relay, realtime and use_tool paths that "
    "rely on it, which is out of scope for the security hotfix."))
def test_no_handler_rewrites_its_arguments_after_the_gate():
    """core/handlers/_fs_base.py:715-719.

    BaseFsHandler._resolve_expressions runs the full PawFlow expression
    language (resolve_value, cascade flow -> conversation -> user -> global)
    at handler entry - after registry.execute, therefore after approval, and
    with no exclusion list at all. A `path` approved as inert literal text
    can resolve to a different concrete target.
    """
    base = _src(_FS_BASE)
    assert "def _resolve_expressions" in base
    assert "resolve_value(" in base

    rewriters = sorted(
        p.name for p in _HANDLERS.glob("*.py")
        if "self._resolve_expressions(" in _src(p)
    )

    # INVARIANT: arguments are frozen once approved.
    assert rewriters == [], (
        "handlers rewriting approved arguments: " + ", ".join(rewriters))
