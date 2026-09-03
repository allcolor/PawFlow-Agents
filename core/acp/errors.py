"""Typed failures raised by PawFlow's shared ACP runtime."""

from __future__ import annotations


class AcpRuntimeError(RuntimeError):
    """Base class for PawFlow-facing ACP runtime failures."""


class AcpStartupError(AcpRuntimeError):
    """Raised when an ACP process cannot reach an initialized state."""


class AcpSessionClosedError(AcpRuntimeError):
    """Raised when work is submitted to a closed ACP process session."""


class AcpProcessExitedError(AcpRuntimeError):
    """Raised when an ACP subprocess exits outside an intentional shutdown."""

    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        suffix = "unknown" if returncode is None else str(returncode)
        super().__init__(f"ACP process exited with code {suffix}")
