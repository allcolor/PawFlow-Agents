"""Tests for core.handlers.push_notification.PushNotificationHandler.

Pawflow replacement for the Claude Code built-in `PushNotification`:
persists the notification via ConversationWriter (so history replays it)
and publishes a `notification` SSE event (so every live webchat client
sees the bell + toast).
"""

import time
import unittest
from unittest.mock import patch, MagicMock

from core.handlers.push_notification import PushNotificationHandler


class TestPushNotificationHandler(unittest.TestCase):

    def setUp(self):
        # Wipe rate-limit state between tests so cooldown doesn't bleed.
        PushNotificationHandler._last_fire.clear()
        self.h = PushNotificationHandler()
        self.h.set_conversation_id("conv-abc")
        self.h.set_agent_name("qwen")
        self.h.set_user_id("alice")

    def test_name_matches_cc_builtin(self):
        assert self.h.name == "PushNotification"

    def test_schema_requires_message_and_status(self):
        sch = self.h.parameters_schema
        assert set(sch["required"]) == {"message", "status"}
        assert sch["properties"]["status"]["enum"] == ["proactive"]

    def test_execute_missing_message_errors(self):
        res = self.h.execute({"status": "proactive"})
        assert res.startswith("Error:")
        assert "message" in res.lower()

    def test_execute_missing_conversation_context_errors(self):
        h = PushNotificationHandler()  # no conv set
        res = h.execute({"message": "hi", "status": "proactive"})
        assert res.startswith("Error:")
        assert "conversation" in res.lower()

    def test_execute_persists_and_publishes(self):
        captured = {}
        def _fake_enqueue(self_w, msg, agent_name="", user_id="",
                         ttl=None, sse_events=None, wait=False):
            captured["msg"] = msg
            captured["agent"] = agent_name
            captured["user"] = user_id
            captured["sse"] = sse_events

        with patch("core.conversation_writer.ConversationWriter.enqueue_message",
                   _fake_enqueue):
            res = self.h.execute({"message": "build failed: 2 tests",
                                   "status": "proactive"})

        assert res.startswith("Notification delivered")
        msg = captured["msg"]
        assert msg["role"] == "user"
        assert msg["content"] == "build failed: 2 tests"
        assert msg["source"]["type"] == "system"
        assert msg["source"]["name"] == "notification"
        assert msg["source"]["agent"] == "qwen"
        assert msg["source"]["status"] == "proactive"
        assert captured["agent"] == "qwen"
        assert captured["user"] == "alice"
        # Two SSE events: new_message (renders the row) + notification
        # (transient bell/toast/browser-notif).
        assert len(captured["sse"]) == 2
        types = [e["type"] for e in captured["sse"]]
        assert types == ["new_message", "notification"]
        nm_evt, notif_evt = captured["sse"]
        assert nm_evt["data"]["role"] == "user"
        assert nm_evt["data"]["content"] == "build failed: 2 tests"
        assert nm_evt["data"]["source"]["name"] == "notification"
        assert notif_evt["cid"] == "conv-abc"
        assert notif_evt["data"]["content"] == "build failed: 2 tests"
        assert notif_evt["data"]["agent"] == "qwen"

    def test_message_truncated_past_200_chars(self):
        long = "x" * 500
        with patch("core.conversation_writer.ConversationWriter.enqueue_message",
                   lambda *a, **kw: None):
            res = self.h.execute({"message": long, "status": "proactive"})
        # Message is silently truncated, the call succeeds
        assert res.startswith("Notification delivered")
        # Execute again — the second call should still be within 200 chars
        PushNotificationHandler._last_fire.clear()  # skip cooldown
        captured = {}
        def _cap(self_w, msg, **kw):
            captured["msg"] = msg
        with patch("core.conversation_writer.ConversationWriter.enqueue_message", _cap):
            self.h.execute({"message": long, "status": "proactive"})
        assert len(captured["msg"]["content"]) <= 200

    def test_newlines_stripped_from_message(self):
        captured = {}
        def _cap(self_w, msg, **kw):
            captured["msg"] = msg
        with patch("core.conversation_writer.ConversationWriter.enqueue_message", _cap):
            self.h.execute({"message": "line1\nline2\rline3",
                             "status": "proactive"})
        assert "\n" not in captured["msg"]["content"]
        assert "\r" not in captured["msg"]["content"]
        assert "line1" in captured["msg"]["content"]
        assert "line3" in captured["msg"]["content"]

    def test_rate_limit_blocks_rapid_fire(self):
        with patch("core.conversation_writer.ConversationWriter.enqueue_message",
                   lambda *a, **kw: None):
            r1 = self.h.execute({"message": "a", "status": "proactive"})
            r2 = self.h.execute({"message": "b", "status": "proactive"})
        assert r1.startswith("Notification delivered")
        assert r2.startswith("Error:")
        assert "rate-limited" in r2.lower()

    def test_rate_limit_is_per_conv_agent(self):
        # Different (conv, agent) tuples don't share cooldown.
        h2 = PushNotificationHandler()
        h2.set_conversation_id("conv-xyz")  # different conv
        h2.set_agent_name("qwen")
        with patch("core.conversation_writer.ConversationWriter.enqueue_message",
                   lambda *a, **kw: None):
            r1 = self.h.execute({"message": "a", "status": "proactive"})
            r2 = h2.execute({"message": "b", "status": "proactive"})
        assert r1.startswith("Notification delivered")
        assert r2.startswith("Notification delivered")

    def test_rate_limit_clears_after_window(self):
        with patch("core.conversation_writer.ConversationWriter.enqueue_message",
                   lambda *a, **kw: None):
            self.h.execute({"message": "a", "status": "proactive"})
            # Fake time jump past the 5s window
            key = ("conv-abc", "qwen")
            PushNotificationHandler._last_fire[key] -= 10.0
            r = self.h.execute({"message": "b", "status": "proactive"})
        assert r.startswith("Notification delivered")


class TestScheduleContinuation(unittest.TestCase):

    def test_requires_conversation_context(self):
        from core.handlers.file_ops import ScheduleContinuationHandler
        h = ScheduleContinuationHandler()
        result = h.execute({"plan": "check memory", "delay_seconds": 60})
        assert result.startswith("Error: no conversation context")

    def test_persists_poll_scheduler_entry(self):
        from core.handlers.file_ops import ScheduleContinuationHandler
        from core.poll_scheduler import PollScheduler
        h = ScheduleContinuationHandler()
        h.set_conversation_id("conv-abc")
        h.set_user_id("alice")
        h.set_agent_name("assistant")
        result = h.execute({"plan": "check memory CSV", "delay_seconds": 60})
        assert result.startswith("Continuation scheduled for ")
        schedules = PollScheduler.instance().list_all()
        assert len(schedules) == 1
        entry = schedules[0]
        assert entry["conversation_id"] == "conv-abc"
        assert entry["user_id"] == "alice"
        assert entry["key"].startswith("conv-abc::continuation::")
        assert entry["reason"] == "[scheduled:assistant] [continuation] check memory CSV"


class TestScheduleWakeupRename(unittest.TestCase):
    """The handler is now called ScheduleWakeup (was schedule_recheck).
    Same semantics, same infra, new name matching the CC built-in.
    """

    def test_handler_imported_by_new_name(self):
        from core.handlers.file_ops import ScheduleWakeupHandler
        h = ScheduleWakeupHandler()
        assert h.name == "ScheduleWakeup"

    def test_registry_exports_new_name(self):
        from core.tool_registry import ScheduleWakeupHandler  # noqa: F401

    def test_old_symbol_is_gone(self):
        from core.handlers import file_ops
        assert not hasattr(file_ops, "ScheduleRecheckHandler")

    def test_disallowed_list_includes_new_mcps(self):
        from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
        disallowed = ClaudeCodeSessionMixin._DISALLOWED_BUILTIN_TOOLS
        assert "ScheduleWakeup" in disallowed
        assert "PushNotification" in disallowed
        assert "Monitor" in disallowed


if __name__ == "__main__":
    unittest.main()
