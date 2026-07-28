"""Recovery of wrapper calls whose streamed tool input was lost.

Taken verbatim from a production turn (2026-07-28 08:31:05): the streamed
``input_json_delta`` chunks arrived incomplete, so the accumulated JSON had
no ``tool_name``, the MCP wrapper stayed un-unwrapped and the call rendered
as a bare ``mcp__pawflow__use_tool`` — which ``has_complete_mcp_tool_call``
then drops, so it never reached the conversation. The request-body replay
that follows carries the complete input and must supersede it.
"""

import pytest

from core.llm_providers.claude_code_interactive import _CCITurnCoordinator


def _stop():
    return {"type": "hook", "hook_event_name": "Stop",
            "input": {"hook_event_name": "Stop"}}


class _Events:
    def __init__(self, rows):
        self.rows = list(rows)

    def wait_event(self, session_token, timeout=None):
        return self.rows.pop(0) if self.rows else {}


def _run(rows):
    blocks = []
    coord = _CCITurnCoordinator(
        _Events(rows), "sess",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))
    coord.run()
    return coord, blocks


def _wrapper_block_rows(truncated_json: str):
    return [
        {"type": "sse", "event": "content_block_start", "payload": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_lost",
                              "name": "mcp__pawflow__use_tool"}}},
        {"type": "sse", "event": "content_block_delta", "payload": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta",
                      "partial_json": truncated_json}}},
        {"type": "sse", "event": "content_block_stop", "payload": {
            "type": "content_block_stop", "index": 0}},
    ]


def test_observed_request_body_supersedes_unwrappable_stream_emit():
    # Shape observed at 08:30:39: the leading chunk carrying "tool_name" was
    # lost, so the wrapper cannot be unwrapped at all and the call is dropped
    # downstream — nothing was persisted, so re-emitting cannot duplicate.
    full_args = {"path": "/workspace/tasks/ai/agent_streaming.py",
                 "offset": 120, "limit": 50}
    rows = _wrapper_block_rows(
        '", "arguments_json": "{\\"path\\":\\"/workspace/tasks/ai/'
        'agent_streaming.py\\",\\"offset\\":120,\\"limit\\":50}')
    rows += [
        {"type": "tool_use", "tool_use_id": "toolu_lost", "name": "read",
         "arguments": dict(full_args)},
        _stop(),
    ]

    coord, blocks = _run(rows)

    persisted = [tc for tc in coord.turn_tool_calls
                 if tc.get("id") == "toolu_lost"]
    assert len(persisted) == 1
    assert persisted[0]["name"] == "read"
    assert persisted[0]["arguments"] == full_args
    emitted = [p for kind, p in blocks if kind == "tool_use"]
    assert emitted[-1]["name"] == "read"
    assert emitted[-1]["arguments"] == full_args
    assert emitted[-1]["tool_origin"] == "mcp"


def test_resolved_call_is_never_superseded_twice():
    # A call whose name DID resolve was persisted downstream; re-emitting it
    # from the request body would show the same call twice.
    args = {"command": "ls"}
    rows = [
        {"type": "sse", "event": "content_block_start", "payload": {
            "type": "content_block_start", "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_ok",
                              "name": "mcp__pawflow__use_tool"}}},
        {"type": "sse", "event": "content_block_delta", "payload": {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": (
                '{"tool_name": "bash", "arguments_json": '
                '"{\\"command\\":\\"ls\\"}"}')}}},
        {"type": "sse", "event": "content_block_stop", "payload": {
            "type": "content_block_stop", "index": 0}},
        {"type": "tool_use", "tool_use_id": "toolu_ok", "name": "bash",
         "arguments": dict(args)},
        _stop(),
    ]

    coord, blocks = _run(rows)

    emitted = [p for kind, p in blocks if kind == "tool_use"]
    assert len(emitted) == 1
    assert emitted[0]["name"] == "bash"
    assert emitted[0]["arguments"] == args


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Residual case (seen at 08:31:05): enough of the stream survived to "
        "resolve the NAME ('read') but the inner arguments_json stayed "
        "truncated, so the call was emitted — and persisted — with empty "
        "args. The request body that follows holds the complete input, but "
        "re-emitting it would show the call twice unless the tool_call SSE "
        "event upserts by tc_id. Remove this xfail once that is settled."
    ),
)
def test_observed_supersedes_resolved_name_with_empty_args():
    full_args = {"path": "/workspace/tasks/ai/agent_streaming.py",
                 "offset": 492, "limit": 60}
    rows = _wrapper_block_rows(
        '{"tool_name": "read", "arguments_json": '
        '"{\\"path\\":\\"/workspace/tasks/ai/agent_streaming.py\\",\\"off')
    rows += [
        {"type": "tool_use", "tool_use_id": "toolu_lost", "name": "read",
         "arguments": dict(full_args)},
        _stop(),
    ]

    coord, _blocks = _run(rows)

    persisted = [tc for tc in coord.turn_tool_calls
                 if tc.get("id") == "toolu_lost"]
    assert persisted[0]["arguments"] == full_args


def test_empty_observed_input_does_not_clobber_a_lost_call():
    # The replays seen alongside the corrupt stream often carry {} — they
    # must not be mistaken for a recovery.
    rows = _wrapper_block_rows('{"tool_name": "read", "argu')
    rows += [
        {"type": "tool_use", "tool_use_id": "toolu_lost", "name": "read",
         "arguments": {}},
        _stop(),
    ]

    coord, blocks = _run(rows)

    emitted = [p for kind, p in blocks if kind == "tool_use"]
    assert len(emitted) == 1
    assert emitted[0]["arguments"] == {}
