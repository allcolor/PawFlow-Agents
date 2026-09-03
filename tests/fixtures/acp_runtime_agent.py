"""Executable stable-ACP fixture used by shared runtime tests."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from acp import RequestError, run_agent
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
)


def _text(value: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=value)


class RuntimeFixtureAgent:
    def __init__(self) -> None:
        self.client: Any = None
        self.cancelled = asyncio.Event()
        self.loaded = False
        self.stale_load_seen = False
        self.new_session_calls = 0

    def on_connect(self, connection: Any) -> None:
        self.client = connection

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any = None,
        client_info: Any = None,
        **kwargs: Any,
    ) -> Any:
        del client_capabilities, client_info, kwargs
        return {
            "protocolVersion": protocol_version,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {
                    "audio": True,
                    "embeddedContext": True,
                    "image": True,
                },
            },
            "agentInfo": {
                "name": "pawflow-runtime-fixture",
                "version": "1",
            },
        }

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del cwd, additional_directories, mcp_servers, kwargs
        self.loaded = False
        self.new_session_calls += 1
        return NewSessionResponse(
            session_id=os.environ.get(
                "PAWFLOW_ACP_FIXTURE_SESSION_ID", "runtime-session"
            )
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        del cwd, additional_directories, mcp_servers, kwargs
        if session_id == os.environ.get("PAWFLOW_ACP_FIXTURE_STALE_SESSION"):
            self.stale_load_seen = True
            raise RequestError(-32002, "unknown ACP session")
        self.loaded = True
        return LoadSessionResponse()

    async def prompt(
        self,
        session_id: str,
        prompt: list[Any],
        **kwargs: Any,
    ) -> PromptResponse:
        del kwargs
        if os.environ.get("PAWFLOW_ACP_FIXTURE_MODE") == "provider":
            text = "\n".join(
                str(getattr(block, "text", "") or "")
                for block in prompt
                if str(getattr(block, "type", "") or "") == "text"
            )
            if "provider-wait" in text:
                await self.cancelled.wait()
                self.cancelled.clear()
                return PromptResponse(stop_reason="cancelled")

            if os.environ.get("PAWFLOW_ACP_FIXTURE_EMIT_TOOL") == "1":
                await self.client.session_update(
                    session_id=session_id,
                    update=ToolCallStart(
                        session_update="tool_call",
                        tool_call_id="provider-tool",
                        title="Fixture tool",
                        kind="read",
                        status="pending",
                        raw_input={"path": "/workspace/input.txt"},
                    ),
                )
                await self.client.session_update(
                    session_id=session_id,
                    update=ToolCallProgress(
                        session_update="tool_call_update",
                        tool_call_id="provider-tool",
                        title="Fixture tool",
                        status="completed",
                        raw_output="fixture result",
                    ),
                )

            payload = {
                "inherited": os.environ.get("PAWFLOW_ACP_INHERITED", ""),
                "loaded": self.loaded,
                "mime_types": [
                    str(getattr(block, "mime_type", "") or "")
                    for block in prompt
                ],
                "new_session_calls": self.new_session_calls,
                "pid": os.getpid(),
                "session_id": session_id,
                "stale_load_seen": self.stale_load_seen,
                "text": text,
                "types": [
                    str(getattr(block, "type", "") or "")
                    for block in prompt
                ],
            }
            await self.client.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=_text(json.dumps(payload, sort_keys=True)),
                ),
            )
            await self.client.session_update(
                session_id=session_id,
                update=UsageUpdate(
                    session_update="usage_update",
                    used=10,
                    size=100,
                ),
            )
            return PromptResponse(stop_reason="end_turn")

        value = prompt[0].text
        if value == "die":
            os._exit(7)
        if value == "wait":
            await self.cancelled.wait()
            self.cancelled.clear()
            return PromptResponse(stop_reason="cancelled")

        await self.client.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=_text("hello"),
            ),
        )
        await self.client.session_update(
            session_id=session_id,
            update=AgentThoughtChunk(
                session_update="agent_thought_chunk",
                content=_text("thinking"),
            ),
        )
        await self.client.session_update(
            session_id=session_id,
            update=ToolCallStart(
                session_update="tool_call",
                tool_call_id="tool-1",
                title="Read file",
                kind="read",
                status="pending",
            ),
        )
        permission = await self.client.request_permission(
            session_id=session_id,
            tool_call=ToolCallUpdate(
                tool_call_id="tool-1",
                title="Read file",
                kind="read",
                status="in_progress",
            ),
            options=[
                PermissionOption(
                    option_id="allow-looking-reject",
                    name="Reject",
                    kind="reject_once",
                ),
                PermissionOption(
                    option_id="selected-allow",
                    name="Allow",
                    kind="allow_once",
                ),
            ],
        )
        assert permission.outcome.option_id == "selected-allow"

        read = await self.client.read_text_file(
            session_id=session_id,
            path="/workspace/input.txt",
            line=2,
            limit=3,
        )
        assert read.content == "client content"
        await self.client.write_text_file(
            session_id=session_id,
            path="/workspace/output.txt",
            content="agent content",
        )
        terminal = await self.client.create_terminal(
            session_id=session_id,
            command="python",
            args=["--version"],
            env=[],
            cwd="/workspace",
            output_byte_limit=1024,
        )
        output = await self.client.terminal_output(
            session_id=session_id,
            terminal_id=terminal.terminal_id,
        )
        assert output.output == "Python"
        status = await self.client.wait_for_terminal_exit(
            session_id=session_id,
            terminal_id=terminal.terminal_id,
        )
        assert status.exit_code == 0
        await self.client.kill_terminal(
            session_id=session_id,
            terminal_id=terminal.terminal_id,
        )
        await self.client.release_terminal(
            session_id=session_id,
            terminal_id=terminal.terminal_id,
        )
        await self.client.session_update(
            session_id=session_id,
            update=ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id="tool-1",
                status="completed",
            ),
        )
        await self.client.session_update(
            session_id=session_id,
            update=UsageUpdate(
                session_update="usage_update",
                used=10,
                size=100,
            ),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del session_id, kwargs
        self.cancelled.set()

    async def ext_method(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del method, params
        return {}

    async def ext_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        del method, params


if __name__ == "__main__":
    asyncio.run(run_agent(RuntimeFixtureAgent()))
