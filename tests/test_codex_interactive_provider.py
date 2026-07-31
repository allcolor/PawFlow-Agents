import json
from queue import Empty, Queue
from types import SimpleNamespace

from core._cci_pool_spawn import InteractiveContainer
from core.codex_interactive_pool import (
    CodexInteractivePool, _CodexInteractiveSpawnMixin)
from core.llm_client import LLMClient
from core.llm_providers._codex_interactive_turn import (
    _CodexInteractiveTurnCoordinator)
from services.llm_credential_oauth import normalize_provider
from services.cc_interactive_event_service import CCInteractiveEventService


class _Events:
    def __init__(self, events):
        self.queue = Queue()
        for event in events:
            self.queue.put(event)

    def wait_event(self, _session_token, timeout=None):
        try:
            return self.queue.get(timeout=timeout or 0.01)
        except Empty:
            return {}


def _state(key, index=-1):
    return InteractiveContainer(
        key=key, name=f"container-{key[1]}", workdir=f"/tmp/{key[1]}",
        container_workdir=f"/cc_sessions/{key[1]}/{key[2]}",
        session_token=f"token-{key[1]}", event_service_id="events",
        internal_token="internal", service_id=key[3],
        svc_pool_idx=index, user_id=key[0], conv_id=key[1])


def test_codex_interactive_registered_and_uses_codex_credentials():
    client = LLMClient("codex-interactive")

    assert "codex-interactive" in LLMClient.PROVIDERS
    assert client.default_model == ""
    assert client.supports_live_preempt is True
    assert normalize_provider("codex-interactive") == "codex-app-server"
    assert hasattr(client, "_stream_codex_interactive")


def test_pool_does_not_reserve_oauth_credential_between_agents(monkeypatch):
    pool = CodexInteractivePool()
    monkeypatch.setattr(pool, "ensure_sweeper", lambda **_kwargs: None)
    monkeypatch.setattr(pool, "_is_alive", lambda _name: True)
    calls = []

    def _start(_client, _model, user, conv, agent, key, pool_index=-99):
        calls.append((user, conv, agent, key[3], pool_index))
        return _state(key, index=0)

    monkeypatch.setattr(pool, "_start_new", _start)
    client = SimpleNamespace(timeout=None, _agent_service="shared-service")

    first = pool.ensure_started(client, "model", "user", "conv-a", "a")
    second = pool.ensure_started(client, "model", "user", "conv-b", "b")

    assert first.svc_pool_idx == second.svc_pool_idx == 0
    assert [call[-1] for call in calls] == [-1, -1]
    assert len(pool._sessions) == 2


def test_endpoint_uses_custom_base_url_or_official_auth_endpoint():
    oauth = SimpleNamespace(api_key="", base_url="")
    api_key = SimpleNamespace(api_key="sk-test", base_url="")
    custom = SimpleNamespace(
        api_key="", base_url="http://gateway.example:8443/prefix/v1/")
    custom_http_default = SimpleNamespace(
        api_key="", base_url="http://gateway.example/v1")

    assert _CodexInteractiveSpawnMixin._codex_endpoint(oauth) == (
        "chatgpt.com", 443, "https", "", 443)
    assert _CodexInteractiveSpawnMixin._codex_endpoint(api_key) == (
        "api.openai.com", 443, "https", "", 443)
    assert _CodexInteractiveSpawnMixin._codex_endpoint(custom) == (
        "gateway.example", 8443, "http",
        "https://gateway.example:8443/prefix/v1", 8443)
    assert _CodexInteractiveSpawnMixin._codex_endpoint(custom_http_default) == (
        "gateway.example", 80, "http",
        "https://gateway.example:80/v1", 80)


def test_codex_tmux_injects_custom_base_url_only_when_configured(monkeypatch):
    import core.codex_interactive_pool as pool_mod

    commands = []

    def _run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pool_mod.subprocess, "run", _run)
    monkeypatch.setattr(pool_mod, "docker_cmd", lambda: ["docker"] )
    spawn = _CodexInteractiveSpawnMixin()
    spawn.run_uid = 1000
    spawn.run_gid = 1000
    spawn._user_spec = lambda: "1000:1000"

    common = {
        "name": "codex",
        "container_workdir": "/cc_sessions_host/user/conv/agent",
        "model": "gpt-test",
        "effort": "",
        "session_token": "session",
        "event_url": "ws://events",
        "event_token": "event",
        "internal_token": "internal",
    }
    spawn._start_codex_tmux(
        **common, codex_base_url="https://gateway.example/prefix/v1")
    assert "OPENAI_BASE_URL=https://gateway.example/prefix/v1" in commands[0][-1]

    commands.clear()
    spawn._start_codex_tmux(**common)
    assert "OPENAI_BASE_URL=" not in commands[0][-1]


def test_codex_hooks_use_documented_hooks_json_shape(tmp_path):
    _CodexInteractiveSpawnMixin._write_codex_hooks(str(tmp_path))

    payload = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert set(payload["hooks"]) == {
        "UserPromptSubmit", "Stop", "PreCompact", "PostCompact",
        "SessionEnd"}
    handler = payload["hooks"]["Stop"][0]["hooks"][0]
    assert handler == {
        "type": "command",
        "command": "python3 /opt/pawflow/cc_interactive_hook.py",
        "timeout": 5,
    }


def test_responses_coordinator_streams_blocks_until_codex_stop(monkeypatch):
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    text = []
    thinking = []
    events = _Events([
        {"type": "request_start",
         "path": "/backend-api/codex/responses"},
        {"type": "sse", "payload": {
            "type": "response.created",
            "response": {"model": "gpt-test"}}},
        {"type": "sse", "payload": {
            "type": "response.reasoning_summary_text.delta",
            "delta": "plan"}},
        {"type": "sse", "payload": {
            "type": "response.output_text.delta", "delta": "hello"}},
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": "call-1",
                     "name": "mcp__pawflow__use_tool",
                     "arguments": json.dumps({
                         "tool_name": "read",
                         "arguments": {"path": "/workspace/a.py"}})}}},
        {"type": "tool_result", "tool_use_id": "call-1",
         "content": "contents"},
        {"type": "sse", "payload": {
            "type": "response.completed",
            "response": {"model": "gpt-test", "usage": {
                "input_tokens": 10, "output_tokens": 4,
                "total_tokens": 14}}}},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session", callback=text.append,
        thinking_callback=thinking.append,
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    response = coordinator.run()

    assert "".join(text) == "hello"
    assert "".join(thinking) == "plan"
    assert response.content == "hello"
    assert response.thinking == "plan"
    assert response.model == "gpt-test"
    assert response.total_tokens == 14
    assert [kind for kind, _ in blocks] == [
        "tool_use", "tool_result", "text", "thinking_content"]
    assert blocks[0][1]["name"] == "read"
    assert blocks[0][1]["arguments"] == {"path": "/workspace/a.py"}


def test_codex_event_session_adopts_prefixed_responses_request_with_query(
        monkeypatch):
    service = CCInteractiveEventService({
        "_service_id": "events", "token": "token"})
    state = service.register_session(
        "session", user_id="user", conversation_id="conv",
        agent_name="assistant", provider="codex-interactive")
    adopted = []
    monkeypatch.setattr(
        service, "_adopt_orphan_turn",
        lambda candidate, reason: adopted.append((candidate, reason)))

    service._maybe_adopt_orphan_turn(state, {
        "type": "request_start",
        "path": "/prefix/v1/responses?stream=true",
    })

    assert adopted == [(state, "request in flight")]

