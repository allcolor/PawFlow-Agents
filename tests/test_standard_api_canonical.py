"""Canonicalization and HMAC-chain contracts for standard agent APIs."""

import math

import pytest

from core.standard_api_canonical import (
    canonical_json,
    canonical_request_fingerprint,
    canonical_visible_item,
    compute_hash_chain,
    eligible_prefixes,
)
from core.standard_api_types import (
    NormalizedVisibleItem,
    StandardApiNamespace,
)


def _namespace(**changes):
    values = {
        "publication_id": "a2ap_one",
        "api_generation": 1,
        "key_id": "key_one",
        "dialect": "chat_completions",
        "api_model_id": "pawflow-agent",
        "canonicalization_version": 1,
        "hash_secret_version": 1,
    }
    values.update(changes)
    return StandardApiNamespace(**values)


def _item(kind, **data):
    return NormalizedVisibleItem(kind=kind, data=data)


def test_canonical_json_is_deterministic_and_preserves_unicode_and_whitespace():
    left = {"z": [2, 1], "a": " café "}
    right = {"a": " café ", "z": [2, 1]}

    assert canonical_json(left) == canonical_json(right)
    assert canonical_json(left).decode("utf-8") == '{"a":" café ","z":[2,1]}'
    assert canonical_json({"text": "x"}) != canonical_json({"text": " x"})


def test_non_finite_numbers_are_rejected():
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite"):
            canonical_json({"value": value})


def test_tool_arguments_canonicalize_valid_json_but_tag_invalid_raw_strings():
    first = _item("client_tool_call_batch", calls=[{
        "id": "call_1",
        "name": "lookup",
        "arguments": '{"b":2,"a":1}',
    }])
    equivalent = _item("client_tool_call_batch", calls=[{
        "id": "call_1",
        "name": "lookup",
        "arguments": '{ "a": 1, "b": 2 }',
    }])
    invalid = _item("client_tool_call_batch", calls=[{
        "id": "call_1",
        "name": "lookup",
        "arguments": '{"a":',
    }])

    assert canonical_visible_item(first) == canonical_visible_item(equivalent)
    assert canonical_visible_item(invalid)["calls"][0]["arguments"] == {
        "encoding": "raw",
        "value": '{"a":',
    }


def test_sdk_default_nulls_and_empty_assistant_content_are_equivalent():
    null_content = _item(
        "assistant_message", content=None, refusal=None, annotations=[])
    empty_content = _item("assistant_message", content="")

    assert canonical_visible_item(null_content) == canonical_visible_item(
        empty_content)


def test_hash_chain_is_namespace_isolated_and_only_server_boundaries_are_eligible():
    items = (
        _item("user_message", content="hello"),
        _item("assistant_message", content="hi"),
        _item("user_message", content="again"),
        _item("client_tool_call_batch", calls=[{
            "id": "call_1", "name": "lookup", "arguments": "{}"}]),
    )
    secret = b"test-only-secret"

    hashes = compute_hash_chain(_namespace(), items, secret)
    assert hashes == compute_hash_chain(_namespace(), items, secret)
    assert hashes != compute_hash_chain(
        _namespace(key_id="key_two"), items, secret)
    assert hashes != compute_hash_chain(
        _namespace(dialect="anthropic_messages"), items, secret)
    assert hashes != compute_hash_chain(
        _namespace(api_generation=2), items, secret)

    assert eligible_prefixes(items, hashes) == (
        {
            "prefix_hash": hashes[1],
            "item_count": 2,
            "boundary_kind": "assistant_message",
        },
        {
            "prefix_hash": hashes[3],
            "item_count": 4,
            "boundary_kind": "client_tool_call_batch",
        },
    )


def test_request_fingerprint_explicitly_excludes_transport_only_fields():
    secret = b"test-only-secret"
    base = {
        "model": "pawflow-agent",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "metadata": {"trace": "one"},
    }
    transport_variant = dict(base, stream=True, metadata={"trace": "two"})

    assert canonical_request_fingerprint(
        base, secret, excluded_fields={"stream", "metadata"}) == (
        canonical_request_fingerprint(
            transport_variant,
            secret,
            excluded_fields={"stream", "metadata"},
        )
    )
    assert canonical_request_fingerprint(base, secret) != (
        canonical_request_fingerprint(transport_variant, secret)
    )
