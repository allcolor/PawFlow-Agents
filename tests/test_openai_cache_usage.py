"""Cache accounting for OpenAI-compatible Chat Completions responses."""

import json

from core._llm_types import LLMMessage
from core.llm_client import LLMClient
from core.llm_providers.openai import LLMOpenaiMixin


def _client() -> LLMClient:
    return LLMClient(provider="openai", config={
        "api_key": "sk-test",
        "base_url": "http://localhost:11434/v1",
        "default_model": "test-model",
    })


def _non_streaming_completion(monkeypatch, usage):
    client = _client()
    monkeypatch.setattr(client, "_http_post", lambda *_args, **_kwargs: {
        "model": "deepseek-chat",
        "choices": [{
            "message": {"content": "answer"},
            "finish_reason": "stop",
        }],
        "usage": usage,
    })
    return LLMOpenaiMixin._complete_openai(
        client,
        [LLMMessage("user", "ping", conversation_id="conv1")],
        "deepseek-chat", 0.0, 0, None,
    )


def _streaming_completion(monkeypatch, usage):
    event = {
        "model": "deepseek-chat",
        "choices": [{
            "delta": {"content": "answer"},
            "finish_reason": "stop",
        }],
        "usage": usage,
    }
    payload = (
        f"data: {json.dumps(event)}\n\n"
        "data: [DONE]\n\n"
    ).encode()

    class _Response:
        status = 200
        reason = "OK"

        def __init__(self):
            self._chunks = [payload, b""]

        def read(self, _size):
            return self._chunks.pop(0)

        def getheaders(self):
            return []

    class _Connection:
        def __init__(self, host, port=None, timeout=None):
            pass

        def request(self, method, path, body=None, headers=None):
            pass

        def getresponse(self):
            return _Response()

        def close(self):
            pass

    monkeypatch.setattr(
        "core.llm_providers.openai.http.client.HTTPConnection", _Connection)
    return LLMOpenaiMixin._stream_openai(
        _client(),
        [LLMMessage("user", "ping", conversation_id="conv1")],
        "deepseek-chat", 0.0, 0, None, None,
    )


def test_deepseek_cache_counters_drive_non_streaming_usage(monkeypatch):
    response = _non_streaming_completion(monkeypatch, {
        "prompt_tokens": 1024,
        "completion_tokens": 10,
        "total_tokens": 1034,
        "prompt_cache_hit_tokens": 960,
        "prompt_cache_miss_tokens": 64,
        "prompt_tokens_details": {"cached_tokens": 11},
    })

    assert response.tokens_in == 64
    assert response.cache_read_tokens == 960
    assert response.total_tokens == 1034
    assert response.input_usage_native is True


def test_deepseek_cache_counters_drive_streaming_usage(monkeypatch):
    response = _streaming_completion(monkeypatch, {
        "prompt_tokens": 2048,
        "completion_tokens": 20,
        "total_tokens": 2068,
        "prompt_cache_hit_tokens": 2016,
        "prompt_cache_miss_tokens": 32,
        "prompt_tokens_details": {"cached_tokens": 13},
    })

    assert response.tokens_in == 32
    assert response.cache_read_tokens == 2016
    assert response.total_tokens == 2068
    assert response.input_usage_native is True


def test_openai_nested_cached_tokens_remain_supported(monkeypatch):
    response = _non_streaming_completion(monkeypatch, {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "total_tokens": 110,
        "prompt_tokens_details": {"cached_tokens": 60},
    })

    assert response.tokens_in == 40
    assert response.cache_read_tokens == 60
