"""Tests for WhatsApp integration: service, tasks, handler, flow, i18n."""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _webhook_body(text="Hello", sender="33612345678", msg_id="msg123",
                  msg_type="text", name="John", timestamp="1234567890"):
    return json.dumps({
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": sender,
                        "id": msg_id,
                        "type": msg_type,
                        "text": {"body": text},
                        "timestamp": timestamp,
                    }],
                    "contacts": [{
                        "wa_id": sender,
                        "profile": {"name": name},
                    }],
                }
            }]
        }]
    })


def _verify_request(token="my_token", challenge="challenge123"):
    return {
        "query_params": {
            "hub.mode": "subscribe",
            "hub.verify_token": token,
            "hub.challenge": challenge,
        }
    }


# ===========================================================================
# 1. TestWhatsAppService
# ===========================================================================

class TestWhatsAppService:
    """Tests for services.whatsapp_service.WhatsAppService."""

    def _make_service(self, **overrides):
        from services.whatsapp_service import WhatsAppService
        cfg = {
            "phone_number_id": "123456",
            "access_token": "EAAx...",
            "verify_token": "my_token",
        }
        cfg.update(overrides)
        return WhatsAppService(cfg)

    def test_type(self):
        from services.whatsapp_service import WhatsAppService
        assert WhatsAppService.TYPE == "whatsappCloud"

    def test_channel_name(self):
        from services.whatsapp_service import WhatsAppService
        assert WhatsAppService.CHANNEL_NAME == "whatsapp"

    def test_config_parsing(self):
        svc = self._make_service(api_version="v18.0")
        assert svc._phone_number_id == "123456"
        assert svc._access_token == "EAAx..."
        assert svc._verify_token == "my_token"
        assert svc._api_version == "v18.0"

    def test_default_api_version(self):
        svc = self._make_service()
        assert svc._api_version == "v21.0"

    def test_missing_phone_number_id_raises(self):
        from services.whatsapp_service import WhatsAppService
        svc = WhatsAppService({"access_token": "tok", "verify_token": "vt"})
        with pytest.raises(ValueError):
            svc._create_connection()

    def test_missing_access_token_raises(self):
        from services.whatsapp_service import WhatsAppService
        svc = WhatsAppService({"phone_number_id": "123", "verify_token": "vt"})
        with pytest.raises(ValueError):
            svc._create_connection()

    def test_handle_verify_correct_token(self):
        svc = self._make_service()
        result = svc._handle_verify(_verify_request("my_token", "challenge123"))
        assert result["status"] == 200
        assert result["body"] == "challenge123"

    def test_handle_verify_wrong_token(self):
        svc = self._make_service()
        result = svc._handle_verify(_verify_request("wrong_token", "challenge123"))
        assert result["status"] == 403

    def test_handle_webhook_dispatches_via_callback(self):
        svc = self._make_service()
        received = []
        svc.register_handler("test_owner", lambda u: received.append(u))
        request = {"body": _webhook_body("Hi there")}
        result = svc._handle_webhook(request)
        assert result["status"] == 200
        assert len(received) == 1
        assert received[0]["phone"] == "33612345678"
        assert received[0]["content"] == "Hi there"

    def test_handle_webhook_parses_contact_name(self):
        svc = self._make_service()
        received = []
        svc.register_handler("test_owner", lambda u: received.append(u))
        request = {"body": _webhook_body("test", name="Alice")}
        svc._handle_webhook(request)
        assert received[0]["name"] == "Alice"

    def test_rate_limit_no_crash(self):
        svc = self._make_service()
        if hasattr(svc, "_check_rate_limit"):
            svc._check_rate_limit()  # should not raise

    @patch("http.client.HTTPSConnection")
    def test_send_message_mock_http(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"messages":[{"id":"mid1"}]}'
        mock_conn.getresponse.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        svc = self._make_service()
        result = svc.send_message("33699999999", "Test message")
        assert result is not None
        assert result.get("message_id") == "mid1"


# ===========================================================================
# 2. TestWhatsAppReceiverTask
# ===========================================================================

class TestWhatsAppReceiverTask:
    """Tests for tasks.io.whatsapp_receiver.WhatsAppReceiverTask."""

    def _make_task(self, **overrides):
        from tasks.io.whatsapp_receiver import WhatsAppReceiverTask
        cfg = {"service_id": "wa-svc-1"}
        cfg.update(overrides)
        return WhatsAppReceiverTask(cfg)

    def test_type(self):
        from tasks.io.whatsapp_receiver import WhatsAppReceiverTask
        assert WhatsAppReceiverTask.TYPE == "whatsappReceiver"

    def test_is_persistent_source(self):
        task = self._make_task()
        assert task.is_persistent_source is True

    def test_parse_update_creates_flowfile(self):
        task = self._make_task()
        update = {
            "phone": "33612345678",
            "message_id": "msg123",
            "message_type": "text",
            "content": "Hello world",
            "name": "John",
        }
        ff = task._parse_update(update)
        assert ff is not None
        assert ff.get_attribute("whatsapp.phone") == "33612345678"
        assert ff.get_attribute("whatsapp.name") == "John"
        assert ff.get_attribute("whatsapp.message_id") == "msg123"
        assert ff.get_attribute("whatsapp.message_type") == "text"

    def test_parse_update_empty_returns_none(self):
        task = self._make_task()
        result = task._parse_update({})
        assert result is None

    def test_parse_update_empty_content_returns_none(self):
        task = self._make_task()
        result = task._parse_update({"content": "", "phone": "123"})
        assert result is None

    def test_flowfile_content(self):
        task = self._make_task()
        update = {
            "phone": "33600000000",
            "message_id": "mid456",
            "message_type": "text",
            "content": "Test content",
            "name": "Alice",
        }
        ff = task._parse_update(update)
        assert ff is not None
        assert ff.get_content() == b"Test content"

    def test_flowfile_attributes_present(self):
        task = self._make_task()
        update = {
            "phone": "33600000000",
            "message_id": "mid456",
            "message_type": "image",
            "content": "(image)",
            "name": "Alice",
        }
        ff = task._parse_update(update)
        assert ff is not None
        attrs = ff.attributes
        assert "whatsapp.phone" in attrs
        assert "whatsapp.name" in attrs
        assert "whatsapp.message_id" in attrs
        assert "whatsapp.message_type" in attrs

    def test_on_update_enqueues(self):
        task = self._make_task()
        update = {
            "phone": "33612345678",
            "message_id": "m1",
            "message_type": "text",
            "content": "ping",
            "name": "Bob",
        }
        task._on_update(update)
        assert task.has_pending_input() is True

    def test_execute_returns_flowfile_from_queue(self):
        from core import FlowFile
        task = self._make_task()
        update = {
            "phone": "33612345678",
            "message_id": "m4",
            "message_type": "text",
            "content": "test message",
            "name": "Dave",
        }
        task._on_update(update)
        task._registered = True
        ff = FlowFile(content=b"ignored")
        result = task.execute(ff)
        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0].get_attribute("whatsapp.phone") == "33612345678"

    def test_execute_returns_empty_when_queue_empty(self):
        from core import FlowFile
        task = self._make_task()
        task._registered = True
        ff = FlowFile(content=b"")
        result = task.execute(ff)
        assert result == [] or result is None or result == ()


# ===========================================================================
# 3. TestWhatsAppSendTask
# ===========================================================================

class TestWhatsAppSendTask:
    """Tests for tasks.io.whatsapp_send.WhatsAppSendTask."""

    def test_type(self):
        from tasks.io.whatsapp_send import WhatsAppSendTask
        assert WhatsAppSendTask.TYPE == "whatsappSend"

    def test_parameter_schema_has_service_id(self):
        from tasks.io.whatsapp_send import WhatsAppSendTask
        task = WhatsAppSendTask({"service_id": "s1"})
        schema = task.get_parameter_schema()
        assert "service_id" in schema

    def test_parameter_schema_has_phone(self):
        from tasks.io.whatsapp_send import WhatsAppSendTask
        task = WhatsAppSendTask({"service_id": "s1"})
        schema = task.get_parameter_schema()
        assert "phone" in schema


# ===========================================================================
# 4. TestWhatsAppSendHandler
# ===========================================================================

class TestWhatsAppSendHandler:
    """Tests for the send_whatsapp agent tool handler."""

    def test_handler_name(self):
        from tasks.io.whatsapp_send import WhatsAppSendHandler
        handler = WhatsAppSendHandler()
        assert handler.name == "send_whatsapp"

    def test_parameters_schema(self):
        from tasks.io.whatsapp_send import WhatsAppSendHandler
        handler = WhatsAppSendHandler()
        schema = handler.parameters_schema
        assert "phone" in str(schema)
        assert "text" in str(schema)

    def test_execute_without_service_returns_error(self):
        from tasks.io.whatsapp_send import WhatsAppSendHandler
        handler = WhatsAppSendHandler()
        result = handler.execute({"phone": "33600000000", "text": "hi"})
        assert "error" in result.lower() or "Error" in result

    def test_execute_with_mock_service(self):
        from tasks.io.whatsapp_send import WhatsAppSendHandler
        mock_service = MagicMock()
        mock_service.send_message.return_value = {"message_id": "mid1"}
        handler = WhatsAppSendHandler()
        handler.set_service(mock_service)
        result = handler.execute({"phone": "33600000000", "text": "hello"})
        assert result is not None
        mock_service.send_message.assert_called_once()

    def test_execute_missing_params_returns_error(self):
        from tasks.io.whatsapp_send import WhatsAppSendHandler
        handler = WhatsAppSendHandler()
        result = handler.execute({"phone": "", "text": ""})
        assert "error" in result.lower() or "Error" in result


# ===========================================================================
# 5. TestWhatsAppFlow
# ===========================================================================

class TestWhatsAppFlow:
    """Tests for flows/whatsapp_agent.json structure."""

    @pytest.fixture
    def flow_data(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "flows", "whatsapp_agent.json")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_flow_loads(self, flow_data):
        assert flow_data is not None

    def test_flow_has_tasks(self, flow_data):
        tasks = flow_data.get("tasks", {})
        assert len(tasks) > 0

    def test_flow_has_connections(self, flow_data):
        conns = flow_data.get("connections", [])
        assert len(conns) > 0

    def test_flow_contains_whatsapp_receiver(self, flow_data):
        tasks = flow_data.get("tasks", {})
        types = [t.get("type") for t in tasks.values()]
        assert "whatsappReceiver" in types

    def test_flow_contains_whatsapp_send(self, flow_data):
        tasks = flow_data.get("tasks", {})
        types = [t.get("type") for t in tasks.values()]
        assert "whatsappSend" in types


# ===========================================================================
# 6. TestWhatsAppI18n
# ===========================================================================

class TestWhatsAppI18n:
    """Verify WhatsApp-related i18n keys exist in all locales."""

    LOCALES = ["en", "fr", "es"]
    EXPECTED_KEYS = [
        "task.whatsapp_receiver.name",
        "task.whatsapp_send.name",
        "service.whatsapp.name",
    ]

    @pytest.fixture
    def translations(self):
        import os
        result = {}
        i18n_dir = os.path.join(os.path.dirname(__file__), "..", "gui", "i18n")
        for locale in self.LOCALES:
            path = os.path.join(i18n_dir, f"{locale}.json")
            with open(path, "r", encoding="utf-8") as f:
                result[locale] = json.load(f)
        return result

    def test_en_has_whatsapp_keys(self, translations):
        data = translations["en"]
        for key in self.EXPECTED_KEYS:
            assert key in data, f"Missing i18n key '{key}' in en.json"

    def test_fr_has_whatsapp_keys(self, translations):
        data = translations["fr"]
        for key in self.EXPECTED_KEYS:
            assert key in data, f"Missing i18n key '{key}' in fr.json"

    def test_es_has_whatsapp_keys(self, translations):
        data = translations["es"]
        for key in self.EXPECTED_KEYS:
            assert key in data, f"Missing i18n key '{key}' in es.json"
