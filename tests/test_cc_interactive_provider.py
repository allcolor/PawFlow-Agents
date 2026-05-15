import inspect
import json
from queue import Queue

from core.llm_client import LLMClient
from core.llm_providers.claude_code_interactive import _CCITurnCoordinator


class _Events:
    def __init__(self, events):
        self.q = Queue()
        for event in events:
            self.q.put(event)

    def wait_event(self, session_token, timeout=None):
        if self.q.empty():
            return {}
        return self.q.get()


def _sse(name, payload):
    return {"type": "sse", "event": name, "payload": payload}


def test_claude_code_interactive_provider_registered_and_dispatched():
    assert "claude-code-interactive" in LLMClient.PROVIDERS
    assert LLMClient("claude-code-interactive").default_model == ""
    assert LLMClient("claude-code-interactive").supports_live_preempt is True
    assert hasattr(LLMClient, "_stream_claude_code_interactive")

    complete_src = inspect.getsource(LLMClient.complete)
    stream_src = inspect.getsource(LLMClient.complete_stream)
    send_src = inspect.getsource(LLMClient.send_user_message)
    assert '"claude-code-interactive"' in complete_src
    assert '"claude-code-interactive"' in stream_src
    assert "_stream_claude_code_interactive" in stream_src
    assert "_cci_send_user_message" in send_src


def test_turn_coordinator_assembles_text_thinking_and_tool_use():
    events = [
        _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hi "},
        }),
        _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "there"},
        }),
        _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "thinking_delta", "thinking": "plan"},
        }),
        _sse("content_block_start", {
            "type": "content_block_start",
            "index": 2,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read"},
        }),
        _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"path":"'},
        }),
        _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": 'a.png"}'},
        }),
        _sse("message_delta", {
            "type": "message_delta",
            "usage": {"input_tokens": 11, "output_tokens": 7},
        }),
        _sse("message_stop", {"type": "message_stop"}),
    ]

    seen = []
    resp = _CCITurnCoordinator(_Events(events), "sess", callback=seen.append).run()

    assert resp.content == "Hi there"
    assert seen == ["Hi ", "there"]
    assert resp.thinking == "plan"
    assert resp.tokens_in == 11
    assert resp.tokens_out == 7
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].id == "toolu_1"
    assert resp.tool_calls[0].name == "read"
    assert resp.tool_calls[0].arguments == {"path": "a.png"}


def test_turn_coordinator_accepts_stop_hook_as_lifecycle_end():
    events = [
        _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "done"},
        }),
        {"type": "hook", "hook_event_name": "Stop", "input": {"hook_event_name": "Stop"}},
    ]

    resp = _CCITurnCoordinator(_Events(events), "sess").run()

    assert resp.content == "done"
    assert resp.raw["lifecycle_events"][0]["hook_event_name"] == "Stop"


def test_prompt_materializes_image_ref_as_at_path(tmp_path, monkeypatch):
    from core.llm_client import LLMMessage

    class _Store:
        def get_required(self, file_id, user_id, conversation_id):
            assert file_id == "img1"
            return "sample.png", b"PNG", "image/png"

    import core.file_store as file_store
    monkeypatch.setattr(file_store.FileStore, "instance", staticmethod(lambda: _Store()))

    client = LLMClient("claude-code-interactive", config={"experimental": True})
    msg = LLMMessage(
        role="user",
        conversation_id="conv",
        content=[
            {"type": "text", "text": "describe"},
            {"type": "image_ref", "file_id": "img1", "filename": "sample.png"},
        ],
    )

    prompt = client._cci_prompt([msg], None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv")

    assert "@/cc_sessions/u/conv/a/.pawflow_vision/img1.png" in prompt
    assert (tmp_path / ".pawflow_vision" / "img1.png").read_bytes() == b"PNG"


def test_initial_interactive_prompt_writes_context_file(tmp_path):
    from core.llm_client import LLMMessage

    client = LLMClient("claude-code-interactive", config={"experimental": True})
    messages = [
        LLMMessage(role="system", content="system rules", conversation_id="conv"),
        LLMMessage(role="assistant", content="compact summary", conversation_id="conv"),
        LLMMessage(role="user", content="latest request", conversation_id="conv"),
    ]

    prompt = client._cci_prompt(
        messages, None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=True)
    context_file = tmp_path / ".pawflow_cci" / "initial_context.md"
    body = context_file.read_text()

    assert "@/cc_sessions/u/conv/a/.pawflow_cci/initial_context.md" in prompt
    assert "system rules" in body
    assert "compact summary" in body
    assert "latest request" in body
    assert "latest request" not in prompt


def test_resume_interactive_prompt_uses_current_turn_only(tmp_path):
    from core.llm_client import LLMMessage

    client = LLMClient("claude-code-interactive", config={"experimental": True})
    messages = [
        LLMMessage(role="system", content="system rules", conversation_id="conv"),
        LLMMessage(role="assistant", content="old answer", conversation_id="conv"),
        LLMMessage(role="user", content="new request", conversation_id="conv"),
    ]

    prompt = client._cci_prompt(
        messages, None, str(tmp_path), "/cc_sessions/u/conv/a", "u", "conv",
        initial_context=False)

    assert "system rules" in prompt
    assert "new request" in prompt
    assert "old answer" not in prompt
    assert not (tmp_path / ".pawflow_cci" / "initial_context.md").exists()


def test_interactive_pool_writes_lifecycle_hooks(tmp_path):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool

    pool = InteractiveClaudeCodePool()
    pool._write_hook_settings(str(tmp_path))

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert set(settings["hooks"]) == {
        "Stop", "StopFailure", "PreCompact", "PostCompact", "SessionEnd"
    }
    stop_hook = settings["hooks"]["Stop"][0]["hooks"][0]
    assert stop_hook["command"] == "python3"
    assert stop_hook["args"] == ["/opt/pawflow/cc_interactive_hook.py"]


def test_interactive_pool_interrupt_and_force_stop_keys(monkeypatch):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool, InteractiveContainer

    calls = []

    class _Run:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input")))
        return _Run()

    monkeypatch.setattr("core.claude_code_interactive_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.claude_code_interactive_pool.subprocess.run", fake_run)

    pool = InteractiveClaudeCodePool()
    monkeypatch.setattr(pool, "_is_alive", lambda name: True)
    state = InteractiveContainer(
        key=("u", "c", "a", "svc"),
        name="container",
        workdir="/host",
        container_workdir="/cc_sessions/u/c/a",
        session_token="sess",
        event_service_id="events",
        internal_token="internal",
    )

    assert pool.send_interrupt(state, "interrupt message") is True
    assert calls[0][0][-1:] == ["Escape"]
    assert calls[1][0][-2:] == ["load-buffer", "-"]
    assert calls[1][1] == b"interrupt message"
    assert calls[3][0][-1:] == ["Enter"]

    calls.clear()
    assert pool.force_stop(state) is True
    assert calls == [(["docker", "exec", "--user", "1000:1000", "container",
                      "tmux", "send-keys", "-t", "pawflow", "Escape", "Escape"], None)]
