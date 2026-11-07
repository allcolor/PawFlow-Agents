"""There are exactly two cases for a CLI turn, and no third one.

    CASE 1  no process running -> we launch  -> COLD START, full context
    CASE 2  a process IS running             -> DELTA
    crash / whatever -> back to case 1, otherwise case 2

The context phase decides which case a turn is built for, but the PROVIDER is
what launches or reuses, so only it observes the truth. Both directions of
disagreement must therefore be caught:

    built as delta, provider must launch  -> ColdStartRequired   (case 1)
    built as cold,  provider found alive  -> DeltaContextRequired (case 2)

Only the first half existed. Without the second, a turn built as a cold start
and executed as a reuse ran in the bastard state that is neither case: the
whole transcript loaded and compacted for a launch that never happened, the
gauge zeroed against a session that never restarted, and the persisted session
pointers cleared and rewritten on every message.
"""

import unittest
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core._llm_types import ColdStartRequired, DeltaContextRequired
from core.llm_providers.cli_shared import LLMCliSharedMixin


class _ReachedAfterLiveGuard(BaseException):
    pass


@pytest.mark.parametrize("provider", ["codex-app-server", "gemini"])
@pytest.mark.parametrize("is_delta", [False, True])
@pytest.mark.parametrize("recover", [False, True])
def test_real_provider_live_lookup_preserves_context_signal_and_lock(
        monkeypatch, tmp_path, provider, is_delta, recover):
    from core.llm_client import LLMClient, LLMMessage

    client = LLMClient(provider, config={"api_key": "test"})
    client._pawflow_context_is_delta = is_delta
    content = [{"type": "text", "text": "pending input"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,UE5H"}}]
    messages = [LLMMessage(role="user", content=deepcopy(content), conversation_id="conv")]
    lock = threading.Lock()
    live = SimpleNamespace(
        proc=MagicMock(), container_name="healthy-container", session_id="live-session",
        mcp_internal_token="", event_q=None, reuse_count=1,
        turn_lock=MagicMock(wraps=lock),
        is_container_alive=lambda: True, is_process_alive=lambda: True)
    registry = MagicMock()
    registry.get.return_value = live
    store = MagicMock()
    store.get_extra.return_value = ""
    monkeypatch.setattr("core.conversation_store.ConversationStore.instance", lambda: store)
    reached = MagicMock(side_effect=_ReachedAfterLiveGuard)
    if provider == "codex-app-server":
        import core.llm_providers._codex_app_stream as module
        monkeypatch.setattr("core.codex_live_registry.CodexLiveRegistry.instance", lambda: registry)
        monkeypatch.setattr(client, "_codex_get_session_workdir", lambda *args: str(tmp_path))
        monkeypatch.setattr(client, "_codex_app_container_dir", lambda path: path)
        monkeypatch.setattr(client, "_codex_app_estimate_prompt_tokens", lambda text: 0)
        method = client._stream_codex_app_server
        extractor = "_codex_app_extract_images"
    else:
        import core.llm_providers._gemini_stream as module
        monkeypatch.setattr("core.gemini_live_registry.GeminiLiveRegistry.instance", lambda: registry)
        monkeypatch.setattr(client, "_gemini_get_session_workdir", lambda *args: str(tmp_path))
        monkeypatch.setattr(client, "_gemini_acp_container_dir", lambda path: path)
        monkeypatch.setattr(client, "_gemini_acp_full_initial_text", lambda *args: "cold context")
        method = client._stream_gemini
        extractor = "_gemini_acp_extract_images"
    extracted = []
    extract_images = getattr(client, extractor)

    def capture_images(*args, **kwargs):
        images = extract_images(*args, **kwargs)
        extracted.append(images)
        return images

    monkeypatch.setattr(client, extractor, capture_images)
    # This point follows live lookup and precedes submission and its cleanup.
    monkeypatch.setattr(module, "uuid", SimpleNamespace(uuid4=reached))
    try:
        with pytest.raises(_ReachedAfterLiveGuard if is_delta or recover else DeltaContextRequired):
            kwargs = {"messages": messages, "model": "model", "call_user_id": "u",
                      "call_conversation_id": "conv", "call_agent_name": "worker"}
            if recover:
                from core.agent_executor import SubAgentExecutor
                monkeypatch.setattr(client, "complete_stream", method)
                SubAgentExecutor._call_subagent_llm(client, stream=True, **kwargs)
            else:
                method(**kwargs)
        assert client._pawflow_context_is_delta is True
        assert lock.locked() is (is_delta or recover)
        attempts = 2 if recover and not is_delta else 1
        assert live.turn_lock.acquire.call_count == attempts
        assert live.turn_lock.release.call_count == (0 if is_delta else 1)
        assert reached.call_count == (1 if is_delta or recover else 0)
        if recover:
            assert [len(images) for images in extracted] == [1] * attempts
            assert messages[0].content == content
        registry.evict.assert_not_called()
        live.proc.terminate.assert_not_called()
        live.proc.kill.assert_not_called()
    finally:
        if lock.locked():
            lock.release()


class _Provider(LLMCliSharedMixin):
    def __init__(self, is_delta):
        self._pawflow_context_is_delta = is_delta


class TheTwoGuardsAreMirrors(unittest.TestCase):

    def test_a_delta_turn_that_must_launch_is_sent_back_as_cold(self):
        provider = _Provider(is_delta=True)
        with self.assertRaises(ColdStartRequired):
            provider._cli_require_cold_context("p")

    def test_a_cold_turn_that_found_a_live_process_is_sent_back_as_delta(self):
        provider = _Provider(is_delta=False)
        with self.assertRaises(DeltaContextRequired):
            provider._cli_require_delta_context("p")

    def test_each_guard_is_silent_in_its_own_case(self):
        """The ordinary paths: case 1 launching, case 2 reusing."""
        _Provider(is_delta=False)._cli_require_cold_context("p")
        _Provider(is_delta=True)._cli_require_delta_context("p")

    def test_neither_guard_fires_twice(self):
        """The rebuilt context is correct; a stale marker must not bounce it."""
        cold = _Provider(is_delta=True)
        with self.assertRaises(ColdStartRequired):
            cold._cli_require_cold_context("p")
        cold._cli_require_cold_context("p")          # now silent

        delta = _Provider(is_delta=False)
        with self.assertRaises(DeltaContextRequired):
            delta._cli_require_delta_context("p")
        delta._cli_require_delta_context("p")        # now silent

    def test_the_guard_flips_the_marker_to_what_the_turn_now_is(self):
        """Otherwise the rebuilt turn would be bounced straight back."""
        cold = _Provider(is_delta=True)
        with self.assertRaises(ColdStartRequired):
            cold._cli_require_cold_context("p")
        self.assertFalse(cold._pawflow_context_is_delta)

        delta = _Provider(is_delta=False)
        with self.assertRaises(DeltaContextRequired):
            delta._cli_require_delta_context("p")
        self.assertTrue(delta._pawflow_context_is_delta)

    def test_the_release_hook_runs_before_the_raise(self):
        """The turn lock is held here and the try/finally has not started.

        A caller that took something before asking must get it back, or the
        next turn on that session waits forever for a turn that already ended.
        """
        for method, marker in (("_cli_require_cold_context", True),
                               ("_cli_require_delta_context", False)):
            released = []
            provider = _Provider(is_delta=marker)
            with self.assertRaises((ColdStartRequired, DeltaContextRequired)):
                getattr(provider, method)("p", release=lambda: released.append(1))
            self.assertEqual(released, [1], method)

    def test_a_failing_release_hook_does_not_swallow_the_raise(self):
        def boom():
            raise RuntimeError("release failed")

        with self.assertRaises(DeltaContextRequired):
            _Provider(is_delta=False)._cli_require_delta_context("p", release=boom)


class EveryCliAsksBothQuestions(unittest.TestCase):
    """The rule is generic: it holds for every CLI provider, not just one."""

    PROVIDERS = (
        "core/llm_providers/_cc_stream.py",
        "core/llm_providers/_codex_app_stream.py",
        "core/llm_providers/_gemini_stream.py",
        "core/llm_providers/claude_code_interactive.py",
        "core/llm_providers/antigravity_interactive.py",
        "core/llm_providers/codex_interactive.py",
    )

    def test_every_cli_provider_guards_both_directions(self):
        missing = []
        for path in self.PROVIDERS:
            src = Path(path).read_text(encoding="utf-8")
            if "_cli_require_cold_context" not in src:
                missing.append(f"{path}: no cold guard (case 1)")
            if "_cli_require_delta_context" not in src:
                missing.append(f"{path}: no delta guard (case 2)")
        self.assertEqual(missing, [], "a CLI can still run in neither case")


class TheLoopRebuildsInBothDirections(unittest.TestCase):

    def test_the_loop_catches_both_and_rebuilds_by_the_ordinary_path(self):
        src = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
        self.assertIn("except ColdStartRequired:", src)
        self.assertIn("except DeltaContextRequired:", src)
        # Rebuilt through _prepare_agent_context, never reassembled by hand.
        self.assertIn("force_cold=True", src)
        self.assertIn("force_delta=True", src)

    def test_the_context_phase_honours_both_forces(self):
        src = Path("tasks/ai/_agentctx_p1.py").read_text(encoding="utf-8")
        self.assertIn('getattr(st, "force_delta", False)', src)
        self.assertIn('getattr(st, "force_cold", False)', src)

    def test_delta_rebuild_uses_the_active_retrigger_payload(self):
        src = Path("tasks/ai/_alc_llm_turn.py").read_text(encoding="utf-8")
        self.assertIn('st.ctx.get("_active_retrigger_messages")', src)
        self.assertIn(
            'st._rebuild_args["skip_current_user_inject"] = True', src)

    def test_the_retry_loop_lets_both_through(self):
        """Swallowed by the provider retry, the rebuild would never happen."""
        src = Path("core/_llm_client_driver.py").read_text(encoding="utf-8")
        self.assertIn("DeltaContextRequired", src)


if __name__ == "__main__":
    unittest.main()
