"""On the Responses API a model's reasoning is an ITEM, and it has to come back.

chat/completions has no equivalent: the chain of thought is a field on the
message, and dropping it costs nothing but display. The Responses API models it
as its own output item that the NEXT request is expected to carry back with the
turn that produced it. Codex does exactly that.

PawFlow accumulated only the reasoning *text* and threw the item away, so every
iteration of a tool loop re-entered having forgotten why it had called the tool.
And under Zero Data Retention (`store: false`), where the item lives nowhere but
in our own history, omitting it is a hard 400 rather than a quality loss.
"""

import json

from core.llm_client import LLMClient, LLMMessage, LLMToolCall
from core.llm_providers.openai_responses import (
    build_responses_input, _StreamState)

CONV = "c1"

REASONING_ITEM = {
    "type": "reasoning",
    "id": "rs_abc",
    "summary": [],
    "encrypted_content": "gAAAAAB-opaque-blob",
}


# ── capture ────────────────────────────────────────────────────────────────


def test_a_reasoning_item_is_captured_whole():
    st = _StreamState()
    st.feed({"type": "response.output_item.added", "item_id": "rs_abc",
             "item": {"type": "reasoning", "id": "rs_abc", "summary": []}})
    st.feed({"type": "response.output_item.done", "item_id": "rs_abc",
             "item": REASONING_ITEM})
    assert st.reasoning_order == ["rs_abc"]
    assert st.reasoning_items["rs_abc"]["encrypted_content"] == \
        "gAAAAAB-opaque-blob"


def test_done_supersedes_added():
    """`added` opens a stub; the encrypted content only arrives on `done`."""
    st = _StreamState()
    st.feed({"type": "response.output_item.added", "item_id": "rs_abc",
             "item": {"type": "reasoning", "id": "rs_abc"}})
    st.feed({"type": "response.output_item.done", "item_id": "rs_abc",
             "item": REASONING_ITEM})
    assert "encrypted_content" in st.reasoning_items["rs_abc"]
    assert len(st.reasoning_order) == 1, "one item, not two"


def test_a_reasoning_item_is_not_mistaken_for_a_tool_call():
    st = _StreamState()
    st.feed({"type": "response.output_item.added", "item_id": "rs_abc",
             "item": REASONING_ITEM})
    assert st.calls == {}


def test_reasoning_text_still_accumulates_separately():
    """The item is for the wire, the text is for the reader. Both."""
    st = _StreamState()
    st.feed({"type": "response.reasoning_summary_text.delta",
             "delta": "weighing the options"})
    st.feed({"type": "response.output_item.done", "item_id": "rs_abc",
             "item": REASONING_ITEM})
    assert "".join(st.reasoning) == "weighing the options"
    assert st.reasoning_order == ["rs_abc"]


# ── replay ─────────────────────────────────────────────────────────────────


def test_the_item_is_replayed_before_the_call_it_led_to():
    """The API validates the order: a reasoning item must be followed by the
    item it reasoned towards."""
    _instr, items = build_responses_input([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "",
         "reasoning_item": json.dumps([REASONING_ITEM]),
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "ls", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ])
    assert [i["type"] for i in items] == [
        "message", "reasoning", "function_call", "function_call_output"]
    assert items[1]["encrypted_content"] == "gAAAAAB-opaque-blob"


def test_a_turn_that_only_answered_carries_its_reasoning_too():
    _instr, items = build_responses_input([
        {"role": "assistant", "content": "the answer",
         "reasoning_item": json.dumps([REASONING_ITEM])},
    ])
    assert [i["type"] for i in items] == ["reasoning", "message"]


def test_a_malformed_item_is_dropped_not_raised_on():
    """A lost chain of thought costs continuity; a malformed item costs the
    whole request."""
    _instr, items = build_responses_input([
        {"role": "assistant", "content": "hi", "reasoning_item": "{not json"},
    ])
    assert [i["type"] for i in items] == ["message"]


def test_a_non_reasoning_item_is_never_replayed():
    """Whatever ends up in the field, only reasoning items go back on the
    wire -- anything else would be an item we invented."""
    _instr, items = build_responses_input([
        {"role": "assistant", "content": "hi",
         "reasoning_item": json.dumps([{"type": "function_call",
                                        "call_id": "x", "name": "rm"}])},
    ])
    assert [i["type"] for i in items] == ["message"]


def test_a_leading_system_message_still_becomes_instructions():
    """Reasoning items must not count as "we have left the system prefix"."""
    instructions, items = build_responses_input([
        {"role": "system", "content": "be brief"},
        {"role": "assistant", "content": "ok",
         "reasoning_item": json.dumps([REASONING_ITEM])},
    ])
    assert instructions == "be brief"
    assert [i["type"] for i in items] == ["reasoning", "message"]


# ── the field reaches the builder, and only the right builder ──────────────


def _client():
    return LLMClient(provider="openai-responses", config={
        "api_key": "k", "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-5.5"})


def _assistant_with_reasoning():
    return [
        LLMMessage(role="user", content="go", conversation_id=CONV),
        LLMMessage(role="assistant", content="",
                   tool_calls=[LLMToolCall(id="call_1", name="ls",
                                           arguments={})],
                   reasoning_item=json.dumps([REASONING_ITEM]),
                   conversation_id=CONV),
        LLMMessage(role="tool", content="ok", tool_call_id="call_1",
                   conversation_id=CONV),
    ]


def test_the_responses_body_carries_the_item():
    client = _client()
    body = client._build_responses_body(
        _assistant_with_reasoning(), "gpt-5.5", 0.5, 100, None)
    types = [i["type"] for i in body["input"]]
    assert "reasoning" in types
    assert types.index("reasoning") < types.index("function_call")


def test_chat_completions_never_sees_the_field():
    """An unknown key inside a chat/completions message is a 400, not an
    ignored field -- so the normaliser only emits it when asked."""
    client = _client()
    plain = client._build_openai_messages(
        _assistant_with_reasoning(), user_id="u", conversation_id=CONV)
    assert all("reasoning_item" not in m for m in plain)


def test_zero_data_retention_asks_for_the_encrypted_content():
    client = LLMClient(provider="openai-responses", config={
        "api_key": "k", "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-5.5", "store": False})
    body = client._build_responses_body(
        _assistant_with_reasoning(), "gpt-5.5", 0.5, 100, None)
    assert body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"]


def test_without_the_setting_neither_key_is_sent():
    """Omitted means the API default (server-side storage), which is what an
    ordinary org wants -- sending store=true unasked would be a decision."""
    body = _client()._build_responses_body(
        _assistant_with_reasoning(), "gpt-5.5", 0.5, 100, None)
    assert "store" not in body
    assert "include" not in body


def _body_with(**cfg):
    client = LLMClient(provider="openai-responses", config=dict(
        {"api_key": "k", "base_url": "https://api.openai.com/v1",
         "default_model": "gpt-5.5"}, **cfg))
    return client._build_responses_body(
        _assistant_with_reasoning(), "gpt-5.5", 0.5, 100, None)


def test_store_off_through_extra_body_still_asks_for_the_encrypted_content():
    """`extra_body` is merged over the body, and the include used to be decided
    BEFORE that merge -- so the one route the connection form actually exposed
    produced a ZDR request with no encrypted reasoning: a 400 on the next turn
    of any tool loop."""
    body = _body_with(extra_body={"store": False})
    assert body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"]


def test_a_form_field_answers_with_a_string():
    """`store` is a select in the llmConnection schema, so it arrives as the
    text "false" -- and `bool("false")` is True, which would silently turn the
    one setting a ZDR org depends on into its opposite."""
    body = _body_with(store="false")
    assert body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"]

    on = _body_with(store="true")
    assert on["store"] is True
    assert "include" not in on

    unset = _body_with(store="")
    assert "store" not in unset, "empty select means unset, not false"
    assert "include" not in unset


def test_the_setting_is_reachable_from_the_connection_form():
    """Documented and configurable are not the same thing: the docs asked for
    `store: false` while the schema exposed no such field."""
    from services.llm_connection import LLMConnectionService

    svc = object.__new__(LLMConnectionService)
    schema = svc.get_parameter_schema()
    assert "store" in schema, "ZDR is not configurable without it"
    assert schema["store"]["default"] == ""
    # Responses-only: chat/completions has no stored response object.
    shown = [r for r in svc.get_parameter_rules()
             if "store" in r["set"] and r["set"]["store"]["visible"]]
    assert shown and shown[0]["when"]["provider"] == ["openai-responses"]


def test_an_explicit_include_is_completed_not_overwritten():
    """A caller's include is kept AND the ZDR one is added to it.

    Testing the key alone made an explicit include a second way to break the
    same thing `store` did: `store: false` with any include of one's own was a
    request the API keeps nothing of, asking for no encrypted reasoning --
    a 400 on the next turn of the tool loop, exactly as before.
    """
    body = _body_with(store=False,
                      extra_body={"include": ["message.output_text"]})
    assert body["include"] == ["message.output_text",
                               "reasoning.encrypted_content"]


def test_an_empty_include_is_still_completed():
    """The reviewer's repro: `include: []` is present, so the key test passed
    it through untouched."""
    body = _body_with(store=False, extra_body={"include": []})
    assert body["include"] == ["reasoning.encrypted_content"]


def test_the_include_is_not_asked_for_twice():
    body = _body_with(store=False, extra_body={
        "include": ["reasoning.encrypted_content"]})
    assert body["include"] == ["reasoning.encrypted_content"]


def test_a_scalar_include_is_not_dropped_on_the_floor():
    """Not a shape the API takes, but silently replacing what the caller wrote
    is how the first version of this lost the setting it was fixing."""
    body = _body_with(store=False, extra_body={"include": "message.output_text"})
    assert body["include"] == ["message.output_text",
                               "reasoning.encrypted_content"]


def test_storage_on_asks_for_nothing_even_with_an_include():
    """The encrypted content is only needed when the API keeps nothing."""
    body = _body_with(store=True, extra_body={"include": ["message.output_text"]})
    assert body["include"] == ["message.output_text"]


# ── persistence ────────────────────────────────────────────────────────────


def test_the_item_survives_serialization():
    from tasks.ai.agent_serialization import AgentSerializationMixin

    mixin = object.__new__(AgentSerializationMixin)
    msg = LLMMessage(role="assistant", content="hi",
                     reasoning_item=json.dumps([REASONING_ITEM]),
                     conversation_id=CONV)
    rows = mixin._serialize_messages([msg])
    assert rows[0]["reasoning_item"] == json.dumps([REASONING_ITEM])


def test_an_item_with_no_thinking_text_is_still_stored():
    """The reason it hangs off the assistant row and not the thinking row: on
    OpenAI the summary is routinely empty while the item still exists."""
    from tasks.ai.agent_serialization import AgentSerializationMixin

    mixin = object.__new__(AgentSerializationMixin)
    msg = LLMMessage(role="assistant", content="hi", thinking="",
                     reasoning_item=json.dumps([REASONING_ITEM]),
                     conversation_id=CONV)
    rows = mixin._serialize_messages([msg])
    assert not any(r.get("role") == "thinking" for r in rows)
    assert rows[0]["reasoning_item"]
