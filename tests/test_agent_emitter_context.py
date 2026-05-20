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

    def test_cci_heartbeat_skips_pawflow_context_recount(self):
        em, _bus = self._make_cci_emitter()
        with patch("tasks.ai.context_usage.compute_context_usage") as compute:
            self.assertIsNone(em._context_usage_payload("heartbeat"))
        compute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
