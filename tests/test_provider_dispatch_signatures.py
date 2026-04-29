"""Static check: every provider's `_stream_*` method accepts the kwargs
that `LLMClient.complete_stream` actually passes to it.

Without this test, a kwarg drift between the dispatch site
(`core/llm_client.py:complete_stream._do_stream`) and a per-provider
`_stream_*` signature only surfaces at runtime when the provider is
actually invoked — the bug shipped in `a443a68` (codex/gemini
`_stream_*` got a `thinking_callback` kwarg that the signatures didn't
accept) is exactly that class. The contract enforced here is read live
from the source: each branch of the dispatch is parsed, the kwargs it
passes are extracted, and the corresponding `_stream_*` is checked to
accept them.
"""

import ast
import inspect
import json
from pathlib import Path

import core.llm_client  # registers providers
from core.llm_client import LLMClient


def _parse_dispatch_branches() -> dict:
    """Return {provider: {kwarg_name, ...}} as actually called in
    `complete_stream`'s `_do_stream` block.

    Walks the AST of core/llm_client.py and finds each
    `if/elif self.provider == "X": ... self._stream_*(...)` call,
    collecting the keyword argument names passed.
    """
    src = Path(core.llm_client.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # Pattern: comparing self.provider to a string literal.
        provider_name = None
        test = node.test
        if (isinstance(test, ast.Compare)
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.left, ast.Attribute)
                and isinstance(test.left.value, ast.Name)
                and test.left.value.id == "self"
                and test.left.attr == "provider"
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and isinstance(test.comparators[0].value, str)):
            provider_name = test.comparators[0].value
        if not provider_name:
            continue
        # Find any `self._stream_*(...)` call inside this branch's body.
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == "self"
                    and sub.func.attr.startswith("_stream_")):
                kwargs = {kw.arg for kw in sub.keywords if kw.arg is not None}
                out[provider_name] = (sub.func.attr, kwargs)
                break
    return out


def _accepts_kwarg(fn, kwarg: str) -> bool:
    sig = inspect.signature(fn)
    if kwarg in sig.parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in sig.parameters.values())


def test_dispatch_kwargs_match_signatures():
    """Every kwarg the dispatch passes must be accepted by the target.

    Reads the dispatch live from llm_client.py's AST and validates each
    provider's `_stream_*` signature accepts the exact set passed at the
    call site. Drift in either direction (dispatch adds a kwarg the
    method doesn't take, or method drops a kwarg the dispatch still
    passes) fails this test — same class as the codex/gemini
    `thinking_callback` runtime crash from a443a68.
    """
    branches = _parse_dispatch_branches()
    assert branches, "failed to parse any provider branch from llm_client.py"
    failures = []
    for provider, (method_name, kwargs) in branches.items():
        if not hasattr(LLMClient, method_name):
            failures.append(
                f"provider '{provider}' → LLMClient.{method_name} missing")
            continue
        fn = getattr(LLMClient, method_name)
        for kw in sorted(kwargs):
            if not _accepts_kwarg(fn, kw):
                failures.append(
                    f"LLMClient.{method_name} (provider '{provider}') is "
                    f"missing kwarg `{kw}` — the dispatch in "
                    f"complete_stream._do_stream passes it. Add it to the "
                    f"signature or remove it from the dispatch.")
    if failures:
        raise AssertionError("\n".join(failures))


def test_gemini_provider_uses_acp_runtime_contracts():
    """Gemini provider must use ACP, not the old headless stream-json path."""
    from core.llm_providers.gemini import LLMGeminiMixin

    provider_src = inspect.getsource(LLMGeminiMixin)
    stream_src = inspect.getsource(LLMGeminiMixin._stream_gemini)
    proc_src = inspect.getsource(LLMGeminiMixin._gemini_acp_start_process)
    settings_src = inspect.getsource(LLMGeminiMixin._gemini_acp_write_settings)
    settings_mcp_src = inspect.getsource(LLMGeminiMixin._gemini_acp_settings_mcp_servers)
    mcp_src = inspect.getsource(LLMGeminiMixin._gemini_acp_mcp_servers)

    assert 'args = ["--debug", "--acp"]' in proc_src
    assert '"--yolo"' not in proc_src
    assert 'if model:' in proc_src
    assert 'args = ["--model", model, *args]' in proc_src
    assert '"session/new"' in provider_src
    assert '"session/load"' in provider_src
    assert '"authenticate"' in provider_src
    assert "_gemini_acp_start_stdout_drain" in stream_src
    assert '"session/prompt"' in stream_src
    assert "GeminiLiveRegistry" in stream_src
    assert "[gemini-acp-live] REUSE" in stream_src
    assert "[gemini-acp-live] active" in stream_src
    assert "[gemini-acp-live] keep-alive" in stream_src
    assert "live_session.turn_lock.acquire()" in stream_src
    assert "live_session.turn_lock.release()" in stream_src
    assert "store.get_extra(conv_id, session_key) or" in stream_src
    assert "loading in fresh ACP process" in stream_src
    assert "session_id = \"\"\n                initial_text" not in stream_src
    assert "_GeminiAcpCapacityError" in provider_src
    assert "Gemini capacity exhausted" in stream_src
    send_src = inspect.getsource(LLMGeminiMixin._gemini_send_user_message)
    assert '"session/cancel"' in send_src
    assert '"session/prompt"' in send_src
    assert "preempt_req_id" in send_src
    assert "return True" in send_src
    assert '"type": "image"' in inspect.getsource(LLMGeminiMixin._gemini_acp_image_item)
    assert '"mimeType"' in inspect.getsource(LLMGeminiMixin._gemini_acp_image_item)
    assert '"includeThoughts": True' in settings_src
    assert '"thinkingLevel"' in settings_src
    assert '"thinkingBudget"' in settings_src
    assert '"modelConfigs"' in settings_src
    assert '"overrides"' in settings_src
    assert '"customOverrides"' in settings_src
    assert '"match": {}' in settings_src
    assert '"general": {"defaultApprovalMode": "auto_edit", "maxAttempts": 1}' in settings_src
    assert '"useWriteTodos": False' in settings_src
    assert 'if model:' in settings_src
    assert 'settings["model"] = {"name": model}' in settings_src
    assert '"pawflow-current"' not in settings_src
    assert 'model or "gemini-3-pro-preview"' not in settings_src
    assert '"/usr/bin/python3"' in mcp_src
    assert '"mcpServers"' not in settings_src
    assert '"folderTrust": {"enabled": False}' in settings_src
    assert '"tools": {"exclude": excluded_core_tools}' in settings_src
    assert '"core": [' not in settings_src
    assert '"run_shell_command"' in settings_src
    assert '"web_fetch"' in settings_src
    assert '"allowMCPServers": ["pawflow"]' in settings_src
    assert '"mcp": {"allowed": ["pawflow"]}' in settings_src
    assert '"excludeTools": excluded_core_tools' in settings_src
    assert '"list_directory"' in settings_src
    assert '"read_file"' in settings_src
    assert '"type": "stdio"' in settings_mcp_src
    assert '"timeout": 15000' in settings_mcp_src
    assert '"trust": True' in settings_mcp_src
    assert '"env": env' in settings_mcp_src
    assert '"cwd": mcp_cwd' in settings_mcp_src
    assert "mcp_cwd=container_dir" in stream_src
    assert "_gemini_acp_new_session(proc, container_dir, mcp_servers)" in stream_src
    assert "_gemini_acp_load_session(proc, session_id, container_dir, mcp_servers)" in stream_src
    assert "PAWFLOW_GEMINI_ACP_FIRST_EVENT_TIMEOUT" not in stream_src
    assert "first-event worker timeout" not in stream_src
    assert "no ACP event after" not in stream_src
    assert "no prompt activity after" not in stream_src
    assert "_prompt_activity_deadline" not in stream_src
    assert "_skip_resume_replay" in stream_src
    assert 'kind == "available_commands_update"' in stream_src
    assert "_first_event_reader" not in stream_src
    assert "timeout_s=None" in stream_src
    assert "incoming_id is not None" in stream_src
    assert "int(incoming_id)" in stream_src
    read_src = inspect.getsource(LLMGeminiMixin._gemini_acp_read_message)
    assert "timeout_s is not None" in read_src
    assert "refusing blocking readline" in read_src
    assert "stdout_q.get(timeout=min(0.5, remaining))" in read_src
    assert "wait_log_s" in read_src
    assert "[gemini-acp][wait]" in read_src
    assert "[gemini-acp][gap]" in stream_src
    assert "[gemini-acp][recv]" in stream_src
    assert "_gemini_acp_message_preview" in provider_src
    assert '"session/request_permission"' in stream_src
    assert "_gemini_acp_permission_result" in provider_src
    assert "[gemini-acp][stderr]" in inspect.getsource(LLMGeminiMixin._gemini_acp_start_stderr_drain)
    assert "timeout_s=30.0" in inspect.getsource(LLMGeminiMixin._gemini_acp_initialize)
    assert '"oauth-personal"' in inspect.getsource(LLMGeminiMixin._gemini_acp_authenticate)
    assert "_gemini_acp_enqueue_live_tool_tc" in provider_src
    assert "ANY_TOOL" in provider_src and "ANY_ARGS_HASH" in provider_src
    assert "_GEMINI_PAWFLOW_PREAMBLE" in provider_src
    assert "_gemini_acp_live_text" in provider_src
    assert "self._gemini_acp_live_text(text or \"\")" in send_src
    assert "self._gemini_acp_live_text(" in stream_src


def test_cli_providers_do_not_force_default_model_flags():
    from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
    from core.llm_providers.codex_session import CodexSessionMixin
    from core.llm_providers.gemini import LLMGeminiMixin

    for provider in ("claude-code", "codex-app-server", "gemini"):
        assert LLMClient(provider=provider, config={}).default_model == ""

    assert '"--model", model or "sonnet"' not in inspect.getsource(
        ClaudeCodeSessionMixin._build_claude_cmd)
    assert '"--model", model or "gpt-5.2-codex"' not in inspect.getsource(
        CodexSessionMixin._build_codex_cmd)
    codex_src = inspect.getsource(LLMCodexAppServerMixin)
    assert 'model or "gpt-5.4"' not in codex_src
    assert 'params["model"] = model' in codex_src
    gemini_src = inspect.getsource(LLMGeminiMixin._gemini_acp_start_process)
    assert 'if model:' in gemini_src
    assert 'args = ["--model", model, *args]' in gemini_src


def test_gemini_acp_capacity_error_is_non_retryable_text():
    from core.llm_providers.gemini import LLMGeminiMixin

    text = LLMGeminiMixin._gemini_acp_capacity_error({
        "code": 500,
        "message": "You have exhausted your capacity on this model. Your quota will reset after 7s.",
    })

    assert text == "Gemini model capacity exhausted; cooldown 7s"
    assert "500" not in text
    assert "reset" not in text.lower()


def test_gemini_acp_permission_result_accepts_pawflow_allow_option():
    from core.llm_providers.gemini import LLMGeminiMixin

    result = LLMGeminiMixin._gemini_acp_permission_result({
        "tool": "mcp_pawflow_use_tool",
        "options": [{"kind": "allow_once", "optionId": "allow-1"}],
    })
    assert result == {"outcome": {"outcome": "selected", "optionId": "allow-1"}}


def test_gemini_acp_permission_result_denies_builtin_allow_option():
    from core.llm_providers.gemini import LLMGeminiMixin

    result = LLMGeminiMixin._gemini_acp_permission_result({
        "tool": "list_directory",
        "options": [{"kind": "allow_once", "optionId": "allow-1"}],
    })
    assert result == {"outcome": {"outcome": "cancelled"}}


def test_gemini_acp_permission_result_cancels_without_allow_option():
    from core.llm_providers.gemini import LLMGeminiMixin

    result = LLMGeminiMixin._gemini_acp_permission_result({"options": []})
    assert result == {"outcome": {"outcome": "cancelled"}}


def test_agent_core_passes_live_block_callback_to_acp_providers():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    assert 'block_callback=_cli_block_callback if _client_provider in ("codex-app-server", "gemini") else None' in src



def test_gemini_acp_extracts_inner_pawflow_tool_arguments():
    from core.llm_providers.gemini import LLMGeminiMixin

    text = (
        "MCP tool 'use_tool' reported tool error for function call: "
        "{'name': 'use_tool', 'args': {'tool_name': 'list_dir', "
        "'arguments': {'path': '/workspace'}}}"
    )
    assert LLMGeminiMixin._gemini_acp_extract_tool_arguments_from_text(text) == {
        "tool_name": "list_dir",
        "arguments": {"path": "/workspace"},
    }


def test_gemini_acp_displays_inner_pawflow_tool_name_from_result():
    from core.llm_providers.gemini import LLMGeminiMixin

    result = '<tool_output tool="read">\nhello\n</tool_output>'
    assert LLMGeminiMixin._gemini_acp_display_tool_name("use_tool", result) == "read"

    error = "MCP tool 'use_tool' reported tool error for function call: {'tool_name': 'list_dir'}"
    assert LLMGeminiMixin._gemini_acp_display_tool_name("use_tool", error) == "list_dir"


def test_gemini_acp_displays_inner_pawflow_tool_args():
    from core.llm_providers.gemini import LLMGeminiMixin

    name, args = LLMGeminiMixin._gemini_acp_display_tool_call(
        "use_tool", {"tool_name": "list_dir", "arguments": {"path": "/workspace"}})
    assert name == "list_dir"
    assert args == {"path": "/workspace"}


def test_gemini_acp_drops_serialized_tool_calls_from_thinking():
    from core.llm_providers.gemini import LLMGeminiMixin

    raw = '{"tool_name":"use_tool","arguments":{"tool_name":"read"}}'
    assert LLMGeminiMixin._gemini_acp_clean_thinking(raw) == ""
    assert LLMGeminiMixin._gemini_acp_clean_thinking("I need to inspect files.")


def test_gemini_acp_recovers_replayed_tool_args_from_history(tmp_path):
    from core.llm_providers.gemini import LLMGeminiMixin

    chats = tmp_path / ".gemini" / "tmp" / "gemini" / "chats"
    chats.mkdir(parents=True)
    tool_id = "mcp_pawflow_use_tool-123-1"
    (chats / "session.jsonl").write_text(
        json.dumps({
            "toolCalls": [{
                "id": tool_id,
                "name": "mcp_pawflow_use_tool",
                "args": {"tool_name": "list_dir", "arguments": {"path": "/workspace"}},
            }],
        }) + "\n",
        encoding="utf-8",
    )
    assert LLMGeminiMixin._gemini_acp_history_tool_arguments(str(tmp_path), tool_id) == {
        "tool_name": "list_dir",
        "arguments": {"path": "/workspace"},
    }


def test_agent_core_hides_schema_tool_events_on_purpose():
    src = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
    tool_call_sse = src[src.index("# Assistant tool_calls"):src.index("# role=tool")]
    tool_result_sse = src[src.index("# role=tool"):src.index("_agent_for_route")]
    assert "get_tool_schema" in tool_call_sse
    assert "continue" in tool_call_sse
    assert "mcp__pawflow__get_tool_schema" in tool_result_sse
    assert '_raw_tool_name = ""' in tool_result_sse



def test_gemini_flushes_live_text_before_final_completion():
    """Long Gemini ACP text chunks must reach the chat before final stop."""
    from core.llm_providers.gemini import LLMGeminiMixin

    assert not LLMGeminiMixin._gemini_acp_should_flush_live_text("", 10.0, 20.0)
    assert LLMGeminiMixin._gemini_acp_should_flush_live_text("x" * 240, 10.0, 10.1)
    assert LLMGeminiMixin._gemini_acp_should_flush_live_text("short", 10.0, 11.1)
    assert not LLMGeminiMixin._gemini_acp_should_flush_live_text("short", 10.0, 10.5)

    src = inspect.getsource(LLMGeminiMixin._stream_gemini)
    message_idx = src.index('if kind == "agent_message_chunk":')
    helper_idx = src.index("_gemini_acp_should_flush_live_text", message_idx)
    flush_idx = src.index("_flush_text()", helper_idx)
    continue_idx = src.index("continue", message_idx)
    assert helper_idx < flush_idx < continue_idx


def test_gemini_flushes_pending_text_before_live_tool_callback():
    """Gemini ACP tool calls must not overtake already-emitted text."""
    from core.llm_providers.gemini import LLMGeminiMixin

    src = inspect.getsource(LLMGeminiMixin._stream_gemini)
    helper_idx = src.index("def _emit_started_tool(")
    callback_idx = src.index('block_callback("tool_use", {', helper_idx)
    assert helper_idx < callback_idx

    tool_call_idx = src.index('if kind == "tool_call":')
    flush_idx = src.index("if turn_text_parts:", tool_call_idx)
    started_idx = src.index("_emit_started_tool", tool_call_idx)
    assert flush_idx < started_idx

    update_idx = src.index('if kind == "tool_call_update":')
    update_flush_idx = src.index("if turn_text_parts:", update_idx)
    update_started_idx = src.index("_emit_started_tool", update_idx)
    assert update_flush_idx < update_started_idx



def test_gemini_reuse_depends_only_on_persisted_session_pointer():
    """Gemini mirrors CC/Codex: existing session id means resume; invalidation clears extras."""
    from core.llm_providers.gemini import LLMGeminiMixin

    src = inspect.getsource(LLMGeminiMixin._stream_gemini)
    assert 'session_key = f"gemini_acp_session:{agent_name or \'default\'}"' in src
    assert 'session_version_key = f"gemini_acp_session_version:{agent_name or \'default\'}"' in src
    assert 'prompt_mode = "resume" if session_id else "cold"' in src
    assert "clearing legacy stored session" in src
    assert "fresh PawFlow context" not in src
    assert "len(messages or []) <= 2" not in src



def test_gemini_completed_tool_call_emits_result_without_update_event():
    """Gemini ACP sometimes sends a terminal tool_call with no follow-up update."""
    from core.llm_providers.gemini import LLMGeminiMixin

    src = inspect.getsource(LLMGeminiMixin._stream_gemini)
    tool_call = src[src.index('if kind == "tool_call":'):src.index('if kind == "tool_call_update":')]
    update_idx = src.index('if kind == "tool_call_update":')
    tool_update = src[update_idx:src.index("\n\n\n            _flush_text()", update_idx)]
    assert 'status = update.get("status") or ""' in tool_call
    assert 'if status in _terminal_tool_statuses:' in tool_call
    assert '_emit_finished_tool(update, tc_id, raw_name, raw_input)' in tool_call
    assert '_emit_finished_tool(update, tc_id, raw_name, raw_input)' in tool_update
    assert 'enqueue_live_mapping=False' in src



def test_provider_mixins_have_no_method_collisions():
    """Each provider's per-CLI helper methods must NOT collide.

    `LLMClient` inherits from the active provider mixins, while the legacy
    Codex CLI mixin still exists as helper/reference code for app-server.
    Two active mixins defining a method with the same name silently let
    Python's MRO pick one — and the wrong provider's implementation runs.

    Convention enforced here: every method on a provider mixin (or its
    session mixin) MUST be prefixed with the CLI name (`_cc_`,
    `_codex_app_`, `_gemini_`) UNLESS it is one of the OK_TO_COLLIDE
    exceptions below.
    """
    from core.llm_providers.claude_code import LLMClaudeCodeMixin as CC
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin as CAPP
    from core.llm_providers.gemini import LLMGeminiMixin as GM
    from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin as CCS
    from core.llm_providers.codex_session import CodexSessionMixin as CXS
    from core.llm_providers.gemini_session import GeminiSessionMixin as GMS

    def _own(c):
        return set(c.__dict__.keys()) - {
            "__module__", "__qualname__", "__doc__",
            "__dict__", "__weakref__",
        }

    cc_all = _own(CC) | _own(CCS)
    capp_all = _own(CAPP) | _own(CXS)
    gm_all = _own(GM) | _own(GMS)

    # Names that may legitimately appear identically on multiple mixins:
    #   - Constants / regex whose value is identical across CLIs.
    #   - `_get_tool_relay_info` is a classmethod returning the SHARED
    #     PawFlow tool relay service — codex/gemini delegate to CC's.
    #   - `_pool_counter` / `_pool_lock` are accessed via
    #     `<Mixin>._pool_counter` (class-name prefix), so per-class
    #     state is preserved despite the name collision.
    OK_TO_COLLIDE = {
        "_DISALLOWED_BUILTIN_TOOLS",
        "_LEGACY_IMAGE_RE",
        "_OAUTH_REFRESH_MIN_TTL_SEC",
        "_get_tool_relay_info",
        "_pool_counter",
        "_pool_lock",
    }

    failures = []
    for label, a, b in (
        ("CC ∩ codex-app-server", cc_all, capp_all),
        ("CC ∩ gemini", cc_all, gm_all),
        ("codex-app-server ∩ gemini", capp_all, gm_all),
    ):
        bad = (a & b) - OK_TO_COLLIDE
        if bad:
            failures.append(
                f"{label}: {sorted(bad)} — these names collide on "
                f"LLMClient and Python's MRO will silently pick one. "
                f"Rename with the CLI prefix (`_cc_*` / `_codex_app_*` / "
                f"`_gemini_*`) or add to "
                f"OK_TO_COLLIDE if intentional.")
    if failures:
        raise AssertionError("\n\n".join(failures))
