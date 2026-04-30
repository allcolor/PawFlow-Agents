import inspect

from core.agent_prompt_policy import (
    CLI_MCP_SYSTEM_PROMPT,
    COMMON_AGENT_SYSTEM_PROMPT,
)


def test_common_agent_prompt_contains_four_operating_points_without_mcp():
    for title in (
        "Think Before Coding",
        "Simplicity First",
        "Surgical Changes",
        "Goal-Driven Execution",
    ):
        assert title in COMMON_AGENT_SYSTEM_PROMPT
    assert "MCP" not in COMMON_AGENT_SYSTEM_PROMPT
    assert "PawFlow" not in COMMON_AGENT_SYSTEM_PROMPT


def test_cli_mcp_prompt_names_forbidden_native_tools():
    for name in (
        "ApplyPatch",
        "apply_patch",
        "exec_command",
        "Bash",
        "Read",
        "Edit",
        "Grep",
        "web_search",
    ):
        assert name in CLI_MCP_SYSTEM_PROMPT
    assert "There is no native fallback path" in CLI_MCP_SYSTEM_PROMPT


def test_cli_providers_share_the_same_mcp_prompt():
    from core.llm_providers.codex_session import CodexSessionMixin
    from core.llm_providers.gemini import LLMGeminiMixin

    assert CodexSessionMixin._CODEX_PAWFLOW_PREAMBLE == CLI_MCP_SYSTEM_PROMPT
    assert LLMGeminiMixin._GEMINI_PAWFLOW_PREAMBLE == CLI_MCP_SYSTEM_PROMPT


def test_agent_builders_inject_common_prompt_and_cli_mcp_separately():
    from core.agent_executor import resolve_agent_task
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
    from core.llm_providers.claude_code import LLMClaudeCodeMixin
    from core.llm_providers.gemini import LLMGeminiMixin
    from tasks.ai.agent_context import AgentContextMixin

    assert "inject_common_agent_system_prompt" in inspect.getsource(
        AgentContextMixin)
    agent_context_src = inspect.getsource(AgentContextMixin)
    assert "messages.insert(0, LLMMessage(role=\"system\"" in agent_context_src
    assert "base_message_count += 1" in agent_context_src
    assert "inject_common_agent_system_prompt" in inspect.getsource(
        resolve_agent_task)
    assert "append_cli_mcp_system_prompt" in inspect.getsource(
        LLMClaudeCodeMixin._stream_claude_code)
    assert "_codex_app_resume_text" in inspect.getsource(
        LLMCodexAppServerMixin._stream_codex_app_server)
    assert "_gemini_acp_resume_text" in inspect.getsource(
        LLMGeminiMixin._stream_gemini)
