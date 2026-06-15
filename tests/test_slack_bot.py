"""Tests for Slack bot service, receiver task, send task, and related components."""

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ---------------------------------------------------------------------------
# TestSlackBotService
# ---------------------------------------------------------------------------


class TestSlackBotService:
    """Tests for SlackBotService configuration and event processing."""

    def test_config_parsing(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({
            "bot_token": "xoxb-test",
            "app_token": "xapp-test",
            "signing_secret": "secret123",
            "mode": "socket",
        })
        assert svc.config.get("bot_token") == "xoxb-test"
        assert svc.config.get("app_token") == "xapp-test"
        assert svc.config.get("signing_secret") == "secret123"
        assert svc.config.get("mode") == "socket"

    def test_type(self):
        from services.slack_bot_service import SlackBotService
        assert SlackBotService.TYPE == "slackBot"

    def test_channel_name(self):
        from services.slack_bot_service import SlackBotService
        assert SlackBotService.CHANNEL_NAME == "slack"

    def test_missing_bot_token_raises(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({})
        with pytest.raises(ValueError):
            svc._create_connection()

    def test_graceful_import_error_no_slack_sdk(self):
        import sys
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        with patch.dict(sys.modules, {"slack_sdk": None, "slack_sdk.web": None}):
            with pytest.raises((ImportError, ModuleNotFoundError, RuntimeError)):
                svc._create_connection()

    def test_process_event_message_dispatches(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        svc._dispatch = MagicMock()
        event = {
            "type": "message",
            "user": "U123",
            "channel": "C456",
            "text": "hello",
            "ts": "1234.5678",
            "team": "T789",
        }
        svc._process_event(event)
        svc._dispatch.assert_called_once()

    def test_process_event_bot_message_skipped(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        svc._dispatch = MagicMock()
        event = {
            "type": "message",
            "user": "U123",
            "channel": "C456",
            "text": "bot says hi",
            "ts": "1234.5678",
            "team": "T789",
            "bot_id": "B999",
        }
        svc._process_event(event)
        svc._dispatch.assert_not_called()

    def test_process_event_own_user_id_skipped(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        svc._dispatch = MagicMock()
        svc._bot_user_id = "U123"
        event = {
            "type": "message",
            "user": "U123",
            "channel": "C456",
            "text": "echo",
            "ts": "1234.5678",
            "team": "T789",
        }
        svc._process_event(event)
        svc._dispatch.assert_not_called()

    def test_process_event_subtype_skipped(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        svc._dispatch = MagicMock()
        event = {
            "type": "message",
            "subtype": "channel_join",
            "user": "U123",
            "channel": "C456",
            "text": "joined",
            "ts": "1234.5678",
            "team": "T789",
        }
        svc._process_event(event)
        svc._dispatch.assert_not_called()

    def test_process_event_non_message_type_skipped(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        svc._dispatch = MagicMock()
        event = {
            "type": "reaction_added",
            "user": "U123",
            "item": {"channel": "C456"},
        }
        svc._process_event(event)
        svc._dispatch.assert_not_called()

    def test_handle_events_api_url_verification(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        request = {"body": json.dumps({"type": "url_verification", "challenge": "abc123"})}
        result = svc._handle_events_api(request)
        assert result is not None
        assert result["status"] == 200
        # body is JSON string containing the challenge
        body = json.loads(result["body"])
        assert body["challenge"] == "abc123"

    def test_handle_events_api_message_dispatches(self):
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        svc._process_event = MagicMock()
        event = {
            "type": "message",
            "user": "U123",
            "channel": "C456",
            "text": "hello",
            "ts": "1234.5678",
            "team": "T789",
        }
        request = {"body": json.dumps({"type": "event_callback", "event": event})}
        svc._handle_events_api(request)
        svc._process_event.assert_called_once_with(event)

    def test_dispatch_update_format(self):
        """_process_event converts raw Slack event into normalized update dict."""
        from services.slack_bot_service import SlackBotService
        svc = SlackBotService({"bot_token": "xoxb-test"})
        dispatched = []
        svc._dispatch = lambda u: dispatched.append(u)
        event = {
            "type": "message",
            "user": "U123",
            "channel": "C456",
            "text": "hello",
            "ts": "1234.5678",
            "team": "T789",
        }
        svc._process_event(event)
        assert len(dispatched) == 1
        update = dispatched[0]
        assert update["channel_id"] == "C456"
        assert update["user_id"] == "U123"
        assert update["content"] == "hello"
        assert update["message_id"] == "1234.5678"
        assert update["team_id"] == "T789"


# ---------------------------------------------------------------------------
# TestSlackReceiverTask
# ---------------------------------------------------------------------------

class TestSlackReceiverTask:
    """Tests for SlackReceiverTask."""

    def test_type(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        assert SlackReceiverTask.TYPE == "slackReceiver"

    def test_is_persistent_source(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        assert task.is_persistent_source is True

    def test_parse_update_creates_flowfile(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        from core import FlowFile
        task = SlackReceiverTask({"service_id": "slack1"})
        # _parse_update expects the normalized update dict (not raw Slack event)
        update = {
            "content": "hello world",
            "channel_id": "C456",
            "user_id": "U123",
            "username": "testuser",
            "team_id": "T789",
            "message_id": "1234.5678",
            "thread_ts": "",
        }
        ff = task._parse_update(update)
        assert isinstance(ff, FlowFile)

    def test_parse_update_channel_id_attr(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        update = {
            "content": "hello",
            "channel_id": "C456",
            "user_id": "U123",
            "username": "",
            "team_id": "T789",
            "message_id": "1234.5678",
            "thread_ts": "",
        }
        ff = task._parse_update(update)
        assert ff.get_attribute("slack.channel_id") == "C456"

    def test_parse_update_user_id_attr(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        update = {
            "content": "hello",
            "channel_id": "C456",
            "user_id": "U123",
            "username": "",
            "team_id": "T789",
            "message_id": "1234.5678",
            "thread_ts": "",
        }
        ff = task._parse_update(update)
        assert ff.get_attribute("slack.user_id") == "U123"

    def test_parse_update_username_attr(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        update = {
            "content": "hello",
            "channel_id": "C456",
            "user_id": "U123",
            "username": "alice",
            "team_id": "T789",
            "message_id": "1234.5678",
            "thread_ts": "",
        }
        ff = task._parse_update(update)
        assert ff.get_attribute("slack.username") == "alice"

    def test_parse_update_team_id_attr(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        update = {
            "content": "hello",
            "channel_id": "C456",
            "user_id": "U123",
            "username": "",
            "team_id": "T789",
            "message_id": "1234.5678",
            "thread_ts": "",
        }
        ff = task._parse_update(update)
        assert ff.get_attribute("slack.team_id") == "T789"

    def test_parse_update_message_id_attr(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        update = {
            "content": "hello",
            "channel_id": "C456",
            "user_id": "U123",
            "username": "",
            "team_id": "T789",
            "message_id": "1234.5678",
            "thread_ts": "",
        }
        ff = task._parse_update(update)
        assert ff.get_attribute("slack.message_id") == "1234.5678"

    def test_parse_update_thread_ts_attr(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        update = {
            "content": "reply",
            "channel_id": "C456",
            "user_id": "U123",
            "username": "",
            "team_id": "T789",
            "message_id": "1234.9999",
            "thread_ts": "1234.5678",
        }
        ff = task._parse_update(update)
        assert ff.get_attribute("slack.thread_ts") == "1234.5678"

    def test_parse_update_empty_content_returns_none(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        update = {"content": "", "channel_id": "C456", "user_id": "U1"}
        result = task._parse_update(update)
        assert result is None

    def test_on_update_enqueues(self):
        from tasks.io.slack_receiver import SlackReceiverTask
        task = SlackReceiverTask({"service_id": "slack1"})
        update = {
            "content": "ping",
            "channel_id": "C456",
            "user_id": "U123",
            "username": "",
            "team_id": "T789",
            "message_id": "1234.5678",
            "thread_ts": "",
        }
        task._on_update(update)
        assert task.has_pending_input() is True


# ---------------------------------------------------------------------------
# TestSlackSendTask
# ---------------------------------------------------------------------------

class TestSlackSendTask:
    """Tests for SlackSendTask (in notify_slack.py)."""

    def test_type(self):
        from tasks.io.notify_slack import SlackSendTask
        assert SlackSendTask.TYPE == "slackSend"

    def test_parameter_schema_has_service_id(self):
        from tasks.io.notify_slack import SlackSendTask
        task = SlackSendTask({"service_id": "s1"})
        schema = task.get_parameter_schema()
        assert "service_id" in schema

    def test_parameter_schema_has_channel_id(self):
        from tasks.io.notify_slack import SlackSendTask
        task = SlackSendTask({"service_id": "s1"})
        schema = task.get_parameter_schema()
        assert "channel_id" in schema


# ---------------------------------------------------------------------------
# TestSlackSendHandler
# ---------------------------------------------------------------------------

class TestSlackSendHandler:
    """Tests for SlackSendHandler agent tool handler."""

    def test_name(self):
        from tasks.io.notify_slack import SlackSendHandler
        handler = SlackSendHandler()
        assert handler.name == "send_slack"

    def test_execute_without_service_returns_error(self):
        from tasks.io.notify_slack import SlackSendHandler
        handler = SlackSendHandler()
        result = handler.execute({"channel_id": "C456", "text": "hi"})
        assert "error" in result.lower() or "Error" in result

    def test_execute_with_mock_service(self):
        from tasks.io.notify_slack import SlackSendHandler
        mock_svc = MagicMock()
        mock_svc.send_message.return_value = {"message_id": "123"}
        handler = SlackSendHandler()
        handler.set_service(mock_svc)
        result = handler.execute({"channel_id": "C456", "text": "hello"})
        mock_svc.send_message.assert_called_once()

    def test_parameters_schema(self):
        from tasks.io.notify_slack import SlackSendHandler
        handler = SlackSendHandler()
        schema = handler.parameters_schema
        assert "channel_id" in str(schema)
        assert "text" in str(schema)


# ---------------------------------------------------------------------------
# TestNotifySlackBackwardCompat
# ---------------------------------------------------------------------------

class TestNotifySlackBackwardCompat:
    """Tests for backward compatibility of NotifySlackTask."""

    def test_notify_slack_task_exists(self):
        from tasks.io.notify_slack import NotifySlackTask
        assert NotifySlackTask is not None

    def test_notify_slack_type(self):
        from tasks.io.notify_slack import NotifySlackTask
        assert NotifySlackTask.TYPE == "notifySlack"

    def test_both_types_registered(self):
        from core import TaskFactory
        # Verify both types are in the registry
        registry = TaskFactory._tasks
        assert "notifySlack" in registry
        assert "slackSend" in registry

