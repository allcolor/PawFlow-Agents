"""Tests for context-fill fields on StreamEmitter `done` event."""

import unittest
from unittest.mock import MagicMock

from tasks.ai.agent_emitter import AgentResult, StreamEmitter


class TestOnDoneContextFields(unittest.TestCase):

    def _make_emitter(self, max_ctx=200000, cc_window=0):
        bus = MagicMock()
        # Per-stream context window cache replaces the old singleton
        # _cc_context_window attr (which got clobbered across concurrent
        # streams on the shared provider). Key by (conv_id, agent_name).
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

    def test_on_done_publishes_context_fields(self):
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
        self.assertEqual(data["context_used"], 50000)
        self.assertEqual(data["context_max"], 200000)
        self.assertAlmostEqual(data["context_pct"], 0.25, places=4)

    def test_on_done_zero_max_safe(self):
        em, bus = self._make_emitter(max_ctx=0)
        res = AgentResult(tokens_in=1000, all_msg_ids=["m"])
        em.on_done(res)
        (_, _, data), _ = bus.publish_event.call_args
        # Unknown budget: no fictional 200k default. ctx_max stays 0
        # and pct stays 0 — UI skips the gauge rather than display a
        # fake budget.
        self.assertEqual(data["context_max"], 0)
        self.assertEqual(data["context_pct"], 0.0)

    def test_on_done_no_tokens(self):
        em, bus = self._make_emitter(max_ctx=128000)
        res = AgentResult(tokens_in=0, all_msg_ids=["m"])
        em.on_done(res)
        (_, _, data), _ = bus.publish_event.call_args
        self.assertEqual(data["context_used"], 0)
        self.assertEqual(data["context_max"], 128000)
        self.assertEqual(data["context_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
