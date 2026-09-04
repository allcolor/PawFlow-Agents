"""Managed MCP: spec table, mode-aware pool launch and reuse (WP0/WP3).

Launch snapshot tests prove the expected CLI wiring in ``managed_mcp`` mode
and, negatively, that no interception artifact appears: no proxy process, no
generated CA, no vendor host override, no interception environment variable.
The MITM launch snapshots in the existing suites remain the other half.
"""
import json
from types import SimpleNamespace

import pytest

from core.managed_mcp_spec import (
    MANAGED_MCP_LAUNCH_REVISION,
    MANAGED_MCP_OBSERVATION_MODE,
    MANAGED_MCP_PROVIDERS,
    MITM_OBSERVATION_MODE,
    is_managed_mcp_provider,
    managed_mcp_capability_matrix,
    managed_mcp_observation_mode,
    managed_mcp_pool_family,
    managed_mcp_source_input,
    managed_mcp_spec,
    require_available,
)


class _Run:
    returncode = 0
    stdout = "true"
    stderr = ""


def _record_runs(monkeypatch, module):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Run()
    monkeypatch.setattr(f"{module}.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr(f"{module}.subprocess.run", fake_run)
    return calls


class TestSpecTable:
    def test_exact_provider_values(self):
        assert set(MANAGED_MCP_PROVIDERS) == {"cc_mcp", "codex_mcp", "agy_mcp"}
        for name, spec in MANAGED_MCP_PROVIDERS.items():
            assert spec.provider == name
            assert spec.final_source == "stop_hook"
            assert spec.thinking_source == "unavailable"
            assert spec.live_preempt is False

    def test_families_and_aliases(self):
        assert managed_mcp_spec("cc_mcp").credential_family == "claude-code"
        assert managed_mcp_spec("codex_mcp").credential_family == "codex-app-server"
        assert managed_mcp_spec("agy_mcp").credential_family == "gemini"
        assert managed_mcp_pool_family("cc_mcp") == "claude-code-interactive"
        assert managed_mcp_pool_family("codex_mcp") == "codex-interactive"
        assert managed_mcp_pool_family("claude-code-interactive") == "claude-code-interactive"
        assert managed_mcp_source_input("cc_mcp") == "cc_mcp_tmux"
        assert managed_mcp_source_input("codex-interactive") == ""

    def test_observation_mode_selection(self):
        assert managed_mcp_observation_mode("cc_mcp") == MANAGED_MCP_OBSERVATION_MODE
        assert managed_mcp_observation_mode("claude-code-interactive") == MITM_OBSERVATION_MODE
        assert managed_mcp_observation_mode("") == MITM_OBSERVATION_MODE
        assert is_managed_mcp_provider("external_mcp") is False

    def test_agy_is_available_from_native_stop_final(self):
        spec = managed_mcp_spec("agy_mcp")
        assert spec.available is True
        assert spec.unavailable_reason == ""
        assert require_available("agy_mcp") is spec
        assert require_available("cc_mcp").available is True
        with pytest.raises(ValueError):
            require_available("claude-code-interactive")

    def test_capability_matrix_is_honest(self):
        matrix = managed_mcp_capability_matrix()
        assert matrix["cc_mcp"]["usage_source"] == "unavailable"
        assert matrix["cc_mcp"]["text_streaming"] == "final_only"
        assert matrix["cc_mcp"]["builtin_tools_visible"] is True
        assert matrix["codex_mcp"]["builtin_tools_visible"] is False
        assert matrix["codex_mcp"]["context_source"] == "codex_rollout_token_count"
        assert matrix["agy_mcp"]["available"] is True
        assert matrix["cc_mcp"]["label"] == "Claude Code \u2014 MCP hooks"


class TestReuseCompatibility:
    def test_managed_state_needs_matching_provider_mode_and_revision(self):
        from core.claude_code_interactive_pool import (
            InteractiveClaudeCodePool,
            InteractiveContainer,
        )
        pool = InteractiveClaudeCodePool()
        state = InteractiveContainer(
            key=("u", "c", "a", "svc"), name="n", workdir="/w",
            container_workdir="/cc_sessions/c/a", session_token="s",
            event_service_id="e", internal_token="i",
            provider="cc_mcp", observation_mode=MANAGED_MCP_OBSERVATION_MODE,
            launch_revision=MANAGED_MCP_LAUNCH_REVISION)
        assert pool._session_compatible(state, SimpleNamespace(provider="cc_mcp"))
        assert not pool._session_compatible(
            state, SimpleNamespace(provider="claude-code-interactive"))
        state.launch_revision = "0"
        assert not pool._session_compatible(state, SimpleNamespace(provider="cc_mcp"))

    def test_mitm_state_refuses_managed_client_and_keeps_opinionless_client(self):
        from core.claude_code_interactive_pool import (
            InteractiveClaudeCodePool,
            InteractiveContainer,
        )
        pool = InteractiveClaudeCodePool()
        state = InteractiveContainer(
            key=("u", "c", "a", "svc"), name="n", workdir="/w",
            container_workdir="/cc_sessions/c/a", session_token="s",
            event_service_id="e", internal_token="i")
        assert state.observation_mode == MITM_OBSERVATION_MODE
        assert state.provider == "claude-code-interactive"
        assert not pool._session_compatible(state, SimpleNamespace(provider="cc_mcp"))
        assert pool._session_compatible(
            state, SimpleNamespace(provider="claude-code-interactive"))
        # A test double whose provider is not a string has no opinion.
        assert pool._session_compatible(state, SimpleNamespace(provider=object()))
        assert pool._session_compatible(state, object())

    def test_ensure_started_recreates_incompatible_live_session(self, monkeypatch):
        from core.claude_code_interactive_pool import (
            InteractiveClaudeCodePool,
            InteractiveContainer,
        )
        pool = InteractiveClaudeCodePool()
        old = InteractiveContainer(
            key=("u", "c", "a", "svc"), name="old", workdir="/w",
            container_workdir="/cc_sessions/c/a", session_token="s-old",
            event_service_id="e", internal_token="i")
        pool._sessions[old.key] = old
        killed, recovered, launched = [], [], []
        monkeypatch.setattr(pool, "ensure_sweeper", lambda **kw: None)
        monkeypatch.setattr(pool, "_is_alive", lambda name: True)
        monkeypatch.setattr(pool, "_tmux_is_alive", lambda name: True)
        monkeypatch.setattr(pool, "_kill_container", killed.append)
        monkeypatch.setattr(pool, "_recover_container_tokens", recovered.append)
        monkeypatch.setattr(pool, "_unregister_event_session", lambda s: None)
        monkeypatch.setattr(pool, "_credentials_pool_size", lambda *a: 1)

        new = InteractiveContainer(
            key=old.key, name="new", workdir="/w",
            container_workdir="/cc_sessions/c/a", session_token="s-new",
            event_service_id="e", internal_token="i", provider="cc_mcp",
            observation_mode=MANAGED_MCP_OBSERVATION_MODE,
            launch_revision=MANAGED_MCP_LAUNCH_REVISION)

        def _start_new(client, model, user_id, conversation_id, agent_name,
                       key, pool_index=-1):
            launched.append(key)
            return new
        monkeypatch.setattr(pool, "_start_new", _start_new)
        client = SimpleNamespace(provider="cc_mcp", _agent_service="svc",
                                 api_key="", idle_ttl_seconds=None)
        got = pool.ensure_started(client, "m", "u", "c", "a")
        assert got is new
        assert killed == ["old"] and recovered == [old]
        assert launched == [old.key]
        # Same client again: the managed session is reused as-is.
        assert pool.ensure_started(client, "m", "u", "c", "a") is new
        assert launched == [old.key]

    def test_list_sessions_reports_concrete_provider(self, monkeypatch):
        from core.claude_code_interactive_pool import (
            InteractiveClaudeCodePool,
            InteractiveContainer,
        )
        from core.codex_interactive_pool import CodexInteractivePool
        pool = InteractiveClaudeCodePool()
        managed = InteractiveContainer(
            key=("u", "c", "a", "svc"), name="n", workdir="/w",
            container_workdir="/cc_sessions/c/a", session_token="s",
            event_service_id="e", internal_token="i", provider="cc_mcp",
            observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        pool._sessions[managed.key] = managed
        rows = pool.list_sessions_snapshot("u", "c")
        assert rows[0]["provider"] == "cc_mcp"
        assert rows[0]["observation_mode"] == "managed_mcp"

        codex = CodexInteractivePool()
        mitm = InteractiveContainer(
            key=("u", "c", "b", "svc"), name="m", workdir="/w",
            container_workdir="/cc_sessions/c/b", session_token="s2",
            event_service_id="e", internal_token="i")
        cmcp = InteractiveContainer(
            key=("u", "c", "d", "svc"), name="k", workdir="/w",
            container_workdir="/cc_sessions/c/d", session_token="s3",
            event_service_id="e", internal_token="i", provider="codex_mcp",
            observation_mode=MANAGED_MCP_OBSERVATION_MODE)
        codex._sessions[mitm.key] = mitm
        codex._sessions[cmcp.key] = cmcp
        by_agent = {row["agent_name"]: row["provider"]
                    for row in codex.list_sessions_snapshot("u", "c")}
        assert by_agent == {"b": "codex-interactive", "d": "codex_mcp"}


class TestClaudeLaunch:
    def test_managed_tmux_has_no_interception_env(self, monkeypatch):
        from core.claude_code_interactive_pool import InteractiveClaudeCodePool
        calls = _record_runs(monkeypatch, "core.claude_code_interactive_pool")
        InteractiveClaudeCodePool()._start_claude_tmux(
            name="container", container_workdir="/cc_sessions_host/u/c/a",
            mcp_path="/cc_sessions/c/a/.mcp.json", model="opus",
            ca_path="", session_token="session-token",
            event_url="wss://events", event_token="event-token",
            internal_token="internal-token", hook_client="cc")
        shell = calls[0][-1]
        assert "NODE_EXTRA_CA_CERTS" not in shell
        assert "CLAUDE_CODE_CERT_STORE" not in shell
        assert "ANTHROPIC_BASE_URL" not in shell
        assert "PAWFLOW_CCI_HOOK_CLIENT=cc" in shell
        # The MCP bridge and the hook credentials are unchanged.
        assert "--strict-mcp-config" in shell
        assert "--mcp-config /cc_sessions/c/a/.mcp.json" in shell
        assert "PAWFLOW_CCI_SESSION_TOKEN=session-token" in shell
        assert "PAWFLOW_CCI_INJECTED_PROMPTS=" in shell
        assert "--resume" not in shell

    def test_mitm_tmux_snapshot_is_unchanged(self, monkeypatch):
        from core.claude_code_interactive_pool import InteractiveClaudeCodePool
        calls = _record_runs(monkeypatch, "core.claude_code_interactive_pool")
        InteractiveClaudeCodePool()._start_claude_tmux(
            name="container", container_workdir="/cc_sessions_host/u/c/a",
            mcp_path="/cc_sessions/c/a/.mcp.json", model="opus",
            ca_path="/cc_sessions/c/a/ca.crt", session_token="s",
            event_url="wss://events", event_token="e", internal_token="i")
        shell = calls[0][-1]
        assert "NODE_EXTRA_CA_CERTS=/cc_sessions/c/a/ca.crt" in shell
        assert "CLAUDE_CODE_CERT_STORE=system" in shell
        assert "PAWFLOW_CCI_HOOK_CLIENT" not in shell

    def test_managed_container_has_no_vendor_redirect_or_proxy_files(self, monkeypatch):
        from core.claude_code_interactive_pool import InteractiveClaudeCodePool
        calls = _record_runs(monkeypatch, "core.claude_code_interactive_pool")
        monkeypatch.setattr("core._cci_pool_spawn.to_host_path", lambda p: p)
        monkeypatch.setattr("core._cci_pool_spawn.translate_path", lambda p: p)
        monkeypatch.setattr("core._cci_pool_spawn.get_server_id", lambda: "server1234567890")
        monkeypatch.setattr("core._cci_pool_spawn.ca_private_key_is_host_only", lambda m: True)
        InteractiveClaudeCodePool()._spawn_container(
            user_id="u", conversation_id="c", agent_name="a",
            upstream_host="api.anthropic.com", intercept=False)
        run_cmd = calls[0]
        assert "api.anthropic.com:127.0.0.1" not in run_cmd
        assert "host.docker.internal:host-gateway" in run_cmd
        copied = [c[-2] for c in calls if c[:2] == ["docker", "cp"]]
        assert not any("cc_interactive_proxy.py" in src for src in copied)
        assert not any("cc_interactive_observers.py" in src for src in copied)
        assert not any("cc_interactive_ws.py" in src for src in copied)
        assert any(src.endswith("cc_interactive_hook.py") for src in copied)
        assert any(src.endswith("mcp_bridge.py") for src in copied)

    def test_mitm_container_still_redirects_vendor_host(self, monkeypatch):
        from core.claude_code_interactive_pool import InteractiveClaudeCodePool
        calls = _record_runs(monkeypatch, "core.claude_code_interactive_pool")
        monkeypatch.setattr("core._cci_pool_spawn.to_host_path", lambda p: p)
        monkeypatch.setattr("core._cci_pool_spawn.translate_path", lambda p: p)
        monkeypatch.setattr("core._cci_pool_spawn.get_server_id", lambda: "server1234567890")
        monkeypatch.setattr("core._cci_pool_spawn.ca_private_key_is_host_only", lambda m: True)
        InteractiveClaudeCodePool()._spawn_container(
            user_id="u", conversation_id="c", agent_name="a",
            upstream_host="api.anthropic.com")
        assert "api.anthropic.com:127.0.0.1" in calls[0]
        copied = [c[-2] for c in calls if c[:2] == ["docker", "cp"]]
        assert any("cc_interactive_proxy.py" in src for src in copied)

    def test_managed_start_new_skips_every_interception_step(self, monkeypatch, tmp_path):
        """End-to-end launch orchestration in managed mode: no leaf cert, no
        CA install, no proxy start, native endpoint, provider registered."""
        import core._cci_pool_spawn as spawn
        from core.claude_code_interactive_pool import InteractiveClaudeCodePool
        pool = InteractiveClaudeCodePool()
        steps = []
        registered = {}

        class _Events:
            service_id = "events"

            def register_session(self, token, **kw):
                registered.update(kw)
        monkeypatch.setattr(
            "services.cc_interactive_event_service.get_or_create_cc_interactive_event_service",
            lambda: ("wss://localhost:1/ws", "tok", _Events()))
        monkeypatch.setattr(spawn, "get_host_ip", lambda: "10.0.0.1")
        monkeypatch.setattr(spawn, "generate_leaf",
                            lambda *a, **k: steps.append("generate_leaf"))
        monkeypatch.setattr(pool, "_spawn_container",
                            lambda **kw: steps.append(("spawn", kw["intercept"])) or "ctr")
        monkeypatch.setattr(pool, "_write_hook_settings", lambda *a, **k: steps.append("hooks"))
        monkeypatch.setattr(pool, "_install_ca", lambda *a, **k: steps.append("install_ca"))
        monkeypatch.setattr(pool, "_start_proxy", lambda **k: steps.append("start_proxy"))
        monkeypatch.setattr(pool, "_start_claude_tmux",
                            lambda **k: steps.append(("tmux", k["ca_path"], k["hook_client"],
                                                      k["anthropic_base_url"])))
        monkeypatch.setattr("core.cli_process_config.shell_cli_environment",
                            lambda *a, **k: "")
        client = SimpleNamespace(
            provider="cc_mcp", base_url="https://vendor.example/v1",
            api_key="sk-test", _agent_service="svc",
            _get_session_workdir=lambda c, a, u: str(tmp_path),
            _setup_credentials=lambda *a, **k: None,
            _setup_mcp_config=lambda *a, **k: (str(tmp_path / ".mcp.json"), "itok"),
            _cfg=lambda k, d="": d)
        state = pool._start_new(client, "opus", "u", "c", "a", ("u", "c", "a", "svc"))
        assert "generate_leaf" not in steps
        assert "install_ca" not in steps
        assert "start_proxy" not in steps
        assert ("spawn", False) in steps
        assert ("tmux", "", "cc", "https://vendor.example/v1") in steps
        assert state.provider == "cc_mcp"
        assert state.observation_mode == MANAGED_MCP_OBSERVATION_MODE
        assert state.launch_revision == MANAGED_MCP_LAUNCH_REVISION
        assert state.proxy_started is False
        assert registered["provider"] == "cc_mcp"
        assert registered["observation_mode"] == MANAGED_MCP_OBSERVATION_MODE


class TestCodexLaunch:
    def test_managed_codex_tmux_carries_hook_client_and_no_local_endpoint(self, monkeypatch):
        from core.codex_interactive_pool import _CodexInteractiveSpawnMixin
        calls = _record_runs(monkeypatch, "core.codex_interactive_pool")
        spawn = _CodexInteractiveSpawnMixin()
        spawn.run_uid, spawn.run_gid = "1000", "1000"
        spawn._user_spec = lambda: "1000:1000"
        spawn._start_codex_tmux(
            name="ctr", container_workdir="/cc_sessions_host/u/c/a",
            model="gpt-5", effort="", session_token="s",
            event_url="wss://events", event_token="e", internal_token="i",
            codex_base_url="", hook_client="codex")
        shell = calls[0][-1]
        assert "PAWFLOW_CCI_HOOK_CLIENT=codex" in shell
        assert "OPENAI_BASE_URL" not in shell
        assert "CODEX_HOME=/cc_sessions/c/a/.codex" in shell

    def test_codex_hooks_writer_is_the_managed_hook_source(self, tmp_path):
        from core.codex_interactive_pool import _CodexInteractiveSpawnMixin
        _CodexInteractiveSpawnMixin._write_codex_hooks(str(tmp_path))
        hooks = json.loads((tmp_path / ".codex" / "hooks.json").read_text())["hooks"]
        assert set(hooks) == {"UserPromptSubmit", "Stop", "PreCompact",
                              "PostCompact", "SessionEnd"}
        assert hooks["Stop"][0]["hooks"][0]["command"] == (
            "python3 /opt/pawflow/cc_interactive_hook.py")


class TestAntigravityPool:
    def test_managed_pool_contract(self, monkeypatch):
        from core._antigravity_base import AntigravityObserverSession
        from core.antigravity_observer_pool import AntigravityObserverPool
        from core.llm_providers.managed_mcp import LLMManagedMcpMixin

        pool = AntigravityObserverPool()
        monkeypatch.setattr(AntigravityObserverPool, "instance",
                            classmethod(lambda cls: pool))
        assert LLMManagedMcpMixin._managed_mcp_pool(
            managed_mcp_spec("agy_mcp")) is pool
        state = AntigravityObserverSession(
            key=("u", "c", "a", "svc"), name="ctr", workdir="/w",
            container_workdir="/cc_sessions/c/a", log_path="/l",
            session_token="session")
        pool._sessions[state.key] = state
        pool.begin_turn(state)
        assert state.in_flight == 1
        pool.end_turn(state)
        assert state.in_flight == 0
        assert pool.find_by_session_token("session") is state
        monkeypatch.setattr(pool, "_is_alive", lambda name: True)
        monkeypatch.setattr(pool, "_tmux_is_alive", lambda name: True)
        assert pool.session_is_live("ctr") is True

    def test_managed_launch_failure_unregisters_event_session(
            self, monkeypatch, tmp_path):
        from core.antigravity_observer_pool import AntigravityObserverPool

        registered = []
        unregistered = []

        class _Events:
            def register_session(self, token, **kwargs):
                registered.append(token)

            def unregister_session(self, token):
                unregistered.append(token)

        pool = AntigravityObserverPool()
        client = SimpleNamespace(
            provider="agy_mcp", _agent_service="",
            _gemini_setup_credentials=lambda workdir: None)
        monkeypatch.setattr(pool, "_workdir", lambda *args: str(tmp_path))
        monkeypatch.setattr(pool, "_write_antigravity_config",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr(pool, "_write_agy_managed_hooks",
                            lambda workdir: {})
        monkeypatch.setattr(
            "core.cli_process_config.shell_cli_environment",
            lambda *args, **kwargs: "")
        monkeypatch.setattr("core.docker_utils.get_host_ip",
                            lambda: "host.docker.internal")
        monkeypatch.setattr(
            "services.cc_interactive_event_service."
            "get_or_create_cc_interactive_event_service",
            lambda: ("ws://localhost/events", "secret", _Events()))

        def fail_spawn(**kwargs):
            raise RuntimeError("spawn failed")

        monkeypatch.setattr(pool, "_spawn_container", fail_spawn)

        with pytest.raises(RuntimeError, match="spawn failed"):
            pool._start_new("u", "c", "a", "svc", "gemini", client=client)

        assert len(registered) == 1
        assert unregistered == registered

    def test_managed_liveness_ignores_proxy_log(self, monkeypatch):
        from core._antigravity_base import AntigravityObserverSession
        from core.antigravity_observer_pool import AntigravityObserverPool
        pool = AntigravityObserverPool()
        state = AntigravityObserverSession(
            key=("u", "c", "a", "svc"), name="ctr", workdir="/w",
            container_workdir="/cc_sessions/c/a", log_path="/nonexistent.jsonl")
        state.observation_mode = MANAGED_MCP_OBSERVATION_MODE
        monkeypatch.setattr(pool, "_is_alive", lambda name: True)
        monkeypatch.setattr(pool, "_tmux_is_alive", lambda name: True)
        consulted = []
        monkeypatch.setattr(pool, "_proxy_log_ready",
                            lambda path: consulted.append(path) or False)
        assert pool._is_usable(state) is True
        assert consulted == []
        monkeypatch.setattr(pool, "_tmux_is_alive", lambda name: False)
        assert pool._is_usable(state) is False
        # MITM mode keeps consulting the proxy log.
        state.observation_mode = MITM_OBSERVATION_MODE
        assert pool._is_usable(state) is False
        assert consulted == ["/nonexistent.jsonl"]

    def test_managed_hooks_are_written_to_shared_config(self, tmp_path):
        from core.antigravity_observer_pool import AntigravityObserverPool
        hooks = AntigravityObserverPool._write_agy_managed_hooks(str(tmp_path))
        assert set(hooks) == {"PreInvocation", "Stop", "SessionEnd"}
        shared = json.loads((tmp_path / ".gemini" / "config" / "hooks.json").read_text())
        assert shared["hooks"] == hooks
        cli = json.loads((tmp_path / ".gemini" / "antigravity-cli" / "settings.json").read_text())
        assert cli["hooks"] == hooks
        assert hooks["Stop"][0]["hooks"][0]["command"] == (
            "python3 /opt/pawflow/cc_interactive_hook.py")

    def test_managed_agy_container_mounts_hook_not_proxy(self, monkeypatch):
        from core.antigravity_observer_pool import AntigravityObserverPool
        calls = _record_runs(monkeypatch, "core.antigravity_observer_pool")
        monkeypatch.setattr("core.antigravity_observer_pool.to_host_path", lambda p: p)
        monkeypatch.setattr("core.antigravity_observer_pool.translate_path", lambda p: p)
        monkeypatch.setattr("core.antigravity_observer_pool.get_server_id", lambda: "server1234567890")
        monkeypatch.setattr("core.antigravity_observer_pool.ca_private_key_is_host_only", lambda m: True)
        pool = AntigravityObserverPool()
        pool._spawn_container(user_id="u", conversation_id="c", agent_name="a",
                              intercept=False)
        assert "daily-cloudcode-pa.googleapis.com:127.0.0.1" not in calls[0]
        copied = [c[-2] for c in calls if c[:2] == ["docker", "cp"]]
        assert not any("ag_observer_proxy.py" in src for src in copied)
        assert any(src.endswith("cc_interactive_hook.py") for src in copied)

    def test_agy_tmux_carries_hook_env_when_managed(self, monkeypatch):
        from core.antigravity_observer_pool import AntigravityObserverPool
        calls = _record_runs(monkeypatch, "core.antigravity_observer_pool")
        pool = AntigravityObserverPool()
        monkeypatch.setattr(pool, "_prime_agy_mcp", lambda name: None)
        pool._start_agy_tmux(
            name="ctr", container_workdir="/cc_sessions_host/u/c/a",
            hook_env={"PAWFLOW_CCI_HOOK_CLIENT": "agy",
                      "PAWFLOW_CCI_SESSION_TOKEN": "s"})
        shell = calls[0][-1]
        assert "PAWFLOW_CCI_HOOK_CLIENT=agy" in shell
        assert "PAWFLOW_CCI_SESSION_TOKEN=s" in shell
        calls.clear()
        pool._start_agy_tmux(name="ctr", container_workdir="/cc_sessions_host/u/c/a")
        assert "PAWFLOW_CCI_HOOK_CLIENT" not in calls[0][-1]

    def test_antigravity_reuse_refuses_mode_mismatch(self):
        from core._antigravity_base import AntigravityObserverSession
        from core.antigravity_observer_pool import AntigravityObserverPool
        state = AntigravityObserverSession(
            key=("u", "c", "a", "svc"), name="ctr", workdir="/w",
            container_workdir="/cc_sessions/c/a", log_path="/l")
        assert AntigravityObserverPool._session_compatible(
            state, SimpleNamespace(provider="antigravity-interactive"))
        assert not AntigravityObserverPool._session_compatible(
            state, SimpleNamespace(provider="agy_mcp"))
