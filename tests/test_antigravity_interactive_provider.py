import inspect
import json
from pathlib import Path

from core.llm_client import LLMClient
from core.llm_providers.antigravity_interactive import _AntigravityTurnCoordinator


def test_antigravity_interactive_provider_registered_and_dispatched():
    assert "antigravity-interactive" in LLMClient.PROVIDERS
    assert LLMClient("antigravity-interactive").default_model == ""
    assert LLMClient("antigravity-interactive").supports_live_preempt is True
    assert hasattr(LLMClient, "_stream_antigravity_interactive")

    complete_src = inspect.getsource(LLMClient.complete)
    stream_src = inspect.getsource(LLMClient.complete_stream)
    send_src = inspect.getsource(LLMClient.send_user_message)
    abort_src = inspect.getsource(LLMClient.abort)
    assert '"antigravity-interactive"' in complete_src
    assert "_stream_antigravity_interactive" in stream_src
    assert "_agi_send_user_message" in send_src
    assert "cancel_antigravity_interactive" in abort_src


def test_antigravity_turn_coordinator_reads_jsonl_text_thinking_and_tools(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    events = [
        {"type": "proxy_start", "upstream_host": "daily-cloudcode-pa.googleapis.com"},
        {"type": "ag_text_delta", "text": "Hi ", "thinking": "plan"},
        {"type": "ag_text_delta", "text": "there", "tool_calls": [{
            "id": "ag_tool_1", "name": "read", "arguments": {"path": "a.py"},
        }]},
        {"type": "ag_text_delta", "tool_results": [{
            "tool_use_id": "ag_tool_1", "content": "ok",
        }]},
        {"type": "ag_text_delta", "done": True, "usage": {"input_tokens": 3, "output_tokens": 2}},
    ]
    log_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    seen = []
    thinking = []
    blocks = []
    turns = []
    response = _AntigravityTurnCoordinator(
        str(log_path), offset=0, callback=seen.append,
        thinking_callback=thinking.append,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
        turn_callback=lambda text, tool_calls, thinking="": turns.append((text, tool_calls, thinking)),
    ).run()

    assert response.content == "Hi there"
    assert response.thinking == "plan"
    assert seen == ["Hi ", "there"]
    assert thinking == ["plan"]
    assert response.tokens_in == 3
    assert response.tokens_out == 2
    assert ("text", {"text": "Hi there"}) in blocks
    assert ("tool_use", {
        "id": "ag_tool_1", "name": "read", "arguments": {"path": "a.py"},
        "tool_origin": "native",
    }) in blocks
    assert ("tool_result", {
        "tc_id": "ag_tool_1", "tool": "read", "result": "ok",
        "tool_origin": "native",
    }) in blocks
    assert turns == []


def test_antigravity_turn_coordinator_flushes_text_at_tool_boundaries(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    events = [
        {"type": "ag_text_delta", "text": "I will "},
        {"type": "ag_text_delta", "text": "inspect."},
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc1", "name": "list_dir", "arguments": {"DirectoryPath": "."},
        }]},
        {"type": "ag_text_delta", "tool_results": [{"name": "list_dir", "content": "a.py"}]},
        {"type": "ag_text_delta", "text": "Done."},
        {"type": "ag_text_delta", "done": True},
    ]
    log_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    blocks = []
    turns = []
    response = _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
        turn_callback=lambda text, tool_calls, thinking="": turns.append((text, tool_calls, thinking)),
    ).run()

    # content is the FINAL visible message (last segment), not the
    # whole-turn join — channel bridges relay it verbatim as the final
    # reply, while each segment is still published individually below.
    assert response.content == "Done."
    assert blocks == [
        ("text", {"text": "I will inspect."}),
        ("tool_use", {"id": "tc1", "name": "list_dir", "arguments": {"DirectoryPath": "."}, "tool_origin": "native"}),
        ("tool_result", {"tc_id": "tc1", "tool": "list_dir", "result": "a.py", "tool_origin": "native"}),
        ("text", {"text": "Done."}),
    ]
    assert turns == []


def test_antigravity_preempt_after_done_does_not_abandon_final_answer(monkeypatch, tmp_path):
    """Regression (mirror of the claude-code-interactive fix): a preempt that
    extends the turn past a Stop/done must not be cut off by a later idle gap.

    The model answers and signals done; a PawFlow preempt injects a new prompt
    (fresh request_start); the model churns on a large tool result (idle gap)
    before streaming the real final answer. The stale done latch used to trip
    the post-done drain during that gap, returning the already-delivered first
    answer and abandoning the final answer (tmux-only). A fresh request_start
    after done must clear the latch.
    """
    import core.llm_providers.antigravity_interactive as agi

    # Drain=0: any idle gap finishes the turn the instant the latch is set.
    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)

    class SequentialTail:
        def __init__(self, rows):
            self.rows = list(rows)

        def wait_event(self, timeout=0.25):
            if not self.rows:
                return {}
            return self.rows.pop(0)

    rows = [
        {"type": "ag_text_delta", "text": "first answer"},
        {"type": "ag_text_delta", "done": True},
        # Preempt injects a new prompt — a fresh request begins immediately.
        # This must clear the stale done latch.
        {"type": "request_start"},
        # Model churns on the (large) tool result: idle gaps with no events.
        {},
        {},
        {"type": "ag_text_delta", "text": "the real final answer"},
        {"type": "ag_text_delta", "done": True},
        {},
    ]

    (tmp_path / "observer.jsonl").write_text("", encoding="utf-8")
    flushed_text = []
    coord = _AntigravityTurnCoordinator(
        str(tmp_path / "observer.jsonl"), offset=0,
        block_callback=lambda kind, payload: (
            flushed_text.append(payload.get("text", "")) if kind == "text" else None),
    )
    coord.tail = SequentialTail(rows)
    response = coord.run()

    assert response.content == "the real final answer"
    assert "the real final answer" in flushed_text


def test_antigravity_turn_coordinator_matches_idless_function_response_by_name(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    events = [
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc-list", "name": "list_dir", "arguments": {"DirectoryPath": "."},
        }]},
        {"type": "ag_text_delta", "tool_results": [{
            "name": "list_dir", "content": "initial_context.md\nsettings.json",
        }]},
        {"type": "ag_text_delta", "done": True},
    ]
    log_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    blocks = []
    _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
    ).run()

    assert ("tool_result", {
        "tc_id": "tc-list",
        "tool": "list_dir",
        "result": "initial_context.md\nsettings.json",
        "tool_origin": "native",
    }) in blocks


def test_antigravity_turn_coordinator_does_not_persist_text_chunks_with_block_callback(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    events = [
        {"type": "ag_text_delta", "text": "Non, je n'ai pas "},
        {"type": "ag_text_delta", "text": "directement accès "},
        {"type": "ag_text_delta", "text": "aux outils MCP."},
        {"type": "ag_text_delta", "done": True},
    ]
    log_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    blocks = []
    turns = []
    response = _AntigravityTurnCoordinator(
        str(log_path), offset=0, callback=lambda _delta: None,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
        turn_callback=lambda text, tool_calls, thinking="": turns.append((text, tool_calls, thinking)),
    ).run()

    assert response.content == "Non, je n'ai pas directement accès aux outils MCP."
    assert blocks == [("text", {"text": "Non, je n'ai pas directement accès aux outils MCP."})]
    assert turns == []


def test_antigravity_turn_coordinator_finishes_text_after_idle_without_done(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_NO_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    log_path.write_text(
        json.dumps({"type": "ag_text_delta", "text": "visible in tmux"}) + "\n",
        encoding="utf-8",
    )

    response = _AntigravityTurnCoordinator(str(log_path), offset=0).run()

    assert response.content == "visible in tmux"


def test_antigravity_turn_coordinator_ignores_tool_step_stop(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_NO_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    events = [
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc1", "name": "view_file", "arguments": {"path": "initial_context.md"},
        }]},
        {"type": "ag_text_delta", "text": "", "finish_reason": "STOP"},
        {"type": "ag_text_delta", "text": "final answer"},
    ]
    log_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    response = _AntigravityTurnCoordinator(str(log_path), offset=0).run()

    assert response.content == "final answer"


def test_antigravity_final_content_is_last_segment_not_turn_join(monkeypatch, tmp_path):
    """content carries only the final visible message (last segment).

    Channel bridges (Telegram) relay LLMResponse.content verbatim as the
    final reply; joining the whole turn concatenated every intermediate
    narration above the final answer.
    """
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    events = [
        {"type": "ag_text_delta", "text": "Je lis les fichiers."},
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc1", "name": "read", "arguments": {"path": "a.md"},
        }]},
        {"type": "ag_text_delta", "tool_results": [{
            "tool_use_id": "tc1", "content": "ok",
        }]},
        {"type": "ag_text_delta", "text": "Voici la review finale.", "done": True},
    ]
    log_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    blocks = []
    response = _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
    ).run()

    assert response.content == "Voici la review finale."
    assert ("text", {"text": "Je lis les fichiers."}) in blocks


def test_antigravity_turn_coordinator_does_not_finish_on_text_stop_without_done(monkeypatch, tmp_path):
    import threading
    import time
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_NO_DONE_IDLE_DRAIN_SECONDS", 0.3)
    log_path = tmp_path / "observer.jsonl"
    log_path.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "ag_text_delta", "text": "first part"},
        {"type": "ag_text_delta", "finish_reason": "STOP"},
    ]), encoding="utf-8")

    def append_late_answer():
        time.sleep(0.1)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "ag_text_delta", "text": " second part", "done": True}) + "\n")

    threading.Thread(target=append_late_answer, daemon=True).start()
    response = _AntigravityTurnCoordinator(str(log_path), offset=0).run()

    # finish_reason flushes a segment boundary: the late continuation is
    # the final visible message, so content carries only that segment.
    assert response.content == " second part"


def test_antigravity_turn_coordinator_waits_for_tool_result_after_step_stop(monkeypatch, tmp_path):
    import threading
    import time
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_NO_DONE_IDLE_DRAIN_SECONDS", 0)
    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0.05)
    log_path = tmp_path / "observer.jsonl"
    log_path.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc1", "name": "pawflow/use_tool",
            "arguments": {"tool_name": "list_dir", "arguments": {"path": "."}},
            "tool_origin": "mcp",
        }]},
        {"type": "ag_text_delta", "finish_reason": "STOP"},
    ]), encoding="utf-8")

    def append_late_events():
        time.sleep(0.1)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "ag_text_delta", "tool_results": [{
                "name": "list_dir", "content": "a.py", "tool_origin": "mcp",
            }]}) + "\n")
            fh.write(json.dumps({"type": "ag_text_delta", "text": "done", "done": True}) + "\n")

    threading.Thread(target=append_late_events, daemon=True).start()
    blocks = []
    response = _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
    ).run()

    # The tool name is unwrapped for display (use_tool -> list_dir), so the
    # tool_result carries the same unwrapped name as its matching tool_use,
    # not the raw `pawflow/use_tool` wrapper.
    assert ("tool_result", {
        "tc_id": "tc1", "tool": "list_dir", "result": "a.py",
        "tool_origin": "mcp",
    }) in blocks
    assert response.content == "done"


def test_antigravity_turn_coordinator_unwraps_mcp_tool_call_for_display(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    log_path.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc1", "name": "bash",
            "arguments": {
                "tool_name": "bash",
                "arguments_json": '{"path": "/workspace", "command": "git status --short"}',
            },
            "tool_origin": "mcp",
        }]},
        {"type": "ag_text_delta", "done": True},
    ]), encoding="utf-8")

    blocks = []
    _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
    ).run()

    assert ("tool_use", {
        "id": "tc1",
        "name": "bash",
        "arguments": {"path": "/workspace", "command": "git status --short"},
        "tool_origin": "mcp",
    }) in blocks


def test_antigravity_turn_coordinator_reads_mcp_result_from_mitm_tool_result(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    log_path.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "tool_use",
            "tool_use_id": "tc1", "name": "pawflow/use_tool",
            "arguments": {"tool_name": "bash", "arguments": {"command": "git status"}},
            "tool_origin": "mcp",
        },
        {"type": "tool_result",
            "tool_use_id": "tc1", "name": "pawflow/use_tool",
            "content": "Created At: now\nCompleted At: now\nOn branch main\n",
            "tool_origin": "mcp",
        },
        {"type": "ag_text_delta", "finish_reason": "STOP"},
        {"type": "ag_text_delta", "done": True},
    ]), encoding="utf-8")

    blocks = []
    _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
    ).run()

    # Name unwrapped for display (use_tool -> bash), tool_origin preserved.
    assert ("tool_result", {
        "tc_id": "tc1",
        "tool": "bash",
        "result": "Created At: now\nCompleted At: now\nOn branch main\n",
        "tool_origin": "mcp",
    }) in blocks


def test_antigravity_turn_coordinator_exits_when_tmux_is_interrupted(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_NO_DONE_IDLE_DRAIN_SECONDS", 30)
    log_path = tmp_path / "observer.jsonl"
    log_path.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc1", "name": "pawflow/use_tool",
            "arguments": {"tool_name": "bash", "arguments": {"command": "sleep 60"}},
            "tool_origin": "mcp",
        }]},
        {"type": "ag_text_delta", "finish_reason": "STOP"},
    ]), encoding="utf-8")

    checks = []
    response = _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        interrupted_callback=lambda: checks.append(True) or True,
    ).run()

    assert checks
    assert response.content == ""


def test_antigravity_turn_coordinator_does_not_match_native_result_to_mcp_call(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    log_path.write_text("".join(json.dumps(e) + "\n" for e in [
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc1", "name": "pawflow/use_tool",
            "arguments": {"tool_name": "read", "arguments": {"path": "x"}},
            "tool_origin": "mcp",
        }]},
        {"type": "ag_text_delta", "tool_results": [{
            "name": "read", "content": "stale native", "tool_origin": "native",
        }]},
        {"type": "ag_text_delta", "done": True},
    ]), encoding="utf-8")

    blocks = []
    _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
    ).run()

    assert not [payload for kind, payload in blocks if kind == "tool_result"]


def test_antigravity_proxy_emits_incremental_sse_semantic_delta(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    resp = ag_observer_proxy.HTTP1Observer("conn1", "upstream_to_client")
    chunk = b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"hi"},{"functionCall":{"name":"read","args":{"path":"a.py"}}}]},"finishReason":"STOP"}]}}\n\n'
    resp.feed(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n"
    )

    deltas = [event for event in events if event.get("type") == "ag_text_delta"]
    assert deltas
    assert deltas[0]["text"] == "hi"
    assert deltas[0]["finish_reason"] == "STOP"
    assert deltas[0]["tool_calls"][0]["name"] == "read"
    assert deltas[0]["tool_calls"][0]["arguments"] == {"path": "a.py"}
    assert deltas[0]["tool_calls"][0]["tool_origin"] == "native"


def test_antigravity_proxy_emits_user_prompt_from_request_body(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    body = b'{"contents":[{"role":"user","parts":[{"text":"manual smoke"}]}]}'
    req = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    req.feed(
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )

    prompts = [event for event in events if event.get("type") == "ag_user_prompt"]
    assert prompts[-1]["text"] == "manual smoke"
    assert prompts[-1]["request_id"]


def test_antigravity_proxy_emits_tool_result_from_request_function_response(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    body = json.dumps({
        "request": {
            "contents": [{
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": "list_dir",
                        "response": {"content": "a.py\nb.py"},
                    },
                }],
            }],
        },
    }).encode()
    req = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    req.feed(
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )

    results = [event for event in events if event.get("type") == "tool_result"]
    assert results[-1] == {
        "type": "tool_result",
        "connection_id": "conn1",
        "direction": "client_to_upstream",
        "method": "POST",
        "path": "/v1internal:streamGenerateContent?alt=sse",
        "request_id": results[-1]["request_id"],
        "tool_use_id": results[-1]["tool_use_id"],
        "name": "list_dir",
        "content": "a.py\nb.py",
        "tool_origin": "native",
    }


def test_antigravity_proxy_emits_tool_result_after_large_request_prefix(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    body = json.dumps({
        "request": {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "x" * 300_000},
                    {"functionResponse": {
                        "name": "view_file",
                        "response": {"content": "late result"},
                    }},
                ],
            }],
        },
    }).encode()
    req = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    req.feed(
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )

    summaries = [event for event in events if event.get("type") == "http1_body_summary"]
    assert summaries[-1]["observed_body_bytes"] == len(body)
    results = [event for event in events if event.get("type") == "tool_result"]
    assert results[-1]["name"] == "view_file"
    assert results[-1]["content"] == "late result"
    assert results[-1]["tool_origin"] == "native"


def test_antigravity_proxy_emits_text_encoded_mcp_tool_result(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    body = json.dumps({
        "request": {
            "contents": [
                {"role": "model", "parts": [{"functionCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": "pawflow",
                        "ToolName": "use_tool",
                        "Arguments": {"tool_name": "bash", "arguments": {"command": "git status"}},
                    },
                }}]},
                {"role": "user", "parts": [{"text": "Created At: now\nCompleted At: now\nOn branch main\n"}]},
            ],
        },
    }).encode()
    req = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    req.feed(
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )

    results = [event for event in events if event.get("type") == "tool_result"]
    assert results[-1]["name"] == "pawflow/use_tool"
    assert results[-1]["content"] == "Created At: now\nCompleted At: now\nOn branch main\n"
    assert results[-1]["tool_origin"] == "mcp"


def test_antigravity_proxy_scans_latest_non_user_content_for_tool_result(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    body = json.dumps({
        "request": {
            "contents": [
                {"role": "user", "parts": [{"text": "old prompt"}]},
                {"role": "model", "parts": [{"functionResponse": {
                    "name": "call_mcp_tool",
                    "response": {"content": "Created At: now\nCompleted At: now\nerror\n"},
                    "ServerName": "pawflow",
                    "ToolName": "use_tool",
                }}]},
            ],
        },
    }).encode()
    req = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    req.feed(
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )

    results = [event for event in events if event.get("type") == "tool_result"]
    assert results[-1]["content"] == "Created At: now\nCompleted At: now\nerror\n"


def test_antigravity_proxy_keeps_mcp_function_response_without_server_fields(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    body = json.dumps({
        "request": {
            "contents": [{"role": "user", "parts": [{"functionResponse": {
                "name": "call_mcp_tool",
                "response": {"content": "Created At: now\nCompleted At: now\nunknown argument\n"},
            }}]}],
        },
    }).encode()
    req = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    req.feed(
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )

    results = [event for event in events if event.get("type") == "tool_result"]
    assert results[-1]["name"] == "pawflow/use_tool"
    assert results[-1]["content"] == "Created At: now\nCompleted At: now\nunknown argument\n"
    assert results[-1]["tool_origin"] == "mcp"


def test_antigravity_proxy_unwraps_call_mcp_tool_function_call(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    chunk = b'data: {"response":{"candidates":[{"content":{"parts":[{"functionCall":{"name":"call_mcp_tool","args":{"ServerName":"pawflow","ToolName":"get_tool_schema","Arguments":{"tool_name":"bash"}}}}]}}]}}\n\n'
    resp = ag_observer_proxy.HTTP1Observer("conn1", "upstream_to_client")
    resp.feed(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n"
    )

    deltas = [event for event in events if event.get("type") == "ag_text_delta"]
    assert deltas[-1]["tool_calls"][0]["name"] == "pawflow/get_tool_schema"
    assert deltas[-1]["tool_calls"][0]["arguments"] == {"tool_name": "bash"}
    assert deltas[-1]["tool_calls"][0]["tool_origin"] == "mcp"


def test_antigravity_proxy_skips_incomplete_mcp_tool_call(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)
    chunk = b'data: {"response":{"candidates":[{"content":{"parts":[{"functionCall":{"name":"call_mcp_tool","args":{"ServerName":"pawflow"}}}]}}]}}\n\n'
    resp = ag_observer_proxy.HTTP1Observer("conn1", "upstream_to_client")
    resp.feed(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n"
    )

    deltas = [event for event in events if event.get("type") == "ag_text_delta"]
    assert not deltas


def test_antigravity_turn_coordinator_preserves_tool_origin(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    events = [
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc-mcp", "name": "pawflow/read",
            "arguments": {"path": "a.py"}, "tool_origin": "mcp",
        }]},
        {"type": "ag_text_delta", "tool_results": [{
            "tool_use_id": "tc-mcp", "name": "pawflow/read",
            "content": "ok", "tool_origin": "mcp",
        }]},
        {"type": "ag_text_delta", "done": True},
    ]
    log_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    blocks = []
    _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
    ).run()

    # Name unwrapped for display (pawflow/read -> read), tool_origin preserved
    # (the actual subject of this test).
    assert ("tool_use", {
        "id": "tc-mcp", "name": "read",
        "arguments": {"path": "a.py"}, "tool_origin": "mcp",
    }) in blocks
    assert ("tool_result", {
        "tc_id": "tc-mcp", "tool": "read",
        "result": "ok", "tool_origin": "mcp",
    }) in blocks


def test_antigravity_turn_coordinator_defaults_observed_tool_origin_native(monkeypatch, tmp_path):
    import core.llm_providers.antigravity_interactive as agi

    monkeypatch.setattr(agi, "_POST_DONE_IDLE_DRAIN_SECONDS", 0)
    log_path = tmp_path / "observer.jsonl"
    events = [
        {"type": "ag_text_delta", "tool_calls": [{
            "id": "tc-native", "name": "view_file",
            "arguments": {"path": "a.py"},
        }]},
        {"type": "ag_text_delta", "tool_results": [{
            "tool_use_id": "tc-native", "name": "view_file", "content": "ok",
        }]},
        {"type": "ag_text_delta", "done": True},
    ]
    log_path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    blocks = []
    _AntigravityTurnCoordinator(
        str(log_path), offset=0,
        block_callback=lambda kind, payload: blocks.append((kind, payload)),
    ).run()

    assert ("tool_use", {
        "id": "tc-native", "name": "view_file",
        "arguments": {"path": "a.py"}, "tool_origin": "native",
    }) in blocks
    assert ("tool_result", {
        "tc_id": "tc-native", "tool": "view_file",
        "result": "ok", "tool_origin": "native",
    }) in blocks


def test_antigravity_initial_context_uses_ag_file(tmp_path):
    client = LLMClient("antigravity-interactive")
    from core.llm_client import LLMMessage

    msg = LLMMessage(role="user", content="hello", conversation_id="conv")
    prompt = client._agi_prompt(
        [msg], [], str(tmp_path), "/cc_sessions/conv/agent",
        "u", "conv", initial_context=True, agent_name="agent")

    assert ".pawflow_ag/initial_context.md" in prompt
    assert "Latest turn to answer now:" in prompt
    assert "hello" in prompt
    assert (tmp_path / ".pawflow_ag" / "initial_context.md").is_file()
    initial_context = (tmp_path / ".pawflow_ag" / "initial_context.md").read_text(
        encoding="utf-8")
    assert "## Latest User Request" in initial_context
    assert "hello" in initial_context


def test_antigravity_prompt_materializes_image_ref_as_at_path(tmp_path, monkeypatch):
    from core.llm_client import LLMMessage

    class _Store:
        def get_required(self, file_id, user_id, conversation_id):
            assert (file_id, user_id, conversation_id) == ("img1", "u", "conv")
            return "sample.png", b"PNG", "image/png"

    monkeypatch.setattr("core.file_store.FileStore.instance", staticmethod(lambda: _Store()))

    client = LLMClient("antigravity-interactive")
    msg = LLMMessage(
        role="user",
        conversation_id="conv",
        content=[
            {"type": "text", "text": "describe"},
            {"type": "image_ref", "file_id": "img1", "filename": "sample.png"},
        ],
    )

    prompt = client._agi_prompt([msg], None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv")

    assert "Attachments:\nfs://filestore/img1/sample.png -> @/cc_sessions/u/conv/a/.pawflow_vision/img1.png" in prompt
    assert "describe" in prompt
    assert (tmp_path / ".pawflow_vision" / "img1.png").read_bytes() == b"PNG"


def test_antigravity_prompt_materializes_inline_image_url_as_at_path(tmp_path):
    from core.llm_client import LLMMessage

    client = LLMClient("antigravity-interactive")
    msg = LLMMessage(
        role="user",
        conversation_id="conv",
        content=[
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,UE5H"}},
        ],
    )

    prompt = client._agi_prompt([msg], None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv")

    assert "Attachments:\n@/cc_sessions/u/conv/a/.pawflow_vision/inline_" in prompt
    image_files = list((tmp_path / ".pawflow_vision").glob("inline_*.png"))
    assert len(image_files) == 1
    assert image_files[0].read_bytes() == b"PNG"


def test_antigravity_live_preempt_materializes_image_attachment(tmp_path, monkeypatch):
    class _Store:
        def get_required(self, file_id, user_id, conversation_id):
            assert (file_id, user_id, conversation_id) == ("img1", "u", "conv")
            return "sample.png", b"PNG", "image/png"

    class _State:
        workdir = str(tmp_path)
        container_workdir = "/cc_sessions/u/conv/a"

    class _Pool:
        def __init__(self):
            self.sent = []

        def find_session(self, *args, **kwargs):
            return _State()

        def send_interrupt(self, state, text):
            self.sent.append(text)
            return True

    pool = _Pool()
    monkeypatch.setattr("core.file_store.FileStore.instance", staticmethod(lambda: _Store()))
    monkeypatch.setattr(
        "core.llm_providers.antigravity_interactive.AntigravityObserverPool.instance",
        staticmethod(lambda: pool),
    )

    client = LLMClient("antigravity-interactive")

    assert client._agi_send_user_message(
        "look at this",
        attachments=[{"file_id": "img1", "filename": "sample.png", "mime_type": "image/png"}],
        user_id="u",
        conversation_id="conv",
        agent_name="a",
    ) is True

    assert "Attachments:\nfs://filestore/img1/sample.png -> @/cc_sessions/u/conv/a/.pawflow_vision/img1.png" in pool.sent[0]
    assert "look at this" in pool.sent[0]
    assert (tmp_path / ".pawflow_vision" / "img1.png").read_bytes() == b"PNG"


def test_antigravity_pool_pastes_multiline_prompts_atomically(monkeypatch):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession
    import core.antigravity_observer_pool as pool_mod

    pool = AntigravityObserverPool()
    state = AntigravityObserverSession(
        key=("u", "conv", "agent", "svc"), name="container",
        workdir="/tmp", container_workdir="/cc_sessions/conv/agent",
        log_path="/tmp/observer.jsonl")
    events = []

    monkeypatch.setattr(pool, "_is_alive", lambda name: True)
    monkeypatch.setattr(pool_mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(pool, "_load_buffer", lambda _state, text: events.append(("load", text)) or True)
    monkeypatch.setattr(pool, "_paste_buffer", lambda _state: events.append(("paste", None)) or True)
    monkeypatch.setattr(pool, "send_keys", lambda _state, keys: events.append(("keys", keys)) or True)

    assert pool.send_text(state, "line 1\nline 2\nline 3") is True
    assert events == [
        ("load", "line 1\nline 2\nline 3"),
        ("paste", None),
        ("keys", ["Enter"]),
    ]


def test_antigravity_literal_chunk_uses_tmux_buffer_not_shell_arg(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    pool = AntigravityObserverPool()
    state = AntigravityObserverSession(
        key=("u", "conv", "agent", "svc"), name="container",
        workdir=str(tmp_path), container_workdir="/cc_sessions/conv/agent",
        log_path=str(tmp_path / "observer.jsonl"))
    calls = []
    pool._load_buffer = lambda _state, text: calls.append(("load", text)) or True
    pool._paste_buffer = lambda _state: calls.append(("paste", None)) or True

    assert pool._send_literal_chunk(state, "</message>") is True
    assert calls == [("load", "</message>"), ("paste", None)]


def test_antigravity_pool_uses_agent_service_for_session_key(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    client = LLMClient("antigravity-interactive", config={"provider": "antigravity-interactive"})
    client._agent_service = "agy_llm_service"
    calls = []

    def fake_start_new(self, user_id, conversation_id, agent_name, service_id, model, client=None):
        calls.append((service_id, getattr(client, "_agent_service", "")))
        return AntigravityObserverSession(
            key=(user_id, conversation_id, agent_name, service_id),
            name="container", workdir=str(tmp_path),
            container_workdir="/cc_sessions/conv/agent",
            log_path=str(tmp_path / "observer.jsonl"),
        )

    monkeypatch.setattr(AntigravityObserverPool, "_start_new", fake_start_new)
    pool = AntigravityObserverPool()
    pool.ensure_started(client, "", "u", "conv", "agent")

    assert calls == [("agy_llm_service", "agy_llm_service")]
    assert client._agent_service == "agy_llm_service"


def test_antigravity_provider_suspends_manual_ingest_while_consuming_log():
    import inspect
    from core.llm_client import LLMClient

    src = inspect.getsource(LLMClient._stream_antigravity_interactive)
    assert "pool.suspend_manual_ingest(state)" in src
    assert "pool.resume_manual_ingest(state)" in src


def test_antigravity_start_new_does_not_mutate_client_agent_service(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool

    class _Client:
        _agent_service = "agy_llm_service"
        _user_id = ""
        _agent_name = ""

        def _gemini_setup_credentials(self, workdir):
            assert self._agent_service == "agy_llm_service"

        def _gemini_acp_mcp_servers(self, user_id, conversation_id, agent_name):
            return {}, ""

        def _gemini_acp_write_settings(self, *args, **kwargs):
            return None

        def _gemini_acp_settings_mcp_servers(self, *args, **kwargs):
            return {}

    pool = AntigravityObserverPool()
    monkeypatch.setattr(pool, "_workdir", lambda *args: str(tmp_path))
    monkeypatch.setattr(pool, "_spawn_container", lambda **kwargs: "container")
    monkeypatch.setattr(pool, "_install_ca", lambda *args, **kwargs: None)
    monkeypatch.setattr(pool, "_start_proxy", lambda *args, **kwargs: None)
    monkeypatch.setattr(pool, "_start_agy_tmux", lambda *args, **kwargs: None)

    client = _Client()
    state = pool._start_new("u", "conv", "agent", "agy_llm_service", "", client=client)

    assert state.service_id == "agy_llm_service"
    assert client._agent_service == "agy_llm_service"

