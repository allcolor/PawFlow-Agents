"""Tests for Discord integration: service, tasks, handler, flow, i18n."""

import json
import os
import queue
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# 1. TestDiscordBotService
# ---------------------------------------------------------------------------

import core.paths as _paths


class TestDiscordBotService:
    """Tests for DiscordBotService configuration and setup."""

    def _make_service(self, config=None):
        from services.discord_bot_service import DiscordBotService
        cfg = config or {"bot_token": "test-token-123"}
        return DiscordBotService(cfg)

    def test_config_parsing_bot_token(self):
        svc = self._make_service({"bot_token": "abc123"})
        assert svc.config.get("bot_token") == "abc123"

    def test_guild_ids_parsed(self):
        svc = self._make_service({
            "bot_token": "tok",
            "guild_ids": "111,222,333",
        })
        assert svc._guild_ids == {"111", "222", "333"}

    def test_allowed_channels_parsed(self):
        svc = self._make_service({
            "bot_token": "tok",
            "allowed_channels": "100,200",
        })
        assert svc._allowed_channels == {"100", "200"}

    def test_type_is_discord_bot(self):
        from services.discord_bot_service import DiscordBotService
        assert DiscordBotService.TYPE == "discordBot"

    def test_channel_name(self):
        from services.discord_bot_service import DiscordBotService
        assert DiscordBotService.CHANNEL_NAME == "discord"

    def test_missing_bot_token_raises(self):
        from services.discord_bot_service import DiscordBotService
        svc = DiscordBotService({})
        with pytest.raises(ValueError):
            svc._create_connection()

    def test_graceful_import_error(self):
        """If discord.py is not installed, _create_connection raises ImportError."""
        svc = self._make_service({"bot_token": "tok"})
        with patch.dict("sys.modules", {"discord": None}):
            with pytest.raises((ImportError, RuntimeError)):
                svc._create_connection()


# ---------------------------------------------------------------------------
# 2. TestDiscordReceiverTask
# ---------------------------------------------------------------------------

class TestDiscordReceiverTask:
    """Tests for discordReceiver task."""

    def _make_task(self, config=None):
        from tasks.io.discord_receiver import DiscordReceiverTask
        cfg = config or {"service_id": "discord1"}
        return DiscordReceiverTask(cfg)

    def test_type(self):
        from tasks.io.discord_receiver import DiscordReceiverTask
        assert DiscordReceiverTask.TYPE == "discordReceiver"

    def test_is_persistent_source(self):
        task = self._make_task()
        assert task.is_persistent_source is True

    def test_has_pending_input_false_when_empty(self):
        task = self._make_task()
        assert task.has_pending_input() is False

    def test_parse_update_creates_flowfile(self):
        from core import FlowFile
        task = self._make_task()
        update = {
            "content": "hello world",
            "channel_id": "ch1",
            "user_id": "u1",
            "username": "alice",
            "guild_id": "g1",
            "message_id": "m1",
            "message_type": "text",
        }
        ff = task._parse_update(update)
        assert ff is not None
        assert ff.get_attribute("discord.channel_id") == "ch1"
        assert ff.get_attribute("discord.user_id") == "u1"
        assert ff.get_attribute("discord.username") == "alice"
        assert ff.get_attribute("discord.guild_id") == "g1"
        assert ff.get_attribute("discord.message_id") == "m1"
        assert ff.get_attribute("discord.message_type") == "text"

    def test_parse_update_empty_content_returns_none(self):
        task = self._make_task()
        update = {"content": "", "channel_id": "ch1", "user_id": "u1",
                  "username": "bob", "guild_id": "g1", "message_id": "m2",
                  "message_type": "text"}
        result = task._parse_update(update)
        assert result is None

    def test_on_update_enqueues(self):
        task = self._make_task()
        update = {
            "content": "ping",
            "channel_id": "ch1",
            "user_id": "u1",
            "username": "carol",
            "guild_id": "g1",
            "message_id": "m3",
            "message_type": "text",
        }
        task._on_update(update)
        assert task.has_pending_input() is True

    def test_execute_returns_flowfile_from_queue(self):
        from core import FlowFile
        task = self._make_task()
        update = {
            "content": "test message",
            "channel_id": "ch1",
            "user_id": "u1",
            "username": "dave",
            "guild_id": "g1",
            "message_id": "m4",
            "message_type": "text",
        }
        task._on_update(update)
        task._registered = True  # skip initialize
        ff = FlowFile(content=b"ignored")
        result = task.execute(ff)
        if isinstance(result, list):
            assert len(result) >= 1
            out = result[0]
        else:
            out = result
        assert out.get_attribute("discord.channel_id") == "ch1"

    def test_execute_returns_empty_when_queue_empty(self):
        from core import FlowFile
        task = self._make_task()
        task._registered = True  # skip initialize
        ff = FlowFile(content=b"")
        result = task.execute(ff)
        assert result is None or result == [] or result == ()

    def test_flowfile_attributes_complete(self):
        task = self._make_task()
        update = {
            "content": "attr check",
            "channel_id": "c100",
            "user_id": "u100",
            "username": "eve",
            "guild_id": "g100",
            "message_id": "m100",
            "message_type": "reply",
        }
        ff = task._parse_update(update)
        expected_attrs = [
            "discord.channel_id",
            "discord.user_id",
            "discord.username",
            "discord.guild_id",
            "discord.message_id",
            "discord.message_type",
        ]
        for attr in expected_attrs:
            assert ff.get_attribute(attr) is not None, f"Missing attribute: {attr}"


# ---------------------------------------------------------------------------
# 3. TestDiscordSendTask
# ---------------------------------------------------------------------------

class TestDiscordSendTask:
    """Tests for discordSend task."""

    def _make_task(self, config=None):
        from tasks.io.discord_send import DiscordSendTask
        cfg = config or {"service_id": "discord1", "channel_id": "ch1"}
        return DiscordSendTask(cfg)

    def test_type(self):
        from tasks.io.discord_send import DiscordSendTask
        assert DiscordSendTask.TYPE == "discordSend"

    def test_parameter_schema_has_service_id_and_channel_id(self):
        from tasks.io.discord_send import DiscordSendTask
        task = DiscordSendTask({"service_id": "s1"})
        schema = task.get_parameter_schema()
        assert "service_id" in schema
        assert "channel_id" in schema

    def test_execute_resolves_channel_id_expression(self):
        from core import FlowFile
        task = self._make_task({"service_id": "discord1", "channel_id": "${discord.channel_id}"})
        ff = FlowFile(content=b"reply text")
        ff.set_attribute("discord.channel_id", "resolved_ch")
        mock_svc = MagicMock()
        mock_svc.send_message.return_value = {"message_id": "123"}
        with patch.object(task, 'get_service', return_value=mock_svc):
            task.execute(ff)
        call_args = mock_svc.send_message.call_args
        assert call_args is not None
        assert "resolved_ch" in str(call_args)


# ---------------------------------------------------------------------------
# 4. TestDiscordSendHandler
# ---------------------------------------------------------------------------

class TestDiscordSendHandler:
    """Tests for send_discord agent tool handler."""

    def _make_handler(self, service=None):
        from core.tool_registry import ToolRegistry
        registry = ToolRegistry()
        from tasks.io.discord_send import DiscordSendHandler
        handler = DiscordSendHandler(service=service)
        return handler

    def test_name(self):
        from tasks.io.discord_send import DiscordSendHandler
        handler = DiscordSendHandler()
        assert handler.name == "send_discord"

    def test_parameter_schema(self):
        from tasks.io.discord_send import DiscordSendHandler
        handler = DiscordSendHandler()
        schema = handler.parameters_schema
        assert "channel_id" in str(schema)
        assert "text" in str(schema)

    def test_execute_without_service_returns_error(self):
        from tasks.io.discord_send import DiscordSendHandler
        handler = DiscordSendHandler()
        result = handler.execute({"channel_id": "ch1", "text": "hello"})
        assert "error" in result.lower() or "Error" in result

    def test_execute_with_mock_service_sends(self):
        from tasks.io.discord_send import DiscordSendHandler
        mock_svc = MagicMock()
        mock_svc.send_message.return_value = {"message_id": "123"}
        handler = DiscordSendHandler()
        handler.set_service(mock_svc)
        result = handler.execute({"channel_id": "ch1", "text": "hello"})
        mock_svc.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# 5. TestDiscordFlow
# ---------------------------------------------------------------------------

class TestDiscordFlow:
    """Tests for the discord_agent.json flow definition."""

    @pytest.fixture
    def flow_data(self):
        # Resolve through _paths so conftest's tmpdir-redirected REPOSITORY_DIR
        # is honored (conftest copies global defs into the tmp repo at session
        # setup). Hard-coded "../data/..." read from the real repo on disk.
        flow_path = _paths.REPOSITORY_DIR / "flows" / "global" / "default" / "discord_agent" / "versions" / "1.0.0.json"
        with open(flow_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_has_correct_service_type(self, flow_data):
        services = flow_data.get("services", {})
        types = [s.get("type") for s in services.values()]
        assert "discordBot" in types

    def test_has_correct_task_types(self, flow_data):
        tasks = flow_data.get("tasks", {})
        types = [t.get("type") for t in tasks.values()]
        assert "discordReceiver" in types
        assert "discordSend" in types

    def test_has_two_relations(self, flow_data):
        relations = flow_data.get("relations", [])
        assert len(relations) == 2


# ---------------------------------------------------------------------------
# 6. TestDiscordI18n
# ---------------------------------------------------------------------------
