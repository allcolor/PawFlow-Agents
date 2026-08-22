"""OmniRoute Chat Completions integration at the pinned public wire seam."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import ServiceError
from core._llm_client_driver import OPENAI_WIRE_PROVIDERS
from core.llm_client import LLMClient, LLMMessage, LLMToolDefinition
from core.llm_providers.omniroute import (
    PINNED_UPSTREAM_COMMIT,
    auth_headers,
    parse_response_metadata,
    request_headers,
)
from services.llm_connection import LLMConnectionService


class _GatewayHandler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        return

    def do_GET(self):
        self.__class__.requests.append(("GET", self.path, dict(self.headers), None))
        body = json.dumps({
            "object": "list",
            "data": [
                {"id": "auto", "owned_by": "omniroute"},
                {"id": "auto/coding", "owned_by": "omniroute"},
            ],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.__class__.requests.append(
            ("POST", self.path, dict(self.headers), payload))
        if payload.get("stream"):
            body = (
                ": x-omniroute-provider=anthropic\n"
                ": x-omniroute-fallback-attempts=2\n"
                "data: {\"model\":\"auto\",\"choices\":[{\"delta\":{\"content\":\"ok\"},\"finish_reason\":null}]}\n"
                "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n"
                "data: [DONE]\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("X-OmniRoute-Version", "3.8.50")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body[:31])
            self.wfile.flush()
            self.wfile.write(body[31:])
            return
        body = json.dumps({
            "model": "auto",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{\"q\":\"x\"}"},
                    }],
                },
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-OmniRoute-Provider", "openai")
        self.send_header("X-OmniRoute-Model", "gpt-5.6")
        self.send_header("X-OmniRoute-Response-Cost", "0.0125")
        self.send_header("X-Ignored-Secret", "must-not-appear")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def gateway():
    _GatewayHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", _GatewayHandler.requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _client(base_url, **overrides):
    config = {
        "base_url": base_url,
        "api_key": "gateway-key",
        "omniroute_auth_mode": "bearer",
        "default_model": "auto",
        "omniroute_mode": "balanced",
        "omniroute_budget_usd": 0.5,
        "omniroute_budget_fallback": "strict",
        "max_retries": 1,
    }
    config.update(overrides)
    return LLMClient(provider="omniroute", config=config)


def test_provider_is_pinned_and_uses_chat_completions_wire():
    assert len(PINNED_UPSTREAM_COMMIT) == 40
    assert "omniroute" in LLMClient.PROVIDERS
    assert "omniroute" in OPENAI_WIRE_PROVIDERS
    assert "omniroute" not in LLMClient.DEFAULT_URLS


def test_auth_mode_is_explicit_and_budget_controls_are_strict():
    assert auth_headers("key", "bearer") == {"Authorization": "Bearer key"}
    assert auth_headers("", "none") == {}
    with pytest.raises(ValueError, match="auth_mode"):
        auth_headers("", "")
    with pytest.raises(ValueError, match="requires api_key"):
        auth_headers("", "bearer")

    headers = request_headers({
        "omniroute_mode": "cheap",
        "omniroute_budget_usd": 0.25,
        "omniroute_budget_fallback": "cheapest",
    })
    assert headers["X-OmniRoute-Mode"] == "cheap"
    assert headers["X-OmniRoute-Budget"] == "0.25"
    assert headers["X-OmniRoute-Budget-Fallback"] == "cheapest"
    assert "X-OmniRoute-Budget" not in request_headers({
        "omniroute_budget_usd": 0})


def test_metadata_parser_accepts_only_bounded_typed_allowlisted_fields():
    metadata = parse_response_metadata([
        ("X-OmniRoute-Provider", "anthropic"),
        ("X-OmniRoute-Latency-Ms", "42"),
        ("X-OmniRoute-Model", "bad\nvalue"),
        ("Authorization", "secret"),
        ("X-OmniRoute-Response-Cost", "nan"),
    ], [": x-omniroute-cache-hit=true"])
    assert metadata == {
        "gateway": "omniroute",
        "upstream_provider": "anthropic",
        "gateway_latency_ms": 42,
        "cache_hit": True,
    }
    assert "secret" not in json.dumps(metadata)


def test_non_streaming_request_preserves_tools_and_reports_gateway_metadata(gateway):
    base_url, requests = gateway
    client = _client(base_url)
    response = client.complete(
        [LLMMessage(role="user", content="use tool", conversation_id="conv-1")],
        tools=[LLMToolDefinition(
            name="lookup", description="lookup", parameters={"type": "object"})],
    )

    method, path, headers, payload = requests[-1]
    assert (method, path) == ("POST", "/v1/chat/completions")
    assert headers["Authorization"] == "Bearer gateway-key"
    assert headers["X-OmniRoute-Mode"] == "balanced"
    assert payload["model"] == "auto"
    assert payload["tools"][0]["function"]["name"] == "lookup"
    assert response.tool_calls[0].arguments == {"q": "x"}
    assert response.provider_metadata["upstream_provider"] == "openai"
    assert response.provider_metadata["upstream_model"] == "gpt-5.6"
    assert response.provider_metadata["gateway_cost_usd"] == 0.0125
    assert "must-not-appear" not in json.dumps(response.provider_metadata)


def test_streaming_metadata_comments_survive_transport_chunk_boundaries(gateway):
    base_url, _requests = gateway
    response = _client(base_url).complete_stream([
        LLMMessage(role="user", content="hello", conversation_id="conv-1")])

    assert response.content == "ok"
    assert response.provider_metadata["gateway_version"] == "3.8.50"
    assert response.provider_metadata["upstream_provider"] == "anthropic"
    assert response.provider_metadata["fallback_attempts"] == 2


def test_model_discovery_uses_same_auth_and_never_changes_default(gateway):
    base_url, requests = gateway
    service = LLMConnectionService({
        "provider": "omniroute",
        "base_url": base_url,
        "omniroute_auth_mode": "bearer",
        "api_key": "gateway-key",
        "default_model": "auto",
    })
    service.connect()

    models = service.list_omniroute_models()

    assert models == [
        {"id": "auto", "owned_by": "omniroute"},
        {"id": "auto/coding", "owned_by": "omniroute"},
    ]
    assert requests[-1][0:2] == ("GET", "/v1/models")
    assert requests[-1][2]["Authorization"] == "Bearer gateway-key"
    assert service.default_model == "auto"


@pytest.mark.parametrize("config, message", [
    ({"provider": "omniroute", "default_model": "auto", "omniroute_auth_mode": "none"}, "base_url"),
    ({"provider": "omniroute", "base_url": "http://localhost/v1", "omniroute_auth_mode": "none"}, "default_model"),
    ({"provider": "omniroute", "base_url": "http://localhost/v1", "default_model": "auto", "omniroute_auth_mode": "bearer"}, "api_key"),
])
def test_service_rejects_ambiguous_or_missing_required_config(config, message):
    with pytest.raises(ServiceError, match=message):
        LLMConnectionService(config).connect()


def test_plain_openai_request_headers_remain_byte_compatible():
    client = LLMClient(provider="openai", config={"api_key": "k"})
    assert client._openai_auth_headers() == {"Authorization": "Bearer k"}
    assert client._openai_provider_headers() == {}
    assert client._openai_provider_metadata(
        [("X-OmniRoute-Provider", "ignored")]) == {}
