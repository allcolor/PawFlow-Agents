"""Managed MCP: the native-final turn coordinator (WP2).

A fake event service feeds the coordinator exactly what a managed session
receives: lifecycle hooks and relay tool rows. Never vendor traffic.
"""
import queue
import threading
import time

import pytest

from core._llm_types import LLMCallError
from core.llm_client import CCCompactDetected
from core.llm_providers._managed_mcp_turn import _ManagedMcpTurnCoordinator
from tools import cc_interactive_hook


class FakeEventService:
    """Queue-backed stand-in for CCInteractiveEventService."""

    def __init__(self, events=(), refuse_claim=False):
        self.q = queue.Queue()
        for event in events:
            self.q.put(event)
        self.epoch = 0
        self.refuse_claim = refuse_claim
        self.claims = []

    def claim_consumer(self, session_token, *, kind="request"):
        self.claims.append(kind)
        if self.refuse_claim:
            return 0
        self.epoch += 1
        return self.epoch

    def wait_event(self, session_token, timeout=None, epoch=0):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return {}

    def session_state(self, session_token):
        return None


def _hook(name, **fields):
    return {"type": "hook", "hook_event_name": name, "input": fields,
            "timestamp": time.time()}


def _coord(events, **kwargs):
    svc = FakeEventService(events, refuse_claim=kwargs.pop("refuse", False))
    calls = {"text": [], "blocks": [], "turn": []}
    coord = _ManagedMcpTurnCoordinator(
        svc, "sess", provider=kwargs.pop("provider", "cc_mcp"),
        callback=calls["text"].append,
        block_callback=lambda kind, payload: calls["blocks"].append((kind, payload)),
        turn_callback=lambda text, tools, thinking: calls["turn"].append((text, tools, thinking)),
        **kwargs)
    return coord, calls, svc


class TestFinal:
    def test_stop_final_completes_once(self):
        coord, calls, _ = _coord([
            _hook("UserPromptSubmit", pawflow_injected_prompt=True),
            _hook("Stop", last_assistant_message="final text",
                  final_source="hook_field", model="claude-opus-4"),
        ])
        response = coord.run()
        assert response.content == "final text"
        assert response.tool_calls == []
        assert response.model == "claude-opus-4"
        assert response.raw["provider"] == "cc_mcp"
        assert response.raw["telemetry"]["final_source"] == "hook_field"
        assert response.raw["telemetry"]["usage"] == "unavailable"
        assert response.raw["telemetry"]["context"] == "unavailable"
        assert response.tokens_in == 0 and response.tokens_out == 0
        assert calls["text"] == ["final text"]
        assert [b for b in calls["blocks"] if b[0] == "text"] == [
            ("text", {"text": "final text"})]
        assert coord.prompt_submitted is True

    def test_agy_native_final_model_output_reaches_response(self):
        info = cc_interactive_hook._compact_input(
            cc_interactive_hook._normalize_client_input({
                "hookEventName": "Stop",
                "finalModelOutput": "native agy final",
                "terminationReason": "STOP",
            }, "agy"))
        hook_name = info.pop("hook_event_name")
        coord, calls, _ = _coord([
            _hook("UserPromptSubmit", pawflow_injected_prompt=True),
            _hook(hook_name, **info),
        ], provider="agy_mcp")

        response = coord.run()

        assert response.content == "native agy final"
        assert response.raw["provider"] == "agy_mcp"
        assert response.raw["telemetry"]["final_source"] == "hook_field"
        assert response.raw["lifecycle_events"][-1]["input"]["reason"] == "STOP"
        assert calls["text"] == ["native agy final"]

    def test_turn_callback_without_block_callback_gets_text(self):
        svc = FakeEventService([_hook("Stop", last_assistant_message="t")])
        turns = []
        coord = _ManagedMcpTurnCoordinator(
            svc, "sess", provider="codex_mcp",
            turn_callback=lambda text, tools, thinking: turns.append(text))
        response = coord.run()
        assert response.content == "t"
        assert turns == ["t"]
        assert response.raw["telemetry"]["usage"] == "codex_rollout_token_count"

    def test_stale_stop_from_previous_turn_is_ignored(self):
        started = time.time()
        stale = _hook("Stop", last_assistant_message="old answer")
        stale["timestamp"] = started - 30
        fresh = _hook("Stop", last_assistant_message="new answer")
        coord, calls, _ = _coord([stale, fresh], started_at=started)
        assert coord.run().content == "new answer"
        assert calls["text"] == ["new answer"]

    def test_duplicate_final_after_completion_is_left_for_next_turn(self):
        coord, calls, svc = _coord([
            _hook("Stop", last_assistant_message="one"),
        ])
        coord.run()
        # A late duplicate lands after the turn: nothing consumes it here.
        svc.q.put(_hook("Stop", last_assistant_message="dup"))
        assert calls["text"] == ["one"]
        assert svc.q.qsize() == 1

    def test_no_extractable_final_is_typed_and_not_retryable(self):
        coord, _, _ = _coord([_hook("Stop", last_assistant_message="   ")])
        with pytest.raises(LLMCallError) as exc:
            coord.run()
        assert exc.value.retryable is False
        assert exc.value.provider == "cc_mcp"
        assert "no extractable final" in str(exc.value)

    def test_consumer_refused_yields_empty_response(self):
        coord, calls, svc = _coord([_hook("Stop", last_assistant_message="x")],
                                   refuse=True)
        response = coord.run()
        assert response.content == ""
        assert calls["text"] == []
        assert svc.q.qsize() == 1


class TestFailures:
    def test_stop_failure_is_typed(self):
        coord, _, _ = _coord([_hook("StopFailure", error="429 rate limit")])
        with pytest.raises(LLMCallError) as exc:
            coord.run()
        assert exc.value.category == "rate_limited"
        assert exc.value.retryable is False

    def test_session_end_before_final_is_typed(self):
        coord, _, _ = _coord([_hook("SessionEnd", reason="exit")])
        with pytest.raises(LLMCallError) as exc:
            coord.run()
        assert "session ended" in str(exc.value)
        assert exc.value.retryable is False

    def test_compaction_hook_hands_context_to_pawflow(self):
        coord, _, _ = _coord([_hook("PreCompact")])
        with pytest.raises(CCCompactDetected):
            coord.run()

    def test_final_deadline_is_typed_timeout(self):
        coord, _, _ = _coord([], final_timeout=0.2)
        with pytest.raises(LLMCallError) as exc:
            coord.run()
        assert exc.value.category == "timeout"
        assert exc.value.retryable is False

    def test_abort_stops_waiting(self):
        abort = threading.Event()
        coord, _, _ = _coord([])
        threading.Timer(0.1, abort.set).start()
        with pytest.raises(RuntimeError, match="aborted"):
            coord.run(abort)

    def test_dead_session_fails_the_turn(self, monkeypatch):
        import core.llm_providers._cci_turn as cci_turn
        monkeypatch.setattr(cci_turn, "_LIVENESS_PROBE_IDLE_SECONDS", 0.0)
        coord, _, _ = _coord([], liveness_callback=lambda: False)
        with pytest.raises(RuntimeError, match="died mid-turn"):
            coord.run()


class TestToolRows:
    def test_relay_tool_rows_are_mirrored_once_and_not_executed(self):
        shared_use, shared_result = set(), set()
        events = [
            {"type": "tool_use", "tool_use_id": "t1", "name": "read",
             "arguments": {"path": "a"}, "tool_origin": "mcp"},
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            # The relay may observe the same call twice.
            {"type": "tool_use", "tool_use_id": "t1", "name": "read",
             "arguments": {"path": "a"}, "tool_origin": "mcp"},
            _hook("Stop", last_assistant_message="done"),
        ]
        coord, calls, _ = _coord(events, emitted_tool_use_ids=shared_use,
                                 emitted_tool_result_ids=shared_result)
        response = coord.run()
        uses = [b for b in calls["blocks"] if b[0] == "tool_use"]
        results = [b for b in calls["blocks"] if b[0] == "tool_result"]
        assert len(uses) == 1 and uses[0][1]["id"] == "t1"
        assert len(results) == 1 and results[0][1]["result"] == "ok"
        # The agent loop must never run these again.
        assert response.tool_calls == []
        assert shared_use == {"t1"} and shared_result == {"t1"}

    def test_vendor_events_are_ignored_never_assembled(self):
        events = [
            {"type": "sse", "event": "content_block_delta", "payload": {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "leaked"}}},
            {"type": "request_start", "request_id": "r", "path": "/v1/messages"},
            _hook("Stop", last_assistant_message="hook final"),
        ]
        coord, calls, _ = _coord(events)
        assert coord.run().content == "hook final"
        assert calls["text"] == ["hook final"]

    def test_post_stop_tool_row_race_is_absorbed(self):
        events = [
            _hook("Stop", last_assistant_message="final"),
            {"type": "tool_result", "tool_use_id": "late", "content": "x"},
        ]
        coord, _calls, svc = _coord(events)
        coord.run()
        assert svc.q.qsize() == 0
