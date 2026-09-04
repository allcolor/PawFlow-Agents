"""``antigravity-acp``: Google's official Antigravity ACP server as a provider.

The server (``agy_acp_server``) is Google's own binary, listed by Google in
the public ACP registry, and is the integration surface Zed and JetBrains
use. PawFlow runs it inside the ``pawflow-claude-code`` image and talks plain
ACP to it through the shared ``LLMAcpMixin`` runtime. This module only adds
what the generic provider cannot know:

- the fixed command (``docker exec -i <container> agy_acp_server.par --uid=``)
  and the container lifecycle (``core.antigravity_acp_pool``);
- ``GEMINI_HOME`` per (user, service) so one login serves every
  conversation of that service;
- the four authentication methods the server advertises;
- the in-container PawFlow MCP bridge definition;
- stderr redirection (the server logs verbosely; an unread pipe would block
  it).

Every override is guarded by ``self.provider`` so the generic ``acp``
provider keeps its behaviour in the same ``LLMClient`` MRO.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from acp.schema import EnvVariable, McpServerStdio

from core._llm_types import LLMClientError
from core.antigravity_acp_pool import (
    CONTAINER_MCP_BRIDGE,
    CONTAINER_PYTHON,
    AntigravityAcpPool,
)

PROVIDER = "antigravity-acp"

#: Authentication method ids advertised by ``agy_acp_server`` 1.1.1.
AUTH_METHODS = (
    "oauth-personal",
    "oauth-business",
    "gemini-api-key",
    "agent-platform",
)
DEFAULT_AUTH_METHOD = "oauth-personal"
_API_KEY_METHODS = {"gemini-api-key", "agent-platform"}
#: Names PawFlow owns inside the container; a service must not override them.
_RESERVED_ENV = ("HOME", "GEMINI_HOME", "GEMINI_API_KEY", "GOOGLE_API_KEY", "USER")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EPHEMERAL_MARKER = ":ephemeral:"


def parse_cli_environment(text: Any) -> dict[str, str]:
    """Parse ``NAME=value`` lines; blank lines and ``#`` comments are skipped."""
    env: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"cli_environment line is not NAME=value: {line!r}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"Invalid cli_environment variable name: {name!r}")
        env[name] = value
    return env


def _boolean(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"expected a boolean, got {value!r}")


def validate_antigravity_acp_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded, normalized ``antigravity-acp`` configuration."""
    method = str(config.get("antigravity_acp_auth_method") or DEFAULT_AUTH_METHOD).strip()
    if method not in AUTH_METHODS:
        raise ValueError(
            "antigravity_acp_auth_method must be one of: " + ", ".join(AUTH_METHODS))
    api_key = str(config.get("api_key") or "").strip()
    if method == "gemini-api-key" and not api_key:
        raise ValueError("antigravity_acp_auth_method=gemini-api-key requires api_key")
    if method not in _API_KEY_METHODS and api_key:
        raise ValueError(
            f"antigravity_acp_auth_method={method} does not use api_key; clear it")
    env = parse_cli_environment(config.get("cli_environment"))
    for reserved in _RESERVED_ENV:
        if reserved in env:
            raise ValueError(f"cli_environment must not set {reserved}")
    if method == "gemini-api-key":
        env["GEMINI_API_KEY"] = api_key
    elif method == "agent-platform" and api_key:
        env["GOOGLE_API_KEY"] = api_key
    return {
        "auth_method_id": method,
        "env": env,
        "reuse_process": _boolean(config.get("acp_reuse_process"), True),
        "load_session": _boolean(config.get("acp_load_session"), True),
        "use_client_io": _boolean(config.get("acp_use_client_io"), True),
        "title": str(config.get("acp_title_override") or "").strip() or "Antigravity",
    }


class LLMAntigravityAcpMixin:
    """Antigravity ACP server driven through the shared ACP runtime.

    The driver dispatches ``antigravity-acp`` to ``_stream_acp`` directly;
    this mixin only bends the shared runtime where the container changes
    the answer.
    """

    # -- shared-runtime overrides (guarded by provider) --------------------------

    def _acp_config(self) -> dict[str, Any]:
        if self.provider != PROVIDER:
            return super()._acp_config()
        from core.docker_utils import docker_cmd

        validated = validate_antigravity_acp_config(self._config_ref)
        docker = list(docker_cmd())
        base = AntigravityAcpPool.base_dir()
        base.mkdir(parents=True, exist_ok=True)
        return {
            # Real exec argv is completed in _acp_open_session once the
            # container exists; the signature must not depend on its name.
            "command": docker[0],
            "args": tuple(docker[1:]),
            "cwd": str(base),
            "env": dict(validated["env"]),
            "auth_method_id": validated["auth_method_id"],
            "auto_auth_single": False,
            "reuse_process": validated["reuse_process"],
            "load_session": validated["load_session"],
            "additional_directories": (),
            "mcp_mode": "pawflow",
            "use_client_io": validated["use_client_io"],
            "title": validated["title"],
        }

    def _acp_open_session(
        self,
        live: Any,
        config: Mapping[str, Any],
        *,
        user_id: str,
        conversation_id: str,
        agent_name: str,
        service_id: str,
        persist_session: bool,
    ) -> tuple[str, bool]:
        if self.provider != PROVIDER:
            return super()._acp_open_session(
                live, config,
                user_id=user_id, conversation_id=conversation_id,
                agent_name=agent_name, service_id=service_id,
                persist_session=persist_session,
            )
        if not (user_id and conversation_id and agent_name and service_id):
            raise LLMClientError(
                "antigravity-acp requires user_id, conversation_id, agent_name "
                "and service_id")
        pool = AntigravityAcpPool.instance()
        try:
            container = pool.ensure(
                user_id=user_id, conversation_id=conversation_id,
                agent_name=agent_name, service_id=service_id)
            argv = pool.exec_argv(container, env_names=sorted(config["env"]))
        except (RuntimeError, ValueError, OSError) as exc:
            raise LLMClientError(f"antigravity-acp container failed: {exc}") from exc
        launch = dict(config)
        launch["command"] = argv[0]
        launch["args"] = tuple(argv[1:])
        launch["cwd"] = container.workdir
        launch["session_cwd"] = pool.session_cwd(container)
        launch["stderr_path"] = pool.stderr_path(container)
        return super()._acp_open_session(
            live, launch,
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name, service_id=service_id,
            persist_session=persist_session,
        )

    def _acp_process_kwargs(self, config: Mapping[str, Any]) -> dict[str, Any]:
        if self.provider != PROVIDER:
            return super()._acp_process_kwargs(config)
        stderr_path = str(config.get("stderr_path") or "")
        return {"stderr_path": stderr_path} if stderr_path else {}

    def _acp_build_mcp_servers(
        self,
        live: Any,
        config: Mapping[str, Any],
        *,
        user_id: str,
        conversation_id: str,
        agent_name: str,
    ) -> list[McpServerStdio]:
        if self.provider != PROVIDER:
            return super()._acp_build_mcp_servers(
                live, config, user_id=user_id,
                conversation_id=conversation_id, agent_name=agent_name)
        from core.docker_utils import get_host_ip
        from core.internal_auth import mint_token
        from core.llm_providers.claude_code_session import ClaudeCodeSessionMixin

        relay_url, relay_token = ClaudeCodeSessionMixin._get_tool_relay_info()
        if not relay_url:
            raise LLMClientError(
                "antigravity-acp requires a connected toolRelay service")
        host_ip = get_host_ip()
        relay_url = str(relay_url).replace("localhost", host_ip).replace("127.0.0.1", host_ip)
        self._acp_revoke_internal_token(live)
        token = mint_token()
        live.internal_token = token
        values = {
            "PAWFLOW_TOOL_RELAY_URL": relay_url,
            "PAWFLOW_TOOL_RELAY_TOKEN": str(relay_token or ""),
            "PAWFLOW_INTERNAL_TOKEN": token,
            "PAWFLOW_USER_ID": user_id,
            "PAWFLOW_CONVERSATION_ID": conversation_id,
            "PAWFLOW_AGENT_NAME": agent_name,
        }
        return [
            McpServerStdio(
                name="pawflow",
                command=CONTAINER_PYTHON,
                args=[CONTAINER_MCP_BRIDGE],
                env=[EnvVariable(name=name, value=value) for name, value in values.items()],
            )
        ]

    def _acp_close_entry(
        self,
        key: tuple[str, str, str, str],
        live: Any,
        *,
        force: bool,
    ) -> None:
        try:
            super()._acp_close_entry(key, live, force=force)
        finally:
            if self.provider == PROVIDER and force:
                service_id, user_id, conversation_id, agent_name = key
                # A killed docker CLI leaves the exec'd server alive; the
                # container is the exact unit that isolates it, so drop it on
                # force stop. Ephemeral streams share the foreground container
                # of their (user, conversation, agent, service) and only own
                # their process, so a clean close keeps the container.
                AntigravityAcpPool.instance().kill_session(
                    user_id=user_id, conversation_id=conversation_id,
                    agent_name=agent_name.split(_EPHEMERAL_MARKER, 1)[0],
                    service_id=service_id)


__all__ = [
    "AUTH_METHODS",
    "DEFAULT_AUTH_METHOD",
    "LLMAntigravityAcpMixin",
    "PROVIDER",
    "parse_cli_environment",
    "validate_antigravity_acp_config",
]
