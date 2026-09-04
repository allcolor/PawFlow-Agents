"""Outbound identity headers and operator-configured ``extra_headers``."""

import json

import pytest

from core import __version__
from core._llm_types import LLMMessage
from core.llm_client import LLMClient
from core.llm_http_headers import (
    llm_api_headers,
    pawflow_user_agent,
    render_extra_headers,
    request_scope,
)
from core.llm_providers._codex_credentials import (
    refresh_oauth_token as refresh_codex_oauth_token,
)
from core.llm_providers.gemini_session import (
    refresh_oauth_token as refresh_gemini_oauth_token,
)

OPENCODE_HEADERS = {"x-opencode-session": "${request.session_id}"}


def _client(base_url: str, extra_headers=None) -> LLMClient:
    config = {
        "api_key": "sk-test",
        "base_url": base_url,
        "default_model": "deepseek-v4-flash",
    }
    if extra_headers is not None:
        config["extra_headers"] = extra_headers
    return LLMClient(provider="openai", config=config)


def _completion_payload() -> dict:
    return {
        "model": "deepseek-v4-flash",
        "choices": [{
            "message": {"content": "ok"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                  "total_tokens": 2},
    }


def _scope(**overrides):
    values = {"session_id": "sess", "conversation_id": "conv",
              "user_id": "quentin", "agent_name": "assistant"}
    values.update(overrides)
    return request_scope(**values)


def test_user_agent_uses_the_authoritative_pawflow_version():
    assert pawflow_user_agent() == f"PawFlow/{__version__}"


def test_extra_headers_render_the_request_scope():
    rendered = render_extra_headers({
        "x-opencode-session": "${request.session_id}",
        "x-tenant": "${request.user_id}/${request.agent_name}",
        "x-pawflow": "pf-${pawflow.version}",
    }, _scope())

    assert rendered == {
        "x-opencode-session": "sess",
        "x-tenant": "quentin/assistant",
        "x-pawflow": f"pf-{__version__}",
    }


def test_extra_headers_never_send_raw_templates_or_protected_names():
    rendered = render_extra_headers({
        "Authorization": "Bearer stolen",
        "x-api-key": "nope",
        "Content-Length": "0",
        "x-unresolved": "${does_not_exist_anywhere_zz}",
        "x-empty": "   ",
        "bad name": "x",
        "x-object": {"nested": True},
        "x-crlf": "a\r\nInjected: b",
        "User-Agent": "opencode/1.0",
    }, _scope())

    assert rendered == {"x-crlf": "aInjected: b", "User-Agent": "opencode/1.0"}


def test_llm_api_headers_without_configuration_is_identity_only():
    assert llm_api_headers() == {"User-Agent": f"PawFlow/{__version__}"}
    assert llm_api_headers({}, None) == {"User-Agent": f"PawFlow/{__version__}"}


@pytest.mark.parametrize("header_name", ["User-Agent", "user-agent", "USER-AGENT"])
def test_llm_api_headers_lets_configuration_replace_the_user_agent(header_name):
    headers = llm_api_headers({header_name: "opencode/2"}, _scope())
    assert headers == {"User-Agent": "opencode/2"}


def test_request_scope_requires_a_session_id():
    with pytest.raises(ValueError, match="session_id"):
        request_scope(session_id="")


def test_session_id_is_the_conversation_inside_one():
    client = _client("https://opencode.ai/zen/go/v1", OPENCODE_HEADERS)
    assert client.request_headers("conv-123")["x-opencode-session"] == "conv-123"


def test_session_id_is_stable_per_service_outside_a_conversation():
    client = _client("https://opencode.ai/zen/go/v1", OPENCODE_HEADERS)
    first = client.request_headers()["x-opencode-session"]
    again = client.request_headers()["x-opencode-session"]
    clone = client.clone_for_call().request_headers()["x-opencode-session"]
    other_service = _client("https://opencode.ai/zen/go/v1", OPENCODE_HEADERS)

    assert first and first == again == clone
    assert other_service.request_headers()["x-opencode-session"] != first


def test_extra_headers_survive_the_lazy_resolving_service_config():
    """Service configs resolve ${...} on every .get(); the request scope only
    exists at request time, so the template must reach the client intact."""
    from core.expression import LazyResolveDict

    config = LazyResolveDict({
        "api_key": "sk-test",
        "base_url": "https://opencode.ai/zen/go/v1",
        "default_model": "deepseek-v4-flash",
        "extra_headers": json.dumps(OPENCODE_HEADERS),
    })
    client = LLMClient(provider="openai", config=config)

    assert client.request_headers("conv-lazy")["x-opencode-session"] == "conv-lazy"


def test_extra_headers_accept_the_json_string_form_of_the_service_form():
    client = _client("https://opencode.ai/zen/go/v1", json.dumps(OPENCODE_HEADERS))
    assert client.request_headers("conv-json")["x-opencode-session"] == "conv-json"

    broken = _client("https://opencode.ai/zen/go/v1", "{not json")
    assert broken.request_headers("conv") == {"User-Agent": f"PawFlow/{__version__}"}


@pytest.mark.parametrize("base_url", [
    "https://api.openai.com/v1",
    "https://opencode.ai/zen/go/v1",
    "https://example.com/zen/go/v1",
])
def test_no_gateway_header_is_ever_implied_by_the_url(base_url):
    client = _client(base_url)
    assert client.request_headers("private-conv") == {
        "User-Agent": f"PawFlow/{__version__}"}


def test_non_streaming_request_carries_rendered_headers(monkeypatch):
    captured = {}
    client = _client("https://opencode.ai/zen/go/v1", OPENCODE_HEADERS)

    def fake_post(path, body, headers, *, base_url=""):
        captured.update(path=path, headers=dict(headers), base_url=base_url)
        return _completion_payload()

    monkeypatch.setattr(client, "_http_post", fake_post)
    client._complete_openai(
        [LLMMessage("user", "ping", conversation_id="conv-non-stream")],
        "deepseek-v4-flash", 0.0, 0, None,
        call_conversation_id="conv-non-stream",
    )

    assert captured["path"] == "/chat/completions"
    assert captured["base_url"] == "https://opencode.ai/zen/go/v1"
    assert captured["headers"]["User-Agent"] == f"PawFlow/{__version__}"
    assert captured["headers"]["x-opencode-session"] == "conv-non-stream"


def test_streaming_request_carries_rendered_headers(monkeypatch):
    event = {
        "model": "deepseek-v4-flash",
        "choices": [{
            "delta": {"content": "ok"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                  "total_tokens": 2},
    }
    payload = (
        f"data: {json.dumps(event)}\n\n"
        "data: [DONE]\n\n"
    ).encode()
    captured = {}

    class Response:
        status = 200
        reason = "OK"

        def __init__(self):
            self.chunks = [payload, b""]

        def read(self, _size):
            return self.chunks.pop(0)

        def getheaders(self):
            return []

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, method, path, body=None, headers=None):
            captured.update(method=method, path=path, headers=dict(headers or {}))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(
        "core.llm_providers.openai.http.client.HTTPSConnection", Connection)
    client = _client("https://opencode.ai/zen/go/v1", OPENCODE_HEADERS)
    client._stream_openai(
        [LLMMessage("user", "ping", conversation_id="conv-stream")],
        "deepseek-v4-flash", 0.0, 0, None, None,
        call_conversation_id="conv-stream",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/zen/go/v1/chat/completions"
    assert captured["headers"]["User-Agent"] == f"PawFlow/{__version__}"
    assert captured["headers"]["x-opencode-session"] == "conv-stream"


@pytest.mark.parametrize("header_name", [None, "User-Agent", "user-agent"])
def test_shared_json_transport_preserves_configured_identity(monkeypatch, header_name):
    captured = {}

    class Response:
        status = 200

        def getheaders(self):
            return []

        def read(self):
            return b"{}"

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, method, path, body=None, headers=None):
            captured.update(method=method, path=path, headers=dict(headers or {}))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(
        "core.llm_providers.cli_shared.http.client.HTTPConnection", Connection)
    client = _client("http://provider.example/v1")
    headers = {"X-Vendor-Contract": "kept"}
    if header_name:
        headers[header_name] = "generic-client/0"
    client._http_post(
        "/messages", {}, headers,
        base_url="http://provider.example/v1",
    )

    user_agents = [value for name, value in captured["headers"].items()
                   if name.lower() == "user-agent"]
    assert user_agents == (["generic-client/0"] if header_name
                           else [f"PawFlow/{__version__}"])
    assert captured["headers"]["X-Vendor-Contract"] == "kept"
    assert "x-opencode-session" not in captured["headers"]


@pytest.mark.parametrize("refresh", [
    LLMClient._refresh_oauth_token,
    refresh_codex_oauth_token,
    refresh_gemini_oauth_token,
], ids=["claude", "codex", "gemini"])
def test_provider_oauth_refresh_identifies_as_pawflow(monkeypatch, refresh):
    captured = {}

    class Response:
        status = 400

        def read(self):
            return b'{"error":"invalid_request"}'

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, method, path, body=None, headers=None):
            captured.update(method=method, path=path, headers=dict(headers or {}))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("http.client.HTTPSConnection", Connection)
    with pytest.raises(RuntimeError):
        refresh("refresh-token")

    assert captured["method"] == "POST"
    assert captured["headers"]["User-Agent"] == f"PawFlow/{__version__}"
    assert "x-opencode-session" not in captured["headers"]
