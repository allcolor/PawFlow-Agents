"""Single-owner process and event-loop bridge for outbound ACP agents."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from acp import spawn_agent_process
from acp.schema import Implementation

from core.acp.client_adapter import AcpClientAdapter, AcpClientHandlers
from core.acp.errors import (
    AcpProcessExitedError,
    AcpSessionClosedError,
    AcpStartupError,
)
from core.acp.protocol import initialize_connection
from core.acp.session_state import AcpEventChannel


class AcpPromptHandle:
    """Thread-safe handle for one submitted ACP prompt."""

    def __init__(
        self,
        owner: AcpProcessSession,
        generation: int,
        session_id: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        self._owner = owner
        self.generation = generation
        self.session_id = session_id
        self._future = future

    @property
    def done(self) -> bool:
        return self._future.done()

    def result(self, timeout: float | None = None) -> Any:
        return self._future.result(timeout)

    def cancel(self, timeout: float | None = None) -> None:
        self._owner.cancel(self.session_id, timeout=timeout)


class AcpProcessSession:
    """Own one ACP subprocess, SDK connection, and asyncio loop thread."""

    def __init__(
        self,
        command: str,
        args: Sequence[str],
        *,
        handlers: AcpClientHandlers | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        event_capacity: int = 256,
        startup_timeout: float = 15.0,
        shutdown_timeout: float = 2.0,
        client_info: Implementation | None = None,
        stderr_path: str | None = None,
    ) -> None:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command is required")
        if isinstance(args, (str, bytes)) or any(
            not isinstance(arg, str) for arg in args
        ):
            raise ValueError("args must be a sequence of strings")
        if startup_timeout <= 0:
            raise ValueError("startup_timeout must be positive")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")
        if env is not None and any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in env.items()
        ):
            raise ValueError("env keys and values must be strings")

        self.command = command
        self.args = tuple(args)
        self.env = dict(env) if env is not None else None
        self.cwd = cwd
        # Where the agent's stderr goes. The SDK default is an unread pipe,
        # which blocks a verbose agent once the pipe buffer fills; a file
        # keeps its diagnostics without that back-pressure.
        self.stderr_path = str(stderr_path) if stderr_path else None
        self.events = AcpEventChannel(event_capacity)
        self._handlers = handlers or AcpClientHandlers()
        self._startup_timeout = startup_timeout
        self._shutdown_timeout = shutdown_timeout
        self._client_info = client_info

        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._connection: Any = None
        self._process: Any = None
        self._adapter: AcpClientAdapter | None = None
        self._initialize_response: Any = None
        self._startup_error: BaseException | None = None
        self._runtime_error: BaseException | None = None
        self._generation = 0
        self._closing_generations: set[int] = set()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def initialize_response(self) -> Any:
        with self._lock:
            return self._initialize_response

    @property
    def runtime_error(self) -> BaseException | None:
        with self._lock:
            return self._runtime_error

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(
                self._thread
                and self._thread.is_alive()
                and self._connection is not None
            )

    def start(self, timeout: float | None = None) -> Any:
        wait_timeout = self._startup_timeout if timeout is None else timeout
        if wait_timeout <= 0:
            raise ValueError("timeout must be positive")

        with self._lock:
            current = self._thread
            if current is not None and current.is_alive():
                if self._connection is not None:
                    return self._initialize_response
                raise AcpStartupError("ACP process is already starting")
            if current is not None:
                current.join(timeout=0)

            self._generation += 1
            generation = self._generation
            self._ready = threading.Event()
            self._startup_error = None
            self._runtime_error = None
            self._initialize_response = None
            thread = threading.Thread(
                target=self._thread_main,
                args=(generation,),
                name=f"pawflow-acp-{generation}",
                daemon=True,
            )
            self._thread = thread
            thread.start()

        if not self._ready.wait(wait_timeout):
            self.close(force=True)
            raise AcpStartupError("timed out starting ACP process")

        with self._lock:
            error = self._startup_error
            response = self._initialize_response
        if error is not None:
            self.close(force=True)
            raise AcpStartupError(str(error)) from error
        if response is None:
            self.close(force=True)
            raise AcpStartupError("ACP process stopped before initialization")
        return response

    def call(
        self,
        method: str,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        connection, loop = self._running_connection()
        coroutine = getattr(connection, method)(*args, **kwargs)
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f"timed out waiting for ACP {method}") from None

    def begin_prompt(
        self,
        session_id: str,
        prompt: list[Any],
    ) -> AcpPromptHandle:
        if not session_id:
            raise ValueError("session_id is required")
        connection, loop = self._running_connection()
        with self._lock:
            generation = self._generation
        future = asyncio.run_coroutine_threadsafe(
            self._run_prompt(connection, generation, session_id, prompt),
            loop,
        )
        return AcpPromptHandle(self, generation, session_id, future)

    def cancel(
        self,
        session_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        self.call("cancel", session_id=session_id, timeout=timeout)

    def restart(self, timeout: float | None = None) -> Any:
        self.close(force=True)
        return self.start(timeout=timeout)

    def close(self, *, force: bool = False) -> None:
        with self._lock:
            thread = self._thread
            loop = self._loop
            generation = self._generation
            if thread is None or not thread.is_alive():
                return
            self._closing_generations.add(generation)
            if loop is not None:
                loop.call_soon_threadsafe(self._signal_stop, force)

        join_timeout = (
            self._shutdown_timeout + 1.0
            if force
            else (self._shutdown_timeout * 2.0) + 1.0
        )
        thread.join(join_timeout)
        if thread.is_alive() and not force:
            with self._lock:
                loop = self._loop
                if loop is not None:
                    loop.call_soon_threadsafe(self._signal_stop, True)
            thread.join(self._shutdown_timeout + 1.0)
        if thread.is_alive():
            raise AcpSessionClosedError("ACP process did not stop")

    async def _run_prompt(
        self,
        connection: Any,
        generation: int,
        session_id: str,
        prompt: list[Any],
    ) -> Any:
        adapter = self._adapter
        if adapter is None:
            raise AcpSessionClosedError("ACP client adapter is unavailable")
        adapter.bind_session_generation(session_id, generation)
        try:
            response = await connection.prompt(
                session_id=session_id,
                prompt=prompt,
            )
        except BaseException as exc:
            with self._lock:
                closing = generation in self._closing_generations
            self.events.publish_terminal(
                generation,
                session_id,
                "cancelled" if closing or isinstance(exc, asyncio.CancelledError)
                else "error",
                error=None if closing else exc,
            )
            raise
        else:
            self.events.publish_terminal(
                generation,
                session_id,
                "response",
                payload=response,
            )
            return response
        finally:
            adapter.unbind_session_generation(session_id, generation)

    def _running_connection(
        self,
    ) -> tuple[Any, asyncio.AbstractEventLoop]:
        with self._lock:
            if (
                self._connection is None
                or self._loop is None
                or self._thread is None
                or not self._thread.is_alive()
            ):
                raise AcpSessionClosedError("ACP process session is not running")
            return self._connection, self._loop

    def _thread_main(self, generation: int) -> None:
        try:
            asyncio.run(self._own_process(generation))
        except Exception as exc:  # noqa: BLE001 - thread ownership boundary
            with self._lock:
                initialized = self._initialize_response is not None
                if initialized:
                    self._runtime_error = exc
                else:
                    self._startup_error = exc
            if initialized:
                self.events.publish_terminal(
                    generation,
                    "",
                    "process_exit",
                    error=exc,
                )
        finally:
            with self._lock:
                if self._generation == generation:
                    self._connection = None
                    self._process = None
                    self._adapter = None
                    self._loop = None
                    self._stop_event = None
                self._closing_generations.discard(generation)
                self._ready.set()

    async def _own_process(self, generation: int) -> None:
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()
        adapter = AcpClientAdapter(
            self.events,
            generation,
            handlers=self._handlers,
        )
        with self._lock:
            self._loop = loop
            self._stop_event = stop_event
            self._adapter = adapter

        transport_kwargs: dict[str, Any] = {
            "shutdown_timeout": self._shutdown_timeout,
        }
        stderr_file = None
        if self.stderr_path:
            os.makedirs(os.path.dirname(self.stderr_path) or ".", exist_ok=True)
            stderr_file = open(self.stderr_path, "ab", buffering=0)  # noqa: SIM115
            transport_kwargs["stderr"] = stderr_file.fileno()
        try:
            await self._run_process(adapter, generation, stop_event, transport_kwargs)
        finally:
            if stderr_file is not None:
                stderr_file.close()

    async def _run_process(
        self,
        adapter: AcpClientAdapter,
        generation: int,
        stop_event: asyncio.Event,
        transport_kwargs: Mapping[str, Any],
    ) -> None:
        async with spawn_agent_process(
            adapter,
            self.command,
            *self.args,
            env=({**os.environ, **self.env} if self.env is not None else None),
            cwd=self.cwd,
            transport_kwargs=dict(transport_kwargs),
        ) as (connection, process):
            with self._lock:
                self._connection = connection
                self._process = process

            response = await initialize_connection(
                connection,
                client_capabilities=adapter.capabilities,
                client_info=self._client_info,
            )
            with self._lock:
                self._initialize_response = response
                self._ready.set()

            process_wait = asyncio.create_task(process.wait())
            stop_wait = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                (process_wait, stop_wait),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            if process_wait in done and not stop_event.is_set():
                error = AcpProcessExitedError(process.returncode)
                with self._lock:
                    self._runtime_error = error
                self.events.publish_terminal(
                    generation,
                    "",
                    "process_exit",
                    error=error,
                )
            await adapter.close()

    def _signal_stop(self, force: bool) -> None:
        stop_event = self._stop_event
        process = self._process
        if force and process is not None and process.returncode is None:
            process.kill()
        if stop_event is not None:
            stop_event.set()
