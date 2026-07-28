"""Background work: PawFlow's CamelCase tools must be reachable, and the
guidance must name the tools that make polling unnecessary.

The defect these cover: ``use_tool`` lowercased any CamelCase name to map
Claude Code's native spellings (Read -> read) onto PawFlow tools. PawFlow
registers CamelCase tools of its own, and those are precisely the ones whose
CC built-in equivalents are disallowed — so ``Monitor`` answered "unknown tool
'monitor'" with no way through, and agents fell back to sleep-polling a log.
"""

from pathlib import Path

from core.tool_registry import create_default_registry

ROOT = Path(__file__).resolve().parent.parent

#: PawFlow tools registered under a CamelCase name. Lowercasing any of them
#: makes them unreachable through use_tool.
CAMEL_TOOLS = ("Monitor", "ScheduleWakeup", "PushNotification",
               "EnterPlanMode", "ExitPlanMode")


def _bridge_source():
    return (ROOT / "tools" / "mcp_bridge.py").read_text(encoding="utf-8")


def test_pawflow_registers_camelcase_tools_that_must_survive_name_mapping():
    registry = create_default_registry()
    names = {h.name for h in registry.list_tools()}

    for tool in CAMEL_TOOLS:
        assert tool in names, f"{tool} is no longer registered under that name"


def test_monitor_is_registered_so_use_tool_can_reach_it():
    registry = create_default_registry()

    assert registry.get("Monitor") is not None
    # ...and not under the lowered spelling the bridge used to rewrite to.
    assert registry.get("monitor") is None


def test_bridge_tries_the_name_as_written_before_lowering_it():
    src = _bridge_source()

    # The rewrite must not happen before the call: the name is only lowered
    # after the server has said it does not know it.
    assert "_lower_fallback = tool_name.lower()" in src
    assert '"unknown tool" in result' in src
    assert "tool_name=_lower_fallback" in src


def test_the_camelcase_rewrite_is_no_longer_unconditional():
    src = _bridge_source()

    # The old shape assigned straight into tool_name, so the first (and only)
    # request already carried the wrong name.
    assert 'tool_name = _lower' not in src.replace("_lower_fallback", "")


def test_cc_native_monitor_stays_blocked_so_the_pawflow_one_is_used():
    # CC's built-in would run in the CLI container, not against the relay
    # workspace; blocking it is correct, which is exactly why the MCP one has
    # to work.
    from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin

    disallowed = ClaudeCodeSessionMixin._DISALLOWED_BUILTIN_TOOLS

    assert "Monitor" in disallowed
    assert "ScheduleWakeup" in disallowed
    assert "PushNotification" in disallowed


def test_guidance_names_the_tools_instead_of_a_vague_background_hint():
    src = (ROOT / "tasks" / "ai" / "_agentctx_p3.py").read_text(encoding="utf-8")

    assert "Use `Monitor`" in src
    assert "run_tests" in src
    assert "security_scan" in src


def test_guidance_forbids_the_sleep_poll_pattern_by_name():
    src = (ROOT / "tasks" / "ai" / "_agentctx_p3.py").read_text(encoding="utf-8")

    assert "NEVER poll with sleep" in src
    assert "sleep N; tail log" in src


def test_apply_patch_says_when_to_prefer_it_not_only_what_it_does():
    # It used to describe the format and cite Codex, which reads as another
    # harness's idiom -- so agents kept issuing repeated edit calls instead.
    from core.handlers.apply_patch import ApplyPatchHandler

    description = ApplyPatchHandler().description

    assert "3 or more separate places" in description
    assert "already edited this turn" in description


def test_edit_points_at_apply_patch_at_the_moment_of_choosing():
    from core.handlers.edit_handler import EditHandler

    assert "apply_patch" in EditHandler().description


def test_edit_guidance_uses_a_countable_trigger_not_an_adjective():
    # "patch-shaped changes" needed a judgement call that never fired; a
    # count does.
    src = (ROOT / "tasks" / "ai" / "_agentctx_p3.py").read_text(encoding="utf-8")

    assert "patch-shaped" not in src
    assert "3+ places in the same file" in src


def test_bash_points_at_monitor_where_the_agent_actually_reads():
    from core.handlers.bash import BashHandler

    handler = BashHandler()

    assert "Monitor" in handler.description
    assert "Monitor" in handler.parameters_schema[
        "properties"]["run_in_background"]["description"]
