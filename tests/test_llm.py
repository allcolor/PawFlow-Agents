"""Tests for LLM Connection Service and InferLLM Task."""

import json
import pytest
from unittest.mock import patch, MagicMock
from http.client import HTTPResponse
from io import BytesIO

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile, TaskFactory, ServiceFactory
from services.llm_connection import LLMConnectionService, LLMMessage, LLMResponse


def _mock_http_response(data: dict, status: int = 200) -> MagicMock:
    """Create a mock HTTP response."""
    body = json.dumps(data).encode("utf-8")
    mock = MagicMock()
    mock.status = status
    mock.read.return_value = body
    return mock


# -- OpenAI response format --
OPENAI_RESPONSE = {
    "id": "chatcmpl-123",
    "model": "gpt-4o-mini",
    "choices": [{
        "message": {"role": "assistant", "content": "Hello! How can I help?"},
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
}

# -- Anthropic response format --
ANTHROPIC_RESPONSE = {
    "id": "msg_123",
    "model": "claude-sonnet-4-20250514",
    "content": [{"type": "text", "text": "Bonjour! Comment puis-je aider?"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 12, "output_tokens": 9},
}


class TestLLMConnectionService:

    def test_register(self):
        assert "llmConnection" in ServiceFactory.list_types()

    def test_openai_complete(self):
        svc = LLMConnectionService({
            "provider": "openai",
            "api_key": "test-key",
            "default_model": "gpt-4o-mini",
        })
        svc.connect()

        with patch.object(svc._client, "_http_post", return_value=OPENAI_RESPONSE):
            response = svc.complete(
                messages=[LLMMessage("user", "Hello", conversation_id="test_conv")],
                temperature=0.5,
                max_tokens=100,
            )

        assert response.content == "Hello! How can I help?"
        assert response.model == "gpt-4o-mini"
        assert response.tokens_in == 10
        assert response.tokens_out == 8
        assert response.finish_reason == "stop"
        assert response.duration_ms > 0

    def test_anthropic_complete(self):
        svc = LLMConnectionService({
            "provider": "anthropic",
            "api_key": "test-key",
            "default_model": "claude-sonnet-4-20250514",
        })
        svc.connect()

        with patch.object(svc._client, "_http_post", return_value=ANTHROPIC_RESPONSE):
            response = svc.complete(
                messages=[
                    LLMMessage("system", "You are helpful", conversation_id="test_conv"),
                    LLMMessage("user", "Bonjour", conversation_id="test_conv"),
                ],
            )

        assert response.content == "Bonjour! Comment puis-je aider?"
        assert response.tokens_in == 12
        assert response.tokens_out == 9
        assert response.finish_reason == "end_turn"

    def test_anthropic_system_message_handling(self):
        """Anthropic API separates system from messages."""
        svc = LLMConnectionService({
            "provider": "anthropic",
            "api_key": "test-key",
        })
        svc.connect()

        captured = {}

        def mock_post(path, body, headers):
            captured.update(body)
            return ANTHROPIC_RESPONSE

        with patch.object(svc._client, "_http_post", side_effect=mock_post):
            svc.complete(messages=[
                LLMMessage("system", "Be concise", conversation_id="test_conv"),
                LLMMessage("user", "Hi", conversation_id="test_conv"),
            ])

        # System should be a top-level field with cache_control, not in messages
        system = captured["system"]
        assert isinstance(system, list)
        assert system[0]["text"] == "Be concise"
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert len(captured["messages"]) == 1
        assert captured["messages"][0]["role"] == "user"

    def test_openai_json_mode(self):
        svc = LLMConnectionService({
            "provider": "openai",
            "api_key": "test-key",
        })
        svc.connect()

        captured = {}

        def mock_post(path, body, headers):
            captured.update(body)
            return OPENAI_RESPONSE

        with patch.object(svc._client, "_http_post", side_effect=mock_post):
            svc.complete(
                messages=[LLMMessage("user", "Give JSON", conversation_id="test_conv")],
                response_format="json",
            )

        assert captured["response_format"] == {"type": "json_object"}

    def test_missing_api_key_raises(self):
        svc = LLMConnectionService({"provider": "openai", "api_key": ""})
        with pytest.raises(Exception):
            svc.connect()

    def test_unknown_provider_raises(self):
        svc = LLMConnectionService({"provider": "gemini", "api_key": "key"})
        with pytest.raises(Exception):
            svc.connect()

    def test_parameter_schema(self):
        svc = LLMConnectionService({"provider": "openai", "api_key": "k"})
        schema = svc.get_parameter_schema()
        assert "provider" in schema
        assert "api_key" in schema
        assert schema["api_key"]["sensitive"] is True


class TestInferLLMTask:

    def test_register(self):
        assert "inferLLM" in TaskFactory.list_types()

    def test_basic_inference(self):
        task_class = TaskFactory.get("inferLLM")
        task = task_class({
            "provider": "openai",
            "api_key": "test-key",
            "model": "gpt-4o-mini",
            "system_prompt": "You are helpful.",
        })

        ff = FlowFile(content=b"What is Python?")

        with patch(
            "services.llm_connection.LLMConnectionService.complete",
            return_value=LLMResponse(
                content="Python is a programming language.",
                model="gpt-4o-mini",
                tokens_in=15,
                tokens_out=8,
                finish_reason="stop",
                duration_ms=250.0,
            ),
        ):
            results = task.execute(ff)

        assert len(results) == 1
        assert results[0].get_content() == b"Python is a programming language."
        assert results[0].get_attribute("llm.model") == "gpt-4o-mini"
        assert results[0].get_attribute("llm.tokens_in") == "15"
        assert results[0].get_attribute("llm.tokens_out") == "8"
        assert results[0].get_attribute("llm.duration_ms") == "250.0"

    def test_keep_original_mode(self):
        task_class = TaskFactory.get("inferLLM")
        task = task_class({
            "provider": "openai",
            "api_key": "test-key",
            "keep_original": True,
        })

        ff = FlowFile(content=b"Original content")

        with patch(
            "services.llm_connection.LLMConnectionService.complete",
            return_value=LLMResponse(content="LLM says hi"),
        ):
            results = task.execute(ff)

        # Original content preserved
        assert results[0].get_content() == b"Original content"
        # Response stored in attribute
        assert results[0].get_attribute("llm.response") == "LLM says hi"

    def test_output_attribute_mode(self):
        task_class = TaskFactory.get("inferLLM")
        task = task_class({
            "provider": "openai",
            "api_key": "test-key",
            "output_attribute": "summary",
        })

        ff = FlowFile(content=b"Long text to summarize")

        with patch(
            "services.llm_connection.LLMConnectionService.complete",
            return_value=LLMResponse(content="Short summary"),
        ):
            results = task.execute(ff)

        assert results[0].get_content() == b"Long text to summarize"
        assert results[0].get_attribute("summary") == "Short summary"

    def test_input_from_attribute(self):
        task_class = TaskFactory.get("inferLLM")
        task = task_class({
            "provider": "openai",
            "api_key": "test-key",
            "input_attribute": "question",
        })

        ff = FlowFile(content=b"ignored", attributes={"question": "What is 2+2?"})

        captured_messages = []

        def mock_complete(messages, **kwargs):
            captured_messages.extend(messages)
            return LLMResponse(content="4")

        with patch(
            "services.llm_connection.LLMConnectionService.complete",
            side_effect=mock_complete,
        ):
            task.execute(ff)

        assert any(m.content == "What is 2+2?" for m in captured_messages)

    def test_system_prompt_interpolation(self):
        task_class = TaskFactory.get("inferLLM")
        task = task_class({
            "provider": "openai",
            "api_key": "test-key",
            "system_prompt": "Translate to ${target_lang}",
        })

        ff = FlowFile(
            content=b"Hello world",
            attributes={"target_lang": "French"},
        )

        captured_messages = []

        def mock_complete(messages, **kwargs):
            captured_messages.extend(messages)
            return LLMResponse(content="Bonjour le monde")

        with patch(
            "services.llm_connection.LLMConnectionService.complete",
            side_effect=mock_complete,
        ):
            task.execute(ff)

        system_msg = [m for m in captured_messages if m.role == "system"][0]
        assert system_msg.content == "Translate to French"

    def test_parameter_schema(self):
        task_class = TaskFactory.get("inferLLM")
        task = task_class({"provider": "openai", "api_key": "k"})
        schema = task.get_parameter_schema()
        assert "provider" in schema
        assert "system_prompt" in schema
        assert "temperature" in schema
        assert "response_format" in schema


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
