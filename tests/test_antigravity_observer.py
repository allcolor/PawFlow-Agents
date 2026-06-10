"""Regression tests for Antigravity observer mode."""

from __future__ import annotations

import threading

from pathlib import Path


def test_observer_container_routes_antigravity_backend_to_local_proxy(monkeypatch):
    from core.antigravity_observer_pool import AntigravityObserverPool

    calls = []

    class _Run:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)
    monkeypatch.setattr("core.antigravity_observer_pool.get_server_id", lambda: "server1234567890")
    monkeypatch.setattr("core.antigravity_observer_pool.translate_path", lambda p: p)
    monkeypatch.setattr("core.antigravity_observer_pool.to_host_path", lambda p: p)
    monkeypatch.setattr("core.antigravity_observer_pool.ca_private_key_is_host_only", lambda mounts: True)
    monkeypatch.setattr(AntigravityObserverPool, "_base_dir", staticmethod(lambda: Path("/tmp/agobs")))

    name = AntigravityObserverPool()._spawn_container(
        user_id="u", conversation_id="c", agent_name="assistant")

    assert name.startswith("pf-server123456-agyobs-")
    run_cmd = calls[0]
    assert "--add-host" in run_cmd
    assert "daily-cloudcode-pa.googleapis.com:127.0.0.1" in run_cmd
    assert "/opt/pawflow/ag_observer_proxy.py:ro" not in " ".join(run_cmd)
    assert any(cmd[:2] == ["docker", "cp"] and cmd[-1].endswith(":/opt/pawflow/ag_observer_proxy.py") for cmd in calls)


def test_observer_proxy_receives_log_and_cert_env(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from core.antigravity_observer_pool import AntigravityObserverPool

    calls = []

    class _Run:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)
    monkeypatch.setattr(AntigravityObserverPool, "_resolve_upstream_ips", staticmethod(lambda: ["1.2.3.4"]))
    monkeypatch.setattr(AntigravityObserverPool, "_base_dir", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(AntigravityObserverPool, "_wait_for_proxy_start", lambda self, path, **kwargs: None)

    certs = SimpleNamespace(
        cert_path=tmp_path / "u" / "c" / "a" / ".pawflow_ag" / "certs" / "leaf.crt",
        key_path=tmp_path / "u" / "c" / "a" / ".pawflow_ag" / "certs" / "leaf.key",
    )
    log_path = str(tmp_path / "u" / "c" / "a" / ".pawflow_ag" / "logs" / "observer.jsonl")
    stderr_path = str(tmp_path / "u" / "c" / "a" / ".pawflow_ag" / "logs" / "proxy.stderr.log")

    AntigravityObserverPool()._start_proxy(
        name="container", container_workdir="/cc_sessions/u/c/a",
        log_path=log_path, stderr_path=stderr_path, certs=certs)

    cmd = calls[0]
    assert Path(stderr_path).read_text(encoding="utf-8").startswith("starting Antigravity observer proxy")
    assert "PAWFLOW_AG_OBSERVER_LOG=/cc_sessions/u/c/a/.pawflow_ag/logs/observer.jsonl" in cmd
    assert "PAWFLOW_AG_UPSTREAM_IPS=1.2.3.4" in cmd
    assert "PAWFLOW_AG_LEAF_CERT=/cc_sessions/u/c/a/.pawflow_ag/certs/leaf.crt" in cmd
    assert "PAWFLOW_AG_LEAF_KEY=/cc_sessions/u/c/a/.pawflow_ag/certs/leaf.key" in cmd
    assert cmd[-3:-1] == ["bash", "-lc"]
    assert "exec python3 /opt/pawflow/ag_observer_proxy.py" in cmd[-1]
    assert "2>&1" in cmd[-1]


def test_observer_writes_antigravity_mcp_config_shape(tmp_path):
    import json
    from core.antigravity_observer_pool import AntigravityObserverPool

    class _Client:
        def _gemini_acp_mcp_servers(self, user_id, conversation_id, agent_name):
            return ([{"name": "pawflow"}], "internal-token")

        def _gemini_acp_write_settings(self, workdir, **kwargs):
            settings = Path(workdir) / ".gemini" / "settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(
                json.dumps({"permissions": {"allow": ["command(ls)"]}}),
                encoding="utf-8",
            )

        def _gemini_acp_settings_mcp_servers(self, mcp_servers, mcp_cwd):
            return {
                "pawflow": {
                    "type": "stdio",
                    "command": "/usr/bin/python3",
                    "args": ["/opt/pawflow/mcp_bridge.py"],
                    "cwd": mcp_cwd,
                    "env": {"PAWFLOW_INTERNAL_TOKEN": "token"},
                    "timeout": 15000,
                    "trust": True,
                }
            }

    AntigravityObserverPool()._write_antigravity_config(
        _Client(), str(tmp_path), "u", "c", "a", "")

    antigravity_config = json.loads(
        (tmp_path / ".gemini" / "antigravity" / "mcp_config.json").read_text(encoding="utf-8"))
    assert antigravity_config == {
        "mcpServers": [
            {
                "serverName": "pawflow",
                "type": "stdio",
                "command": "/usr/bin/python3",
                "args": ["/opt/pawflow/mcp_bridge.py"],
                "cwd": "/cc_sessions/c/a",
                "env": {"PAWFLOW_INTERNAL_TOKEN": "token"},
                "timeout": 15000,
                "trust": True,
                "disabled": False,
            }
        ]
    }
    cli_config = json.loads(
        (tmp_path / ".gemini" / "antigravity-cli" / "mcp_config.json").read_text(encoding="utf-8"))
    assert cli_config["mcpServers"][0]["serverName"] == "pawflow"
    assert cli_config["mcpServers"][0]["command"] == "/usr/bin/python3"
    assert cli_config["mcpServers"][0]["args"] == ["/opt/pawflow/mcp_bridge.py"]
    assert cli_config["mcpServers"][0]["cwd"] == "/cc_sessions/c/a"
    assert cli_config["mcpServers"][0]["type"] == "stdio"
    root_mcp_config = json.loads(
        (tmp_path / ".gemini" / "mcp_config.json").read_text(encoding="utf-8"))
    assert root_mcp_config["mcpServers"]["pawflow"]["command"] == "/usr/bin/python3"
    workspace_mcp_config = json.loads(
        (tmp_path / ".agents" / "mcp_config.json").read_text(encoding="utf-8"))
    assert workspace_mcp_config["mcpServers"]["pawflow"]["command"] == "/usr/bin/python3"
    cli_settings = json.loads(
        (tmp_path / ".gemini" / "antigravity-cli" / "settings.json").read_text(encoding="utf-8"))
    assert cli_settings["enableTelemetry"] is False
    assert cli_settings["trustedWorkspaces"] == ["/cc_sessions/c/a"]
    assert cli_settings["allowMCPServers"] == ["pawflow"]
    assert cli_settings["mcp"] == {"allowed": ["pawflow"]}
    assert cli_settings["permissions"]["allow"] == ["mcp(pawflow/*)", "mcp_pawflow_*", "mcp_*"]
    assert cli_settings["mcpServers"]["pawflow"]["command"] == "/usr/bin/python3"
    settings = json.loads((tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == [
        "command(ls)", "mcp(pawflow/*)", "mcp_pawflow_*", "mcp_*"]
    assert settings["mcpServers"]["pawflow"]["command"] == "/usr/bin/python3"
    assert settings["mcpServers"]["pawflow"]["args"] == ["/opt/pawflow/mcp_bridge.py"]
    assert settings["mcpServers"]["pawflow"]["cwd"] == "/cc_sessions/c/a"
    rule = (tmp_path / ".agents" / "rules" / "pawflow-mcp.md").read_text(encoding="utf-8")
    assert "Use the configured MCP server `pawflow`" in rule
    assert "Do not create custom WebSocket" in rule


def test_observer_workspace_rule_rejects_relay_client_bypass(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool

    AntigravityObserverPool._write_workspace_rules(str(tmp_path))

    rule = (tmp_path / ".agents" / "rules" / "pawflow-mcp.md").read_text(encoding="utf-8")
    assert "MCP server `pawflow`" in rule
    assert "relay" in rule
    assert "bypassing" in rule


def test_observer_container_session_paths_are_posix(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool

    monkeypatch.setattr(AntigravityObserverPool, "_base_dir", staticmethod(lambda: tmp_path))
    path = tmp_path / "quentin.anciaux" / "fa1dc365d31b4ec4" / "gemini" / ".pawflow_ag" / "logs" / "observer.jsonl"

    assert AntigravityObserverPool()._container_session_path(str(path)) == (
        "/cc_sessions/quentin.anciaux/fa1dc365d31b4ec4/gemini/.pawflow_ag/logs/observer.jsonl"
    )


def test_observer_session_restarts_when_proxy_log_never_started(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    pool = AntigravityObserverPool()
    key = ("u", "c", "a", "")
    stale = AntigravityObserverSession(
        key=key,
        name="old-container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "missing.jsonl"),
    )
    fresh = AntigravityObserverSession(
        key=key,
        name="new-container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool._sessions[key] = stale
    killed = []

    monkeypatch.setattr(pool, "_is_alive", lambda name: True)
    monkeypatch.setattr(pool, "kill", lambda state: killed.append(state.name))
    monkeypatch.setattr(pool, "_start_new", lambda *args: fresh)

    state = pool.start(user_id="u", conversation_id="c", agent_name="a")

    assert state is fresh
    assert killed == ["old-container"]


def test_observer_start_enables_manual_ingest_for_reused_session(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    pool = AntigravityObserverPool()
    key = ("u", "c", "a", "svc")
    existing = AntigravityObserverSession(
        key=key,
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool._sessions[key] = existing
    enabled = []
    monkeypatch.setattr(pool, "_is_usable", lambda state: True)
    monkeypatch.setattr(pool, "_ensure_manual_ingest", lambda state: enabled.append(state.name))

    state = pool.start(user_id="u", conversation_id="c", agent_name="a", service_id="svc")

    assert state is existing
    assert enabled == ["container"]


def test_observer_manual_ingest_persists_user_and_assistant(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession
    from core.conversation_writer import ConversationWriter

    writes = []

    class _Writer:
        def enqueue_message(self, msg, **kwargs):
            writes.append((msg, kwargs))

    writer = _Writer()
    monkeypatch.setattr(
        ConversationWriter,
        "for_conversation",
        classmethod(lambda cls, cid: writer),
    )
    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()

    pool._persist_manual_user_prompt(state, {"text": "PF_MANUAL_SMOKE respond exactly OK"})
    turn = pool._new_manual_turn()
    pool._accumulate_manual_event(state, turn, {"type": "ag_text_delta", "text": "OK"})
    pool._accumulate_manual_event(state, turn, {"type": "ag_text_delta", "done": True})
    pool._flush_manual_turn(state, turn)

    assert [msg["role"] for msg, _kw in writes] == ["user", "assistant"]
    assert writes[0][0]["source"]["target_agent"] == "gemini"
    assert writes[0][1]["sse_events"][0]["data"]["role"] == "user"
    assert writes[1][0]["content"] == "OK"
    assert writes[1][0]["source"]["observer_manual"] is True
    assert writes[1][1]["sse_events"][0]["type"] == "new_message"


def test_observer_manual_ingest_streams_text_tokens_with_final_msg_id(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession
    from core.conversation_event_bus import ConversationEventBus
    from core.conversation_writer import ConversationWriter

    events = []
    writes = []

    class _Bus:
        def publish_event(self, cid, event_type, data=None):
            events.append((cid, event_type, data or {}))

    class _Writer:
        def enqueue_message(self, msg, **kwargs):
            writes.append((msg, kwargs))

    monkeypatch.setattr(
        ConversationEventBus,
        "instance",
        classmethod(lambda cls: _Bus()),
    )
    monkeypatch.setattr(
        ConversationWriter,
        "for_conversation",
        classmethod(lambda cls, cid: _Writer()),
    )
    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()

    pool._accumulate_manual_event(state, turn, {"type": "ag_text_delta", "text": "hello "})
    pool._accumulate_manual_event(state, turn, {"type": "ag_text_delta", "text": "world"})
    pool._accumulate_manual_event(state, turn, {"type": "ag_text_delta", "done": True})
    pool._flush_manual_turn(state, turn)

    token_events = [event for event in events if event[1] == "token"]
    assert [event[2]["text"] for event in token_events] == ["hello ", "world"]
    assert token_events[0][2]["msg_id"] == token_events[1][2]["msg_id"]
    assert writes[0][0]["role"] == "assistant"
    assert writes[0][0]["msg_id"] == token_events[0][2]["msg_id"]
    assert [evt["type"] for evt in writes[0][1]["sse_events"]] == [
        "new_message", "turn_complete"]


def test_observer_manual_ingest_does_not_fabricate_missing_tool_result(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession
    from core.conversation_writer import ConversationWriter

    writes = []

    class _Writer:
        def enqueue_message(self, msg, **kwargs):
            writes.append((msg, kwargs))

    monkeypatch.setattr(
        ConversationWriter,
        "for_conversation",
        classmethod(lambda cls, cid: _Writer()),
    )
    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()

    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "tool_calls": [{"id": "tc1", "name": "view_file", "arguments": {"path": "a.py"}}],
        "finish_reason": "STOP",
    })
    pool._flush_manual_turn(state, turn)

    assert [msg["role"] for msg, _kw in writes] == ["assistant"]
    assert writes[0][0]["tool_calls"][0]["name"] == "view_file"
    assert all(msg["role"] != "tool" for msg, _kw in writes)


def test_observer_manual_ingest_flushes_tool_call_immediately_without_duplicate(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession
    from core.conversation_writer import ConversationWriter

    writes = []

    class _Writer:
        def enqueue_message(self, msg, **kwargs):
            writes.append((msg, kwargs))

    monkeypatch.setattr(
        ConversationWriter,
        "for_conversation",
        classmethod(lambda cls, cid: _Writer()),
    )
    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()

    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "tool_calls": [{"id": "tc1", "name": "view_file", "arguments": {"path": "a.py"}}],
    })
    assert [msg["role"] for msg, _kw in writes] == ["assistant"]
    assert writes[0][0]["tool_calls"][0]["id"] == "tc1"

    pool._flush_manual_turn(state, turn)

    assert [msg["role"] for msg, _kw in writes] == ["assistant"]


def test_observer_manual_ingest_unwraps_mcp_tool_call(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession
    from core.conversation_writer import ConversationWriter

    writes = []

    class _Writer:
        def enqueue_message(self, msg, **kwargs):
            writes.append((msg, kwargs))

    monkeypatch.setattr(
        ConversationWriter,
        "for_conversation",
        classmethod(lambda cls, cid: _Writer()),
    )
    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()

    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "tool_calls": [{
            "id": "tc1",
            "name": "call_mcp_tool",
            "arguments": {
                "ServerName": "pawflow",
                "ToolName": "read",
                "Arguments": {"path": "a.py", "limit": 20},
            },
        }],
    })
    pool._flush_manual_turn(state, turn)

    tool_call = writes[0][0]["tool_calls"][0]
    assert tool_call["name"] == "read"
    assert tool_call["arguments"] == {"path": "a.py", "limit": 20}
    assert tool_call["tool_origin"] == "mcp"
    sse_data = writes[0][1]["sse_events"][0]["data"]
    assert sse_data["tool"] == "read"
    assert sse_data["arguments"] == {"path": "a.py", "limit": 20}
    assert sse_data["tool_origin"] == "mcp"


def test_observer_manual_ingest_matches_idless_tool_result_by_name(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession
    from core.conversation_writer import ConversationWriter

    writes = []

    class _Writer:
        def enqueue_message(self, msg, **kwargs):
            writes.append((msg, kwargs))

    monkeypatch.setattr(
        ConversationWriter,
        "for_conversation",
        classmethod(lambda cls, cid: _Writer()),
    )
    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()

    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "tool_calls": [{"id": "tc1", "name": "list_dir", "arguments": {"DirectoryPath": "."}}],
    })
    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "tool_results": [{"name": "list_dir", "content": "a.py\nb.py"}],
    })
    pool._flush_manual_turn(state, turn)

    assert [msg["role"] for msg, _kw in writes] == ["assistant", "tool"]
    assert writes[0][0]["tool_calls"][0]["id"] == "tc1"
    assert writes[1][0]["tool_call_id"] == "tc1"
    assert writes[1][1]["sse_events"][0]["data"]["tc_id"] == "tc1"


def test_observer_manual_ingest_does_not_match_native_result_to_mcp_call(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession
    from core.conversation_writer import ConversationWriter

    writes = []

    class _Writer:
        def enqueue_message(self, msg, **kwargs):
            writes.append((msg, kwargs))

    monkeypatch.setattr(
        ConversationWriter,
        "for_conversation",
        classmethod(lambda cls, cid: _Writer()),
    )
    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()

    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "tool_calls": [{
            "id": "tc1", "name": "pawflow/use_tool",
            "arguments": {"tool_name": "read", "arguments": {"path": "x"}},
            "tool_origin": "mcp",
        }],
    })
    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "tool_results": [{"name": "read", "content": "old output", "tool_origin": "native"}],
    })
    pool._flush_manual_turn(state, turn)

    assert [msg["role"] for msg, _kw in writes] == ["assistant"]
    assert all(msg["role"] != "tool" for msg, _kw in writes)


def test_observer_pool_detects_antigravity_interrupted_prompt(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    monkeypatch.setattr(
        pool,
        "capture_tmux_tail",
        lambda _state, lines=80: "\nInterrupted - What should Antigravity CLI do instead?\n",
    )

    assert pool.is_interrupted_prompt(state) is True


def test_observer_manual_ingest_ignores_tool_step_stop(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()

    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "tool_calls": [{"id": "tc1", "name": "view_file", "arguments": {"path": "ctx.md"}}],
    })
    pool._accumulate_manual_event(state, turn, {
        "type": "ag_text_delta",
        "text": "",
        "finish_reason": "STOP",
    })

    assert turn["done"] is False
    assert turn["awaiting_tool_followup"] is True

    pool._accumulate_manual_event(state, turn, {"type": "ag_text_delta", "text": "final answer"})

    assert turn["awaiting_tool_followup"] is False


def test_observer_manual_ingest_idle_flushes_text_without_done(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()

    monkeypatch.setattr("core.antigravity_observer_pool.time.time", lambda: 100.0)
    pool._accumulate_manual_event(state, turn, {"type": "ag_text_delta", "text": "visible in tmux"})
    monkeypatch.setattr("core.antigravity_observer_pool.time.time", lambda: 109.0)

    assert pool._manual_turn_idle_expired(turn, 8.0) is True


def test_observer_manual_ingest_skips_repeated_prompt_during_active_turn(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    turn = pool._new_manual_turn()
    persisted = []
    pool._persist_manual_user_prompt = lambda _state, event: persisted.append(event["text"])

    for event in [
        {"type": "ag_user_prompt", "request_id": "r1", "text": "same prompt"},
        {"type": "ag_user_prompt", "request_id": "r2", "text": "same prompt"},
    ]:
        if turn.get("prompt_seen") and not turn.get("done"):
            continue
        pool._persist_manual_user_prompt(state, event)
        turn["prompt_seen"] = True

    assert persisted == ["same prompt"]


def test_observer_manual_ingest_skips_pawflow_injected_prompt(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    prompt = "PawFlow cold-session bootstrap.\nRead the file first."

    pool._remember_injected_prompt(state, prompt)

    assert pool._consume_injected_prompt(state, prompt) is True
    assert pool._consume_injected_prompt(state, prompt) is False


def test_observer_manual_ingest_skips_pending_injected_prompt_when_proxy_text_differs(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    prompt = (
        "PawFlow cold-session bootstrap.\n\n"
        "Latest turn to answer now:\n"
        "<message role=\"user\">\nreview the last commits\n</message>\n"
    )

    pool._remember_injected_prompt(state, prompt)

    assert pool._consume_injected_prompt(state, "review the last commits") is True
    assert pool._consume_injected_prompt(state, "manual follow-up") is False


def test_observer_manual_ingest_persist_consumes_injected_prompt(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    prompt = (
        "PawFlow cold-session bootstrap.\n\n"
        "Path: /cc_sessions/conv/gemini/.pawflow_ag/initial_context.md\n"
        "Latest turn to answer now:\n<message role=\"user\">hi</message>\n"
    )
    pool._remember_injected_prompt(state, prompt)

    pool._persist_manual_user_prompt(state, {"text": prompt})

    assert state.injected_prompt_hashes == {}
    assert state.pending_injected_prompt_ignores == []


def test_observer_manual_ingest_skips_antigravity_provider_context_prompt(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    state = AntigravityObserverSession(
        key=("alice", "conv", "gemini", "agy_service"),
        name="container",
        workdir=str(tmp_path),
        container_workdir="/cc_sessions/conv/gemini",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    persisted = []
    pool._persist_manual_user_prompt = lambda _state, event: persisted.append(event["text"])
    event = {
        "type": "ag_user_prompt",
        "request_id": "r-provider",
        "text": "<identity>\nYou are Antigravity, a powerful agentic AI coding assistant designed by Google.\n</identity>",
    }

    turn = pool._new_manual_turn()
    prompt_text = event["text"]
    if not (pool._consume_injected_prompt(state, prompt_text)
            or pool._is_provider_context_prompt(prompt_text)):
        pool._persist_manual_user_prompt(state, event)
        turn["prompt_seen"] = True

    assert persisted == []


def test_observer_proxy_log_ready_requires_current_backend(tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool

    log_path = tmp_path / "observer.jsonl"
    log_path.write_text(
        '{"type":"proxy_start","upstream_host":"aicode.googleapis.com"}\n'
        '{"type":"proxy_start","upstream_host":"daily-cloudcode-pa.googleapis.com"}\n',
        encoding="utf-8",
    )

    assert AntigravityObserverPool._proxy_log_ready(str(log_path)) is True


def test_observer_tmux_starts_agy_without_prompt_injection(monkeypatch):
    from core.antigravity_observer_pool import AntigravityObserverPool

    calls = []

    class _Run:
        returncode = 0
        stdout = "true"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)

    AntigravityObserverPool()._start_agy_tmux(
        name="container", container_workdir="/cc_sessions_host/u/c/a")

    assert calls[0][calls[0].index("unshare"):calls[0].index("bash")] == [
        "unshare", "-m", "--propagation", "unchanged", "--"]
    shell = calls[0][-1]
    assert "mkdir -p /cc_sessions" in shell
    assert "mount --bind /cc_sessions_host/u /cc_sessions" in shell
    assert "cd /cc_sessions/c/a" in shell
    assert "tmux new-session -d -s pawflow-agy" in shell
    assert "HOME=/cc_sessions/c/a" in shell
    assert "GEMINI_CLI_HOME=/cc_sessions/c/a" in shell
    assert "CASCADE_ENABLE_MCP_TOOLS=true" in shell
    assert "agy --dangerously-skip-permissions" in shell
    assert "--print" not in shell
    assert "--prompt" not in shell
    assert calls[1][-3:] == ["has-session", "-t", "pawflow-agy"]
    assert "/mcp" in calls[2][-1]
    assert "tmux send-keys -t pawflow-agy Enter" in calls[2][-1]


def test_antigravity_tmux_submit_pastes_complete_prompt_before_enter(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    calls = []
    sleeps = []

    class _Run:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)
    # Patch lands on the shared stdlib time module — only record this test
    # thread's sleeps so leaked background pollers cannot pollute asserts.
    monkeypatch.setattr(
        "core.antigravity_observer_pool.time.sleep",
        lambda value, _t=threading.get_ident(): (
            sleeps.append(value) if threading.get_ident() == _t else None))
    monkeypatch.setattr(AntigravityObserverPool, "_is_alive", lambda self, name: True)

    state = AntigravityObserverSession(
        key=("u", "c", "a", "svc"), name="container",
        workdir=str(tmp_path), container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "observer.jsonl"),
    )

    assert AntigravityObserverPool().send_text(state, "hello") is True
    flat = [cmd for cmd, _kw in calls]
    assert flat[0][-6:] == ["tmux", "send-keys", "-t", "pawflow-agy:0.0", "-X", "cancel"]
    assert flat[1][-3:] == ["tmux", "load-buffer", "-"]
    assert calls[1][1]["input"] == b"hello"
    assert flat[2][-4:] == ["paste-buffer", "-p", "-t", "pawflow-agy:0.0"]
    assert flat[3][-4:] == ["send-keys", "-t", "pawflow-agy:0.0", "Enter"]
    assert sleeps and sleeps[0] >= 0.15


def test_antigravity_tmux_submit_strips_trailing_submit_newline(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    calls = []

    class _Run:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)
    monkeypatch.setattr("core.antigravity_observer_pool.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(AntigravityObserverPool, "_is_alive", lambda self, name: True)

    state = AntigravityObserverSession(
        key=("u", "c", "a", "svc"), name="container",
        workdir=str(tmp_path), container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "observer.jsonl"),
    )

    assert AntigravityObserverPool().send_text(state, "hello\n") is True
    flat = [cmd for cmd, _kw in calls]
    assert flat[0][-6:] == ["tmux", "send-keys", "-t", "pawflow-agy:0.0", "-X", "cancel"]
    assert calls[1][1]["input"] == b"hello"
    assert flat[2][-4:] == ["paste-buffer", "-p", "-t", "pawflow-agy:0.0"]
    assert flat[3][-4:] == ["send-keys", "-t", "pawflow-agy:0.0", "Enter"]


def test_antigravity_tmux_submit_rejects_duplicate_in_flight_prompt(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    calls = []

    class _Run:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)
    monkeypatch.setattr("core.antigravity_observer_pool.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(AntigravityObserverPool, "_is_alive", lambda self, name: True)

    state = AntigravityObserverSession(
        key=("u", "c", "a", "svc"), name="container",
        workdir=str(tmp_path), container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()

    assert pool.send_text(state, "hello\n") is True
    assert pool.send_text(state, "hello\n") is False
    assert state.last_error == "duplicate in-flight Antigravity tmux submit"
    flat = [cmd for cmd, _kw in calls]
    assert [cmd[-3:] for cmd in flat].count(["tmux", "load-buffer", "-"]) == 1
    assert [cmd[-4:] for cmd in flat].count(["paste-buffer", "-p", "-t", "pawflow-agy:0.0"]) == 1
    assert [cmd[-4:] for cmd in flat].count(["send-keys", "-t", "pawflow-agy:0.0", "Enter"]) == 1

    pool.mark_submit_complete(state)
    assert pool.send_text(state, "hello\n") is True
    flat = [cmd for cmd, _kw in calls]
    assert [cmd[-3:] for cmd in flat].count(["tmux", "load-buffer", "-"]) == 2


def test_antigravity_tmux_submit_does_not_replay_large_prompt_in_chunks(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    calls = []
    sleeps = []

    class _Run:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)
    # Patch lands on the shared stdlib time module — only record this test
    # thread's sleeps so leaked background pollers cannot pollute asserts.
    monkeypatch.setattr(
        "core.antigravity_observer_pool.time.sleep",
        lambda value, _t=threading.get_ident(): (
            sleeps.append(value) if threading.get_ident() == _t else None))
    monkeypatch.setattr(AntigravityObserverPool, "_is_alive", lambda self, name: True)

    state = AntigravityObserverSession(
        key=("u", "c", "a", "svc"), name="container",
        workdir=str(tmp_path), container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "observer.jsonl"),
    )

    payload = "x" * 513

    assert AntigravityObserverPool().send_text(state, payload) is True
    flat = [cmd for cmd, _kw in calls]
    assert flat[0][-6:] == ["tmux", "send-keys", "-t", "pawflow-agy:0.0", "-X", "cancel"]
    assert calls[1][1]["input"] == payload.encode("utf-8")
    assert flat[2][-4:] == ["paste-buffer", "-p", "-t", "pawflow-agy:0.0"]
    assert flat[3][-4:] == ["send-keys", "-t", "pawflow-agy:0.0", "Enter"]
    assert len(flat) == 4
    assert sleeps[-1] >= 0.15


def test_antigravity_tmux_submit_aborts_if_session_invalidated_after_paste(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    calls = []

    class _Run:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)
    monkeypatch.setattr("core.antigravity_observer_pool.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(AntigravityObserverPool, "_is_alive", lambda self, name: True)

    state = AntigravityObserverSession(
        key=("u", "c", "a", "svc"), name="container",
        workdir=str(tmp_path), container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "observer.jsonl"),
    )
    pool = AntigravityObserverPool()
    original_paste = pool._paste_buffer

    def paste_and_invalidate(_state):
        ok = original_paste(_state)
        _state.manual_ingest_stop.set()
        return ok

    monkeypatch.setattr(pool, "_paste_buffer", paste_and_invalidate)

    assert pool.send_text(state, "hello") is False
    assert state.last_error == "Container container was invalidated during tmux submit"
    flat = [cmd for cmd, _kw in calls]
    assert flat[0][-6:] == ["tmux", "send-keys", "-t", "pawflow-agy:0.0", "-X", "cancel"]
    assert flat[1][-3:] == ["tmux", "load-buffer", "-"]
    assert flat[2][-4:] == ["paste-buffer", "-p", "-t", "pawflow-agy:0.0"]
    assert all(cmd[-1] != "Enter" for cmd in flat)


def test_antigravity_pool_kill_and_evict_scopes_by_conv_and_agent(
        monkeypatch, tmp_path):
    from core.antigravity_observer_pool import (
        AntigravityObserverPool,
        AntigravityObserverSession,
    )

    pool = AntigravityObserverPool()
    killed = []

    def fake_kill(state):
        killed.append(state.name)

    monkeypatch.setattr(pool, "kill", fake_kill)

    def add(key, name):
        pool._sessions[key] = AntigravityObserverSession(
            key=key, name=name, workdir=str(tmp_path / name),
            container_workdir=f"/cc_sessions/{key[1]}/{key[2]}",
            log_path=str(tmp_path / f"{name}.jsonl"),
        )

    add(("u", "c", "agent-a", "svc1"), "a1")
    add(("u", "c", "agent-a", "svc2"), "a2")
    add(("u", "c", "agent-b", "svc1"), "b1")
    add(("u", "other", "agent-a", "svc1"), "other")

    assert pool.kill_and_evict_by_conv_agent("c", "agent-a", "compact") == 2
    assert killed == ["a1", "a2"]
    assert ("u", "c", "agent-b", "svc1") in pool._sessions
    assert ("u", "other", "agent-a", "svc1") in pool._sessions

    assert pool.kill_and_evict_by_conv("c", "invalidate") == 1
    assert killed == ["a1", "a2", "b1"]
    assert list(pool._sessions) == [("u", "other", "agent-a", "svc1")]


def test_antigravity_kill_sends_sigkill_before_remove(monkeypatch, tmp_path):
    from core.antigravity_observer_pool import AntigravityObserverPool, AntigravityObserverSession

    calls = []

    class _Run:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Run()

    monkeypatch.setattr("core.antigravity_observer_pool.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.antigravity_observer_pool.subprocess.run", fake_run)
    monkeypatch.setattr("core.antigravity_observer_pool.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(AntigravityObserverPool, "_is_alive", lambda self, name: False)

    state = AntigravityObserverSession(
        key=("u", "c", "a", "svc"), name="container",
        workdir=str(tmp_path), container_workdir="/cc_sessions/c/a",
        log_path=str(tmp_path / "observer.jsonl"),
    )

    AntigravityObserverPool().kill(state)

    assert calls[0] == ["docker", "kill", "--signal=KILL", "container"]
    assert calls[1] == ["docker", "rm", "-f", "container"]
    assert state.manual_ingest_stop.is_set()


def test_chat_ui_exposes_single_agent_tmux_action_for_antigravity():
    terminal = Path("tasks/io/chat_ui/terminal.js").read_text(encoding="utf-8")
    template = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
    commands = Path("tasks/io/chat_ui/commands.js").read_text(encoding="utf-8")
    service_flow = Path("tasks/ai/actions/service_flow.py").read_text(encoding="utf-8")

    assert "function cmdAgentTmux" in terminal
    assert "provider === 'antigravity-interactive'" in terminal
    assert "open_antigravity_interactive_terminal" in terminal
    assert "service_id: serviceId || ''" in terminal
    assert "cmdAgentTmux()" in template
    assert "cmdAntigravityObserver" not in terminal
    assert "Antigravity Observer" not in template
    assert "'/agy-observe'" not in commands
    assert "'/agy'" not in commands
    assert "from core.antigravity_observer_pool import AntigravityObserverPool" in service_flow
    assert 'action in {"open_antigravity_interactive_terminal", "start_antigravity_observer"}' in service_flow
    assert "importlib.reload(ag_pool_mod)" not in service_flow


def test_observer_proxy_mirrors_missing_alpn_as_http11(monkeypatch):
    import ssl
    from tools import ag_observer_proxy

    protocols = []

    class _Ctx:
        def set_alpn_protocols(self, values):
            protocols.append(values)

    monkeypatch.setattr(ssl, "create_default_context", lambda: _Ctx())
    monkeypatch.setattr(ag_observer_proxy, "_resolve_upstream_ips", lambda: [])

    try:
        ag_observer_proxy._connect_upstream("")
    except ConnectionError:
        pass

    assert protocols == [["http/1.1"]]


def test_observer_proxy_clears_upstream_timeout_after_connect(monkeypatch):
    from tools import ag_observer_proxy

    created = []
    timeouts = []

    class _Ctx:
        def set_alpn_protocols(self, values):
            pass

        def wrap_socket(self, raw, server_hostname):
            return wrapped

    class _Wrapped:
        def settimeout(self, value):
            timeouts.append(value)

    wrapped = _Wrapped()

    monkeypatch.setattr(ag_observer_proxy, "_resolve_upstream_ips", lambda: ["1.2.3.4"])
    monkeypatch.setattr(ag_observer_proxy.ssl, "create_default_context", lambda: _Ctx())
    monkeypatch.setattr(
        ag_observer_proxy.socket,
        "create_connection",
        lambda address, timeout: created.append((address, timeout)) or object(),
    )

    assert ag_observer_proxy._connect_upstream("") is wrapped
    assert created == [(('1.2.3.4', 443), 20)]
    assert timeouts == [None]


def test_observer_http1_parser_logs_request_without_auth(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)

    observer = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    observer.feed(
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\n"
        b"Host: daily-cloudcode-pa.googleapis.com\r\n"
        b"Authorization: Bearer secret\r\n"
        b"Content-Length: 5\r\n\r\nhello"
    )

    assert events[0]["type"] == "http1_headers"
    assert events[0]["method"] == "POST"
    assert events[0]["path"] == "/v1internal:streamGenerateContent?alt=sse"
    assert ["Authorization", "<redacted>"] in events[0]["headers"]
    assert events[1]["type"] == "http1_body"
    assert events[1]["bytes"] == 5


def test_observer_http1_parser_handles_keepalive_requests(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)

    observer = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    observer.feed(
        b"POST /v1internal:listExperiments HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}"
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello"
    )

    paths = [event.get("path") for event in events if event.get("type") == "http1_headers"]
    assert paths == [
        "/v1internal:listExperiments",
        "/v1internal:streamGenerateContent?alt=sse",
    ]


def test_observer_http1_parser_emits_json_and_sse_shapes(monkeypatch):
    from tools import ag_observer_proxy

    events = []
    monkeypatch.setattr(ag_observer_proxy, "_event", events.append)

    req = ag_observer_proxy.HTTP1Observer("conn1", "client_to_upstream")
    body = b'{"token":"secret","contents":[{"role":"user","parts":[{"text":"hello"}]}]}'
    req.feed(
        b"POST /v1internal:streamGenerateContent?alt=sse HTTP/1.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    summaries = [event for event in events if event.get("type") == "http1_body_summary"]
    assert summaries[-1]["json_shape"]["fields"]["token"] == {"type": "redacted"}
    assert summaries[-1]["json_shape"]["fields"]["contents"]["type"] == "array"

    events.clear()
    resp = ag_observer_proxy.HTTP1Observer("conn1", "upstream_to_client")
    chunk = b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}}\n\n'
    resp.feed(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n0\r\n\r\n"
    )
    summaries = [event for event in events if event.get("type") == "http1_body_summary"]
    assert summaries[-1]["sse_event_count"] == 1
    assert summaries[-1]["sse_events"][0]["json_shape"]["fields"]["response"]["type"] == "object"
    deltas = [event for event in events if event.get("type") == "ag_text_delta"]
    assert deltas[-1]["text"] == "hi"

    events.clear()
    resp = ag_observer_proxy.HTTP1Observer("conn1", "upstream_to_client")
    crlf_chunk = b'data: {"response":{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}}\r\n\r\n'
    resp.feed(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(crlf_chunk):x}\r\n".encode() + crlf_chunk + b"\r\n0\r\n\r\n"
    )
    deltas = [event for event in events if event.get("type") == "ag_text_delta"]
    assert deltas[-1]["text"] == "ok"
