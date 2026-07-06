"""Tests for LLM Connection Service and InferLLM Task."""

import json
import time
import pytest
from unittest.mock import patch, MagicMock
from http.client import HTTPResponse
from io import BytesIO

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile, TaskFactory, ServiceFactory
from core.llm_client import LLMClient, LLMClientError
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


def test_transport_stream_disconnect_is_retryable_marker():
    err = (
        "codex app-server error: {'message': 'Reconnecting... 2/5', "
        "'codexErrorInfo': {'responseStreamDisconnected': {'httpStatusCode': None}}, "
        "'additionalDetails': 'stream disconnected before completion: "
        "websocket closed by server before response.completed'}"
    )

    assert LLMClient._is_transient_transport_error(err) is True


def test_non_transport_cli_exit_is_not_retryable_marker():
    err = "Claude CLI stream exited with code 1"

    assert LLMClient._is_transient_transport_error(err) is False


def test_complete_stream_call_identity_is_available_for_relay_url_resolution(monkeypatch):
    client = LLMClient(provider="openai", config={
        "api_key": "sk-test",
        "base_url": "http://relay1/localhost:11434/v1",
        "max_retries": 1,
    })
    captured = {}

    def fake_stream(self, messages, model, temperature, max_tokens, tools, callback, **kwargs):
        captured["user_id"] = getattr(self, "_user_id", "")
        captured["conversation_id"] = getattr(self, "_conversation_id", "")
        captured["agent_name"] = getattr(self, "_agent_name", "")
        return LLMResponse(content="ok", model=model, finish_reason="stop")

    monkeypatch.setattr(LLMClient, "_stream_openai", fake_stream)

    client.complete_stream(
        [LLMMessage("user", "hi", conversation_id="conv1")],
        call_user_id="alice",
        call_conversation_id="conv1",
        call_agent_name="assistant",
    )

    assert captured == {
        "user_id": "alice",
        "conversation_id": "conv1",
        "agent_name": "assistant",
    }


def test_openai_relay_stream_broken_pipe_falls_back_to_non_stream(monkeypatch):
    client = LLMClient(provider="openai", config={
        "api_key": "sk-test",
        "base_url": "http://127.0.0.1:9090/relay-proxy/relay1/tok/l/localhost:11434/v1",
        "default_model": "local-model",
        "max_retries": 1,
    })
    chunks = []

    def fail_stream(*args, **kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    def complete_fallback(*args, **kwargs):
        return LLMResponse(content="fallback ok", model="local-model", finish_reason="stop", tokens_out=3)

    monkeypatch.setattr(client, "_stream_openai", fail_stream)
    monkeypatch.setattr(client, "_complete_openai", complete_fallback)

    resp = client.complete_stream(
        [LLMMessage("user", "hi", conversation_id="conv1")],
        callback=chunks.append,
        call_user_id="alice",
        call_conversation_id="conv1",
    )

    assert resp.content == "fallback ok"
    assert chunks == ["fallback ok"]


def test_openai_stream_resolves_relay_base_url_once(monkeypatch):
    from services import http_listener_service as _hl_mod

    class _Listener:
        is_ssl = False
        public_hostname = ""

    class _Response:
        status = 200
        reason = "OK"

        def __init__(self):
            self._chunks = [b"data: [DONE]\n\n", b""]

        def read(self, _size):
            return self._chunks.pop(0)

    class _Connection:
        requests = []

        def __init__(self, host, port=None, timeout=None):
            self.host = host
            self.port = port
            self.timeout = timeout

        def request(self, method, path, body=None, headers=None):
            self.requests.append((method, path, body, headers))

        def getresponse(self):
            return _Response()

        def close(self):
            pass

    issued = []

    def issue_token(user_id, relay_id, conv_id=""):
        issued.append((user_id, relay_id, conv_id))
        return f"tok{len(issued)}"

    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", issue_token)
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")
    monkeypatch.setattr("core.llm_providers.openai.http.client.HTTPConnection", _Connection)

    client = LLMClient(provider="openai", config={
        "api_key": "sk-test",
        "base_url": "http://relay1/localhost:11434/v1",
        "relay_local": True,
        "default_model": "glm-5.2:cloud",
        "max_retries": 1,
    })

    resp = client.complete_stream(
        [LLMMessage("user", "ping", conversation_id="conv1")],
        max_tokens=10,
        call_user_id="allcolor",
        call_conversation_id="conv1",
    )

    assert resp.finish_reason == ""
    assert issued == [("allcolor", "relay1", "conv1")]
    assert "/relay-proxy/relay1/tok1/l/localhost:11434/v1/chat/completions" in _Connection.requests[0][1]


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

        with patch.object(LLMClient, "_http_post", return_value=OPENAI_RESPONSE):
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

        with patch.object(LLMClient, "_http_post", return_value=ANTHROPIC_RESPONSE):
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

        with patch.object(LLMClient, "_http_post", side_effect=mock_post):
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

        with patch.object(LLMClient, "_http_post", side_effect=mock_post):
            svc.complete(
                messages=[LLMMessage("user", "Give JSON", conversation_id="test_conv")],
                response_format="json",
            )

        assert captured["response_format"] == {"type": "json_object"}

    def test_openai_extra_body_is_merged_and_protected_keys_are_ignored(self):
        svc = LLMConnectionService({
            "provider": "openai",
            "api_key": "test-key",
            "base_url": "https://openrouter.ai/api/v1",
            "default_model": "qwen/qwen3-4b",
            "extra_body": {
                "provider": {
                    "order": ["Fireworks", "Together"],
                    "allow_fallbacks": False,
                },
                "transforms": ["middle-out"],
                "include_reasoning": True,
                "model": "must-not-override",
            },
        })
        svc.connect()

        captured = {}

        def mock_post(path, body, headers):
            captured["path"] = path
            captured.update(body)
            return OPENAI_RESPONSE

        with patch.object(LLMClient, "_http_post", side_effect=mock_post):
            svc.complete(messages=[LLMMessage("user", "Hi", conversation_id="test_conv")])

        assert captured["path"] == "/chat/completions"
        assert captured["model"] == "qwen/qwen3-4b"
        assert captured["provider"] == {
            "order": ["Fireworks", "Together"],
            "allow_fallbacks": False,
        }
        assert captured["transforms"] == ["middle-out"]
        assert captured["include_reasoning"] is True

    def test_openai_stream_relay_base_url_uses_call_identity(self, monkeypatch):
        from services import http_listener_service as _hl_mod

        class _Listener:
            is_ssl = False
            public_hostname = ""

        monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
        monkeypatch.setattr("core.relay_proxy_auth.issue_token",
                            lambda user_id, relay_id, conv_id="": f"tok-{user_id}-{relay_id}-{conv_id}")
        monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")
        monkeypatch.setattr("core.relay_bindings.get_default",
                            lambda cid, agent="": "relay1")

        svc = LLMConnectionService({
            "provider": "openai",
            "api_key": "test-key",
            "base_url": "relay://MyWorkspace/localhost:11434/v1",
            "relay_local": True,
            "default_model": "glm-5.2-cloud",
            "max_retries": 1,
        })
        svc.connect()
        captured = {}

        def fake_stream(client, messages, model, temperature, max_tokens, tools, callback, **kwargs):
            captured["base_url"] = client.base_url
            captured["user_id"] = getattr(client, "_user_id", "")
            captured["conversation_id"] = getattr(client, "_conversation_id", "")
            return LLMResponse(content="ok", model=model, finish_reason="stop")

        monkeypatch.setattr(LLMClient, "_stream_openai", fake_stream)

        resp = svc.complete_stream(
            messages=[LLMMessage("user", "Hello", conversation_id="conv1")],
            call_user_id="alice",
            call_conversation_id="conv1",
            call_agent_name="assistant",
        )

        assert resp.content == "ok"
        assert captured["user_id"] == "alice"
        assert captured["conversation_id"] == "conv1"
        assert captured["base_url"].startswith(
            "http://10.0.0.2:9090/relay-proxy/")
        assert "tok-alice-" in captured["base_url"]
        assert "-conv1/l/localhost:11434/v1" in captured["base_url"]

    def test_llm_service_rules_hide_cli_fields_for_api_providers(self):
        rules = LLMConnectionService({}).get_parameter_rules()

        api_rule = next(r for r in rules if r["when"] == {"provider": ["openai", "anthropic"]})
        assert api_rule["set"]["docker_image"]["visible"] is False
        assert api_rule["set"]["docker_cpu_limit"]["visible"] is False
        assert api_rule["set"]["docker_memory_limit"]["visible"] is False
        assert api_rule["set"]["effort"]["visible"] is False
        assert api_rule["set"]["extra_body"]["visible"] is False
        assert api_rule["set"]["relay_local"]["visible"] is True

        openai_rule = next(r for r in rules if r["when"] == {"provider": ["openai"]})
        assert openai_rule["set"]["extra_body"]["visible"] is True

    def test_claude_code_interactive_exposes_base_url_for_api_key_mode(self):
        rules = LLMConnectionService({}).get_parameter_rules()
        rule = next(r for r in rules if r["when"] == {"provider": ["claude-code-interactive"]})

        assert rule["set"]["api_key"]["visible"] is True
        assert rule["set"]["base_url"]["visible"] is True

    def test_cli_api_key_providers_expose_base_url_when_supported(self):
        rules = LLMConnectionService({}).get_parameter_rules()
        for provider in ("claude-code", "claude-code-interactive", "antigravity-interactive", "codex-app-server", "gemini"):
            rule = next(r for r in rules if r["when"] == {"provider": [provider]})
            assert rule["set"]["api_key"]["visible"] is True
            assert rule["set"]["base_url"]["visible"] is True

    def test_missing_api_key_raises(self):
        svc = LLMConnectionService({"provider": "openai", "api_key": ""})
        with pytest.raises(Exception):
            svc.connect()

    def test_unknown_provider_raises(self):
        """An unregistered provider name must blow up at connect() time.
        Use a name no provider could ever claim — 'gemini' used to be
        unknown, then we shipped a real gemini CLI provider, so the
        old probe value silently became valid."""
        svc = LLMConnectionService({
            "provider": "definitely-not-a-real-provider-xyz",
            "api_key": "key",
        })
        with pytest.raises(Exception):
            svc.connect()

    def test_parameter_schema(self):
        svc = LLMConnectionService({"provider": "openai", "api_key": "k"})
        schema = svc.get_parameter_schema()
        assert "provider" in schema
        assert "api_key" in schema
        assert schema["api_key"]["sensitive"] is True

    def test_timeout_default_means_no_timeout(self):
        svc = LLMConnectionService({"provider": "openai", "api_key": "k"})
        schema = svc.get_parameter_schema()

        assert schema["timeout"]["default"] == 0
        assert svc.timeout is None
        assert LLMClient(provider="openai", config={}).timeout is None
        assert LLMClient(provider="openai", config={"timeout": 0}).timeout is None
        assert LLMClient(provider="openai", config={"timeout": ""}).timeout is None
        assert LLMClient(provider="openai", config={"timeout": 37}).timeout == 37

    def test_permanent_request_errors_are_not_retryable(self):
        client = LLMClient(provider="openai", config={})
        assert client._is_permanent_request_error("HTTP 401 unauthorized") is True
        assert client._is_permanent_request_error("403 permission_denied") is True
        assert client._is_permanent_request_error("model_not_found") is True
        assert client._is_permanent_request_error("HTTP 429 rate_limit") is False
        assert client._is_permanent_request_error("HTTP 500 server_error") is False

    def test_global_circuit_breaker_opens_and_half_opens(self):
        client = LLMClient(provider="openai", config={
            "base_url": "https://example.invalid",
            "default_model": "m",
            "circuit_breaker_failures": 2,
            "circuit_breaker_cooldown": 1,
        })
        LLMClient._circuit_state.clear()

        client._circuit_after_failure("m", "HTTP 529 overloaded")
        client._circuit_before_call("m")
        client._circuit_after_failure("m", "HTTP 529 overloaded")
        with pytest.raises(LLMClientError, match="circuit open"):
            client._circuit_before_call("m")

        key = client._circuit_key("m")
        LLMClient._circuit_state[key]["open_until"] = time.time() - 1
        client._circuit_before_call("m")
        assert LLMClient._circuit_state[key]["half_open"] is True
        client._circuit_after_success("m")
        assert key not in LLMClient._circuit_state

    def test_default_models_are_loaded_from_system_config(self, monkeypatch, tmp_path):
        from core.llm_client import _load_default_models

        src = open("core/llm_client.py", encoding="utf-8").read()
        assert "DEFAULT_MODELS = {" not in src
        configured = {
            "openai": "gpt-5.5",
            "anthropic": "claude-opus-4-7",
            "claude-code": "claude-opus-4-7",
            "claude-code-interactive": "claude-opus-4-7",
            "codex-app-server": "gpt-5.5",
            "gemini": "gemini-3.1-pro",
        }
        config_path = tmp_path / "default_models.json"
        config_path.write_text(json.dumps(configured), encoding="utf-8")
        monkeypatch.setenv("PAWFLOW_DEFAULT_MODELS_FILE", str(config_path))

        assert _load_default_models() == configured
        assert set(configured) >= {"openai", "anthropic", "claude-code", "codex-app-server", "gemini"}

    def test_default_models_use_shipped_config_when_no_runtime_override(self, monkeypatch):
        from core.llm_client import _load_default_models

        monkeypatch.delenv("PAWFLOW_DEFAULT_MODELS_FILE", raising=False)
        shipped = json.loads(
            open("config/default_models.json", encoding="utf-8").read())
        defaults = _load_default_models()
        # data/system override is absent in the test env → shipped config wins
        assert defaults == shipped
        assert defaults["anthropic"] == "claude-fable-5"
        assert defaults["claude-code"] == "best"

    def test_default_models_fall_back_when_system_config_is_missing(self, monkeypatch, tmp_path, caplog):
        from core.llm_client import _load_default_models

        monkeypatch.setenv("PAWFLOW_DEFAULT_MODELS_FILE", str(tmp_path / "missing.json"))
        with caplog.at_level("WARNING", logger="core.llm_client"):
            defaults = _load_default_models()
        assert set(defaults) >= {"openai", "anthropic", "claude-code", "codex-app-server", "gemini"}
        assert defaults["claude-code"]
        # Missing file is the normal state — must not warn at startup.
        assert not caplog.records


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


class TestChatCompletionsEndpoint:
    """base_url -> chat/completions suffix resolution.

    Guards against re-appending /v1 onto bases that already carry a version
    segment (e.g. z.ai's /api/paas/v4), which produced 404s on /v1/... paths.
    """

    @pytest.mark.parametrize("base_url,expected_full", [
        # Bases ending in any /v<N> keep their own version, no /v1 re-append.
        ("https://api.openai.com/v1", "/v1/chat/completions"),
        ("https://openrouter.ai/api/v1", "/api/v1/chat/completions"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1",
         "/compatible-mode/v1/chat/completions"),
        ("https://api.z.ai/api/paas/v4", "/api/paas/v4/chat/completions"),
        # Suffixed version segments (Gemini-compatible gateways) count too.
        ("https://generativelanguage.googleapis.com/v1beta",
         "/v1beta/chat/completions"),
        # Already-complete endpoint is used verbatim (no duplication), whether
        # the path carries a version segment or not.
        ("https://api.z.ai/api/paas/v4/chat/completions",
         "/api/paas/v4/chat/completions"),
        ("https://proxy.example.com/openai/chat/completions",
         "/openai/chat/completions"),
        # No version segment -> default /v1 (host-only or bare path).
        ("https://api.z.ai", "/v1/chat/completions"),
        ("", "/v1/chat/completions"),
        ("https://proxy.example.com/openai", "/openai/v1/chat/completions"),
    ])
    def test_endpoint_resolution(self, base_url, expected_full):
        from urllib.parse import urlparse
        from core.llm_providers.openai import LLMOpenaiMixin
        suffix = LLMOpenaiMixin._chat_completions_endpoint(base_url)
        full = (urlparse(base_url).path.rstrip("/") + suffix).replace("//", "/")
        assert full == expected_full


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
