"""Generic outbound Agent Client Protocol provider."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from acp import RequestError
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    EnvVariable,
    ImageContentBlock,
    McpServerStdio,
    TextContentBlock,
    TextResourceContents,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)

from core.acp import AcpClientHandlers, AcpProcessSession
from core.acp.client_adapter import select_permission_response
from core._llm_types import ColdStartRequired, LLMClientError

#: Providers that run through ``LLMAcpMixin``. The generic ``acp`` provider
#: takes its command from the service; specializations fix it themselves.
ACP_PROVIDERS = frozenset({"acp", "antigravity-acp", "cursor-acp", "grok-build-acp"})

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_MEDIA_BYTES = 16 * 1024 * 1024
_TERMINAL_TOOL_STATUSES = frozenset({"completed", "failed"})
_ACP_TOOL_POLICY_NAMES = {
    "read": "read",
    "edit": "write",
    "delete": "delete",
    "move": "edit",
    "search": "search",
    "execute": "bash",
    "think": "acp_think",
    "fetch": "web_search",
    "switch_mode": "acp_switch_mode",
    "other": "acp_tool",
}


def _boolean(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean, got {value!r}")


def _json_value(value: Any, expected: type, field_name: str) -> Any:
    if isinstance(value, expected):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be valid JSON")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON") from exc
    if not isinstance(parsed, expected):
        raise ValueError(
            f"{field_name} must decode to {expected.__name__}"
        )
    return parsed


def validate_acp_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded normalized config for a generic ACP process."""

    command = str(config.get("acp_command") or "").strip()
    if not command:
        raise ValueError("acp_command is required for provider 'acp'")
    if os.path.sep in command:
        resolved_command = os.path.abspath(command)
        if not os.path.isfile(resolved_command):
            raise ValueError("acp_command does not exist")
        if not os.access(resolved_command, os.X_OK):
            raise ValueError("acp_command is not executable")
    else:
        resolved_command = shutil.which(command) or ""
        if not resolved_command:
            raise ValueError(f"acp_command executable not found: {command}")

    raw_args = config.get("acp_args", [])
    if raw_args in (None, ""):
        raw_args = []
    args = _json_value(raw_args, list, "acp_args")
    if any(not isinstance(value, str) for value in args):
        raise ValueError("acp_args must be a JSON array of strings")

    cwd = str(config.get("acp_cwd") or "").strip()
    if not cwd:
        raise ValueError("acp_cwd is required for provider 'acp'")
    cwd = os.path.abspath(cwd)
    if not os.path.isdir(cwd):
        raise ValueError("acp_cwd must be an existing directory")

    raw_env = config.get("acp_env", {})
    if raw_env in (None, ""):
        raw_env = {}
    env = _json_value(raw_env, dict, "acp_env")
    for name, value in env.items():
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ValueError(f"Invalid acp_env variable name: {name!r}")
        if not isinstance(value, str):
            raise ValueError("acp_env values must be strings")

    raw_directories = config.get("acp_additional_directories", [])
    if raw_directories in (None, ""):
        raw_directories = []
    directories = _json_value(
        raw_directories, list, "acp_additional_directories"
    )
    normalized_directories = []
    for value in directories:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "acp_additional_directories must be a JSON array of paths"
            )
        path = os.path.abspath(value)
        if not os.path.isdir(path):
            raise ValueError(
                f"ACP additional directory does not exist: {value}"
            )
        normalized_directories.append(path)

    mcp_mode = str(config.get("acp_mcp_mode") or "pawflow").strip().lower()
    if mcp_mode not in {"none", "pawflow"}:
        raise ValueError("acp_mcp_mode must be 'none' or 'pawflow'")

    auth_method = str(config.get("acp_auth_method_id") or "").strip()
    title = str(config.get("acp_title_override") or "").strip()
    return {
        "command": resolved_command,
        "args": tuple(args),
        "cwd": cwd,
        "env": dict(env),
        "auth_method_id": auth_method,
        "auto_auth_single": _boolean(
            config.get("acp_auto_auth_single_method"), False
        ),
        "reuse_process": _boolean(
            config.get("acp_reuse_process"), True
        ),
        "load_session": _boolean(
            config.get("acp_load_session"), True
        ),
        "additional_directories": tuple(normalized_directories),
        "mcp_mode": mcp_mode,
        "use_client_io": _boolean(
            config.get("acp_use_client_io"), True
        ),
        "title": title,
    }


@dataclass
class _AcpLiveSession:
    signature: tuple[Any, ...]
    process: AcpProcessSession | None = None
    session_id: str = ""
    internal_token: str = ""
    registry: Any = None
    active_handle: Any = None
    turn_lock: threading.RLock = field(default_factory=threading.RLock)
    grant_lock: threading.RLock = field(default_factory=threading.RLock)
    write_grants: set[str] = field(default_factory=set)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    force_stop_event: threading.Event = field(default_factory=threading.Event)


class LLMAcpMixin:
    """Run a configured ACP agent as a normal PawFlow LLM provider."""

    def set_tool_registry(self, registry: Any) -> None:
        self._tool_registry = registry

    def _acp_config(self) -> dict[str, Any]:
        return validate_acp_config(self._config_ref)

    def _acp_shared_state(
        self,
    ) -> tuple[dict[tuple[str, str, str, str], _AcpLiveSession], threading.RLock]:
        sessions = getattr(self, "_acp_live_sessions", None)
        lock = getattr(self, "_acp_live_lock", None)
        if not isinstance(sessions, dict) or lock is None:
            sessions = {}
            lock = threading.RLock()
            self._acp_live_sessions = sessions
            self._acp_live_lock = lock
        return sessions, lock

    @staticmethod
    def _acp_stream_key(
        service_id: str,
        user_id: str,
        conversation_id: str,
        agent_name: str,
    ) -> tuple[str, str, str, str]:
        return (
            service_id or "acp",
            user_id,
            conversation_id,
            agent_name or "default",
        )

    @staticmethod
    def _acp_signature(config: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            config["command"],
            config["args"],
            config["cwd"],
            tuple(sorted(config["env"].items())),
            config["auth_method_id"],
            config["auto_auth_single"],
            config["load_session"],
            config["additional_directories"],
            config["mcp_mode"],
            config["use_client_io"],
        )

    def _acp_session_store_key(self, service_id: str, agent_name: str) -> str:
        signature = self._acp_signature(self._acp_config())
        payload = json.dumps(
            signature, ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        revision = hashlib.sha256(payload).hexdigest()[:16]
        return (
            f"acp_session:{service_id or 'acp'}:"
            f"{agent_name or 'default'}:{revision}"
        )

    def _acp_has_live_session(
        self,
        service_id: str,
        user_id: str,
        conversation_id: str,
        agent_name: str,
    ) -> bool:
        sessions, lock = self._acp_shared_state()
        key = self._acp_stream_key(
            service_id, user_id, conversation_id, agent_name
        )
        with lock:
            live = sessions.get(key)
            return bool(
                live is not None
                and live.process is not None
                and live.process.is_running
                and live.session_id
            )

    def _acp_get_stored_session(
        self,
        conversation_id: str,
        service_id: str,
        agent_name: str,
    ) -> str:
        if not conversation_id:
            return ""
        from core.conversation_store import ConversationStore

        return str(
            ConversationStore.instance().get_extra(
                conversation_id,
                self._acp_session_store_key(service_id, agent_name),
            )
            or ""
        )

    def _acp_set_stored_session(
        self,
        conversation_id: str,
        service_id: str,
        agent_name: str,
        session_id: str,
    ) -> None:
        if not conversation_id:
            return
        from core.conversation_store import ConversationStore

        ConversationStore.instance().set_extra(
            conversation_id,
            self._acp_session_store_key(service_id, agent_name),
            session_id,
        )

    @staticmethod
    def _acp_registry_result(result: Any, operation: str) -> str:
        text = str(result if result is not None else "")
        if text.startswith("Error"):
            raise RequestError.internal_error(
                {"operation": operation, "message": text[:1000]}
            )
        return text

    def _acp_client_handlers(
        self,
        live: _AcpLiveSession,
        enabled: bool,
        *,
        user_id: str,
        conversation_id: str,
        agent_name: str,
    ) -> AcpClientHandlers:
        from core.acp.native_extensions import (
            NativeAcpExtensions,
            interaction_permission,
        )

        native = NativeAcpExtensions(
            provider=getattr(self, "provider", "acp"), live=live,
            user_id=user_id, conversation_id=conversation_id, agent_name=agent_name,
        )

        def _registry(session_id: str) -> Any:
            if not live.session_id or session_id != live.session_id:
                raise RequestError.invalid_params(
                    {"message": "stale ACP session"}
                )
            if live.registry is None:
                raise RequestError.internal_error(
                    {"message": "PawFlow tool registry is unavailable"}
                )
            return live.registry

        def _safe_arguments(tool_call: Any) -> tuple[dict[str, Any], set[str]]:
            raw_input = getattr(tool_call, "raw_input", None)
            arguments = dict(raw_input) if isinstance(raw_input, Mapping) else {}
            paths: set[str] = set()
            for key, value in list(arguments.items()):
                if isinstance(value, str):
                    if key in {"path", "file_path"} and value.strip():
                        paths.add(value.strip())
                    elif len(value) > 1000:
                        arguments[key] = value[:1000] + "..."
                elif not isinstance(value, (int, float, bool, type(None))):
                    arguments[key] = f"<{type(value).__name__}>"
            for location in getattr(tool_call, "locations", None) or []:
                path = str(getattr(location, "path", "") or "").strip()
                if path:
                    paths.add(path)
            for content in getattr(tool_call, "content", None) or []:
                path = str(getattr(content, "path", "") or "").strip()
                if path:
                    paths.add(path)
            if len(paths) == 1 and "path" not in arguments:
                arguments["path"] = next(iter(paths))
            return arguments, paths

        def _authorize(
            tool_name: str,
            action_summary: str,
            arguments: dict[str, Any],
        ) -> str:
            from core.tool_approval import ToolApprovalGate
            from core.tool_authorization import gate_for_runtime

            mode = ToolApprovalGate.get_mode(conversation_id)
            if mode == "read_only" and not ToolApprovalGate.is_read_only_allowed(
                tool_name, arguments
            ):
                return "reject_once"
            gated = gate_for_runtime(
                tool_name=tool_name,
                arguments=arguments,
                user_id=user_id,
                conversation_id=conversation_id,
                agent_name=agent_name,
                runtime="acp",
                permission_mode=mode,
                cancel_event=live.cancel_event,
            )
            if gated == "":
                return "allow_once"
            if gated is not None:
                return "reject_once"
            if mode == "auto":
                return "allow_once"
            approval = ToolApprovalGate.check(
                tool_name,
                action_summary,
                conversation_id,
                user_id,
                arguments=arguments,
                agent_name=agent_name,
                cancel_event=live.cancel_event,
            )
            return "allow_once" if approval == "approved" else "reject_once"

        def permission(
            session_id: str,
            tool_call: Any,
            options: Sequence[Any],
        ) -> Any:
            if not live.session_id or session_id != live.session_id:
                raise RequestError.invalid_params({"message": "stale ACP session"})
            interaction = interaction_permission(tool_call, list(options), native.ask)
            if interaction is not None:
                return interaction
            _registry(session_id)
            kind = str(getattr(tool_call, "kind", "") or "other")
            tool_name = _ACP_TOOL_POLICY_NAMES.get(kind, "acp_tool")
            arguments, paths = _safe_arguments(tool_call)
            title = str(getattr(tool_call, "title", "") or tool_name).strip()
            decision = _authorize(tool_name, f"ACP: {title[:200]}", arguments)
            response = select_permission_response(decision, list(options))
            selected = getattr(response.outcome, "option_id", None)
            selected_kind = next(
                (
                    str(option.kind)
                    for option in options
                    if option.option_id == selected
                ),
                "",
            )
            if (
                tool_name == "write"
                and selected_kind in {"allow_once", "allow_always"}
            ):
                with live.grant_lock:
                    live.write_grants.update(paths)
            return response

        def read_text_file(
            session_id: str,
            path: str,
            line: int | None,
            limit: int | None,
        ) -> str:
            registry = _registry(session_id)
            arguments: dict[str, Any] = {"path": path}
            if line is not None:
                arguments["offset"] = line
            if limit is not None:
                arguments["limit"] = limit
            if _authorize("read", f"ACP read {path}", arguments) != "allow_once":
                raise RequestError.invalid_params(
                    {"message": "ACP read was denied by PawFlow policy"}
                )
            return self._acp_registry_result(
                registry.execute("read", arguments), "read_text_file"
            )

        def write_text_file(
            session_id: str,
            path: str,
            content: str,
        ) -> None:
            registry = _registry(session_id)
            normalized_path = str(path or "").strip()
            with live.grant_lock:
                if normalized_path not in live.write_grants:
                    raise RequestError.invalid_params(
                        {
                            "message": (
                                "ACP write requires a matching approved edit "
                                "permission"
                            )
                        }
                    )
                live.write_grants.remove(normalized_path)
            self._acp_registry_result(
                registry.execute(
                    "write", {"path": path, "content": content}
                ),
                "write_text_file",
            )

        return AcpClientHandlers(
            permission=permission,
            read_text_file=read_text_file if enabled else None,
            write_text_file=write_text_file if enabled else None,
            ext_method=native.request if native.provider in {"cursor-acp", "grok-build-acp"} else None,
            ext_notification=native.notification if native.provider == "cursor-acp" else None,
            extension_methods=(
                ("cursor/ask_question", "cursor/create_plan", "cursor/update_todos")
                if native.provider == "cursor-acp" else
                ("x.ai/ask_user_question", "x.ai/exit_plan_mode")
                if native.provider == "grok-build-acp" else ()
            ),
            extension_notifications=(
                ("cursor/update_todos",) if native.provider == "cursor-acp" else ()
            ),
        )

    def _acp_revoke_internal_token(self, live: _AcpLiveSession) -> None:
        token = live.internal_token
        live.internal_token = ""  # nosec B105 - clear the token, not a password literal
        if not token:
            return
        from core.internal_auth import revoke_token

        revoke_token(token)

    def _acp_close_entry(
        self,
        key: tuple[str, str, str, str],
        live: _AcpLiveSession,
        *,
        force: bool,
    ) -> None:
        sessions, lock = self._acp_shared_state()
        with lock:
            if sessions.get(key) is live:
                sessions.pop(key, None)
        process = live.process
        live.process = None
        live.active_handle = None
        live.session_id = ""
        live.cancel_event.set()
        live.force_stop_event.set()
        with live.grant_lock:
            live.write_grants.clear()
        try:
            if process is not None:
                process.close(force=force)
        finally:
            self._acp_revoke_internal_token(live)

    def _acp_build_mcp_servers(
        self,
        live: _AcpLiveSession,
        config: Mapping[str, Any],
        *,
        user_id: str,
        conversation_id: str,
        agent_name: str,
    ) -> list[McpServerStdio]:
        if config["mcp_mode"] == "none":
            return []
        from core.llm_providers.claude_code_session import (
            ClaudeCodeSessionMixin,
        )

        relay_url, relay_token = ClaudeCodeSessionMixin._get_tool_relay_info()
        if not relay_url:
            raise LLMClientError(
                "acp_mcp_mode='pawflow' requires a connected toolRelay service"
            )
        from core.internal_auth import mint_token

        self._acp_revoke_internal_token(live)
        token = mint_token()
        live.internal_token = token
        bridge = Path(__file__).resolve().parents[2] / "tools" / "mcp_bridge.py"
        if not bridge.is_file():
            raise LLMClientError("PawFlow MCP bridge executable is unavailable")
        values = {
            "PAWFLOW_TOOL_RELAY_URL": str(relay_url),
            "PAWFLOW_TOOL_RELAY_TOKEN": str(relay_token or ""),
            "PAWFLOW_INTERNAL_TOKEN": token,
            "PAWFLOW_USER_ID": user_id,
            "PAWFLOW_CONVERSATION_ID": conversation_id,
            "PAWFLOW_AGENT_NAME": agent_name,
        }
        return [
            McpServerStdio(
                name="pawflow",
                command=sys.executable,
                args=[str(bridge)],
                env=[
                    EnvVariable(name=name, value=value)
                    for name, value in values.items()
                ],
            )
        ]

    def _acp_authenticate(
        self,
        process: AcpProcessSession,
        initialized: Any,
        config: Mapping[str, Any],
    ) -> None:
        methods = list(initialized.auth_methods or [])
        configured = config["auth_method_id"]
        if not methods:
            if configured:
                raise LLMClientError(
                    "acp_auth_method_id was configured but the agent "
                    "advertised no authentication methods"
                )
            return
        by_id = {str(method.id): method for method in methods}
        method_id = configured
        if method_id:
            if method_id not in by_id:
                available = ", ".join(sorted(by_id))
                raise LLMClientError(
                    "Unknown acp_auth_method_id; advertised ids: "
                    + available
                )
        elif len(methods) == 1 and config["auto_auth_single"]:
            method_id = str(methods[0].id)
        else:
            available = ", ".join(sorted(by_id))
            raise LLMClientError(
                "ACP agent requires authentication; configure the exact "
                "acp_auth_method_id. Advertised ids: "
                + available
            )
        process.call("authenticate", method_id=method_id)

    @staticmethod
    def _acp_stale_session_error(exc: BaseException) -> bool:
        return (
            isinstance(exc, RequestError)
            and int(getattr(exc, "code", 0) or 0) == -32002
        )

    def _acp_process_kwargs(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """Extra ``AcpProcessSession`` keyword arguments for this provider."""
        del config
        return {}

    def _acp_process_class(self) -> type[AcpProcessSession]:
        return AcpProcessSession

    def _acp_open_session(
        self,
        live: _AcpLiveSession,
        config: Mapping[str, Any],
        *,
        user_id: str,
        conversation_id: str,
        agent_name: str,
        service_id: str,
        persist_session: bool,
    ) -> tuple[str, bool]:
        # The process cwd is where PawFlow launches the command; the session
        # cwd is the workspace the agent sees. They differ when the command
        # is a container bridge such as ``docker exec``.
        session_cwd = str(config.get("session_cwd") or config["cwd"])
        process = self._acp_process_class()(
            config["command"],
            config["args"],
            handlers=self._acp_client_handlers(
                live,
                bool(config["use_client_io"]),
                user_id=user_id,
                conversation_id=conversation_id,
                agent_name=agent_name,
            ),
            env=config["env"],
            cwd=config["cwd"],
            startup_timeout=15.0,
            shutdown_timeout=2.0,
            **self._acp_process_kwargs(config),
        )
        live.process = process
        initialized = process.start()
        self._acp_authenticate(process, initialized, config)
        capabilities = initialized.agent_capabilities
        additional_directories = list(config["additional_directories"])
        session_capabilities = capabilities.session_capabilities
        if (
            additional_directories
            and getattr(
                session_capabilities, "additional_directories", None
            )
            is None
        ):
            raise LLMClientError(
                "ACP agent does not support additionalDirectories"
            )
        mcp_servers = self._acp_build_mcp_servers(
            live,
            config,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
        )
        stored = (
            self._acp_get_stored_session(
                conversation_id, service_id, agent_name
            )
            if persist_session
            else ""
        )
        if stored and config["load_session"] and capabilities.load_session:
            live.session_id = stored
            try:
                process.call(
                    "load_session",
                    cwd=session_cwd,
                    session_id=stored,
                    mcp_servers=mcp_servers,
                    additional_directories=additional_directories or None,
                )
                return stored, False
            except BaseException as exc:
                live.session_id = ""
                if not self._acp_stale_session_error(exc):
                    raise
                self._acp_set_stored_session(
                    conversation_id, service_id, agent_name, ""
                )

        created = process.call(
            "new_session",
            cwd=session_cwd,
            mcp_servers=mcp_servers,
            additional_directories=additional_directories or None,
        )
        session_id = str(created.session_id or "")
        if not session_id:
            raise LLMClientError("ACP agent returned an empty session id")
        live.session_id = session_id
        return session_id, True

    @staticmethod
    def _acp_content_text(content: Any) -> str:
        if isinstance(content, TextContentBlock):
            return content.text
        resource = getattr(content, "resource", None)
        text = getattr(resource, "text", None)
        return str(text or "")

    @staticmethod
    def _acp_data_uri(value: str) -> tuple[str, str]:
        if not value.startswith("data:") or "," not in value:
            raise ValueError("ACP media content requires a base64 data URI")
        header, data = value.split(",", 1)
        if ";base64" not in header:
            raise ValueError("ACP media content requires base64 encoding")
        mime_type = header[5:].split(";", 1)[0].strip()
        if not mime_type:
            raise ValueError("ACP media content is missing a MIME type")
        try:
            decoded = base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise ValueError("ACP media content has invalid base64 data") from exc
        if len(decoded) > _MAX_MEDIA_BYTES:
            raise ValueError("ACP media content exceeds 16 MiB")
        return mime_type, data

    @staticmethod
    def _acp_http_media(
        url: str,
        *,
        user_id: str,
        conversation_id: str,
    ) -> tuple[str, str]:
        from core.relay_proxy_url import resolve_relay_aware_url

        safe_url = resolve_relay_aware_url(
            url,
            user_id=user_id,
            conversation_id=conversation_id,
            allow_private=False,
            service_name="ACP media attachment",
        )
        import requests

        try:
            with requests.get(
                safe_url,
                headers={"User-Agent": "PawFlow-ACP/1.0"},
                timeout=30,
                allow_redirects=False,
                stream=True,
            ) as response:
                status = int(response.status_code or 0)
                if status >= 300:
                    raise ValueError(
                        f"ACP media download returned HTTP {status}"
                    )
                raw_length = response.headers.get("Content-Length", "")
                try:
                    size_hint = int(raw_length or 0)
                except (TypeError, ValueError):
                    size_hint = 0
                if size_hint > _MAX_MEDIA_BYTES:
                    raise ValueError("ACP media content exceeds 16 MiB")
                chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_MEDIA_BYTES:
                        raise ValueError("ACP media content exceeds 16 MiB")
                    chunks.append(chunk)
                mime_type = str(
                    response.headers.get("Content-Type", "") or ""
                ).split(";", 1)[0].strip().lower()
        except requests.RequestException as exc:
            raise ValueError(f"ACP media download failed: {exc}") from exc
        if not mime_type.startswith(("image/", "audio/")):
            raise ValueError(
                f"ACP media URL returned unsupported type {mime_type!r}"
            )
        return mime_type, base64.b64encode(b"".join(chunks)).decode("ascii")

    def _acp_media_block(
        self,
        part: Mapping[str, Any],
        *,
        user_id: str,
        conversation_id: str,
        capabilities: Any,
    ) -> Any:
        kind = str(part.get("type") or "")
        mime_type = str(part.get("mime_type") or "")
        data = ""
        uri = None
        if kind == "image_url":
            uri = str((part.get("image_url") or {}).get("url") or "")
            if uri.startswith("data:"):
                mime_type, data = self._acp_data_uri(uri)
            elif uri.startswith(("http://", "https://", "relay://")):
                mime_type, data = self._acp_http_media(
                    uri,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
            else:
                raise ValueError(
                    "ACP image URL must use data, http, https, or relay"
                )
        elif kind in {"image", "audio"}:
            source = part.get("source") or {}
            if source.get("type") != "base64":
                raise ValueError(f"Unsupported ACP {kind} source")
            mime_type = str(source.get("media_type") or mime_type)
            data = str(source.get("data") or "")
            self._acp_data_uri(
                f"data:{mime_type};base64,{data}"
            )
        elif kind in {"image_ref", "file_ref"}:
            file_id = str(part.get("file_id") or "")
            if not file_id:
                raise ValueError(f"{kind} block missing file_id")
            if not user_id or not conversation_id:
                raise ValueError(
                    f"{kind} requires user and conversation identity"
                )
            from core.file_store import FileStore

            filename, payload, stored_type = FileStore.instance().get_required(
                file_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            mime_type = mime_type or str(stored_type or "")
            uri = f"fs://filestore/{file_id}/{filename}"
            if len(payload) > _MAX_MEDIA_BYTES:
                raise ValueError("ACP attachment exceeds 16 MiB")
            if mime_type.startswith(("image/", "audio/")):
                data = base64.b64encode(payload).decode("ascii")
            elif getattr(capabilities, "embedded_context", False):
                is_text = mime_type.startswith("text/") or mime_type in {
                    "application/json",
                    "application/xml",
                    "application/yaml",
                }
                resource = (
                    TextResourceContents(
                        uri=uri,
                        mime_type=mime_type or None,
                        text=payload.decode("utf-8"),
                    )
                    if is_text
                    else BlobResourceContents(
                        uri=uri,
                        mime_type=mime_type or None,
                        blob=base64.b64encode(payload).decode("ascii"),
                    )
                )
                return EmbeddedResourceContentBlock(
                    type="resource",
                    resource=resource,
                )
            elif mime_type.startswith("text/"):
                return TextContentBlock(
                    type="text", text=payload.decode("utf-8")
                )
            else:
                raise ValueError(
                    f"ACP agent cannot accept attachment type {mime_type!r}"
                )
        else:
            raise ValueError(f"Unsupported ACP content block type: {kind}")

        if mime_type.startswith("image/"):
            if not getattr(capabilities, "image", False):
                raise ValueError("ACP agent does not advertise image prompts")
            return ImageContentBlock(
                type="image",
                data=data,
                mime_type=mime_type,
                uri=uri,
            )
        if mime_type.startswith("audio/"):
            if not getattr(capabilities, "audio", False):
                raise ValueError("ACP agent does not advertise audio prompts")
            return AudioContentBlock(
                type="audio", data=data, mime_type=mime_type
            )
        raise ValueError(f"Unsupported ACP media type: {mime_type!r}")

    def _acp_prompt_blocks(
        self,
        messages: Sequence[Any],
        *,
        cold: bool,
        capabilities: Any,
        user_id: str,
        conversation_id: str,
    ) -> list[Any]:
        current = next(
            (
                message
                for message in reversed(messages)
                if getattr(message, "role", "") == "user"
            ),
            None,
        )
        if current is None:
            raise ValueError("ACP prompt requires a user message")

        blocks: list[Any] = []
        if cold:
            system_prompt, user_text = self._serialize_messages_for_cli(
                list(messages), None
            )
            text = user_text
            if system_prompt:
                text = (
                    "<system_instructions>\n"
                    + system_prompt
                    + "\n</system_instructions>\n\n"
                    + text
                )
            if text:
                blocks.append(TextContentBlock(type="text", text=text))
        else:
            content = getattr(current, "content", "")
            if isinstance(content, str):
                if content:
                    blocks.append(
                        TextContentBlock(type="text", text=content)
                    )
            elif isinstance(content, list):
                text = "\n".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, Mapping)
                    and part.get("type") == "text"
                )
                if text:
                    blocks.append(TextContentBlock(type="text", text=text))

        content = getattr(current, "content", "")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, Mapping):
                    raise ValueError("ACP content blocks must be objects")
                kind = str(part.get("type") or "")
                if kind == "text":
                    continue
                if kind == "document" and part.get("text"):
                    blocks.append(
                        TextContentBlock(
                            type="text", text=str(part.get("text"))
                        )
                    )
                    continue
                blocks.append(
                    self._acp_media_block(
                        part,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        capabilities=capabilities,
                    )
                )
        if not blocks:
            blocks.append(TextContentBlock(type="text", text=""))
        return blocks

    @staticmethod
    def _acp_tool_output(update: Any) -> str:
        value = getattr(update, "raw_output", None)
        if isinstance(value, str) and value:
            return value
        if value is not None and not isinstance(value, str):
            return json.dumps(value, ensure_ascii=False, default=str)
        content = getattr(update, "content", None) or []
        if not content:
            return ""
        serialized = [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            if hasattr(item, "model_dump")
            else item
            for item in content
        ]
        return json.dumps(serialized, ensure_ascii=False, default=str)

    def _acp_apply_update(
        self,
        update: Any,
        *,
        callback: Any,
        thinking_callback: Any,
        block_callback: Any,
        text_parts: list[str],
        thinking_parts: list[str],
        started_tools: set[str],
        completed_tools: set[str],
        tool_id_prefix: str,
        conversation_id: str,
        agent_name: str,
        user_id: str,
        event_cid: str,
    ) -> None:
        if isinstance(update, AgentMessageChunk):
            delta = self._acp_content_text(update.content)
            if delta:
                text_parts.append(delta)
                if callback:
                    callback(delta)
            return
        if isinstance(update, AgentThoughtChunk):
            delta = self._acp_content_text(update.content)
            if delta:
                thinking_parts.append(delta)
                if thinking_callback:
                    thinking_callback(delta)
            return
        if isinstance(update, UsageUpdate):
            self._record_observed_context(
                conversation_id, agent_name, update.used, mode="session"
            )
            windows = getattr(
                self, "_cli_observed_context_window_by_stream", None
            )
            if isinstance(windows, dict) and update.size > 0:
                windows[(conversation_id, agent_name)] = int(update.size)
            self.publish_observed_context_usage(
                conversation_id,
                agent_name,
                user_id=user_id,
                event_cid=event_cid,
                source="acp_usage_update",
            )
            return
        if isinstance(update, (ToolCallStart, ToolCallProgress)):
            raw_tool_id = str(update.tool_call_id or "")
            if not raw_tool_id:
                return
            tool_id = f"{tool_id_prefix}:{raw_tool_id}"
            title = str(getattr(update, "title", "") or "ACP tool")
            arguments = getattr(update, "raw_input", None)
            if not isinstance(arguments, dict):
                arguments = {}
            if tool_id not in started_tools:
                started_tools.add(tool_id)
                if block_callback:
                    block_callback(
                        "tool_use",
                        {
                            "id": tool_id,
                            "name": title,
                            "arguments": arguments,
                            "thinking": "".join(thinking_parts).strip(),
                            "tool_origin": "acp",
                        },
                    )
                    thinking_parts.clear()
            status = str(getattr(update, "status", "") or "")
            if status in _TERMINAL_TOOL_STATUSES and tool_id not in completed_tools:
                completed_tools.add(tool_id)
                if block_callback:
                    block_callback(
                        "tool_result",
                        {
                            "tc_id": tool_id,
                            "tool": title,
                            "result": self._acp_tool_output(update),
                            "is_error": status == "failed",
                        },
                    )

    @staticmethod
    def _acp_usage_delta(
        live: _AcpLiveSession, response: Any
    ) -> tuple[int, int, int, int]:
        """Return ACP PromptResponse usage, whose counters are per turn."""

        del live
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0, 0
        current = {
            "input": int(usage.input_tokens or 0),
            "output": int(usage.output_tokens or 0),
            "cache_read": int(usage.cached_read_tokens or 0),
            "cache_write": int(usage.cached_write_tokens or 0),
        }
        return tuple(
            max(0, current[name])
            for name in ("input", "output", "cache_read", "cache_write")
        )

    def _stream_acp(
        self,
        messages: Sequence[Any],
        model: str,
        temperature: float,
        max_tokens: int,
        tools: Sequence[Any] | None,
        callback: Any = None,
        thinking_budget: int = 0,
        thinking_callback: Any = None,
        turn_callback: Any = None,
        block_callback: Any = None,
        *,
        call_user_id: str | None = None,
        call_conversation_id: str | None = None,
        call_agent_name: str | None = None,
        call_event_cid: str | None = None,
        call_ephemeral_stream: bool | None = None,
    ) -> Any:
        del temperature, max_tokens, thinking_budget, turn_callback, tools
        config = self._acp_config()
        user_id = str(call_user_id or "")
        conversation_id = str(call_conversation_id or "")
        agent_name = str(call_agent_name or "")
        event_cid = str(call_event_cid or conversation_id)
        ephemeral = bool(call_ephemeral_stream)
        service_id = str(
            getattr(self, "_agent_service", "")
            or self._config_ref.get("_service_id", "")
            or ""
        )
        key = self._acp_stream_key(
            service_id, user_id, conversation_id, agent_name
        )
        signature = self._acp_signature(config)
        sessions, shared_lock = self._acp_shared_state()
        replaced_live = None
        if ephemeral:
            key = (*key[:3], f"{key[3]}:ephemeral:{uuid.uuid4().hex}")
            live = _AcpLiveSession(signature=signature)
            with shared_lock:
                sessions[key] = live
        else:
            with shared_lock:
                live = sessions.get(key)
                if live is not None and live.signature != signature:
                    replaced_live = live
                    sessions.pop(key, None)
                    live = None
                if live is None:
                    live = _AcpLiveSession(signature=signature)
                    sessions[key] = live
        if replaced_live is not None:
            self._acp_close_entry(key, replaced_live, force=True)

        response = None
        clean = False
        created = False
        with live.turn_lock:
            live.cancel_event.clear()
            live.force_stop_event.clear()
            with live.grant_lock:
                live.write_grants.clear()
            live.registry = getattr(self, "_tool_registry", None)
            try:
                process = live.process
                cold = not (
                    process is not None
                    and process.is_running
                    and bool(live.session_id)
                )
                if cold:
                    if process is not None:
                        process.close(force=True)
                        self._acp_revoke_internal_token(live)
                    live.process = None
                    live.session_id = ""
                    session_id, created = self._acp_open_session(
                        live,
                        config,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        agent_name=agent_name,
                        service_id=service_id,
                        persist_session=not ephemeral,
                    )
                    if (
                        not ephemeral
                        and created
                        and getattr(self, "_pawflow_context_is_delta", False)
                    ):
                        self._acp_set_stored_session(
                            conversation_id, service_id, agent_name, ""
                        )
                        raise ColdStartRequired(
                            "ACP session disappeared before a delta turn; "
                            "rebuild the full PawFlow context"
                        )
                    cold_prompt = created
                else:
                    session_id = live.session_id
                    cold_prompt = False
                process = live.process
                if process is None:
                    raise LLMClientError("ACP process session is unavailable")
                initialized = process.initialize_response
                prompt_capabilities = (
                    initialized.agent_capabilities.prompt_capabilities
                )
                prompt = self._acp_prompt_blocks(
                    messages,
                    cold=cold_prompt,
                    capabilities=prompt_capabilities,
                    user_id=user_id,
                    conversation_id=conversation_id,
                )
                process.events.drain()
                dropped_before = process.events.dropped_updates
                handle = process.begin_prompt(session_id, prompt)
                live.active_handle = handle
                text_parts: list[str] = []
                thinking_parts: list[str] = []
                started_tools: set[str] = set()
                completed_tools: set[str] = set()
                while response is None:
                    try:
                        event = process.events.get(timeout=0.2)
                    except TimeoutError:
                        if live.force_stop_event.is_set():
                            raise LLMClientError("ACP prompt was cancelled") from None
                        continue
                    if event.generation != handle.generation:
                        continue
                    if event.kind == "process_exit":
                        raise event.error or LLMClientError(
                            "ACP process exited during prompt"
                        )
                    if event.session_id != session_id:
                        continue
                    if event.kind == "update":
                        self._acp_apply_update(
                            event.payload,
                            callback=callback,
                            thinking_callback=thinking_callback,
                            block_callback=block_callback,
                            text_parts=text_parts,
                            thinking_parts=thinking_parts,
                            started_tools=started_tools,
                            completed_tools=completed_tools,
                            tool_id_prefix=f"acp-{handle.generation}",
                            conversation_id=conversation_id,
                            agent_name=agent_name,
                            user_id=user_id,
                            event_cid=event_cid,
                        )
                    elif event.kind == "response":
                        response = event.payload
                    elif event.kind == "cancelled":
                        raise LLMClientError("ACP prompt was cancelled")
                    else:
                        raise event.error or LLMClientError(
                            "ACP prompt failed"
                        )
                if process.events.dropped_updates != dropped_before:
                    raise LLMClientError(
                        "ACP update channel overflowed; refusing a partial response"
                    )
                handle.result()
                tokens_in, tokens_out, cache_read, cache_write = (
                    self._acp_usage_delta(live, response)
                )
                from core._llm_types import LLMResponse

                result = LLMResponse(
                    content="".join(text_parts),
                    model=config["title"] or model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_write,
                    input_usage_native=(
                        True if getattr(response, "usage", None) is not None
                        else False
                    ),
                    finish_reason=str(response.stop_reason or "end_turn"),
                    thinking="".join(thinking_parts),
                    raw={
                        "session_id": session_id,
                        "tool_results": len(completed_tools),
                    },
                )
                if created and not ephemeral:
                    self._acp_set_stored_session(
                        conversation_id, service_id, agent_name, session_id
                    )
                clean = True
                return result
            except LLMClientError:
                raise
            except Exception as exc:
                raise LLMClientError(f"ACP provider failed: {exc}") from exc
            finally:
                live.active_handle = None
                live.registry = None
                with live.grant_lock:
                    live.write_grants.clear()
                should_close = (
                    ephemeral
                    or not config["reuse_process"]
                    or not clean
                    or (
                        response is not None
                        and str(response.stop_reason or "") == "cancelled"
                    )
                )
                if should_close:
                    self._acp_close_entry(key, live, force=not clean)

    def _acp_abort_active(self, *, force: bool) -> None:
        sessions, _ = self._acp_shared_state()
        for key, live in list(sessions.items()):
            handle = live.active_handle
            process = live.process
            if handle is None or process is None:
                continue
            try:
                if force:
                    live.cancel_event.set()
                    live.force_stop_event.set()
                    self._acp_close_entry(key, live, force=True)
                else:
                    live.cancel_event.set()
                    handle.cancel()
            except Exception:
                if force:
                    raise

    def _acp_close_all(self) -> None:
        sessions, _ = self._acp_shared_state()
        for key, live in list(sessions.items()):
            self._acp_close_entry(key, live, force=True)


__all__ = ["ACP_PROVIDERS", "LLMAcpMixin", "validate_acp_config"]
