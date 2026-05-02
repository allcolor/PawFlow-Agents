"""Static coverage for the isolated Codex app-server provider."""

import inspect
import os
import time

from core.llm_client import LLMClient
from core.llm_providers.codex_app_server import LLMCodexAppServerMixin
from services.llm_connection import LLMConnectionService


def test_only_codex_app_server_is_registered():
    assert "codex" not in LLMClient.PROVIDERS
    assert "codex" not in LLMClient.DEFAULT_MODELS
    assert "codex-app-server" in LLMClient.PROVIDERS
    assert LLMClient.DEFAULT_MODELS["codex-app-server"] == "gpt-5.4"
    assert not hasattr(LLMClient, "_stream_codex")
    assert hasattr(LLMClient, "_stream_codex_app_server")


def test_codex_app_server_dispatch_contracts_are_wired():
    src = inspect.getsource(LLMClient.send_user_message)
    assert 'self.provider == "codex-app-server"' in src
    assert "_codex_app_send_user_message" in src

    complete_src = inspect.getsource(LLMClient.complete)
    stream_src = inspect.getsource(LLMClient.complete_stream)
    assert "_stream_codex_app_server" in complete_src
    assert "_stream_codex_app_server" in stream_src
    assert "_stream_codex(" not in complete_src
    assert "_stream_codex(" not in stream_src
    assert '"codex-app-server"' in complete_src
    assert '"codex-app-server"' in stream_src


def test_codex_app_server_uses_app_server_protocol_and_local_images():
    src = inspect.getsource(LLMCodexAppServerMixin)
    assert '["app-server"]' in src
    assert '"thread/start"' in src
    assert '"thread/resume"' in src
    assert '"turn/start"' in src
    assert '"turn/steer"' in src
    assert '"effort"' in src
    assert '"summary"' in src
    assert '"localImage"' in src
    assert "_codex_app_image_item" in src


def test_codex_app_server_stdio_is_utf8_and_ascii_safe_json():
    src = inspect.getsource(LLMCodexAppServerMixin)
    assert 'encoding="utf-8"' in src
    assert 'errors="replace"' in src
    assert "json.dumps(msg, ensure_ascii=True)" in src


def test_codex_app_server_reasoning_is_wired():
    complete_src = inspect.getsource(LLMClient.complete)
    stream_src = inspect.getsource(LLMClient.complete_stream)
    provider_src = inspect.getsource(LLMCodexAppServerMixin)
    assert "thinking_budget=thinking_budget" in complete_src
    assert "thinking_budget=thinking_budget" in stream_src
    assert "_codex_app_effort" in provider_src
    assert "count_messages_tokens" in provider_src
    assert "prompt_tokens" in provider_src
    assert "prompt_mode = \"resume\"" in provider_src
    assert "mode=%s" in provider_src
    assert "full_context_text" not in provider_src
    assert "tokens_in=max(0, int(prompt_tokens or 0))" in provider_src
    assert "item/reasoning/summaryTextDelta" in provider_src
    assert "item/reasoning/textDelta" in provider_src
    assert 'item.get("type") == "reasoning"' in provider_src
    assert "_append_final_reasoning" in provider_src
    assert "text in existing or existing in text" in provider_src



def test_codex_app_server_effort_mapping():
    assert LLMCodexAppServerMixin._codex_app_effort(0, "") == "low"
    assert LLMCodexAppServerMixin._codex_app_effort(5000, "") == "medium"
    assert LLMCodexAppServerMixin._codex_app_effort(10000, "") == "high"
    assert LLMCodexAppServerMixin._codex_app_effort(20000, "") == "xhigh"
    assert LLMCodexAppServerMixin._codex_app_effort(0, "max") == "xhigh"
    assert LLMCodexAppServerMixin._codex_app_reasoning_summary("low") == "none"
    assert LLMCodexAppServerMixin._codex_app_reasoning_summary("medium") == "auto"




def test_codex_mcp_config_uses_absolute_python_for_app_server_spawn():
    from core.llm_providers.codex_session import CodexSessionMixin

    src = inspect.getsource(CodexSessionMixin._codex_setup_mcp_config)
    assert 'python_bin = "/usr/bin/python3"' in src


def test_codex_mcp_config_does_not_set_tool_timeout():
    from core.llm_providers.codex_session import CodexSessionMixin

    src = inspect.getsource(CodexSessionMixin._codex_setup_mcp_config)
    assert "tool_timeout_sec" not in src
    assert "startup_timeout_sec = 20" in src


def test_codex_preamble_names_disabled_native_tools():
    from core.llm_providers.codex_session import CodexSessionMixin

    preamble = CodexSessionMixin._CODEX_PAWFLOW_PREAMBLE
    assert "ApplyPatch" in preamble
    assert "exec_command" in preamble
    assert "PawFlow MCP tools" in preamble


def test_codex_app_server_container_dir_matches_pool_namespace():
    from core.llm_providers.codex_session import _get_sessions_base

    workdir = os.path.join(_get_sessions_base(), "alice", "conv123", "assistant")
    assert LLMCodexAppServerMixin._codex_app_container_dir(workdir) == "/cc_sessions/conv123/assistant"


def test_codex_app_server_recovers_from_stale_thread_rollout():
    src = inspect.getsource(LLMCodexAppServerMixin._stream_codex_app_server)
    assert "_codex_app_missing_rollout_error" in src
    assert "store.set_extra(conv_id, thread_key, \"\")" in src
    assert "initial_text = self._codex_app_full_initial_text(messages)" in src
    assert "stale thread id" in src


def test_codex_app_server_registers_live_app_server_session():
    src = inspect.getsource(LLMCodexAppServerMixin._stream_codex_app_server)
    assert "CodexLiveRegistry" in src
    assert "live_reg.register" in src
    assert "[codex-app-live] keep-alive" in src
    assert "[codex-app-live] active" in src
    assert "Surface LIVE while the app-server turn is running" in src
    assert "live_reg.touch(live_key)" in src
    assert "is_reuse" in src
    assert "not turn_failed" in src
    assert "proc_alive" in src
    assert "is_process_alive" in src
    assert "is_container_alive" in src
    assert "process dead but container alive" in src
    assert "get_compatible" in src
    assert "live_session.turn_lock.acquire()" in src
    assert "live_session.turn_lock.release()" in src
    assert "live_reg.ensure_sweeper" in src
    assert "idle_ttl_seconds=int(_idle_ttl) if _idle_ttl else None" in src
    assert "active_turn=True" in src
    assert "active_turn=False" in src

    assert LLMConnectionService({}).get_service_actions() == []

    rules = LLMConnectionService({}).get_parameter_rules()
    assert any(
        rule.get("when", {}).get("provider") == ["codex-app-server"]
        and rule.get("set", {}).get("api_key", {}).get("visible") is True
        and rule.get("set", {}).get("effort", {}).get("visible") is True
        for rule in rules
    )


def test_codex_app_server_hands_context_compaction_to_pawflow():
    src = inspect.getsource(LLMCodexAppServerMixin._stream_codex_app_server)
    assert 'item.get("type") == "contextCompaction"' in src
    assert "CCCompactDetected" in src
    assert "handing compaction to PawFlow" in src


def test_codex_app_server_uses_completed_message_as_text_source_of_truth():
    src = inspect.getsource(LLMCodexAppServerMixin._stream_codex_app_server)
    assert 'item.get("type") in ("message", "agentMessage")' in src
    assert "assistant delta/final mismatch" in src
    assert "turn_text_parts = [final_text]" in src
    assert "turn_text_is_final = True" in src
    assert "dropping non-final assistant delta text" in src
    assert "final_text_parts.append(final_text)" in src
    assert '"".join(final_text_parts).strip() or "".join(text_parts).strip()' in src


def test_codex_live_sweeper_does_not_evict_active_turn():
    from core.codex_live_registry import CodexLiveRegistry

    reg = CodexLiveRegistry()
    key = ("user", "conv", "assistant", "svc", 0)
    session = reg.register(
        key, "container", "/tmp/work", service_id="svc",
        session_id="thread", active_turn=True)
    session.last_used = time.monotonic() - 9999

    assert reg.sweep_idle(ttl=1) == 0
    assert reg.get(key) is session

    reg.ensure_sweeper(idle_ttl_seconds=1800)
    assert reg._idle_ttl == 1800
    reg._sweeper_stop.set()
    with reg._lock:
        reg._containers.clear()


def test_codex_live_lookup_falls_back_when_pool_idx_extra_is_missing():
    from core.codex_live_registry import CodexLiveRegistry

    reg = CodexLiveRegistry()
    key = ("user", "conv", "assistant", "svc", 0)
    session = reg.register(
        key, "container", "/tmp/work", service_id="svc",
        session_id="thread")
    session.last_used = time.monotonic()

    assert reg.get(("user", "conv", "assistant", "svc", -1)) is None
    compatible = reg.get_compatible("user", "conv", "assistant", "svc")
    assert compatible == (key, session)


def test_codex_live_session_tracks_process_and_container_separately(monkeypatch):
    from core.codex_live_registry import CodexLiveSession
    from core.codex_pool import CodexPool

    class DeadProc:
        def poll(self):
            return 0

    class LiveProc:
        def poll(self):
            return None

    class Pool:
        def _is_container_alive(self, name):
            return name == "container"

    monkeypatch.setattr(CodexPool, "instance", classmethod(lambda cls: Pool()))

    session = CodexLiveSession(
        container_name="container", workdir="/tmp", service_id="svc",
        proc=DeadProc())
    assert not session.is_process_alive()
    assert session.is_container_alive()
    assert not session.is_alive()

    session.proc = LiveProc()
    assert session.is_process_alive()
    assert session.is_container_alive()
    assert session.is_alive()
