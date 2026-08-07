"""Every provider that can measure its own prompt size must feed the gauge.

The gauge has two possible numerators. The reconstructed one counts the
messages PawFlow holds, which for a CLI session is a subset of the window at
best: it sees neither the provider's system prompt and tool schemas nor the
session history the provider resumed, and for an externalized context it sees
only what survives the bootstrap-read boundary. The measured one is the number
the provider itself counted.

When the measurement exists and is not recorded, ``compute_context_usage``
silently falls back to the reconstruction -- which is how a session holding a
full window came to report 0%. These tests pin the recording for every
provider that has a measurement available, so a future refactor cannot drop one
back to the estimate without going red.
"""

import inspect
import json

import pytest

from core.llm_client import LLMClient
from core.llm_providers.cli_shared import LLMCliSharedMixin


def _client(provider: str) -> LLMClient:
    return LLMClient(provider)


def test_wire_usage_sums_cached_and_uncached_prompt_tokens():
    """Cache reads and cache creation occupy the window like plain input.

    For a Claude Code session they are most of the prompt: a gauge built on
    ``input_tokens`` alone would report a small fraction of the real window.
    """
    client = _client("claude-code-interactive")
    client.record_observed_wire_usage(
        {"input_tokens": 1_200,
         "cache_read_input_tokens": 180_000,
         "cache_creation_input_tokens": 47_222,
         "output_tokens": 900},
        "conv-1", "claude")
    assert client._cli_observed_context_tokens_by_stream[
        ("conv-1", "claude")] == 228_422


def test_wire_usage_without_cache_fields_records_the_prompt_it_has():
    """The Antigravity observer normalizes promptTokenCount to input_tokens."""
    client = _client("antigravity-interactive")
    client.record_observed_wire_usage(
        {"input_tokens": 64_000, "output_tokens": 700}, "conv-2", "agi")
    assert client._cli_observed_context_tokens_by_stream[
        ("conv-2", "agi")] == 64_000


@pytest.mark.parametrize("usage", [None, {}, "not-a-dict", {"output_tokens": 5}])
def test_absent_measurement_records_nothing(usage):
    """No measurement must leave the map empty, not write a 0.

    A recorded 0 is indistinguishable from 'measured an empty window' for
    every consumer, and would pin the gauge at 0% instead of letting the
    reconstruction answer.
    """
    client = _client("claude-code-interactive")
    client.record_observed_wire_usage(usage, "conv-3", "claude")
    assert client._cli_observed_context_tokens_by_stream == {}


def test_observed_window_is_recorded_only_when_positive():
    client = _client("codex-app-server")
    client.record_observed_cli_window("conv-4", "codex", 0)
    assert client._cli_observed_context_window_by_stream == {}
    client.record_observed_cli_window("conv-4", "codex", 272_000)
    assert client._cli_observed_context_window_by_stream[
        ("conv-4", "codex")] == 272_000


def test_one_recorder_shared_by_every_provider():
    """Codex borrows the shared implementation rather than copying it.

    Two copies of the recorder is how one of them drifts: the Codex mixin used
    to define its own, and the gauge then depended on which mixin won the MRO.
    """
    from core.llm_providers.codex_interactive import LLMCodexInteractiveMixin
    assert (LLMCodexInteractiveMixin.record_observed_cli_context
            is LLMCliSharedMixin.record_observed_cli_context)
    assert (LLMClient.record_observed_cli_context
            is LLMCliSharedMixin.record_observed_cli_context)


@pytest.mark.parametrize("method_name", [
    "_stream_claude_code_interactive",
    "interrupt_claude_code_interactive",
    "_stream_antigravity_interactive",
    "interrupt_antigravity_interactive",
])
def test_interactive_turn_and_interrupt_paths_record(method_name):
    """An interrupt runs against the same window as an ordinary turn."""
    src = inspect.getsource(getattr(LLMClient, method_name))
    assert ("record_observed_wire_usage" in src
            or "_cci_record_observed_context" in src), method_name


def test_claude_code_batch_provider_records_its_result_usage():
    from core.llm_providers import _cc_stream_result
    src = inspect.getsource(_cc_stream_result)
    assert "record_observed_cli_context" in src


def test_codex_app_server_measures_from_its_native_rollout():
    """app-server writes the same rollout the TUI does -- read it, don't guess."""
    from core.llm_providers import _codex_app_stream
    src = inspect.getsource(_codex_app_stream)
    assert "codex_rollout_context_usage" in src
    assert "record_observed_cli_context" in src
    assert "record_observed_cli_window" in src
    # Scoped to the thread that just answered: app-server can resume several
    # threads under one workdir, where newest-mtime is not the right file.
    assert "thread_id=thread_id" in src


def test_rollout_reader_scopes_to_the_requested_thread(tmp_path):
    from core.llm_providers.codex_interactive import (
        codex_rollout_context_usage)

    sessions = tmp_path / ".codex" / "sessions"
    sessions.mkdir(parents=True)

    def _write(name: str, used: int, window: int) -> None:
        (sessions / name).write_text(json.dumps({"payload": {
            "type": "token_count",
            "info": {"last_token_usage": {"input_tokens": used},
                     "model_context_window": window}}}) + "\n")

    _write("rollout-2026-thread-aaa.jsonl", 111_000, 272_000)
    _write("rollout-2026-thread-bbb.jsonl", 222_000, 272_000)

    assert codex_rollout_context_usage(
        str(tmp_path), thread_id="thread-aaa")[0] == 111_000
    assert codex_rollout_context_usage(
        str(tmp_path), thread_id="thread-bbb")[0] == 222_000
    # An unknown thread must report nothing rather than someone else's window.
    assert codex_rollout_context_usage(
        str(tmp_path), thread_id="thread-zzz") == (0, 0)


def test_gemini_acp_prompt_tokens_prefer_the_native_counter():
    client = _client("gemini")
    meta = {"quota": {"token_count": {
        "promptTokenCount": 131_000,
        "candidatesTokenCount": 400,
        "totalTokenCount": 131_400}}}
    assert client._gemini_acp_prompt_tokens(meta) == 131_000


def test_gemini_acp_prompt_tokens_fall_back_to_total_minus_answer():
    client = _client("gemini")
    meta = {"quota": {"token_count": {
        "candidatesTokenCount": 400, "totalTokenCount": 131_400}}}
    assert client._gemini_acp_prompt_tokens(meta) == 131_000


def test_gemini_acp_prompt_tokens_absent_reports_nothing():
    client = _client("gemini")
    assert client._gemini_acp_prompt_tokens({}) == 0
    assert client._gemini_acp_prompt_tokens(
        {"quota": {"token_count": {"candidatesTokenCount": 400}}}) == 0


def test_gemini_stream_records_the_native_prompt_size():
    from core.llm_providers import _gemini_stream
    src = inspect.getsource(_gemini_stream)
    assert "_gemini_acp_prompt_tokens" in src
    assert "record_observed_cli_context" in src
