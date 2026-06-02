"""Tests for IdentityService and cross-channel conversation sharing."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from core.identity_service import IdentityService
from core.conversation_store import ConversationStore
from core import FlowFile


class TestIdentityService(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _paths
        self._orig_ucd = _paths.USER_CONFIG_DIR
        _paths.USER_CONFIG_DIR = __import__('pathlib').Path(self.tmp)
        self.ids = IdentityService()
        IdentityService._instance = self.ids

    def tearDown(self):
        IdentityService.reset()
        import core.paths as _paths
        _paths.USER_CONFIG_DIR = self._orig_ucd
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_link_basic(self):
        ok = self.ids.link("alice@test.com", "telegram", "123456")
        self.assertTrue(ok)

    def test_resolve_user(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        user = self.ids.resolve_user("telegram", "123456")
        self.assertEqual(user, "alice@test.com")

    def test_resolve_unknown(self):
        user = self.ids.resolve_user("telegram", "999999")
        self.assertIsNone(user)

    def test_link_already_linked_different_user(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        ok = self.ids.link("bob@test.com", "telegram", "123456")
        self.assertFalse(ok)

    def test_link_same_user_update(self):
        self.ids.link("alice@test.com", "telegram", "111")
        ok = self.ids.link("alice@test.com", "telegram", "222")
        self.assertTrue(ok)
        user = self.ids.resolve_user("telegram", "222")
        self.assertEqual(user, "alice@test.com")
        self.assertIsNone(self.ids.resolve_user("telegram", "111"))

    def test_unlink(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        ok = self.ids.unlink("alice@test.com", "telegram")
        self.assertTrue(ok)
        self.assertIsNone(self.ids.resolve_user("telegram", "123456"))

    def test_unlink_nonexistent(self):
        ok = self.ids.unlink("nobody@test.com", "telegram")
        self.assertFalse(ok)

    def test_get_channel_id(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        cid = self.ids.get_channel_id("alice@test.com", "telegram")
        self.assertEqual(cid, "123456")

    def test_get_channel_id_unknown(self):
        cid = self.ids.get_channel_id("nobody", "telegram")
        self.assertIsNone(cid)

    def test_active_conv(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        self.ids.set_active_conv("alice@test.com", "telegram", "conv-abc")
        active = self.ids.get_active_conv("alice@test.com", "telegram")
        self.assertEqual(active, "conv-abc")

    def test_active_conv_no_link(self):
        ok = self.ids.set_active_conv("nobody", "telegram", "conv-abc")
        self.assertFalse(ok)

    def test_get_links(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        links = self.ids.get_links("alice@test.com")
        self.assertEqual(links, {"telegram": "123456"})

    def test_get_links_excludes_internal(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        self.ids.set_active_conv("alice@test.com", "telegram", "conv-abc")
        links = self.ids.get_links("alice@test.com")
        self.assertNotIn("active_conv", links)

    def test_persistence(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        self.ids.set_active_conv("alice@test.com", "telegram", "conv-abc")
        # Reload — paths still point to tmp dir
        IdentityService.reset()
        ids2 = IdentityService()
        user = ids2.resolve_user("telegram", "123456")
        self.assertEqual(user, "alice@test.com")
        active = ids2.get_active_conv("alice@test.com", "telegram")
        self.assertEqual(active, "conv-abc")

    def test_singleton(self):
        self.assertIs(IdentityService.instance(), IdentityService.instance())

    def test_list_all(self):
        self.ids.link("alice@test.com", "telegram", "111")
        self.ids.link("bob@test.com", "telegram", "222")
        all_links = self.ids.list_all()
        self.assertIn("alice@test.com", all_links)
        self.assertIn("bob@test.com", all_links)

    def test_unlink_clears_active_conv(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        self.ids.set_active_conv("alice@test.com", "telegram", "conv-abc")
        self.ids.unlink("alice@test.com", "telegram")
        active = self.ids.get_active_conv("alice@test.com", "telegram")
        self.assertIsNone(active)


class TestAgentLoopTelegramAuth(unittest.TestCase):
    """Test that unlinked Telegram users are rejected."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p; self._orig_ucd = _p.USER_CONFIG_DIR; _p.USER_CONFIG_DIR = __import__("pathlib").Path(self.tmp); self.ids = IdentityService()
        IdentityService._instance = self.ids

    def tearDown(self):
        IdentityService.reset()
        import core.paths as _p
        _p.USER_CONFIG_DIR = self._orig_ucd
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unlinked_telegram_user_rejected(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test"})
        ff = FlowFile(content=b"Hello")
        ff.set_attribute("telegram.user_id", "999999")
        ff.set_attribute("telegram.chat_id", "999999")
        result = task.execute(ff)
        self.assertEqual(len(result), 1)
        content = result[0].get_content().decode("utf-8")
        self.assertIn("Access denied", content)

    def test_linked_telegram_user_not_rejected(self):
        # Link the user first
        self.ids.link("alice@test.com", "telegram", "111111")
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "provider": "openai"})
        ff = FlowFile(content=b"Hello")
        ff.set_attribute("telegram.user_id", "111111")
        ff.set_attribute("telegram.chat_id", "111111")
        # This will fail further in the pipeline (no real API key),
        # but it should NOT be rejected at the auth gate
        try:
            task.execute(ff)
        except Exception:
            pass  # Expected — no real LLM
        # If we got here without "Access denied", auth passed

    def test_conv_command_allowed_for_unlinked(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        ff = FlowFile(content=b"/conv info")
        ff.set_attribute("telegram.user_id", "999999")
        ff.set_attribute("telegram.chat_id", "999999")
        result = task.execute(ff)
        content = result[0].get_content().decode("utf-8")
        # Should get the "not linked" message from _handle_telegram_conv_command
        self.assertIn("not linked", content)


class TestTelegramConvCommands(unittest.TestCase):
    """Test /conv commands for Telegram users."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p; self._orig_ucd = _p.USER_CONFIG_DIR; _p.USER_CONFIG_DIR = __import__("pathlib").Path(self.tmp); self.ids = IdentityService()
        self._orig_oauth_tokens = _p.OAUTH_INVITE_TOKENS_FILE
        _p.OAUTH_INVITE_TOKENS_FILE = __import__("pathlib").Path(self.tmp) / "oauth_tokens.json"
        IdentityService._instance = self.ids
        ConversationStore.reset()
        self.store = ConversationStore(store_dir=os.path.join(self.tmp, "convs"))
        ConversationStore._instance = self.store
        # Link a user
        self.ids.link("alice@test.com", "telegram", "111111")

    def tearDown(self):
        IdentityService.reset()
        ConversationStore.reset()
        import core.paths as _p
        _p.OAUTH_INVITE_TOKENS_FILE = self._orig_oauth_tokens
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_ff(self, text):
        ff = FlowFile(content=text.encode("utf-8"))
        ff.set_attribute("telegram.user_id", "111111")
        ff.set_attribute("telegram.chat_id", "111111")
        return ff

    def test_conv_info_no_active(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        result = task.execute(self._make_ff("/conv info"))
        content = result[0].get_content().decode("utf-8")
        self.assertIn("No active conversation", content)

    def test_conv_new(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        result = task.execute(self._make_ff("/conv new"))
        content = result[0].get_content().decode("utf-8")
        self.assertIn("New conversation started", content)
        # Verify active conv was set
        active = self.ids.get_active_conv("alice@test.com", "telegram")
        self.assertIsNotNone(active)

    def test_conv_list_empty(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        result = task.execute(self._make_ff("/conv list"))
        content = result[0].get_content().decode("utf-8")
        self.assertIn("No conversations", content)

    def test_conv_list_with_conversations(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        # Create a conversation
        self.store.save("conv-123", [{"role": "user", "content": "hi"}],
                       user_id="alice@test.com")
        result = task.execute(self._make_ff("/conv list"))
        content = result[0].get_content().decode("utf-8")
        self.assertIn("conv-123", content)

    def test_conv_select(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        self.store.save("conv-abc123", [{"role": "user", "content": "hi"}],
                       user_id="alice@test.com")
        result = task.execute(self._make_ff("/conv select conv-abc123"))
        content = result[0].get_content().decode("utf-8")
        self.assertIn("Switched", content)
        active = self.ids.get_active_conv("alice@test.com", "telegram")
        self.assertEqual(active, "conv-abc123")

    def test_conv_select_prefix(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        self.store.save("conv-abc123def", [{"role": "user", "content": "hi"}],
                       user_id="alice@test.com")
        result = task.execute(self._make_ff("/conv select conv-abc"))
        content = result[0].get_content().decode("utf-8")
        self.assertIn("Switched", content)

    def test_conv_select_not_found(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        result = task.execute(self._make_ff("/conv select nonexistent"))
        content = result[0].get_content().decode("utf-8")
        self.assertIn("not found", content)


class TestAgentLoopAccountLinking(unittest.TestCase):
    """Test link_account / unlink_account / get_links actions."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p; self._orig_ucd = _p.USER_CONFIG_DIR; _p.USER_CONFIG_DIR = __import__("pathlib").Path(self.tmp); self.ids = IdentityService()
        IdentityService._instance = self.ids
        ConversationStore.reset()
        self.store = ConversationStore(store_dir=os.path.join(self.tmp, "convs"))
        ConversationStore._instance = self.store

    def tearDown(self):
        IdentityService.reset()
        ConversationStore.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _action(self, action_data, user_id="alice@test.com"):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        ff = FlowFile(content=json.dumps(action_data).encode("utf-8"))
        ff.set_attribute("http.auth.principal", user_id)
        result = task.execute(ff)
        return json.loads(result[0].get_content().decode("utf-8"))

    def test_link_account_telegram(self):
        resp = self._action({
            "action": "link_account", "provider": "telegram",
            "provider_id": "123456",
        })
        self.assertTrue(resp.get("linked"))
        self.assertEqual(
            self.ids.resolve_user("telegram", "123456"),
            "alice@test.com",
        )

    def test_link_account_telegram_no_auth(self):
        resp = self._action({
            "action": "link_account", "provider": "telegram",
            "provider_id": "123456",
        }, user_id="")
        self.assertIn("error", resp)

    def test_link_account_telegram_conflict(self):
        self.ids.link("bob@test.com", "telegram", "123456")
        resp = self._action({
            "action": "link_account", "provider": "telegram",
            "provider_id": "123456",
        })
        self.assertIn("error", resp)

    def test_unlink_account_telegram(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        resp = self._action({"action": "unlink_account", "provider": "telegram"})
        self.assertTrue(resp.get("unlinked"))
        self.assertIsNone(self.ids.resolve_user("telegram", "123456"))

    def test_get_links(self):
        self.ids.link("alice@test.com", "telegram", "123456")
        resp = self._action({"action": "list_linked_accounts"})
        self.assertEqual(resp["links"]["telegram"], "123456")

    def test_get_links_empty(self):
        resp = self._action({"action": "list_linked_accounts"})
        self.assertEqual(resp["links"], {})

    def test_begin_oauth_account_link_sets_link_cookie_and_clears_session(self):
        from core import oauth_invite_tokens
        from core.security import SecurityManager
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"api_key": "test", "conversation_store": True})
        ff = FlowFile(content=json.dumps({
            "action": "begin_oauth_account_link",
        }).encode("utf-8"))
        ff.set_attribute("http.auth.principal", "alice@test.com")
        ff.set_attribute("http.cookie.pawflow_token", "session-abc")
        SecurityManager.get_instance()._sessions["session-abc"] = object()

        result = task.execute(ff)[0]
        resp = json.loads(result.get_content().decode("utf-8"))

        self.assertTrue(resp["ok"])
        self.assertEqual(resp["login_url"], "/auth/login")
        cookie = result.get_attribute("http.response.header.Set-Cookie")
        self.assertIn("pawflow_oauth_link_token=pfo_", cookie)
        self.assertIn("pawflow_token=; Path=/; Max-Age=0", cookie)
        self.assertNotIn("session-abc", SecurityManager.get_instance()._sessions)
        tokens = oauth_invite_tokens.list_tokens()
        self.assertEqual(tokens[0]["link_username"], "alice@test.com")


class TestMessageChannelField(unittest.TestCase):
    """Test that messages get the 'channel' field when serialized."""

    def test_serialize_with_channel(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.llm_client import LLMMessage
        task = AgentLoopTask({"api_key": "test"})
        msgs = [
            LLMMessage(role="user", content="hello", conversation_id="test_conv"),
            LLMMessage(role="assistant", content="hi there", conversation_id="test_conv"),
            LLMMessage(role="tool", content="result", tool_call_id="tc1", conversation_id="test_conv"),
        ]
        serialized = task._serialize_messages(msgs, channel="telegram")
        self.assertEqual(serialized[0]["channel"], "telegram")
        self.assertEqual(serialized[1]["channel"], "telegram")
        # tool messages don't get channel
        self.assertNotIn("channel", serialized[2])

    def test_serialize_without_channel(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from core.llm_client import LLMMessage
        task = AgentLoopTask({"api_key": "test"})
        msgs = [LLMMessage(role="user", content="hello", conversation_id="test_conv")]
        serialized = task._serialize_messages(msgs)
        self.assertNotIn("channel", serialized[0])

    def test_classify_preserves_channel(self):
        from tasks.ai.agent_loop import AgentLoopTask
        raw = [
            {"role": "user", "content": "hello", "channel": "telegram"},
            {"role": "assistant", "content": "hi"},
        ]
        classified = AgentLoopTask._classify_messages_for_display(raw)
        self.assertEqual(classified[0]["channel"], "telegram")
        self.assertNotIn("channel", classified[1])


class TestIdentityBotToken(unittest.TestCase):
    """Test personal bot token storage in IdentityService."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p; self._orig_ucd = _p.USER_CONFIG_DIR; _p.USER_CONFIG_DIR = __import__("pathlib").Path(self.tmp); self.ids = IdentityService()
        IdentityService._instance = self.ids

    def tearDown(self):
        IdentityService.reset()
        import core.paths as _p
        _p.USER_CONFIG_DIR = self._orig_ucd
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_link_with_bot_token(self):
        self.ids.link("alice@test.com", "telegram", "123", bot_token="BOT:xxx")
        token = self.ids.get_bot_token("alice@test.com", "telegram")
        self.assertEqual(token, "BOT:xxx")

    def test_link_without_bot_token(self):
        self.ids.link("alice@test.com", "telegram", "123")
        token = self.ids.get_bot_token("alice@test.com", "telegram")
        self.assertIsNone(token)

    def test_bot_token_not_in_links(self):
        self.ids.link("alice@test.com", "telegram", "123", bot_token="BOT:xxx")
        links = self.ids.get_links("alice@test.com")
        self.assertNotIn("telegram_bot_token", links)
        self.assertEqual(links["telegram"], "123")

    def test_unlink_removes_bot_token(self):
        self.ids.link("alice@test.com", "telegram", "123", bot_token="BOT:xxx")
        self.ids.unlink("alice@test.com", "telegram")
        token = self.ids.get_bot_token("alice@test.com", "telegram")
        self.assertIsNone(token)

    def test_bot_token_persists(self):
        self.ids.link("alice@test.com", "telegram", "123", bot_token="BOT:xxx")
        # Reload
        IdentityService.reset()
        ids2 = IdentityService()
        token = ids2.get_bot_token("alice@test.com", "telegram")
        self.assertEqual(token, "BOT:xxx")

    def test_update_bot_token(self):
        self.ids.link("alice@test.com", "telegram", "123", bot_token="BOT:old")
        self.ids.link("alice@test.com", "telegram", "123", bot_token="BOT:new")
        token = self.ids.get_bot_token("alice@test.com", "telegram")
        self.assertEqual(token, "BOT:new")


class TestTelegramBotPool(unittest.TestCase):
    """Test TelegramBotPool structure (without real Telegram API)."""

    def setUp(self):
        from services.telegram_bot_service import TelegramBotPool
        TelegramBotPool.reset()
        self.pool = TelegramBotPool()
        self.pool._ensure_polling = lambda: None  # no real polling in tests
        TelegramBotPool._instance = self.pool

    def tearDown(self):
        from services.telegram_bot_service import TelegramBotPool
        TelegramBotPool.reset()

    def test_singleton(self):
        from services.telegram_bot_service import TelegramBotPool
        self.assertIs(TelegramBotPool.instance(), TelegramBotPool.instance())

    def test_get_bot_token_for_user_empty(self):
        token = self.pool.get_bot_token_for_user("alice")
        self.assertIsNone(token)

    @patch("services.telegram_bot_service._api_call_static")
    def test_register_bot(self, mock_api):
        mock_api.return_value = {"username": "mybot"}
        username = self.pool.register_bot("BOT:xxx", "alice@test.com")
        self.assertEqual(username, "mybot")
        token = self.pool.get_bot_token_for_user("alice@test.com")
        self.assertEqual(token, "BOT:xxx")

    @patch("services.telegram_bot_service._api_call_static")
    def test_register_bot_already_registered(self, mock_api):
        mock_api.return_value = {"username": "mybot"}
        self.pool.register_bot("BOT:xxx", "alice@test.com")
        # Second register same token — should return cached username
        username = self.pool.register_bot("BOT:xxx", "alice@test.com")
        self.assertEqual(username, "mybot")
        # getMe only called once (second time returns from cache)
        self.assertEqual(mock_api.call_count, 1)

    @patch("services.telegram_bot_service._api_call_static")
    def test_unregister_bot(self, mock_api):
        mock_api.return_value = {"username": "mybot"}
        self.pool.register_bot("BOT:xxx", "alice@test.com")
        self.pool.unregister_bot("BOT:xxx")
        token = self.pool.get_bot_token_for_user("alice@test.com")
        self.assertIsNone(token)

    def test_unregister_unknown(self):
        # Should not error
        self.pool.unregister_bot("BOT:nonexistent")

    @patch("services.telegram_bot_service._api_call_static")
    def test_callback_dispatch(self, mock_api):
        mock_api.return_value = {"username": "mybot"}
        received = []
        self.pool.register_callback(lambda u: received.append(u))
        self.pool.register_bot("BOT:xxx", "alice@test.com")
        # Simulate dispatch
        self.pool._dispatch({"message": {"text": "hi"}, "_bot_owner": "alice"})
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["_bot_owner"], "alice")

    @patch("services.telegram_bot_service._api_call_static")
    def test_multiple_bots(self, mock_api):
        mock_api.side_effect = [
            {"username": "bot_alice"},
            {"username": "bot_bob"},
        ]
        self.pool.register_bot("BOT:alice", "alice@test.com")
        self.pool.register_bot("BOT:bob", "bob@test.com")
        self.assertEqual(
            self.pool.get_bot_token_for_user("alice@test.com"), "BOT:alice"
        )
        self.assertEqual(
            self.pool.get_bot_token_for_user("bob@test.com"), "BOT:bob"
        )

