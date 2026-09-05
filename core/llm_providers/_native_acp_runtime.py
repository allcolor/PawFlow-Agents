"""Managed Cursor/Grok containers attached to the shared ACP session lifecycle."""

from __future__ import annotations

import logging
import os
import re
import subprocess  # nosec B404 - bounded Docker lifecycle calls.
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from core._llm_types import LLMClientError
from core.llm_providers.acp import _boolean, _json_value
from core.native_cli_auth import (
    native_cli_binary,
    native_cli_home,
    native_cli_image,
    native_cli_user_spec,
)

PROVIDERS = frozenset({"cursor-acp", "grok-build-acp"})
CONTAINER_HOME = "/native-home"
CONTAINER_BRIDGE = "/opt/pawflow/mcp_bridge.py"
CONTAINER_PYTHON = "/usr/bin/python3"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_ENV = {"HOME", "USER", "LOGNAME", "PATH", "SHELL", "BASH_ENV", "ENV", "WSLENV"}
_RESERVED_PREFIXES = ("PAWFLOW_", "DOCKER_", "XDG_", "LD_", "DYLD_", "PYTHON", "NODE", "BUN_")
logger = logging.getLogger(__name__)


def _directory(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required and must name an existing directory")
    path = Path(value.strip()).resolve()
    if not path.is_dir() or path == Path(path.anchor):
        raise ValueError(f"{field} must be an existing non-root directory")
    for reserved in ("/native-home", "/opt/pawflow", "/usr", "/bin", "/sbin",
                     "/lib", "/lib64", "/etc", "/proc", "/sys", "/dev"):
        protected = Path(reserved)
        if path.is_relative_to(protected) or protected.is_relative_to(path):
            raise ValueError(f"{field} overlaps a protected native runtime path")
    return str(path)


def validate_native_acp_config(
    config: Mapping[str, Any], provider: str, defaults: list[str], title: str,
) -> dict[str, Any]:
    """Validate container settings without looking for the native CLI on the host."""
    if str(config.get("auth_mode") or "none") != "none":
        raise ValueError(f"{provider} uses native CLI authentication; auth_mode must be none")
    if config.get("api_key") or config.get("credential_service_id"):
        raise ValueError(f"{provider} uses native CLI auth or provider keys in acp_env")

    command = str(config.get("acp_command") or native_cli_binary(provider)).strip()
    if not command or command.startswith("-") or "\x00" in command:
        raise ValueError("acp_command must name an executable inside the managed image")
    raw_args = config.get("acp_args")
    args = _json_value([] if raw_args in (None, "") else raw_args, list, "acp_args")
    if any(not isinstance(value, str) or "\x00" in value for value in args):
        raise ValueError("acp_args must be a JSON array of strings without NUL")
    # The service UI serializes its empty default as the nonempty string '[]'.
    args = args or defaults

    raw_env = config.get("acp_env")
    env = _json_value({} if raw_env in (None, "") else raw_env, dict, "acp_env")
    for name, value in env.items():
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ValueError("acp_env contains an invalid variable name")
        if name in _RESERVED_ENV or name.startswith(_RESERVED_PREFIXES):
            raise ValueError(f"acp_env cannot override runtime variable {name}")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("acp_env values must be strings without NUL")

    cwd = _directory(config.get("acp_cwd"), "acp_cwd")
    raw_directories = config.get("acp_additional_directories")
    directories = _json_value(
        [] if raw_directories in (None, "") else raw_directories,
        list, "acp_additional_directories",
    )
    directories = tuple(
        _directory(value, "acp_additional_directories") for value in directories
    )
    mcp_mode = str(config.get("acp_mcp_mode") or "pawflow").strip().lower()
    if mcp_mode not in {"none", "pawflow"}:
        raise ValueError("acp_mcp_mode must be 'none' or 'pawflow'")
    image = native_cli_image(provider)
    if not image or image.startswith("-") or "\x00" in image:
        raise ValueError("Native CLI image must be a Docker image reference")
    return {
        "command": command,
        "args": tuple(args),
        "cwd": cwd,
        "env": dict(env),
        "image": image,
        "user_spec": native_cli_user_spec(),
        "auth_method_id": str(config.get("acp_auth_method_id") or "").strip(),
        "auto_auth_single": _boolean(config.get("acp_auto_auth_single_method"), False),
        "reuse_process": _boolean(config.get("acp_reuse_process"), True),
        "load_session": _boolean(config.get("acp_load_session"), True),
        "additional_directories": directories,
        "mcp_mode": mcp_mode,
        "use_client_io": _boolean(config.get("acp_use_client_io"), True),
        "title": str(config.get("acp_title_override") or title).strip(),
    }


def _mount(path: str | Path, target: str, *, readonly: bool = False) -> list[str]:
    from core.docker_utils import to_host_path, translate_path

    source = translate_path(to_host_path(str(Path(path).resolve())))
    if ":" in source or "\x00" in source:
        raise ValueError("Native ACP mount source cannot contain ':' or NUL")
    return ["-v", f"{source}:{target}" + (":ro" if readonly else "")]


class NativeAcpRuntime:
    """One container per live ACP process, including each ephemeral process."""

    def __init__(self, provider: str, config: Mapping[str, Any], *,
                 user_id: str, conversation_id: str, agent_name: str, service_id: str):
        from core.docker_utils import (
            docker_cmd,
            get_server_id,
            pawflow_container_labels,
        )

        if not all((user_id, conversation_id, agent_name, service_id)):
            raise LLMClientError(
                f"{provider} requires user_id, conversation_id, agent_name and service_id"
            )
        self.name = f"pf-{get_server_id()[:12]}-nativeacp-{uuid.uuid4().hex[:12]}"
        self.docker = list(docker_cmd())
        home = native_cli_home(provider, user_id, service_id)
        # ACP callbacks retain the configured relay path contract. Renaming
        # nested workspaces to /workspace would address a different relay file.
        self.cwd = config["cwd"]
        mounts = _mount(home, CONTAINER_HOME) + _mount(self.cwd, self.cwd)
        self.additional_directories = []
        mounted = {self.cwd}
        for directory in config["additional_directories"]:
            if directory not in mounted:
                path = Path(directory)
                if not path.is_relative_to(self.cwd):
                    mounts += _mount(directory, directory)
                mounted.add(directory)
            self.additional_directories.append(directory)

        if config["mcp_mode"] == "pawflow":
            root = Path(__file__).resolve().parents[2]
            for source, target in (
                (root / "tools/mcp_bridge.py", CONTAINER_BRIDGE),
                (root / "core/tool_json.py", "/opt/pawflow/tool_json.py"),
                (root / "pawflow_relay", "/opt/pawflow/pawflow_relay"),
            ):
                mounts += _mount(source, target, readonly=True)

        # Override the image entrypoint directly. No shell, exposed ports,
        # shared session root, Docker socket, or inherited host provider keys.
        self.argv = self.docker + [
            "run", "--rm", "--pull", "never", "-i", "--init", "--name", self.name,
            *pawflow_container_labels(provider),
            "--user", config["user_spec"], "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--add-host", "host.docker.internal:host-gateway",
            "-w", self.cwd, "-e", f"HOME={CONTAINER_HOME}", "-e", "USER=pawflow",
            *mounts,
        ]
        for name in sorted(config["env"]):
            self.argv += ["-e", name]
        self.argv += ["--entrypoint", config["command"], config["image"], *config["args"]]

    def close(self) -> None:
        # Killing the docker CLI alone can leave the native agent and MCP alive.
        try:
            result = subprocess.run(  # nosec B603 - internally built argv, no shell.
                self.docker + ["rm", "-f", self.name],
                capture_output=True, timeout=10, check=False,
            )
            if result.returncode and b"No such container" not in result.stderr:
                logger.warning("Native ACP container cleanup failed: %s", self.name)
        except (OSError, subprocess.SubprocessError):
            logger.warning("Native ACP container cleanup failed: %s", self.name, exc_info=True)


class LLMNativeAcpRuntimeMixin:
    """Shared guarded overrides; retain generic ACP sessions and provider extensions."""

    def _acp_signature(self, config: Mapping[str, Any]) -> tuple[Any, ...]:
        signature = super()._acp_signature(config)
        if self.provider not in PROVIDERS:
            return signature
        return (*signature, self.provider, config["image"], config["user_spec"])

    def _acp_open_session(self, live: Any, config: Mapping[str, Any], *,
                          user_id: str, conversation_id: str, agent_name: str,
                          service_id: str, persist_session: bool) -> tuple[str, bool]:
        identity = {
            "user_id": user_id, "conversation_id": conversation_id,
            "agent_name": agent_name, "service_id": service_id,
            "persist_session": persist_session,
        }
        if self.provider not in PROVIDERS:
            return super()._acp_open_session(live, config, **identity)
        previous = getattr(live, "native_runtime", None)
        if previous is not None:
            previous.close()
        runtime = NativeAcpRuntime(
            self.provider, config, user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name, service_id=service_id,
        )
        live.native_runtime = runtime
        launch = {
            **config, "command": runtime.argv[0], "args": tuple(runtime.argv[1:]),
            "session_cwd": runtime.cwd,
            "additional_directories": tuple(runtime.additional_directories),
        }
        try:
            return super()._acp_open_session(live, launch, **identity)
        except BaseException:
            runtime.close()
            raise

    def _acp_process_kwargs(self, config: Mapping[str, Any]) -> dict[str, Any]:
        if self.provider not in PROVIDERS:
            return super()._acp_process_kwargs(config)
        return {"stderr_path": os.devnull}

    def _acp_build_mcp_servers(self, live: Any, config: Mapping[str, Any], **identity):
        servers = super()._acp_build_mcp_servers(live, config, **identity)
        if self.provider not in PROVIDERS:
            return servers
        from core.docker_utils import get_host_ip

        for server in servers:
            server.command = CONTAINER_PYTHON
            server.args = [CONTAINER_BRIDGE]
            for entry in server.env:
                if entry.name == "PAWFLOW_TOOL_RELAY_URL":
                    entry.value = entry.value.replace("localhost", get_host_ip()).replace(
                        "127.0.0.1", get_host_ip()
                    )
        return servers

    def _acp_close_entry(self, key, live, *, force: bool) -> None:
        runtime = getattr(live, "native_runtime", None) if self.provider in PROVIDERS else None
        try:
            if runtime is not None and force:
                runtime.close()
            super()._acp_close_entry(key, live, force=force)
        finally:
            if runtime is not None:
                runtime.close()
                if getattr(live, "native_runtime", None) is runtime:
                    live.native_runtime = None
