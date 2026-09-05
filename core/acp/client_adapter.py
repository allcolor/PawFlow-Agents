"""Typed ACP Client adapter with injected PawFlow-owned handlers."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from acp import RequestError
from acp.router import Route
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    CreateTerminalResponse,
    DeclineElicitationResponse,
    FileSystemCapabilities,
    KillTerminalResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

from core.acp.session_state import AcpEventChannel

Handler = Callable[..., Any]
logger = logging.getLogger(__name__)


@dataclass
class AcpClientHandlers:
    """PawFlow authorities exposed to one outbound ACP connection."""

    permission: Handler | None = None
    read_text_file: Handler | None = None
    write_text_file: Handler | None = None
    create_terminal: Handler | None = None
    terminal_output: Handler | None = None
    wait_for_terminal_exit: Handler | None = None
    kill_terminal: Handler | None = None
    release_terminal: Handler | None = None
    create_elicitation: Handler | None = None
    complete_elicitation: Handler | None = None
    ext_method: Handler | None = None
    ext_notification: Handler | None = None
    extension_methods: tuple[str, ...] = ()
    extension_notifications: tuple[str, ...] = ()


def cancelled_permission_response() -> RequestPermissionResponse:
    """Return the stable ACP fail-closed permission outcome."""

    return RequestPermissionResponse.model_validate(
        {"outcome": {"outcome": "cancelled"}}
    )


def select_permission_response(
    decision: str,
    options: list[Any],
) -> RequestPermissionResponse:
    """Map a PawFlow decision to an option by exact stable ACP kind."""

    requested = str(decision or "").strip().lower()
    preferences = {
        "allow_once": ("allow_once",),
        "session_allow": ("allow_once",),
        "allow_always": ("allow_always", "allow_once"),
        "reject_once": ("reject_once", "reject_always"),
        "denied": ("reject_once", "reject_always"),
        "reject_always": ("reject_always", "reject_once"),
    }.get(requested, ())
    for kind in preferences:
        option = next(
            (candidate for candidate in options if candidate.kind == kind),
            None,
        )
        if option is not None:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(
                    outcome="selected",
                    option_id=option.option_id,
                )
            )
    return cancelled_permission_response()


class AcpClientAdapter:
    """Stable ACP Client implementation backed by explicit handler callables."""

    def __init__(
        self,
        event_channel: AcpEventChannel,
        generation: int,
        handlers: AcpClientHandlers | None = None,
    ) -> None:
        if generation <= 0:
            raise ValueError("generation must be positive")
        self._events = event_channel
        self._generation = generation
        self._handlers = handlers or AcpClientHandlers()
        self._session_generations: dict[str, int] = {}
        self._terminals: set[tuple[str, str]] = set()
        self._lock = threading.RLock()
        self.connection: Any = None

    @property
    def capabilities(self) -> ClientCapabilities:
        has_read = self._handlers.read_text_file is not None
        has_write = self._handlers.write_text_file is not None
        fs = None
        if has_read or has_write:
            fs = FileSystemCapabilities(
                read_text_file=has_read,
                write_text_file=has_write,
            )
        terminal_handlers = (
            self._handlers.create_terminal,
            self._handlers.terminal_output,
            self._handlers.wait_for_terminal_exit,
            self._handlers.kill_terminal,
            self._handlers.release_terminal,
        )
        return ClientCapabilities(
            fs=fs,
            terminal=all(handler is not None for handler in terminal_handlers),
        )

    def on_connect(self, connection: Any) -> None:
        self.connection = connection
        # SDK 0.12.1 only auto-routes underscore-prefixed extensions. Cursor
        # and Grok also send documented plain names. Register exact aliases
        # on this connection's SDK router; never replace global routing.
        aliases = (
            (self._handlers.extension_methods, "request", self.ext_method),
            (self._handlers.extension_notifications, "notification", self.ext_notification),
        )
        for methods, kind, handler in aliases:
            for method in methods:
                connection._conn._handler.add_route(Route(
                    method=method, kind=kind, func=partial(handler, method),
                ))

    def bind_session_generation(self, session_id: str, generation: int) -> None:
        with self._lock:
            self._session_generations[session_id] = generation

    def unbind_session_generation(self, session_id: str, generation: int) -> None:
        with self._lock:
            if self._session_generations.get(session_id) == generation:
                self._session_generations.pop(session_id, None)

    def _event_generation(self, session_id: str) -> int:
        with self._lock:
            return self._session_generations.get(session_id, self._generation)

    def _require_terminal_owner(self, session_id: str, terminal_id: str) -> None:
        with self._lock:
            owned = (session_id, terminal_id) in self._terminals
        if not owned:
            raise RequestError.invalid_params(
                {"message": "ACP terminal does not belong to this session"}
            )

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        del kwargs
        self._events.publish_update(
            self._event_generation(session_id),
            session_id,
            update,
        )

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[Any],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        del kwargs
        handler = self._require(self._handlers.permission, "session/request_permission")
        result = await self._invoke(handler, session_id, tool_call, options)
        if isinstance(result, RequestPermissionResponse):
            selected = getattr(result.outcome, "option_id", None)
            if selected is None:
                return result
            if any(option.option_id == selected for option in options):
                return result
            return cancelled_permission_response()
        return select_permission_response(str(result or ""), options)

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        del kwargs
        handler = self._require(self._handlers.read_text_file, "fs/read_text_file")
        result = await self._invoke(handler, session_id, path, line, limit)
        if isinstance(result, ReadTextFileResponse):
            return result
        if isinstance(result, str):
            return ReadTextFileResponse(content=result)
        raise TypeError("read_text_file handler must return str or ReadTextFileResponse")

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse:
        del kwargs
        handler = self._require(self._handlers.write_text_file, "fs/write_text_file")
        result = await self._invoke(handler, session_id, path, content)
        if result is None:
            return WriteTextFileResponse()
        if isinstance(result, WriteTextFileResponse):
            return result
        raise TypeError("write_text_file handler must return WriteTextFileResponse or None")

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[Any] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        del kwargs
        handler = self._require(self._handlers.create_terminal, "terminal/create")
        result = await self._invoke(
            handler,
            session_id,
            command,
            args,
            env,
            cwd,
            output_byte_limit,
        )
        if isinstance(result, str):
            result = CreateTerminalResponse(terminal_id=result)
        if not isinstance(result, CreateTerminalResponse):
            raise TypeError(
                "create_terminal handler must return str or CreateTerminalResponse"
            )
        with self._lock:
            self._terminals.add((session_id, result.terminal_id))
        return result

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> TerminalOutputResponse:
        del kwargs
        self._require_terminal_owner(session_id, terminal_id)
        handler = self._require(self._handlers.terminal_output, "terminal/output")
        result = await self._invoke(handler, session_id, terminal_id)
        if isinstance(result, str):
            result = TerminalOutputResponse(output=result, truncated=False)
        if not isinstance(result, TerminalOutputResponse):
            raise TypeError(
                "terminal_output handler must return str or TerminalOutputResponse"
            )
        return result

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> WaitForTerminalExitResponse:
        del kwargs
        self._require_terminal_owner(session_id, terminal_id)
        handler = self._require(
            self._handlers.wait_for_terminal_exit,
            "terminal/wait_for_exit",
        )
        result = await self._invoke(handler, session_id, terminal_id)
        if isinstance(result, int):
            result = WaitForTerminalExitResponse(exit_code=result)
        if not isinstance(result, WaitForTerminalExitResponse):
            raise TypeError(
                "wait_for_terminal_exit handler must return int "
                "or WaitForTerminalExitResponse"
            )
        return result

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> KillTerminalResponse:
        del kwargs
        self._require_terminal_owner(session_id, terminal_id)
        handler = self._require(self._handlers.kill_terminal, "terminal/kill")
        result = await self._invoke(handler, session_id, terminal_id)
        if result is None:
            return KillTerminalResponse()
        if isinstance(result, KillTerminalResponse):
            return result
        raise TypeError("kill_terminal handler must return KillTerminalResponse or None")

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> ReleaseTerminalResponse:
        del kwargs
        self._require_terminal_owner(session_id, terminal_id)
        handler = self._require(self._handlers.release_terminal, "terminal/release")
        result = await self._invoke(handler, session_id, terminal_id)
        if result is None:
            result = ReleaseTerminalResponse()
        if not isinstance(result, ReleaseTerminalResponse):
            raise TypeError(
                "release_terminal handler must return ReleaseTerminalResponse or None"
            )
        with self._lock:
            self._terminals.discard((session_id, terminal_id))
        return result

    async def create_elicitation(
        self,
        message: str,
        mode: Any,
        **kwargs: Any,
    ) -> Any:
        handler = self._handlers.create_elicitation
        if handler is None:
            return DeclineElicitationResponse(action="decline")
        return await self._invoke(handler, message, mode, **kwargs)

    async def complete_elicitation(
        self,
        elicitation_id: str,
        **kwargs: Any,
    ) -> None:
        handler = self._handlers.complete_elicitation
        if handler is not None:
            await self._invoke(handler, elicitation_id, **kwargs)

    async def ext_method(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        handler = self._require(self._handlers.ext_method, f"_{method}")
        result = await self._invoke(handler, method, params)
        if not isinstance(result, dict):
            raise TypeError("ext_method handler must return dict")
        return result

    async def ext_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        handler = self._handlers.ext_notification
        if handler is not None:
            await self._invoke(handler, method, params)

    async def close(self) -> None:
        handler = self._handlers.release_terminal
        with self._lock:
            terminals = list(self._terminals)
        if handler is None:
            with self._lock:
                self._terminals.clear()
            return
        for session_id, terminal_id in terminals:
            try:
                await self.release_terminal(session_id, terminal_id)
            except Exception as exc:  # noqa: BLE001 - injected owner boundary
                logger.warning(
                    "Failed to release ACP terminal %s: %s",
                    terminal_id,
                    exc,
                )

    @staticmethod
    async def _invoke(handler: Handler, *args: Any, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(handler):
            return await handler(*args, **kwargs)
        result = await asyncio.to_thread(handler, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _require(handler: Handler | None, method: str) -> Handler:
        if handler is None:
            raise RequestError.method_not_found(method)
        return handler
