"""Tests for Telegram integration — TelegramBotService, TelegramReceiverTask, TelegramSendTask.

Tests cover:
- Service properties and configuration
- Receiver task self-triggering protocol
- Send task parameter schema
- Message conversion to FlowFile
- User filtering
- Flow structure validation
- i18n keys
"""

import json
import base64
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import FlowFile, TaskFactory, ServiceFactory


# ── TelegramBotService ──────────────────────────────────────────────


import core.paths as _paths
class TestTelegramBotService(unittest.TestCase):

    def test_service_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        svc_class = ServiceFactory.get("telegramBot")
        assert svc_class is not None
        assert svc_class.TYPE == "telegramBot"

    def test_service_requires_token(self):
        from services.telegram_bot_service import TelegramBotService
        svc = TelegramBotService({"bot_token": ""})
        with self.assertRaises(ValueError):
            svc._create_connection()

    def test_allowed_users_parsing(self):
        from services.telegram_bot_service import TelegramBotService
        svc = TelegramBotService({
            "bot_token": "test",
            "allowed_users": "123, 456, 789",
        })
        assert svc._allowed_users == {"123", "456", "789"}

    def test_empty_allowed_users(self):
        from services.telegram_bot_service import TelegramBotService
        svc = TelegramBotService({"bot_token": "test"})
        assert svc._allowed_users == set()

    def test_allowed_users_is_not_exposed_in_public_schema(self):
        from services.telegram_bot_service import TelegramBotService
        schema = TelegramBotService({"bot_token": "test"}).get_parameter_schema()
        assert "allowed_users" not in schema

    def test_dispatch_filters_users(self):
        from services.telegram_bot_service import TelegramBotService
        svc = TelegramBotService({
            "bot_token": "test",
            "allowed_users": "100",
        })
        received = []
        svc._callbacks["test"] = lambda u: received.append(u)

        # User 200 should be filtered out
        svc._dispatch({
            "message": {
                "from": {"id": 200, "username": "intruder"},
                "chat": {"id": 200},
                "text": "hello",
            }
        })
        assert len(received) == 0

        # User 100 should pass through
        svc._dispatch({
            "message": {
                "from": {"id": 100, "username": "allowed"},
                "chat": {"id": 100},
                "text": "hello",
            }
        })
        assert len(received) == 1

    def test_register_unregister_handler(self):
        from services.telegram_bot_service import TelegramBotService
        svc = TelegramBotService({"bot_token": "test"})
        # Patch _ensure_polling to avoid starting real thread
        svc._ensure_polling = MagicMock()
        cb = MagicMock()
        svc.register_handler("owner1", cb)
        assert "owner1" in svc._callbacks
        svc.unregister_handler("owner1")
        assert "owner1" not in svc._callbacks

    @patch("services.telegram_bot_service._send_api_call")
    def test_send_message_splits_long(self, send_api):
        """Messages > 4096 chars should be split."""
        from services.telegram_bot_service import TelegramBotService
        svc = TelegramBotService({"bot_token": "test"})
        calls = []

        def mock_api_call(token, method, params=None):
            calls.append(dict(params or {}))
            return {"message_id": len(calls)}

        send_api.side_effect = mock_api_call
        result = svc.send_message(
            "123", "x" * 8200, parse_mode="Markdown", reply_to=42,
            reply_markup={"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]})

        assert len(calls) == 3
        assert result == {"message_id": 3}
        assert all(len(call["text"]) <= 4096 for call in calls)
        assert all(call["parse_mode"] == "Markdown" for call in calls)
        assert calls[0]["reply_to_message_id"] == 42
        assert "reply_to_message_id" not in calls[1]
        assert "reply_markup" not in calls[0]
        assert "reply_markup" in calls[-1]

    @patch("services.telegram_bot_service._send_api_call")
    def test_send_message_keeps_parse_mode_for_short_text(self, send_api):
        from services.telegram_bot_service import TelegramBotService
        svc = TelegramBotService({"bot_token": "test"})
        calls = []

        def mock_api_call(token, method, params=None):
            calls.append(dict(params or {}))
            return {"message_id": 1}

        send_api.side_effect = mock_api_call
        svc.send_message("123", "**hello**", parse_mode="Markdown")

        assert calls == [{
            "chat_id": "123", "text": "**hello**", "parse_mode": "Markdown",
        }]

    @patch("services.telegram_bot_service._send_api_call")
    def test_bot_pool_send_message_splits_long(self, api_call):
        from services.telegram_bot_service import TelegramBotPool
        api_call.side_effect = [{"message_id": 1}, {"message_id": 2}]
        pool = TelegramBotPool()

        result = pool.send_message("token", "123", "word " * 900,
                                   parse_mode="Markdown")

        assert result == {"message_id": 2}
        assert api_call.call_count == 2
        for call in api_call.call_args_list:
            params = call.args[2]
            assert len(params["text"]) <= 4096
            assert params["parse_mode"] == "Markdown"

    def test_html_split_keeps_tags_balanced(self):
        """Long HTML must split without dangling tags (the blockquote 400 bug)."""
        from html.parser import HTMLParser
        from services.telegram_bot_service import _split_telegram_text

        class _Balance(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.ok = True

            def handle_starttag(self, tag, attrs):
                self.stack.append(tag)

            def handle_endtag(self, tag):
                if not self.stack or self.stack[-1] != tag:
                    self.ok = False
                else:
                    self.stack.pop()

        def balanced(s):
            p = _Balance()
            p.feed(s)
            return p.ok and not p.stack

        text = "\U0001f4ad <i>agent thinking</i>\n<blockquote>" + (
            "alpha beta " * 2000) + "</blockquote>"
        chunks = _split_telegram_text(text, parse_mode="HTML")
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 4096
            assert balanced(chunk), f"unbalanced chunk tail: {chunk[-60:]!r}"

    def test_html_split_short_message_untouched(self):
        from services.telegram_bot_service import _split_telegram_text
        msg = "<blockquote>short reasoning</blockquote>"
        assert _split_telegram_text(msg, parse_mode="HTML") == [msg]


# ── TelegramReceiverTask ────────────────────────────────────────────


class TestTelegramReceiverTask(unittest.TestCase):

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        task_class = TaskFactory.get("telegramReceiver")
        assert task_class is not None
        assert task_class.TYPE == "telegramReceiver"

    def test_task_metadata(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        assert TelegramReceiverTask.NAME == "Telegram Receiver"
        assert TelegramReceiverTask.ICON == "telegram"

    def test_has_pending_input_protocol(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        assert task.has_pending_input() is False

    def test_message_to_flowfile(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True

        # Simulate incoming update
        update = {
            "update_id": 1,
            "message": {
                "message_id": 42,
                "from": {"id": 123, "username": "testuser", "first_name": "Test"},
                "chat": {"id": 456},
                "text": "Hello bot!",
            },
        }
        task._on_update(update)

        assert task.has_pending_input() is True
        results = task.execute()
        assert len(results) == 1
        ff = results[0]
        assert ff.get_content() == b"Hello bot!"
        assert ff.get_attribute("telegram.chat_id") == "456"
        assert ff.get_attribute("telegram.user_id") == "123"
        assert ff.get_attribute("telegram.username") == "testuser"
        assert ff.get_attribute("telegram.first_name") == "Test"
        assert ff.get_attribute("telegram.message_id") == "42"
        assert ff.get_attribute("telegram.message_type") == "text"

    def test_document_message(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True

        update = {
            "update_id": 2,
            "message": {
                "message_id": 43,
                "from": {"id": 123, "username": "testuser", "first_name": "Test"},
                "chat": {"id": 456},
                "document": {
                    "file_id": "doc_abc123",
                    "file_name": "report.pdf",
                },
                "caption": "Here's the report",
            },
        }
        task._on_update(update)
        results = task.execute()
        ff = results[0]
        content = json.loads(ff.get_content().decode())
        assert content["type"] == "document"
        assert content["file_id"] == "doc_abc123"
        assert content["file_name"] == "report.pdf"
        assert ff.get_attribute("telegram.message_type") == "document"

    def test_photo_message(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True

        update = {
            "update_id": 3,
            "message": {
                "message_id": 44,
                "from": {"id": 123, "username": "testuser", "first_name": "Test"},
                "chat": {"id": 456},
                "photo": [
                    {"file_id": "small_id", "width": 100, "height": 100},
                    {"file_id": "large_id", "width": 800, "height": 800},
                ],
                "caption": "A photo",
            },
        }
        task._on_update(update)
        results = task.execute()
        ff = results[0]
        # Photo content is the caption text (or "(photo)" if no caption)
        assert ff.get_content() == b"A photo"
        assert ff.get_attribute("telegram.message_type") == "photo"
        # image_base64/image_file_id only set when download succeeds (no service in test)

    def test_voice_download_uses_personal_bot_token(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True

        with patch("services.telegram_bot_service.TelegramBotPool.instance") as pool_instance:
            pool = MagicMock()
            pool.get_file_bytes.return_value = (b"voice-bytes", "voice/file.oga")
            pool_instance.return_value = pool
            task._on_update({
                "update_id": 7,
                "_bot_token": "personal-token",
                "message": {
                    "message_id": 46,
                    "from": {"id": 123, "username": "testuser", "first_name": "Test"},
                    "chat": {"id": 456},
                    "voice": {"file_id": "voice_123", "duration": 2},
                },
            })

        ff = task.execute()[0]
        content = json.loads(ff.get_content().decode())
        assert ff.get_attribute("telegram.message_type") == "voice"
        assert content["file_name"] == "telegram_voice.ogg"
        assert content["mime_type"] == "audio/ogg"
        assert base64.b64decode(content["data_base64"]) == b"voice-bytes"
        pool.get_file_bytes.assert_called_once_with("personal-token", "voice_123")

    def test_audio_message_is_marked_for_stt(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True

        with patch("services.telegram_bot_service.TelegramBotPool.instance") as pool_instance:
            pool = MagicMock()
            pool.get_file_bytes.return_value = (b"audio-bytes", "audio/file.mp3")
            pool_instance.return_value = pool
            task._on_update({
                "update_id": 8,
                "_bot_token": "personal-token",
                "message": {
                    "message_id": 47,
                    "from": {"id": 123, "username": "testuser", "first_name": "Test"},
                    "chat": {"id": 456},
                    "audio": {
                        "file_id": "audio_123",
                        "file_name": "clip.mp3",
                        "mime_type": "audio/mpeg",
                        "duration": 3,
                    },
                },
            })

        ff = task.execute()[0]
        content = json.loads(ff.get_content().decode())
        assert ff.get_attribute("telegram.message_type") == "audio"
        assert content["type"] == "audio"
        assert content["mime_type"] == "audio/mpeg"
        assert base64.b64decode(content["data_base64"]) == b"audio-bytes"

    def test_non_message_update_ignored(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._on_update({"update_id": 5, "edited_message": {}})
        assert task.has_pending_input() is False

    def test_callback_query_to_flowfile(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True

        task._on_update({
            "update_id": 6,
            "callback_query": {
                "id": "cb1",
                "from": {"id": 123, "username": "testuser", "first_name": "Test"},
                "data": "conv:new:start",
                "message": {"message_id": 45, "chat": {"id": 456}},
            },
        })

        ff = task.execute()[0]
        assert ff.get_content() == b"conv:new:start"
        assert ff.get_attribute("telegram.message_type") == "callback_query"
        assert ff.get_attribute("telegram.callback_query_id") == "cb1"
        assert ff.get_attribute("telegram.callback_data") == "conv:new:start"
        assert ff.get_attribute("telegram.user_id") == "123"

    def test_empty_queue_returns_empty(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True  # skip service lookup
        results = task.execute()
        assert results == []

    def test_message_enrichment_attributes(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True
        task._on_update({
            "update_id": 10,
            "message": {
                "message_id": 50,
                "from": {"id": 123, "username": "u", "first_name": "T"},
                "chat": {"id": -100456, "type": "supergroup", "title": "Grp"},
                "text": "spam @someone",
                "entities": [{"type": "mention", "offset": 5, "length": 8}],
                "reply_to_message": {
                    "message_id": 49,
                    "from": {"id": 999, "username": "victim"},
                    "text": "original",
                },
            },
        })
        ff = task.execute()[0]
        assert ff.get_attribute("telegram.update_type") == "message"
        assert ff.get_attribute("telegram.chat_type") == "supergroup"
        assert ff.get_attribute("telegram.chat_title") == "Grp"
        assert ff.get_attribute("telegram.reply_to_message_id") == "49"
        assert ff.get_attribute("telegram.reply_to_user_id") == "999"
        assert ff.get_attribute("telegram.reply_to_text") == "original"
        assert json.loads(ff.get_attribute("telegram.entities"))[0]["type"] == "mention"
        assert json.loads(ff.get_attribute("telegram.raw"))["update_id"] == 10

    def test_new_chat_members_surfaced(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True
        task._on_update({
            "update_id": 11,
            "message": {
                "message_id": 51,
                "from": {"id": 1, "username": "adder"},
                "chat": {"id": -100456, "type": "supergroup"},
                "new_chat_members": [{"id": 777, "username": "newbie"}],
            },
        })
        ff = task.execute()[0]
        assert ff.get_attribute("telegram.new_chat_member_ids") == "777"
        assert json.loads(ff.get_attribute("telegram.new_chat_members"))[0]["id"] == 777

    def test_my_chat_member_update_emitted(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True
        task._on_update({
            "update_id": 12,
            "my_chat_member": {
                "chat": {"id": -100456, "type": "supergroup", "title": "Grp"},
                "from": {"id": 1, "username": "owner", "first_name": "O"},
                "old_chat_member": {"user": {"id": 5}, "status": "left"},
                "new_chat_member": {
                    "user": {"id": 5, "username": "thebot", "is_bot": True},
                    "status": "administrator",
                },
            },
        })
        assert task.has_pending_input() is True
        ff = task.execute()[0]
        assert ff.get_attribute("telegram.update_type") == "my_chat_member"
        assert ff.get_attribute("telegram.message_type") == "my_chat_member"
        assert ff.get_attribute("telegram.chat_id") == "-100456"
        assert ff.get_attribute("telegram.old_status") == "left"
        assert ff.get_attribute("telegram.new_status") == "administrator"
        assert ff.get_attribute("telegram.target_user_id") == "5"
        assert ff.get_attribute("telegram.user_id") == "1"

    def test_allowed_updates_config_parsed(self):
        from services.telegram_bot_service import (
            TelegramBotService, _DEFAULT_ALLOWED_UPDATES)
        svc = TelegramBotService({
            "bot_token": "t",
            "allowed_updates": "message, my_chat_member , chat_member",
        })
        assert svc._allowed_updates == [
            "message", "my_chat_member", "chat_member"]
        # Empty config falls back to the default set.
        svc2 = TelegramBotService({"bot_token": "t"})
        assert svc2._allowed_updates == list(_DEFAULT_ALLOWED_UPDATES)
        # Union is idempotent and additive.
        svc2.add_allowed_updates("message,chat_member")
        assert svc2._allowed_updates == [
            "message", "callback_query", "chat_member"]

    def test_receiver_pushes_allowed_updates_to_service(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({
            "service_id": "tg",
            "allowed_updates": "my_chat_member,chat_member",
        })
        svc = MagicMock()
        with patch.object(task, "get_service", return_value=svc), \
                patch.object(task, "_register_pool_bots"):
            task._ensure_registered()
        svc.add_allowed_updates.assert_called_once_with(
            "my_chat_member,chat_member")


# ── TelegramApiTask ─────────────────────────────────────────────────


class TestTelegramApiTask(unittest.TestCase):

    def _task(self, config):
        from tasks.io.telegram_api import TelegramApiTask
        return TelegramApiTask(config)

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        assert TaskFactory.get("telegramApi") is not None

    def test_calls_method_with_resolved_params(self):
        from core import FlowFile
        task = self._task({
            "service_id": "tg",
            "method": "banChatMember",
            "params": '{"chat_id": "${telegram.chat_id}", '
                      '"user_id": "${telegram.target_user_id}", "revoke": true}',
        })
        svc = MagicMock()
        svc.call_api.return_value = True
        ff = FlowFile(content=b"")
        ff.set_attribute("telegram.chat_id", "-100456")
        ff.set_attribute("telegram.target_user_id", "777")
        with patch.object(task, "get_service", return_value=svc):
            out = task.execute(ff)[0]
        svc.call_api.assert_called_once_with(
            "banChatMember",
            {"chat_id": "-100456", "user_id": "777", "revoke": True})
        assert out.get_attribute("telegram.api_ok") == "true"
        assert out.get_attribute("telegram.api_method") == "banChatMember"

    def test_api_error_is_non_fatal_by_default(self):
        from core import FlowFile
        task = self._task({
            "service_id": "tg", "method": "banChatMember",
            "params": '{"chat_id": "1", "user_id": "2"}',
        })
        svc = MagicMock()
        svc.call_api.side_effect = RuntimeError("not enough rights")
        with patch.object(task, "get_service", return_value=svc):
            out = task.execute(FlowFile(content=b""))[0]
        assert out.get_attribute("telegram.api_ok") == "false"
        assert "not enough rights" in out.get_attribute("telegram.api_error")

    def test_api_error_raises_when_configured(self):
        from core import FlowFile, TaskError
        task = self._task({
            "service_id": "tg", "method": "banChatMember",
            "raise_on_error": True,
        })
        svc = MagicMock()
        svc.call_api.side_effect = RuntimeError("boom")
        with patch.object(task, "get_service", return_value=svc):
            with self.assertRaises(TaskError):
                task.execute(FlowFile(content=b""))

    def test_missing_method_raises(self):
        from core import FlowFile, TaskError
        task = self._task({"service_id": "tg", "method": ""})
        with patch.object(task, "get_service", return_value=MagicMock()):
            with self.assertRaises(TaskError):
                task.execute(FlowFile(content=b""))


# ── TelegramSendTask ────────────────────────────────────────────────


class TestTelegramSendTask(unittest.TestCase):

    def test_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        task_class = TaskFactory.get("telegramSend")
        assert task_class is not None
        assert task_class.TYPE == "telegramSend"

    def test_task_metadata(self):
        from tasks.io.telegram_send import TelegramSendTask
        assert TelegramSendTask.NAME == "Telegram Send"
        assert TelegramSendTask.ICON == "telegram"

    def test_parameter_schema(self):
        from tasks.io.telegram_send import TelegramSendTask
        task = TelegramSendTask({"service_id": "tg", "chat_id": "456"})
        schema = task.get_parameter_schema()
        assert "service_id" in schema
        assert "chat_id" in schema
        assert "parse_mode" in schema

    def test_reply_markup_attribute_is_sent(self):
        from core import FlowFile
        from tasks.io.telegram_send import TelegramSendTask

        svc = MagicMock()
        svc.send_message.return_value = {"message_id": 7}
        task = TelegramSendTask({"service_id": "tg", "chat_id": "456"})
        task.get_service = lambda service_id: svc

        ff = FlowFile(content=b"Choose")
        ff.set_attribute("telegram.chat_id", "456")
        ff.set_attribute("telegram.reply_markup", json.dumps({
            "inline_keyboard": [[{"text": "A", "callback_data": "a"}]],
        }))

        task.execute(ff)

        svc.send_message.assert_called_once()
        assert svc.send_message.call_args.kwargs["reply_markup"] == {
            "inline_keyboard": [[{"text": "A", "callback_data": "a"}]],
        }

    def test_markdown_parse_error_retries_as_plain_text(self):
        """A Telegram 400 'can't parse entities' must not lose the message:
        the send is retried without parse_mode."""
        from core import FlowFile
        from tasks.io.telegram_send import TelegramSendTask

        svc = MagicMock()
        svc.send_message.side_effect = [
            RuntimeError(
                'Telegram API sendMessage returned 400: {"ok":false,'
                '"error_code":400,"description":"Bad Request: can\'t parse '
                'entities: Can\'t find end of the entity starting at byte '
                'offset 81"}'),
            {"message_id": 8},
        ]
        task = TelegramSendTask({"service_id": "tg", "chat_id": "456"})
        task.get_service = lambda service_id: svc

        ff = FlowFile(content=b"/conv new <agent> --title <title> agent_runtime_port")
        ff.set_attribute("telegram.chat_id", "456")

        out = task.execute(ff)

        assert svc.send_message.call_count == 2
        assert svc.send_message.call_args_list[0].kwargs["parse_mode"] == "Markdown"
        assert svc.send_message.call_args_list[1].kwargs["parse_mode"] == ""
        assert out[0].get_attribute("telegram.send_status") == "sent"
        assert out[0].get_attribute("telegram.sent_message_id") == "8"

    def test_non_parse_send_error_is_not_retried(self):
        from core import FlowFile
        from tasks.io.telegram_send import TelegramSendTask

        svc = MagicMock()
        svc.send_message.side_effect = RuntimeError(
            "Telegram API sendMessage returned 403: bot was blocked")
        task = TelegramSendTask({"service_id": "tg", "chat_id": "456"})
        task.get_service = lambda service_id: svc

        ff = FlowFile(content=b"hello")
        ff.set_attribute("telegram.chat_id", "456")

        out = task.execute(ff)

        assert svc.send_message.call_count == 1
        assert out[0].get_attribute("telegram.send_status") == "error"

    def test_tts_audio_attribute_is_sent_after_text(self):
        from core import FlowFile
        from tasks.io.telegram_send import TelegramSendTask

        svc = MagicMock()
        svc.send_message.return_value = {"message_id": 7}
        task = TelegramSendTask({"service_id": "tg", "chat_id": "456"})
        task.get_service = lambda service_id: svc

        ff = FlowFile(content=b"Reply text")
        ff.set_attribute("telegram.chat_id", "456")
        ff.set_attribute("telegram.tts_audio_base64", base64.b64encode(b"audio").decode("ascii"))
        ff.set_attribute("telegram.tts_filename", "reply.mp3")
        ff.set_attribute("telegram.tts_content_type", "audio/mpeg")

        task.execute(ff)

        svc.send_message.assert_called_once()
        svc.send_audio.assert_called_once_with(
            "456", b"audio", filename="reply.mp3", content_type="audio/mpeg")

    def test_tts_audio_attribute_is_sent_without_duplicate_text(self):
        from core import FlowFile
        from tasks.io.telegram_send import TelegramSendTask

        svc = MagicMock()
        task = TelegramSendTask({"service_id": "tg", "chat_id": "456"})
        task.get_service = lambda service_id: svc

        ff = FlowFile(content=b"")
        ff.set_attribute("telegram.chat_id", "456")
        ff.set_attribute("telegram.tts_audio_base64", base64.b64encode(b"audio").decode("ascii"))
        ff.set_attribute("telegram.tts_filename", "reply.mp3")
        ff.set_attribute("telegram.tts_content_type", "audio/mpeg")

        task.execute(ff)

        svc.send_message.assert_not_called()
        svc.send_audio.assert_called_once_with(
            "456", b"audio", filename="reply.mp3", content_type="audio/mpeg")


class TestTelegramAgentClientTask(unittest.TestCase):

    def test_conversation_bridge_task_registered(self):
        from tasks import register_all_tasks
        register_all_tasks()
        task_class = TaskFactory.get("telegramConversationBridge")
        assert task_class is not None
        assert task_class.TYPE == "telegramConversationBridge"

    def _make_bridge(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        bridge = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        sent = []
        bridge._send = lambda user_id, chat_id, text: (sent.append(text) or True)
        # One subscriber for any conversation; stub media side-channels.
        bridge._telegram_subscribers = staticmethod(
            lambda conversation_id, data=None: iter([("u1", "c1")]))
        bridge._send_tts_audio = lambda *a, **k: None
        bridge._send_message_attachments = lambda *a, **k: None
        bridge._send_tool_media = lambda *a, **k: None
        return bridge, sent

    def test_bridge_flushes_thinking_when_closed_by_final_message(self):
        # The actual bug: thinking_content carries `agent_name`, but the final
        # `new_message` carries only `source.name`. Keying the flush on
        # agent_name alone stranded the pre-answer thinking of no-tool-call
        # turns. The burst must flush (before the message) when the closer is a
        # new_message identified only by source.name.
        bridge, sent = self._make_bridge()
        cid = "convX"
        bridge._on_event(cid, "thinking_content",
                         {"text": "pre-answer reasoning", "agent_name": "claude"})
        bridge._on_event(cid, "new_message", {
            "role": "assistant", "content": "the answer",
            "msg_id": "m1", "source": {"name": "claude"}})
        joined = "\n".join(sent)
        assert "pre-answer reasoning" in joined, sent
        # Thinking is delivered before the answer message.
        think_idx = next(i for i, t in enumerate(sent) if "pre-answer reasoning" in t)
        ans_idx = next(i for i, t in enumerate(sent) if "the answer" in t)
        assert think_idx < ans_idx, sent

    def test_bridge_flushes_last_thinking_burst_on_done(self):
        # The final reasoning of a turn (... -> thinking_content -> done) has no
        # tool/message after it to close the burst; `done` must flush it or it
        # never reaches Telegram (webchat showed it, Telegram did not).
        bridge, sent = self._make_bridge()
        cid = "convX"
        bridge._on_event(cid, "thinking_content",
                         {"text": "final reasoning block", "agent_name": "claude"})
        assert sent == []  # buffered, not yet flushed
        bridge._on_event(cid, "done", {"agent_name": "claude"})
        assert any("final reasoning block" in t for t in sent), sent

    def test_bridge_flushes_thinking_on_error_event(self):
        bridge, sent = self._make_bridge()
        cid = "convX"
        bridge._on_event(cid, "thinking_content",
                         {"text": "reasoning before error", "agent_name": "claude"})
        bridge._on_event(cid, "error_event", {"agent_name": "claude"})
        assert any("reasoning before error" in t for t in sent), sent

    def test_bridge_done_flushes_all_agents(self):
        bridge, sent = self._make_bridge()
        cid = "convX"
        bridge._on_event(cid, "thinking_content",
                         {"text": "alpha reasoning", "agent_name": "a"})
        bridge._on_event(cid, "thinking_content",
                         {"text": "beta reasoning", "agent_name": "b"})
        bridge._on_event(cid, "done", {})
        joined = "\n".join(sent)
        assert "alpha reasoning" in joined and "beta reasoning" in joined, sent

    def test_agent_client_forwards_telegram_image_as_attachment(self):
        src = Path("tasks/io/telegram_agent_client.py").read_text(encoding="utf-8")
        assert 'flowfile.get_attribute("telegram.image_base64")' in src
        assert '"mime_type": "image/jpeg"' in src
        assert "attachments=attachments" in src

    def test_agent_client_reports_voice_without_stt(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=json.dumps({
                "type": "voice",
                "data_base64": base64.b64encode(b"audio").decode("ascii"),
            }).encode("utf-8"))
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m1")
            ff.set_attribute("telegram.message_type", "voice")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("tasks.ai.actions.media.resolve_stt_service", return_value=(None, "no STT service available")), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message") as submit:
                out = task.execute(ff)

            assert len(out) == 1
            reply = out[0].get_content().decode("utf-8")
            assert "Speech transcription failed" in reply
            assert "no STT service available" in reply
            submit.assert_not_called()
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_reports_configured_stt_runtime_error(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        class FailingSTT:
            def transcribe(self, **_kwargs):
                raise RuntimeError("Whisper model turbo is being downloaded")

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=json.dumps({
                "type": "voice",
                "data_base64": base64.b64encode(b"audio").decode("ascii"),
            }).encode("utf-8"))
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m1")
            ff.set_attribute("telegram.message_type", "voice")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("tasks.ai.actions.media.resolve_stt_service", return_value=(FailingSTT(), None)), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message") as submit:
                out = task.execute(ff)

            assert len(out) == 1
            assert out[0].get_content().decode("utf-8") == (
                "Speech transcription failed: Whisper model turbo is being downloaded")
            submit.assert_not_called()
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_telegram_voice_uses_configured_stt_service(self):
        from unittest.mock import patch
        from tasks.io.telegram_agent_client import _transcribe_telegram_voice

        class FakeSTT:
            def __init__(self):
                self.calls = []

            def set_runtime_context(self, **kwargs):
                self.context = kwargs

            def transcribe(self, **kwargs):
                self.calls.append(kwargs)
                if kwargs.get("audio_path"):
                    assert Path(kwargs["audio_path"]).read_bytes() == b"audio"
                    assert kwargs["audio_bytes"] == b""
                return {"text": "transcribed voice"}

        svc = FakeSTT()
        content = json.dumps({
            "type": "voice",
            "data_base64": base64.b64encode(b"audio").decode("ascii"),
        })

        with patch("tasks.ai.actions.media.resolve_stt_service", return_value=(svc, None)) as resolve:
            text = _transcribe_telegram_voice(content, "alice", "conv1", "assistant")

        assert text == "transcribed voice"
        resolve.assert_called_once_with("alice", "conv1", "assistant", ("transcribe",))
        if not svc.calls[0].get("audio_path"):
            assert svc.calls[0]["audio_bytes"] == b"audio"
        assert svc.calls[0]["mime_type"] == "audio/ogg"

    def test_agent_client_voice_resolves_prefixed_telegram_link_to_principal_for_stt(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        class FakeSTT:
            def transcribe(self, **kwargs):
                return {"text": "transcribed from prefixed link"}

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "telegram:111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            svc = FakeSTT()

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=json.dumps({
                "type": "voice",
                "data_base64": base64.b64encode(b"audio").decode("ascii"),
            }).encode("utf-8"))
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m1")
            ff.set_attribute("telegram.message_type", "voice")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("tasks.ai.actions.media.resolve_stt_service", return_value=(svc, None)) as resolve, \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", return_value=type("Submission", (), {
                        "conversation_id": "conv1",
                        "turn_id": "telegram:111111:m1",
                        "wait_for_done": False,
                        "status": "accepted",
                    })()) as submit:
                out = task.execute(ff)

            assert out == []
            resolve.assert_called_once_with("alice", "conv1", "assistant", ("transcribe",))
            submit.assert_called_once()
            request = submit.call_args.args[0]
            assert request.user_id == "alice"
            assert request.message == "transcribed from prefixed link"
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_telegram_audio_uses_configured_stt_service(self):
        from unittest.mock import patch
        from tasks.io.telegram_agent_client import _transcribe_telegram_voice

        class FakeSTT:
            def transcribe(self, **kwargs):
                self.kwargs = kwargs
                return {"text": "transcribed audio"}

        svc = FakeSTT()
        content = json.dumps({
            "type": "audio",
            "file_name": "clip.mp3",
            "mime_type": "audio/mpeg",
            "data_base64": base64.b64encode(b"audio").decode("ascii"),
        })

        with patch("tasks.ai.actions.media.resolve_stt_service", return_value=(svc, None)):
            text = _transcribe_telegram_voice(content, "alice", "conv1", "assistant")

        assert text == "transcribed audio"
        assert svc.kwargs["mime_type"] == "audio/mpeg"
        assert svc.kwargs["filename"] == "clip.mp3"

    def test_telegram_voice_uses_shared_stt_resolver(self):
        from unittest.mock import patch
        from tasks.io.telegram_agent_client import _transcribe_telegram_voice

        class FakeSTT:
            def transcribe(self, **kwargs):
                return {"text": "resolved transcribed"}

        svc = FakeSTT()
        content = json.dumps({
            "type": "voice",
            "data_base64": base64.b64encode(b"audio").decode("ascii"),
        })

        with patch("tasks.ai.actions.media.resolve_stt_service", return_value=(svc, None)) as resolve:
            text = _transcribe_telegram_voice(content, "alice", "conv1", "assistant")

        assert text == "resolved transcribed"
        resolve.assert_called_once_with("alice", "conv1", "assistant", ("transcribe",))

    def test_telegram_voice_passes_task_conversation_to_shared_stt_resolver(self):
        from unittest.mock import patch
        from tasks.io.telegram_agent_client import _transcribe_telegram_voice

        class FakeSTT:
            def transcribe(self, **kwargs):
                return {"text": "task transcribed"}

        svc = FakeSTT()
        content = json.dumps({
            "type": "voice",
            "file_name": "telegram_voice.ogg",
            "mime_type": "audio/ogg",
            "data_base64": base64.b64encode(b"audio").decode("ascii"),
        })

        with patch("tasks.ai.actions.media.resolve_stt_service", return_value=(svc, None)) as resolve:
            text = _transcribe_telegram_voice(
                content, "alice", "conv1::task::t123", "assistant")

        assert text == "task transcribed"
        resolve.assert_called_once_with(
            "alice", "conv1::task::t123", "assistant", ("transcribe",))

    def test_shared_stt_resolver_auto_selects_any_registered_stt_service(self):
        from services.base_stt import BaseSTTService
        from tasks.ai.actions.media import resolve_stt_service

        class CustomTelegramSTT(BaseSTTService):
            TYPE = "customTelegramSTT"

            def transcribe(self, **kwargs):
                return {"text": "custom"}

        class FakeDef:
            service_id = "custom_stt"
            service_type = "customTelegramSTT"
            scope = "global"
            scope_id = ""

        svc = CustomTelegramSTT({})
        ServiceFactory.register(CustomTelegramSTT)
        registry = MagicMock()
        registry.resolve_by_type.side_effect = lambda service_type, **kwargs: (
            [FakeDef()] if service_type == "customTelegramSTT" else []
        )
        registry.resolve.return_value = svc
        try:
            with patch("core.service_registry.ServiceRegistry.get_instance", return_value=registry):
                resolved, err = resolve_stt_service("alice", "conv1", "assistant")
        finally:
            ServiceFactory._services.pop("customTelegramSTT", None)

        assert resolved is svc
        assert err is None
        registry.resolve.assert_called_once_with("custom_stt", user_id="alice", conv_id="conv1")

    def test_agent_client_materializes_telegram_image_attachment(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from core.agent_runtime_api import AgentFinalResult
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")
            captured = {}

            def submit(req):
                captured["attachments"] = req.attachments
                return type("Submission", (), {
                    "conversation_id": "conv1",
                    "turn_id": "telegram:111111:m1",
                })()

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=b"caption")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m1")
            ff.set_attribute("telegram.message_type", "photo")
            ff.set_attribute("telegram.image_base64", base64.b64encode(b"image-bytes").decode("ascii"))

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("core.file_store.FileStore.instance") as fs_instance, \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", side_effect=submit), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.wait_for_done", return_value=AgentFinalResult("conv1", "telegram:111111:m1", response="ok")):
                fs = MagicMock()
                fs.store.return_value = "file123"
                fs_instance.return_value = fs
                task.execute(ff)

            assert captured["attachments"][0]["file_id"] == "file123"
            assert captured["attachments"][0]["url"] == "/files/file123/telegram_photo.jpg"
            assert captured["attachments"][0]["data"]
            fs.store.assert_called_once()
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_forwards_telegram_document_as_attachment_not_base64_text(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from core.agent_runtime_api import AgentFinalResult
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")
            captured = {}

            def submit(req):
                captured["message"] = req.message
                captured["attachments"] = req.attachments
                return type("Submission", (), {
                    "conversation_id": "conv1",
                    "turn_id": "telegram:111111:m-doc",
                    "wait_for_done": True,
                    "status": "accepted",
                })()

            payload = {
                "type": "document",
                "file_name": "report.pdf",
                "mime_type": "application/pdf",
                "caption": "Please review",
                "data_base64": base64.b64encode(b"%PDF-1.4").decode("ascii"),
            }
            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=json.dumps(payload).encode("utf-8"))
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m-doc")
            ff.set_attribute("telegram.message_type", "document")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("core.file_store.FileStore.instance") as fs_instance, \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", side_effect=submit), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.wait_for_done", return_value=AgentFinalResult("conv1", "telegram:111111:m-doc", response="ok")):
                fs = MagicMock()
                fs.store.return_value = "file-doc"
                fs_instance.return_value = fs
                task.execute(ff)

            assert captured["message"] == "Please review"
            assert "data_base64" not in captured["message"]
            assert captured["attachments"] == [{
                "filename": "report.pdf",
                "mime_type": "application/pdf",
                "data": payload["data_base64"],
                "file_id": "file-doc",
                "url": "/files/file-doc/report.pdf",
            }]
            fs.store.assert_called_once()
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_does_not_mirror_dispatch_command_to_conversation(self):
        """Slash commands are client-internal: they must never appear in the
        shared conversation (webchat, VS Code, PawCode)."""
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            recorded = {}

            class RuntimeTask:
                def execute(self, flowfile):
                    recorded["runtime_body"] = json.loads(
                        flowfile.get_content().decode("utf-8"))
                    flowfile.set_content(json.dumps({"help": "Available commands"}).encode("utf-8"))
                    return [flowfile]

            class Writer:
                def enqueue_message(self, msg, **kwargs):
                    recorded["mirrored"] = msg

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=b"/help@SomeBot")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m-help")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("core.agent_runtime_ports.resolve_agent_runtime_task", return_value=RuntimeTask()), \
                    patch("core.conversation_writer.ConversationWriter.for_conversation", return_value=Writer()):
                out = task.execute(ff)

            assert b"Available commands" in out[0].get_content()
            assert recorded["runtime_body"]["text"] == "/help"
            assert "mirrored" not in recorded
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_dispatch_command_falls_back_to_live_instance(self):
        """Without agent_runtime_port, commands use AgentLoopTask._live_instance
        — the same fallback as regular messages."""
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from tasks.ai.agent_loop import AgentLoopTask
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            class RuntimeTask:
                def execute(self, flowfile):
                    flowfile.set_content(json.dumps({"help": "Available commands"}).encode("utf-8"))
                    return [flowfile]

            class Writer:
                def enqueue_message(self, msg, **kwargs):
                    pass

            task = TelegramAgentClientTask({})
            ff = FlowFile(content=b"/help")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m-help")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch.object(AgentLoopTask, "_live_instance", RuntimeTask()), \
                    patch("core.conversation_writer.ConversationWriter.for_conversation", return_value=Writer()):
                out = task.execute(ff)

            assert b"Available commands" in out[0].get_content()
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_command_failure_replies_instead_of_silence(self):
        """A command-handling failure must answer the user, not drop the
        flowfile through an unhandled exception."""
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from tasks.ai.agent_loop import AgentLoopTask
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            task = TelegramAgentClientTask({})
            ff = FlowFile(content=b"/help")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m-help")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch.object(AgentLoopTask, "_live_instance", None):
                out = task.execute(ff)

            content = out[0].get_content().decode("utf-8")
            assert content.startswith("Command failed:")
            assert "No live AgentLoopTask" in content
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_does_not_mirror_telegram_local_commands(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.conversation_store import ConversationStore
        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")
            store = ConversationStore.instance()
            store.save("conv1", [], user_id="alice")

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=b"/conv@SomeBot list")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m-conv-list")

            with patch("core.conversation_writer.ConversationWriter.for_conversation") as writer:
                out = task.execute(ff)

            assert b"Conversations:" in out[0].get_content()
            writer.assert_not_called()
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_selected_agent_lookup_can_avoid_persisting_default(self):
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        store = MagicMock()
        store.get_extra.return_value = {}

        with patch("core.conversation_store.ConversationStore.instance", return_value=store), \
                patch("core.conv_agent_config.get_all_agent_configs", return_value={"assistant": {}}):
            selected = TelegramAgentClientTask._selected_agent_for_conversation(
                "conv1", persist_default=False)

        assert selected == "assistant"
        store.set_extra.assert_not_called()

    def test_telegram_command_paths_use_passive_agent_lookup(self):
        src = Path("tasks/io/telegram_agent_client.py").read_text(encoding="utf-8")
        assert "conversation_id, persist_default=False" in src

    def test_agent_client_does_not_attach_tts_audio_to_wait_for_done_text(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from core.agent_runtime_api import AgentFinalResult
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=b"hello")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m1")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("tasks.io._telegram_voice._telegram_tts_enabled", return_value=True), \
                    patch("tasks.io._telegram_bridge._attach_telegram_tts_audio") as attach_audio, \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", return_value=type("Submission", (), {
                        "conversation_id": "conv1",
                        "turn_id": "telegram:111111:m1",
                        "wait_for_done": True,
                        "status": "accepted",
                    })()), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.wait_for_done", return_value=AgentFinalResult("conv1", "telegram:111111:m1", response="final text")):
                out = task.execute(ff)

            assert out[0].get_content() == b"final text"
            attach_audio.assert_not_called()
            assert not out[0].get_attribute("telegram.tts_audio_base64")
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_does_not_wait_for_preempt_ack(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from core.agent_runtime_api import AgentSubmission
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=b"interrupt")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m2")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", return_value=AgentSubmission(
                        "accepted", "conv1", "telegram:111111:m2", wait_for_done=False)), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.wait_for_done") as wait:
                out = task.execute(ff)

            assert out == []
            wait.assert_not_called()
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_returns_no_reply_when_wait_times_out(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from core.agent_runtime_api import AgentSubmission
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=b"long request")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m3")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", return_value=AgentSubmission(
                        "accepted", "conv1", "telegram:111111:m3", wait_for_done=True)), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.wait_for_done", return_value=None):
                out = task.execute(ff)

            assert out == []
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_does_not_register_live_callback_for_telegram_turn(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.identity_service import IdentityService
        from core.agent_runtime_api import AgentFinalResult
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")
            captured = {}

            def submit(req):
                captured["live_callback"] = req.live_callback
                return type("Submission", (), {
                    "conversation_id": "conv1",
                    "turn_id": "telegram:111111:m1",
                    "wait_for_done": True,
                    "status": "accepted",
                })()

            task = TelegramAgentClientTask({
                "agent_runtime_port": "pawflow_agent.agent_runtime_in",
                "service_id": "telegram_bot",
            })
            ff = FlowFile(content=b"ping")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m1")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", side_effect=submit), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.wait_for_done", return_value=AgentFinalResult("conv1", "telegram:111111:m1", response="ok")):
                task.execute(ff)

            assert captured["live_callback"] is None
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_streaming_user_message_event_includes_attachments(self):
        src = Path("tasks/ai/agent_streaming.py").read_text(encoding="utf-8")
        assert '"attachments": _attachments_body' in src

    def test_conversation_bridge_formats_user_attachments(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})

        text = task._format_event("new_message", {
            "role": "user",
            "content": "look",
            "source": {"name": "alice"},
            "attachments": [{"filename": "image.png", "mime_type": "image/png"}],
        })

        assert text == "⬜ <b>alice</b>\nlook [attachments: 1 image attachment]"

    def test_conversation_bridge_forwards_assistant_messages_live(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "new_message", {
                "role": "assistant",
                "content": "Je cherche les occurrences exactes.",
                "msg_id": "a1",
                "source": {"name": "assistant"},
            })

        task._send.assert_called_once_with(
            "alice", "chat-1", "🟩 <b>assistant</b>\n<blockquote>Je cherche les occurrences exactes.</blockquote>")

    def test_conversation_bridge_suppresses_duplicate_final_message(self):
        """The CCI tmux-capture re-publishes the final assistant message with a
        FRESH msg_id, racing the live coordinator. The msg_id dedup can't catch
        it (ids differ); the content backstop must, so Telegram gets it once."""
        import tasks.io.telegram_agent_client as tac
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        tac._TELEGRAM_SENT_ASSISTANT_CONTENT.clear()
        tac._TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS.clear()
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock(return_value=True)

        final = "Voici l'évaluation complète du correctif."
        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "new_message", {
                "role": "assistant", "content": final,
                "msg_id": "live-1", "source": {"name": "assistant"}})
            task._on_event("conv1", "new_message", {
                "role": "assistant", "content": final,
                "msg_id": "tmux-2", "source": {"name": "assistant"}})

        assert task._send.call_count == 1

    def test_conversation_bridge_distinct_finals_both_sent(self):
        """The backstop must not over-suppress: two genuinely different final
        messages in the same conversation are both forwarded."""
        import tasks.io.telegram_agent_client as tac
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        tac._TELEGRAM_SENT_ASSISTANT_CONTENT.clear()
        tac._TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS.clear()
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock(return_value=True)

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "new_message", {
                "role": "assistant", "content": "Premier message.",
                "msg_id": "m1", "source": {"name": "assistant"}})
            task._on_event("conv1", "new_message", {
                "role": "assistant", "content": "Second message, différent.",
                "msg_id": "m2", "source": {"name": "assistant"}})

        assert task._send.call_count == 2

    def test_conversation_bridge_renders_markdown_fences_as_telegram_code_blocks(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})

        text = task._format_event("new_message", {
            "role": "assistant",
            "content": "Use this:\n```text\ncopy me <ok>\n```\nThen:\n```python\nprint('ok')\n```",
            "source": {"name": "assistant"},
        })

        assert "```" not in text
        assert text == (
            "🟩 <b>assistant</b>\n"
            "Use this:\n<pre><code>copy me &lt;ok&gt;</code></pre>\n"
            "Then:\n<pre><code>print(&#x27;ok&#x27;)</code></pre>"
        )

    def test_conversation_bridge_forwards_user_attachment_media(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock(return_value=True)
        task._send_media = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]), \
                patch("tasks.io._telegram_bridge._load_filestore_media", return_value=("image.png", b"png", "image/png")) as load:
            task._on_event("conv1", "new_message", {
                "role": "user",
                "content": "look",
                "source": {"name": "alice"},
                "attachments": [{"filename": "image.png", "mime_type": "image/png", "file_id": "fid1"}],
            })

        load.assert_called_once_with("fid1", "alice")
        task._send_media.assert_called_once_with("alice", "chat-1", b"png", "image.png", "image/png")

    def test_conversation_bridge_loads_webchat_attachment_with_event_user(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock(return_value=True)
        task._send_media = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("telegram-user", "telegram:1725865697")]), \
                patch("tasks.io._telegram_bridge._load_filestore_media", return_value=("image.png", b"png", "image/png")) as load:
            task._on_event("conv1", "new_message", {
                "role": "user",
                "content": "look",
                "source": {"type": "user", "name": "allcolor"},
                "attachments": [{"filename": "image.png", "mime_type": "image/png", "file_id": "fid1"}],
            })

        load.assert_called_once_with("fid1", "allcolor")
        task._send_media.assert_called_once_with(
            "telegram-user", "telegram:1725865697", b"png", "image.png", "image/png")

    def test_conversation_bridge_sends_attachment_media_to_api_chat_id(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        svc = MagicMock()
        svc._initialized = True
        task.get_service = MagicMock(return_value=svc)

        task._send_media(
            "alice", "telegram:1725865697", b"png", "image.png", "image/png")

        svc.send_photo.assert_called_once_with(
            "1725865697", b"png", filename="image.png", content_type="image/png")

    def test_conversation_bridge_forwards_content_image_ref_media(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock(return_value=True)
        task._send_media = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "telegram:1725865697")]), \
                patch("tasks.io._telegram_bridge._load_filestore_media", return_value=("image.png", b"png", "image/png")) as load:
            task._on_event("conv1", "new_message", {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {"type": "image_ref", "file_id": "fid1", "filename": "image.png"},
                ],
                "source": {"name": "alice"},
            })

        load.assert_called_once_with("fid1", "alice")
        task._send_media.assert_called_once_with(
            "alice", "telegram:1725865697", b"png", "image.png", "image/png")

    def test_conversation_bridge_formats_agent_service_badge(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})

        text = task._format_event("new_message", {
            "role": "assistant",
            "content": "ok",
            "source": {
                "name": "assistant",
                "llm_service": "codex_appserver_llm_service",
            },
        })

        assert text == (
            "🟩 <b>assistant</b> <code>codex_appserver_llm_service</code>\n"
            "<blockquote>ok</blockquote>"
        )

    def test_conversation_bridge_skips_runtime_live_telegram_agent_events(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "new_message", {
                "role": "assistant",
                "content": "live",
                "source": {"name": "assistant", "channel": "telegram"},
            })
            task._on_event("conv1", "tool_call", {
                "agent_name": "assistant",
                "tool_name": "read",
                "source": {"channel": "telegram"},
            })

        task._send.assert_not_called()

    def test_conversation_bridge_does_not_echo_telegram_user_message_by_msg_id(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "new_message", {
                "role": "user",
                "content": "Voilà, j'ai restart en .16",
                "msg_id": "telegram:111111:42",
                "source": {"name": "allcolor"},
            })

        task._send.assert_not_called()

    def test_conversation_bridge_never_sends_done_as_chat_message(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask

        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]), \
                patch.object(TelegramConversationBridgeTask, "_send") as send:
            task._on_event("conv1", "done", {"response": "concaténé", "agent_name": "assistant"})

        send.assert_not_called()

    def test_agent_client_does_not_send_done_aggregate_after_live_assistant_message(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.agent_runtime_api import AgentFinalResult
        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import (
            TelegramAgentClientTask,
            _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS,
            _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS,
        )

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")
            _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS.add("conv1")
            _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS.add("conv1\x1ffinal1")

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=b"hello")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m1")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", return_value=type("Submission", (), {
                        "conversation_id": "conv1",
                        "turn_id": "telegram:111111:m1",
                        "wait_for_done": True,
                        "status": "accepted",
                    })()), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.wait_for_done", return_value=AgentFinalResult(
                        "conv1", "telegram:111111:m1",
                        response="intermediate text\nfinal text",
                        data={"msg_id": "final1"},
                    )):
                out = task.execute(ff)

            assert out[0].get_content() == b""
            assert "conv1" not in _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_sends_done_final_after_live_intermediate_assistant(self):
        import shutil
        import tempfile
        from unittest.mock import patch

        from core.agent_runtime_api import AgentFinalResult
        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import (
            TelegramAgentClientTask,
            _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS,
            _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS,
        )

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv1")
            _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS.add("conv1")
            _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS.add("conv1\x1fintermediate1")

            task = TelegramAgentClientTask({"agent_runtime_port": "pawflow_agent.agent_runtime_in"})
            ff = FlowFile(content=b"hello")
            ff.set_attribute("telegram.user_id", "111111")
            ff.set_attribute("telegram.chat_id", "111111")
            ff.set_attribute("telegram.message_id", "m1")

            with patch.object(TelegramAgentClientTask, "_selected_agent_for_conversation", return_value="assistant"), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.submit_message", return_value=type("Submission", (), {
                        "conversation_id": "conv1",
                        "turn_id": "telegram:111111:m1",
                        "wait_for_done": True,
                        "status": "accepted",
                    })()), \
                    patch("core.agent_runtime_api.AgentRuntimeAPI.wait_for_done", return_value=AgentFinalResult(
                        "conv1", "telegram:111111:m1",
                        response="final text",
                        data={"msg_id": "final1"},
                    )):
                out = task.execute(ff)

            assert out[0].get_content() == b"final text"
            assert "conv1" not in _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS
            assert "conv1\x1fintermediate1" not in _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            _TELEGRAM_LIVE_ASSISTANT_SENT_TURNS.discard("conv1")
            _TELEGRAM_LIVE_SENT_ASSISTANT_MSG_IDS.discard("conv1\x1fintermediate1")
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bridge_uses_linked_active_telegram_conversation(self):
        import shutil
        import tempfile

        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "telegram:1725865697")
            ids.set_active_conv("alice", "telegram", "conv1")

            subscribers = list(TelegramConversationBridgeTask._telegram_subscribers("conv1", {}))

            assert subscribers == [("alice", "telegram:1725865697")]
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bridge_requires_active_telegram_conversation(self):
        import shutil
        import tempfile

        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "telegram:1725865697")
            ids.set_active_conv("alice", "telegram", "other_conv")

            subscribers = list(TelegramConversationBridgeTask._telegram_subscribers("conv1", {}))

            assert subscribers == []
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tts_command_toggles_conversation_audio(self):
        import shutil
        import tempfile

        from core.conversation_store import ConversationStore
        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramAgentClientTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "111111")
            ids.set_active_conv("alice", "telegram", "conv_tts")
            store = ConversationStore.instance()
            store.save("conv_tts", [], user_id="alice")
            store.set_extra("conv_tts", "active_resources", {"agent": "assistant"})
            store.set_extra("conv_tts", "audio_services", {"assistant": "tts1"})

            task = TelegramAgentClientTask({})
            assert "enabled" in task._handle_command("/tts on", "alice", "111111")
            assert store.get_extra("conv_tts", "telegram_tts_enabled") is True
            assert "disabled" in task._handle_command("/tts off", "alice", "111111")
            assert store.get_extra("conv_tts", "telegram_tts_enabled") is False
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_agent_client_attaches_tts_audio_when_enabled(self):
        from tasks.io.telegram_agent_client import _attach_telegram_tts_audio

        class FakeTTS:
            def set_runtime_context(self, **kwargs):
                self.context = kwargs

            def speak(self, **kwargs):
                self.kwargs = kwargs
                return {"audio_bytes": b"speech", "content_type": "audio/mpeg"}

        ff = FlowFile(content=b"")
        store = MagicMock()
        store.get_extra.side_effect = lambda _cid, key: {
            "telegram_tts_enabled": True,
            "audio_services": {"assistant": "tts1"},
        }.get(key)
        registry = MagicMock()
        svc = FakeTTS()
        registry.resolve.return_value = svc

        with patch("core.conversation_store.ConversationStore.instance", return_value=store), \
                patch("core.service_registry.ServiceRegistry.get_instance", return_value=registry):
            _attach_telegram_tts_audio(ff, "hello", "alice", "conv1", "assistant")

        assert base64.b64decode(ff.get_attribute("telegram.tts_audio_base64")) == b"speech"
        assert ff.get_attribute("telegram.tts_content_type") == "audio/mpeg"
        registry.resolve.assert_called_once_with("tts1", user_id="alice", conv_id="conv1")

    def test_agent_client_no_longer_attaches_tts_from_wait_for_done_response(self):
        src = Path("tasks/io/telegram_agent_client.py").read_text(encoding="utf-8")
        assert "response_text or str(result.response or \"\")" not in src
        assert "flowfile, response_text," not in src

    def test_conversation_bridge_sends_tts_for_live_assistant_message(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()
        task._send_tts_audio = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "new_message", {
                "role": "assistant",
                "content": "message intermédiaire",
                "agent_name": "assistant",
                "msg_id": "mid1",
            })

        task._send.assert_called_once()
        task._send_tts_audio.assert_called_once()

    def test_conversation_bridge_drops_generic_thinking_heartbeat(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "thinking", {
                "agent_name": "assistant",
            })

        task._send.assert_not_called()

    def test_conversation_bridge_forwards_real_thinking_content(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()
        task._send_tool_media = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            # cci streams the reasoning live as thinking_delta fragments (a
            # transient preview); the durable thinking_content that follows is
            # the SAME reasoning, finalized. Telegram must show it ONCE — the
            # content supersedes the delta preview, it is not appended to it
            # (appending was the duplication bug).
            task._on_event("conv1", "thinking_delta", {"agent_name": "assistant", "text": "Found the "})
            task._on_event("conv1", "thinking_delta", {"agent_name": "assistant", "text": "bug"})
            task._on_event("conv1", "thinking_content", {"agent_name": "assistant", "text": "Found the bug in the parser"})
            task._send.assert_not_called()
            # The next non-thinking event flushes ONE consolidated block.
            task._on_event("conv1", "tool_result", {"agent_name": "assistant", "tool": "read"})

        assert [call.args[2] for call in task._send.call_args_list] == [
            "💭 <i>assistant thinking</i>\n<blockquote>Found the bug in the parser</blockquote>",
        ]

    def test_conversation_bridge_thinking_content_not_duplicated_after_delta_preview(self):
        """Regression: fragmented delta preview + the full durable block must
        collapse to ONE message, not preview-fragments + the whole thing
        again (the Telegram thinking-duplication bug)."""
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()
        task._send_tool_media = MagicMock()

        full = "Worker.py shrank by 111 lines. Let me run the test suite."
        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            # The provider streams the block in fragments...
            for frag in ("Worker", ".py shrank ", "by 111 lines. ", "Let me run ", "the test suite."):
                task._on_event("conv1", "thinking_delta", {"agent_name": "assistant", "text": frag})
            # ...then the durable, clean block.
            task._on_event("conv1", "thinking_content", {"agent_name": "assistant", "text": full})
            task._on_event("conv1", "done", {"agent_name": "assistant"})

        assert [call.args[2] for call in task._send.call_args_list] == [
            f"💭 <i>assistant thinking</i>\n<blockquote>{full}</blockquote>",
        ]

    def test_conversation_bridge_does_not_flush_delta_preview_before_tool_call(self):
        """Regression: CCI may emit thinking_delta fragments before a tool_call
        and only later publish the durable thinking_content. Telegram must not
        expose the transient fragment as a standalone broken sentence."""
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()
        task._send_tool_media = MagicMock()

        full = "I cannot access search results, so I am trying the website directly."
        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "thinking_delta", {"agent_name": "assistant", "text": "I website"})
            task._on_event("conv1", "tool_call", {"agent_name": "assistant", "tool": "fetch"})
            task._on_event("conv1", "thinking_content", {"agent_name": "assistant", "text": full})
            task._on_event("conv1", "done", {"agent_name": "assistant"})

        assert [call.args[2] for call in task._send.call_args_list] == [
            "🟩 <b>assistant</b>\n<blockquote>calling <code>fetch</code></blockquote>",
            f"💭 <i>assistant thinking</i>\n<blockquote>{full}</blockquote>",
        ]

    def test_conversation_bridge_flushes_delta_preview_when_no_content(self):
        """Fallback: if a thinking burst is streamed via deltas but never
        finalized with a thinking_content (e.g. cancelled turn), the preview
        is still flushed so the reasoning is not lost."""
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "thinking_delta", {"agent_name": "assistant", "text": "partial thought"})
            task._on_event("conv1", "done", {"agent_name": "assistant"})

        assert [call.args[2] for call in task._send.call_args_list] == [
            "💭 <i>assistant thinking</i>\n<blockquote>partial thought</blockquote>",
        ]

    def test_conversation_bridge_forwards_periodic_waiting_progress(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]), \
                patch("tasks.io._telegram_bridge.time.time", side_effect=[100.0, 105.0, 107.0]):
            task._on_event("conv1", "thinking", {"agent_name": "assistant", "waiting_seconds": 4})
            task._on_event("conv1", "thinking", {"agent_name": "assistant", "waiting_seconds": 10})
            task._on_event("conv1", "thinking", {"agent_name": "assistant", "waiting_seconds": 22})

        task._send.assert_not_called()

    def test_conversation_bridge_forwards_tool_progress_by_name(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()
        task._send_tool_media = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "tool_call", {"agent_name": "assistant", "tool": "read"})
            task._on_event("conv1", "tool_result", {"agent_name": "assistant", "tool": "read"})
            task._on_event("conv1", "tool_call", {"agent_name": "assistant", "tool": "grep"})
            task._on_event("conv1", "tool_result", {"agent_name": "assistant", "tool": "grep"})

        assert [call.args[2] for call in task._send.call_args_list] == [
            "🟩 <b>assistant</b>\n<blockquote>calling <code>read</code></blockquote>",
            "🟩 <b>assistant</b>\n<blockquote>calling <code>grep</code></blockquote>",
        ]
        assert task._send_tool_media.call_count == 2

    def test_conversation_bridge_unwraps_use_tool_progress_name(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        task._send = MagicMock()
        task._send_tool_media = MagicMock()

        with patch.object(TelegramConversationBridgeTask, "_telegram_subscribers", return_value=[("alice", "chat-1")]):
            task._on_event("conv1", "tool_call", {
                "agent_name": "assistant",
                "tool": "use_tool",
                "arguments": {"tool_name": "read", "arguments": {"path": "x"}},
            })
            task._on_event("conv1", "tool_result", {
                "agent_name": "assistant",
                "tool": "use_tool",
                "arguments": {"tool_name": "bash", "arguments": {"cmd": "git status"}},
            })

        assert [call.args[2] for call in task._send.call_args_list] == [
            "🟩 <b>assistant</b>\n<blockquote>calling <code>read</code></blockquote>",
        ]
        task._send_tool_media.assert_called_once()

    def test_conversation_bridge_does_not_restart_stopped_service(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        svc = MagicMock()
        svc._initialized = False
        task.get_service = MagicMock(return_value=svc)

        task._send("alice", "chat-1", "hello")

        svc.ensure_connected.assert_not_called()
        svc.send_message.assert_not_called()

    def test_conversation_bridge_sends_api_chat_id(self):
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
        task = TelegramConversationBridgeTask({"service_id": "telegram_bot"})
        svc = MagicMock()
        svc._initialized = True
        task.get_service = MagicMock(return_value=svc)

        assert task._send("alice", "telegram:1725865697", "hello") is True

        svc.send_message.assert_called_once_with(
            "1725865697", "hello", parse_mode="HTML")

    def test_telegram_receiver_cleanup_unregisters_pool_callback(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "telegram_bot"})
        svc = MagicMock()
        task.get_service = MagicMock(return_value=svc)
        pool = MagicMock()

        with patch("services.telegram_bot_service.TelegramBotPool.instance", return_value=pool):
            task._pool_registered = True
            task.cleanup()

        pool.unregister_callback.assert_called_once_with(task._on_update)

    def test_conversation_bridge_uses_active_telegram_link(self):
        import shutil
        import tempfile

        from core.identity_service import IdentityService
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask

        tmp = tempfile.mkdtemp()
        IdentityService.reset()
        import core.paths as _p
        orig_ucd = _p.USER_CONFIG_DIR
        _p.USER_CONFIG_DIR = Path(tmp) / "users"
        try:
            ids = IdentityService()
            IdentityService._instance = ids
            ids.link("alice", "telegram", "telegram:1725865697")
            ids.set_active_conv("alice", "telegram", "conv1")

            subscribers = list(TelegramConversationBridgeTask._telegram_subscribers("conv1"))

            assert subscribers == [("alice", "telegram:1725865697")]
        finally:
            IdentityService.reset()
            _p.USER_CONFIG_DIR = orig_ucd
            shutil.rmtree(tmp, ignore_errors=True)

    def test_conversation_bridge_requires_selected_telegram_conversation(self):
        from unittest.mock import patch
        from tasks.io.telegram_agent_client import TelegramConversationBridgeTask

        ids = MagicMock()
        ids.list_all.return_value = {"alice": {"telegram": "chat-1"}}
        ids.get_active_conv.return_value = ""
        store = MagicMock()
        store.get_extra.return_value = ""

        with patch("core.identity_service.IdentityService.instance", return_value=ids), \
                patch("core.conversation_store.ConversationStore.instance", return_value=store):
            subscribers = list(TelegramConversationBridgeTask._telegram_subscribers(
                "conv1", {"source": {"name": "web-user"}}))

        assert subscribers == []

        ids.get_active_conv.return_value = "conv1"
        with patch("core.identity_service.IdentityService.instance", return_value=ids), \
                patch("core.conversation_store.ConversationStore.instance", return_value=store):
            subscribers = list(TelegramConversationBridgeTask._telegram_subscribers(
                "conv1", {"source": {"name": "web-user"}}))

        assert subscribers == [("alice", "chat-1")]


# ── TelegramSendHandler ────────────────────────────────────────────


class TestTelegramSendHandler(unittest.TestCase):

    def test_handler_properties(self):
        from tasks.io.telegram_send import TelegramSendHandler
        h = TelegramSendHandler()
        assert h.name == "send_telegram"
        assert "chat_id" in h.parameters_schema["properties"]
        assert "text" in h.parameters_schema["properties"]

    def test_handler_no_service(self):
        from tasks.io.telegram_send import TelegramSendHandler
        h = TelegramSendHandler()
        result = h.execute({"chat_id": "123", "text": "hi"})
        assert "Error" in result

    def test_handler_missing_params(self):
        from tasks.io.telegram_send import TelegramSendHandler
        h = TelegramSendHandler()
        result = h.execute({})
        assert "Error" in result

    def test_handler_with_mock_service(self):
        from tasks.io.telegram_send import TelegramSendHandler
        h = TelegramSendHandler()
        mock_svc = MagicMock()
        mock_svc.send_message.return_value = {"message_id": 99}
        h.set_service(mock_svc)

        result = h.execute({"chat_id": "telegram:123", "text": "hello"})
        assert "99" in result
        mock_svc.send_message.assert_called_once_with("123", "hello")


# ── Flow structure ──────────────────────────────────────────────────


class TestTelegramFlow(unittest.TestCase):

    def test_flow_file_valid(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "telegram_agent" / "versions" / "1.0.0.json"
        assert path.exists()
        flow = json.loads(path.read_text(encoding="utf-8"))
        assert flow["id"] == "telegram-agent"
        assert flow["version"] == "1.0.0"

    def test_flow_has_required_services(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "telegram_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        assert "telegram_bot" in flow["services"]
        svc = flow["services"]["telegram_bot"]
        assert svc["type"] == "telegramBot"

    def test_flow_bot_token_is_sensitive(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "telegram_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        bot_token = flow["parameters"]["bot_token"]
        assert bot_token["sensitive"] is True
        assert bot_token["required"] is True

    def test_flow_has_required_tasks(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "telegram_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        tasks = flow["tasks"]
        assert "receive" in tasks
        assert tasks["receive"]["type"] == "telegramReceiver"
        assert "agent_client" in tasks
        assert tasks["agent_client"]["type"] == "telegramAgentClient"
        assert tasks["agent_client"]["max_instances"] == 20
        assert "send_reply" in tasks
        assert tasks["send_reply"]["type"] == "telegramSend"
        assert "conversation_bridge" in tasks
        assert tasks["conversation_bridge"]["type"] == "telegramConversationBridge"

    def test_flow_relations(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "telegram_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        rels = flow["relations"]
        sources = [c["source"] for c in rels]
        targets = [c["target"] for c in rels]
        assert "receive" in sources
        assert "agent_client" in sources
        assert "agent_client" in targets
        assert "send_reply" in targets

    def test_flow_parser_normalizes_relations_for_execution(self):
        from engine.parser import FlowParser
        from tasks import register_all_tasks

        register_all_tasks()
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "telegram_agent" / "versions" / "1.0.0.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        flow = FlowParser.parse(raw)

        assert {rel["from"] for rel in flow.relations} == {"receive", "agent_client"}
        assert {rel["to"] for rel in flow.relations} == {"agent_client", "send_reply"}
        assert all(rel["type"] == "success" for rel in flow.relations)
        assert flow.tasks["agent_client"]._max_instances == 20

    def test_flow_declares_agent_runtime_link(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "telegram_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        assert flow["parameters"]["agent_runtime_port"] == "pawflow_agent.agent_runtime_in"
        assert flow["tasks"]["agent_client"]["parameters"]["agent_runtime_port"] == "${agent_runtime_port}"
        assert flow["runtime_links"] == [{
            "from": "agent_client",
            "to": "${agent_runtime_port}",
            "type": "agentRuntime",
            "description": "Submit Telegram messages to the shared PawFlow agent runtime",
        }]

    def test_custom_bot_flow_file_valid(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "custom_bot" / "versions" / "1.0.0.json"
        assert path.exists()
        flow = json.loads(path.read_text(encoding="utf-8"))
        assert flow["id"] == "telegram-custom-bot"
        assert flow["fqn"] == "telegram.custom_bot:1.0.0"
        assert flow["package"] == "telegram"
        assert flow["tasks"]["receive"]["type"] == "telegramReceiver"
        assert flow["tasks"]["handle_command"]["type"] == "executeScript"
        assert flow["tasks"]["send_reply"]["type"] == "telegramSend"

    def test_custom_bot_flow_parser_normalizes_relations(self):
        from engine.parser import FlowParser
        from tasks import register_all_tasks

        register_all_tasks()
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "custom_bot" / "versions" / "1.0.0.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        flow = FlowParser.parse(raw)

        assert {rel["from"] for rel in flow.relations} == {"receive", "handle_command"}
        assert {rel["to"] for rel in flow.relations} == {"handle_command", "send_reply"}
        assert all(rel["type"] == "success" for rel in flow.relations)

    def test_custom_bot_script_checks_allowed_users_and_commands(self):
        from engine.parser import FlowParser
        from tasks import register_all_tasks

        register_all_tasks()
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "telegram" / "custom_bot" / "versions" / "1.0.0.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["parameters"]["allowed_users"] = "42"
        flow = FlowParser.parse(raw)
        task = flow.tasks["handle_command"]

        denied = FlowFile(content=b"/hello")
        denied.set_attribute("telegram.user_id", "100")
        [out] = task.execute(denied)
        assert out.get_content().decode("utf-8").startswith("Access denied.")

        allowed = FlowFile(content=b"/hello")
        allowed.set_attribute("telegram.user_id", "42")
        allowed.set_attribute("telegram.first_name", "Ada")
        [out] = task.execute(allowed)
        assert out.get_content() == b"Hello Ada."

        help_ff = FlowFile(content=b"/help")
        help_ff.set_attribute("telegram.user_id", "42")
        [out] = task.execute(help_ff)
        assert "/hello - Say hello" in out.get_content().decode("utf-8")
        assert json.loads(out.get_attribute("telegram.reply_markup"))["inline_keyboard"]

    def test_pawflow_agent_declares_agent_runtime_input_port(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        assert flow["ports"]["agent_runtime_in"] == {
            "type": "agentRuntime",
            "task": "agent",
            "direction": "input",
            "description": "Submit messages to the shared AgentLoop runtime",
        }


# ── i18n ────────────────────────────────────────────────────────────


def _make_bridge():
    from tasks.io.telegram_agent_client import TelegramConversationBridgeTask
    bridge = TelegramConversationBridgeTask({"service_id": "svc"})
    sends = []
    bridge._send = lambda uid, cid, text: (sends.append(text), True)[1]
    bridge._telegram_subscribers = lambda cid, data=None: [("u1", "c1")]
    bridge._send_tool_media = lambda *a, **k: None
    bridge._send_message_attachments = lambda *a, **k: None
    return bridge, sends


def test_telegram_bridge_coalesces_thinking_into_one_block():
    """Regression: CCI thinking arrives as many small blocks. Telegram must get
    ONE consolidated thinking message per burst, never the fragments ("bouts")
    nor a final duplicate."""
    bridge, sends = _make_bridge()
    conv = "conv1"

    # Three streamed thinking blocks — buffered, nothing sent yet.
    for part in ("First part.", "Second part.", "Third part."):
        bridge._on_event(conv, "thinking_content",
                         {"agent_name": "claude", "text": part})
    assert sends == [], "thinking fragments must not be sent individually"

    # A tool result closes the burst -> exactly one consolidated thinking block.
    bridge._on_event(conv, "tool_result", {"agent_name": "claude"})
    thinking_sends = [s for s in sends if "thinking" in s]
    assert len(thinking_sends) == 1, f"expected 1 consolidated block, got {sends!r}"
    block = thinking_sends[0]
    assert "First part." in block
    assert "Second part." in block
    assert "Third part." in block
    assert bridge._thinking_buf == {}, "buffer must be cleared after flush"


def test_telegram_bridge_thinking_merge_dedups_cumulative_snapshots():
    """Providers that re-send a growing snapshot must not produce repeated text
    in the consolidated block."""
    bridge, sends = _make_bridge()
    conv = "conv2"
    bridge._on_event(conv, "thinking_content", {"agent_name": "a", "text": "Step one"})
    bridge._on_event(conv, "thinking_content", {"agent_name": "a", "text": "Step one Step two"})
    bridge._on_event(conv, "tool_result", {"agent_name": "a"})
    block = [s for s in sends if "thinking" in s][0]
    assert block.count("Step one") == 1
    assert "Step two" in block


# ── Persistent send transport (regression: Telegram lag/burst) ─────

class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._b = body.encode("utf-8")

    def read(self):
        return self._b


def _patch_send_conn(script):
    """Return (state, FakeConn). script items: ('resp', status, body) or
    ('raise', exc). FakeConn pops one item per request()."""
    from collections import deque
    q = deque(script)
    state = {"created": 0, "closed": 0}

    class FakeConn:
        def __init__(self, *a, **k):
            state["created"] += 1
            self._pending = None

        def request(self, *a, **k):
            item = q.popleft()
            if item[0] == "raise":
                raise item[1]
            self._pending = item

        def getresponse(self):
            return _FakeResp(self._pending[1], self._pending[2])

        def close(self):
            state["closed"] += 1

    return state, FakeConn


class TestTelegramPersistentSend(unittest.TestCase):
    def setUp(self):
        import services.telegram_bot_service as tg
        self.tg = tg
        tg._reset_send_channels()

    def tearDown(self):
        self.tg._reset_send_channels()

    def test_keep_alive_reuses_one_connection(self):
        ok = '{"ok": true, "result": {"message_id": 1}}'
        state, FakeConn = _patch_send_conn(
            [("resp", 200, ok), ("resp", 200, ok), ("resp", 200, ok)])
        with patch.object(self.tg.http.client, "HTTPSConnection", FakeConn):
            for _ in range(3):
                self.tg._send_api_call("tok", "sendMessage", {"chat_id": "1"})
        # One TLS connection reused for all three sends.
        self.assertEqual(state["created"], 1)

    def test_429_retry_after_then_success(self):
        state, FakeConn = _patch_send_conn([
            ("resp", 429, '{"ok": false, "parameters": {"retry_after": 2}}'),
            ("resp", 200, '{"ok": true, "result": {"message_id": 5}}'),
        ])
        slept = []
        with patch.object(self.tg.http.client, "HTTPSConnection", FakeConn), \
                patch.object(self.tg.time, "sleep", slept.append):
            result = self.tg._send_api_call("tok", "sendMessage", {"chat_id": "1"})
        self.assertEqual(result, {"message_id": 5})
        # retry_after honoured. Membership (not equality): the patched global
        # time.sleep can also catch unrelated background-thread sleeps when the
        # whole suite runs.
        self.assertIn(2.0, slept)
        # 429 keeps the connection (response fully read) — no reconnect.
        self.assertEqual(state["created"], 1)

    def test_broken_socket_reconnects(self):
        state, FakeConn = _patch_send_conn([
            ("raise", OSError("broken pipe")),
            ("resp", 200, '{"ok": true, "result": {"message_id": 7}}'),
        ])
        with patch.object(self.tg.http.client, "HTTPSConnection", FakeConn):
            result = self.tg._send_api_call("tok", "sendMessage", {"chat_id": "1"})
        self.assertEqual(result, {"message_id": 7})
        # Reconnected after the broken socket was dropped.
        self.assertEqual(state["created"], 2)

