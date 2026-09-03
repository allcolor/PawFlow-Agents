"""Shared outbound ACP process-session and typed-client tests."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest
from acp import RequestError
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    CreateTerminalResponse,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

from core.acp.client_adapter import (
    AcpClientAdapter,
    AcpClientHandlers,
    select_permission_response,
)
from core.acp.errors import AcpProcessExitedError
from core.acp.process_session import AcpProcessSession
from core.acp.session_state import AcpEventChannel

_FIXTURE = Path(__file__).parent / "fixtures" / "acp_runtime_agent.py"


def _text(value: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=value)


def _handlers(calls: list[Any]) -> AcpClientHandlers:
    async def permission(
        session_id: str,
        tool_call: Any,
        options: list[Any],
    ) -> str:
        calls.append(("permission", session_id, tool_call.tool_call_id))
        assert [item.kind for item in options] == ["reject_once", "allow_once"]
        return "allow_once"

    async def read(
        session_id: str,
        path: str,
        line: int | None,
        limit: int | None,
    ) -> ReadTextFileResponse:
        calls.append(("read", session_id, path, line, limit))
        return ReadTextFileResponse(content="client content")

    async def write(
        session_id: str,
        path: str,
        content: str,
    ) -> WriteTextFileResponse:
        calls.append(("write", session_id, path, content))
        return WriteTextFileResponse()

    async def create(
        session_id: str,
        command: str,
        args: list[str] | None,
        env: list[Any] | None,
        cwd: str | None,
        output_byte_limit: int | None,
    ) -> CreateTerminalResponse:
        calls.append(
            (
                "create",
                session_id,
                command,
                args,
                env,
                cwd,
                output_byte_limit,
            )
        )
        return CreateTerminalResponse(terminal_id="terminal-1")

    async def output(
        session_id: str,
        terminal_id: str,
    ) -> TerminalOutputResponse:
        calls.append(("output", session_id, terminal_id))
        return TerminalOutputResponse(output="Python", truncated=False)

    async def wait(
        session_id: str,
        terminal_id: str,
    ) -> WaitForTerminalExitResponse:
        calls.append(("wait", session_id, terminal_id))
        return WaitForTerminalExitResponse(exit_code=0)

    async def kill(
        session_id: str,
        terminal_id: str,
    ) -> KillTerminalResponse:
        calls.append(("kill", session_id, terminal_id))
        return KillTerminalResponse()

    async def release(
        session_id: str,
        terminal_id: str,
    ) -> ReleaseTerminalResponse:
        calls.append(("release", session_id, terminal_id))
        return ReleaseTerminalResponse()

    return AcpClientHandlers(
        permission=permission,
        read_text_file=read,
        write_text_file=write,
        create_terminal=create,
        terminal_output=output,
        wait_for_terminal_exit=wait,
        kill_terminal=kill,
        release_terminal=release,
    )


def _session(calls: list[Any], *, capacity: int = 16) -> AcpProcessSession:
    return AcpProcessSession(
        sys.executable,
        [str(_FIXTURE)],
        handlers=_handlers(calls),
        event_capacity=capacity,
        startup_timeout=5,
        shutdown_timeout=0.25,
    )


def _events_until_terminal(
    session: AcpProcessSession,
    generation: int,
    session_id: str,
    timeout: float = 5,
) -> list[Any]:
    deadline = time.monotonic() + timeout
    found = []
    while True:
        event = session.events.get(max(0.001, deadline - time.monotonic()))
        if event.generation != generation or event.session_id != session_id:
            continue
        found.append(event)
        if event.terminal:
            return found


def test_event_channel_saturation_never_drops_terminal_state():
    channel = AcpEventChannel(max_updates=2)
    channel.publish_update(1, "s", "first")
    second = channel.publish_update(1, "s", "second")
    third = channel.publish_update(1, "s", "third")
    terminal = channel.publish_terminal(1, "s", "response", payload="done")

    assert channel.dropped_updates == 1
    assert channel.drain() == [second, third, terminal]


@pytest.mark.asyncio
async def test_permission_mapping_uses_exact_kinds_and_fails_closed():
    options = [
        PermissionOption(
            option_id="allow_always_in_id",
            name="Allow-looking reject",
            kind="reject_once",
        ),
        PermissionOption(
            option_id="plain",
            name="Plain",
            kind="allow_once",
        ),
    ]
    selected = select_permission_response("allow_always", options)
    assert selected.outcome.option_id == "plain"

    invalid = RequestPermissionResponse.model_validate(
        {"outcome": {"outcome": "selected", "optionId": "missing"}}
    )
    adapter = AcpClientAdapter(
        AcpEventChannel(),
        1,
        AcpClientHandlers(permission=lambda *_: invalid),
    )
    checked = await adapter.request_permission("s", object(), options)
    assert checked.outcome.outcome == "cancelled"


@pytest.mark.asyncio
async def test_terminal_operations_are_scoped_to_the_creating_session():
    calls: list[Any] = []
    adapter = AcpClientAdapter(
        AcpEventChannel(), 1, handlers=_handlers(calls)
    )
    created = await adapter.create_terminal(
        "session-a", "python", ["--version"], None, None, 1024
    )

    for operation in (
        adapter.terminal_output,
        adapter.wait_for_terminal_exit,
        adapter.kill_terminal,
        adapter.release_terminal,
    ):
        with pytest.raises(RequestError):
            await operation("session-b", created.terminal_id)
    assert [call[0] for call in calls] == ["create"]

    await adapter.release_terminal("session-a", created.terminal_id)
    with pytest.raises(RequestError):
        await adapter.terminal_output("session-a", created.terminal_id)


def test_process_session_streams_typed_updates_and_client_calls():
    calls: list[Any] = []
    session = _session(calls)
    try:
        initialized = session.start()
        assert initialized.protocol_version == 1
        created = session.call(
            "new_session",
            cwd="/workspace",
            mcp_servers=[],
            timeout=5,
        )
        handle = session.begin_prompt(
            created.session_id,
            [_text("hello")],
        )
        events = _events_until_terminal(
            session,
            handle.generation,
            created.session_id,
        )

        assert handle.result(5).stop_reason == "end_turn"
        assert [type(event.payload) for event in events] == [
            AgentMessageChunk,
            AgentThoughtChunk,
            ToolCallStart,
            ToolCallProgress,
            UsageUpdate,
            type(handle.result()),
        ]
        assert events[-1].kind == "response"
        assert [call[0] for call in calls] == [
            "permission",
            "read",
            "write",
            "create",
            "output",
            "wait",
            "kill",
            "release",
        ]
    finally:
        session.close()
    assert not session.is_running


def test_process_session_cancel_is_protocol_notification_not_force_kill():
    calls: list[Any] = []
    session = _session(calls)
    try:
        session.start()
        created = session.call(
            "new_session",
            cwd="/workspace",
            mcp_servers=[],
            timeout=5,
        )
        handle = session.begin_prompt(created.session_id, [_text("wait")])
        handle.cancel(timeout=5)
        assert handle.result(5).stop_reason == "cancelled"
        events = _events_until_terminal(
            session,
            handle.generation,
            created.session_id,
        )
        assert events[-1].kind == "response"
        assert session.is_running
    finally:
        session.close()


def test_unexpected_process_exit_wakes_prompt_and_restart_fences_generation():
    calls: list[Any] = []
    session = _session(calls)
    try:
        session.start()
        first_generation = session.generation
        created = session.call(
            "new_session",
            cwd="/workspace",
            mcp_servers=[],
            timeout=5,
        )
        handle = session.begin_prompt(created.session_id, [_text("die")])
        with pytest.raises(ConnectionError):
            handle.result(5)

        deadline = time.monotonic() + 5
        while session.is_running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert isinstance(session.runtime_error, AcpProcessExitedError)

        session.start()
        assert session.generation == first_generation + 1
        created = session.call(
            "new_session",
            cwd="/workspace",
            mcp_servers=[],
            timeout=5,
        )
        second = session.begin_prompt(created.session_id, [_text("hello")])
        events = _events_until_terminal(
            session,
            second.generation,
            created.session_id,
        )
        assert second.result(5).stop_reason == "end_turn"
        assert all(event.generation == second.generation for event in events)
    finally:
        session.close(force=True)
