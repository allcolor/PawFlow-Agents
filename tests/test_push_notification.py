"""Tests for core.handlers.push_notification.PushNotificationHandler.

Pawflow replacement for the Claude Code built-in `PushNotification`:
publishes one runtime-only `notification` SSE event so every live webchat
client sees the bell, toast, and in-memory notification-center entry.
"""

import unittest
from unittest.mock import patch

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

    def test_execute_publishes_without_persisting(self):
        captured = []

        def _fake_publish(_bus, conversation_id, event_type, data=None):
            captured.append((conversation_id, event_type, data))

        with patch(
            "core.conversation_event_bus.ConversationEventBus.publish_event",
            _fake_publish,
        ), patch(
            "core.conversation_writer.ConversationWriter.enqueue_message",
            side_effect=AssertionError("notification must not be persisted"),
        ):
            res = self.h.execute({"message": "build failed: 2 tests",
                                   "status": "proactive"})

        assert res.startswith("Notification delivered")
        assert len(captured) == 1
        conversation_id, event_type, data = captured[0]
        assert conversation_id == "conv-abc"
        assert event_type == "notification"
        assert data["content"] == "build failed: 2 tests"
        assert data["agent"] == "qwen"
        assert data["status"] == "proactive"
        assert data["msg_id"]

    def test_message_truncated_past_200_chars(self):
        long = "x" * 500
        with patch("core.conversation_event_bus.ConversationEventBus.publish_event",
                   lambda *a, **kw: None):
            res = self.h.execute({"message": long, "status": "proactive"})
        # Message is silently truncated, the call succeeds
        assert res.startswith("Notification delivered")
        # Execute again — the second call should still be within 200 chars
        PushNotificationHandler._last_fire.clear()  # skip cooldown
        captured = {}
        def _cap(_bus, _cid, _event_type, data=None):
            captured["data"] = data
        with patch("core.conversation_event_bus.ConversationEventBus.publish_event", _cap):
            self.h.execute({"message": long, "status": "proactive"})
        assert len(captured["data"]["content"]) <= 200

    def test_newlines_stripped_from_message(self):
        captured = {}
        def _cap(_bus, _cid, _event_type, data=None):
            captured["data"] = data
        with patch("core.conversation_event_bus.ConversationEventBus.publish_event", _cap):
            self.h.execute({"message": "line1\nline2\rline3",
                             "status": "proactive"})
        assert "\n" not in captured["data"]["content"]
        assert "\r" not in captured["data"]["content"]
        assert "line1" in captured["data"]["content"]
        assert "line3" in captured["data"]["content"]

    def test_rate_limit_blocks_rapid_fire(self):
        with patch("core.conversation_event_bus.ConversationEventBus.publish_event",
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
        with patch("core.conversation_event_bus.ConversationEventBus.publish_event",
                   lambda *a, **kw: None):
            r1 = self.h.execute({"message": "a", "status": "proactive"})
            r2 = h2.execute({"message": "b", "status": "proactive"})
        assert r1.startswith("Notification delivered")
        assert r2.startswith("Notification delivered")

    def test_rate_limit_clears_after_window(self):
        with patch("core.conversation_event_bus.ConversationEventBus.publish_event",
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
        matches = [
            entry for entry in schedules
            if entry.get("conversation_id") == "conv-abc"
            and entry.get("reason") == "[scheduled:assistant] [continuation] check memory CSV"
        ]
        assert len(matches) == 1
        entry = matches[0]
        assert entry["user_id"] == "alice"
        assert entry["key"].startswith("conv-abc::continuation::")


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
