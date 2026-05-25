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
    assert ("tool_use", {"id": "ag_tool_1", "name": "read", "arguments": {"path": "a.py"}}) in blocks
    assert ("tool_result", {"tc_id": "ag_tool_1", "tool": "read", "result": "ok"}) in blocks
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

    assert response.content == "I will inspect.Done."
    assert blocks == [
        ("text", {"text": "I will inspect."}),
        ("tool_use", {"id": "tc1", "name": "list_dir", "arguments": {"DirectoryPath": "."}}),
        ("tool_result", {"tc_id": "tc1", "tool": "list_dir", "result": "a.py"}),
        ("text", {"text": "Done."}),
    ]
    assert turns == []


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

    deltas = [event for event in events if event.get("type") == "ag_text_delta"]
    assert deltas[-1]["tool_results"] == [{
        "tool_use_id": deltas[-1]["tool_results"][0]["tool_use_id"],
        "name": "list_dir",
        "content": "a.py\nb.py",
    }]


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
    deltas = [event for event in events if event.get("type") == "ag_text_delta"]
    assert deltas[-1]["tool_results"][0]["name"] == "view_file"
    assert deltas[-1]["tool_results"][0]["content"] == "late result"


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


def test_antigravity_pool_types_multiline_prompts_with_shift_enter(monkeypatch):
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
    monkeypatch.setattr(pool, "_send_literal_text", lambda _state, text: events.append(("text", text)) or True)
    monkeypatch.setattr(pool, "send_keys", lambda _state, keys: events.append(("keys", keys)) or True)

    assert pool.send_text(state, "line 1\nline 2\nline 3") is True
    assert events == [
        ("text", "line 1"),
        ("keys", ["S-Enter"]),
        ("text", "line 2"),
        ("keys", ["S-Enter"]),
        ("text", "line 3"),
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

