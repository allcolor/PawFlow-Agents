"""Explicit JSON nulls in Chat Completions payloads must read as absent.

Observed 2026-09-04 on opencode_llm_service (OpenCode Go, glm-5.3-flash):
after a 200 the stream carried a delta whose ``tool_calls`` was ``null``.
``dict.get("tool_calls", [])`` returned ``None``, the ``for`` raised
``TypeError: 'NoneType' object is not iterable``, the except clause did not
cover it, and the whole agent turn died as "LLM streaming failed".
"""

import json

from core._llm_types import LLMMessage
from core.llm_client import LLMClient


def _sse(*events):
    return [e.encode() for e in events] + [b""]


class _Response:
    status = 200
    reason = "OK"

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, _size=None):
        return self._chunks.pop(0) if self._chunks else b""

    def getheaders(self):
        return []


def _client(monkeypatch, chunks):
    class _Connection:
        def __init__(self, host, port=None, timeout=None):
            pass

        def request(self, method, path, body=None, headers=None):
            pass

        def getresponse(self):
            return _Response(chunks)

        def close(self):
            pass

    monkeypatch.setattr(
        "core.llm_providers.openai.http.client.HTTPConnection", _Connection)
    return LLMClient(provider="openai", config={
        "api_key": "sk-test",
        "base_url": "http://localhost:11434/v1",
        "default_model": "glm-5.3-flash",
        "max_retries": 1,
    })


def _event(payload) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


NULL_TOOL_CALLS = _event({"choices": [{"delta": {
    "content": "hello", "tool_calls": None, "reasoning_content": None}}]})
NULL_DELTA = _event({"choices": [{"delta": None, "finish_reason": None}]})
NULL_FUNCTION = _event({"choices": [{"delta": {"tool_calls": [
    {"index": None, "id": "call_1", "function": None},
    {"index": 0, "function": {"name": "lookup", "arguments": None}},
    {"index": 0, "function": {"arguments": "{\"q\": 1}"}},
]}}]})
STOP = _event({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
DONE = "data: [DONE]\n\n"


def test_stream_treats_null_fields_as_absent(monkeypatch):
    client = _client(monkeypatch, _sse(
        NULL_TOOL_CALLS, NULL_DELTA, NULL_FUNCTION, STOP, DONE))

    resp = client.complete_stream(
        [LLMMessage("user", "ping", conversation_id="conv")],
        callback=lambda _text: None)

    assert resp.content == "hello"
    assert [(tc.id, tc.name, tc.arguments) for tc in resp.tool_calls] == [
        ("call_1", "lookup", {"q": 1}),
    ]


def test_non_streaming_treats_null_fields_as_absent(monkeypatch):
    client = _client(monkeypatch, [])
    payload = {
        "model": "glm-5.3-flash",
        "choices": [{
            "message": {"role": "assistant", "content": None,
                        "reasoning_content": None, "tool_calls": None},
            "finish_reason": "stop",
        }],
        "usage": None,
    }
    monkeypatch.setattr(client, "_http_post",
                        lambda *_args, **_kwargs: payload)

    resp = client.complete([LLMMessage("user", "ping", conversation_id="conv")])

    assert resp.content == ""
    assert resp.tool_calls == []
