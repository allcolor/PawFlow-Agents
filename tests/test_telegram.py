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

    def test_send_message_splits_long(self):
        """Messages > 4096 chars should be split."""
        from services.telegram_bot_service import TelegramBotService
        svc = TelegramBotService({"bot_token": "test"})
        call_count = 0
        original_api_call = svc._api_call

        def mock_api_call(method, params=None):
            nonlocal call_count
            call_count += 1
            return {"message_id": call_count}

        svc._api_call = mock_api_call
        result = svc.send_message("123", "x" * 8200)
        # Should split into 3 chunks: 4096 + 4096 + 8
        assert call_count == 3


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

    def test_non_message_update_ignored(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._on_update({"update_id": 5, "edited_message": {}})
        assert task.has_pending_input() is False

    def test_empty_queue_returns_empty(self):
        from tasks.io.telegram_receiver import TelegramReceiverTask
        task = TelegramReceiverTask({"service_id": "tg"})
        task._registered = True  # skip service lookup
        results = task.execute()
        assert results == []


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
        task = TelegramSendTask({"service_id": "tg"})
        schema = task.get_parameter_schema()
        assert "service_id" in schema
        assert "chat_id" in schema
        assert "parse_mode" in schema


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

        result = h.execute({"chat_id": "123", "text": "hello"})
        assert "99" in result
        mock_svc.send_message.assert_called_once_with("123", "hello")


# ── Flow structure ──────────────────────────────────────────────────


class TestTelegramFlow(unittest.TestCase):

    def test_flow_file_valid(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "telegram_agent" / "versions" / "1.0.0.json"
        assert path.exists()
        flow = json.loads(path.read_text(encoding="utf-8"))
        assert flow["id"] == "telegram-agent"
        assert flow["version"] == "1.0.0"

    def test_flow_has_required_services(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "telegram_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        assert "telegram_bot" in flow["services"]
        svc = flow["services"]["telegram_bot"]
        assert svc["type"] == "telegramBot"

    def test_flow_has_required_tasks(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "telegram_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        tasks = flow["tasks"]
        assert "receive" in tasks
        assert tasks["receive"]["type"] == "telegramReceiver"
        assert "agent" in tasks
        assert tasks["agent"]["type"] == "agentLoop"
        assert "send_reply" in tasks
        assert tasks["send_reply"]["type"] == "telegramSend"

    def test_flow_relations(self):
        path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "telegram_agent" / "versions" / "1.0.0.json"
        flow = json.loads(path.read_text(encoding="utf-8"))
        rels = flow["relations"]
        sources = [c["source"] for c in rels]
        targets = [c["target"] for c in rels]
        assert "receive" in sources
        assert "agent" in sources
        assert "agent" in targets
        assert "send_reply" in targets


# ── i18n ────────────────────────────────────────────────────────────

