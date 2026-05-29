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
from core.llm_client import LLMClient, is_mcp_tool_call_name, unwrap_mcp_tool


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
    assert "is_process_alive" in stream_src
    assert "is_container_alive" in stream_src
    assert "process dead but container alive" in stream_src
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
    assert "_had_preempts_this_turn = False" in stream_src
    assert "_preempt_prompt_active = True" in stream_src
    assert "self._had_preempts_this_turn = True" in stream_src
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
    assert "self._gemini_acp_resume_text(messages)" in stream_src


def test_cli_providers_do_not_force_default_model_flags():
    from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
    from core.llm_providers.codex_session import CodexSessionMixin
    from core.llm_providers.gemini import LLMGeminiMixin

    for provider in ("claude-code", "codex-app-server", "gemini"):
        assert LLMClient(provider=provider, config={}).default_model == ""

    assert '"--model", model or "sonnet"' not in inspect.getsource(
        ClaudeCodeSessionMixin._build_claude_cmd)
    cc_cmd_src = inspect.getsource(ClaudeCodeSessionMixin._build_claude_cmd)
    assert '"--thinking-display", "summarized"' in cc_cmd_src
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


def test_gemini_acp_capacity_error_matches_no_capacity_available():
    from core.llm_providers.gemini import LLMGeminiMixin

    text = LLMGeminiMixin._gemini_acp_capacity_error({
        "code": 500,
        "message": "No capacity available for model gemini-2.5-flash on the server",
    })

    assert text == "Gemini model capacity exhausted"


def test_gemini_acp_persists_session_only_after_successful_prompt():
    from core.llm_providers.gemini import LLMGeminiMixin

    stream_src = inspect.getsource(LLMGeminiMixin._stream_gemini)
    open_block = stream_src[
        stream_src.index("opening new session cwd"):
        stream_src.index("elif not session_id:", stream_src.index("opening new session cwd"))
    ]
    success_block = stream_src[
        stream_src.index("tokens_out = self._gemini_acp_output_tokens"):
        stream_src.index("return LLMResponse(", stream_src.index("tokens_out = self._gemini_acp_output_tokens"))
    ]

    assert "opened_session_this_call = True" in open_block
    assert "store.set_extra(conv_id, session_key, session_id)" not in open_block
    assert "store.set_extra(conv_id, session_key, session_id)" in success_block
    assert "failed fresh session" in stream_src


def test_mcp_bridge_aliases_gemini_builtin_list_directory_to_list_dir():
    src = Path("tools/mcp_bridge.py").read_text(encoding="utf-8")
    alias_block = src[src.index("_TOOL_ALIASES = {"):
                      src.index("# Case-insensitive alias lookup")]
    assert '"list_directory": "list_dir"' in alias_block
    assert '"read_file": "read"' in alias_block
    assert '"search": "grep"' not in alias_block


def test_mcp_bridge_dispatches_tool_calls_concurrently():
    src = Path("tools/mcp_bridge.py").read_text(encoding="utf-8")
    assert "_respond_lock = threading.Lock()" in src
    assert "_active_call_threads = set()" in src
    assert "self._pending = {}" in src
    assert "name=\"mcp-bridge-ws-reader\"" in src
    assert "target=_run_tool_call" in src
    assert "_wait_for_active_tool_calls()" in src
    assert "daemon=False" in src
    assert "_do_request_serial" not in src


def test_mcp_bridge_retries_initial_tool_relay_connection():
    src = Path("tools/mcp_bridge.py").read_text(encoding="utf-8")
    assert "def _ensure_relay_client():" in src
    assert "retrying a failed initial connect" in src
    assert "for attempt in range(5):" in src
    assert "Connecting to tool relay on demand (attempt" in src
    assert "time.sleep(0.5)" in src
    assert "Tool relay unavailable after retries" in src
    assert "relay_client = _ensure_relay_client()" in src
    assert "if not relay_client:" in src
    assert "result = relay_client.request(\"get_tool_schema\"" in src
    assert "result = relay_client.request(\"execute_tool\"" in src


def test_tool_relay_info_refreshes_registered_ws_route():
    src = Path("core/llm_providers/claude_code_session.py").read_text(encoding="utf-8")
    block = src[src.index("def _get_tool_relay_info"):
                src.index("# Proactively refresh OAuth tokens")]
    assert "if cls._tool_relay_cache:" not in block
    assert "svc.connect()" in block
    assert "not svc.is_connected()" in block
    assert "skipping cached route" in block
    assert "failed to register route /ws/tools" in block


def test_mcp_bridge_and_tool_relay_emit_timing_breakdown():
    bridge_src = Path("tools/mcp_bridge.py").read_text(encoding="utf-8")
    relay_src = Path("services/tool_relay_service.py").read_text(encoding="utf-8")
    codex_src = Path("core/llm_providers/codex_app_server.py").read_text(encoding="utf-8")

    assert "TIMING tools/call" in bridge_src
    assert "bridge_ms=" in bridge_src
    assert "return_wait_ms=" in bridge_src
    assert "timing do_execute" in relay_src
    assert "timing get_registry" in relay_src
    assert "mcp_ms=" in relay_src
    assert "fs_find_ms=" in relay_src
    assert "registry_ms=" in relay_src
    assert "exec_ms=" in relay_src
    assert "timing ws_send" in relay_src
    assert "timing mcpToolCall started" in codex_src
    assert "timing mcpToolCall completed" in codex_src


def test_mcp_use_tool_preserves_registered_search_tool_name():
    name, args = unwrap_mcp_tool(
        "mcp__pawflow__use_tool",
        {"tool_name": "search", "arguments": {"pattern": "placeholder"}},
    )

    assert name == "search"
    assert args == {"pattern": "placeholder"}


def test_mcp_use_tool_unwraps_dotted_provider_wrapper():
    name, args = unwrap_mcp_tool(
        "mcp__pawflow__.use_tool",
        {"tool_name": "read", "arguments": {"path": "/workspace/README.md"}},
    )

    assert name == "read"
    assert args == {"path": "/workspace/README.md"}


def test_mcp_use_tool_unwraps_slash_provider_wrapper():
    name, args = unwrap_mcp_tool(
        "pawflow/use_tool",
        {"tool_name": "bash", "arguments": {"command": "git status --short"}},
    )

    assert name == "bash"
    assert args == {"command": "git status --short"}


def test_mcp_call_name_detection_covers_antigravity_wrappers():
    assert is_mcp_tool_call_name("call_mcp_tool")
    assert is_mcp_tool_call_name("pawflow/read")
    assert is_mcp_tool_call_name("pawflow/use_tool")
    assert is_mcp_tool_call_name("mcp__pawflow__use_tool")
    assert not is_mcp_tool_call_name("read")


def test_mcp_use_tool_unwraps_parameters_payload():
    name, args = unwrap_mcp_tool(
        "mcp__pawflow__.use_tool",
        {"parameters": {"tool_name": "search", "arguments": {"pattern": "x"}}},
    )

    assert name == "search"
    assert args == {"pattern": "x"}


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
    assert 'block_callback=_cli_block_callback if _client_provider in ("claude-code-interactive", "antigravity-interactive", "codex-app-server", "gemini") else None' in src
    assert 'turn_callback=_claude_code_turn_callback if _client_provider in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "gemini") else None' in src



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

    wrapper = '<tool_output tool="mcp_pawflow_use_tool">\nhello\n</tool_output>'
    assert LLMGeminiMixin._gemini_acp_display_tool_name("use_tool", wrapper) == "use_tool"

    error = "MCP tool 'use_tool' reported tool error for function call: {'tool_name': 'list_dir'}"
    assert LLMGeminiMixin._gemini_acp_display_tool_name("use_tool", error) == "list_dir"


def test_gemini_acp_displays_inner_pawflow_tool_args():
    from core.llm_providers.gemini import LLMGeminiMixin

    name, args = LLMGeminiMixin._gemini_acp_display_tool_call(
        "use_tool", {"tool_name": "list_dir", "arguments": {"path": "/workspace"}})
    assert name == "list_dir"
    assert args == {"path": "/workspace"}


def test_gemini_acp_strips_pawflow_wrapper_from_tool_result_text():
    from core.llm_providers.gemini import LLMGeminiMixin

    wrapped = '<tool_output tool="mcp_pawflow_use_tool">\ncommit 551e9182\n</tool_output>'
    assert LLMGeminiMixin._gemini_acp_clean_tool_result_text(wrapped) == "commit 551e9182"

    real_tool = '<tool_output tool="bash">\ncommit 551e9182\n</tool_output>'
    assert LLMGeminiMixin._gemini_acp_clean_tool_result_text(real_tool) == real_tool


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



def test_gemini_does_not_persist_partial_text_chunks():
    """Gemini must match Codex/CC: token deltas stream live, but persisted
    assistant messages flush only at tool/final turn boundaries.
    """
    from core.llm_providers.gemini import LLMGeminiMixin

    assert not hasattr(LLMGeminiMixin, "_gemini_acp_should_flush_live_text")
    src = inspect.getsource(LLMGeminiMixin._stream_gemini)
    message_idx = src.index('if kind == "agent_message_chunk":')
    continue_idx = src.index("continue", message_idx)
    message_block = src[message_idx:continue_idx]
    assert "callback(delta)" in message_block
    assert "_flush_text()" not in message_block


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


def test_codex_app_preempt_attachment_items_resolve_filestore_image(tmp_path, monkeypatch):
    from core.file_store import FileStore
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin

    store = FileStore(base_dir=str(tmp_path / "files"))
    monkeypatch.setattr(FileStore, "_instance", store)
    file_id = store.store(
        "image.png", b"image-bytes", "image/png",
        conversation_id="conv-live", user_id="user-1")

    workdir = tmp_path / "work"
    items = LLMCodexAppServerMixin()._codex_app_attachment_items(
        [{"filename": "image.png", "mime_type": "image/png", "file_id": file_id}],
        user_id="user-1",
        conversation_id="conv-live",
        workdir=str(workdir),
        container_dir="/workspace",
    )

    assert items and items[0]["type"] == "localImage"
    assert items[0]["path"].startswith("/workspace/.pawflow_vision/")
    saved = workdir / ".pawflow_vision" / items[0]["path"].rsplit("/", 1)[-1]
    assert saved.read_bytes() == b"image-bytes"


def test_codex_app_mcp_image_result_never_serializes_base64():
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin

    item = {
        "result": [
            {"type": "text", "text": "Image: fs://filestore/fid/screen.png"},
            {"type": "image", "mimeType": "image/png", "data": "A" * 100_000},
        ]
    }

    text = LLMCodexAppServerMixin._codex_app_result_text(item)

    assert text == "Image: fs://filestore/fid/screen.png"
    assert "AAAA" not in text
    assert "data" not in text


def test_codex_app_mcp_image_only_result_uses_small_placeholder():
    from core.llm_providers.codex_app_server import LLMCodexAppServerMixin

    item = {"result": [{"type": "image", "mimeType": "image/png", "data": "A" * 100_000}]}

    text = LLMCodexAppServerMixin._codex_app_result_text(item)

    assert text == "[image sent to vision: 1]"
    assert len(text) < 64



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
    #   - Python runtime metadata dunders can be injected on every class.
    OK_TO_COLLIDE = {
        "_DISALLOWED_BUILTIN_TOOLS",
        "_LEGACY_IMAGE_RE",
        "_OAUTH_REFRESH_MIN_TTL_SEC",
        "_get_tool_relay_info",
        "_pool_counter",
        "_pool_lock",
        "__firstlineno__",
        "__static_attributes__",
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
