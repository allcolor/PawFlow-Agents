"""Tests for context-fill fields on StreamEmitter `done` event."""

import unittest
from unittest.mock import MagicMock, patch

from tasks.ai.agent_emitter import AgentResult, StreamEmitter


class TestOnDoneContextFields(unittest.TestCase):

    def _make_emitter(self, max_ctx=200000, cc_window=0):
        bus = MagicMock()
        _cw_map = {("cid1", "test"): cc_window} if cc_window else {}
        client = MagicMock(
            provider="anthropic", base_url="", default_model="x",
            _cc_context_window_by_stream=_cw_map)
        ctx = {
            "active_agent_name": "test",
            "active_llm_service": "svc",
            "user_id": "u",
            "max_context_size": max_ctx,
            "_event_cid": "cid1",
            "client": client,
        }
        em = StreamEmitter(
            conversation_id="cid1", bus=bus, ctx=ctx,
            agent=MagicMock(), gen_key="k", generation=1)
        return em, bus

    def _make_cci_emitter(self):
        bus = MagicMock()
        client = MagicMock(
            provider="claude-code-interactive", base_url="", default_model="x",
            _cc_context_window_by_stream={})
        ctx = {
            "active_agent_name": "test",
            "active_llm_service": "svc",
            "user_id": "u",
            "max_context_size": 1000000,
            "_event_cid": "cid1",
            "client": client,
        }
        return StreamEmitter(
            conversation_id="cid1", bus=bus, ctx=ctx,
            agent=MagicMock(), gen_key="k", generation=1), bus

    def test_on_done_does_not_publish_context_fields(self):
        em, bus = self._make_emitter(max_ctx=200000)
        res = AgentResult(
            response_content="hi", conversation_id="cid1",
            model="claude-x", provider="anthropic",
            tokens_in=50000, tokens_out=200,
            all_msg_ids=["m1"])
        em.on_done(res)
        bus.publish_event.assert_called_once()
        (_cid, evt, data), _ = bus.publish_event.call_args
        self.assertEqual(evt, "done")
        self.assertNotIn("context_used", data)
        self.assertNotIn("context_max", data)
        self.assertNotIn("context_pct", data)

    def test_on_done_zero_max_still_omits_context_fields(self):
        em, bus = self._make_emitter(max_ctx=0)
        res = AgentResult(tokens_in=1000, all_msg_ids=["m"])
        em.on_done(res)
        (_, _, data), _ = bus.publish_event.call_args
        self.assertNotIn("context_used", data)
        self.assertNotIn("context_max", data)
        self.assertNotIn("context_pct", data)

    def test_on_done_no_tokens_still_omits_context_fields(self):
        em, bus = self._make_emitter(max_ctx=128000)
        res = AgentResult(tokens_in=0, all_msg_ids=["m"])
        em.on_done(res)
        (_, _, data), _ = bus.publish_event.call_args
        self.assertNotIn("context_used", data)
        self.assertNotIn("context_max", data)
        self.assertNotIn("context_pct", data)

    def test_cci_gauge_uses_authoritative_context_phase_calculation(self):
        # The shared calculation owns provider-phase accounting; the emitter
        # must not substitute raw provider usage or a second estimate.
        em, _bus = self._make_cci_emitter()
        with patch("tasks.ai.context_usage.compute_context_usage") as compute:
            compute.return_value = {
                "used": 1234, "max": 1000000, "pct": 0.001234,
                "source": "append", "message_count": 3, "cache_mode": "delta",
                "updated_at": 0.0,
            }
            payload = em._context_usage_payload("append")
        compute.assert_called_once()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["context_used"], 1234)

    def test_cci_thinking_callback_publishes_live_delta(self):
        em, bus = self._make_cci_emitter()
        cb = em.get_thinking_callback(False)

        cb("plan")

        bus.publish_event.assert_called_once()
        (cid, evt, data), _ = bus.publish_event.call_args
        self.assertEqual(cid, "cid1")
        self.assertEqual(evt, "thinking_delta")
        self.assertEqual(data["text"], "plan")
        self.assertEqual(data["agent_name"], "test")
        self.assertEqual(data["source"]["provider"], "claude-code-interactive")

    def test_non_cci_thinking_callback_does_not_publish_live_delta(self):
        em, bus = self._make_emitter()
        cb = em.get_thinking_callback(False)

        cb("plan")

        bus.publish_event.assert_not_called()


class TestTokenPreview(unittest.TestCase):
    """The answer appears while it is written, for every provider.

    Each provider feeds the token callback -- the API ones with real deltas,
    the CLI ones with whole blocks. The callback used to drop all of it, so a
    turn landed sealed at the end and only a captured tmux turn ever streamed.
    """

    def _emitter(self, provider="anthropic"):
        bus = MagicMock()
        client = MagicMock(provider=provider, base_url="", default_model="x",
                          _cc_context_window_by_stream={})
        ctx = {"active_agent_name": "test", "active_llm_service": "svc",
               "user_id": "u", "max_context_size": 1000, "_event_cid": "cid1",
               "client": client}
        em = StreamEmitter(conversation_id="cid1", bus=bus, ctx=ctx,
                           agent=MagicMock(), gen_key="k", generation=1)
        em._current_msg_id = "m1"
        return em, bus

    def _tokens(self, bus):
        return [c.args[2] for c in bus.publish_event.call_args_list
                if c.args[1] == "token"]

    def test_a_token_is_published_under_the_id_the_message_will_carry(self):
        """Same id, or the live bubble and the stored line are two messages."""
        em, bus = self._emitter()

        em.get_token_callback(False)("Hel")

        self.assertEqual(self._tokens(bus)[0]["msg_id"], "m1")
        self.assertEqual(self._tokens(bus)[0]["text"], "Hel")

    def test_every_provider_streams_not_just_the_interactive_ones(self):
        for provider in ("anthropic", "openai", "gemini", "claude-code",
                         "claude-code-interactive"):
            em, bus = self._emitter(provider)
            em.get_token_callback(False)("x")
            self.assertEqual(len(self._tokens(bus)), 1, provider)

    def test_a_silent_poll_previews_nothing(self):
        """Its text may end in NO_PENDING_WORK and never be persisted."""
        em, bus = self._emitter()

        em.get_token_callback(True)("quiet")

        self.assertEqual(self._tokens(bus), [])

    def test_no_id_means_no_preview(self):
        """The bus refuses an unpaired token; asking would raise mid-turn."""
        em, bus = self._emitter()
        em._current_msg_id = ""

        em.get_token_callback(False)("orphan")

        self.assertEqual(self._tokens(bus), [])

    def test_a_broken_bus_never_breaks_the_turn_it_previews(self):
        em, bus = self._emitter()
        bus.publish_event.side_effect = RuntimeError("bus down")

        em.get_token_callback(False)("still fine")

    def test_a_cancelled_generation_still_stops_the_turn(self):
        """The preview must not swallow the cancellation the callback raises."""
        from tasks.ai.agent_emitter import AgentCancelled
        em, _bus = self._emitter()
        em.agent._is_current_generation.return_value = False

        with self.assertRaises(AgentCancelled):
            em.get_token_callback(False)("gone")


if __name__ == "__main__":
    unittest.main()
