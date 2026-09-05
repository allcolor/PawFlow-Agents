"""Managed native ACP launch, auth scope, MCP transport and container cleanup."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core._llm_types import LLMClientError, LLMMessage
from core.llm_providers._native_acp_runtime import NativeAcpRuntime
from core.llm_providers.acp import LLMAcpMixin, _AcpLiveSession
from core.llm_providers.cursor_acp import (
    LLMCursorAcpMixin,
    validate_cursor_acp_config,
)
from core.llm_providers.grok_build_acp import (
    LLMGrokBuildAcpMixin,
    validate_grok_build_acp_config,
)
from core.native_cli_auth import native_cli_home

VALIDATORS = [validate_cursor_acp_config, validate_grok_build_acp_config]
IDENTITY = {"user_id": "user", "conversation_id": "conversation", "agent_name": "assistant",
            "service_id": "service", "persist_session": False}


class Client(LLMCursorAcpMixin, LLMGrokBuildAcpMixin, LLMAcpMixin):
    pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("core.docker_utils.docker_cmd", lambda: ["docker"])
    monkeypatch.setattr("core.docker_utils.get_server_id", lambda: "server")
    monkeypatch.setattr("core.docker_utils.to_host_path", lambda path: "/host" + path)
    monkeypatch.setattr("core.docker_utils.translate_path", lambda path: path)
    monkeypatch.setenv("PAWFLOW_RUN_UID", "1001")
    monkeypatch.setenv("PAWFLOW_RUN_GID", "1002")
    for name in ("PAWFLOW_CURSOR_BIN", "PAWFLOW_CURSOR_IMAGE",
                 "PAWFLOW_GROK_BUILD_BIN", "PAWFLOW_GROK_BUILD_IMAGE"):
        monkeypatch.delenv(name, raising=False)
    client = Client()
    client.provider = "cursor-acp"
    client._config_ref = {"acp_cwd": str(tmp_path), "acp_mcp_mode": "none"}
    return client


@pytest.fixture
def docker_calls(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr("core.llm_providers._native_acp_runtime.subprocess.run", run)
    return calls


@pytest.fixture
def processes(client, monkeypatch):
    processes = []

    class Process:
        def __init__(self, command, args, **kwargs):
            self.command, self.args, self.kwargs = command, args, kwargs
            self.calls, self.closed = [], []
            self.is_running = True
            self.initialize_response = SimpleNamespace(
                auth_methods=[SimpleNamespace(id=client._acp_config()["auth_method_id"])],
                agent_capabilities=SimpleNamespace(
                    session_capabilities=SimpleNamespace(additional_directories=True),
                    load_session=True,
                ),
            )
            processes.append(self)

        def start(self):
            return self.initialize_response

        def call(self, method, **kwargs):
            self.calls.append((method, kwargs))
            return SimpleNamespace(session_id="created")

        def close(self, *, force):
            self.closed.append(force)
            self.is_running = False

    monkeypatch.setattr(client, "_acp_process_class", lambda: Process)
    return processes


@pytest.mark.parametrize("validate", VALIDATORS)
@pytest.mark.parametrize("bad,match", [
    ({"auth_mode": "api_key"}, "auth_mode must be none"),
    ({"api_key": "secret"}, "native CLI auth"),
    ({"credential_service_id": "credential"}, "native CLI auth"),
    ({"acp_env": {"HOME": "/other"}}, "runtime variable HOME"),
    ({"acp_env": {"XDG_CONFIG_HOME": "/other"}}, "runtime variable XDG"),
    ({"acp_env": {"PAWFLOW_INTERNAL_TOKEN": "token"}}, "runtime variable PAWFLOW"),
    ({"acp_env": {"DOCKER_HOST": "tcp://elsewhere"}}, "runtime variable DOCKER"),
    ({"acp_env": {"LD_PRELOAD": "/inject"}}, "runtime variable LD"),
    ({"acp_env": {"XAI_API_KEY": 1}}, "strings"),
    ({"acp_env": {"XAI_API_KEY": "a\x00b"}}, "NUL"),
    ({"acp_args": ["a\x00b"]}, "NUL"),
    ({"acp_command": "-bad"}, "executable"),
    ({"acp_cwd": "/"}, "non-root"),
    ({"acp_cwd": ""}, "acp_cwd"),
    ({"acp_additional_directories": ["/"]}, "non-root"),
])
def test_rejects_ambiguous_auth_and_runtime_overrides(tmp_path, validate, bad, match):
    with pytest.raises(ValueError, match=match):
        validate({"acp_cwd": str(tmp_path), **bad})


@pytest.mark.parametrize("directory", ["/native-home/project", "/opt", "/opt/pawflow",
                                      "/usr/local/project", "/etc", "/proc"])
def test_configured_mounts_cannot_shadow_runtime_paths(monkeypatch, directory):
    from core.llm_providers._native_acp_runtime import _directory
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    with pytest.raises(ValueError, match="protected native runtime"):
        _directory(directory, "acp_cwd")


@pytest.mark.parametrize("provider", ["cursor-acp", "grok-build-acp"])
@pytest.mark.parametrize("location", ["project", "extra"])
def test_managed_permission_read_and_write_keep_configured_paths(
    client, processes, tmp_path, monkeypatch, provider, location,
):
    from acp import RequestError
    from acp.schema import PermissionOption
    from unittest.mock import Mock

    project = tmp_path / "project"
    extra = tmp_path / "extra"
    project.mkdir()
    extra.mkdir()
    client.provider = provider
    client._config_ref.update(acp_cwd=str(project), acp_additional_directories=[str(extra)])
    live = _AcpLiveSession(signature=())
    client._acp_open_session(live, client._acp_config(), **IDENTITY)
    live.registry = Mock()
    live.registry.execute.return_value = "file content"
    handlers = processes[0].kwargs["handlers"]
    monkeypatch.setattr("core.tool_approval.ToolApprovalGate.get_mode", lambda *_: "auto")
    monkeypatch.setattr("core.tool_authorization.gate_for_runtime", lambda **_: None)
    path = str(tmp_path / location / "approved.txt")
    assert handlers.read_text_file("created", path, 2, 3) == "file content"
    live.registry.execute.assert_called_once_with("read", {"path": path, "offset": 2, "limit": 3})
    tool_call = SimpleNamespace(kind="edit", title="Edit configured file",
                                raw_input={"path": path}, locations=[], content=[])
    reply = handlers.permission("created", tool_call, [
        PermissionOption(option_id="allow", name="Allow once", kind="allow_once")])
    assert reply.outcome.option_id == "allow"
    with pytest.raises(RequestError):
        handlers.write_text_file("created", "/workspace/approved.txt", "wrong file")
    handlers.write_text_file("created", path, "accepted")
    live.registry.execute.assert_called_with("write", {"path": path, "content": "accepted"})
    with pytest.raises(RequestError):
        handlers.write_text_file("created", path, "already consumed")


@pytest.mark.parametrize("provider,prefix,validate", [
    ("cursor-acp", "PAWFLOW_CURSOR", validate_cursor_acp_config),
    ("grok-build-acp", "PAWFLOW_GROK_BUILD", validate_grok_build_acp_config),
])
def test_honors_container_binary_image_and_explicit_service_overrides(
    client, monkeypatch, provider, prefix, validate,
):
    monkeypatch.setenv(prefix + "_BIN", "/opt/custom/native")
    monkeypatch.setenv(prefix + "_IMAGE", "native:test")
    client.provider = provider
    settings = dict(client._config_ref)
    assert validate(settings)["command"] == "/opt/custom/native"
    settings.update(acp_command="/image-only/agent", acp_args='["custom", "--model", "chosen"]',
                    acp_env='{"XAI_API_KEY":"service-secret"}',
                    acp_auth_method_id="explicit", acp_use_client_io="false",
                    acp_reuse_process="false", acp_load_session="false",
                    acp_title_override="Custom")
    validated = validate(settings)
    assert validated["command"] == "/image-only/agent"
    assert validated["args"] == ("custom", "--model", "chosen")
    assert validated["image"] == "native:test"
    assert validated["env"] == {"XAI_API_KEY": "service-secret"}
    assert validated["auth_method_id"] == "explicit"
    assert validated["title"] == "Custom"
    assert validated["user_spec"] == "1001:1002"
    assert not any(validated[key] for key in ("use_client_io", "reuse_process", "load_session"))
    assert settings["acp_args"] == '["custom", "--model", "chosen"]'


def test_grok_does_not_consume_host_credentials(client, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "host-secret")
    config = validate_grok_build_acp_config(client._config_ref)
    assert config["auth_method_id"] == "cached_token"
    assert config["env"] == {}
    config = validate_grok_build_acp_config({
        **client._config_ref, "acp_env": {"XAI_API_KEY": ""},
    })
    assert config["auth_method_id"] == "cached_token"


@pytest.mark.parametrize("provider", ["cursor-acp", "grok-build-acp"])
def test_scoped_argv_uses_login_home_and_maps_configured_workspace(
    client, tmp_path, provider,
):
    work = tmp_path / "project"
    work.mkdir()
    child = work / "child"
    child.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    client.provider = provider
    client._config_ref.update(
        acp_cwd=str(work), acp_additional_directories=[str(child), str(extra)],
        acp_env={"XAI_API_KEY": "service-secret"},
    )
    config = client._acp_config()
    runtime = NativeAcpRuntime(provider, config, **{
        key: value for key, value in IDENTITY.items() if key != "persist_session"
    })
    argv = runtime.argv
    home = native_cli_home(provider, "user", "service")
    assert f"/host{home}:/native-home" in argv
    assert f"/host{work}:{work}" in argv
    assert f"/host{extra}:{extra}" in argv
    assert runtime.additional_directories == [str(child), str(extra)]
    assert sum(arg == "-v" for arg in argv) == 3
    assert argv[argv.index("-w") + 1] == str(work)
    assert argv[argv.index("--user") + 1] == "1001:1002"
    assert "HOME=/native-home" in argv
    assert "XAI_API_KEY" in argv and "service-secret" not in " ".join(argv)
    assert argv[-len(config["args"]):] == list(config["args"])
    assert argv[argv.index("--entrypoint") + 1] == config["command"]
    assert "--pull" in argv and "never" in argv
    assert not any(flag in argv for flag in ("-p", "-P", "--publish", "--privileged", "--network"))
    assert not any("docker.sock" in arg for arg in argv)
    assert "org.pawflow.kind=" + provider in argv


def test_only_auth_home_is_shared_across_conversations(client):
    config = client._acp_config()
    identity = {key: value for key, value in IDENTITY.items() if key != "persist_session"}
    first = NativeAcpRuntime(client.provider, config, **identity)
    second = NativeAcpRuntime(client.provider, config, **{**identity, "conversation_id": "other"})
    assert first.name != second.name
    auth_mount = f"/host{native_cli_home(client.provider, 'user', 'service')}:/native-home"
    assert auth_mount in first.argv and auth_mount in second.argv
    for overrides in ({"user_id": "other"}, {"service_id": "other"}):
        other = NativeAcpRuntime(client.provider, config, **{**identity, **overrides})
        assert auth_mount not in other.argv
    for field in identity:
        with pytest.raises(LLMClientError, match="requires"):
            NativeAcpRuntime(client.provider, config, **{**identity, field: ""})


@pytest.mark.parametrize("provider", ["cursor-acp", "grok-build-acp"])
def test_generic_open_session_receives_managed_launch_and_loads_session(
    client, processes, monkeypatch, provider,
):
    client.provider = provider
    monkeypatch.setattr(client, "_acp_get_stored_session", lambda *_: "stored")
    live = _AcpLiveSession(signature=())
    result = client._acp_open_session(live, client._acp_config(), **{
        **IDENTITY, "persist_session": True,
    })
    assert result == ("stored", False)
    process = processes[0]
    assert process.command == "docker"
    assert process.kwargs["cwd"] == client._config_ref["acp_cwd"]
    assert process.kwargs["stderr_path"] == os.devnull
    method, values = process.calls[-1]
    assert method == "load_session"
    assert values["cwd"] == client._config_ref["acp_cwd"]
    assert values["session_id"] == "stored"
    assert values["mcp_servers"] == []
    assert process.calls[0][0] == "authenticate"
    if provider == "grok-build-acp":
        assert process.calls[0][1]["headless"] is True


def test_mcp_bridge_and_tokens_use_protocol_only(client, processes, monkeypatch):
    client._config_ref["acp_mcp_mode"] = "pawflow"
    monkeypatch.setattr(
        "core.llm_providers.claude_code_session.ClaudeCodeSessionMixin._get_tool_relay_info",
        lambda: ("ws://127.0.0.1:1234/ws", "relay-secret"),
    )
    monkeypatch.setattr("core.docker_utils.get_host_ip", lambda: "192.0.2.10")
    monkeypatch.setattr("core.internal_auth.mint_token", lambda: "internal-secret")
    live = _AcpLiveSession(signature=())
    client._acp_open_session(live, client._acp_config(), **IDENTITY)
    process = processes[0]
    server = process.calls[-1][1]["mcp_servers"][0]
    assert server.command == "/usr/bin/python3"
    assert server.args == ["/opt/pawflow/mcp_bridge.py"]
    env = {entry.name: entry.value for entry in server.env}
    assert env["PAWFLOW_TOOL_RELAY_URL"] == "ws://192.0.2.10:1234/ws"
    assert env["PAWFLOW_TOOL_RELAY_TOKEN"] == "relay-secret"
    assert env["PAWFLOW_INTERNAL_TOKEN"] == live.internal_token == "internal-secret"
    assert env["PAWFLOW_USER_ID"] == "user"
    assert env["PAWFLOW_CONVERSATION_ID"] == "conversation"
    assert env["PAWFLOW_AGENT_NAME"] == "assistant"
    assert all(value not in " ".join(process.args) for value in ("relay-secret", "internal-secret"))
    assert not any(name.startswith("PAWFLOW_") for name in process.kwargs["env"])
    assert sum(arg.endswith(":ro") for arg in process.args) == 3


@pytest.mark.parametrize("force", [True, False])
def test_close_removes_exact_container_and_revokes_token(
    client, processes, docker_calls, monkeypatch, force,
):
    live = _AcpLiveSession(signature=())
    client._acp_open_session(live, client._acp_config(), **IDENTITY)
    name = live.native_runtime.name
    live.internal_token = "internal-secret"
    revoked = []
    monkeypatch.setattr("core.internal_auth.revoke_token", revoked.append)
    key = ("service", "user", "conversation", "assistant")
    client._acp_shared_state()[0][key] = live
    client._acp_close_entry(key, live, force=force)
    assert docker_calls
    assert all(argv == ["docker", "rm", "-f", name] for argv, _ in docker_calls)
    assert processes[0].closed == [force]
    assert revoked == ["internal-secret"]
    assert live.native_runtime is None and live.process is None
    assert key not in client._acp_shared_state()[0]


def test_force_abort_kills_ephemeral_container_before_process_without_killing_foreground(
    client, processes, docker_calls, monkeypatch,
):
    first, ephemeral = (_AcpLiveSession(signature=()) for _ in range(2))
    client._acp_open_session(first, client._acp_config(), **IDENTITY)
    client._acp_open_session(ephemeral, client._acp_config(), **IDENTITY)
    assert first.native_runtime.name != ephemeral.native_runtime.name
    first_name, ephemeral_name = first.native_runtime.name, ephemeral.native_runtime.name
    sessions = client._acp_shared_state()[0]
    sessions[("service", "user", "conversation", "assistant")] = first
    sessions[("service", "user", "conversation", "assistant:ephemeral:id")] = ephemeral
    ephemeral.active_handle = object()

    def closed(*, force):
        assert force
        assert docker_calls[0][0] == ["docker", "rm", "-f", ephemeral_name]

    monkeypatch.setattr(processes[1], "close", closed)
    client._acp_abort_active(force=True)
    assert first.process is processes[0]
    assert first.native_runtime.name == first_name
    assert not any(first_name in argv for argv, _ in docker_calls)


def test_failed_start_and_stale_process_remove_owned_containers(
    client, processes, docker_calls, monkeypatch,
):
    live = _AcpLiveSession(signature=())
    client._acp_open_session(live, client._acp_config(), **IDENTITY)
    old_name = live.native_runtime.name
    client._acp_open_session(live, client._acp_config(), **IDENTITY)
    assert docker_calls[0][0] == ["docker", "rm", "-f", old_name]
    process_type = client._acp_process_class()

    def failed_start(self):
        raise RuntimeError("failed initialize")

    monkeypatch.setattr(process_type, "start", failed_start)
    with pytest.raises(RuntimeError, match="failed initialize"):
        client._acp_open_session(live, client._acp_config(), **IDENTITY)
    assert docker_calls[-1][0] == ["docker", "rm", "-f", live.native_runtime.name]


def test_cleanup_still_removes_container_when_process_close_fails(
    client, processes, docker_calls, monkeypatch,
):
    live = _AcpLiveSession(signature=())
    client._acp_open_session(live, client._acp_config(), **IDENTITY)

    def failed_close(**kwargs):
        raise RuntimeError("failed close")

    monkeypatch.setattr(processes[0], "close", failed_close)
    name = live.native_runtime.name
    with pytest.raises(RuntimeError, match="failed close"):
        client._acp_close_entry(("service", "user", "conversation", "assistant"), live, force=False)
    assert docker_calls[-1][0] == ["docker", "rm", "-f", name]


def test_session_signature_covers_container_configuration(client):
    config = client._acp_config()
    signature = client._acp_signature(config)
    for key, value in (("image", "other:image"), ("user_spec", "12:34"),
                       ("command", "other-cli"), ("args", ("other",)),
                       ("cwd", "/other"), ("env", {"XAI_API_KEY": "other"})):
        assert client._acp_signature({**config, key: value}) != signature
    client.provider = "grok-build-acp"
    assert client._acp_signature(config) != signature
    client.provider = "acp"
    assert client._acp_signature(config) == LLMAcpMixin._acp_signature(config)
    assert client._acp_process_kwargs(config) == {}


@pytest.mark.parametrize("provider", ["cursor-acp", "grok-build-acp"])
def test_managed_stream_reuses_stdio_session_and_isolates_ephemeral_calls(
    client, docker_calls, monkeypatch, provider,
):
    from core.llm_client import LLMClient

    client = LLMClient(provider=provider, config=client._config_ref)
    client._agent_service = "service"
    client._config_ref["acp_use_client_io"] = False
    stored = []
    monkeypatch.setattr(client, "_acp_get_stored_session", lambda *_: stored[-1] if stored else "")
    monkeypatch.setattr(client, "_acp_set_stored_session", lambda *args: stored.append(args[-1]))
    monkeypatch.setattr(
        "core.acp.native_extensions.collect_native_questions",
        lambda *_a, **_k: {"q": "second" if provider == "cursor-acp" else "Second"},
    )
    fixture = Path(__file__).parent / "fixtures" / "native_acp_agent.py"
    process_type = client._acp_process_class()
    launches = []

    class FixtureProcess(process_type):
        def __init__(self, command, args, **kwargs):
            # Exercise the complete managed mixin and real ACP transport while
            # replacing only the Docker executable with the stdio test agent.
            assert command == "docker" and args[0] == "run"
            launches.append(tuple(args))
            super().__init__(sys.executable, [str(fixture),
                             "cursor" if provider == "cursor-acp" else "grok"], **kwargs)

    monkeypatch.setattr(client, "_acp_process_class", lambda: FixtureProcess)

    def prompt(ephemeral=False):
        return client._stream_acp(
            [LLMMessage(role="user", content="hello", conversation_id="conversation")],
            "native", 0, 100, None,
            call_user_id="user", call_conversation_id="conversation", call_agent_name="assistant",
            call_ephemeral_stream=ephemeral,
        )

    try:
        first = prompt()
        second = prompt()
        assert first.content == second.content
        assert first.finish_reason == second.finish_reason == "end_turn"
        assert len(launches) == 1 and stored == ["native-session"]
        answer = json.loads(first.content)
        if provider == "cursor-acp":
            assert answer["outcome"]["answers"][0]["selectedOptionIds"] == ["second"]
        else:
            assert answer["answers"] == {"Choose": ["Second"]}
        foreground = next(iter(client._acp_shared_state()[0].values()))
        foreground_name = foreground.native_runtime.name
        assert prompt(ephemeral=True).content == first.content
        assert len(launches) == 2
        assert len(client._acp_shared_state()[0]) == 1
        assert foreground.process.is_running
        assert not any(foreground_name in argv for argv, _ in docker_calls)
        assert prompt().content == first.content and len(launches) == 2
    finally:
        client._acp_close_all()
