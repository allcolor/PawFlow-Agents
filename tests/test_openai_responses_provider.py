"""The Responses API is a different wire format, not a flag on chat/completions.

Every test here pins something that silently produces a broken request or a
hung stream if it drifts back toward the chat/completions shape.
"""

import json
import unittest

from core.llm_client import LLMClient, LLMMessage
from core.llm_providers.openai_responses import (
    build_responses_input, responses_endpoint, _StreamState)


class Endpoint(unittest.TestCase):

    def test_a_versioned_base_does_not_get_another_v1(self):
        self.assertEqual(responses_endpoint("https://api.openai.com/v1"),
                         "/responses")

    def test_an_unversioned_base_gets_the_default_version(self):
        # DeepSeek publishes exactly this base for the Responses API.
        self.assertEqual(responses_endpoint("https://api.deepseek.com"),
                         "/v1/responses")

    def test_a_base_that_already_names_the_endpoint_is_left_alone(self):
        self.assertEqual(
            responses_endpoint("https://host/v1/responses"), "")


class InputItems(unittest.TestCase):
    """messages[] -> input[]: the conversion that decides request validity."""

    def test_a_leading_system_message_becomes_instructions(self):
        instructions, items = build_responses_input([
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ])
        self.assertEqual(instructions, "be brief")
        self.assertEqual([i["type"] for i in items], ["message"])

    def test_a_later_system_message_stays_in_place(self):
        """Hoisting it into instructions would reorder the conversation."""
        instructions, items = build_responses_input([
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "now switch tone"},
        ])
        self.assertEqual(instructions, "")
        self.assertEqual([i.get("role") for i in items], ["user", "system"])

    def test_who_wrote_it_decides_the_content_part_type(self):
        """An assistant turn sent as input_text is rejected outright."""
        _instr, items = build_responses_input([
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ])
        self.assertEqual(items[0]["content"][0]["type"], "input_text")
        self.assertEqual(items[1]["content"][0]["type"], "output_text")

    def test_a_tool_call_becomes_its_own_item_after_its_message(self):
        _instr, items = build_responses_input([
            {"role": "assistant", "content": "calling",
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "ls",
                                          "arguments": '{"p":"/"}'}}]},
        ])
        self.assertEqual([i["type"] for i in items],
                         ["message", "function_call"])
        self.assertEqual(items[1]["call_id"], "call_1")
        self.assertEqual(items[1]["name"], "ls")

    def test_a_tool_result_stops_being_a_message(self):
        """It is addressed by call id, not by position in a role: tool row."""
        _instr, items = build_responses_input([
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ])
        self.assertEqual(items, [{"type": "function_call_output",
                                  "call_id": "call_1", "output": "ok"}])

    def test_an_argumentless_call_still_carries_valid_json(self):
        _instr, items = build_responses_input([
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c", "function": {"name": "now",
                                                    "arguments": ""}}]},
        ])
        self.assertEqual(items[0]["arguments"], "{}")


class StreamEvents(unittest.TestCase):
    """Semantic SSE: typed events, and no [DONE] to stop on."""

    def test_text_and_reasoning_accumulate_separately(self):
        st = _StreamState()
        st.feed({"type": "response.reasoning_text.delta", "delta": "think"})
        st.feed({"type": "response.output_text.delta", "delta": "he"})
        st.feed({"type": "response.output_text.delta", "delta": "llo"})
        self.assertEqual("".join(st.text), "hello")
        self.assertEqual("".join(st.reasoning), "think")

    def test_reasoning_summary_deltas_are_visible_reasoning(self):
        st = _StreamState()
        st.feed({"type": "response.reasoning_summary_text.delta",
                 "delta": "summary"})
        self.assertEqual("".join(st.reasoning), "summary")

    def test_interleaved_calls_are_keyed_by_item_not_by_index(self):
        """Parallel tool calling is always on, so an index is not an identity."""
        st = _StreamState()
        st.feed({"type": "response.output_item.added", "item_id": "a",
                 "item": {"type": "function_call", "id": "a",
                          "call_id": "c1", "name": "one"}})
        st.feed({"type": "response.output_item.added", "item_id": "b",
                 "item": {"type": "function_call", "id": "b",
                          "call_id": "c2", "name": "two"}})
        st.feed({"type": "response.function_call_arguments.delta",
                 "item_id": "a", "delta": '{"x":'})
        st.feed({"type": "response.function_call_arguments.delta",
                 "item_id": "b", "delta": '{"y":2}'})
        st.feed({"type": "response.function_call_arguments.delta",
                 "item_id": "a", "delta": "1}"})
        self.assertEqual(st.calls["a"]["arguments"], '{"x":1}')
        self.assertEqual(st.calls["b"]["arguments"], '{"y":2}')
        self.assertEqual(st.calls["a"]["name"], "one")

    def test_the_done_item_overrides_the_accumulated_arguments(self):
        st = _StreamState()
        st.feed({"type": "response.function_call_arguments.delta",
                 "item_id": "a", "delta": '{"trunc'})
        st.feed({"type": "response.output_item.done", "item_id": "a",
                 "item": {"type": "function_call", "id": "a", "call_id": "c",
                          "name": "f", "arguments": '{"whole":1}'}})
        self.assertEqual(st.calls["a"]["arguments"], '{"whole":1}')

    def test_a_non_function_output_item_is_not_a_tool_call(self):
        st = _StreamState()
        st.feed({"type": "response.output_item.added", "item_id": "r",
                 "item": {"type": "reasoning", "id": "r"}})
        self.assertEqual(st.calls, {})

    def test_the_terminal_event_carries_usage(self):
        st = _StreamState()
        st.feed({"type": "response.completed", "response": {
            "status": "completed", "model": "deepseek-v4-flash",
            "usage": {"input_tokens": 100, "output_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 40}}}})
        self.assertEqual(st.status, "completed")
        self.assertEqual(st.model, "deepseek-v4-flash")
        self.assertEqual(st.usage["input_tokens"], 100)
        self.assertEqual(st.terminal_event, "response.completed")

    def test_a_failed_response_keeps_its_reason(self):
        st = _StreamState()
        st.feed({"type": "response.failed", "response": {
            "status": "failed", "error": {"message": "context too long"}}})
        self.assertEqual(st.status, "failed")
        self.assertEqual(st.error, "context too long")


class _FakeResponse:
    """An HTTP response replaying a canned SSE body in small reads."""

    def __init__(self, body, status=200):
        self.status = status
        self.reason = "OK"
        self._data = body.encode("utf-8")
        self._pos = 0

    def read(self, n=None):
        if n is None:
            n = len(self._data)
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeConn:
    last_body = None

    def __init__(self, response):
        self._response = response

    def request(self, method, path, body=None, headers=None):
        _FakeConn.last_body = json.loads(body.decode("utf-8"))
        _FakeConn.last_path = path

    def getresponse(self):
        return self._response

    def close(self):
        pass


def _sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n"
                   for e in events)


class EndToEnd(unittest.TestCase):

    def _client(self):
        return LLMClient(provider="openai-responses", config={
            "api_key": "k", "base_url": "https://api.deepseek.com",
            "default_model": "deepseek-v4-flash"})

    def _run(self, client, body, messages=None, tools=None, callback=None):
        import core.llm_providers.openai_responses as mod
        conn = _FakeConn(_FakeResponse(body))
        orig = mod.http.client.HTTPSConnection
        mod.http.client.HTTPSConnection = lambda *a, **k: conn
        try:
            return client._stream_openai_responses(
                messages or [LLMMessage(role="user", content="hi",
                                        conversation_id="c1")],
                "deepseek-v4-flash", 0.5, 100, tools, callback)
        finally:
            mod.http.client.HTTPSConnection = orig

    def test_a_stream_without_a_done_sentinel_still_terminates(self):
        """There is no `data: [DONE]`; the terminal event is the end.

        A chat/completions reader waits for a sentinel that never arrives.
        """
        result = self._run(self._client(), _sse(
            {"type": "response.created", "response": {"status": "in_progress"}},
            {"type": "response.output_text.delta", "delta": "hel"},
            {"type": "response.output_text.delta", "delta": "lo"},
            {"type": "response.completed", "response": {
                "status": "completed", "model": "deepseek-v4-flash",
                "usage": {"input_tokens": 30, "output_tokens": 5}}},
        ))
        self.assertEqual(result.content, "hello")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.tokens_in, 30)
        self.assertEqual(result.tokens_out, 5)

    def test_the_request_is_input_items_not_messages(self):
        client = self._client()
        self._run(client, _sse({"type": "response.completed",
                                "response": {"status": "completed"}}))
        body = _FakeConn.last_body
        self.assertIn("input", body)
        self.assertNotIn("messages", body)
        self.assertTrue(body["stream"])
        self.assertEqual(body["max_output_tokens"], 100)
        self.assertNotIn("max_tokens", body)
        self.assertEqual(_FakeConn.last_path, "/v1/responses")

    def test_tools_are_declared_flat(self):
        class _T:
            name = "ls"
            description = "list"
            parameters = {"type": "object", "properties": {}}

        client = self._client()
        self._run(client, _sse({"type": "response.completed",
                               "response": {"status": "completed"}}),
                  tools=[_T()])
        tool = _FakeConn.last_body["tools"][0]
        self.assertEqual(tool["name"], "ls")
        self.assertNotIn("function", tool), "the chat/completions envelope is back"

    def test_a_streamed_tool_call_comes_back_parsed(self):
        result = self._run(self._client(), _sse(
            {"type": "response.output_item.added", "item_id": "i1",
             "item": {"type": "function_call", "id": "i1",
                      "call_id": "call_9", "name": "ls"}},
            {"type": "response.function_call_arguments.delta",
             "item_id": "i1", "delta": '{"path":"/tmp"}'},
            {"type": "response.completed", "response": {"status": "completed"}},
        ))
        self.assertEqual(len(result.tool_calls), 1)
        call = result.tool_calls[0]
        self.assertEqual(call.id, "call_9")
        self.assertEqual(call.name, "ls")
        self.assertEqual(call.arguments, {"path": "/tmp"})
        self.assertEqual(result.finish_reason, "tool_calls")

    def test_a_cache_hit_is_not_billed_as_a_miss(self):
        result = self._run(self._client(), _sse(
            {"type": "response.completed", "response": {
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 10,
                          "input_tokens_details": {"cached_tokens": 60}}}},
        ))
        self.assertEqual(result.cache_read_tokens, 60)
        self.assertEqual(result.tokens_in, 40)

    def test_a_truncated_response_reports_a_length_stop(self):
        result = self._run(self._client(), _sse(
            {"type": "response.output_text.delta", "delta": "partial"},
            {"type": "response.incomplete", "response": {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"}}},
        ))
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(result.content, "partial")

    def test_a_failed_response_raises_rather_than_returning_empty(self):
        from core.llm_client import LLMClientError
        with self.assertRaises(LLMClientError) as ctx:
            self._run(self._client(), _sse(
                {"type": "response.failed", "response": {
                    "status": "failed",
                    "error": {"message": "context too long"}}}))
        self.assertIn("context too long", str(ctx.exception))

    def test_eof_before_a_terminal_event_is_an_error(self):
        from core.llm_client import LLMClientError
        with self.assertRaises(LLMClientError) as ctx:
            self._run(self._client(), _sse(
                {"type": "response.output_text.delta",
                 "delta": "partial"}))
        self.assertIn("terminal event", str(ctx.exception))

    def test_the_text_callback_fires_once_for_the_whole_block(self):
        seen = []
        self._run(self._client(), _sse(
            {"type": "response.output_text.delta", "delta": "a"},
            {"type": "response.output_text.delta", "delta": "b"},
            {"type": "response.completed", "response": {"status": "completed"}},
        ), callback=seen.append)
        self.assertEqual(seen, ["ab"])


class Registration(unittest.TestCase):

    def test_the_provider_is_selectable_and_dispatched(self):
        from core._llm_client_driver import (
            OPENAI_WIRE_PROVIDERS, RESPONSES_WIRE_PROVIDERS)

        self.assertIn("openai-responses", LLMClient.PROVIDERS)
        self.assertIn("openai-responses", RESPONSES_WIRE_PROVIDERS)
        # It must NOT ride the chat/completions branch: same host, different
        # endpoint, incompatible payload.
        self.assertNotIn("openai-responses", OPENAI_WIRE_PROVIDERS)

    def test_the_service_offers_it_with_the_openai_field_set(self):
        from services.llm_connection import LLMConnectionService

        svc = LLMConnectionService({"provider": "openai-responses",
                                    "api_key": "k"})
        schema = svc.get_parameter_schema()
        self.assertIn("openai-responses", schema["provider"]["options"])
        shown = [r for r in svc.get_parameter_rules()
                 if "openai-responses" in (r.get("when") or {}).get("provider", [])]
        self.assertTrue(shown, "no rule makes the API fields visible")
        self.assertTrue(any("base_url" in r["set"] for r in shown))


if __name__ == "__main__":
    unittest.main()
