"""``antigravity-acp`` provider: registration, config, container argv, runtime.

The runtime tests drive the real ``LLMAcpMixin`` path with the ACP fixture
agent replaying what Google's ``agy_acp_server`` 1.1.1 answered during the
2026-09-04 spike: four named auth methods, ``-32000 Authentication required``
before ``authenticate``, and MCP servers accepted at ``session/new``. Docker
is replaced by running the fixture directly; everything else is production
code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import core.paths as _paths
from core import ServiceError
from core.antigravity_acp_pool import (
    SERVER_ARGS,
    AntigravityAcpContainer,
    AntigravityAcpPool,
)
from core.llm_auth_modes import NONE, default_mode
from core.llm_client import LLMClient, LLMMessage
from core.llm_providers.acp import ACP_PROVIDERS, LLMAcpMixin
from core.llm_providers.antigravity_acp import (
    AUTH_METHODS,
    parse_cli_environment,
    validate_antigravity_acp_config,
)
from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin
from services.llm_connection import LLMConnectionService


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "acp_runtime_agent.py"
IDENTITY = ("user-acp", "conv-acp", "assistant", "agy-acp")


def _config(**overrides):
    config = {
        "provider": "antigravity-acp",
        "auth_mode": "none",
        "_service_id": "agy-acp",
        "max_retries": 3,
        "max_context_size": 100,
    }
    config.update(overrides)
    return config


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "RUNTIME_DIR", tmp_path / "runtime")
    return tmp_path / "runtime"


@pytest.fixture
def stored_sessions(monkeypatch):
    values = {}

    def get_session(_self, conversation_id, service_id, agent_name):
        key = _self._acp_session_store_key(service_id, agent_name)
        return values.get((conversation_id, key), "")

    def set_session(_self, conversation_id, service_id, agent_name, session_id):
        key = _self._acp_session_store_key(service_id, agent_name)
        values[(conversation_id, key)] = session_id

    monkeypatch.setattr(LLMAcpMixin, "_acp_get_stored_session", get_session)
    monkeypatch.setattr(LLMAcpMixin, "_acp_set_stored_session", set_session)
    return values


@pytest.fixture
def fake_containers(runtime_dir, monkeypatch):
    """Replace Docker with the fixture agent; keep every other production path."""
    ensured = []
    killed = []

    def ensure(self, *, user_id, conversation_id, agent_name, service_id):
        key = (user_id, conversation_id, agent_name, service_id)
        ensured.append(key)
        return AntigravityAcpContainer(
            key=key,
            name=f"fake-{len(ensured)}",
            workdir=str(self.workdir(user_id, conversation_id, agent_name)),
            home=str(self.home_dir(user_id, service_id)),
        )

    def exec_argv(self, container, *, env_names=()):
        del self, container, env_names
        return [sys.executable, str(FIXTURE)]

    def kill_session(self, *, user_id, conversation_id, agent_name, service_id):
        del self
        killed.append((user_id, conversation_id, agent_name, service_id))
        return True

    monkeypatch.setattr(AntigravityAcpPool, "ensure", ensure)
    monkeypatch.setattr(AntigravityAcpPool, "exec_argv", exec_argv)
    monkeypatch.setattr(AntigravityAcpPool, "kill_session", kill_session)
    monkeypatch.setattr(
        ClaudeCodeSessionMixin, "_get_tool_relay_info",
        classmethod(lambda cls: ("wss://localhost:9090/ws/tools/relay-1", "relay-token")),
    )
    monkeypatch.setattr("core.internal_auth.mint_token", lambda: "internal-token")
    monkeypatch.setattr("core.internal_auth.revoke_token", lambda _token: None)
    return {"ensured": ensured, "killed": killed}


def _cli_environment(auth="antigravity", stderr="fixture stderr line"):
    lines = ["PAWFLOW_ACP_FIXTURE_MODE=provider", "# comment", ""]
    if auth:
        lines.append(f"PAWFLOW_ACP_FIXTURE_AUTH={auth}")
    if stderr:
        lines.append(f"PAWFLOW_ACP_FIXTURE_STDERR={stderr}")
    return "\n".join(lines)


def _call(service, text="hello", **kwargs):
    message = LLMMessage(role="user", content=text, conversation_id="conv-acp")
    return service.complete_stream(
        [message],
        call_user_id="user-acp",
        call_conversation_id="conv-acp",
        call_agent_name="assistant",
        call_event_cid="conv-acp",
        **kwargs,
    )


def _payload(response):
    return json.loads(response.content)


def _close(service):
    service._client._acp_close_all()


# -- registration --------------------------------------------------------------


def test_provider_is_registered_as_an_acp_provider_with_its_own_form():
    assert "antigravity-acp" in LLMClient.PROVIDERS
    assert "antigravity-acp" in ACP_PROVIDERS
    assert default_mode("antigravity-acp", {}) == NONE
    service = LLMConnectionService(_config())
    schema = service.get_parameter_schema()
    assert schema["antigravity_acp_auth_method"]["options"] == list(AUTH_METHODS)
    rule = next(
        item for item in service.get_parameter_rules()
        if item.get("when") == {"provider": ["antigravity-acp"]}
    )
    assert rule["set"]["antigravity_acp_auth_method"] == {"visible": True, "required": True}
    assert rule["set"]["auth_mode"]["default"] == "none"
    assert rule["set"]["credential_service_id"] == {"visible": False}
    assert "acp_command" not in rule["set"]
    hidden = next(
        item for item in service.get_parameter_rules()
        if "antigravity-acp" in item.get("when", {}).get("provider", [])
        and "acp_command" in item["set"]
    )
    assert hidden["set"]["acp_command"] == {"visible": False}
    service.connect()
    service.disconnect()


def test_service_refuses_a_credential_pool_and_bad_methods():
    with pytest.raises(ServiceError, match="credential pool"):
        LLMConnectionService(
            _config(auth_mode="oauth", credential_service_id="gemini_oauth_credentials")
        ).connect()
    with pytest.raises(ServiceError, match="antigravity_acp_auth_method must be one of"):
        LLMConnectionService(_config(antigravity_acp_auth_method="api-key")).connect()
    with pytest.raises(ServiceError, match="requires api_key"):
        LLMConnectionService(
            _config(auth_mode="api_key", antigravity_acp_auth_method="gemini-api-key")
        ).connect()


def test_config_validation_owns_the_container_environment():
    validated = validate_antigravity_acp_config(_config())
    assert validated["auth_method_id"] == "oauth-personal"
    assert validated["env"] == {}
    assert validated["title"] == "Antigravity"

    validated = validate_antigravity_acp_config(_config(
        auth_mode="api_key", api_key="key-1",
        antigravity_acp_auth_method="gemini-api-key",
        cli_environment="GOOGLE_CLOUD_PROJECT=proj\n",
    ))
    assert validated["env"] == {"GOOGLE_CLOUD_PROJECT": "proj", "GEMINI_API_KEY": "key-1"}

    with pytest.raises(ValueError, match="does not use api_key"):
        validate_antigravity_acp_config(_config(api_key="key-1"))
    with pytest.raises(ValueError, match="must not set GEMINI_HOME"):
        validate_antigravity_acp_config(_config(cli_environment="GEMINI_HOME=/x"))
    with pytest.raises(ValueError, match="not NAME=value"):
        parse_cli_environment("BROKEN")
    with pytest.raises(ValueError, match="variable name"):
        parse_cli_environment("BAD-NAME=1")


def test_acp_config_is_fixed_and_the_generic_provider_is_untouched(runtime_dir):
    client = LLMClient(provider="antigravity-acp", config=_config())
    config = client._acp_config()
    assert config["mcp_mode"] == "pawflow"
    assert config["auth_method_id"] == "oauth-personal"
    assert config["auto_auth_single"] is False
    assert config["additional_directories"] == ()
    assert Path(config["cwd"]) == AntigravityAcpPool.base_dir()

    generic = LLMClient(provider="acp", config={"provider": "acp", "acp_cwd": str(runtime_dir)})
    with pytest.raises(ValueError, match="acp_command is required"):
        generic._acp_config()


# -- container argv --------------------------------------------------------------


def test_exec_argv_forwards_environment_by_name_only(runtime_dir):
    pool = AntigravityAcpPool()
    container = AntigravityAcpContainer(
        key=IDENTITY,
        name="pf-owner-agyacp-1",
        workdir=str(pool.workdir("user-acp", "conv-acp", "assistant")),
        home=str(pool.home_dir("user-acp", "agy-acp")),
    )
    argv = pool.exec_argv(container, env_names=["GEMINI_API_KEY", "GOOGLE_CLOUD_PROJECT"])

    assert "exec" in argv and "-i" in argv
    assert argv[-2:] == [pool.server_binary(), *SERVER_ARGS]
    assert argv[-3] == "pf-owner-agyacp-1"
    assert argv[argv.index("-w") + 1] == "/cc_sessions_host/user-acp/conv-acp/assistant"
    assert "GEMINI_HOME=/cc_sessions_host/user-acp/homes/agy-acp/.gemini" in argv
    assert "HOME=/cc_sessions_host/user-acp/homes/agy-acp" in argv
    for name in ("GEMINI_API_KEY", "GOOGLE_CLOUD_PROJECT"):
        assert argv[argv.index(name) - 1] == "-e"
    assert not any(item.startswith("GEMINI_API_KEY=") for item in argv)
    assert pool.session_cwd(container) == "/cc_sessions_host/user-acp/conv-acp/assistant"
    assert pool.stderr_path(container).endswith("/logs/acp-server.stderr.log")

    with pytest.raises(ValueError, match="invalid environment name"):
        pool.exec_argv(container, env_names=["KEY=value"])
    with pytest.raises(ValueError, match="service_id is required"):
        pool.home_dir("user-acp", "")


# -- runtime through the fixture ------------------------------------------------------


def test_turn_authenticates_scopes_mcp_and_redirects_stderr(
    fake_containers, stored_sessions,
):
    service = LLMConnectionService(_config(cli_environment=_cli_environment()))
    try:
        response = _call(service, "first")
        payload = _payload(response)
        assert payload["auth_method"] == "oauth-personal"
        assert payload["cwd"] == "/cc_sessions_host/user-acp/conv-acp/assistant"
        assert payload["mcp_servers"] == [{
            "name": "pawflow",
            "command": "/usr/bin/python3",
            "args": ["/opt/pawflow/mcp_bridge.py"],
            "env_names": [
                "PAWFLOW_AGENT_NAME", "PAWFLOW_CONVERSATION_ID",
                "PAWFLOW_INTERNAL_TOKEN", "PAWFLOW_TOOL_RELAY_TOKEN",
                "PAWFLOW_TOOL_RELAY_URL", "PAWFLOW_USER_ID",
            ],
        }]
        assert response.finish_reason == "end_turn"
        assert response.model == "Antigravity"
        assert response.tokens_in == 0 and response.tokens_out == 0
        assert fake_containers["ensured"] == [IDENTITY]

        second = _payload(_call(service, "second"))
        assert second["pid"] == payload["pid"]
        assert second["session_id"] == payload["session_id"]
        assert service._client._acp_has_live_session(*IDENTITY[3:] + IDENTITY[:3])

        stderr_log = Path(
            AntigravityAcpPool.workdir("user-acp", "conv-acp", "assistant")
        ) / "logs" / "acp-server.stderr.log"
        assert "fixture stderr line" in stderr_log.read_text(encoding="utf-8")
    finally:
        _close(service)
    assert fake_containers["killed"] == [IDENTITY]


def test_ephemeral_stream_shares_the_container_but_not_the_process(
    fake_containers, stored_sessions,
):
    service = LLMConnectionService(_config(cli_environment=_cli_environment()))
    try:
        foreground = _payload(_call(service, "foreground"))
        ephemeral = _payload(_call(service, "ephemeral", call_ephemeral_stream=True))
        resumed = _payload(_call(service, "foreground again"))
        assert ephemeral["pid"] != foreground["pid"]
        assert ephemeral["loaded"] is False
        assert resumed["pid"] == foreground["pid"]
        # Same identity twice: the ephemeral process ran in the foreground
        # container, and its clean close did not kill that container.
        assert fake_containers["ensured"] == [IDENTITY, IDENTITY]
        assert fake_containers["killed"] == []
    finally:
        _close(service)
    assert fake_containers["killed"] == [IDENTITY]


def test_configured_method_must_be_advertised(fake_containers, stored_sessions):
    service = LLMConnectionService(
        _config(cli_environment=_cli_environment(auth=""), antigravity_acp_auth_method="oauth-business")
    )
    try:
        with pytest.raises(ServiceError, match="advertised no authentication methods"):
            _call(service)
    finally:
        _close(service)


def test_process_restart_reloads_the_persisted_session(fake_containers, stored_sessions):
    service = LLMConnectionService(_config(cli_environment=_cli_environment()))
    try:
        first = _payload(_call(service, "first"))
        _close(service)
        second = _payload(_call(service, "after restart"))
        assert second["pid"] != first["pid"]
        assert second["loaded"] is True
        assert second["session_id"] == first["session_id"]
        assert second["auth_method"] == "oauth-personal"
    finally:
        _close(service)
