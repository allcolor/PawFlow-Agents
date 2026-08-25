import json
from queue import Empty, Queue
from types import SimpleNamespace

import pytest

from core._cci_pool_spawn import InteractiveContainer
from core.codex_interactive_pool import (
    CodexInteractivePool, _CodexInteractiveSpawnMixin)
from core.llm_client import CCCompactDetected, LLMClient
from core.llm_providers._codex_interactive_turn import (
    _CodexInteractiveTurnCoordinator)
from services.llm_credential_oauth import normalize_provider
from services.cc_interactive_event_service import CCInteractiveEventService


class _Events:
    def __init__(self, events):
        self.queue = Queue()
        self.code_mode_sessions = []
        for event in events:
            self.queue.put(event)

    def wait_event(self, _session_token, timeout=None):
        try:
            return self.queue.get(timeout=timeout or 0.01)
        except Empty:
            return {}

    def mark_code_mode(self, session_token):
        self.code_mode_sessions.append(session_token)


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
    monkeypatch.setattr(pool, "_tmux_is_alive", lambda _name: True)
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


def test_pool_restarts_when_container_lives_but_tmux_was_killed(monkeypatch):
    pool = CodexInteractivePool()
    monkeypatch.setattr(pool, "ensure_sweeper", lambda **_kwargs: None)
    monkeypatch.setattr(pool, "_is_alive", lambda _name: True)
    monkeypatch.setattr(pool, "_tmux_is_alive", lambda _name: False)

    key = ("user", "conv", "agent", "service")
    stale = _state(key)
    replacement = _state(key)
    replacement.name = "replacement"
    pool._sessions[key] = stale
    recovered = []
    killed = []
    launched = []
    monkeypatch.setattr(
        pool, "_recover_container_tokens", lambda state: recovered.append(state))
    monkeypatch.setattr(pool, "_kill_container", lambda name: killed.append(name))
    monkeypatch.setattr(
        pool, "_start_new",
        lambda *_args, **_kwargs: launched.append(True) or replacement)
    client = SimpleNamespace(timeout=None, _agent_service="service")

    got = pool.ensure_started(client, "model", "user", "conv", "agent")

    assert got is replacement
    assert recovered == [stale]
    assert killed == [stale.name]
    assert launched == [True]
    assert pool._sessions[key] is replacement


def test_destroy_ephemeral_kills_and_removes_runtime(tmp_path, monkeypatch):
    import core.codex_interactive_pool as pool_mod

    root = tmp_path / "codex"
    workdir = root / "user" / "_project_wiki_job" / "project-wiki"
    workdir.mkdir(parents=True)
    (workdir / "state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pool_mod._paths, "CODEX_SESSIONS_DIR", root)
    pool = CodexInteractivePool()
    key = ("user", "_project_wiki_job", "project-wiki", "service")
    state = _state(key)
    state.workdir = str(workdir)
    state.internal_token = "internal-token"
    pool._sessions[key] = state
    killed = []
    recovered = []
    unregistered = []
    revoked = []
    monkeypatch.setattr(
        pool, "_kill_container", lambda name: killed.append(name))
    monkeypatch.setattr(
        pool, "_recover_container_tokens", lambda item: recovered.append(item))
    events = SimpleNamespace(
        unregister_session=lambda token: unregistered.append(token))
    monkeypatch.setattr(
        "services.cc_interactive_event_service."
        "get_or_create_cc_interactive_event_service",
        lambda: ("", "", events))
    monkeypatch.setattr(
        "core.internal_auth.revoke_token", lambda token: revoked.append(token))

    pool.destroy_ephemeral(state)

    assert key not in pool._sessions
    assert killed == [state.name]
    assert recovered == [state]
    assert unregistered == [state.session_token]
    assert revoked == ["internal-token"]
    assert not workdir.exists()
    assert not (root / "user" / "_project_wiki_job").exists()


def test_ephemeral_stream_is_destroyed_when_prompt_send_fails(monkeypatch):
    client = LLMClient("codex-interactive")
    key = ("user", "_project_wiki_job", "project-wiki", "")
    state = _state(key)
    destroyed = []
    acquired_conversations = []
    def ensure_started(_client, _model, _user, conversation, _agent,
                       **_kwargs):
        acquired_conversations.append(conversation)
        return state
    pool = SimpleNamespace(
        ensure_started=ensure_started,
        touch=lambda _state: None,
        send_text=lambda _state, _prompt: False,
        destroy_ephemeral=lambda item: destroyed.append(item),
    )
    monkeypatch.setattr(
        CodexInteractivePool, "instance", classmethod(lambda cls: pool))
    monkeypatch.setattr(client, "_cci_prompt", lambda *_args, **_kwargs: "prompt")
    events = SimpleNamespace(
        claim_consumer=lambda _token: 1,
        drain_session=lambda _token: None,
        release_consumer=lambda _token, _epoch: None,
    )
    monkeypatch.setattr(
        "services.cc_interactive_event_service."
        "get_or_create_cc_interactive_event_service",
        lambda: ("", "", events))

    with pytest.raises(Exception, match="Failed to paste prompt"):
        client._stream_codex_interactive(
            [], "", call_user_id="user",
            call_conversation_id="_project_wiki_job",
            call_agent_name="project-wiki",
            call_ephemeral_stream=True)

    assert destroyed == [state]
    assert acquired_conversations[0].startswith(
        "_project_wiki_job__ephemeral_")
    assert acquired_conversations[0] != "_project_wiki_job"


def test_codex_provider_releases_request_lease_when_coordinator_raises(
        monkeypatch):
    client = LLMClient("codex-interactive")
    state = _state(("user", "conv", "assistant", ""))
    pool = SimpleNamespace(
        ensure_started=lambda *_args, **_kwargs: state,
        touch=lambda _state: None,
        send_text=lambda _state, _prompt: True,
        send_interrupt=lambda _state, _text: True,
        destroy_ephemeral=lambda _state: None)
    released = []
    events = SimpleNamespace(
        claim_consumer=lambda _token: 11,
        drain_session=lambda _token: None,
        release_consumer=lambda token, epoch: released.append((token, epoch)))

    class _FailingCoordinator:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, _abort=None):
            raise RuntimeError("callback failed")

    monkeypatch.setattr(
        CodexInteractivePool, "instance", classmethod(lambda cls: pool))
    monkeypatch.setattr(client, "_cci_prompt", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(
        client, "_codex_interactive_session_state", lambda **_kwargs: state)
    monkeypatch.setattr(
        "core.llm_providers.codex_interactive."
        "_CodexInteractiveTurnCoordinator", _FailingCoordinator)
    monkeypatch.setattr(
        "services.cc_interactive_event_service."
        "get_or_create_cc_interactive_event_service",
        lambda: ("", "", events))

    with pytest.raises(RuntimeError, match="callback failed"):
        client._stream_codex_interactive(
            [], "", call_user_id="user", call_conversation_id="conv",
            call_agent_name="assistant")
    with pytest.raises(RuntimeError, match="callback failed"):
        client.interrupt_codex_interactive(
            "stop", user_id="user", conversation_id="conv",
            agent_name="assistant")

    assert released == [(state.session_token, 11), (state.session_token, 11)]


def test_codex_provider_kills_native_session_when_compaction_hook_fires(
        monkeypatch):
    client = LLMClient("codex-interactive")
    state = _state(("user", "conv", "assistant", "svc"))
    killed = []
    released = []
    pool = SimpleNamespace(
        ensure_started=lambda *_args, **_kwargs: state,
        touch=lambda _state: None,
        send_text=lambda _state, _prompt: True,
        send_interrupt=lambda _state, _text: True,
        destroy_ephemeral=lambda _state: None,
        kill_session=lambda *args: killed.append(args) or True)
    events = SimpleNamespace(
        claim_consumer=lambda _token: 12,
        drain_session=lambda _token: None,
        release_consumer=lambda token, epoch: released.append((token, epoch)))

    class _CompactCoordinator:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, _abort=None):
            raise CCCompactDetected("PreCompact")

    monkeypatch.setattr(
        CodexInteractivePool, "instance", classmethod(lambda cls: pool))
    monkeypatch.setattr(client, "_cci_prompt", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(
        client, "_codex_interactive_session_state", lambda **_kwargs: state)
    monkeypatch.setattr(
        "core.llm_providers.codex_interactive."
        "_CodexInteractiveTurnCoordinator", _CompactCoordinator)
    monkeypatch.setattr(
        "services.cc_interactive_event_service."
        "get_or_create_cc_interactive_event_service",
        lambda: ("", "", events))

    with pytest.raises(CCCompactDetected, match="PreCompact"):
        client._stream_codex_interactive(
            [], "", call_user_id="user", call_conversation_id="conv",
            call_agent_name="assistant")
    with pytest.raises(CCCompactDetected, match="PreCompact"):
        client.interrupt_codex_interactive(
            "stop", user_id="user", conversation_id="conv",
            agent_name="assistant")

    assert killed == [
        ("user", "conv", "assistant", "svc"),
        ("user", "conv", "assistant", "svc"),
    ]
    assert released == [(state.session_token, 12), (state.session_token, 12)]


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
        **common, codex_base_url="https://gateway.example/prefix/v1",
        cli_environment="CUSTOM='hello world'")
    assert "OPENAI_BASE_URL=https://gateway.example/prefix/v1" in commands[0][-1]
    assert "CUSTOM='hello world' HOME=/cc_sessions/conv/agent" in commands[0][-1]

    commands.clear()
    spawn._start_codex_tmux(**common)
    assert "OPENAI_BASE_URL=" not in commands[0][-1]


def test_codex_tmux_keeps_the_same_builtins_as_app_server(monkeypatch):
    # Same binary, same session workdir, same bootstrap file. The only
    # difference between the two codex providers is the transport, so the
    # tool set cannot differ either: `codex app-server` is launched with no
    # blocklist, and blocking the builtins here left the TUI unable to read
    # the `initial_context.md` we hand it, or to open the attachments we
    # write beside it.
    import core.codex_interactive_pool as pool_mod

    commands = []
    monkeypatch.setattr(
        pool_mod.subprocess, "run",
        lambda command, **_kw: commands.append(command) or SimpleNamespace(
            returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(pool_mod, "docker_cmd", lambda: ["docker"])
    spawn = _CodexInteractiveSpawnMixin()
    spawn.run_uid = 1000
    spawn.run_gid = 1000
    spawn._user_spec = lambda: "1000:1000"

    spawn._start_codex_tmux(
        name="codex", container_workdir="/cc_sessions_host/user/conv/agent",
        model="gpt-test", effort="", session_token="session",
        event_url="ws://events", event_token="event",
        internal_token="internal")

    shell = commands[0][-1]
    assert "--disable" not in shell
    assert "tools.view_image=false" not in shell
    assert "tools.web_search=false" not in shell


# ── The trust modal must never be reachable ──────────────────────────
#
# A cold TUI in an untrusted directory opens "Do you trust the contents of
# this directory?" and waits. No readiness marker is ever drawn, so the
# 45s readiness wait times out, the injected prompt is pasted into the
# modal, and only a human at the tmux can unblock the launch.


class _StopSpawn(Exception):
    pass


def test_start_new_declares_the_session_workdir_trusted(monkeypatch, tmp_path):
    recorded = {}

    class _Client:
        api_key = ""

        @staticmethod
        def _codex_get_session_workdir(*_a, **_kw):
            return str(tmp_path)

        @staticmethod
        def _codex_setup_credentials(*_a, **_kw):
            return None

        @staticmethod
        def _codex_setup_mcp_config(*args, **kwargs):
            recorded["trusted_project"] = kwargs.get("trusted_project")
            # Nothing past this point can run without Docker.
            raise _StopSpawn()

    pool = CodexInteractivePool()
    try:
        pool._start_new(_Client(), "gpt-test", "user", "conv", "agent",
                        ("user", "conv", "agent", "svc"))
    except _StopSpawn:
        pass

    assert recorded["trusted_project"] == "/cc_sessions/conv/agent"


def test_trusted_project_is_the_directory_codex_actually_runs_in():
    # _start_codex_tmux cds into the namespaced path derived from the
    # physical workdir. Trusting any other string trusts nothing.
    physical = CodexInteractivePool._physical_container_workdir(
        "user", "conv", "agent")
    parts = physical.lstrip("/").split("/")
    ns_workdir = "/cc_sessions/" + "/".join(parts[2:])

    assert CodexInteractivePool._container_workdir(
        "user", "conv", "agent") == ns_workdir


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
                "input_tokens_details": {"cached_tokens": 7},
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
    assert response.tokens_in == 3
    assert response.cache_read_tokens == 7
    assert [kind for kind, _ in blocks] == [
        "tool_use", "tool_result", "text", "thinking_content"]
    assert blocks[0][1]["name"] == "read"
    assert blocks[0][1]["arguments"] == {"path": "/workspace/a.py"}


@pytest.mark.parametrize("hook_name", ["PreCompact", "PostCompact"])
def test_responses_coordinator_hands_native_compaction_to_pawflow(hook_name):
    events = _Events([
        {"type": "request_start",
         "path": "/backend-api/codex/responses"},
        {"type": "hook", "hook_event_name": hook_name,
         "input": {"trigger": "auto"}},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(events, "session")

    with pytest.raises(CCCompactDetected, match=hook_name):
        coordinator.run()

    # Native post-compact/turn events must not be consumed or adopted. The
    # common agent loop catches CCCompactDetected and rebuilds from PawFlow's
    # own forced compact instead.
    assert events.queue.qsize() == 1


def test_responses_coordinator_shows_the_native_calls_too(monkeypatch):
    # Codex's own tools are not `function_call` items: the shell is a
    # `local_shell_call` carrying an `action`, apply_patch a `custom_tool_call`
    # carrying a freeform `input`. Reading only `function_call` rendered a turn
    # with no tool at all while the model was visibly running them.
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    events = _Events([
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "local_shell_call", "call_id": "call-1",
                     "action": {"type": "exec",
                                "command": ["bash", "-lc", "git status"]}}}},
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "call_id": "call-2",
                     "name": "apply_patch",
                     "input": "*** Begin Patch\n*** End Patch"}}},
        {"type": "tool_result", "tool_use_id": "call-1",
         "content": "clean"},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    coordinator.run()

    assert [(kind, payload.get("name") or payload.get("tool"))
            for kind, payload in blocks] == [
        ("tool_use", "local_shell"),
        ("tool_use", "apply_patch"),
        ("tool_result", "local_shell"),
    ]
    assert blocks[0][1]["arguments"] == {
        "type": "exec", "command": ["bash", "-lc", "git status"]}
    assert blocks[1][1]["arguments"] == {
        "input": "*** Begin Patch\n*** End Patch"}


def test_a_code_mode_body_draws_its_row_and_arms_the_relay(monkeypatch):
    # The body arms the session so the relay reports the calls it executes.
    # It also keeps its own row: a script can reach a tool without going
    # through PawFlow -- reading the bootstrap context is the first thing it
    # does -- and no relay reports those. Dropping the row left them in no
    # view at all. Which tools the source names is still not readable from
    # it; the relay's rows are what name them.
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    events = _Events([
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "call_id": "call-1",
                     "name": "exec",
                     "input": "const rs=await Promise.all(names.map("
                              "tool_name=>tools.mcp__pawflow__get_tool_schema("
                              "{tool_name})));text(rs)"}}},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    coordinator.run()

    assert events.code_mode_sessions == ["session"]
    assert [kind for kind, _ in blocks] == ["tool_use"]
    assert blocks[0][1]["name"] == "exec"


def test_relay_reported_calls_render_like_any_other_tool(monkeypatch):
    # What the relay publishes for a code-mode body enters by the same door as
    # the MITM's own observations, so those calls become rows through the one
    # path every provider shares: real name, real arguments, MCP badge, and
    # the result the tool actually returned.
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    events = _Events([
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "call_id": "call-1",
                     "name": "exec",
                     "input": 'await tools.mcp__pawflow__use_tool({tool_name})'}}},
        {"type": "tool_use", "tool_use_id": "req-1",
         "name": "read", "arguments": {"path": "/workspace/a.py"},
         "tool_origin": "mcp"},
        {"type": "tool_result", "tool_use_id": "req-1",
         "content": "line one"},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    coordinator.run()

    # The body's own row comes first -- it is a real call Codex made -- then
    # the relay's, naming what the script actually ran.
    assert [kind for kind, _ in blocks] == [
        "tool_use", "tool_use", "tool_result"]
    assert blocks[0][1]["name"] == "exec"
    assert blocks[1][1]["name"] == "read"
    assert blocks[1][1]["arguments"] == {"path": "/workspace/a.py"}
    assert blocks[1][1]["tool_origin"] == "mcp"
    assert blocks[2][1]["tc_id"] == "req-1"
    assert blocks[2][1]["tool"] == "read"
    assert blocks[2][1]["result"] == "line one"


def test_a_script_output_yields_to_the_relay_rows_end_to_end(monkeypatch):
    # The duplication the row elision left behind: a script's output is the
    # aggregate of the calls the relay reported one by one, so the same bytes
    # went into the next context twice. Codex never sees the relay's rows --
    # only its own script and its output -- so this is free while the session
    # is warm and paid in full at every cold start.
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    events = _Events([
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "call_id": "call-1",
                     "name": "exec",
                     "input": 'await tools.mcp__pawflow__use_tool({tool_name})'}}},
        {"type": "tool_use", "tool_use_id": "req-1", "name": "read",
         "arguments": {"path": "/workspace/a.py"}, "tool_origin": "mcp"},
        {"type": "tool_result", "tool_use_id": "req-1", "content": "AAA" * 200},
        {"type": "tool_use", "tool_use_id": "req-2", "name": "grep",
         "arguments": {"pattern": "x"}, "tool_origin": "mcp"},
        {"type": "tool_result", "tool_use_id": "req-2", "content": "BBB" * 200},
        # The script's own result: everything above, once more.
        {"type": "tool_result", "tool_use_id": "call-1",
         "content": "AAA" * 200 + "BBB" * 200},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    coordinator.run()

    results = {payload.get("tc_id"): payload["result"]
               for kind, payload in blocks if kind == "tool_result"}
    # Each call keeps its own result in full: that is where the detail lives.
    assert results["req-1"] == "AAA" * 200
    assert results["req-2"] == "BBB" * 200
    # The script's copy of each is replaced by a pointer to the row that has
    # it. Both copies, and nothing else -- there was nothing else here.
    assert "AAA" not in results["call-1"]
    assert "BBB" not in results["call-1"]
    assert str(len("AAA" * 200)) in results["call-1"]
    assert str(len("BBB" * 200)) in results["call-1"]


def test_what_a_script_derived_survives_the_elision_end_to_end(monkeypatch):
    # A script does not only relay: it compares, counts, concludes. That
    # sentence exists in no row, and dropping the whole output because the
    # script happened to make a call deleted the one thing the turn produced.
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    derived = "\nderived comparison: 3 lines differ, none in the API\n"
    events = _Events([
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "call_id": "call-1",
                     "name": "exec",
                     "input": 'await tools.mcp__pawflow__use_tool({tool_name})'}}},
        {"type": "tool_use", "tool_use_id": "req-1", "name": "read",
         "arguments": {"path": "/workspace/a.py"}, "tool_origin": "mcp"},
        {"type": "tool_result", "tool_use_id": "req-1", "content": "AAA" * 200},
        {"type": "tool_result", "tool_use_id": "call-1",
         "content": "AAA" * 200 + derived},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    coordinator.run()

    results = {payload.get("tc_id"): payload["result"]
               for kind, payload in blocks if kind == "tool_result"}
    assert "AAA" not in results["call-1"], "the echoed result still goes"
    assert derived in results["call-1"], "and what only the script knows stays"


def test_a_re_read_relay_row_is_not_a_second_call(monkeypatch):
    # A Responses call item is observed twice -- streamed as it is made, then
    # replayed in the next request's input with its output. The base class
    # renders the second observation onto the first row, and a counter bumped
    # after that call counted the re-read as a new call: the NEXT script, which
    # drove nothing at all, was then billed for it and lost its own output.
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    alone = "what the second script read with Codex's own runtime" * 3
    events = _Events([
        # script 1 and its one relay row.
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "call_id": "call-1",
                     "name": "exec",
                     "input": 'await tools.mcp__pawflow__use_tool({tool_name})'}}},
        {"type": "tool_use", "tool_use_id": "req-1", "name": "read",
         "arguments": {"path": "/workspace/a.py"}, "tool_origin": "mcp"},
        {"type": "tool_result", "tool_use_id": "req-1", "content": "AAA" * 200},
        {"type": "tool_result", "tool_use_id": "call-1", "content": "AAA" * 200},
        # script 2: no relay row of its own, ever.
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "call_id": "call-2",
                     "name": "exec",
                     "input": 'await tools.mcp__pawflow__use_tool({tool_name})'}}},
        # ...and only now is script 1's relay row observed a second time, as
        # the next request replays it in its input. Counted there, it became
        # script 2's call.
        {"type": "tool_use", "tool_use_id": "req-1", "name": "read",
         "arguments": {"path": "/workspace/a.py"}, "tool_origin": "mcp"},
        {"type": "tool_result", "tool_use_id": "call-2", "content": alone},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    coordinator.run()

    results = {payload.get("tc_id"): payload["result"]
               for kind, payload in blocks if kind == "tool_result"}
    assert results["call-2"] == alone, "it drove nothing; nobody else says this"
    assert len(coordinator._mcp_row_results) == 1, "one row, seen twice"


def test_a_script_that_drove_no_relay_call_keeps_its_output(monkeypatch):
    # Read through Codex's own runtime: no relay row describes it, so the
    # script's output is the only record of what the turn did.
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    events = _Events([
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "call_id": "call-1",
                     "name": "exec",
                     "input": 'await tools.mcp__pawflow__use_tool({tool_name})'}}},
        {"type": "tool_result", "tool_use_id": "call-1",
         "content": "the whole bootstrap context, read by the script itself"},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    coordinator.run()

    results = [payload["result"] for kind, payload in blocks
               if kind == "tool_result"]
    assert results == [
        "the whole bootstrap context, read by the script itself"]


def test_a_call_seen_under_each_of_its_two_ids_stays_one_row(monkeypatch):
    # A call item carries `call_id` AND `id`. The stream can quote one and
    # the replayed input the other: keyed on the raw id that is two rows for
    # one call, and the result lands on the second, leaving the first pending
    # until the turn ends and stamps it stopped.
    import core.llm_providers._codex_interactive_turn as turn_mod

    monkeypatch.setattr(turn_mod, "_POST_STOP_IDLE_DRAIN_SECONDS", 0)
    blocks = []
    events = _Events([
        {"type": "sse", "payload": {
            "type": "response.output_item.done",
            "item": {"type": "custom_tool_call", "id": "ctc_1",
                     "name": "exec", "input": "ls"}}},
        {"type": "tool_use", "tool_use_id": "call_1",
         "alias_ids": ["call_1", "ctc_1"], "name": "exec",
         "arguments": {"input": "ls"}},
        {"type": "tool_result", "tool_use_id": "call_1", "content": "a.py"},
        {"type": "hook", "hook_event_name": "Stop", "input": {}},
    ])
    coordinator = _CodexInteractiveTurnCoordinator(
        events, "session",
        block_callback=lambda kind, payload: blocks.append((kind, payload)))

    coordinator.run()

    assert [kind for kind, _ in blocks] == ["tool_use", "tool_result"]
    assert blocks[0][1]["id"] == "ctc_1"
    assert blocks[1][1]["tc_id"] == "ctc_1"
    assert blocks[1][1]["result"] == "a.py"


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
