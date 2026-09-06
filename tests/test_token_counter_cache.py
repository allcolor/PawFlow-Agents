"""Bounded raw-count reuse preserves mutable context accounting."""
import json
import sys
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from core import token_counter as tc


_GET_ENCODING = tc._get_encoding


class Encoding:
    def __init__(self, width=3):
        self.width = width
        self.calls = []

    def encode(self, text, *, disallowed_special):
        assert disallowed_special == ()
        self.calls.append(text)
        return range((len(text.encode("utf-8")) + self.width - 1) // self.width)


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(tc, "_token_cache", OrderedDict())
    monkeypatch.setattr(tc, "_token_cache_bytes", 0)
    monkeypatch.setattr(tc, "_token_cache_state", None)
    monkeypatch.setattr(tc, "_encoding_failed_at", 0.0)
    encoding = Encoding()
    monkeypatch.setattr(tc, "_encoding", encoding)
    monkeypatch.setattr(tc, "_get_encoding", lambda: tc._encoding)
    return encoding


def full_count(messages, system="", tools=(), multiplier=1.0, width=3):
    """Independent uncached reference for the existing counting contract."""
    def raw(text):
        size = len(text.encode("utf-8"))
        return (size + width - 1) // width

    total = 0
    for message in messages:
        content = (message.get("content", "") if isinstance(message, dict)
                   else getattr(message, "content", ""))
        total += 4
        if isinstance(content, str):
            total += raw(content)
        elif isinstance(content, list):
            total += sum(raw(block["text"]) for block in content
                         if isinstance(block, dict) and block.get("text"))
    if system:
        total += raw(system)
    for tool in tools:
        get = tool.get if isinstance(tool, dict) else lambda key: getattr(tool, key, None)
        text = str(get("name") or "") + str(get("description") or "")
        params = get("parameters")
        if params:
            try:
                text += (json.dumps(params, sort_keys=True, ensure_ascii=False)
                         if isinstance(params, dict) else str(params))
            except (TypeError, ValueError):
                text += str(params)
        total += raw(text)
    return int(total * multiplier) if multiplier and multiplier != 1.0 else total


@pytest.mark.parametrize("multiplier", [0, None, 1.0, 1.33, 1.6, 2.0])
@pytest.mark.parametrize("fallback", [False, True])
def test_repeated_context_exact_parity(isolated_cache, monkeypatch, multiplier, fallback):
    if fallback:
        monkeypatch.setattr(tc, "_encoding", None)
    messages = [
        {"content": "Unicode café 漢字 " + chr(0x1F43E)},
        SimpleNamespace(content=[{"text": "literal <|endoftext|> marker"},
                                 {"type": "image", "source": {"data": "large"}},
                                 {"text": ""}, "ignored"]),
        {"content": None},
        {},
    ]
    tools = [
        {"name": "read", "description": "Read café", "parameters":
         {"type": "object", "properties": {"path": {"type": "string"}}}},
        SimpleNamespace(name="other", description=None, parameters={"enum": {"a", "b"}}),
        {"name": "mixed", "parameters": {1: "numeric", "two": "string"}},
        {"name": "list", "parameters": ["one", "two"]},
    ]
    expected = full_count(messages, "system", tools, multiplier, width=4 if fallback else 3)
    for _ in range(3):
        assert tc.count_context_tokens(
            messages, system_prompt="system", tool_defs=tools, multiplier=multiplier) == expected
    if not fallback:
        assert len(isolated_cache.calls) == 8


def test_unchanged_text_and_tool_schema_skip_encoding(isolated_cache):
    messages = [{"content": "message"}, {"content": [{"text": "block"}]}]
    tools = [{"name": "tool", "description": "description",
              "parameters": {"properties": {"x": {"type": "string"}}}}]
    first = tc.count_context_tokens(messages, system_prompt="system", tool_defs=tools)
    assert len(isolated_cache.calls) == 4
    for _ in range(4):
        assert tc.count_context_tokens(
            messages, system_prompt="system", tool_defs=tools) == first
    assert len(isolated_cache.calls) == 4


def test_message_edits_compaction_and_schema_edits(isolated_cache):
    messages = [SimpleNamespace(content="first"), {"content": [{"text": "second"}]}]
    tools = [{"name": "tool", "parameters": {"enum": [1]}}]
    system = "system"

    def check():
        assert tc.count_context_tokens(messages, system_prompt=system, tool_defs=tools) == (
            full_count(messages, system, tools))

    check()
    calls = len(isolated_cache.calls)
    messages[0].content = "replaced message content"
    check()
    assert len(isolated_cache.calls) == calls + 1
    messages[1]["content"][0]["text"] = "edited nested block"
    check()
    messages.append({"content": "appended"})
    check()
    del messages[:2]
    check()
    messages[:] = [{"content": "compacted summary"}]
    check()
    tools[0]["parameters"]["enum"][0] = 1.0
    check()
    tools[0]["parameters"]["enum"][0] = True
    check()
    tools[0]["description"] = "new description"
    check()
    tools[:] = [SimpleNamespace(name="new tool", description="different", parameters={})]
    system = "new provider system prompt"
    check()


def test_multiplier_is_applied_after_raw_total(isolated_cache):
    messages = [{"content": "a"}, {"content": [{"text": "b"}, {"text": "c"}]}]
    raw = tc.count_context_tokens(messages, system_prompt="d", tool_defs=[{"name": "e"}])
    calls = len(isolated_cache.calls)
    for multiplier in (0, 1.33, 1.6, 2.0):
        assert tc.count_context_tokens(
            messages, system_prompt="d", tool_defs=[{"name": "e"}],
            multiplier=multiplier) == (int(raw * multiplier) if multiplier else raw)
        assert tc.count_messages_tokens(messages, multiplier) == (
            int(11 * multiplier) if multiplier else 11)
    assert len(isolated_cache.calls) == calls


def test_encoding_and_fallback_implementation_changes(isolated_cache, monkeypatch):
    text = "abcdefghij"
    assert tc.count_tokens(text) == 4
    monkeypatch.setattr(tc, "_encoding", Encoding(width=2))
    assert tc.count_tokens(text) == 5
    monkeypatch.setattr(tc, "_encoding", None)
    assert tc.count_tokens(text) == 3
    monkeypatch.setattr(tc, "_estimate_tokens", lambda value: 19)
    assert tc.count_tokens(text) == 19
    monkeypatch.setattr(tc, "_encoding", isolated_cache)
    monkeypatch.setattr(Encoding, "encode", lambda self, value, **kwargs: range(7))
    assert tc.count_tokens(text) == 7


def test_instance_encode_monkeypatch_does_not_reuse_count(isolated_cache, monkeypatch):
    assert tc.count_tokens("same") == 2
    monkeypatch.setattr(isolated_cache, "encode", lambda value, **kwargs: range(9))
    assert tc.count_tokens("same") == 9


def test_fallback_cache_does_not_delay_tokenizer_retry(monkeypatch):
    import time

    clock = [100.0]
    calls = []
    recovered = Encoding(width=2)

    def get_encoding(name):
        calls.append(name)
        if len(calls) == 1:
            raise RuntimeError("temporarily unavailable")
        return recovered

    monkeypatch.setattr(tc, "_get_encoding", _GET_ENCODING)
    monkeypatch.setattr(tc, "_encoding", None)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(get_encoding=get_encoding))
    assert tc.count_tokens("abcdefghij") == 3
    assert tc.count_tokens("abcdefghij") == 3
    assert len(calls) == 1
    clock[0] += tc._ENCODING_RETRY_SECONDS
    assert tc.count_tokens("abcdefghij") == 5
    assert len(calls) == 2
    assert len(recovered.calls) == 1
    assert tc.count_tokens("abcdefghij") == 5
    assert len(recovered.calls) == 1


def test_lru_entry_limit(isolated_cache, monkeypatch):
    monkeypatch.setattr(tc, "_TOKEN_CACHE_MAX_ENTRIES", 2)
    for text in ("a", "b", "a", "c"):
        tc.count_tokens(text)
    assert list(tc._token_cache) == ["a", "c"]
    assert isolated_cache.calls == ["a", "b", "c"]
    tc.count_tokens("b")
    assert isolated_cache.calls == ["a", "b", "c", "b"]
    assert tc._token_cache_bytes >= sum(map(sys.getsizeof, tc._token_cache))


def test_unicode_byte_limit_and_oversized_bypass(isolated_cache, monkeypatch):
    texts = ["a" * 40, "漢" * 40, chr(0x1F43E) * 40]
    limit = 1000
    monkeypatch.setattr(tc, "_TOKEN_CACHE_MAX_BYTES", limit)
    for text in texts:
        tc.count_tokens(text)
    assert tc._token_cache_bytes <= limit
    assert texts[0] not in tc._token_cache
    huge = "z" * tc._TOKEN_CACHE_MAX_TEXT_BYTES
    tc.count_tokens(huge)
    tc.count_tokens(huge)
    assert huge not in tc._token_cache
    assert isolated_cache.calls[-2:] == [huge, huge]
    assert tc._token_cache_bytes >= sum(map(sys.getsizeof, tc._token_cache))


def test_failures_are_not_cached(isolated_cache, monkeypatch):
    calls = []
    def encode(value, **kwargs):
        calls.append(value)
        if len(calls) == 1:
            raise ValueError("temporary encode error")
        return range(5)

    monkeypatch.setattr(isolated_cache, "encode", encode)
    with pytest.raises(ValueError, match="temporary"):
        tc.count_tokens("retry")
    assert tc.count_tokens("retry") == 5
    assert tc.count_tokens("retry") == 5
    assert calls == ["retry", "retry"]


def test_concurrent_misses_do_not_double_account_bytes(isolated_cache, monkeypatch):
    barrier = Barrier(4)
    def encode(value, **kwargs):
        barrier.wait(timeout=5)
        return range(3)

    monkeypatch.setattr(isolated_cache, "encode", encode)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(tc.count_tokens, ["same"] * 4))
    assert results == [3] * 4
    assert list(tc._token_cache) == ["same"]
    assert tc._token_cache_bytes == 128 + 8 * len("same")


def test_inflight_old_encoding_cannot_publish_after_reset(monkeypatch):
    started, finish = Event(), Event()
    def encode(value, **kwargs):
        started.set()
        assert finish.wait(timeout=5)
        return range(9)

    monkeypatch.setattr(tc, "_encoding", SimpleNamespace(encode=encode))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tc.count_tokens, "same")
        assert started.wait(timeout=5)
        try:
            monkeypatch.setattr(tc, "_encoding", Encoding(width=2))
            assert tc.count_tokens("same") == 2
        finally:
            finish.set()
        assert future.result(timeout=5) == 9
    assert tc.count_tokens("same") == 2
    assert tc._token_cache["same"] == 2
    assert tc._token_cache_bytes == 128 + 8 * len("same")


@pytest.mark.parametrize("text", [
    "", "literal <|endoftext|> marker", "café 漢字", "e\u0301",
    chr(0x1F43E), chr(0xD800), "first\nsecond\tcolumn",
])
def test_real_tokenizer_exact_counts_and_truncation(text, monkeypatch):
    tiktoken = pytest.importorskip("tiktoken")
    encoding = tiktoken.get_encoding("cl100k_base")
    monkeypatch.setattr(tc, "_encoding", encoding)
    tokens = encoding.encode(text, disallowed_special=())
    for multiplier in (1.0, 1.33, 1.6):
        assert tc.count_tokens(text, multiplier) == int(len(tokens) * multiplier)
        assert tc.count_tokens(text, multiplier) == int(len(tokens) * multiplier)
    expected = text if len(tokens) <= 1 else encoding.decode(tokens[:1])
    assert tc.truncate_tokens(text, 1) == expected
