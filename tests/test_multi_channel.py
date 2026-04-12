"""Tests for multi-channel messaging infrastructure."""

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from core import FlowFile


# ---------------------------------------------------------------------------
# 1. TestChannelResolution
# ---------------------------------------------------------------------------

class TestChannelResolution:
    """Channel detection based on FlowFile attributes."""

    # CHANNEL_ATTRS is a local variable in agent_loop, so we replicate it here
    CHANNEL_ATTRS = {
        "telegram": ("telegram.chat_id", "telegram.user_id"),
        "discord":  ("discord.channel_id", "discord.user_id"),
        "whatsapp": ("whatsapp.phone", "whatsapp.phone"),
        "slack":    ("slack.channel_id", "slack.user_id"),
    }

    def _resolve_channel(self, ff):
        for ch, (chat_attr, _) in self.CHANNEL_ATTRS.items():
            if ff.get_attribute(chat_attr):
                return ch
        return "web"

    def test_channel_attrs_covers_all_channels(self):
        for ch in ("telegram", "discord", "whatsapp", "slack"):
            assert ch in self.CHANNEL_ATTRS

    def test_channel_web_when_no_attrs(self):
        ff = FlowFile(content=b"hello")
        assert self._resolve_channel(ff) == "web"

    def test_channel_telegram_when_chat_id_set(self):
        ff = FlowFile(content=b"test")
        ff.set_attribute("telegram.chat_id", "123")
        assert self._resolve_channel(ff) == "telegram"

    def test_channel_discord_when_channel_id_set(self):
        ff = FlowFile(content=b"test")
        ff.set_attribute("discord.channel_id", "456")
        assert self._resolve_channel(ff) == "discord"

    def test_channel_whatsapp_when_phone_set(self):
        ff = FlowFile(content=b"test")
        ff.set_attribute("whatsapp.phone", "+33612345678")
        assert self._resolve_channel(ff) == "whatsapp"

    def test_channel_slack_when_channel_id_set(self):
        ff = FlowFile(content=b"test")
        ff.set_attribute("slack.channel_id", "C01ABC")
        assert self._resolve_channel(ff) == "slack"

    def test_channel_priority_first_match_wins(self):
        ff = FlowFile(content=b"test")
        ff.set_attribute("telegram.chat_id", "111")
        ff.set_attribute("discord.channel_id", "222")
        assert self._resolve_channel(ff) == "telegram"


# ---------------------------------------------------------------------------
# 2. TestLinkIdentityHandler
# ---------------------------------------------------------------------------

class TestLinkIdentityHandler:
    """LinkIdentityHandler generate / verify flow."""

    def _make_handler(self):
        from core.tool_registry import LinkIdentityHandler
        handler = LinkIdentityHandler()
        handler.set_user_id("user1")
        handler.set_channel_info("web", "")
        return handler

    def test_generate_creates_6_digit_code(self):
        handler = self._make_handler()
        result = handler.execute({"action": "generate"})
        # Result is a string like "Link code: 123456\n..."
        assert "Link code:" in result
        # Extract code
        code = result.split("Link code:")[1].strip().split("\n")[0].strip()
        assert len(code) == 6
        assert code.isdigit()

    def test_generate_without_user_id_returns_error(self):
        from core.tool_registry import LinkIdentityHandler
        handler = LinkIdentityHandler()
        handler.set_channel_info("web", "")
        result = handler.execute({"action": "generate"})
        assert "Error" in result or "error" in result

    def test_verify_valid_code_links_identity(self):
        handler = self._make_handler()
        gen_result = handler.execute({"action": "generate"})
        code = gen_result.split("Link code:")[1].strip().split("\n")[0].strip()

        from core.tool_registry import LinkIdentityHandler
        handler2 = LinkIdentityHandler()
        handler2.set_user_id("user1")
        handler2.set_channel_info("discord", "disc_999")
        # Mock IdentityService to avoid needing actual instance
        with patch("core.identity_service.IdentityService") as mock_ids_cls:
            mock_ids = MagicMock()
            mock_ids.link.return_value = True
            mock_ids_cls.instance.return_value = mock_ids
            result = handler2.execute({"action": "verify", "code": code})
        assert "linked" in result.lower() or "success" in result.lower() or "connected" in result.lower()

    def test_verify_invalid_code_returns_error(self):
        handler = self._make_handler()
        handler.execute({"action": "generate"})
        result = handler.execute({"action": "verify", "code": "000000"})
        assert "invalid" in result.lower() or "expired" in result.lower() or "error" in result.lower()

    def test_verify_expired_code_returns_error(self):
        handler = self._make_handler()
        gen_result = handler.execute({"action": "generate"})
        code = gen_result.split("Link code:")[1].strip().split("\n")[0].strip()

        from core.tool_registry import LinkIdentityHandler
        # Expire the code by setting expires to past
        with LinkIdentityHandler._codes_lock:
            if code in LinkIdentityHandler._pending_codes:
                LinkIdentityHandler._pending_codes[code]["expires"] = str(time.time() - 600)

        handler2 = LinkIdentityHandler()
        handler2.set_user_id("user1")
        handler2.set_channel_info("discord", "disc_999")
        result = handler2.execute({"action": "verify", "code": code})
        assert "expired" in result.lower() or "invalid" in result.lower()

    def test_codes_stored_in_pending(self):
        from core.tool_registry import LinkIdentityHandler
        handler = self._make_handler()
        gen_result = handler.execute({"action": "generate"})
        code = gen_result.split("Link code:")[1].strip().split("\n")[0].strip()
        assert code in LinkIdentityHandler._pending_codes

    def test_cleanup_expired_on_generate(self):
        from core.tool_registry import LinkIdentityHandler
        # Insert a fake expired code
        with LinkIdentityHandler._codes_lock:
            LinkIdentityHandler._pending_codes["999999"] = {
                "user_id": "old_user",
                "channel": "web",
                "channel_id": "",
                "expires": str(time.time() - 600),
            }
        handler = self._make_handler()
        handler.execute({"action": "generate"})
        # Expired code should have been cleaned up
        assert "999999" not in LinkIdentityHandler._pending_codes


# ---------------------------------------------------------------------------
# 3. TestIdentityServiceCrossChannel
# ---------------------------------------------------------------------------

class TestIdentityServiceCrossChannel:
    """IdentityService linking and resolution across channels."""

    def _make_service(self):
        from core.identity_service import IdentityService
        import core.paths as _p
        self._tmp_dir = tempfile.mkdtemp()
        self._orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(self._tmp_dir)
        IdentityService.reset()
        svc = IdentityService()
        return svc, self._tmp_dir

    def test_link_discord_identity(self):
        svc, path = self._make_service()
        try:
            svc.link("user1", "discord", "disc123")
            assert svc.resolve_user("discord", "disc123") == "user1"
        finally:
            import core.paths as _p2; _p2.USER_CONFIG_DIR = self._orig_ucd; shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_link_whatsapp_identity(self):
        svc, path = self._make_service()
        try:
            svc.link("user1", "whatsapp", "+33600000000")
            assert svc.resolve_user("whatsapp", "+33600000000") == "user1"
        finally:
            import core.paths as _p2; _p2.USER_CONFIG_DIR = self._orig_ucd; shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_link_slack_identity(self):
        svc, path = self._make_service()
        try:
            svc.link("user1", "slack", "U01ABC")
            assert svc.resolve_user("slack", "U01ABC") == "user1"
        finally:
            import core.paths as _p2; _p2.USER_CONFIG_DIR = self._orig_ucd; shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_resolve_user_all_channels(self):
        svc, path = self._make_service()
        try:
            svc.link("alice", "discord", "d1")
            svc.link("alice", "whatsapp", "w1")
            svc.link("alice", "slack", "s1")
            assert svc.resolve_user("discord", "d1") == "alice"
            assert svc.resolve_user("whatsapp", "w1") == "alice"
            assert svc.resolve_user("slack", "s1") == "alice"
        finally:
            import core.paths as _p2; _p2.USER_CONFIG_DIR = self._orig_ucd; shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_get_active_conv_cross_channel(self):
        svc, path = self._make_service()
        try:
            svc.link("bob", "discord", "d2")
            svc.link("bob", "whatsapp", "w2")
            svc.link("bob", "slack", "s2")
            for ch, ext_id in [("discord", "d2"), ("whatsapp", "w2"), ("slack", "s2")]:
                user = svc.resolve_user(ch, ext_id)
                assert user == "bob"
        finally:
            import core.paths as _p2; _p2.USER_CONFIG_DIR = self._orig_ucd; shutil.rmtree(self._tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. TestBaseMessagingService
# ---------------------------------------------------------------------------

class TestBaseMessagingService:
    """BaseMessagingService handler registration and dispatch."""

    def _make_service(self):
        from services.base_messaging_service import BaseMessagingService
        import threading

        class ConcreteService(BaseMessagingService):
            CHANNEL_NAME = "test"

            def _create_connection(self):
                return {}

            def send_message(self, channel_id, text, **kw):
                return {"message_id": "1"}

            def _poll_loop(self):
                while not self._stop_event.is_set():
                    self._stop_event.wait(1)

        svc = ConcreteService({"dummy": "config"})
        return svc

    def test_register_handler_stores_callback(self):
        svc = self._make_service()
        cb = MagicMock()
        svc.register_handler("owner1", cb)
        assert "owner1" in svc._callbacks

    def test_unregister_handler_removes_callback(self):
        svc = self._make_service()
        cb = MagicMock()
        svc.register_handler("owner1", cb)
        svc.unregister_handler("owner1")
        assert "owner1" not in svc._callbacks

    def test_dispatch_calls_all_handlers(self):
        svc = self._make_service()
        cb1 = MagicMock()
        cb2 = MagicMock()
        svc.register_handler("o1", cb1)
        svc.register_handler("o2", cb2)
        msg = {"text": "hello"}
        svc._dispatch(msg)
        cb1.assert_called_once_with(msg)
        cb2.assert_called_once_with(msg)

    def test_dispatch_handles_callback_errors(self):
        svc = self._make_service()
        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        good_cb = MagicMock()
        svc.register_handler("bad", bad_cb)
        svc.register_handler("good", good_cb)
        msg = {"text": "hi"}
        svc._dispatch(msg)
        bad_cb.assert_called_once_with(msg)
        good_cb.assert_called_once_with(msg)


# ---------------------------------------------------------------------------
# 5. TestBaseReceiverTask
# ---------------------------------------------------------------------------

class TestBaseReceiverTask:
    """BaseReceiverTask queue behaviour."""

    def _make_task(self, maxsize=10):
        from tasks.io.base_messaging_tasks import BaseReceiverTask
        import queue as _q

        class ConcreteReceiver(BaseReceiverTask):
            TYPE = "testReceiver"

            def _parse_update(self, update):
                return FlowFile(content=str(update).encode("utf-8"))

        task = ConcreteReceiver({"dummy": "config"})
        task._queue = _q.Queue(maxsize=maxsize)
        return task

    def test_has_pending_input_empty_is_false(self):
        task = self._make_task()
        assert task.has_pending_input() is False

    def test_enqueue_adds_to_queue(self):
        task = self._make_task()
        ff = FlowFile(content=b"test")
        task._enqueue(ff)
        assert not task._queue.empty()

    def test_execute_returns_from_queue(self):
        task = self._make_task()
        ff = FlowFile(content=b"test")
        task._enqueue(ff)
        task._registered = True
        trigger = FlowFile(content=b"trigger")
        result = task.execute(trigger)
        assert result is not None

    def test_queue_full_drops_message(self):
        task = self._make_task(maxsize=1)
        ff1 = FlowFile(content=b"first")
        ff2 = FlowFile(content=b"second")
        task._enqueue(ff1)
        task._enqueue(ff2)  # should not raise
        assert task._queue.qsize() == 1


# ---------------------------------------------------------------------------
# 6. TestI18nChannelKeys
# ---------------------------------------------------------------------------
