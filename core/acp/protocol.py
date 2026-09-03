"""Pinned ACP protocol negotiation helpers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from acp import PROTOCOL_VERSION
from acp.schema import ClientCapabilities, Implementation, InitializeResponse

ACP_SDK_VERSION = "0.12.1"
ACP_PROTOCOL_VERSION = PROTOCOL_VERSION


class AcpProtocolVersionError(ValueError):
    """Raised when an ACP peer negotiates an unsupported protocol version."""


def require_supported_sdk() -> None:
    """Fail closed when the imported SDK differs from PawFlow's tested pin."""
    try:
        installed = version("agent-client-protocol")
    except PackageNotFoundError as exc:
        raise RuntimeError("agent-client-protocol is not installed") from exc
    if installed != ACP_SDK_VERSION:
        raise RuntimeError(
            "Unsupported agent-client-protocol version "
            f"{installed}; PawFlow requires {ACP_SDK_VERSION}"
        )


async def initialize_connection(
    connection: Any,
    *,
    client_capabilities: ClientCapabilities | None = None,
    client_info: Implementation | None = None,
) -> InitializeResponse:
    """Initialize one SDK connection and enforce stable protocol version 1."""
    require_supported_sdk()
    response = await connection.initialize(
        protocol_version=ACP_PROTOCOL_VERSION,
        client_capabilities=client_capabilities or ClientCapabilities(),
        client_info=client_info,
    )
    if response.protocol_version != ACP_PROTOCOL_VERSION:
        raise AcpProtocolVersionError(
            "ACP protocol version mismatch: "
            f"peer returned {response.protocol_version}, "
            f"PawFlow requires {ACP_PROTOCOL_VERSION}"
        )
    return response
