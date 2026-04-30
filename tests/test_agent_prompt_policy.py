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
    from tasks.ai.agent_core import AgentCoreMixin
    from tasks.ai.agent_context import AgentContextMixin

    assert "inject_common_agent_system_prompt" in inspect.getsource(
        AgentContextMixin)
    agent_context_src = inspect.getsource(AgentContextMixin)
    assert '"_provider_system_prompt": _provider_system_prompt' in agent_context_src
    assert "messages.insert(0, LLMMessage(role=\"system\"" not in agent_context_src
    assert "inject_common_agent_system_prompt" in inspect.getsource(
        resolve_agent_task)
    agent_core_src = inspect.getsource(AgentCoreMixin)
    assert "_with_provider_system_prompt" in agent_core_src
    assert "_provider_system_prompt" in agent_core_src
    assert 'ctx.get("_is_cli_provider") and ctx.get("_cli_has_session")' in agent_core_src
    assert "append_cli_mcp_system_prompt" in inspect.getsource(
        LLMClaudeCodeMixin._stream_claude_code)
    assert "_codex_app_resume_text" in inspect.getsource(
        LLMCodexAppServerMixin._stream_codex_app_server)
    assert "_gemini_acp_resume_text" in inspect.getsource(
        LLMGeminiMixin._stream_gemini)


def test_cli_resume_prompts_do_not_reinject_system_prompt():
    from core.llm_client import LLMMessage
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
    from core.llm_providers.gemini import LLMGeminiMixin

    messages = [
        LLMMessage(role="system", content="SYSTEM", conversation_id="conv1"),
        LLMMessage(role="user", content="hello", conversation_id="conv1"),
    ]

    codex = LLMCodexAppServerMixin()
    gemini = LLMGeminiMixin()

    assert codex._codex_app_resume_text(messages) == "hello"
    assert "<system_instructions>" not in codex._codex_app_resume_text(messages)
    assert gemini._gemini_acp_resume_text(messages) == "hello"
    assert "<system_instructions>" not in gemini._gemini_acp_resume_text(messages)
    assert gemini._gemini_acp_live_text("preempt") == "preempt"


def test_agent_skills_use_assigned_skills_as_single_source():
    from core.agent_executor import resolve_agent_task
    import core.conv_agent_config as conv_agent_config
    from core.conv_agent_config import add_agent_to_conv
    from tasks.ai.actions import agent_resource
    from tasks.ai.agent_context import AgentContextMixin

    context_src = inspect.getsource(AgentContextMixin)
    executor_src = inspect.getsource(resolve_agent_task)
    resource_src = inspect.getsource(agent_resource)
    add_src = inspect.getsource(add_agent_to_conv)
    config_src = inspect.getsource(conv_agent_config)

    assert 'get("assigned_skills")' in context_src
    assert 'agent_def.get("assigned_skills")' in executor_src
    assert '"skills": []' not in config_src
    assert '"skills": skills or []' not in add_src
    assert '"assigned_skills"' in resource_src


def test_compaction_does_not_persist_provider_system_prompt():
    from tasks.ai.agent_compaction import AgentCompactionMixin

    src = inspect.getsource(AgentCompactionMixin._persist_context)
    assert 'persisted[0].role == "system"' in src
    assert "persisted = persisted[1:]" in src


def test_context_editor_displays_tool_call_only_messages():
    from tasks.ai.actions import context_ops

    context_ops_src = inspect.getsource(context_ops)
    editor_src = open("tasks/io/chat_ui/context_editor.js", encoding="utf-8").read()

    assert '"tool_calls": m.get("tool_calls") or []' in context_ops_src
    assert "function _ctxToolCallsText" in editor_src
    assert "if (!String(content).trim() && m.has_tool_calls)" in editor_src
