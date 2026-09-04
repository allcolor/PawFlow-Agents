"""Outbound identity headers for direct LLM API providers."""

import json

import pytest

from core import __version__
from core._llm_types import LLMMessage
from core.llm_client import LLMClient
from core.llm_http_headers import llm_api_headers, pawflow_user_agent
from core.llm_providers._codex_credentials import (
    refresh_oauth_token as refresh_codex_oauth_token,
)
from core.llm_providers.gemini_session import (
    refresh_oauth_token as refresh_gemini_oauth_token,
)


def _client(base_url: str) -> LLMClient:
    return LLMClient(provider="openai", config={
        "api_key": "sk-test",
        "base_url": base_url,
        "default_model": "deepseek-v4-flash",
    })


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


def test_user_agent_uses_the_authoritative_pawflow_version():
    assert pawflow_user_agent() == f"PawFlow/{__version__}"


def test_opencode_go_headers_use_the_exact_conversation_id():
    headers = llm_api_headers(
        "https://opencode.ai/zen/go/v1", conversation_id="conv-123")

    assert headers == {
        "User-Agent": f"PawFlow/{__version__}",
        "x-opencode-session": "conv-123",
    }


@pytest.mark.parametrize("base_url", [
    "https://api.openai.com/v1",
    "https://opencode.ai/zen/v1",
    "https://opencode.ai.evil.example/zen/go/v1",
    "https://example.com/zen/go/v1",
])
def test_opencode_session_header_never_leaks_to_other_endpoints(base_url):
    headers = llm_api_headers(base_url, conversation_id="private-conv")

    assert headers == {"User-Agent": f"PawFlow/{__version__}"}


def test_opencode_go_requires_a_session_identity():
    with pytest.raises(ValueError, match="conversation_id"):
        llm_api_headers("https://opencode.ai/zen/go/v1")


def test_non_streaming_opencode_request_carries_identity_headers(monkeypatch):
    captured = {}
    client = _client("https://opencode.ai/zen/go/v1")

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


def test_streaming_opencode_request_carries_identity_headers(monkeypatch):
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
    client = _client("https://opencode.ai/zen/go/v1")
    client._stream_openai(
        [LLMMessage("user", "ping", conversation_id="conv-stream")],
        "deepseek-v4-flash", 0.0, 0, None, None,
        call_conversation_id="conv-stream",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/zen/go/v1/chat/completions"
    assert captured["headers"]["User-Agent"] == f"PawFlow/{__version__}"
    assert captured["headers"]["x-opencode-session"] == "conv-stream"


def test_shared_json_transport_adds_identity_without_vendor_headers(monkeypatch):
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
    client._http_post(
        "/messages", {}, {
            "X-Vendor-Contract": "kept",
            "User-Agent": "generic-client/0",
        },
        base_url="http://provider.example/v1",
    )

    assert captured["headers"]["User-Agent"] == f"PawFlow/{__version__}"
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
