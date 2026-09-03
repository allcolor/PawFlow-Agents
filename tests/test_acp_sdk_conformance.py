"""ACP SDK 0.12.1 contract and PawFlow transport conformance tests."""

from __future__ import annotations

import asyncio
import os
import struct
import sys
from pathlib import Path
from typing import Any

import pytest
from acp import (
    PROTOCOL_VERSION,
    connect_to_agent,
    run_agent,
    spawn_agent_process,
)
from acp.schema import (
    AcceptElicitationResponse,
    AgentCapabilities,
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    CancelElicitationResponse,
    CancelNotification,
    ClientCapabilities,
    CloseSessionRequest,
    CloseSessionResponse,
    CreateFormSessionElicitationRequest,
    CreateTerminalRequest,
    CreateTerminalResponse,
    DeclineElicitationResponse,
    ElicitationFormSessionMode,
    ElicitationSchema,
    FileSystemCapabilities,
    ForkSessionRequest,
    ForkSessionResponse,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    KillTerminalRequest,
    KillTerminalResponse,
    ListSessionsRequest,
    ListSessionsResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PermissionOption,
    PromptRequest,
    PromptResponse,
    ReadTextFileRequest,
    ReadTextFileResponse,
    ReleaseTerminalRequest,
    ReleaseTerminalResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    ResumeSessionRequest,
    ResumeSessionResponse,
    SessionInfo,
    SessionNotification,
    TerminalOutputRequest,
    TerminalOutputResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    WaitForTerminalExitRequest,
    WaitForTerminalExitResponse,
    WriteTextFileRequest,
    WriteTextFileResponse,
)

from core.acp.protocol import (
    ACP_SDK_VERSION,
    AcpProtocolVersionError,
    initialize_connection,
)
from core.acp.transport import (
    AcpWebSocketTransportError,
    RawWebSocketTransport,
    memory_transport_pair,
    serve_agent_on_websocket,
)
from pawflow_cli.acp_proxy import _parser, build_endpoint, load_config


def _text(value: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=value)


def _round_trip(model: Any) -> None:
    encoded = model.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    assert type(model).model_validate(encoded) == model


def test_generated_models_round_trip_without_local_schema_copies():
    client_caps = ClientCapabilities(
        fs=FileSystemCapabilities(
            read_text_file=True,
            write_text_file=True,
        ),
        terminal=True,
    )
    agent_caps = AgentCapabilities(load_session=True)
    tool_update = ToolCallUpdate(
        tool_call_id="tool-1",
        kind="read",
        status="in_progress",
        title="Read file",
    )
    allow = PermissionOption(
        option_id="allow",
        name="Allow once",
        kind="allow_once",
    )
    schema = ElicitationSchema(type="object", properties={})

    models = [
        InitializeRequest(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=client_caps,
            client_info=Implementation(
                name="pawflow-test",
                version="1",
            ),
        ),
        InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=agent_caps,
            agent_info=Implementation(
                name="fixture-agent",
                version="1",
            ),
        ),
        NewSessionRequest(
            cwd="/workspace",
            additional_directories=["/workspace/docs"],
            mcp_servers=[],
        ),
        NewSessionResponse(session_id="session-1"),
        LoadSessionRequest(
            cwd="/workspace",
            session_id="session-1",
            mcp_servers=[],
        ),
        LoadSessionResponse(),
        ListSessionsRequest(cwd="/workspace", cursor="cursor-1"),
        ListSessionsResponse(
            sessions=[
                SessionInfo(
                    session_id="session-1",
                    cwd="/workspace",
                    title="Fixture",
                )
            ],
            next_cursor="cursor-2",
        ),
        ResumeSessionRequest(
            session_id="session-1",
            cwd="/workspace",
            mcp_servers=[],
        ),
        ResumeSessionResponse(),
        ForkSessionRequest(
            session_id="session-1",
            cwd="/workspace",
            mcp_servers=[],
        ),
        ForkSessionResponse(session_id="session-2"),
        CloseSessionRequest(session_id="session-1"),
        CloseSessionResponse(),
        PromptRequest(
            session_id="session-1",
            prompt=[_text("hello")],
        ),
        PromptResponse(stop_reason="end_turn"),
        CancelNotification(session_id="session-1"),
        SessionNotification(
            session_id="session-1",
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=_text("hello"),
            ),
        ),
        AgentThoughtChunk(
            session_update="agent_thought_chunk",
            content=_text("thinking"),
        ),
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="tool-1",
            title="Read file",
            kind="read",
            status="pending",
        ),
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="tool-1",
            status="completed",
        ),
        UsageUpdate(
            session_update="usage_update",
            used=10,
            size=100,
        ),
        RequestPermissionRequest(
            session_id="session-1",
            tool_call=tool_update,
            options=[allow],
        ),
        RequestPermissionResponse(
            outcome=AllowedOutcome(
                outcome="selected",
                option_id="allow",
            )
        ),
        ReadTextFileRequest(
            session_id="session-1",
            path="/workspace/file.txt",
            line=1,
            limit=2,
        ),
        ReadTextFileResponse(content="hello"),
        WriteTextFileRequest(
            session_id="session-1",
            path="/workspace/file.txt",
            content="hello",
        ),
        WriteTextFileResponse(),
        CreateTerminalRequest(
            session_id="session-1",
            command="python",
            args=["--version"],
            env=[],
            cwd="/workspace",
            output_byte_limit=1024,
        ),
        CreateTerminalResponse(terminal_id="terminal-1"),
        TerminalOutputRequest(
            session_id="session-1",
            terminal_id="terminal-1",
        ),
        TerminalOutputResponse(output="done", truncated=False),
        WaitForTerminalExitRequest(
            session_id="session-1",
            terminal_id="terminal-1",
        ),
        WaitForTerminalExitResponse(exit_code=0),
        KillTerminalRequest(
            session_id="session-1",
            terminal_id="terminal-1",
        ),
        KillTerminalResponse(),
        ReleaseTerminalRequest(
            session_id="session-1",
            terminal_id="terminal-1",
        ),
        ReleaseTerminalResponse(),
        ElicitationFormSessionMode(
            session_id="session-1",
            requested_schema=schema,
        ),
        CreateFormSessionElicitationRequest(
            session_id="session-1",
            message="Choose",
            mode="form",
            requested_schema=schema,
        ),
        AcceptElicitationResponse(action="accept", content={}),
        DeclineElicitationResponse(action="decline"),
        CancelElicitationResponse(action="cancel"),
    ]
    for model in models:
        _round_trip(model)


class _FixtureClient:
    def __init__(self) -> None:
        self.updates: list[Any] = []
        self.calls: list[str] = []

    def on_connect(self, conn: Any) -> None:
        self.connection = conn

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        assert session_id == "session-1"
        self.updates.append(update)

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        self.calls.append("permission")
        assert session_id == "session-1"
        assert tool_call.tool_call_id == "tool-1"
        return RequestPermissionResponse(
            outcome=AllowedOutcome(
                outcome="selected",
                option_id=options[0].option_id,
            )
        )

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        self.calls.append("read")
        assert (session_id, path, line, limit) == (
            "session-1",
            "/workspace/input.txt",
            2,
            3,
        )
        return ReadTextFileResponse(content="client content")

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse:
        self.calls.append("write")
        assert (session_id, path, content) == (
            "session-1",
            "/workspace/output.txt",
            "agent content",
        )
        return WriteTextFileResponse()

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
        self.calls.append("terminal_create")
        assert session_id == "session-1"
        assert (command, args, cwd, output_byte_limit) == (
            "python",
            ["--version"],
            "/workspace",
            1024,
        )
        return CreateTerminalResponse(terminal_id="terminal-1")

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> TerminalOutputResponse:
        self.calls.append("terminal_output")
        return TerminalOutputResponse(output="Python", truncated=False)

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> WaitForTerminalExitResponse:
        self.calls.append("terminal_wait")
        return WaitForTerminalExitResponse(exit_code=0)

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> KillTerminalResponse:
        self.calls.append("terminal_kill")
        return KillTerminalResponse()

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> ReleaseTerminalResponse:
        self.calls.append("terminal_release")
        return ReleaseTerminalResponse()

    async def create_elicitation(
        self,
        message: str,
        mode: Any,
        **kwargs: Any,
    ) -> DeclineElicitationResponse:
        return DeclineElicitationResponse(action="decline")

    async def complete_elicitation(
        self,
        elicitation_id: str,
        **kwargs: Any,
    ) -> None:
        return None

    async def ext_method(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return {}

    async def ext_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        return None


class _FixtureAgent:
    def __init__(self, protocol_version: int = PROTOCOL_VERSION) -> None:
        self.protocol_version = protocol_version
        self.cancelled = asyncio.Event()
        self.client: Any = None

    def on_connect(self, conn: Any) -> None:
        self.client = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        assert protocol_version == PROTOCOL_VERSION
        return InitializeResponse(
            protocol_version=self.protocol_version,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(
                name="fixture-agent",
                version="1",
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        assert cwd == "/workspace"
        return NewSessionResponse(session_id="session-1")

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        assert (cwd, session_id) == ("/workspace", "session-1")
        return LoadSessionResponse()

    async def list_sessions(
        self,
        cwd: str | None = None,
        cursor: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        return ListSessionsResponse(
            sessions=[
                SessionInfo(
                    session_id="session-1",
                    cwd="/workspace",
                )
            ]
        )

    async def prompt(
        self,
        session_id: str,
        prompt: list[Any],
        **kwargs: Any,
    ) -> PromptResponse:
        assert session_id == "session-1"
        assert prompt[0].text == "hello"

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
                kind="read",
                status="in_progress",
                title="Read file",
            ),
            options=[
                PermissionOption(
                    option_id="allow",
                    name="Allow once",
                    kind="allow_once",
                )
            ],
        )
        assert permission.outcome.option_id == "allow"

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

    async def cancel(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        assert session_id == "session-1"
        self.cancelled.set()

    async def ext_method(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        return {}

    async def ext_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        return None


async def _exercise_connection(
    connection: Any,
    agent: _FixtureAgent,
    client: _FixtureClient,
) -> None:
    response = await initialize_connection(
        connection,
        client_capabilities=ClientCapabilities(
            fs=FileSystemCapabilities(
                read_text_file=True,
                write_text_file=True,
            ),
            terminal=True,
        ),
        client_info=Implementation(name="pawflow-test", version="1"),
    )
    assert response.protocol_version == PROTOCOL_VERSION
    session = await connection.new_session(
        cwd="/workspace",
        mcp_servers=[],
    )
    assert session.session_id == "session-1"
    await connection.load_session(
        cwd="/workspace",
        session_id=session.session_id,
        mcp_servers=[],
    )
    listed = await connection.list_sessions(cwd="/workspace")
    assert [item.session_id for item in listed.sessions] == ["session-1"]
    prompt = await connection.prompt(
        session_id=session.session_id,
        prompt=[_text("hello")],
    )
    assert prompt.stop_reason == "end_turn"
    await connection.cancel(session_id=session.session_id)
    await asyncio.wait_for(agent.cancelled.wait(), timeout=2)
    assert [type(item).__name__ for item in client.updates] == [
        "AgentMessageChunk",
        "AgentThoughtChunk",
        "ToolCallStart",
        "ToolCallProgress",
        "UsageUpdate",
    ]
    assert client.calls == [
        "permission",
        "read",
        "write",
        "terminal_create",
        "terminal_output",
        "terminal_wait",
        "terminal_kill",
        "terminal_release",
    ]


@pytest.mark.asyncio
async def test_in_memory_sdk_round_trip_is_bidirectional():
    agent = _FixtureAgent()
    client = _FixtureClient()
    agent_transport, client_transport = memory_transport_pair()
    server = asyncio.create_task(run_agent(agent, agent_transport))
    connection = connect_to_agent(client, client_transport)
    try:
        await asyncio.wait_for(
            _exercise_connection(connection, agent, client),
            timeout=5,
        )
    finally:
        await connection.close()
        await asyncio.wait_for(server, timeout=2)


@pytest.mark.asyncio
async def test_initialize_rejects_incompatible_protocol_version():
    agent = _FixtureAgent(protocol_version=PROTOCOL_VERSION + 1)
    client = _FixtureClient()
    agent_transport, client_transport = memory_transport_pair()
    server = asyncio.create_task(run_agent(agent, agent_transport))
    connection = connect_to_agent(client, client_transport)
    try:
        with pytest.raises(
            AcpProtocolVersionError,
            match="protocol version mismatch",
        ):
            await initialize_connection(connection)
    finally:
        await connection.close()
        await asyncio.wait_for(server, timeout=2)


class _BufferWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _masked_frame(
    payload: bytes,
    *,
    opcode: int = 0x01,
    fin: bool = True,
) -> bytes:
    mask = b"\x01\x02\x03\x04"
    first = (0x80 if fin else 0) | opcode
    length = len(payload)
    if length < 126:
        header = bytes((first, 0x80 | length))
    elif length < 65536:
        header = bytes((first, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((first, 0x80 | 127)) + struct.pack("!Q", length)
    encoded = bytes(
        value ^ mask[index % 4]
        for index, value in enumerate(payload)
    )
    return header + mask + encoded


@pytest.mark.asyncio
async def test_raw_transport_reassembles_text_and_ignores_binary():
    reader = asyncio.StreamReader()
    writer = _BufferWriter()
    reader.feed_data(_masked_frame(b"ignored", opcode=0x02))
    reader.feed_data(_masked_frame(b'{"jsonrpc":', fin=False))
    reader.feed_data(_masked_frame(b'"2.0"}', opcode=0x00))
    transport = RawWebSocketTransport(reader, writer, max_message_bytes=100)
    assert await transport.receive() == {"jsonrpc": "2.0"}
    await transport.close()
    assert writer.closed is True


@pytest.mark.asyncio
async def test_raw_transport_fails_closed_on_invalid_or_oversize_message():
    reader = asyncio.StreamReader()
    reader.feed_data(_masked_frame(b"not-json"))
    transport = RawWebSocketTransport(
        reader,
        _BufferWriter(),
        max_message_bytes=100,
    )
    with pytest.raises(AcpWebSocketTransportError, match="invalid"):
        await transport.receive()

    oversized = RawWebSocketTransport(
        asyncio.StreamReader(),
        _BufferWriter(),
        max_message_bytes=4,
    )
    with pytest.raises(AcpWebSocketTransportError, match="exceeds"):
        await oversized.send({"too": "large"})


def _listener_for(agent: _FixtureAgent):
    from services.http_listener_service import HTTPListenerService

    listener = HTTPListenerService({"host": "127.0.0.1", "port": 0})
    listener.register_route(
        "GET",
        "/acp/publication-1",
        "acp-conformance",
        callback=None,
        ws_handler=lambda sock, _params, _meta: serve_agent_on_websocket(
            sock,
            agent,
        ),
        public=True,
        private_only=True,
    )
    listener.connect()
    port = listener._server.server_address[1]
    return listener, port


@pytest.mark.asyncio
async def test_official_websocket_client_reaches_raw_listener_transport():
    from acp.ws.client import create_websocket_stream

    agent = _FixtureAgent()
    client = _FixtureClient()
    listener, port = _listener_for(agent)
    transport = None
    connection = None
    try:
        transport = await create_websocket_stream(
            f"ws://127.0.0.1:{port}/acp/publication-1",
            headers={"Authorization": "Bearer test-only"},
        )
        connection = connect_to_agent(client, transport)
        await asyncio.wait_for(
            _exercise_connection(connection, agent, client),
            timeout=5,
        )
    finally:
        if connection is not None:
            await connection.close()
        elif transport is not None:
            await transport.close()
        listener.disconnect()


@pytest.mark.asyncio
async def test_stdio_proxy_round_trip_uses_official_sdk_process_helper():
    agent = _FixtureAgent()
    client = _FixtureClient()
    listener, port = _listener_for(agent)
    env = dict(os.environ)
    env.update(
        {
            "PAWFLOW_ACP_SERVER_URL": f"http://127.0.0.1:{port}",
            "PAWFLOW_ACP_PUBLICATION_ID": "publication-1",
            "PAWFLOW_ACP_API_KEY": "test-only",
            "PYTHONPATH": (
                str(Path(__file__).resolve().parents[1])
                + os.pathsep
                + env.get("PYTHONPATH", "")
            ),
        }
    )
    try:
        async with spawn_agent_process(
            client,
            sys.executable,
            "-m",
            "pawflow_cli.acp_proxy",
            env=env,
            transport_kwargs={"shutdown_timeout": 2.0},
        ) as (connection, process):
            assert process.returncode is None
            await asyncio.wait_for(
                _exercise_connection(connection, agent, client),
                timeout=8,
            )
    finally:
        listener.disconnect()


def test_proxy_config_keeps_secrets_out_of_url_and_argv():
    endpoint = build_endpoint(
        "https://pawflow.example/base/",
        "publication / one",
    )
    assert endpoint == (
        "wss://pawflow.example/base/acp/publication%20%2F%20one"
    )
    config = load_config(
        environ={
            "PAWFLOW_ACP_SERVER_URL": "https://pawflow.example",
            "PAWFLOW_ACP_PUBLICATION_ID": "publication-1",
            "PAWFLOW_ACP_API_KEY": "publication-secret",
            "PAWFLOW_GATEWAY_KEY": "gateway-secret",
        }
    )
    assert "publication-secret" not in config.endpoint
    assert "gateway-secret" not in config.endpoint
    assert config.headers == {
        "Authorization": "Bearer publication-secret",
        "X-PawFlow-Gateway-Key": "gateway-secret",
    }
    help_text = _parser().format_help()
    assert "--api-key" not in help_text
    assert "--gateway-key" not in help_text

    with pytest.raises(ValueError, match="must not contain credentials"):
        build_endpoint("https://user:pass@pawflow.example", "pub")
    with pytest.raises(ValueError, match="query or fragment"):
        build_endpoint("https://pawflow.example?key=secret", "pub")


def test_dependency_and_entry_point_contract_is_exact():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (root / "requirements.txt").read_text(
        encoding="utf-8"
    )
    for source in (pyproject, requirements):
        assert "agent-client-protocol==0.12.1" in source
        assert "agent-client-protocol[http]" not in source
        assert "websockets>=13.0" in source
    assert ACP_SDK_VERSION == "0.12.1"
    assert (
        'pawflow-acp = "pawflow_cli.acp_proxy:main"'
        in pyproject
    )
