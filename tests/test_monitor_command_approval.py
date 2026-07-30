"""`monitor` is a bash call wearing a different label.

It builds a shell script and hands it to BashHandler in-process, so the
approval gate never sees a `bash` tool call. Before these tests, one approval
of a harmless `monitor` was persisted as `session_allow` and every later
monitor command rode in on it -- including a destructive one, including
local=true. The gate judges the command, so it has to judge this one too.
"""

import unittest.mock as mock

from core.tool_approval import ToolApprovalGate


BENIGN = "tail -f /var/log/build.log"
DESTRUCTIVE = "rm -rf /workspace/data"


def _check(tool, command, perms, **extra):
    """Ask the gate, with `perms` already granted and no dialog available.

    allow_prompt=False makes the answer readable: "needs_approval" means the
    gate wanted to ask, which is exactly what a persisted permission is
    supposed to skip.
    """
    args = {"command": command}
    args.update(extra)
    with mock.patch.object(ToolApprovalGate, "_get_permissions",
                           return_value=perms):
        return ToolApprovalGate.check(
            tool, f"{tool}({command})", "conv1", "user1",
            arguments=args, allow_prompt=False)


def test_monitor_is_judged_on_its_command():
    assert "monitor" in ToolApprovalGate.COMMAND_BEARING_TOOLS


def test_a_granted_monitor_still_runs_a_harmless_command():
    assert _check("monitor", BENIGN, {"monitor": "session_allow"}) == "approved"


def test_a_granted_monitor_does_not_cover_a_destructive_command():
    assert _check("monitor", DESTRUCTIVE,
                  {"monitor": "session_allow"}) == "needs_approval"


def test_always_allow_on_monitor_does_not_cover_a_destructive_command():
    # always_allow is the strongest thing the dialog offers and it is still
    # a permission for the tool, not for every command the tool can carry.
    assert _check("monitor", DESTRUCTIVE,
                  {"monitor": "always_allow"}) == "needs_approval"


def test_the_host_escalation_is_covered_too():
    # local=true runs on the user's own machine rather than the container.
    assert _check("monitor", DESTRUCTIVE, {"monitor": "session_allow"},
                  local=True) == "needs_approval"


def test_bash_keeps_the_behavior_monitor_now_shares():
    assert _check("bash", BENIGN, {"bash": "session_allow"}) == "approved"
    assert _check("bash", DESTRUCTIVE,
                  {"bash": "session_allow"}) == "needs_approval"
