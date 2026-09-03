"""Transparent stdio-to-WebSocket proxy for published PawFlow ACP agents."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, BinaryIO, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

_MAX_MESSAGE_BYTES = 16 * 1024 * 1024
_GATEWAY_HEADER = "X-PawFlow-Gateway-Key"


@dataclass(frozen=True)
class AcpProxyConfig:
    endpoint: str
    api_key: str
    gateway_key: str = ""

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.gateway_key:
            headers[_GATEWAY_HEADER] = self.gateway_key
        return headers


def _required(value: str, name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    return resolved


def build_endpoint(server_url: str, publication_id: str) -> str:
    """Build a credential-free ACP WebSocket URL from explicit settings."""
    raw_url = _required(server_url, "PAWFLOW_ACP_SERVER_URL")
    publication = _required(
        publication_id,
        "PAWFLOW_ACP_PUBLICATION_ID",
    )
    parsed = urlsplit(raw_url)
    schemes = {
        "http": "ws",
        "https": "wss",
        "ws": "ws",
        "wss": "wss",
    }
    scheme = schemes.get(parsed.scheme.lower())
    if scheme is None or not parsed.netloc:
        raise ValueError(
            "PAWFLOW_ACP_SERVER_URL must use http, https, ws, or wss"
        )
    if parsed.username or parsed.password:
        raise ValueError("PAWFLOW_ACP_SERVER_URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(
            "PAWFLOW_ACP_SERVER_URL must not contain a query or fragment"
        )
    path = parsed.path.rstrip("/") + "/acp/" + quote(publication, safe="")
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def load_config(
    *,
    server_url: str = "",
    publication_id: str = "",
    environ: Mapping[str, str] | None = None,
) -> AcpProxyConfig:
    """Load non-secret flags plus environment-only publication credentials."""
    env = environ if environ is not None else os.environ
    endpoint = build_endpoint(
        server_url or env.get("PAWFLOW_ACP_SERVER_URL", ""),
        publication_id or env.get("PAWFLOW_ACP_PUBLICATION_ID", ""),
    )
    api_key = _required(
        env.get("PAWFLOW_ACP_API_KEY", ""),
        "PAWFLOW_ACP_API_KEY",
    )
    return AcpProxyConfig(
        endpoint=endpoint,
        api_key=api_key,
        gateway_key=str(env.get("PAWFLOW_GATEWAY_KEY", "") or ""),
    )


def _start_stdin_reader(
    stream: BinaryIO,
) -> tuple[asyncio.StreamReader, Any | None]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=_MAX_MESSAGE_BYTES + 1)
    protocol = asyncio.StreamReaderProtocol(reader)
    if os.name != "nt":
        async def _connect() -> Any:
            transport, _ = await loop.connect_read_pipe(
                lambda: protocol,
                stream,
            )
            return transport

        return reader, _connect()

    def _feed() -> None:
        try:
            while data := stream.readline(_MAX_MESSAGE_BYTES + 1):
                loop.call_soon_threadsafe(reader.feed_data, data)
        finally:
            loop.call_soon_threadsafe(reader.feed_eof)

    threading.Thread(
        target=_feed,
        name="pawflow-acp-stdin",
        daemon=True,
    ).start()
    return reader, None


async def _stdin_to_websocket(
    websocket: Any,
    reader: asyncio.StreamReader,
) -> None:
    while True:
        line = await reader.readline()
        if not line:
            await websocket.close()
            return
        if len(line) > _MAX_MESSAGE_BYTES:
            raise ValueError(
                f"ACP stdio message exceeds {_MAX_MESSAGE_BYTES} bytes"
            )
        payload = line.rstrip(b"\r\n")
        if not payload:
            continue
        await websocket.send(payload.decode("utf-8"))


async def _websocket_to_stdout(
    websocket: Any,
    stream: BinaryIO,
) -> None:
    async for message in websocket:
        if isinstance(message, bytes):
            continue
        payload = message.encode("utf-8")
        if len(payload) > _MAX_MESSAGE_BYTES:
            raise ValueError(
                f"ACP WebSocket message exceeds {_MAX_MESSAGE_BYTES} bytes"
            )
        stream.write(payload + b"\n")
        stream.flush()


async def run_proxy(
    config: AcpProxyConfig,
    *,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
) -> None:
    """Forward complete ACP messages without interpreting their JSON bodies."""
    from websockets.asyncio.client import connect

    source = input_stream or sys.stdin.buffer
    destination = output_stream or sys.stdout.buffer
    reader, connector = _start_stdin_reader(source)
    pipe_transport = await connector if connector is not None else None
    try:
        async with connect(
            config.endpoint,
            additional_headers=config.headers,
            max_size=_MAX_MESSAGE_BYTES,
        ) as websocket:
            to_server = asyncio.create_task(
                _stdin_to_websocket(websocket, reader),
                name="pawflow-acp-stdin-to-websocket",
            )
            to_client = asyncio.create_task(
                _websocket_to_stdout(websocket, destination),
                name="pawflow-acp-websocket-to-stdout",
            )
            done, pending = await asyncio.wait(
                {to_server, to_client},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    finally:
        if pipe_transport is not None:
            pipe_transport.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pawflow-acp",
        description=(
            "Bridge ACP over stdio to a published PawFlow agent WebSocket"
        ),
    )
    parser.add_argument(
        "--server-url",
        default="",
        help="PawFlow server base URL; defaults to PAWFLOW_ACP_SERVER_URL",
    )
    parser.add_argument(
        "--publication-id",
        default="",
        help=(
            "Published agent id; defaults to "
            "PAWFLOW_ACP_PUBLICATION_ID"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(
            server_url=args.server_url,
            publication_id=args.publication_id,
        )
        asyncio.run(run_proxy(config))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"pawflow-acp: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
